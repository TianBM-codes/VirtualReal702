import csv
import json
from pathlib import Path
from typing import Any, Sequence

import numpy as np


def _clone_jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _clone_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clone_jsonable(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    return value


def _save_json(path: Path, payload: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_clone_jsonable(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path.resolve())


def _save_vector_txt(path: Path, values: Sequence[float]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savetxt(str(path), np.asarray(values, dtype=np.float64).reshape(1, -1), fmt="%.12g")
    return str(path.resolve())


def _save_matrix_txt(path: Path, matrix: Sequence[Sequence[float]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savetxt(str(path), np.asarray(matrix, dtype=np.float64), fmt="%.12g")
    return str(path.resolve())


def _save_csv_rows(path: Path, fieldnames: Sequence[str], rows: Sequence[dict]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(fieldnames))
        writer.writeheader()
        for row in rows:
            row_dict = dict(row or {})
            writer.writerow({str(field): _clone_jsonable(row_dict.get(field)) for field in fieldnames})
    return str(path.resolve())


def _iteration_dir(root_dir: Path, iteration: int) -> Path:
    path = (root_dir / f"iteration_{int(iteration):03d}").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path
