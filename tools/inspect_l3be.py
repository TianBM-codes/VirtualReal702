import argparse
import json
from pathlib import Path
from typing import Optional

import numpy as np

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

try:
    from tools.odb_client import decode_l3be
except ImportError:  # pragma: no cover
    from odb_client import decode_l3be


def _load_bytes(path: Optional[str], url: Optional[str], timeout: float):
    if path:
        file_path = Path(path).expanduser().resolve()
        if not file_path.is_file():
            raise FileNotFoundError(f"文件不存在: {file_path}")
        return file_path.read_bytes(), {"source": str(file_path)}

    if not url:
        raise ValueError("请传 --file 或 --url")

    if requests is None:
        raise RuntimeError("缺少 requests，请先安装: pip install requests")

    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    meta = {
        "source": url,
        "status_code": resp.status_code,
        "content_type": resp.headers.get("content-type"),
    }
    for key, value in resp.headers.items():
        if key.lower().startswith("x-"):
            meta[key] = value
    return resp.content, meta


def _preview(arr: np.ndarray, limit: int):
    flat = np.asarray(arr).reshape(-1)
    data = flat[:limit].tolist()
    if flat.size > limit:
        data.append(f"... ({int(flat.size)} items total)")
    return data


def _summarize_numeric(name: str, arr: np.ndarray):
    if arr.size == 0 or not np.issubdtype(arr.dtype, np.number):
        return None

    flat = np.asarray(arr).reshape(-1)
    if np.issubdtype(arr.dtype, np.floating):
        finite_mask = np.isfinite(flat)
        finite = flat[finite_mask]
        return {
            "finite_count": int(finite.size),
            "nan_count": int(np.isnan(flat).sum()),
            "pos_inf_count": int(np.isposinf(flat).sum()),
            "neg_inf_count": int(np.isneginf(flat).sum()),
            "min": float(finite.min()) if finite.size else None,
            "max": float(finite.max()) if finite.size else None,
            "mean": float(finite.mean()) if finite.size else None,
        }

    return {
        "count": int(flat.size),
        "min": flat.min().item(),
        "max": flat.max().item(),
        "mean": float(flat.mean()),
    }


def main():
    parser = argparse.ArgumentParser(description="查看 L3BE 二进制响应内容")
    parser.add_argument("--file", help="本地二进制文件路径")
    parser.add_argument("--url", help="直接请求接口 URL")
    parser.add_argument("--timeout", type=float, default=30.0, help="请求超时秒数")
    parser.add_argument("--preview", type=int, default=12, help="每个 section 预览前几个值")
    args = parser.parse_args()

    raw, meta = _load_bytes(args.file, args.url, args.timeout)
    sections = decode_l3be(raw)

    print("META:")
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"payload_bytes: {len(raw)}")
    print("")

    print("SECTIONS:")
    for name, arr in sections.items():
        info = {
            "shape": list(arr.shape),
            "dtype": str(arr.dtype),
            "preview": _preview(arr, args.preview),
        }
        numeric = _summarize_numeric(name, arr)
        if numeric is not None:
            info["stats"] = numeric
        print(f"- {name}")
        print(json.dumps(info, ensure_ascii=False, indent=2))

    u = sections.get("u_per_vertex")
    legend = sections.get("legend_range")
    if u is not None:
        print("")
        print("FRAME-SCALARS CHECK:")
        if legend is not None and len(legend) >= 2:
            print(f"legend_range: [{float(legend[0])}, {float(legend[1])}]")
        finite = u[np.isfinite(u)] if np.issubdtype(u.dtype, np.floating) else u.reshape(-1)
        if finite.size:
            outside = int(((finite < 0.0) | (finite > 1.0)).sum())
            print(f"finite_values: {int(finite.size)}")
            print(f"outside_[0,1]: {outside}")
        else:
            print("finite_values: 0")


if __name__ == "__main__":
    main()
