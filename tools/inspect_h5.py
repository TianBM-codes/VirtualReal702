import argparse
import json
from pathlib import Path

import h5py
import numpy as np


def _preview_array(dataset, limit: int):
    if limit <= 0:
        return None
    try:
        data = dataset[()]
    except Exception as exc:
        return f"<read failed: {exc}>"
    array = np.asarray(data)
    if array.ndim == 0:
        try:
            return array.item()
        except Exception:
            return str(array)
    flat = array.reshape(-1)
    preview = flat[:limit].tolist()
    if flat.size > limit:
        preview.append(f"... ({int(flat.size)} items total)")
    return preview


def _visit(name, obj, *, preview_limit: int):
    indent = "  " * name.count("/")
    label = "/" if not name else f"/{name}"
    if isinstance(obj, h5py.Group):
        print(f"{indent}[GROUP] {label}")
        return

    info = {
        "shape": list(obj.shape),
        "dtype": str(obj.dtype),
    }
    if obj.attrs:
        info["attrs"] = {str(key): obj.attrs[key].tolist() if hasattr(obj.attrs[key], "tolist") else obj.attrs[key] for key in obj.attrs.keys()}
    preview = _preview_array(obj, preview_limit)
    if preview is not None:
        info["preview"] = preview
    print(f"{indent}[DATASET] {label} {json.dumps(info, ensure_ascii=False)}")


def main():
    parser = argparse.ArgumentParser(description="查看 HDF5 文件结构和数据预览")
    parser.add_argument("path", help="H5 文件路径")
    parser.add_argument("--preview", type=int, default=8, help="每个 dataset 预览前几个值，默认 8")
    args = parser.parse_args()

    file_path = Path(args.path).expanduser().resolve()
    if not file_path.is_file():
        raise SystemExit(f"H5 文件不存在: {file_path}")

    print(f"H5: {file_path}")
    with h5py.File(file_path, "r") as h5:
        _visit("", h5, preview_limit=args.preview)
        h5.visititems(lambda name, obj: _visit(name, obj, preview_limit=args.preview))


if __name__ == "__main__":
    main()
