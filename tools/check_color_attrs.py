"""
检查 L1 geometry H5 里的 color-code 属性是否正确写入。

用法:
    python tools/check_color_attrs.py <workspace>  [instance_name]

例:
    python tools/check_color_attrs.py model/door
    python tools/check_color_attrs.py model/door PART-1-1
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import h5py
import numpy as np


def check(workspace: str, instance: str = None):
    geom_dir = os.path.join(workspace, "l1", "geometry")
    sets_h5  = os.path.join(workspace, "l1", "sets", "sets.h5")

    if not os.path.isdir(geom_dir):
        print(f"ERROR: {geom_dir} not found")
        return

    files = [f for f in os.listdir(geom_dir) if f.endswith(".h5")]
    if not files:
        print("ERROR: no .h5 files in", geom_dir)
        return

    if instance:
        files = [f for f in files if f == instance + ".h5"]
        if not files:
            print(f"ERROR: {instance}.h5 not found in {geom_dir}")
            return

    for fname in sorted(files):
        path = os.path.join(geom_dir, fname)
        inst_name = fname[:-3]
        print(f"\n{'='*60}")
        print(f"Instance: {inst_name}  ({path})")
        print(f"{'='*60}")

        with h5py.File(path, "r") as f:
            # Nodes
            n_nodes = len(f["nodes/labels"]) if "nodes/labels" in f else 0
            print(f"  nodes: {n_nodes}")

            # Elements
            etypes = list(f.get("elements", {}).keys())
            print(f"  element types: {etypes}")

            for etype in etypes:
                grp = f[f"elements/{etype}"]
                datasets = list(grp.keys())
                n_elems  = len(grp["labels"]) if "labels" in grp else "?"

                has_mat = "material_name" in grp
                has_sec = "section_type"  in grp

                print(f"\n  [{etype}]  n_elems={n_elems}  datasets={datasets}")
                print(f"    material_name present: {has_mat}")
                print(f"    section_type  present: {has_sec}")

                if has_mat:
                    raw = grp["material_name"][:5]
                    vals = [v.tobytes().rstrip(b'\x00').decode('ascii', errors='replace') for v in raw]
                    # Count unique
                    all_vals = grp["material_name"][:]
                    unique = set(v.tobytes().rstrip(b'\x00').decode('ascii', errors='replace') for v in all_vals)
                    empty  = sum(1 for v in all_vals if v.tobytes().rstrip(b'\x00') == b'')
                    print(f"    material_name sample (first 5): {vals}")
                    print(f"    unique values: {sorted(unique)}")
                    print(f"    empty entries: {empty} / {len(all_vals)}")
                else:
                    print("    *** material_name MISSING — need to re-run inp_to_l1.py ***")

                if has_sec:
                    all_sec = grp["section_type"][:]
                    unique_sec = set(v.tobytes().rstrip(b'\x00').decode('ascii', errors='replace') for v in all_sec)
                    print(f"    section_type unique: {sorted(unique_sec)}")

    # Sets
    print(f"\n{'='*60}")
    print(f"Sets H5: {sets_h5}")
    if os.path.exists(sets_h5):
        with h5py.File(sets_h5, "r") as f:
            for inst in f.get("element_sets", {}):
                sets = list(f[f"element_sets/{inst}"].keys())
                print(f"  instance '{inst}': {len(sets)} elsets  sample={sets[:5]}")
    else:
        print("  sets.h5 NOT FOUND")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    ws   = sys.argv[1]
    inst = sys.argv[2] if len(sys.argv) > 2 else None
    check(ws, inst)
