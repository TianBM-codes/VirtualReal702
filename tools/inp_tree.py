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


def _first_scalar_summary(name: str, rows, key: str = "") -> str:
    if not rows or not rows[0]:
        return name
    label = f"{key}=" if key else ""
    return f"{name}({label}{rows[0][0]:.3g})"


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

    def _print_bc_entry(prefix: str, entry) -> None:
        suffix = ""
        if entry.op and entry.op != "MOD":
            suffix += f"  op={entry.op}"
        if entry.amplitude_name:
            suffix += f"  amp={entry.amplitude_name}"
        print(f"{prefix}{entry.nset_name}  DOF {entry.dof_start}-{entry.dof_end} = {entry.value}{suffix}")

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
            c = L if (ni == len(asm.nsets) - 1 and not asm.elsets and not asm.surfaces) else T
            inst_tag = f"  inst={anset.instance_name}" if anset.instance_name else ""
            print(f"{I}{c}{GREEN('Nset')} {nname} ({len(anset.node_labels)}){inst_tag}"
                  + (f":  {_labels_summary(anset.node_labels, max_labels)}"
                     if show_labels else ""))

        for ei, (ename, aelset) in enumerate(asm.elsets.items()):
            c = L if (ei == len(asm.elsets) - 1 and not asm.surfaces) else T
            inst_tag = f"  inst={aelset.instance_name}" if aelset.instance_name else ""
            print(f"{I}{c}{GREEN('Elset')} {ename} ({len(aelset.elem_labels)}){inst_tag}"
                  + (f":  {_labels_summary(aelset.elem_labels, max_labels)}"
                     if show_labels else ""))

        for si, (sname, surf) in enumerate(asm.surfaces.items()):
            c = L if (si == len(asm.surfaces) - 1 and not asm.ties and not asm.couplings) else T
            print(f"{I}{c}{YELLOW('Surface')} {sname} [{surf.surface_type}]  "
                  f"{len(surf.entries)} entries")

        for ti, tie in enumerate(asm.ties):
            c = L if (ti == len(asm.ties) - 1 and not asm.couplings) else T
            adj = "adjust=YES" if tie.adjust else "adjust=NO"
            print(f"{I}{c}{YELLOW('Tie')} {tie.name}  [{tie.tie_type}]  {adj}")
            c2 = S if (ti == len(asm.ties) - 1 and not asm.couplings) else I
            print(f"{I}{c2}{L}{tie.master_surface} → {tie.slave_surface}")

        for ci, coup in enumerate(asm.couplings):
            c = L if ci == len(asm.couplings) - 1 else T
            c2 = S if ci == len(asm.couplings) - 1 else I
            ctype = coup.coupling_type if coup.coupling_type else "?"
            weights_note = DIM("  (weights not parsed)") if coup.coupling_type == "DISTRIBUTING" else ""
            print(f"{I}{c}{YELLOW('Coupling')} {coup.name}  [{ctype}]{weights_note}")
            dof_str = ""
            if coup.dof_ranges:
                dof_str = "  DOF: " + ",".join(f"({a},{b})" for a, b in coup.dof_ranges)
            print(f"{I}{c2}{L}ref={coup.ref_node}  surf={coup.surface}{dof_str}")
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
        if mat.conductivity_data: props.append(_first_scalar_summary("Conductivity", mat.conductivity_data, "k"))
        if mat.expansion_data:    props.append(_first_scalar_summary("Expansion", mat.expansion_data, "alpha"))

        print(f"{I}{m0}{BOLD(mname)}")
        for pi2, prop in enumerate(props):
            c = L if pi2 == len(props) - 1 else T
            print(f"{I}{m1}{c}{BLUE(prop)}")
        if not props:
            print(f"{I}{m1}{L}{DIM('(no properties)')}")

    # ---- Amplitudes -------------------------------------------------- #
    if model.amplitudes:
        print(f"{T}{BOLD(CYAN('Amplitudes'))}  ({len(model.amplitudes)})")
        amp_list = list(model.amplitudes.items())
        for ai, (aname, amp) in enumerate(amp_list):
            c = L if ai == len(amp_list) - 1 else T
            t_range = ""
            if amp.times:
                t_range = f"  t=[{amp.times[0]:.3g} … {amp.times[-1]:.3g}]  ({len(amp.times)} pts)"
            print(f"{I}{c}{aname}{t_range}")

    # ---- Time Points ------------------------------------------------- #
    if model.time_points:
        print(f"{T}{BOLD(CYAN('Time Points'))}  ({len(model.time_points)})")
        tp_list = list(model.time_points.items())
        for ti, (tname, tp) in enumerate(tp_list):
            c = L if ti == len(tp_list) - 1 else T
            t_range = ""
            if tp.times:
                t_range = f"  ({len(tp.times)} pts)  t=[{tp.times[0]:.3g} … {tp.times[-1]:.3g}]"
            print(f"{I}{c}{tname}{t_range}")

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
        if step.dsloads:
            items.append((f"Surface Loads ({len(step.dsloads)})", step.dsloads))

        for ki, (label, entries) in enumerate(items):
            c = L if ki == len(items) - 1 else T
            c2 = S if ki == len(items) - 1 else I
            print(f"    {s1}{c}{YELLOW(label)}")
            for ei2, entry in enumerate(entries[:max_labels]):
                ec = L if (ei2 == len(entries) - 1 or ei2 == max_labels - 1) else T
                if hasattr(entry, 'dof_start'):  # BC
                    print(f"    {s1}{c2}{ec}{entry.nset_name}  "
                          f"DOF {entry.dof_start}–{entry.dof_end} = {entry.value}"
                          + (f"  amp={entry.amplitude_name}" if entry.amplitude_name else ""))
                elif hasattr(entry, 'dof'):  # Cload
                    print(f"    {s1}{c2}{ec}{entry.nset_name}  "
                          f"DOF {entry.dof} = {entry.value}"
                          + (f"  amp={entry.amplitude_name}" if entry.amplitude_name else ""))
                elif hasattr(entry, 'surface_name'):  # Dsload
                    print(f"    {s1}{c2}{ec}{entry.surface_name}  "
                          f"{entry.load_type} = {entry.magnitude}"
                          + (f"  amp={entry.amplitude_name}" if entry.amplitude_name else ""))
                elif hasattr(entry, 'load_type'):  # Dload
                    print(f"    {s1}{c2}{ec}{entry.elset_name}  "
                          f"{entry.load_type} = {entry.magnitude}"
                          + (f"  amp={entry.amplitude_name}" if entry.amplitude_name else ""))
            if len(entries) > max_labels:
                print(f"    {s1}{c2}{L}{DIM(f'... and {len(entries) - max_labels} more')}")

    # ---- Diagnostics summary ----------------------------------------- #
    errors = [d for d in model.diagnostics if d.severity == "ERROR"]
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
                print(f"    {DIM(f'... and {len(warnings) - 5} more')}")
    else:
        print(DIM("\n  No errors or warnings."))


def main():
    arg_use = False
    if arg_use:
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
                   show_labels=True,
                   max_labels=8)
        print_tree(model,
                   show_labels=not args.no_labels,
                   max_labels=args.max_labels)
    else:
        # inp = r"D:\WorkSpace\FEM\Abaqus\2023\win_b64\SMA\samples\job_archive\samples\2d_cpe8p_gc_smallsliding.inp"
        # inp = r"D:\WorkSpace\FEM\Abaqus\2023\win_b64\SMA\samples\job_archive\samples\backhoe_deform_scoopdump_xpl.inp"
        # inp = r"D:\WorkSpace\WebThreeJS\PyModel2JsonDataFolder\model\inp\door.inp"
        # inp = r"D:\WorkSpace\FEM\Abaqus\2023\win_b64\SMA\samples\job_archive\samples\ReactorHead_reference.inp"
        inp = r"C:\Users\12594\xwechat_files\wxid_j1zkqj0iq2n521_b4a6\msg\file\2026-05\zt_gx1_3ce.inp"
        model = parse_inp(inp)
        print_tree(model,
                   show_labels=True,
                   max_labels=18)


if __name__ == "__main__":
    main()
