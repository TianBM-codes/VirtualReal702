import argparse
import json
from pathlib import Path
from typing import Iterable, Optional, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import h5py


def _format_value(value) -> str:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return repr(value)
    if isinstance(value, np.ndarray):
        if value.dtype.kind == "S":
            return repr([item.decode("utf-8", errors="replace") for item in value.tolist()])
        return repr(value.tolist())
    return repr(value)


def _print_attrs(obj, indent: str) -> None:
    if not obj.attrs:
        return
    print(f"{indent}attrs:")
    for key in sorted(obj.attrs.keys()):
        raw_value = obj.attrs[key]
        shown = _format_value(raw_value)
        if key == "components" and isinstance(raw_value, (str, bytes)):
            try:
                decoded = raw_value.decode("utf-8") if isinstance(raw_value, bytes) else raw_value
                shown = repr(json.loads(decoded))
            except Exception:
                pass
        print(f"{indent}  - {key} = {shown}")


def _sample_dataset(ds, limit: int) -> str:
    if ds.size == 0:
        return "[]"
    flat = np.asarray(ds[...]).reshape(-1)
    if flat.dtype.kind == "S":
        values = [item.decode("utf-8", errors="replace") for item in flat[:limit].tolist()]
    else:
        values = flat[:limit].tolist()
    suffix = " ..." if flat.size > limit else ""
    return f"{values}{suffix}"


def _walk_group(group, indent: str, sample_limit: int) -> None:
    import h5py

    for name in sorted(group.keys()):
        obj = group[name]
        path = obj.name
        if isinstance(obj, h5py.Group):
            print(f"{indent}[G] {path}")
            _print_attrs(obj, indent + "  ")
            _walk_group(obj, indent + "  ", sample_limit)
        elif isinstance(obj, h5py.Dataset):
            print(
                f"{indent}[D] {path} "
                f"shape={obj.shape} dtype={obj.dtype}"
            )
            _print_attrs(obj, indent + "  ")
            print(f"{indent}  sample={_sample_dataset(obj, sample_limit)}")


def _find_candidate_h5_files(workspace: Path) -> Iterable[Path]:
    result_root = workspace / "l1" / "results"
    patterns = [
        "**/external__Sensitivity__SENSITIVITY_CLOUD.h5",
        "**/*SENSITIVITY_CLOUD*.h5",
        "**/*Sensitivity*.h5",
    ]
    seen = set()
    for pattern in patterns:
        for path in sorted(result_root.glob(pattern)):
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                yield resolved


def _resolve_h5_path(input_path: Optional[str], workspace: Optional[str]) -> Path:
    if input_path:
        path = Path(input_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"h5 file not found: {path}")
        return path

    if not workspace:
        raise ValueError("请传 --h5 或 --workspace")

    workspace_path = Path(workspace).expanduser().resolve()
    if not workspace_path.exists():
        raise FileNotFoundError(f"workspace not found: {workspace_path}")

    candidates = list(_find_candidate_h5_files(workspace_path))
    if not candidates:
        raise FileNotFoundError(
            "在 workspace 下没有找到灵敏度 h5，"
            "请手动传 --h5 <文件路径>"
        )

    print("找到以下候选文件：")
    for idx, candidate in enumerate(candidates, start=1):
        print(f"  {idx}. {candidate}")
    print(f"默认使用第 1 个：{candidates[0]}")
    return candidates[0]


def inspect_h5(h5_path: Path, sample_limit: int) -> None:
    import h5py

    print("=" * 80)
    print(f"H5 文件: {h5_path}")
    print("=" * 80)

    with h5py.File(h5_path, "r") as h5:
        print("[根节点]")
        _print_attrs(h5, "  ")
        _walk_group(h5, "  ", sample_limit)

        print("\n[摘要]")
        if "ELEMENT_NODAL" in h5:
            for instance_name in sorted(h5["ELEMENT_NODAL"].keys()):
                instance_group = h5["ELEMENT_NODAL"][instance_name]
                components = instance_group.attrs.get("components")
                print(f"  instance={instance_name}")
                if components is not None:
                    print(f"    components={_format_value(components)}")
                for etype in sorted(instance_group.keys()):
                    data_path = f"/ELEMENT_NODAL/{instance_name}/{etype}/data"
                    ds = h5[data_path]
                    print(
                        f"    etype={etype} "
                        f"frames={ds.shape[0] if len(ds.shape) > 0 else 0} "
                        f"elements={ds.shape[1] if len(ds.shape) > 1 else 0} "
                        f"local_nodes={ds.shape[2] if len(ds.shape) > 2 else 0} "
                        f"components={ds.shape[3] if len(ds.shape) > 3 else 0}"
                    )
        elif "NODAL" in h5:
            for instance_name in sorted(h5["NODAL"].keys()):
                ds = h5[f"/NODAL/{instance_name}/data"]
                components = h5[f"/NODAL/{instance_name}"].attrs.get("components")
                print(f"  instance={instance_name}")
                if components is not None:
                    print(f"    components={_format_value(components)}")
                print(
                    f"    frames={ds.shape[0] if len(ds.shape) > 0 else 0} "
                    f"nodes={ds.shape[1] if len(ds.shape) > 1 else 0} "
                    f"components={ds.shape[2] if len(ds.shape) > 2 else 0}"
                )
        else:
            print("  未发现 /ELEMENT_NODAL 或 /NODAL 主结果组。")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="打印灵敏度 H5 文件的结构和部分结果值"
    )
    parser.add_argument("--h5", help="直接指定 h5 文件路径")
    parser.add_argument(
        "--workspace",
        help="项目 workspace 路径，脚本会自动查找灵敏度 h5",
    )
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=8,
        help="每个 dataset 最多打印多少个样本值，默认 8",
    )
    args = parser.parse_args()

    h5_path = _resolve_h5_path(args.h5, args.workspace)
    inspect_h5(h5_path, max(1, int(args.sample_limit)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
