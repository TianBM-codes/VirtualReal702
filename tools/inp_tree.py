"""
INP 模型树打印脚本

按照 Abaqus CAE Model Tree 的层级格式打印解析结果，便于快速核查解析是否正确。

用法:
    python tools/inp_tree.py model.inp
    python tools/inp_tree.py model.inp --no-labels    # 不打印具体标签列表
    python tools/inp_tree.py model.inp --max-labels 5 # 最多显示 N 个标签
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.inp import parse_inp, InpModel


# ANSI color codes (degrade gracefully if terminal doesn't support)
_USE_COLOR = sys.stdout.isatty()

def _c(code, text):
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text

def BOLD(t):   return _c("1", t)
def DIM(t):    return _c("2", t)
def CYAN(t):   return _c("36", t)
def GREEN(t):  return _c("32", t)
def YELLOW(t): return _c("33", t)
def RED(t):    return _c("31", t)
def BLUE(t):   return _c("34", t)


def _labels_summary(labels, max_n: int) -> str:
    if not labels:
        return DIM("(empty)")
    s = ", ".join(str(x) for x in labels[:max_n])
    if len(labels) > max_n:
        s += DIM(f", ... (+{len(labels) - max_n} more)")
    return s


def _labels_payload(labels, show_labels: bool, max_labels: int) -> dict:
    items = [int(x) for x in labels]
    payload = {
        "count": len(items),
    }
    if items:
        payload["range"] = [items[0], items[-1]]
    else:
        payload["range"] = None
    if show_labels:
        payload["preview"] = items[:max_labels]
        payload["truncated"] = max(0, len(items) - max_labels)
    return payload


def build_tree_payload(model: InpModel, show_labels: bool = True, max_labels: int = 8) -> dict:
    parts = []
    for pname, part in model.parts.items():
        elem_groups = {}
        for elem in part.elements.values():
            key = f"{elem.abaqus_type} -> {elem.factory_type}"
            elem_groups.setdefault(key, []).append(int(elem.label))

        part_payload = {
            "name": pname,
            "node_count": len(part.nodes),
            "element_count": len(part.elements),
            "nodes": _labels_payload(sorted(part.nodes.keys()), show_labels, max_labels),
            "element_groups": [],
            "nsets": [],
            "elsets": [],
            "surfaces": [],
            "sections": [],
        }

        for etype, labels in elem_groups.items():
            part_payload["element_groups"].append({
                "type": etype,
                "labels": _labels_payload(sorted(labels), show_labels, max_labels),
            })

        for nname, nset in part.nsets.items():
            part_payload["nsets"].append({
                "name": nname,
                "labels": _labels_payload(list(nset.node_labels), show_labels, max_labels),
            })

        for ename, elset in part.elsets.items():
            part_payload["elsets"].append({
                "name": ename,
                "labels": _labels_payload(list(elset.elem_labels), show_labels, max_labels),
            })

        for sname, surf in part.surfaces.items():
            part_payload["surfaces"].append({
                "name": sname,
                "surface_type": surf.surface_type,
                "entries_preview": [
                    {"ref_name": e.ref_name, "face_id": e.face_id}
                    for e in surf.entries[:max_labels]
                ],
                "entry_count": len(surf.entries),
            })

        for sec in part.sections:
            part_payload["sections"].append({
                "section_type": sec.section_type,
                "elset_name": sec.elset_name,
                "material_name": sec.material_name,
                "orientation_name": sec.orientation_name,
                "thickness": sec.thickness,
                "extra": sec.extra,
            })

        parts.append(part_payload)

    assembly_payload = None
    if model.assembly:
        asm = model.assembly
        assembly_payload = {
            "name": asm.name,
            "instances": [],
            "nsets": [],
            "elsets": [],
            "surfaces": [],
        }
        for iname, inst in asm.instances.items():
            entry = {
                "name": iname,
                "part_name": inst.part_name,
                "translation": list(inst.translation),
                "rotation": None,
            }
            if inst.rotation:
                entry["rotation"] = {
                    "center": list(inst.rotation.center),
                    "axis": list(inst.rotation.axis),
                    "angle_deg": inst.rotation.angle_deg,
                }
            assembly_payload["instances"].append(entry)

        for nname, nset in asm.nsets.items():
            assembly_payload["nsets"].append({
                "name": nname,
                "instance_name": nset.instance_name,
                "labels": _labels_payload(list(nset.node_labels), show_labels, max_labels),
            })

        for ename, elset in asm.elsets.items():
            assembly_payload["elsets"].append({
                "name": ename,
                "instance_name": elset.instance_name,
                "labels": _labels_payload(list(elset.elem_labels), show_labels, max_labels),
            })

        for sname, surf in asm.surfaces.items():
            assembly_payload["surfaces"].append({
                "name": sname,
                "surface_type": surf.surface_type,
                "entries_preview": [
                    {"ref_name": e.ref_name, "face_id": e.face_id}
                    for e in surf.entries[:max_labels]
                ],
                "entry_count": len(surf.entries),
            })

    materials = []
    for mname, mat in model.materials.items():
        props = []
        if mat.density_data:
            props.append({"type": "Density", "value": mat.density_data[0][0]})
        if mat.elastic:
            props.append({
                "type": "Elastic",
                "elastic_type": mat.elastic.elastic_type,
                "data_preview": [list(row) for row in mat.elastic.data[:max_labels]],
            })
        if mat.plastic:
            props.append({"type": "Plastic", "hardening": mat.plastic.hardening, "point_count": len(mat.plastic.data)})
        if mat.hyperelastic:
            props.append({"type": "Hyperelastic", "model": mat.hyperelastic.model})
        if mat.damage_initiation:
            props.append({"type": "DamageInitiation", "criterion": mat.damage_initiation.criterion})
        if mat.damage_evolution:
            props.append({"type": "DamageEvolution", "criterion": mat.damage_evolution.criterion})
        if mat.creep:
            props.append({"type": "Creep", "law": mat.creep.law})
        if mat.conductivity_data:
            props.append({"type": "Conductivity"})
        if mat.expansion_data:
            props.append({"type": "Expansion"})
        materials.append({"name": mname, "properties": props})

    amplitudes = []
    for aname, amp in model.amplitudes.items():
        amplitudes.append({
            "name": aname,
            "point_count": len(amp.times),
            "time_range": [amp.times[0], amp.times[-1]] if amp.times else None,
        })

    steps = []
    for step in model.steps:
        step_payload = {
            "name": step.name,
            "step_type": step.step_type,
            "nlgeom": bool(step.nlgeom),
            "boundary_conditions": [],
            "cloads": [],
            "dloads": [],
        }
        for item in step.boundary_conditions[:max_labels]:
            step_payload["boundary_conditions"].append({
                "nset_name": item.nset_name,
                "dof_start": item.dof_start,
                "dof_end": item.dof_end,
                "value": item.value,
                "amplitude_name": item.amplitude_name,
            })
        for item in step.cloads[:max_labels]:
            step_payload["cloads"].append({
                "nset_name": item.nset_name,
                "dof": item.dof,
                "value": item.value,
                "amplitude_name": item.amplitude_name,
            })
        for item in step.dloads[:max_labels]:
            step_payload["dloads"].append({
                "elset_name": item.elset_name,
                "load_type": item.load_type,
                "magnitude": item.magnitude,
                "amplitude_name": item.amplitude_name,
            })
        steps.append(step_payload)

    diagnostics = [{
        "severity": d.severity,
        "code": d.code,
        "message": d.message,
        "file": d.file,
        "line": d.line,
        "context": d.context,
    } for d in model.diagnostics]

    return {
        "summary": {
            "part_count": len(parts),
            "material_count": len(materials),
            "step_count": len(steps),
            "amplitude_count": len(amplitudes),
            "has_assembly": assembly_payload is not None,
            "diagnostic_count": len(diagnostics),
        },
        "parts": parts,
        "assembly": assembly_payload,
        "materials": materials,
        "amplitudes": amplitudes,
        "steps": steps,
        "diagnostics": diagnostics,
    }


def parse_inp_tree(inp_path: str, show_labels: bool = True, max_labels: int = 8) -> dict:
    model = parse_inp(inp_path)
    return {
        "file_path": os.path.abspath(inp_path),
        "show_labels": bool(show_labels),
        "max_labels": int(max_labels),
        "tree": build_tree_payload(model, show_labels=show_labels, max_labels=max_labels),
    }


def print_tree(model: InpModel, show_labels: bool = True, max_labels: int = 8):
    T = "├── "
    L = "└── "
    I = "│   "
    S = "    "

    def branch(items, prefix, item_fn):
        items = list(items)
        for i, item in enumerate(items):
            connector = L if i == len(items) - 1 else T
            child_prefix = prefix + (S if i == len(items) - 1 else I)
            item_fn(prefix + connector, child_prefix, item)

    # ------------------------------------------------------------------ #
    print(BOLD(f"Model"))

    # ---- Parts -------------------------------------------------------- #
    print(f"{T}{BOLD(CYAN('Parts'))}  ({len(model.parts)})")
    part_list = list(model.parts.items())
    for pi, (pname, part) in enumerate(part_list):
        is_last_part = (pi == len(part_list) - 1)
        p0 = L if is_last_part else T
        p1 = S if is_last_part else I

        print(f"{I}{p0}{BOLD(pname)}  "
              f"[{len(part.nodes)} nodes, {len(part.elements)} elements]")

        # Nodes (just count + range)
        if part.nodes:
            labels = sorted(part.nodes.keys())
            print(f"{I}{p1}{T}Nodes ({len(labels)}): "
                  f"{labels[0]} – {labels[-1]}")

        # Elements grouped by type
        type_groups = {}
        for elem in part.elements.values():
            key = f"{elem.abaqus_type} → {elem.factory_type}"
            type_groups.setdefault(key, []).append(elem.label)
        elem_items = list(type_groups.items())
        eln = len(elem_items)
        for ei, (etype, elabels) in enumerate(elem_items):
            c = L if (ei == eln - 1 and not part.nsets and
                      not part.elsets and not part.surfaces and
                      not part.sections) else T
            print(f"{I}{p1}{c}Elements/{etype} ({len(elabels)})"
                  + (f":  {_labels_summary(sorted(elabels), max_labels)}"
                     if show_labels else ""))

        # Nsets
        for ni, (nname, nset) in enumerate(part.nsets.items()):
            c = L if (ni == len(part.nsets) - 1 and
                      not part.elsets and not part.surfaces and
                      not part.sections) else T
            print(f"{I}{p1}{c}{GREEN('Nset')} {nname} ({len(nset.node_labels)})"
                  + (f":  {_labels_summary(nset.node_labels, max_labels)}"
                     if show_labels else ""))

        # Elsets
        for ei, (ename, elset) in enumerate(part.elsets.items()):
            c = L if (ei == len(part.elsets) - 1 and
                      not part.surfaces and not part.sections) else T
            print(f"{I}{p1}{c}{GREEN('Elset')} {ename} ({len(elset.elem_labels)})"
                  + (f":  {_labels_summary(elset.elem_labels, max_labels)}"
                     if show_labels else ""))

        # Surfaces
        for si, (sname, surf) in enumerate(part.surfaces.items()):
            c = L if (si == len(part.surfaces) - 1 and not part.sections) else T
            entry_str = ", ".join(f"{e.ref_name}/{e.face_id}" for e in surf.entries[:3])
            if len(surf.entries) > 3:
                entry_str += f", ..."
            print(f"{I}{p1}{c}{YELLOW('Surface')} {sname} [{surf.surface_type}]  {entry_str}")

        # Sections
        for si, sec in enumerate(part.sections):
            c = L if si == len(part.sections) - 1 else T
            extra = ""
            if sec.thickness is not None:
                extra = f"  t={sec.thickness}"
            if sec.orientation_name:
                extra += f"  ori={sec.orientation_name}"
            print(f"{I}{p1}{c}{BLUE('Section')} [{sec.section_type}]  "
                  f"elset={sec.elset_name}  mat={sec.material_name}{extra}")

    # ---- Assembly ---------------------------------------------------- #
    if model.assembly:
        asm = model.assembly
        print(f"{T}{BOLD(CYAN('Assembly'))}  \"{asm.name}\"")

        inst_list = list(asm.instances.items())
        for ii, (iname, inst) in enumerate(inst_list):
            is_last = (ii == len(inst_list) - 1 and
                       not asm.nsets and not asm.elsets and not asm.surfaces)
            c = L if is_last else T
            c2 = S if is_last else I
            tx, ty, tz = inst.translation
            rot_str = ""
            if inst.rotation:
                rot_str = (f"  rot={inst.rotation.angle_deg:.1f}° "
                           f"axis=({inst.rotation.axis[0]:.2f},"
                           f"{inst.rotation.axis[1]:.2f},"
                           f"{inst.rotation.axis[2]:.2f})")
            print(f"{I}{c}{BOLD(iname)}  → Part: {inst.part_name}"
                  f"  trans=({tx:.3g},{ty:.3g},{tz:.3g}){rot_str}")

        for ni, (nname, anset) in enumerate(asm.nsets.items()):
            c = L if (ni == len(asm.nsets)-1 and not asm.elsets and not asm.surfaces) else T
            inst_tag = f"  inst={anset.instance_name}" if anset.instance_name else ""
            print(f"{I}{c}{GREEN('Nset')} {nname} ({len(anset.node_labels)}){inst_tag}"
                  + (f":  {_labels_summary(anset.node_labels, max_labels)}"
                     if show_labels else ""))

        for ei, (ename, aelset) in enumerate(asm.elsets.items()):
            c = L if (ei == len(asm.elsets)-1 and not asm.surfaces) else T
            inst_tag = f"  inst={aelset.instance_name}" if aelset.instance_name else ""
            print(f"{I}{c}{GREEN('Elset')} {ename} ({len(aelset.elem_labels)}){inst_tag}"
                  + (f":  {_labels_summary(aelset.elem_labels, max_labels)}"
                     if show_labels else ""))

        for si, (sname, surf) in enumerate(asm.surfaces.items()):
            c = L if si == len(asm.surfaces)-1 else T
            print(f"{I}{c}{YELLOW('Surface')} {sname} [{surf.surface_type}]  "
                  f"{len(surf.entries)} entries")
    else:
        print(f"{T}{DIM('Assembly')}  (none)")

    # ---- Materials --------------------------------------------------- #
    print(f"{T}{BOLD(CYAN('Materials'))}  ({len(model.materials)})")
    mat_list = list(model.materials.items())
    for mi, (mname, mat) in enumerate(mat_list):
        is_last_mat = (mi == len(mat_list) - 1)
        m0 = L if is_last_mat else T
        m1 = S if is_last_mat else I

        props = []
        if mat.density_data:      props.append(f"Density({mat.density_data[0][0]:.3g})")
        if mat.elastic:
            if mat.elastic.data:
                E = mat.elastic.data[0][0] if mat.elastic.data[0] else "?"
                props.append(f"Elastic/{mat.elastic.elastic_type}(E={E:.3g})")
            else:
                props.append(f"Elastic/{mat.elastic.elastic_type}")
        if mat.plastic:           props.append(f"Plastic/{mat.plastic.hardening}({len(mat.plastic.data)} pts)")
        if mat.hyperelastic:      props.append(f"Hyperelastic/{mat.hyperelastic.model}")
        if mat.damage_initiation: props.append(f"DamageInitiation/{mat.damage_initiation.criterion}")
        if mat.damage_evolution:  props.append(f"DamageEvolution")
        if mat.creep:             props.append(f"Creep/{mat.creep.law}")
        if mat.conductivity_data: props.append("Conductivity")
        if mat.expansion_data:    props.append("Expansion")

        print(f"{I}{m0}{BOLD(mname)}")
        for pi2, prop in enumerate(props):
            c = L if pi2 == len(props)-1 else T
            print(f"{I}{m1}{c}{BLUE(prop)}")
        if not props:
            print(f"{I}{m1}{L}{DIM('(no properties)')}")

    # ---- Amplitudes -------------------------------------------------- #
    if model.amplitudes:
        print(f"{T}{BOLD(CYAN('Amplitudes'))}  ({len(model.amplitudes)})")
        amp_list = list(model.amplitudes.items())
        for ai, (aname, amp) in enumerate(amp_list):
            c = L if ai == len(amp_list)-1 else T
            t_range = ""
            if amp.times:
                t_range = f"  t=[{amp.times[0]:.3g} … {amp.times[-1]:.3g}]  ({len(amp.times)} pts)"
            print(f"{I}{c}{aname}{t_range}")

    # ---- Steps ------------------------------------------------------- #
    print(f"{L}{BOLD(CYAN('Steps'))}  ({len(model.steps)})")
    step_list = model.steps
    for si, step in enumerate(step_list):
        is_last_step = (si == len(step_list) - 1)
        s0 = L if is_last_step else T
        s1 = S if is_last_step else I

        nlgeom_tag = "  nlgeom=YES" if step.nlgeom else ""
        print(f"    {s0}{BOLD(step.name)}  [{step.step_type}]{nlgeom_tag}")

        items = []
        if step.boundary_conditions:
            items.append((f"Boundary Conditions ({len(step.boundary_conditions)})",
                          step.boundary_conditions))
        if step.cloads:
            items.append((f"Concentrated Loads ({len(step.cloads)})", step.cloads))
        if step.dloads:
            items.append((f"Distributed Loads ({len(step.dloads)})", step.dloads))

        for ki, (label, entries) in enumerate(items):
            c = L if ki == len(items)-1 else T
            c2 = S if ki == len(items)-1 else I
            print(f"    {s1}{c}{YELLOW(label)}")
            for ei2, entry in enumerate(entries[:max_labels]):
                ec = L if (ei2 == len(entries)-1 or ei2 == max_labels-1) else T
                if hasattr(entry, 'dof_start'):   # BC
                    print(f"    {s1}{c2}{ec}{entry.nset_name}  "
                          f"DOF {entry.dof_start}–{entry.dof_end} = {entry.value}"
                          + (f"  amp={entry.amplitude_name}" if entry.amplitude_name else ""))
                elif hasattr(entry, 'dof'):        # Cload
                    print(f"    {s1}{c2}{ec}{entry.nset_name}  "
                          f"DOF {entry.dof} = {entry.value}"
                          + (f"  amp={entry.amplitude_name}" if entry.amplitude_name else ""))
                elif hasattr(entry, 'load_type'):  # Dload
                    print(f"    {s1}{c2}{ec}{entry.elset_name}  "
                          f"{entry.load_type} = {entry.magnitude}"
                          + (f"  amp={entry.amplitude_name}" if entry.amplitude_name else ""))
            if len(entries) > max_labels:
                print(f"    {s1}{c2}{L}{DIM(f'... and {len(entries)-max_labels} more')}")

    # ---- Diagnostics summary ----------------------------------------- #
    errors   = [d for d in model.diagnostics if d.severity == "ERROR"]
    warnings = [d for d in model.diagnostics if d.severity == "WARNING"]
    if errors or warnings:
        print()
        if errors:
            print(RED(f"  {len(errors)} ERROR(S):"))
            for d in errors:
                print(f"    {d}")
        if warnings:
            print(YELLOW(f"  {len(warnings)} WARNING(S):"))
            for d in warnings[:5]:
                print(f"    {d}")
            if len(warnings) > 5:
                print(f"    {DIM(f'... and {len(warnings)-5} more')}")
    else:
        print(DIM("\n  No errors or warnings."))


def main():
    use_arg = False
    if use_arg:
        parser = argparse.ArgumentParser(description="Print Abaqus INP model tree")
        parser.add_argument("inp", help="Input .inp file")
        parser.add_argument("--no-labels", action="store_true",
                            help="Don't print individual label lists")
        parser.add_argument("--max-labels", type=int, default=8,
                            help="Max labels to show per set (default: 8)")
        args = parser.parse_args()

        print(f"Parsing {args.inp} ...\n")

        model = parse_inp(args.inp)
        print_tree(model,
                   show_labels=not args.no_labels,
                   max_labels=args.max_labels)
    else:
        inp = r"D:\WorkSpace\ThreeJS\PyModel2JsonDataFolder\model\inp\door.inp"
        model = parse_inp(inp)
        print_tree(model,
                   show_labels=True,
                   max_labels=8)


if __name__ == "__main__":
    main()
