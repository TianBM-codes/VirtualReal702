"""
INP → L1 HDF5 工具

将 INP 文件导出为与 abaqus_dump.py + l1_pack.py 相同的 L1 目录布局，
之后可直接运行 L2 ingest.py 处理。

用法:
    python tools/inp_to_l1.py model.inp /data/my_workspace

完成后：
    python src/l2/ingest.py --workspace /data/my_workspace
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.inp import parse_inp
from src.inp.exporter import export_l1


def main():
    p = argparse.ArgumentParser(description="INP file → L1 HDF5 workspace")
    p.add_argument("inp",       help="Input .inp file")
    p.add_argument("workspace", help="Output workspace directory (will be created)")
    args = p.parse_args()

    print(f"Parsing {args.inp} ...")
    model = parse_inp(args.inp)

    errors   = [d for d in model.diagnostics if d.severity == "ERROR"]
    warnings = [d for d in model.diagnostics if d.severity == "WARNING"]
    if warnings:
        print(f"  {len(warnings)} warning(s) during parse")
    if errors:
        print(f"  {len(errors)} error(s) during parse:")
        for d in errors:
            print(f"    [{d.code}] {d.message}")
        print("Aborting export due to errors.")
        sys.exit(1)

    if model.assembly is None:
        print("ERROR: model has no Assembly — cannot determine instances.")
        print("  (INP files without *Assembly are not yet supported for export)")
        sys.exit(1)

    n_inst = len(model.assembly.instances)
    n_nodes = sum(len(p.nodes) for p in model.parts.values())
    n_elems = sum(len(p.elements) for p in model.parts.values())
    print(f"  {len(model.parts)} part(s), {n_inst} instance(s), "
          f"{n_nodes} nodes, {n_elems} elements")

    print(f"Exporting to {args.workspace} ...")
    export_l1(model, args.workspace)

    print("Done.")
    print()
    print("Next step:")
    print(f"  python src/l2/ingest.py --workspace {args.workspace}")


if __name__ == "__main__":
    main()
