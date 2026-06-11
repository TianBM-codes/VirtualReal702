"""
Layer 2: Parser

Iterates over KeywordBlocks from the Lexer and builds an InpModel.
Responsibilities:
- Keyword dispatch via context-aware registry
- Maintaining parse context (ROOT / PART / ASSEMBLY / INSTANCE / STEP / MATERIAL)
- Building InpModel with QualifiedName-aware storage
- All references (set names, material names, etc.) are stored as strings;
  actual resolution happens in the Resolver.

NOT responsible for: expanding set nesting, resolving transforms,
expanding surface face IDs — those are Resolver tasks.
"""
from __future__ import annotations

import ast
import math
import operator
import re
from typing import Callable, Dict, List, Optional, Tuple

from .diagnostics import (
    DiagnosticCollector,
    UNKNOWN_KEYWORD, UNSUPPORTED_PARAM, UNKNOWN_ELEMENT_TYPE,
    MALFORMED_DATA, DUPLICATE_NAME, UMAT_SKIPPED,
    INCLUDE_CYCLE, INCLUDE_NOT_FOUND,
)
from .lexer import KeywordBlock, tokenize
from .model import (
    Assembly, AssemblyElset, AssemblyNset, Amplitude,
    BCDeclaration, CLoadDeclaration, DLoadDeclaration, DsloadDeclaration,
    DesignParameter, DesignResponse, DesignResponseRequest,
    ElasticData, Element, Elset, InpModel,
    Material, Node, Nset, Orientation, Part,
    ParameterDefinition,
    PlasticData, HyperelasticData, DamageData, CreepData,
    Rotation, Section, StepDeclaration, Surface, SurfaceEntry,
    TieConstraint, CouplingConstraint, TimePoints,
    Instance, map_element_type,
)

# ---------------------------------------------------------------------------
# Context tags
# ---------------------------------------------------------------------------
CTX_ROOT      = "ROOT"
CTX_PART      = "PART"
CTX_ASSEMBLY  = "ASSEMBLY"
CTX_INSTANCE  = "INSTANCE"
CTX_STEP      = "STEP"
CTX_MATERIAL  = "MATERIAL"

# BC type-keyword → expanded DOF ranges (Abaqus convention, 1-based)
_BC_TYPE_MAP: Dict[str, List[Tuple[int, int]]] = {
    "ENCASTRE":  [(1, 6)],
    "PINNED":    [(1, 3)],
    "XSYMM":    [(1, 1), (5, 5), (6, 6)],
    "YSYMM":    [(2, 2), (4, 4), (6, 6)],
    "ZSYMM":    [(3, 3), (4, 4), (5, 5)],
    "XASYMM":   [(2, 2), (3, 3), (4, 4)],
    "YASYMM":   [(1, 1), (3, 3), (5, 5)],
    "ZASYMM":   [(1, 1), (2, 2), (6, 6)],
}

HandlerFn = Callable[["InpParser", KeywordBlock], None]


def _ctx(*contexts: str):
    """Decorator to tag a handler with the contexts it is valid in."""
    def decorator(fn: HandlerFn) -> HandlerFn:
        fn._contexts = set(contexts)  # type: ignore[attr-defined]
        return fn
    return decorator


class InpParser:
    """
    Stateful parser.  Call parse(filepath) -> InpModel.
    """

    def __init__(self) -> None:
        self._diag = DiagnosticCollector()
        self._model = InpModel()
        # Context stack: list of (tag, name) e.g. [("ROOT",""), ("PART","PART-1")]
        self._ctx_stack: List[Tuple[str, str]] = [(CTX_ROOT, "")]
        # Pointers to currently-active objects
        self._current_part:     Optional[Part]     = None
        self._current_instance: Optional[Instance] = None
        self._current_assembly: Optional[Assembly] = None
        self._current_step:     Optional[StepDeclaration] = None
        self._current_material: Optional[Material] = None
        # Pending instance transform lines (raw data lines inside *Instance block)
        self._instance_data:    List[str] = []
        # Pending coupling constraint (waiting for *Kinematic / *Distributing sub-block)
        self._current_coupling: Optional[CouplingConstraint] = None
        # Pending design response declaration (waiting for *Element Response / *Node Response)
        self._current_design_response: Optional[DesignResponse] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(self, filepath: str) -> InpModel:
        blocks, issues = tokenize(filepath)

        # Report include issues as diagnostics
        for kind, path, lineno in issues:
            if kind == "CYCLE":
                self._diag.error(INCLUDE_CYCLE,
                                 f"*Include cycle detected: {path}",
                                 file=filepath, line=lineno)
            else:
                self._diag.error(INCLUDE_NOT_FOUND,
                                 f"*Include file not found: {path}",
                                 file=filepath, line=lineno)

        for block in blocks:
            self._dispatch(block)

        self._model.diagnostics = self._diag.items
        return self._model

    # ------------------------------------------------------------------
    # Context helpers
    # ------------------------------------------------------------------

    @property
    def _ctx(self) -> str:
        return self._ctx_stack[-1][0]

    @property
    def _ctx_name(self) -> str:
        return self._ctx_stack[-1][1]

    def _push_ctx(self, tag: str, name: str = "") -> None:
        self._ctx_stack.append((tag, name))

    def _pop_ctx(self) -> None:
        if len(self._ctx_stack) > 1:
            self._ctx_stack.pop()

    def _context_label(self, block: KeywordBlock) -> str:
        return "/".join(f"{t}({n})" if n else t for t, n in self._ctx_stack)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, block: KeywordBlock) -> None:
        kw = block.keyword

        # Keywords that close a context come first
        if kw in ("END PART", "ENDPART"):
            self._end_part(block)
            return
        if kw in ("END ASSEMBLY", "ENDASSEMBLY"):
            self._end_assembly(block)
            return
        if kw in ("END INSTANCE", "ENDINSTANCE"):
            self._end_instance(block)
            return
        if kw in ("END STEP", "ENDSTEP"):
            self._end_step(block)
            return

        # If we are in MATERIAL context and this is not a material sub-keyword,
        # implicitly close the material context first.
        if self._ctx == CTX_MATERIAL and kw not in _MATERIAL_SUB_KEYWORDS:
            self._pop_ctx()
            self._current_material = None

        # If a *Coupling is pending and this is not its sub-keyword, clear the pointer.
        if self._current_coupling is not None and kw not in _COUPLING_SUB_KEYWORDS:
            self._current_coupling = None

        if self._current_design_response is not None and kw not in _DESIGN_RESPONSE_SUB_KEYWORDS:
            self._current_design_response = None

        handler = _KEYWORD_HANDLERS.get(kw)
        if handler is None:
            # Silently skip known-harmless keywords (file metadata, output requests, etc.)
            if kw not in _SILENT_SKIP_KEYWORDS:
                self._diag.warning(
                    UNKNOWN_KEYWORD,
                    f"Unrecognised keyword *{kw}",
                    file=block.source_file, line=block.source_line,
                    context=self._context_label(block),
                )
            return

        handler(self, block)

    # ------------------------------------------------------------------
    # Part
    # ------------------------------------------------------------------

    def _handle_part(self, block: KeywordBlock) -> None:
        name = block.params.get("name", f"__part_{len(self._model.parts)}")
        part = Part(name=name)
        self._model.parts[name] = part
        self._current_part = part
        self._push_ctx(CTX_PART, name)

    def _end_part(self, block: KeywordBlock) -> None:
        self._current_part = None
        if self._ctx == CTX_PART:
            self._pop_ctx()

    # ------------------------------------------------------------------
    # Assembly
    # ------------------------------------------------------------------

    def _handle_assembly(self, block: KeywordBlock) -> None:
        name = block.params.get("name", "Assembly")
        asm = Assembly(name=name)
        self._model.assembly = asm
        self._current_assembly = asm
        self._push_ctx(CTX_ASSEMBLY, name)

    def _end_assembly(self, block: KeywordBlock) -> None:
        self._current_assembly = None
        if self._ctx == CTX_ASSEMBLY:
            self._pop_ctx()

    # ------------------------------------------------------------------
    # Instance
    # ------------------------------------------------------------------

    def _handle_instance(self, block: KeywordBlock) -> None:
        name = block.params.get("name", "")
        part_name = block.params.get("part", "")
        inst = Instance(name=name, part_name=part_name)
        if self._current_assembly is not None:
            self._current_assembly.instances[name] = inst
        self._current_instance = inst
        self._instance_data = list(block.data_lines)
        self._push_ctx(CTX_INSTANCE, name)

    def _end_instance(self, block: KeywordBlock) -> None:
        if self._current_instance is not None:
            self._apply_instance_transform(self._current_instance, self._instance_data)
            self._current_instance = None
            self._instance_data = []
        if self._ctx == CTX_INSTANCE:
            self._pop_ctx()

    def _apply_instance_transform(self, inst: Instance, data_lines: List[str]) -> None:
        """
        Parse translation + optional rotation from Instance data lines.

        Abaqus *Instance transform syntax:
        Line 1 (if present): tx, ty, tz
        Line 2 (if present): x1, y1, z1, x2, y2, z2, angle_deg

        where (x1, y1, z1) and (x2, y2, z2) are two points on the
        rotation axis line.
        """
        if not data_lines:
            return
        try:
            t = [float(v) for v in data_lines[0].split(",") if v.strip()]
            if len(t) >= 3:
                inst.translation = (t[0], t[1], t[2])
        except ValueError:
            self._diag.warning(MALFORMED_DATA,
                               f"Cannot parse translation for instance '{inst.name}'",
                               file="", line=0)
            return

        if len(data_lines) < 2:
            return
        try:
            r = [float(v) for v in data_lines[1].split(",") if v.strip()]
            if len(r) >= 7:
                inst.rotation = Rotation(
                    center=(r[0], r[1], r[2]),
                    axis=(r[3], r[4], r[5]),
                    angle_deg=r[6],
                )
        except ValueError:
            self._diag.warning(MALFORMED_DATA,
                               f"Cannot parse rotation for instance '{inst.name}'",
                               file="", line=0)

    # ------------------------------------------------------------------
    # Node
    # ------------------------------------------------------------------

    def _handle_node(self, block: KeywordBlock) -> None:
        # Optional inline nset
        inline_nset = block.params.get("nset")

        # Determine target Part
        target_part = self._current_part
        if target_part is None and self._ctx not in (CTX_INSTANCE,):
            # nodes defined outside part/instance — create an implicit root part
            if "PART-1-1" not in self._model.parts:
                self._model.parts["PART-1-1"] = Part(name="PART-1-1")
            target_part = self._model.parts["PART-1-1"]

        labels_for_nset: List[int] = []

        for line in block.data_lines:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 3:
                continue
            try:
                label = int(parts[0])
                x = float(parts[1])
                y = float(parts[2])
                z = float(parts[3]) if len(parts) > 3 else 0.0
            except (ValueError, IndexError):
                self._diag.warning(MALFORMED_DATA, f"Bad node line: {line!r}",
                                   file=block.source_file, line=block.source_line)
                continue

            node = Node(label=label, x=x, y=y, z=z)
            if target_part is not None:
                target_part.nodes[label] = node
            labels_for_nset.append(label)

        # Handle inline *Node, nset=...
        if inline_nset and target_part is not None:
            name = inline_nset.strip()
            if name not in target_part.nsets:
                target_part.nsets[name] = Nset(name=name)
            target_part.nsets[name].node_labels.extend(labels_for_nset)

    # ------------------------------------------------------------------
    # Element
    # ------------------------------------------------------------------

    def _handle_element(self, block: KeywordBlock) -> None:
        abaqus_type = block.params.get("type", "").strip().upper()
        factory_type = map_element_type(abaqus_type) or "UNKNOWN"

        if factory_type == "UNKNOWN":
            self._diag.warning(
                UNKNOWN_ELEMENT_TYPE,
                f"Element type '{abaqus_type}' not in mapping table",
                file=block.source_file, line=block.source_line,
            )

        inline_elset = block.params.get("elset")
        target_part = self._current_part

        if target_part is None:
            if "PART-1-1" not in self._model.parts:
                self._model.parts["PART-1-1"] = Part(name="PART-1-1")
            target_part = self._model.parts["PART-1-1"]

        labels_for_elset: List[int] = []
        # High-order elements span multiple physical lines but the Lexer has
        # already joined continuation lines, so we still handle multi-line
        # connectivity by accumulating tokens until we have enough node labels.
        n_corner = _CORNER_NODE_COUNT.get(factory_type, 0)

        i = 0
        lines = block.data_lines
        while i < len(lines):
            tokens = [t.strip() for t in lines[i].split(",") if t.strip()]
            i += 1
            if not tokens:
                continue
            try:
                elem_label = int(tokens[0])
            except ValueError:
                self._diag.warning(MALFORMED_DATA, f"Bad element line: {lines[i-1]!r}",
                                   file=block.source_file, line=block.source_line)
                continue

            node_tokens = tokens[1:]

            # Accumulate across physical lines if not enough nodes yet
            while n_corner > 0 and len(node_tokens) < n_corner and i < len(lines):
                extra = [t.strip() for t in lines[i].split(",") if t.strip()]
                node_tokens.extend(extra)
                i += 1

            node_labels: List[int] = []
            for tok in node_tokens:
                if not tok:
                    continue
                try:
                    node_labels.append(int(tok))
                except ValueError:
                    # Could be a "part.id" reference (rare); skip token
                    continue

            elem = Element(
                label=elem_label,
                abaqus_type=abaqus_type,
                factory_type=factory_type,
                node_labels=node_labels,
            )
            target_part.elements[elem_label] = elem
            labels_for_elset.append(elem_label)

        if inline_elset and target_part is not None:
            name = inline_elset.strip()
            if name not in target_part.elsets:
                target_part.elsets[name] = Elset(name=name)
            target_part.elsets[name].elem_labels.extend(labels_for_elset)

    # ------------------------------------------------------------------
    # Nset / Elset
    # ------------------------------------------------------------------

    def _handle_nset(self, block: KeywordBlock) -> None:
        name = block.params.get("nset", "").strip()
        if not name:
            return
        is_generate = "generate" in block.params
        is_internal = "internal" in block.params
        if is_internal:
            return  # Abaqus internal sets — skip

        instance_name = block.params.get("instance")

        if self._ctx in (CTX_ASSEMBLY, CTX_INSTANCE) and self._current_assembly:
            asm = self._current_assembly
            if name not in asm.nsets:
                asm.nsets[name] = AssemblyNset(name=name, instance_name=instance_name)
            target = asm.nsets[name]
            labels, refs = _parse_set_data(block.data_lines, is_generate)
            target.node_labels.extend(labels)
            target.set_refs.extend(refs)
        else:
            part = self._current_part
            if part is None:
                if "PART-1-1" not in self._model.parts:
                    self._model.parts["PART-1-1"] = Part(name="PART-1-1")
                part = self._model.parts["PART-1-1"]
            if name not in part.nsets:
                part.nsets[name] = Nset(name=name)
            target = part.nsets[name]
            labels, refs = _parse_set_data(block.data_lines, is_generate)
            target.node_labels.extend(labels)
            target.set_refs.extend(refs)

    def _handle_elset(self, block: KeywordBlock) -> None:
        name = block.params.get("elset", "").strip()
        if not name:
            return
        is_generate = "generate" in block.params
        is_internal = "internal" in block.params
        if is_internal:
            return

        instance_name = block.params.get("instance")

        if self._ctx in (CTX_ASSEMBLY, CTX_INSTANCE) and self._current_assembly:
            asm = self._current_assembly
            if name not in asm.elsets:
                asm.elsets[name] = AssemblyElset(name=name, instance_name=instance_name)
            target = asm.elsets[name]
            labels, refs = _parse_set_data(block.data_lines, is_generate)
            target.elem_labels.extend(labels)
            target.set_refs.extend(refs)
        else:
            part = self._current_part
            if part is None:
                if "PART-1-1" not in self._model.parts:
                    self._model.parts["PART-1-1"] = Part(name="PART-1-1")
                part = self._model.parts["PART-1-1"]
            if name not in part.elsets:
                part.elsets[name] = Elset(name=name)
            target = part.elsets[name]
            labels, refs = _parse_set_data(block.data_lines, is_generate)
            target.elem_labels.extend(labels)
            target.set_refs.extend(refs)

    # ------------------------------------------------------------------
    # Surface
    # ------------------------------------------------------------------

    def _handle_surface(self, block: KeywordBlock) -> None:
        name = block.params.get("name", "").strip()
        surf_type = block.params.get("type", "ELEMENT").upper()
        if not name:
            return

        surface = Surface(name=name, surface_type=surf_type)
        for line in block.data_lines:
            parts = [p.strip() for p in line.split(",")]
            if not parts or not parts[0]:
                continue
            ref_name = parts[0]
            face_id = parts[1].strip().upper() if len(parts) > 1 else ""
            surface.entries.append(SurfaceEntry(ref_name=ref_name, face_id=face_id))

        if self._ctx in (CTX_ASSEMBLY, CTX_INSTANCE) and self._current_assembly:
            self._current_assembly.surfaces[name] = surface
        elif self._current_part is not None:
            self._current_part.surfaces[name] = surface
        else:
            # Flat-format INP: surface defined outside any block → store in PART-1-1
            if "PART-1-1" not in self._model.parts:
                self._model.parts["PART-1-1"] = Part(name="PART-1-1")
            self._model.parts["PART-1-1"].surfaces[name] = surface

    # ------------------------------------------------------------------
    # Sections
    # ------------------------------------------------------------------

    def _handle_solid_section(self, block: KeywordBlock) -> None:
        elset = block.params.get("elset", "").strip()
        mat   = block.params.get("material", "").strip()
        ori   = block.params.get("orientation")
        sec = Section(section_type="SOLID", elset_name=elset,
                      material_name=mat, orientation_name=ori)
        self._add_section(sec)

    def _handle_shell_section(self, block: KeywordBlock) -> None:
        elset = block.params.get("elset", "").strip()
        mat   = block.params.get("material", "").strip()
        ori   = block.params.get("orientation")
        composite = "composite" in block.params
        thickness: Optional[float] = None
        thickness_expr: Optional[str] = None
        thickness_param: Optional[str] = None
        if not composite and block.data_lines:
            token = block.data_lines[0].split(",")[0] if block.data_lines[0] else ""
            thickness, thickness_expr, thickness_param = self._parse_scalar_token(token)
        sec = Section(section_type="SHELL", elset_name=elset,
                      material_name=mat, orientation_name=ori,
                      thickness=thickness,
                      thickness_expression=thickness_expr,
                      thickness_parameter=thickness_param,
                      extra={"composite": composite})
        self._add_section(sec)

    def _handle_beam_section(self, block: KeywordBlock) -> None:
        elset   = block.params.get("elset", "").strip()
        mat     = block.params.get("material", "").strip()
        section = block.params.get("section", "").strip()
        ori     = block.params.get("orientation")
        # First data line: section dimensions; second: beam normal direction
        dims:   List[float] = []
        dim_expressions: List[Optional[str]] = []
        dim_parameters: List[Optional[str]] = []
        normal: List[float] = []
        if len(block.data_lines) > 0:
            for token in block.data_lines[0].split(","):
                if not token.strip():
                    continue
                value, expression, parameter_name = self._parse_scalar_token(token)
                dims.append(value if value is not None else 0.0)
                dim_expressions.append(expression)
                dim_parameters.append(parameter_name)
        if len(block.data_lines) > 1:
            try:
                normal = [float(v) for v in block.data_lines[1].split(",") if v.strip()]
            except ValueError:
                pass
        sec = Section(section_type="BEAM", elset_name=elset,
                      material_name=mat, orientation_name=ori,
                      extra={
                          "section_shape": section,
                          "dims": dims,
                          "dim_expressions": dim_expressions,
                          "dim_parameters": dim_parameters,
                          "normal": normal,
                      })
        self._add_section(sec)

    def _handle_membrane_section(self, block: KeywordBlock) -> None:
        elset = block.params.get("elset", "").strip()
        mat   = block.params.get("material", "").strip()
        thickness: Optional[float] = None
        thickness_expr: Optional[str] = None
        thickness_param: Optional[str] = None
        if block.data_lines:
            token = block.data_lines[0].split(",")[0] if block.data_lines[0] else ""
            thickness, thickness_expr, thickness_param = self._parse_scalar_token(token)
        sec = Section(section_type="MEMBRANE", elset_name=elset,
                      material_name=mat, thickness=thickness,
                      thickness_expression=thickness_expr,
                      thickness_parameter=thickness_param)
        self._add_section(sec)

    def _add_section(self, sec: Section) -> None:
        if self._current_part is not None:
            self._current_part.sections.append(sec)
            return

        # Section assignment inside the *Assembly block (references an
        # instance-scoped elset). Keep it on the assembly so the exporter can
        # resolve it per-instance; routing it to a phantom PART-1-1 would drop
        # the material because that part has no nodes/elements.
        if self._ctx in (CTX_ASSEMBLY, CTX_INSTANCE) and self._current_assembly is not None:
            self._current_assembly.sections.append(sec)
            return

        if "PART-1-1" not in self._model.parts:
            self._model.parts["PART-1-1"] = Part(name="PART-1-1")
        self._model.parts["PART-1-1"].sections.append(sec)

    # ------------------------------------------------------------------
    # Material
    # ------------------------------------------------------------------

    def _handle_material(self, block: KeywordBlock) -> None:
        name = block.params.get("name", "").strip()
        if not name:
            return
        mat = Material(name=name)
        self._model.materials[name] = mat
        self._current_material = mat
        self._push_ctx(CTX_MATERIAL, name)

    def _handle_elastic(self, block: KeywordBlock) -> None:
        if self._current_material is None:
            return
        elastic_type = block.params.get("type", "ISOTROPIC").upper()
        data = _parse_float_table(block.data_lines)
        self._current_material.elastic = ElasticData(
            elastic_type=elastic_type, data=data
        )

    def _handle_plastic(self, block: KeywordBlock) -> None:
        if self._current_material is None:
            return
        hardening = block.params.get("hardening", "ISOTROPIC").upper()
        data = _parse_float_table(block.data_lines)
        self._current_material.plastic = PlasticData(hardening=hardening, data=data)

    def _handle_density(self, block: KeywordBlock) -> None:
        if self._current_material is None:
            return
        self._current_material.density_data = _parse_float_table(block.data_lines)

    def _handle_conductivity(self, block: KeywordBlock) -> None:
        if self._current_material is None:
            return
        self._current_material.conductivity_data = _parse_float_table(block.data_lines)

    def _handle_expansion(self, block: KeywordBlock) -> None:
        if self._current_material is None:
            return
        self._current_material.expansion_data = _parse_float_table(block.data_lines)

    def _handle_specific_heat(self, block: KeywordBlock) -> None:
        if self._current_material is None:
            return
        self._current_material.specific_heat_data = _parse_float_table(block.data_lines)

    def _handle_hyperelastic(self, block: KeywordBlock) -> None:
        if self._current_material is None:
            return
        # Model name may appear as a flag param or in keyword itself
        model = next(
            (k.upper() for k in block.params if k not in ("test data input", "n")),
            "NEO HOOKE"
        )
        data = _parse_float_table(block.data_lines)
        self._current_material.hyperelastic = HyperelasticData(model=model, data=data)

    def _handle_damage_initiation(self, block: KeywordBlock) -> None:
        if self._current_material is None:
            return
        criterion = block.params.get("criterion", "DUCTILE").upper()
        data = _parse_float_table(block.data_lines)
        self._current_material.damage_initiation = DamageData(criterion=criterion, data=data)

    def _handle_damage_evolution(self, block: KeywordBlock) -> None:
        if self._current_material is None:
            return
        d_type = block.params.get("type", "DISPLACEMENT").upper()
        data = _parse_float_table(block.data_lines)
        self._current_material.damage_evolution = DamageData(criterion=d_type, data=data)

    def _handle_creep(self, block: KeywordBlock) -> None:
        if self._current_material is None:
            return
        law = block.params.get("law", "STRAIN").upper()
        data = _parse_float_table(block.data_lines)
        self._current_material.creep = CreepData(law=law, data=data)

    def _handle_user_material(self, block: KeywordBlock) -> None:
        self._diag.info(UMAT_SKIPPED, "*User Material encountered — skipped",
                        file=block.source_file, line=block.source_line)

    # ------------------------------------------------------------------
    # Amplitude
    # ------------------------------------------------------------------

    def _handle_amplitude(self, block: KeywordBlock) -> None:
        name = block.params.get("name", "").strip()
        if not name:
            return
        amp = Amplitude(name=name)
        for line in block.data_lines:
            vals = [v.strip() for v in line.split(",") if v.strip()]
            # Each pair is (time, value)
            for j in range(0, len(vals) - 1, 2):
                try:
                    amp.times.append(float(vals[j]))
                    amp.values.append(float(vals[j + 1]))
                except ValueError:
                    pass
        self._model.amplitudes[name] = amp

    # ------------------------------------------------------------------
    # Orientation
    # ------------------------------------------------------------------

    def _handle_orientation(self, block: KeywordBlock) -> None:
        name   = block.params.get("name", "").strip()
        system = block.params.get("system", "RECTANGULAR").upper()
        if not name:
            return
        data: List[float] = []
        for line in block.data_lines:
            try:
                data.extend(float(v) for v in line.split(",") if v.strip())
            except ValueError:
                pass
        self._model.orientations[name] = Orientation(name=name, system=system, data=data)

    def _handle_parameter(self, block: KeywordBlock) -> None:
        for line in block.data_lines:
            for name, expression in _parse_parameter_assignments(line):
                scalar_value, refs = _evaluate_parameter_expression(
                    expression,
                    {
                        key: item.scalar_value
                        for key, item in self._model.parameters.items()
                        if item.scalar_value is not None
                    },
                )
                self._model.parameters[name] = ParameterDefinition(
                    name=name,
                    expression=expression,
                    scalar_value=scalar_value,
                    referenced_parameters=refs,
                )

    def _handle_design_parameter(self, block: KeywordBlock) -> None:
        seen = {item.name for item in self._model.design_parameters}
        for line in block.data_lines:
            for token in line.split(","):
                name = token.strip()
                if not name or name in seen:
                    continue
                seen.add(name)
                self._model.design_parameters.append(
                    DesignParameter(name=name, order=len(self._model.design_parameters) + 1)
                )

    def _handle_design_response(self, block: KeywordBlock) -> None:
        frequency_raw = block.params.get("frequency", "1")
        try:
            frequency = int(float(frequency_raw))
        except ValueError:
            frequency = 1
        response = DesignResponse(
            step_name=self._current_step.name if self._current_step is not None else None,
            frequency=frequency,
            extra={"params": dict(block.params)},
        )
        self._model.design_responses.append(response)
        self._current_design_response = response

    def _handle_element_response(self, block: KeywordBlock) -> None:
        if self._current_design_response is None:
            return
        elset_name = block.params.get("elset", "").strip()
        if not elset_name:
            return
        variables = _parse_response_variables(block.data_lines)
        self._current_design_response.requests.append(
            DesignResponseRequest(region_type="ELEMENT", set_name=elset_name, variables=variables)
        )

    def _handle_node_response(self, block: KeywordBlock) -> None:
        if self._current_design_response is None:
            return
        nset_name = block.params.get("nset", "").strip()
        if not nset_name:
            return
        variables = _parse_response_variables(block.data_lines)
        self._current_design_response.requests.append(
            DesignResponseRequest(region_type="NODE", set_name=nset_name, variables=variables)
        )

    # ------------------------------------------------------------------
    # Step
    # ------------------------------------------------------------------

    def _handle_step(self, block: KeywordBlock) -> None:
        name   = block.params.get("name", f"Step-{len(self._model.steps)+1}")
        nlgeom = block.params.get("nlgeom", "").upper() in ("YES", "ON", "1", "")
        # nlgeom defaults to NO in Abaqus; only YES if explicitly set
        nlgeom = block.params.get("nlgeom", "NO").upper() in ("YES", "ON", "1")
        step = StepDeclaration(name=name, nlgeom=nlgeom)
        self._model.steps.append(step)
        self._current_step = step
        self._push_ctx(CTX_STEP, name)

    def _end_step(self, block: KeywordBlock) -> None:
        self._current_step = None
        self._current_design_response = None
        if self._ctx == CTX_STEP:
            self._pop_ctx()

    def _handle_step_type(self, block: KeywordBlock) -> None:
        """Handles *Static, *Dynamic, *Frequency, *Buckle, etc."""
        if self._current_step is None:
            return
        kw = block.keyword
        type_map = {
            "STATIC":                          "STATIC",
            "DYNAMIC":                         "DYNAMIC",
            "FREQUENCY":                       "FREQUENCY",
            "BUCKLE":                          "BUCKLE",
            "VISCO":                           "VISCO",
            "HEAT TRANSFER":                   "HEAT TRANSFER",
            "COUPLED TEMPERATURE-DISPLACEMENT": "COUPLED TEMP-DISP",
            "SOILS":                           "SOILS",
            "GEOSTATIC":                       "GEOSTATIC",
            "MODAL DYNAMIC":                   "MODAL DYNAMIC",
            "STEADY STATE DYNAMICS":           "STEADY STATE DYNAMICS",
        }
        self._current_step.step_type = type_map.get(kw, kw)

    def _handle_boundary(self, block: KeywordBlock) -> None:
        if self._current_step is None:
            return
        op = block.params.get("op", "MOD").upper()
        amp = block.params.get("amplitude")
        for line in block.data_lines:
            parts = [p.strip() for p in line.split(",")]
            if not parts or not parts[0]:
                continue
            nset_name = parts[0]
            if len(parts) < 2:
                continue

            # Check for type keyword (ENCASTRE, XSYMM, etc.)
            type_kw = parts[1].upper()
            if type_kw in _BC_TYPE_MAP:
                for dof_s, dof_e in _BC_TYPE_MAP[type_kw]:
                    self._current_step.boundary_conditions.append(
                        BCDeclaration(nset_name=nset_name,
                                      dof_start=dof_s, dof_end=dof_e,
                                      value=0.0, op=op, amplitude_name=amp)
                    )
            else:
                try:
                    dof_start = int(parts[1])
                    dof_end   = int(parts[2]) if len(parts) > 2 else dof_start
                    value     = float(parts[3]) if len(parts) > 3 else 0.0
                except ValueError:
                    self._diag.warning(MALFORMED_DATA,
                                       f"Bad *Boundary line: {line!r}",
                                       file=block.source_file, line=block.source_line)
                    continue
                self._current_step.boundary_conditions.append(
                    BCDeclaration(nset_name=nset_name,
                                  dof_start=dof_start, dof_end=dof_end,
                                  value=value, op=op, amplitude_name=amp)
                )

    def _handle_cload(self, block: KeywordBlock) -> None:
        if self._current_step is None:
            return
        amp = block.params.get("amplitude")
        for line in block.data_lines:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 3:
                continue
            try:
                nset_name = parts[0]
                dof   = int(parts[1])
                value = float(parts[2])
            except ValueError:
                self._diag.warning(MALFORMED_DATA, f"Bad *Cload line: {line!r}",
                                   file=block.source_file, line=block.source_line)
                continue
            self._current_step.cloads.append(
                CLoadDeclaration(nset_name=nset_name, dof=dof,
                                 value=value, amplitude_name=amp)
            )

    def _handle_dload(self, block: KeywordBlock) -> None:
        if self._current_step is None:
            return
        amp = block.params.get("amplitude")
        for line in block.data_lines:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 3:
                continue
            try:
                elset_name = parts[0]
                load_type  = parts[1].upper()
                magnitude  = float(parts[2])
            except (ValueError, IndexError):
                self._diag.warning(MALFORMED_DATA, f"Bad *Dload line: {line!r}",
                                   file=block.source_file, line=block.source_line)
                continue
            self._current_step.dloads.append(
                DLoadDeclaration(elset_name=elset_name, load_type=load_type,
                                 magnitude=magnitude, amplitude_name=amp)
            )

    def _handle_dsload(self, block: KeywordBlock) -> None:
        if self._current_step is None:
            return
        amp = block.params.get("amplitude")
        for line in block.data_lines:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 3:
                continue
            try:
                surface_name = parts[0]
                load_type    = parts[1].upper()
                magnitude    = float(parts[2])
            except (ValueError, IndexError):
                self._diag.warning(MALFORMED_DATA, f"Bad *Dsload line: {line!r}",
                                   file=block.source_file, line=block.source_line)
                continue
            self._current_step.dsloads.append(
                DsloadDeclaration(surface_name=surface_name, load_type=load_type,
                                  magnitude=magnitude, amplitude_name=amp)
            )

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------

    def _handle_tie(self, block: KeywordBlock) -> None:
        name = block.params.get("name", "").strip()
        if not name:
            self._diag.warning(MALFORMED_DATA, "*Tie missing name parameter",
                               file=block.source_file, line=block.source_line)
            return
        adjust_str = block.params.get("adjust", "YES").upper()
        tie_type   = block.params.get("type", "SURFACE TO SURFACE").upper()
        adjust     = (adjust_str != "NO")

        master, slave = "", ""
        if block.data_lines:
            cols = [c.strip() for c in block.data_lines[0].split(",")]
            if len(cols) >= 2:
                master, slave = cols[0], cols[1]
            elif len(cols) == 1:
                master = cols[0]
                self._diag.warning(MALFORMED_DATA,
                                   f"*Tie '{name}' missing slave surface",
                                   file=block.source_file, line=block.source_line)

        tie = TieConstraint(name=name, master_surface=master, slave_surface=slave,
                            adjust=adjust, tie_type=tie_type)

        target_asm = self._current_assembly or self._model.assembly
        if target_asm is not None:
            target_asm.ties.append(tie)
        else:
            self._diag.warning(UNSUPPORTED_PARAM,
                               f"*Tie '{name}' defined outside Assembly — skipped",
                               file=block.source_file, line=block.source_line)

    def _handle_coupling(self, block: KeywordBlock) -> None:
        # Abaqus CAE exports "constraint name=", hand-written INPs often use "name="
        name = (block.params.get("constraint name")
                or block.params.get("name", "")).strip()
        ref_node = block.params.get("ref node", block.params.get("ref_node", "")).strip()
        surface  = block.params.get("surface", "").strip()
        if not name:
            self._diag.warning(MALFORMED_DATA, "*Coupling missing name parameter",
                               file=block.source_file, line=block.source_line)
            return

        target_asm = self._current_assembly or self._model.assembly
        if target_asm is None:
            # Flat-format INP: synthesize a minimal assembly to hold couplings
            if self._model.parts:
                self._model.assembly = Assembly(name="synthesized-flat")
                target_asm = self._model.assembly
            else:
                self._diag.warning(UNSUPPORTED_PARAM,
                                   f"*Coupling '{name}' defined outside Assembly — skipped",
                                   file=block.source_file, line=block.source_line)
                return

        coupling = CouplingConstraint(name=name, ref_node=ref_node, surface=surface)
        target_asm.couplings.append(coupling)
        self._current_coupling = coupling

    def _handle_kinematic(self, block: KeywordBlock) -> None:
        if self._current_coupling is None:
            return
        self._current_coupling.coupling_type = "KINEMATIC"
        for line in block.data_lines:
            cols = [c.strip() for c in line.split(",") if c.strip()]
            try:
                if len(cols) >= 2:
                    self._current_coupling.dof_ranges.append((int(cols[0]), int(cols[1])))
                elif len(cols) == 1:
                    d = int(cols[0])
                    self._current_coupling.dof_ranges.append((d, d))
            except ValueError:
                self._diag.warning(MALFORMED_DATA, f"Bad *Kinematic DOF line: {line!r}",
                                   file=block.source_file, line=block.source_line)

    def _handle_distributing(self, block: KeywordBlock) -> None:
        if self._current_coupling is None:
            return
        self._current_coupling.coupling_type = "DISTRIBUTING"

    # ------------------------------------------------------------------
    # Time Points
    # ------------------------------------------------------------------

    def _handle_time_points(self, block: KeywordBlock) -> None:
        name = block.params.get("name", "").strip()
        if not name:
            self._diag.warning(MALFORMED_DATA, "*Time Points missing name parameter",
                               file=block.source_file, line=block.source_line)
            return
        if name in self._model.time_points:
            self._diag.warning(DUPLICATE_NAME,
                               f"*Time Points '{name}' redefined — overwriting previous definition",
                               file=block.source_file, line=block.source_line)
        tp = TimePoints(name=name)
        for line in block.data_lines:
            for tok in line.split(","):
                tok = tok.strip()
                if not tok:
                    continue
                try:
                    tp.times.append(float(tok))
                except ValueError:
                    self._diag.warning(MALFORMED_DATA,
                                       f"Bad *Time Points value: {tok!r}",
                                       file=block.source_file, line=block.source_line)
        self._model.time_points[name] = tp

    def _parse_scalar_token(self, token: str) -> Tuple[Optional[float], Optional[str], Optional[str]]:
        raw = str(token or "").strip()
        if not raw:
            return None, None, None

        match = _PARAM_PLACEHOLDER_RE.fullmatch(raw)
        if match:
            parameter_name = match.group(1)
            parameter_def = self._model.parameters.get(parameter_name)
            resolved_value = parameter_def.scalar_value if parameter_def is not None else None
            return resolved_value, raw, parameter_name

        try:
            return float(raw), raw, None
        except ValueError:
            return None, raw, None


# ---------------------------------------------------------------------------
# Coupling sub-keywords (used to detect implicit Coupling context end in _dispatch)
# ---------------------------------------------------------------------------
_COUPLING_SUB_KEYWORDS = {"KINEMATIC", "DISTRIBUTING"}
_DESIGN_RESPONSE_SUB_KEYWORDS = {"ELEMENT RESPONSE", "NODE RESPONSE"}

# ---------------------------------------------------------------------------
# Known-harmless keywords that produce no useful data — skip silently
# (no WARNING emitted, keeps output clean for typical Abaqus CAE exports)
# ---------------------------------------------------------------------------
_SILENT_SKIP_KEYWORDS = {
    # File header / metadata
    "HEADING", "PREPRINT", "PARAMETER SHAPE VARIATION",
    # Output requests (post-processing, not geometry/material)
    "OUTPUT", "NODE OUTPUT", "ELEMENT OUTPUT", "CONTACT OUTPUT",
    "ENERGY OUTPUT", "MODAL OUTPUT", "RADIATION OUTPUT",
    "RESTART", "MONITOR", "PRINT",
    # Interactions / contact (Phase 2+)
    "CONTACT", "CONTACT PAIR", "CONTACT INCLUSIONS", "CONTACT EXCLUSIONS",
    "SURFACE INTERACTION", "FRICTION", "SURFACE BEHAVIOR",
    "CONTACT PROPERTY ASSIGNMENT", "CONTACT FORMULATION",
    "GENERAL CONTACT",
    # Constraints (Phase 2+)
    "CONNECTOR SECTION", "CONNECTOR BEHAVIOR",
    "CONNECTOR ELASTICITY", "CONNECTOR DAMPING",
    "MPC", "EQUATION",
    "RIGID BODY", "PRE-TENSION SECTION",
    "EMBEDDED ELEMENT",
    # Initial / predefined fields
    "INITIAL CONDITIONS", "PREDEFINED FIELD", "TEMPERATURE",
    # Controls / solver settings
    "CONTROLS", "SOLUTION TECHNIQUE",
    "SELECT EIGENMODES", "AMS",
    # Misc Abaqus/CAE generated
    "SYSTEM", "TRANSFORM", "NORMAL", "NODAL THICKNESS",
    "REBAR LAYER", "GASKET BEHAVIOR",
}

# ---------------------------------------------------------------------------
# Material sub-keyword set (used to detect implicit Material context end)
# ---------------------------------------------------------------------------
_MATERIAL_SUB_KEYWORDS = {
    "ELASTIC", "PLASTIC", "DENSITY", "CONDUCTIVITY", "EXPANSION",
    "SPECIFIC HEAT", "HYPERELASTIC", "HYPERFOAM", "DAMAGE INITIATION",
    "DAMAGE EVOLUTION", "DAMAGE STABILIZATION", "CREEP", "VISCOUS",
    "RATE DEPENDENT", "CYCLIC HARDENING", "DRUCKER PRAGER",
    "DRUCKER PRAGER HARDENING", "CAP PLASTICITY", "MOHR COULOMB PLASTICITY",
    "CONCRETE DAMAGED PLASTICITY", "CONCRETE TENSION STIFFENING",
    "CONCRETE COMPRESSION HARDENING", "BRITTLE CRACKING",
    "VISCOELASTIC", "LATENT HEAT", "USER MATERIAL",
    "CONNECTOR BEHAVIOR", "CONNECTOR ELASTICITY",
}

# ---------------------------------------------------------------------------
# Corner node counts (for multi-line element connectivity)
# ---------------------------------------------------------------------------
_CORNER_NODE_COUNT: Dict[str, int] = {
    "TET4": 4, "TET10": 10,
    "WEDGE6": 6, "WEDGE15": 15,
    "HEX8": 8, "HEX20": 20,
    "TRI3": 3, "TRI6": 6,
    "QUAD4": 4, "QUAD8": 8,
    "LINE2": 2, "LINE3": 3,
    "POINT": 1,
}

# ---------------------------------------------------------------------------
# Keyword → handler mapping
# ---------------------------------------------------------------------------
_p = InpParser  # shorthand

_KEYWORD_HANDLERS: Dict[str, HandlerFn] = {
    # Structure
    "PART":             _p._handle_part,
    "ASSEMBLY":         _p._handle_assembly,
    "INSTANCE":         _p._handle_instance,
    "PARAMETER":        _p._handle_parameter,
    "DESIGN PARAMETER": _p._handle_design_parameter,
    # Geometry
    "NODE":             _p._handle_node,
    "ELEMENT":          _p._handle_element,
    "NSET":             _p._handle_nset,
    "ELSET":            _p._handle_elset,
    "SURFACE":          _p._handle_surface,
    # Sections
    "SOLID SECTION":    _p._handle_solid_section,
    "SHELL SECTION":    _p._handle_shell_section,
    "BEAM SECTION":     _p._handle_beam_section,
    "MEMBRANE SECTION": _p._handle_membrane_section,
    # Material
    "MATERIAL":         _p._handle_material,
    "ELASTIC":          _p._handle_elastic,
    "PLASTIC":          _p._handle_plastic,
    "DENSITY":          _p._handle_density,
    "CONDUCTIVITY":     _p._handle_conductivity,
    "EXPANSION":        _p._handle_expansion,
    "SPECIFIC HEAT":    _p._handle_specific_heat,
    "HYPERELASTIC":     _p._handle_hyperelastic,
    "HYPERFOAM":        _p._handle_hyperelastic,
    "DAMAGE INITIATION": _p._handle_damage_initiation,
    "DAMAGE EVOLUTION":  _p._handle_damage_evolution,
    "CREEP":             _p._handle_creep,
    "USER MATERIAL":     _p._handle_user_material,
    # Other model objects
    "AMPLITUDE":        _p._handle_amplitude,
    "ORIENTATION":      _p._handle_orientation,
    # Step
    "STEP":             _p._handle_step,
    "DESIGN RESPONSE":  _p._handle_design_response,
    "ELEMENT RESPONSE": _p._handle_element_response,
    "NODE RESPONSE":    _p._handle_node_response,
    "STATIC":           _p._handle_step_type,
    "DYNAMIC":          _p._handle_step_type,
    "FREQUENCY":        _p._handle_step_type,
    "BUCKLE":           _p._handle_step_type,
    "VISCO":            _p._handle_step_type,
    "HEAT TRANSFER":    _p._handle_step_type,
    "COUPLED TEMPERATURE-DISPLACEMENT": _p._handle_step_type,
    "SOILS":            _p._handle_step_type,
    "GEOSTATIC":        _p._handle_step_type,
    "MODAL DYNAMIC":    _p._handle_step_type,
    "STEADY STATE DYNAMICS": _p._handle_step_type,
    # Loads & BCs
    "BOUNDARY":         _p._handle_boundary,
    "CLOAD":            _p._handle_cload,
    "DLOAD":            _p._handle_dload,
    "DSLOAD":           _p._handle_dsload,
    # Constraints
    "TIE":              _p._handle_tie,
    "COUPLING":         _p._handle_coupling,
    "KINEMATIC":        _p._handle_kinematic,
    "DISTRIBUTING":     _p._handle_distributing,
    # Time control
    "TIME POINTS":      _p._handle_time_points,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_set_data(
    data_lines: List[str], is_generate: bool
) -> Tuple[List[int], List[str]]:
    """
    Parse Nset/Elset data lines.
    Returns (integer_labels, set_name_refs).
    set_name_refs are names of other sets to include (nesting).
    """
    labels: List[int] = []
    refs:   List[str] = []

    if is_generate:
        # One line: start, end, increment
        for line in data_lines:
            parts = [p.strip() for p in line.split(",") if p.strip()]
            if len(parts) >= 2:
                try:
                    start = int(parts[0])
                    end   = int(parts[1])
                    inc   = int(parts[2]) if len(parts) > 2 else 1
                    labels.extend(range(start, end + 1, inc))
                except ValueError:
                    pass
        return labels, refs

    for line in data_lines:
        for tok in line.split(","):
            tok = tok.strip()
            if not tok:
                continue
            try:
                labels.append(int(tok))
            except ValueError:
                # Token is a set name reference (nesting)
                refs.append(tok)

    return labels, refs


def _parse_float_table(data_lines: List[str]) -> List[Tuple]:
    """
    Parse a block of comma-separated float data (potentially temperature-dependent).
    Each line becomes one tuple.
    """
    result = []
    for line in data_lines:
        parts = [p.strip() for p in line.split(",") if p.strip()]
        try:
            result.append(tuple(float(p) for p in parts))
        except ValueError:
            pass
    return result


_PARAM_PLACEHOLDER_RE = re.compile(r"^<\s*([A-Za-z_][A-Za-z0-9_]*)\s*>$")


def _parse_parameter_assignments(line: str) -> List[Tuple[str, str]]:
    assignments: List[Tuple[str, str]] = []
    current = ""
    depth = 0
    parts: List[str] = []
    for ch in str(line):
        if ch == "," and depth == 0:
            if current.strip():
                parts.append(current.strip())
            current = ""
            continue
        if ch == "(":
            depth += 1
        elif ch == ")" and depth > 0:
            depth -= 1
        current += ch
    if current.strip():
        parts.append(current.strip())

    for part in parts:
        if "=" not in part:
            continue
        name, _, expression = part.partition("=")
        param_name = name.strip()
        expr = expression.strip()
        if param_name and expr:
            assignments.append((param_name, expr))
    return assignments


_ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}
_ALLOWED_UNARYOPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _evaluate_parameter_expression(expression: str, values: Dict[str, Optional[float]]) -> Tuple[Optional[float], List[str]]:
    refs: List[str] = []
    text = str(expression or "").strip()
    if not text:
        return None, refs

    try:
        node = ast.parse(text.replace("^", "**"), mode="eval")
    except SyntaxError:
        return None, refs

    def _visit(current) -> float:
        if isinstance(current, ast.Expression):
            return _visit(current.body)
        if isinstance(current, ast.Constant):
            if isinstance(current.value, (int, float)):
                return float(current.value)
            raise ValueError("unsupported constant")
        if isinstance(current, ast.Num):
            return float(current.n)
        if isinstance(current, ast.Name):
            refs.append(current.id)
            if current.id not in values or values[current.id] is None:
                raise ValueError("unknown parameter reference")
            return float(values[current.id])
        if isinstance(current, ast.BinOp) and type(current.op) in _ALLOWED_BINOPS:
            return _ALLOWED_BINOPS[type(current.op)](_visit(current.left), _visit(current.right))
        if isinstance(current, ast.UnaryOp) and type(current.op) in _ALLOWED_UNARYOPS:
            return _ALLOWED_UNARYOPS[type(current.op)](_visit(current.operand))
        raise ValueError("unsupported expression")

    try:
        return float(_visit(node)), refs
    except Exception:
        return None, refs


def _parse_response_variables(data_lines: List[str]) -> List[str]:
    variables: List[str] = []
    seen = set()
    for line in data_lines:
        token = line.split(",")[0].strip().upper()
        if token and token not in seen:
            seen.add(token)
            variables.append(token)
    return variables
