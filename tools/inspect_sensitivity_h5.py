import argparse
from pathlib import Path
from typing import Optional


def resolve_h5_path(h5_path: Optional[str], workspace: Optional[str]) -> Path:
    if h5_path:
        path = Path(h5_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"找不到 h5 文件: {path}")
        return path

    if not workspace:
        raise ValueError("请传 --h5 或 --workspace")

    result_root = Path(workspace).expanduser().resolve() / "l1" / "results"
    if not result_root.exists():
        raise FileNotFoundError(f"找不到结果目录: {result_root}")

    patterns = [
        "**/external__Sensitivity__SENSITIVITY_CLOUD.h5",
        "**/*SENSITIVITY_CLOUD*.h5",
        "**/*Sensitivity*.h5",
    ]
    for pattern in patterns:
        matches = sorted(result_root.glob(pattern))
        if matches:
            print(f"使用文件: {matches[0]}")
            return matches[0]

    raise FileNotFoundError("在 workspace 下没有找到灵敏度 h5，请手动传 --h5")


def resolve_geom_path(h5_file: Path, geom_path: Optional[str], workspace: Optional[str], instance: str) -> Path:
    if geom_path:
        path = Path(geom_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"找不到 geometry h5: {path}")
        return path

    if workspace:
        path = Path(workspace).expanduser().resolve() / "l1" / "geometry" / f"{instance}.h5"
        if path.is_file():
            return path

    l1_dir = h5_file.parent.parent.parent
    path = l1_dir / "geometry" / f"{instance}.h5"
    if path.is_file():
        return path

    raise FileNotFoundError(
        f"找不到 instance={instance} 对应的 geometry h5，请手动传 --geom"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="打印每个单元的灵敏度值")
    parser.add_argument("--h5", help="灵敏度 h5 文件路径")
    parser.add_argument("--workspace", help="workspace 路径，可自动查找灵敏度 h5")
    parser.add_argument("--geom", help="几何 geometry h5 文件路径，可选")
    parser.add_argument("--instance", help="只打印指定 instance")
    parser.add_argument("--etype", help="只打印指定单元类型，比如 C3D8")
    parser.add_argument("--frame", type=int, default=0, help="打印第几个 frame，默认 0")
    args = parser.parse_args()

    try:
        import h5py
        import numpy as np
    except ImportError as exc:
        raise SystemExit("请先安装依赖: pip install h5py numpy") from exc

    h5_path = resolve_h5_path(args.h5, args.workspace)

    with h5py.File(h5_path, "r") as h5:
        if "ELEMENT_NODAL" not in h5:
            raise SystemExit("这个 h5 里没有 /ELEMENT_NODAL，当前脚本只处理单元灵敏度结果")

        root = h5["ELEMENT_NODAL"]
        instance_names = [args.instance] if args.instance else sorted(root.keys())

        for instance_name in instance_names:
            if instance_name not in root:
                print(f"跳过 instance={instance_name}，因为 h5 中不存在")
                continue

            geom_file = resolve_geom_path(h5_path, args.geom, args.workspace, instance_name)
            print(f"\nInstance: {instance_name}")
            print(f"Geometry: {geom_file}")

            with h5py.File(geom_file, "r") as geom_h5:
                instance_group = root[instance_name]
                etype_names = [args.etype] if args.etype else sorted(instance_group.keys())

                for etype in etype_names:
                    if etype not in instance_group:
                        print(f"  跳过 etype={etype}，因为 h5 中不存在")
                        continue
                    if f"elements/{etype}/labels" not in geom_h5:
                        print(f"  跳过 etype={etype}，因为 geometry 中没有 labels")
                        continue

                    labels = geom_h5[f"elements/{etype}/labels"][:]
                    data = instance_group[etype]["data"]

                    if args.frame < 0 or args.frame >= data.shape[0]:
                        raise SystemExit(
                            f"frame={args.frame} 超出范围，当前 etype={etype} 共有 {data.shape[0]} 帧"
                        )

                    values = np.asarray(data[args.frame])
                    values = np.squeeze(values)

                    print(
                        f"  EType: {etype}, frame={args.frame}, "
                        f"elements={len(labels)}, shape={data.shape}"
                    )

                    if values.ndim == 1:
                        for elem_label, value in zip(labels.tolist(), values.tolist()):
                            print(f"    elem={elem_label}, sensitivity={value}")
                    else:
                        for elem_label, row in zip(labels.tolist(), values.tolist()):
                            print(f"    elem={elem_label}, sensitivity={row}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
