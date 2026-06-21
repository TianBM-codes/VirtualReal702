import csv
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import matplotlib
import numpy as np
from pyNastran.bdf.bdf import BDF

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from config import get_local_service_base_url
from db import ensure_tables_exist, get_connection
from src.inp import parse_inp
from src.inp.parameter_mapping import build_parameter_target_map
from src.l3.core.errors import NotFoundError, ValidationError

from . import inp_service as _inp
from .ccmetrics import build_ccdis as _build_ccdis
from .ccmetrics import build_ccmean as _build_ccmean
from .ccmetrics import build_cctot as _build_cctot
from .console_log_service import safe_write_console_event
from .l3_local_bridge_service import write_external_field_local
from .project_status_service import update_work_condition_project_status
from . import sensitivity_service as _sens
from . import solver_service as _solver

_PARAMETER_ASSIGNMENT_RE = re.compile(r"^\s*([^=\s,]+)\s*=\s*(.+?)\s*$")
_ITERATION_CLEANUP_SUFFIXES = (".com", ".prt", ".pmg", ".pes", ".par", ".msg", ".sta", ".dat")
_DEFAULT_SOL200_BAYESIAN_RESULT_GROUP = "bayesian_sol200"


def _format_scalar(value: float) -> str:
    return format(float(value), ".12g")


def _normalize_optional_path(path: Optional[str]) -> Optional[str]:
    return os.path.abspath(path) if path else None


def _clone_jsonable(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, dict):
        return {str(key): _clone_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clone_jsonable(item) for item in value]
    return value


def _write_bayesian_iteration_console_log(
    *,
    project_id: int,
    batch_no: int,
    iteration_result: dict,
    stopped_early: bool,
) -> None:
    metrics = dict(iteration_result.get("metrics") or {})
    exit_check = dict(iteration_result.get("exit_check") or {})
    lines = [
        f"批次号: {batch_no}",
        f"迭代步: {iteration_result.get('iteration')}",
        f"响应相对残差: {metrics.get('rel_res')}",
        f"最大响应偏差(%): {metrics.get('max_abs_response_diff')}",
    ]
    if exit_check:
        lines.append(f"满足提前终止: {'是' if stopped_early else '否'}")
    safe_write_console_event(project_id, "模型修正迭代完成", lines)


def read_row_from_m_n(file_path: str, m: int, n: int) -> np.ndarray:
    if int(m) < 1 or int(n) < 1:
        raise ValidationError("m and n must be >= 1", {"m": m, "n": n})

    path = _solver._abs_file(file_path, "file_path")
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        lines = handle.readlines()

    if int(m) > len(lines):
        raise ValidationError(
            "row index exceeds file length",
            {"file_path": str(path), "row": int(m), "line_count": len(lines)},
        )

    parts = lines[int(m) - 1].strip().split()
    if int(n) > len(parts):
        raise ValidationError(
            "column index exceeds row length",
            {"file_path": str(path), "row": int(m), "column": int(n), "column_count": len(parts)},
        )

    try:
        values = [float(token) for token in parts[int(n) - 1:]]
    except ValueError as exc:
        raise ValidationError(
            "failed to parse numeric values from the selected text row",
            {"file_path": str(path), "row": int(m), "column": int(n)},
        ) from exc
    return np.asarray(values, dtype=np.float64).reshape(-1, 1)


def _scalarize(value: Any, *, field: str, row_key: str) -> float:
    arr = np.asarray(value, dtype=np.float64)
    if arr.size != 1:
        raise ValidationError(
            "bayesian update requires scalar normalized sensitivities and scalar response values",
            {"field": field, "row_key": row_key, "value": _clone_jsonable(value)},
        )
    scalar = float(arr.reshape(-1)[0])
    if not np.isfinite(scalar):
        raise ValidationError(
            "bayesian update does not accept non-finite scalar values",
            {"field": field, "row_key": row_key, "value": _clone_jsonable(value)},
        )
    return scalar


def _response_row_key(
        *,
        instance: str,
        response_field: str,
        response_component: Optional[str],
        response_position: str,
        response_label: str,
) -> str:
    component = str(response_component or "")
    return f"{instance}|{response_field}|{component}|{response_position}|{response_label}"


def _scoped_target_node_labels_for_instance(
        targets: Sequence[object],
        *,
        instance_name: str,
) -> List[int]:
    labels: List[int] = []
    for item in targets or []:
        text = str(item)
        if "::" in text:
            scope_name, label_text = text.split("::", 1)
            if str(scope_name) != str(instance_name):
                continue
        else:
            label_text = text
        try:
            labels.append(int(label_text))
        except ValueError:
            continue
    return sorted(set(labels))


def _ordered_dsa_field_names(field_prefix: str, field_names: Sequence[str]) -> List[str]:
    def _sort_key(name: str) -> Tuple[int, int, str, str]:
        token = _sens._extract_dsa_field_token(field_prefix, name)
        match = _sens._DSA_PARAMETER_TOKEN_RE.fullmatch(token)
        if match:
            return (0, int(match.group(2)), token, name)
        return (1, 10 ** 9, token, name)

    return sorted({str(item) for item in field_names}, key=_sort_key)


def _vector_from_input(
        raw_value: Any,
        items: Sequence[dict],
        *,
        label: str,
        key_candidates: Sequence[str],
) -> List[float]:
    if raw_value is None:
        raise ValidationError(f"{label} is required", {label: raw_value})

    if isinstance(raw_value, (int, float, np.integer, np.floating)):
        return [float(raw_value)] * len(items)

    if isinstance(raw_value, np.ndarray):
        values = np.asarray(raw_value, dtype=np.float64).reshape(-1).tolist()
        if len(values) != len(items):
            raise ValidationError(
                f"{label} length does not match resolved item count",
                {"expected": len(items), "actual": len(values)},
            )
        return [float(v) for v in values]

    if isinstance(raw_value, (list, tuple)):
        values = [float(v) for v in raw_value]
        if len(values) != len(items):
            raise ValidationError(
                f"{label} length does not match resolved item count",
                {"expected": len(items), "actual": len(values)},
            )
        return values

    if isinstance(raw_value, dict):
        resolved = []
        missing = []
        for item in items:
            chosen = None
            for key_name in key_candidates:
                candidate = item.get(key_name)
                if candidate is None:
                    continue
                candidate_key = str(candidate)
                if candidate_key in raw_value:
                    chosen = raw_value[candidate_key]
                    break
            if chosen is None:
                missing.append({key: item.get(key) for key in key_candidates if item.get(key) is not None})
            else:
                resolved.append(float(chosen))
        if missing:
            raise ValidationError(
                f"{label} is missing values for some resolved items",
                {"missing": missing[:10], "available_keys": sorted(str(key) for key in raw_value.keys())[:20]},
            )
        return resolved

    raise ValidationError(
        f"unsupported {label} value type",
        {"label": label, "value_type": type(raw_value).__name__},
    )


def _default_scatter_vector(
        items: Sequence[dict],
        *,
        default_value: float,
        metadata_key: Optional[str] = None,
) -> List[float]:
    values: List[float] = []
    for item in items:
        value = item.get(metadata_key) if metadata_key else None
        if value is None:
            values.append(float(default_value))
            continue
        numeric = float(value)
        if numeric <= 0:
            raise ValidationError(
                "scatter must be > 0",
                {"metadata_key": metadata_key, "item": dict(item), "value": value},
            )
        values.append(numeric)
    return values


def _resolve_scatter_vector(
        raw_value: Any,
        items: Sequence[dict],
        *,
        label: str,
        key_candidates: Sequence[str],
        default_value: float,
        metadata_key: Optional[str] = None,
) -> np.ndarray:
    if raw_value is None:
        return np.asarray(
            _default_scatter_vector(items, default_value=default_value, metadata_key=metadata_key),
            dtype=np.float64,
        )
    return np.asarray(
        _vector_from_input(raw_value, items, label=label, key_candidates=key_candidates),
        dtype=np.float64,
    )


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
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[str(item) for item in fieldnames])
        writer.writeheader()
        for row in rows:
            writer.writerow({str(field): _clone_jsonable(dict(row).get(field)) for field in fieldnames})
    return str(path.resolve())


def _iteration_dir(root_dir: Path, iteration: int) -> Path:
    path = (root_dir / f"iteration_{int(iteration):03d}").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _save_iteration_artifacts(root_dir: Path, iteration_result: dict) -> dict:
    iteration_dir = _iteration_dir(root_dir, int(iteration_result["iteration"]))
    files = {
        "summary_json": _save_json(iteration_dir / "summary.json", iteration_result),
        "sensitivity_matrix_txt": _save_matrix_txt(iteration_dir / "sensitivity_matrix.txt", iteration_result["sensitivity_matrix"]),
        "response_values_txt": _save_vector_txt(iteration_dir / "response_values.txt", iteration_result["response_values"]),
        "target_responses_txt": _save_vector_txt(iteration_dir / "target_responses.txt", iteration_result["target_responses"]),
        "parameter_values_txt": _save_vector_txt(iteration_dir / "parameter_values.txt", iteration_result["parameter_values"]),
        "updated_parameter_values_txt": _save_vector_txt(
            iteration_dir / "updated_parameter_values.txt",
            iteration_result["bayesian"]["p_new"],
        ),
        # This file is for manual cross-checking against FEMTools: parameter -> target elements.
        "parameter_element_mapping_json": _save_json(
            iteration_dir / "parameter_element_mapping.json",
            iteration_result.get("parameter_element_mapping", []),
        ),
    }
    if iteration_result.get("response_values_before_update") is not None:
        files["response_values_before_update_txt"] = _save_vector_txt(
            iteration_dir / "response_values_before_update.txt",
            iteration_result["response_values_before_update"],
        )
    if iteration_result.get("updated_bdf"):
        files["updated_bdf"] = str(iteration_result["updated_bdf"])
    return {"iteration_dir": str(iteration_dir), "files": files}


def _normalize_batch_no(batch_no: Optional[int]) -> int:
    resolved = 1 if batch_no is None else int(batch_no)
    if resolved <= 0:
        raise ValidationError("batch_no must be > 0", {"batch_no": batch_no})
    return resolved


def _truncate_tracking_name(value: Optional[str], *, default: str, max_length: int = 100) -> str:
    text = str(value or default).strip()
    return (text or default)[: int(max_length)]


def _response_tracking_name(row_meta: dict, index: int) -> str:
    candidates = (
        row_meta.get("tracking_name"),
        row_meta.get("response_name"),
        row_meta.get("response_code"),
    )
    for candidate in candidates:
        text = str(candidate or "").strip()
        if text:
            return _truncate_tracking_name(text, default=f"response_{index}")
    return f"response_{int(index)}"


def _parameter_scope_value(column_meta: dict) -> str:
    mapping = dict(column_meta.get("element_mapping") or {})
    target_rows = list(mapping.get("target_rows") or [])
    if target_rows:
        first_row = dict(target_rows[0])
        for key in ("set_name", "part_name", "instance_name"):
            candidate = first_row.get(key)
            if candidate:
                return _truncate_tracking_name(str(candidate), default="default", max_length=32)
    for key in ("parameter_scope", "set_name", "field"):
        candidate = column_meta.get(key)
        if candidate:
            return _truncate_tracking_name(str(candidate), default="default", max_length=32)
    return "default"


def _response_difference_percent(calculated: float, target: float) -> float:
    cal_value = float(calculated)
    target_value = float(target)
    if np.isclose(target_value, 0.0):
        return 0.0
    return float((cal_value - target_value) / target_value * 100.0)


def _response_difference_percent_for_exit(calculated: float, target: float, *, eps: float = 1e-12) -> float:
    cal_value = float(calculated)
    target_value = float(target)
    if np.isclose(target_value, 0.0, atol=float(eps)):
        return 0.0 if np.isclose(cal_value, target_value, atol=float(eps)) else float("inf")
    return float(abs((cal_value - target_value) / target_value * 100.0))


def build_ccabs(
        r_target: Sequence[float],
        r_model: Sequence[float],
        r_scatter: Any,
        *,
        eps: float = 1e-12,
) -> float:
    r_target_arr = np.asarray(r_target, dtype=np.float64).reshape(-1)
    r_model_arr = np.asarray(r_model, dtype=np.float64).reshape(-1)
    if r_target_arr.shape != r_model_arr.shape:
        raise ValidationError(
            "r_target and r_model size mismatch",
            {"r_target_size": int(r_target_arr.size), "r_model_size": int(r_model_arr.size)},
        )

    r_scatter_arr = _expand_scatter_vector(r_scatter, int(r_target_arr.size), label="response scatter")
    rel_diff = (r_model_arr - r_target_arr) / np.maximum(np.abs(r_target_arr), float(eps))
    return float(np.sum(np.abs(rel_diff) / np.maximum(r_scatter_arr, float(eps))))


def _build_iteration_metrics(
        *,
        response_values: Sequence[float],
        target_values: Sequence[float],
        response_scatter: Any,
        parameter_step: Sequence[float],
        eps: float = 1e-12,
) -> dict:
    r_model = np.asarray(response_values, dtype=np.float64).reshape(-1)
    r_target = np.asarray(target_values, dtype=np.float64).reshape(-1)
    if r_model.shape != r_target.shape:
        raise ValidationError(
            "response_values and target_values size mismatch",
            {"response_size": int(r_model.size), "target_size": int(r_target.size)},
        )

    residual = r_target - r_model
    target_norm = float(np.linalg.norm(r_target))
    residual_norm = float(np.linalg.norm(residual))
    parameter_step_arr = np.asarray(parameter_step, dtype=np.float64).reshape(-1)
    diffs = [
        abs(_response_difference_percent(calculated, target))
        for calculated, target in zip(r_model.tolist(), r_target.tolist())
    ]
    ccabs = build_ccabs(r_target, r_model, response_scatter, eps=eps)
    ccmean = _build_ccmean(r_target, r_model, response_scatter, eps=eps)
    ccdisp = _build_ccdis(r_target, r_model, response_scatter, eps=eps)
    cctotal = _build_cctot(ccabs, ccdisp)

    return {
        "ra_norm": float(np.linalg.norm(r_model)),
        "re_norm": target_norm,
        "dr_norm": residual_norm,
        "ccabs": ccabs,
        "ccmean": ccmean,
        "ccdisp": ccdisp,
        "ccdis": ccdisp,
        "cctotal": cctotal,
        "cctot": cctotal,
        "ccdsf": 0.0,
        "rel_res": float(residual_norm / (target_norm + float(eps))),
        "dx_norm": float(np.linalg.norm(parameter_step_arr)),
        "max_abs_dparam": float(np.max(np.abs(parameter_step_arr))) if parameter_step_arr.size else 0.0,
        "mean_abs_response_diff": float(np.mean(diffs)) if diffs else 0.0,
        "max_abs_response_diff": float(max(diffs)) if diffs else 0.0,
    }


def _evaluate_exit_condition(
    response_values: Sequence[float],
    target_values: Sequence[float],
    *,
    exit_diff_percent: Optional[float],
) -> Optional[dict]:
    if exit_diff_percent is None:
        return None

    threshold = float(exit_diff_percent)
    diffs = [
        _response_difference_percent_for_exit(calculated, target)
        for calculated, target in zip(response_values, target_values)
    ]
    finite_diffs = [float(item) for item in diffs if np.isfinite(item)]
    max_abs_diff = float(max(diffs)) if diffs else 0.0
    mean_abs_diff = float(np.mean(finite_diffs)) if finite_diffs else (0.0 if diffs else 0.0)
    return {
        "enabled": True,
        "threshold": threshold,
        "response_diff_percents": [float(item) if np.isfinite(item) else "inf" for item in diffs],
        "max_abs_response_diff_percent": max_abs_diff if np.isfinite(max_abs_diff) else float("inf"),
        "mean_abs_response_diff_percent": mean_abs_diff,
        "converged": bool(diffs) and all(float(item) <= threshold for item in diffs),
    }


def _group_history_rows(rows: Sequence[dict], name_key: str, value_key: str) -> Dict[str, List[Tuple[int, float]]]:
    grouped: Dict[str, List[Tuple[int, float]]] = {}
    for row in rows:
        grouped.setdefault(str(row[name_key]), []).append((int(row["iteration"]), float(row[value_key])))
    for key, values in grouped.items():
        grouped[key] = sorted(values, key=lambda item: item[0])
    return grouped


def _plot_history_series(
        *,
        path: Path,
        title: str,
        xlabel: str,
        ylabel: str,
        series_map: Dict[str, List[Tuple[int, float]]],
) -> Optional[str]:
    if not series_map:
        return None

    fig, ax = plt.subplots(figsize=(10, 5.5))
    for name, points in series_map.items():
        xs = [item[0] for item in points]
        ys = [item[1] for item in points]
        ax.plot(xs, ys, marker="o", linewidth=1.8, label=name)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.grid(True, linestyle="--", linewidth=0.6, alpha=0.5)
    if len(series_map) <= 8:
        ax.legend(loc="best")
    else:
        ax.legend(loc="center left", bbox_to_anchor=(1.02, 0.5), fontsize=8)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(path), dpi=160, bbox_inches="tight")
    plt.close(fig)
    return str(path.resolve())


def _plot_optimization_overview(
        *,
        path: Path,
        project_id: int,
        batch_no: int,
        iteration_rows: Sequence[dict],
        parameter_rows: Sequence[dict],
        response_rows: Sequence[dict],
) -> Optional[str]:
    if not iteration_rows:
        return None

    fig, axes = plt.subplots(2, 2, figsize=(13, 9))
    ax_obj, ax_param, ax_resp, ax_diff = axes.reshape(-1)

    xs = [int(row["iteration"]) for row in iteration_rows]
    ax_obj.plot(xs, [float(row["mean_abs_response_diff"]) for row in iteration_rows], marker="o", linewidth=1.8)
    ax_obj.set_title("Mean Abs Response Difference (%)")
    ax_obj.set_xlabel("Iteration")
    ax_obj.set_ylabel("Mean Abs Diff (%)")
    ax_obj.grid(True, linestyle="--", linewidth=0.6, alpha=0.5)

    parameter_series = _group_history_rows(parameter_rows, "parameter_name", "value")
    for name, points in parameter_series.items():
        ax_param.plot([item[0] for item in points], [item[1] for item in points], marker="o", linewidth=1.6, label=name)
    ax_param.set_title("Parameter History")
    ax_param.set_xlabel("Iteration")
    ax_param.set_ylabel("Parameter Value")
    ax_param.grid(True, linestyle="--", linewidth=0.6, alpha=0.5)
    if parameter_series:
        ax_param.legend(loc="best", fontsize=8)

    response_series = _group_history_rows(response_rows, "response_name", "calculated_value")
    for name, points in response_series.items():
        ax_resp.plot([item[0] for item in points], [item[1] for item in points], marker="o", linewidth=1.6, label=f"{name} calc")
    target_series = _group_history_rows(response_rows, "response_name", "target_value")
    for name, points in target_series.items():
        ax_resp.plot([item[0] for item in points], [item[1] for item in points], linestyle="--", linewidth=1.2, label=f"{name} target")
    ax_resp.set_title("Response History")
    ax_resp.set_xlabel("Iteration")
    ax_resp.set_ylabel("Response Value")
    ax_resp.grid(True, linestyle="--", linewidth=0.6, alpha=0.5)
    if response_series or target_series:
        ax_resp.legend(loc="best", fontsize=8)

    diff_series = _group_history_rows(response_rows, "response_name", "response_diff_percent")
    for name, points in diff_series.items():
        ax_diff.plot([item[0] for item in points], [item[1] for item in points], marker="o", linewidth=1.6, label=name)
    ax_diff.axhline(0.0, color="black", linewidth=1.0, alpha=0.6)
    ax_diff.set_title("Response Difference (%)")
    ax_diff.set_xlabel("Iteration")
    ax_diff.set_ylabel("Diff (%)")
    ax_diff.grid(True, linestyle="--", linewidth=0.6, alpha=0.5)
    if diff_series:
        ax_diff.legend(loc="best", fontsize=8)

    fig.suptitle(f"Bayesian Optimization History | project={int(project_id)} batch={int(batch_no)}", fontsize=14)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(path), dpi=160, bbox_inches="tight")
    plt.close(fig)
    return str(path.resolve())


def _history_html_content(
        *,
        project_id: int,
        batch_no: int,
        iterations: int,
        iteration_rows: Sequence[dict],
        files: dict,
) -> str:
    table_rows = "\n".join(
        (
            "<tr>"
            f"<td>{int(row['iteration'])}</td>"
            f"<td>{float(row['mean_abs_response_diff']):.6g}</td>"
            f"<td>{float(row['max_abs_response_diff']):.6g}</td>"
            f"<td>{float(row['parameter_step_norm']):.6g}</td>"
            f"<td>{float(row.get('ccabs', 0.0)):.6g}</td>"
            f"<td>{float(row.get('ccmean', 0.0)):.6g}</td>"
            f"<td>{float(row.get('ccdisp', 0.0)):.6g}</td>"
            f"<td>{float(row.get('cctotal', 0.0)):.6g}</td>"
            f"<td>{float(row.get('ccdsf', 0.0)):.6g}</td>"
            f"<td>{float(row.get('rel_res', 0.0)):.6g}</td>"
            "</tr>"
        )
        for row in iteration_rows
    )
    file_items = "\n".join(
        f'<li><a href="{Path(path).name}">{label}</a></li>'
        for label, path in files.items()
        if path and label != "overview_html"
    )
    image_blocks = "\n".join(
        (
            f'<section class="card"><h2>{title}</h2><img src="{Path(path).name}" alt="{title}"></section>'
        )
        for title, path in (
            ("Optimization Overview", files.get("overview_png")),
            ("Parameter History", files.get("parameter_history_png")),
            ("Response History", files.get("response_history_png")),
            ("Response Difference", files.get("response_diff_history_png")),
        )
        if path
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Bayesian Optimization History</title>
  <style>
    :root {{
      --bg: #f4f0e8;
      --panel: #fffdfa;
      --ink: #1f2a30;
      --muted: #68737a;
      --line: #d8cfc2;
      --accent: #1b6f6a;
    }}
    body {{
      margin: 0;
      font-family: "Segoe UI", "PingFang SC", sans-serif;
      background: linear-gradient(180deg, #f8f3ea 0%, var(--bg) 100%);
      color: var(--ink);
    }}
    main {{
      max-width: 1200px;
      margin: 0 auto;
      padding: 32px 20px 48px;
    }}
    .hero {{
      display: grid;
      gap: 8px;
      margin-bottom: 24px;
    }}
    .meta {{
      color: var(--muted);
      font-size: 14px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 18px;
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 18px;
      box-shadow: 0 12px 30px rgba(26, 39, 44, 0.06);
    }}
    h1, h2 {{
      margin: 0 0 12px;
    }}
    img {{
      width: 100%;
      border-radius: 12px;
      border: 1px solid var(--line);
      background: white;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }}
    th, td {{
      padding: 8px 10px;
      border-bottom: 1px solid var(--line);
      text-align: left;
    }}
    ul {{
      margin: 0;
      padding-left: 18px;
    }}
    a {{
      color: var(--accent);
      text-decoration: none;
    }}
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <h1>Bayesian Optimization History</h1>
      <div class="meta">project_id={int(project_id)} | batch_no={int(batch_no)} | iterations={int(iterations)}</div>
    </section>
    <section class="grid">
      <section class="card">
        <h2>Iteration Summary</h2>
        <table>
          <thead>
            <tr>
              <th>Iteration</th>
              <th>Mean Abs Diff (%)</th>
              <th>Max Abs Diff (%)</th>
              <th>Parameter Step Norm</th>
              <th>CCABS</th>
              <th>CCMEAN</th>
              <th>CCDISP</th>
              <th>CCTOTAL</th>
              <th>CCDSF</th>
              <th>Relative Residual</th>
            </tr>
          </thead>
          <tbody>
            {table_rows}
          </tbody>
        </table>
      </section>
      <section class="card">
        <h2>Exported Files</h2>
        <ul>
          {file_items}
        </ul>
      </section>
      {image_blocks}
    </section>
  </main>
</body>
</html>
"""


def _save_bayesian_history_artifacts(
        *,
        root_dir: Path,
        project_id: int,
        batch_no: int,
        iteration_results: Sequence[dict],
) -> dict:
    rows = list(iteration_results or [])
    if not rows:
        return {}

    history_dir = (root_dir / "history").resolve()
    history_dir.mkdir(parents=True, exist_ok=True)

    first_iteration = dict(rows[0])
    parameter_columns = list(first_iteration.get("parameter_columns") or [])
    response_rows_meta = list(first_iteration.get("response_rows") or [])

    parameter_history_rows: List[dict] = []
    for index, column in enumerate(parameter_columns):
        parameter_name = str(column.get("parameter_name") or f"parameter_{index + 1}")
        parameter_history_rows.append(
            {
                "iteration": 0,
                "parameter_name": parameter_name,
                "value": float(first_iteration["parameter_values"][index]),
            }
        )
    for iteration_result in rows:
        updated_values = list(iteration_result.get("bayesian", {}).get("p_new") or [])
        for index, column in enumerate(iteration_result.get("parameter_columns") or []):
            parameter_history_rows.append(
                {
                    "iteration": int(iteration_result["iteration"]),
                    "parameter_name": str(column.get("parameter_name") or f"parameter_{index + 1}"),
                    "value": float(updated_values[index]),
                }
            )

    response_history_rows: List[dict] = []
    iteration_summary_rows: List[dict] = []
    for iteration_result in rows:
        iteration_no = int(iteration_result["iteration"])
        metrics = dict(iteration_result.get("metrics") or _build_iteration_metrics(
            response_values=iteration_result.get("response_values") or [],
            target_values=iteration_result.get("target_responses") or [],
            response_scatter=iteration_result.get("response_scatter") or 0.01,
            parameter_step=iteration_result.get("bayesian", {}).get("dp") or [],
        ))
        diffs: List[float] = []
        for index, row_meta in enumerate(iteration_result.get("response_rows") or response_rows_meta):
            response_name = _response_tracking_name(dict(row_meta), index + 1)
            calculated = float(iteration_result["response_values"][index])
            target = float(iteration_result["target_responses"][index])
            diff = _response_difference_percent(calculated, target)
            diffs.append(abs(diff))
            response_history_rows.append(
                {
                    "iteration": iteration_no,
                    "response_name": response_name,
                    "calculated_value": calculated,
                    "target_value": target,
                    "response_diff_percent": diff,
                }
            )

        parameter_step = np.asarray(iteration_result.get("bayesian", {}).get("dp") or [], dtype=np.float64).reshape(-1)
        iteration_summary_rows.append(
            {
                "iteration": iteration_no,
                "mean_abs_response_diff": float(metrics["mean_abs_response_diff"]),
                "max_abs_response_diff": float(metrics["max_abs_response_diff"]),
                "parameter_step_norm": float(np.linalg.norm(parameter_step)) if parameter_step.size else 0.0,
                "ccabs": float(metrics["ccabs"]),
                "ccmean": float(metrics.get("ccmean", 0.0)),
                "ccdisp": float(metrics.get("ccdisp", metrics.get("ccdis", 0.0))),
                "cctotal": float(metrics.get("cctotal", metrics.get("cctot", 0.0))),
                "ccdsf": float(metrics.get("ccdsf", 0.0)),
                "rel_res": float(metrics["rel_res"]),
                "ra_norm": float(metrics["ra_norm"]),
                "re_norm": float(metrics["re_norm"]),
                "dr_norm": float(metrics["dr_norm"]),
                "dx_norm": float(metrics["dx_norm"]),
                "max_abs_dparam": float(metrics["max_abs_dparam"]),
            }
        )

    summary_payload = {
        "project_id": int(project_id),
        "batch_no": int(batch_no),
        "iterations": len(rows),
        "parameter_names": sorted({str(row["parameter_name"]) for row in parameter_history_rows}),
        "response_names": sorted({str(row["response_name"]) for row in response_history_rows}),
        "iteration_summary": iteration_summary_rows,
    }

    files = {
        "history_summary_json": _save_json(history_dir / "history_summary.json", summary_payload),
        "parameter_history_csv": _save_csv_rows(
            history_dir / "parameter_history.csv",
            ("iteration", "parameter_name", "value"),
            parameter_history_rows,
        ),
        "response_history_csv": _save_csv_rows(
            history_dir / "response_history.csv",
            ("iteration", "response_name", "calculated_value", "target_value", "response_diff_percent"),
            response_history_rows,
        ),
        "iteration_summary_csv": _save_csv_rows(
            history_dir / "iteration_summary.csv",
            (
                "iteration",
                "mean_abs_response_diff",
                "max_abs_response_diff",
                "parameter_step_norm",
                "ccabs",
                "ccmean",
                "ccdisp",
                "cctotal",
                "ccdsf",
                "rel_res",
                "ra_norm",
                "re_norm",
                "dr_norm",
                "dx_norm",
                "max_abs_dparam",
            ),
            iteration_summary_rows,
        ),
    }
    files["parameter_history_png"] = _plot_history_series(
        path=history_dir / "parameter_history.png",
        title="Parameter History",
        xlabel="Iteration",
        ylabel="Parameter Value",
        series_map=_group_history_rows(parameter_history_rows, "parameter_name", "value"),
    )
    files["response_history_png"] = _plot_history_series(
        path=history_dir / "response_history.png",
        title="Calculated Response History",
        xlabel="Iteration",
        ylabel="Response Value",
        series_map=_group_history_rows(response_history_rows, "response_name", "calculated_value"),
    )
    files["response_diff_history_png"] = _plot_history_series(
        path=history_dir / "response_diff_history.png",
        title="Response Difference (%)",
        xlabel="Iteration",
        ylabel="Diff (%)",
        series_map=_group_history_rows(response_history_rows, "response_name", "response_diff_percent"),
    )
    files["overview_png"] = _plot_optimization_overview(
        path=history_dir / "optimization_overview.png",
        project_id=project_id,
        batch_no=batch_no,
        iteration_rows=iteration_summary_rows,
        parameter_rows=parameter_history_rows,
        response_rows=response_history_rows,
    )
    overview_html = history_dir / "overview.html"
    overview_html.write_text(
        _history_html_content(
            project_id=project_id,
            batch_no=batch_no,
            iterations=len(rows),
            iteration_rows=iteration_summary_rows,
            files=files,
        ),
        encoding="utf-8",
    )
    files["overview_html"] = str(overview_html.resolve())
    return {
        "history_dir": str(history_dir),
        "files": files,
        "iteration_summary": iteration_summary_rows,
    }


def _persist_bayesian_tracking_results(*, project_id: int, batch_no: int, iteration_results: Sequence[dict]) -> None:
    resolved_batch_no = _normalize_batch_no(batch_no)
    rows = list(iteration_results or [])
    if not rows:
        return

    ensure_tables_exist()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        for table_name in (
                "t_mt_py_fem_tracking_iteration",
                "t_mt_py_fem_bayesian_iteration_metric",
                "t_mt_py_fem_relevance_tracking",
                "t_mt_py_fem_response_difference",
                "t_mt_py_fem_parameter_variation",
                "t_mt_py_fem_tracking_value",
        ):
            if table_name == "t_mt_py_fem_bayesian_iteration_metric":
                cursor.execute(
                    f"DELETE FROM {table_name} WHERE project_id = %s AND batch_no = %s",
                    (int(project_id), resolved_batch_no),
                )
            elif table_name == "t_mt_py_fem_relevance_tracking":
                cursor.execute(f"DELETE FROM {table_name} WHERE pid = %s", (int(project_id),))
            else:
                cursor.execute(f"DELETE FROM {table_name} WHERE pid = %s AND batch_no = %s", (int(project_id), resolved_batch_no))

        cursor.execute(
            """
            INSERT INTO t_mt_py_fem_tracking_iteration (pid, batch_no, iterations)
            VALUES (%s, %s, %s)
            """,
            (int(project_id), resolved_batch_no, len(rows)),
        )

        first_iteration = dict(rows[0])
        final_iteration = dict(rows[-1])

        initial_parameter_values = {
            _truncate_tracking_name(
                str(column.get("parameter_name") or ""),
                default=f"parameter_{index + 1}",
            ): float(first_iteration["parameter_values"][index])
            for index, column in enumerate(first_iteration.get("parameter_columns") or [])
        }

        for iteration_result in rows:
            iteration_no = int(iteration_result["iteration"])
            response_values = list(iteration_result.get("response_values") or [])
            target_values = list(iteration_result.get("target_responses") or [])
            response_rows = list(iteration_result.get("response_rows") or [])
            metrics = dict(iteration_result.get("metrics") or _build_iteration_metrics(
                response_values=response_values,
                target_values=target_values,
                response_scatter=iteration_result.get("response_scatter") or 0.01,
                parameter_step=iteration_result.get("bayesian", {}).get("dp") or [],
            ))
            cursor.execute(
                """
                INSERT INTO t_mt_py_fem_bayesian_iteration_metric
                (project_id, batch_no, iteration, ccabs, rel_res, ra_norm, re_norm, dr_norm,
                 dx_norm, max_abs_dparam, mean_abs_response_diff, max_abs_response_diff)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    int(project_id),
                    resolved_batch_no,
                    iteration_no,
                    float(metrics["ccabs"]),
                    float(metrics["rel_res"]),
                    float(metrics["ra_norm"]),
                    float(metrics["re_norm"]),
                    float(metrics["dr_norm"]),
                    float(metrics["dx_norm"]),
                    float(metrics["max_abs_dparam"]),
                    float(metrics["mean_abs_response_diff"]),
                    float(metrics["max_abs_response_diff"]),
                ),
            )
            for relevance_type, relevance_value in (
                ("CCABS", float(metrics["ccabs"])),
                ("CCMEAN", float(metrics.get("ccmean", 0.0))),
                ("CCDISP", float(metrics.get("ccdisp", metrics.get("ccdis", 0.0)))),
                ("CCTOTAL", float(metrics.get("cctotal", metrics.get("cctot", 0.0)))),
                ("CCDSF", float(metrics.get("ccdsf", 0.0))),
            ):
                cursor.execute(
                    """
                    INSERT INTO t_mt_py_fem_relevance_tracking (pid, iteration, type, value)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (
                        int(project_id),
                        iteration_no,
                        relevance_type,
                        relevance_value,
                    ),
                )
            for row_index, row_meta in enumerate(response_rows):
                response_name = _response_tracking_name(dict(row_meta), row_index + 1)
                calculated = float(response_values[row_index])
                target = float(target_values[row_index])
                cursor.execute(
                    """
                    INSERT INTO t_mt_py_fem_response_difference
                    (pid, batch_no, response_name, iteration, cal_result_value, test_result_value, response_diff)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        int(project_id),
                        resolved_batch_no,
                        response_name,
                        iteration_no,
                        calculated,
                        target,
                        _response_difference_percent(calculated, target),
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO t_mt_py_fem_tracking_value
                    (pid, batch_no, tracking_type, tracking_name, iteration, track_value)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        int(project_id),
                        resolved_batch_no,
                        "Response",
                        response_name,
                        iteration_no,
                        calculated,
                    ),
                )

            updated_parameters = list(iteration_result.get("bayesian", {}).get("p_new") or [])
            parameter_columns = list(iteration_result.get("parameter_columns") or [])
            for column_index, column_meta in enumerate(parameter_columns):
                parameter_name = _truncate_tracking_name(
                    str(column_meta.get("parameter_name") or ""),
                    default=f"parameter_{column_index + 1}",
                )
                parameter_value = float(updated_parameters[column_index])
                cursor.execute(
                    """
                    INSERT INTO t_mt_py_fem_tracking_value
                    (pid, batch_no, tracking_type, tracking_name, iteration, track_value)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        int(project_id),
                        resolved_batch_no,
                        "Parameter",
                        parameter_name,
                        iteration_no,
                        parameter_value,
                    ),
                )

        final_parameter_values = list(final_iteration.get("bayesian", {}).get("p_new") or [])
        final_parameter_columns = list(final_iteration.get("parameter_columns") or [])
        for column_index, column_meta in enumerate(final_parameter_columns):
            parameter_name = _truncate_tracking_name(
                str(column_meta.get("parameter_name") or ""),
                default=f"parameter_{column_index + 1}",
            )
            ori_value = float(initial_parameter_values.get(parameter_name, column_meta.get("parameter_value") or 0.0))
            result_value = float(final_parameter_values[column_index])
            cursor.execute(
                """
                INSERT INTO t_mt_py_fem_parameter_variation
                (pid, batch_no, parameter_name, parameter_hierarchy, parameter_type, parameter_scope,
                 ori_value, result_value, parameter_variation)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    int(project_id),
                    resolved_batch_no,
                    parameter_name,
                    "default",
                    "default",
                    _parameter_scope_value(dict(column_meta)),
                    ori_value,
                    result_value,
                    result_value - ori_value,
                ),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _clear_bayesian_run_outputs(*, project_id: int, batch_no: int) -> dict:
    resolved_batch_no = _normalize_batch_no(batch_no)
    ensure_tables_exist()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        deleted = {}
        sql_list = [
            ("t_mt_py_fem_tracking_iteration", "DELETE FROM t_mt_py_fem_tracking_iteration WHERE pid = %s AND batch_no = %s", (int(project_id), resolved_batch_no)),
            ("t_mt_py_fem_bayesian_iteration_metric", "DELETE FROM t_mt_py_fem_bayesian_iteration_metric WHERE project_id = %s AND batch_no = %s", (int(project_id), resolved_batch_no)),
            ("t_mt_py_fem_relevance_tracking", "DELETE FROM t_mt_py_fem_relevance_tracking WHERE pid = %s", (int(project_id),)),
            ("t_mt_py_fem_response_difference", "DELETE FROM t_mt_py_fem_response_difference WHERE pid = %s AND batch_no = %s", (int(project_id), resolved_batch_no)),
            ("t_mt_py_fem_parameter_variation", "DELETE FROM t_mt_py_fem_parameter_variation WHERE pid = %s AND batch_no = %s", (int(project_id), resolved_batch_no)),
            ("t_mt_py_fem_tracking_value", "DELETE FROM t_mt_py_fem_tracking_value WHERE pid = %s AND batch_no = %s", (int(project_id), resolved_batch_no)),
            ("t_mt_py_fem_model_update_static_result", "DELETE FROM t_mt_py_fem_model_update_static_result WHERE pid = %s AND batch_no = %s", (int(project_id), resolved_batch_no)),
            ("t_mt_py_fem_model_update_modal_result", "DELETE FROM t_mt_py_fem_model_update_modal_result WHERE pid = %s AND batch_no = %s", (int(project_id), resolved_batch_no)),
        ]
        for table_name, sql, params in sql_list:
            cursor.execute(sql, params)
            deleted[table_name] = int(cursor.rowcount or 0)
        conn.commit()
        return {
            "project_id": int(project_id),
            "batch_no": resolved_batch_no,
            "deleted": deleted,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _group_scoped_targets(targets: Sequence[object]) -> Dict[str, List[int]]:
    grouped: Dict[str, List[int]] = {}
    for item in targets:
        text = str(item)
        if "::" in text:
            scope_name, label_text = text.split("::", 1)
        else:
            scope_name, label_text = "", text
        try:
            label = int(label_text)
        except ValueError:
            continue
        grouped.setdefault(scope_name, []).append(label)

    for scope_name, labels in grouped.items():
        grouped[scope_name] = sorted(set(int(label) for label in labels))
    return grouped


def _parameter_element_mapping_entry(
        *,
        model,
        source_field_name: str,
        parameter_token: str,
        mapped_parameter_name: str,
        parameter_value: float,
        target_rows: Sequence[dict],
        mapping_mode: str,
        scatter: float,
) -> dict:
    # Persist the resolved parameter-to-element mapping used by this iteration so
    # a bad update (for example negative thickness) can be traced back to its target set.
    result = {
        "field": str(source_field_name),
        "parameter_token": str(parameter_token),
        "parameter_name": str(mapped_parameter_name),
        "parameter_value": float(parameter_value),
        "mapping_mode": str(mapping_mode),
        "scatter": float(scatter),
        "target_rows": [_clone_jsonable(dict(row)) for row in target_rows],
    }
    try:
        target_kind, scoped_targets = _sens._parameter_target_labels(model, dict(target_rows[0])) if len(target_rows) == 1 else (None, [])
        if len(target_rows) > 1:
            all_targets: List[object] = []
            resolved_kind = None
            for row in target_rows:
                row_kind, row_targets = _sens._parameter_target_labels(model, dict(row))
                if resolved_kind is None:
                    resolved_kind = row_kind
                elif resolved_kind != row_kind:
                    resolved_kind = "mixed"
                all_targets.extend(list(row_targets))
            target_kind = resolved_kind
            scoped_targets = all_targets
        result.update(
            {
                "target_kind": target_kind,
                "target_count": len(scoped_targets),
                "scoped_targets": [str(item) for item in scoped_targets],
                "targets_by_scope": _group_scoped_targets(scoped_targets),
            }
        )
    except Exception as exc:
        result.update(
            {
                "target_kind": None,
                "target_count": 0,
                "scoped_targets": [],
                "targets_by_scope": {},
                "mapping_error": str(exc),
            }
        )
    return result


def _resolve_cloud_scalar_value(
        *,
        mode: str,
        updated_value: float,
        baseline_value: float,
) -> float:
    if mode == "updated_value":
        return float(updated_value)
    if mode == "delta_value":
        return float(updated_value) - float(baseline_value)
    if mode == "relative_change":
        baseline = float(baseline_value)
        if np.isclose(baseline, 0.0):
            if np.isclose(float(updated_value), baseline):
                return 0.0
            raise ValidationError(
                "relative_change cloud mode requires a non-zero baseline parameter value",
                {"baseline_value": baseline_value, "updated_value": updated_value},
            )
        return (float(updated_value) - baseline) / baseline
    raise ValidationError(
        "unsupported cloud_value_mode",
        {"cloud_value_mode": mode, "allowed": ["updated_value", "delta_value", "relative_change"]},
    )


def _resolve_cloud_scalar_percent_value(
        *,
        updated_value: float,
        baseline_value: float,
) -> float:
    baseline = float(baseline_value)
    if np.isclose(baseline, 0.0):
        if np.isclose(float(updated_value), baseline):
            return 0.0
        raise ValidationError(
            "relative delta percent cloud mode requires a non-zero baseline parameter value",
            {"baseline_value": baseline_value, "updated_value": updated_value},
        )
    return ((float(updated_value) - baseline) / baseline) * 100.0


def _extract_modal_response_values_from_op2(
        *,
        op2_path: str,
        response_rows: Sequence[dict],
        source_label: str = "modal",
) -> np.ndarray:
    from services.model_update.importers.op2_service import _extract_mode_frequency, _read_op2

    resolved_op2 = _solver._abs_file(op2_path, "op2_path")
    op2 = _read_op2(str(resolved_op2))
    eigenvectors = getattr(op2, "eigenvectors", {}) or {}
    if not eigenvectors:
        raise ValidationError(
            f"modal eigenvectors were not found in the {source_label} OP2 result",
            {"op2_path": str(resolved_op2), "source_label": source_label},
        )
    first_subcase_id = sorted(int(key) for key in eigenvectors.keys())[0]
    eigen_data = eigenvectors[first_subcase_id]
    modes_array = np.asarray(getattr(eigen_data, "modes", []), dtype=np.int64)
    if modes_array.size == 0:
        raise ValidationError(
            f"modal mode numbers were not found in the {source_label} OP2 result",
            {"op2_path": str(resolved_op2), "subcase_id": first_subcase_id, "source_label": source_label},
        )
    response_values: List[float] = []
    missing_modes: List[int] = []
    for row in response_rows or []:
        mode_number = row.get("mode_number")
        if mode_number is None:
            raise ValidationError(
                "mode_number is required for modal Bayesian responses",
                {"response_row": dict(row or {}), "source_label": source_label},
            )
        found = np.where(modes_array == int(mode_number))[0]
        if len(found) == 0:
            missing_modes.append(int(mode_number))
            continue
        mode_index = int(found[0])
        frequency, _eigenvalue, warnings = _extract_mode_frequency(eigen_data, mode_index)
        if warnings:
            raise ValidationError(
                f"failed to resolve modal frequency from the {source_label} OP2 result",
                {
                    "op2_path": str(resolved_op2),
                    "subcase_id": first_subcase_id,
                    "mode_number": int(mode_number),
                    "warnings": warnings,
                    "source_label": source_label,
                },
            )
        response_values.append(float(frequency))
    if missing_modes:
        raise ValidationError(
            f"some requested modal frequency responses were not found in the {source_label} OP2 result",
            {
                "op2_path": str(resolved_op2),
                "subcase_id": first_subcase_id,
                "missing_mode_numbers": missing_modes,
                "source_label": source_label,
            },
        )
    return np.asarray(response_values, dtype=np.float64)


def _resolve_solver_op2_path(solver_payload: Dict[str, Any], *, source_label: str) -> str:
    solver = dict(solver_payload.get("solver") or {})
    if not bool(solver.get("ok", False)):
        raise ValidationError(
            f"{source_label} solve failed",
            {"source_label": source_label, "solver": solver},
        )
    summary = dict(solver.get("artifacts_summary") or {})
    for path_text in list(summary.get("op2_files") or []):
        text = str(path_text or "").strip()
        if not text:
            continue
        path = Path(text).expanduser().resolve()
        if path.exists() and path.is_file():
            return str(path)
    raise ValidationError(
        f"{source_label} solve did not produce an OP2 file",
        {
            "source_label": source_label,
            "artifacts_summary": summary,
            "warnings": solver_payload.get("warnings") or [],
        },
    )


def _run_sol103_modal_response_values(
        *,
        input_bdf: str,
        response_rows: Sequence[dict],
        output_bdf: str,
        settings: Optional[Dict[str, Any]] = None,
        nastran: Optional[str] = None,
        timeout_sec: Optional[int] = None,
        extra_args: Optional[List[str]] = None,
) -> Dict[str, Any]:
    from services.model_update.analysis.solver_service import run_nastran_sol103_job

    sol103_payload = run_nastran_sol103_job(
        input_bdf=input_bdf,
        output_bdf=output_bdf,
        settings=dict(settings or {}),
        nastran=nastran,
        run_solver=True,
        timeout_sec=timeout_sec,
        extra_args=list(extra_args or []),
    )
    op2_path = _resolve_solver_op2_path(sol103_payload, source_label="SOL103 modal")
    response_values = _extract_modal_response_values_from_op2(
        op2_path=op2_path,
        response_rows=response_rows,
        source_label="SOL103 modal",
    )
    return {
        "solver_payload": sol103_payload,
        "op2_path": op2_path,
        "response_values": response_values,
    }


def _build_sol200_parameter_rows(parameter_columns: Sequence[dict], parameter_values: Sequence[float]) -> List[dict]:
    rows: List[dict] = []
    values = list(parameter_values) if parameter_values is not None else []
    for index, raw_row in enumerate(parameter_columns or []):
        row = dict(raw_row or {})
        resolved_name = str(
            row.get("parameter_name")
            or row.get("param_name")
            or row.get("field")
            or f"parameter_{index + 1}"
        ).strip() or f"parameter_{index + 1}"
        resolved_type = str(row.get("param_type") or row.get("parameter_type") or row.get("type") or "").upper()
        row["parameter_name"] = resolved_name
        row["name"] = resolved_name
        row["parameter_type"] = resolved_type
        row["type"] = resolved_type
        row["initial"] = float(values[index])
        if row.get("lower_bound") is not None:
            row["lower"] = float(row["lower_bound"])
        elif row.get("lower") is not None:
            row["lower"] = float(row["lower"])
        if row.get("upper_bound") is not None:
            row["upper"] = float(row["upper_bound"])
        elif row.get("upper") is not None:
            row["upper"] = float(row["upper"])
        rows.append(row)
    return rows


def _build_sol200_response_rows(response_rows: Sequence[dict]) -> List[dict]:
    rows: List[dict] = []
    for index, raw_row in enumerate(response_rows or [], start=1):
        row = dict(raw_row or {})
        response_type = str(row.get("response_type") or row.get("type") or "").upper()
        mode_number = row.get("mode_number")
        if response_type not in {"FREQ", "MODAL_FREQUENCY"} or mode_number is None:
            raise ValidationError(
                "SOL200 modal Bayesian only supports modal frequency responses",
                {"response_row": row, "index": index},
            )
        rows.append(
            {
                "name": str(row.get("response_name") or f"FREQ_MODE_{int(mode_number)}").strip(),
                "type": "FREQ",
                "mode_number": int(mode_number),
            }
        )
    return rows


def _build_sol200_final_parameter_cloud_request(
        *,
        batch_no: int,
        parameter_columns: Sequence[dict],
        parameter_mappings: Sequence[dict],
        initial_parameter_values: Sequence[float],
        final_parameter_values: Sequence[float],
        result_group: Optional[str] = None,
        step_name: str = "BayesianUpdate",
        field_name: str = "PARAMETER_RELATIVE_DELTA_PERCENT",
) -> tuple[dict, dict]:
    resolved_result_group = str(result_group or _DEFAULT_SOL200_BAYESIAN_RESULT_GROUP).strip() or _DEFAULT_SOL200_BAYESIAN_RESULT_GROUP
    resolved_step_name = str(step_name or "BayesianUpdate").strip() or "BayesianUpdate"
    resolved_field_name = str(field_name or "PARAMETER_RELATIVE_DELTA_PERCENT").strip() or "PARAMETER_RELATIVE_DELTA_PERCENT"

    if len(parameter_columns or []) != len(parameter_mappings or []):
        raise ValidationError(
            "parameter mapping length does not match parameter column count",
            {"parameter_count": len(parameter_columns or []), "mapping_count": len(parameter_mappings or [])},
        )

    per_instance_labels: Dict[str, Dict[int, float]] = {}
    instance_counts: Dict[str, int] = {}

    for index, raw_mapping in enumerate(parameter_mappings or []):
        raw_mapping_dict = dict(raw_mapping or {})
        mapping = dict(raw_mapping_dict.get("element_mapping") or raw_mapping_dict)
        if "parameter_name" not in mapping and raw_mapping_dict.get("parameter_name") is not None:
            mapping["parameter_name"] = raw_mapping_dict.get("parameter_name")
        if str(mapping.get("target_kind") or "").lower() not in {"cell", ""}:
            raise ValidationError(
                "SOL200 Bayesian cloud export currently supports element targets only",
                {"parameter_name": mapping.get("parameter_name"), "target_kind": mapping.get("target_kind")},
            )
        scalar_value = _resolve_cloud_scalar_percent_value(
            updated_value=float(final_parameter_values[index]),
            baseline_value=float(initial_parameter_values[index]),
        )
        for scope_name, labels in dict(mapping.get("targets_by_scope") or {}).items():
            instance_name = str(scope_name or "").strip()
            if not instance_name:
                continue
            label_map = per_instance_labels.setdefault(instance_name, {})
            for label in labels or []:
                element_label = int(label)
                existing_value = label_map.get(element_label)
                if existing_value is not None:
                    raise ValidationError(
                        "parameter relative delta cloud export found duplicate element assignments",
                        {
                            "batch_no": int(batch_no),
                            "instance_name": instance_name,
                            "element_label": element_label,
                            "existing_value": existing_value,
                            "new_value": scalar_value,
                        },
                    )
                label_map[element_label] = float(scalar_value)

    if not per_instance_labels:
        raise ValidationError("no element targets were resolved for SOL200 parameter cloud export")

    instances_payload = []
    for instance_name, label_map in sorted(per_instance_labels.items()):
        frame_entry = {
            "frame_idx": 0,
            "frame_value": 1.0,
            "description": "Final Relative Delta Percent",
            "data": [
                {"label": int(label), "values": [float(value)]}
                for label, value in sorted(label_map.items())
            ],
        }
        instances_payload.append({"instance": instance_name, "frames": [frame_entry]})
        instance_counts[instance_name] = len(frame_entry["data"])

    request_body = {
        "step_name": resolved_step_name,
        "field_name": resolved_field_name,
        "components": ["RELATIVE_DELTA_PERCENT"],
        "result_group": resolved_result_group,
        "type": "element",
        "instances": instances_payload,
    }
    metadata = {
        "result_group": resolved_result_group,
        "step": resolved_step_name,
        "field": resolved_field_name,
        "position": "ELEMENT_NODAL",
        "value_mode": "relative_delta_percent",
        "frame_count": 1,
        "frames": [
            {
                "frame_idx": 0,
                "frame_value": 1.0,
                "description": "Final Relative Delta Percent",
            }
        ],
        "components": ["RELATIVE_DELTA_PERCENT"],
        "instances": [str(item["instance"]) for item in instances_payload],
        "instance_element_counts": {name: int(count) for name, count in sorted(instance_counts.items())},
    }
    return request_body, metadata


def _write_sol200_final_parameter_cloud_result(
        *,
        odb_id: str,
        base_url: Optional[str],
        batch_no: int,
        parameter_columns: Sequence[dict],
        parameter_mappings: Sequence[dict],
        initial_parameter_values: Sequence[float],
        final_parameter_values: Sequence[float],
        result_group: Optional[str] = None,
        step_name: str = "BayesianUpdate",
        field_name: str = "PARAMETER_RELATIVE_DELTA_PERCENT",
        timeout: int = 60,
) -> dict:
    from src.l3.core.state import registry
    from src.l3.services.external_result_writer import ExternalResultWriter

    resolved_odb_id = str(odb_id or "").strip()
    if not resolved_odb_id:
        raise ValidationError("odb_id is required for local cloud export")

    request_body, metadata = _build_sol200_final_parameter_cloud_request(
        batch_no=batch_no,
        parameter_columns=parameter_columns,
        parameter_mappings=parameter_mappings,
        initial_parameter_values=initial_parameter_values,
        final_parameter_values=final_parameter_values,
        result_group=result_group,
        step_name=step_name,
        field_name=field_name,
    )
    registry_entry = registry.get(resolved_odb_id)
    if registry_entry is None:
        raise NotFoundError(
            "cloud export target odb was not found in the local registry",
            {"odb_id": resolved_odb_id},
        )

    writer = ExternalResultWriter(registry_entry.workspace, metadata["result_group"])
    total_frames = 0
    instances_written = 0
    for inst_data in request_body.get("instances", []):
        frames_raw = []
        for frame in inst_data.get("frames", []):
            frames_raw.append(
                {
                    "frame_idx": int(frame["frame_idx"]),
                    "frame_value": float(frame.get("frame_value", 0.0)),
                    "description": frame.get("description"),
                    "data": [
                        {"label": int(entry["label"]), "values": list(entry.get("values") or [])}
                        for entry in frame.get("data", [])
                    ],
                }
            )
        written = writer.write_element(
            instance=str(inst_data.get("instance") or ""),
            step=metadata["step"],
            field=metadata["field"],
            components=list(request_body.get("components") or []),
            frames=frames_raw,
        )
        total_frames = max(total_frames, int(written))
        instances_written += 1
    write_response = {
        "field_name": metadata["field"],
        "step_name": metadata["step"],
        "instances_written": int(instances_written),
        "frames_written": int(total_frames),
        "source": "external_local",
    }

    result = dict(metadata)
    result.update(
        {
            "odb_id": resolved_odb_id,
            "base_url": None,
            "write_response": write_response,
            "query_hint": {
                "endpoint": "/api/odb/{odb_id}/results/frame-scalars",
                "odb_id": resolved_odb_id,
                "result_group": metadata["result_group"],
                "step": metadata["step"],
                "field": metadata["field"],
                "frame": 0,
                "component_idx": 0,
            },
        }
    )
    return result


def _resolve_loaded_odb_id_for_workspace(workspace: Optional[str]) -> Optional[str]:
    if not workspace:
        return None
    try:
        normalized_workspace = os.path.normcase(os.path.abspath(workspace))
    except Exception:
        return None

    loaded = getattr(_sens.registry, "loaded", {})
    for loaded_odb_id, model_index in dict(loaded).items():
        candidate_workspace = getattr(model_index, "workspace", None)
        if not candidate_workspace:
            continue
        if os.path.normcase(os.path.abspath(candidate_workspace)) == normalized_workspace:
            return str(loaded_odb_id)
    return None


def _build_bayesian_cloud_request(
        *,
        batch_no: int,
        iteration_results: Sequence[dict],
        result_group: Optional[str] = None,
        step_name: str = "BayesianUpdate",
        field_name: str = "PARAMETER_CLOUD",
        value_mode: str = "updated_value",
) -> tuple[dict, dict]:
    if not iteration_results:
        raise ValidationError("iteration_results must not be empty for cloud export")

    resolved_result_group = str(result_group or f"bayesian_batch_{int(batch_no)}")
    resolved_step_name = str(step_name or "BayesianUpdate").strip() or "BayesianUpdate"
    resolved_field_name = str(field_name or "PARAMETER_CLOUD").strip() or "PARAMETER_CLOUD"

    first_iteration = dict(iteration_results[0])
    parameter_columns = list(first_iteration.get("parameter_columns") or [])
    if not parameter_columns:
        raise ValidationError("parameter_columns are required for cloud export")

    components = [
        str(column.get("parameter_name") or column.get("field") or f"parameter_{index + 1}")
        for index, column in enumerate(parameter_columns)
    ]
    baseline_values = {
        components[index]: float(column.get("parameter_value") or 0.0)
        for index, column in enumerate(parameter_columns)
    }

    instances_payload: List[dict] = []
    instance_frames: Dict[str, List[dict]] = {}
    instance_counts: Dict[str, List[int]] = {}

    for iteration_index, iteration_result in enumerate(iteration_results):
        mappings = list(iteration_result.get("parameter_element_mapping") or [])
        if len(mappings) != len(components):
            raise ValidationError(
                "parameter_element_mapping length does not match parameter column count",
                {"expected": len(components), "actual": len(mappings), "iteration": iteration_index + 1},
            )

        per_instance_labels: Dict[str, Dict[int, List[float]]] = {}

        for component_index, raw_mapping in enumerate(mappings):
            raw_mapping_dict = dict(raw_mapping or {})
            mapping = dict(raw_mapping_dict.get("element_mapping") or raw_mapping_dict)
            if "parameter_name" not in mapping and raw_mapping_dict.get("parameter_name") is not None:
                mapping["parameter_name"] = raw_mapping_dict.get("parameter_name")
            if "updated_parameter_value" not in mapping and raw_mapping_dict.get("updated_parameter_value") is not None:
                mapping["updated_parameter_value"] = raw_mapping_dict.get("updated_parameter_value")
            if "parameter_value" not in mapping and raw_mapping_dict.get("parameter_value") is not None:
                mapping["parameter_value"] = raw_mapping_dict.get("parameter_value")
            target_kind = str(mapping.get("target_kind") or "").lower()
            if target_kind not in {"cell", ""}:
                raise ValidationError(
                    "bayesian cloud export currently supports element targets only",
                    {
                        "target_kind": mapping.get("target_kind"),
                        "parameter_name": mapping.get("parameter_name"),
                    },
                )

            parameter_name = components[component_index]
            updated_value = float(mapping.get("updated_parameter_value", mapping.get("parameter_value", 0.0)))
            baseline_value = float(baseline_values.get(parameter_name, mapping.get("parameter_value", 0.0)))
            scalar_value = _resolve_cloud_scalar_value(
                mode=value_mode,
                updated_value=updated_value,
                baseline_value=baseline_value,
            )

            for scope_name, labels in dict(mapping.get("targets_by_scope") or {}).items():
                instance_name = str(scope_name or "").strip()
                if not instance_name:
                    continue
                label_map = per_instance_labels.setdefault(instance_name, {})
                for label in labels or []:
                    element_label = int(label)
                    values = label_map.setdefault(
                        element_label,
                        [float("nan")] * len(components),
                    )
                    values[component_index] = float(scalar_value)

        for instance_name, label_map in per_instance_labels.items():
            frame_entry = {
                "frame_idx": iteration_index,
                "frame_value": float(iteration_index + 1),
                "data": [
                    {"label": int(label), "values": values}
                    for label, values in sorted(label_map.items())
                ],
            }
            instance_frames.setdefault(instance_name, []).append(frame_entry)
            instance_counts.setdefault(instance_name, []).append(len(frame_entry["data"]))

    if not instance_frames:
        raise ValidationError("no element targets were resolved for bayesian cloud export")

    for instance_name, frames in sorted(instance_frames.items()):
        instances_payload.append({"instance": instance_name, "frames": frames})

    request_body = {
        "step_name": resolved_step_name,
        "field_name": resolved_field_name,
        "components": components,
        "result_group": resolved_result_group,
        "type": "element",
        "instances": instances_payload,
    }
    metadata = {
        "result_group": resolved_result_group,
        "step": resolved_step_name,
        "field": resolved_field_name,
        "position": "ELEMENT_NODAL",
        "value_mode": value_mode,
        "frame_count": len(iteration_results),
        "frames": [
            {
                "frame_idx": index,
                "frame_value": float(index + 1),
                "description": f"Iteration {index + 1}",
            }
            for index in range(len(iteration_results))
        ],
        "components": components,
        "instances": [str(item["instance"]) for item in instances_payload],
        "instance_element_counts": {
            instance_name: [int(count) for count in counts]
            for instance_name, counts in sorted(instance_counts.items())
        },
    }
    return request_body, metadata


def _write_bayesian_cloud_result(
        *,
        odb_id: str,
        base_url: Optional[str],
        batch_no: int,
        iteration_results: Sequence[dict],
        result_group: Optional[str] = None,
        step_name: str = "BayesianUpdate",
        field_name: str = "PARAMETER_CLOUD",
        value_mode: str = "updated_value",
        timeout: int = 60,
) -> dict:
    resolved_odb_id = str(odb_id or "").strip()
    if not resolved_odb_id:
        raise ValidationError("odb_id is required for cloud export via external-field api")

    request_body, metadata = _build_bayesian_cloud_request(
        batch_no=batch_no,
        iteration_results=iteration_results,
        result_group=result_group,
        step_name=step_name,
        field_name=field_name,
        value_mode=value_mode,
    )
    resolved_base_url = str(base_url or "").strip().rstrip("/")
    write_response = write_external_field_local(resolved_odb_id, request_body)

    result = dict(metadata)
    result.update(
        {
            "odb_id": resolved_odb_id,
            "base_url": resolved_base_url,
            "write_response": write_response,
            "query_hint": {
                "endpoint": "/api/odb/{odb_id}/results/frame-scalars",
                "odb_id": resolved_odb_id,
                "result_group": metadata["result_group"],
                "step": metadata["step"],
                "field": metadata["field"],
                "frame": 0,
                "component_idx": 0,
            },
        }
    )
    return result


def _read_text_matrix(file_path: str, row_start: int, row_count: int, col_start: int = 1) -> np.ndarray:
    if int(row_count) <= 0:
        raise ValidationError("row_count must be > 0", {"row_count": row_count})
    rows = []
    expected_cols = None
    for offset in range(int(row_count)):
        row = read_row_from_m_n(file_path, int(row_start) + offset, int(col_start)).reshape(-1)
        if expected_cols is None:
            expected_cols = len(row)
        elif len(row) != expected_cols:
            raise ValidationError(
                "text matrix rows have inconsistent column counts",
                {
                    "file_path": os.path.abspath(file_path),
                    "row_start": int(row_start),
                    "row_count": int(row_count),
                    "expected_cols": expected_cols,
                    "actual_cols": len(row),
                    "row": int(row_start) + offset,
                },
            )
        rows.append(row)
    return np.vstack(rows)


def _read_text_row_vector(file_path: str, row: int, col_start: int = 1) -> np.ndarray:
    return read_row_from_m_n(file_path, int(row), int(col_start)).reshape(-1)


def _parameter_values_from_inp(input_inp: str, parameter_names: Sequence[str]) -> np.ndarray:
    model = parse_inp(input_inp)
    values = []
    missing = []
    for name in parameter_names:
        definition = getattr(model, "parameters", {}).get(str(name))
        scalar_value = getattr(definition, "scalar_value", None) if definition is not None else None
        if scalar_value is None:
            missing.append(str(name))
        else:
            values.append(float(scalar_value))
    if missing:
        raise ValidationError(
            "some parameter values could not be resolved from the inp file",
            {"input_inp": os.path.abspath(input_inp), "missing_parameters": missing[:20]},
        )
    return np.asarray(values, dtype=np.float64)


def _expand_scatter_vector(scatter: Any, expected_size: int, *, label: str) -> np.ndarray:
    values = np.asarray(scatter, dtype=np.float64).reshape(-1)
    if values.size == 1 and int(expected_size) > 1:
        values = np.full(int(expected_size), float(values[0]), dtype=np.float64)
    if values.size != int(expected_size):
        raise ValidationError(
            f"{label} size mismatch",
            {"expected": int(expected_size), "actual": int(values.size)},
        )
    if np.any(values <= 0):
        raise ValidationError(
            f"{label} must be > 0",
            {"label": label, "values": values.tolist()},
        )
    return values


def build_normalized_residual(
        r_model: Any,
        r_target: Any,
        *,
        eps: float = 1e-12,
) -> np.ndarray:
    # Follow the FEMTools-style normalized residual used in Untitled-3.py:
    # y_i = (r_model_i - r_target_i) / |r_model_i|
    # If the residual is written as dR_i = r_target_i - r_model_i, then:
    #   -y_i = dR_i / |r_model_i|
    # The later update uses x = -G_n y, so the sign convention is equivalent
    # to applying the normalized residual dR / r in the update direction.
    r_model_arr = np.asarray(r_model, dtype=np.float64).reshape(-1)
    r_target_arr = np.asarray(r_target, dtype=np.float64).reshape(-1)
    if r_model_arr.shape != r_target_arr.shape:
        raise ValidationError(
            "r_model and r_target size mismatch",
            {"r_model_size": int(r_model_arr.size), "r_target_size": int(r_target_arr.size)},
        )
    return (
            (r_model_arr - r_target_arr).reshape(-1, 1)
            / np.maximum(np.abs(r_model_arr).reshape(-1, 1), float(eps))
    )


def _build_normalized_gain_matrix(
        S_norm: Any,
        p_scatter: Any,
        r_scatter: Any,
        *,
        damping: float = 1e-8,
        eps: float = 1e-12,
) -> dict:
    S_norm_arr = np.asarray(S_norm, dtype=np.float64)
    if S_norm_arr.ndim != 2:
        raise ValidationError("S_norm must be a 2D matrix", {"shape": list(S_norm_arr.shape)})

    n_resp, n_param = S_norm_arr.shape
    p_scatter_arr = _expand_scatter_vector(p_scatter, n_param, label="parameter scatter")
    r_scatter_arr = _expand_scatter_vector(r_scatter, n_resp, label="response scatter")

    # Both covariance-like matrices are defined in normalized space, so the
    # gain matrix below is also a normalized-space quantity.
    #
    # The key point is that S_norm is not the raw sensitivity dR/dp.
    # Upstream DSA assembly already converts each scalar sensitivity to:
    #
    #   S_norm(j, i) = (dR_j / dp_i) * p_i / r_j
    #
    # Therefore G_n is the gain matrix associated with the normalized system,
    # not a raw gain matrix that still needs an extra p/r factor afterwards.
    Cp_n = 2.0 * np.diag(1.0 / np.maximum(p_scatter_arr, float(eps)) ** 2)
    Cr_n = np.diag(1.0 / np.maximum(r_scatter_arr, float(eps)) ** 2)
    Cp_n_eff = Cp_n + float(damping) * np.eye(n_param)

    Cp_n_inv = np.linalg.inv(Cp_n_eff)
    Cr_n_inv = np.linalg.inv(Cr_n)
    innovation_cov = Cr_n_inv + S_norm_arr @ Cp_n_inv @ S_norm_arr.T
    G_n = Cp_n_inv @ S_norm_arr.T @ np.linalg.inv(innovation_cov)

    return {
        "Cp_n": Cp_n,
        "Cr_n": Cr_n,
        "Cp_n_eff": Cp_n_eff,
        "Cp_n_inv": Cp_n_inv,
        "Cr_n_inv": Cr_n_inv,
        "innovation_cov": innovation_cov,
        "G_n": G_n,
        "p_scatter": p_scatter_arr,
        "r_scatter": r_scatter_arr,
    }


def bayesian_update_normalized(
        p_current,
        r_model,
        r_target,
        S_norm,
        p_scatter,
        r_scatter,
        damping: float = 1e-8,
        step_scale: float = 1.0,
        lower_bound=None,
        upper_bound=None,
        p_ref=None,
) -> dict:
    p_current = np.asarray(p_current, dtype=float).reshape(-1)
    r_model = np.asarray(r_model, dtype=float).reshape(-1)
    r_target = np.asarray(r_target, dtype=float).reshape(-1)
    S_norm = np.asarray(S_norm, dtype=float)

    if S_norm.ndim != 2:
        raise ValidationError("S_norm must be a 2D matrix", {"shape": list(S_norm.shape)})
    if S_norm.shape != (len(r_model), len(p_current)):
        raise ValidationError(
            "S_norm shape does not match response and parameter counts",
            {"shape": list(S_norm.shape), "response_count": len(r_model), "parameter_count": len(p_current)},
        )

    eps = 1e-12
    delta_r = r_model - r_target

    if p_ref is None:
        p_ref = p_current.copy()
    p_ref = np.asarray(p_ref, dtype=float).reshape(-1)
    p_ref = np.maximum(np.abs(p_ref), eps)

    Dp = np.diag(p_ref)
    # y is the normalized residual vector and G_n is the normalized gain matrix.
    # The update is applied in two stages:
    #
    #   x = -G_n * y
    #   dp = Dp * x
    #   p_new = p_current + dp
    #
    # Combining them gives the practical form:
    #
    #   p_new = p_current + Dp * (-G_n * y)
    #
    # For a single response / single parameter case this corresponds to the
    # same idea as:
    #
    #   p_u = p_0 + p_ii * (...) * dR / r_jj
    #
    # where:
    #   1. the p_i / r_j factor is already embedded in S_norm
    #   2. G_n is built from S_norm, Cp_n, Cr_n in normalized space
    #   3. Dp multiplies back the parameter scale before writing p_new
    y = build_normalized_residual(r_model=r_model, r_target=r_target, eps=eps)
    gain_payload = _build_normalized_gain_matrix(
        S_norm=S_norm,
        p_scatter=p_scatter,
        r_scatter=r_scatter,
        damping=damping,
        eps=eps,
    )
    G_n = gain_payload["G_n"]

    x = float(step_scale) * (G_n @ (-y))
    dp = Dp @ x
    p_new = p_current + dp.reshape(-1)

    if lower_bound is not None:
        p_new = np.maximum(p_new, np.asarray(lower_bound, dtype=float).reshape(-1))
    if upper_bound is not None:
        p_new = np.minimum(p_new, np.asarray(upper_bound, dtype=float).reshape(-1))

    return {
        "delta_r": delta_r.reshape(-1, 1),
        "y": y,
        "x": x,
        "dp": dp,
        "p_new": p_new,
        "G_n": G_n,
        "Cp_n": gain_payload["Cp_n"],
        "Cr_n": gain_payload["Cr_n"],
        "Cp_n_eff": gain_payload["Cp_n_eff"],
        "innovation_cov": gain_payload["innovation_cov"],
        "normalized_parameter_scatter": gain_payload["p_scatter"],
        "normalized_response_scatter": gain_payload["r_scatter"],
    }


def update_parameter_section_values(
        input_inp: str,
        parameter_values: Dict[str, float],
        *,
        output_inp: Optional[str] = None,
) -> dict:
    input_path = _solver._abs_file(input_inp, "input_inp")
    output_path = Path(output_inp).expanduser().resolve() if output_inp else input_path
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines = input_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    updated_lines: List[str] = []
    inside_parameter_block = False
    seen = set()

    for line in lines:
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("*PARAMETER"):
            inside_parameter_block = True
            updated_lines.append(line)
            continue
        if inside_parameter_block and stripped.startswith("*"):
            inside_parameter_block = False

        if inside_parameter_block and stripped and not stripped.startswith("**"):
            parts = [part.strip() for part in line.split(",") if part.strip()]
            replaced_parts = []
            changed = False
            for part in parts:
                match = _PARAMETER_ASSIGNMENT_RE.match(part)
                if not match:
                    replaced_parts.append(part)
                    continue
                name = str(match.group(1)).strip()
                if name in parameter_values:
                    replaced_parts.append(f"{name}={_format_scalar(parameter_values[name])}")
                    seen.add(name)
                    changed = True
                else:
                    replaced_parts.append(f"{name}={match.group(2).strip()}")
            updated_lines.append(",".join(replaced_parts) if changed else line)
            continue

        updated_lines.append(line)

    missing = sorted(str(name) for name in parameter_values.keys() if str(name) not in seen)
    if missing:
        raise ValidationError(
            "some parameters were not found under any *PARAMETER block",
            {"missing_parameters": missing[:20], "input_inp": str(input_path)},
        )

    output_path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")
    return {
        "input_inp": str(input_path),
        "output_inp": str(output_path),
        "updated_parameters": {str(key): float(value) for key, value in parameter_values.items()},
    }


def build_dsa_normalized_sensitivity_matrix(
        *,
        project_id: int,
        odb_id: Optional[str] = None,
        base_url: Optional[str] = None,
        inp_path: Optional[str] = None,
        workspace: Optional[str] = None,
        odb_path: Optional[str] = None,
        workspace_root: Optional[str] = None,
        step: Optional[str] = None,
        instances: Optional[List[str]] = None,
        field_prefix: str = "d_U_",
        response_component: Optional[str] = None,
        position: Optional[str] = None,
        aggregation: str = "max_abs",
        frame: Optional[int] = None,
        abaqus: Optional[str] = None,
        python3: Optional[str] = None,
        keep_raw: bool = False,
        timeout: int = 60,
        result_group: Optional[str] = None,
        optimization_parameter_rows: Optional[List[dict]] = None,
) -> dict:
    if aggregation not in _sens._AGGREGATIONS and aggregation != "first":
        raise ValidationError(
            f"unsupported aggregation '{aggregation}'",
            {"aggregation": aggregation, "allowed": sorted(_sens._AGGREGATIONS | {'first'})},
        )

    resolved_inp_path = _normalize_optional_path(inp_path) or _sens._resolve_inp_path_from_project(project_id)
    if not os.path.exists(resolved_inp_path):
        raise NotFoundError(f"inp file not found: {resolved_inp_path}", {"inp_path": resolved_inp_path})

    resolved_workspace = _normalize_optional_path(workspace)
    workspace_built = False
    client = None
    selector = _sens._build_field_selector(field_prefix=field_prefix)
    resolved_base_url = str(base_url or get_local_service_base_url()).strip().rstrip("/")

    if odb_path:
        odb_abs = os.path.abspath(odb_path)
        if not resolved_workspace:
            workspace_parent = Path(workspace_root).expanduser().resolve() if workspace_root else Path(odb_abs).resolve().parent
            workspace_parent.mkdir(parents=True, exist_ok=True)
            resolved_workspace = str((workspace_parent / f"{Path(odb_abs).stem}_workspace").resolve())
        _sens.build_workspace_from_odb(
            project_id=None,
            odb_path=odb_abs,
            workspace=resolved_workspace,
            abaqus=abaqus,
            python3=python3,
            keep_raw=keep_raw,
        )
        resolved_workspace = _sens._workspace_path(resolved_workspace)
        workspace_built = True
    elif resolved_workspace:
        resolved_workspace = _sens._workspace_path(resolved_workspace)

    if resolved_workspace:
        discovery = _sens._discover_sensitivity_fields_from_workspace(
            resolved_workspace,
            step=step,
            instances=instances,
            selector=selector,
            position=position,
        )
        source_mode = "workspace"
    elif odb_id and (not base_url or _sens.registry.get(str(odb_id)) is not None):
        discovery = _sens._discover_sensitivity_fields_from_registry(
            odb_id,
            step=step,
            instances=instances,
            selector=selector,
            position=position,
        )
        resolved_workspace = discovery["workspace"]
        source_mode = "registry"
    else:
        if not odb_id:
            raise ValidationError(
                "odb_id is required when workspace/odb_path are not provided",
                {"odb_id": odb_id},
            )
        client = _sens.ODBClient(base_url=resolved_base_url, timeout=timeout)
        discovery = _sens._discover_sensitivity_fields(
            client,
            odb_id,
            step=step,
            instances=instances,
            selector=selector,
            position=position,
        )
        source_mode = "l3_api"

    if resolved_workspace:
        resolved_frame = _sens._resolve_workspace_step_frame(
            resolved_workspace,
            step=str(discovery["step"]),
            requested_frame=frame,
        )
    else:
        resolved_frame = int(frame) if frame is not None else 0

    # DSA columns come from result fields, then are mapped back to design parameters
    # and finally to INP target sets/sections.
    dsa_model = parse_inp(resolved_inp_path)
    dsa_parameter_rows = (
        [dict(row) for row in optimization_parameter_rows]
        if optimization_parameter_rows is not None
        else _sens._load_project_optimization_parameters(project_id)
    )
    dsa_parameter_map = (
        _sens._build_dsa_parameter_row_map(dsa_parameter_rows, field_prefix, discovery["field_names"])
        if dsa_parameter_rows
        else {}
    )
    dsa_direct_target_map = build_parameter_target_map(dsa_model)
    dsa_design_parameter_name_map = _sens._build_dsa_design_parameter_name_map(dsa_model)
    dsa_response_specs = _sens._resolve_dsa_response_spec(dsa_model, step_name=discovery["step"]) or []
    if not dsa_response_specs:
        raise ValidationError(
            "no usable design response definition was found in the inp file",
            {"inp_path": resolved_inp_path, "step": discovery["step"]},
        )
    selected_response_specs, explicit_response = _sens._select_dsa_response_specs(
        list(dsa_response_specs),
        field_prefix=field_prefix,
        response_component=response_component,
    )

    response_value_cache: Dict[Tuple[str, str, str, str, Optional[str], Optional[int], int, str], Dict[str, Any]] = {}
    row_order: List[str] = []
    row_meta_map: Dict[str, dict] = {}
    row_response_map: Dict[str, float] = {}
    column_meta_map: Dict[str, dict] = {}
    column_value_map: Dict[str, Dict[str, float]] = {}

    for instance_name in discovery["instances"]:
        for field_meta in discovery["per_instance"][instance_name]:
            source_field_name = str(field_meta["field"])
            selected_position = str(field_meta["position"])
            parameter_token = _sens._extract_dsa_field_token(field_prefix, source_field_name)

            direct_target_rows = list(dsa_direct_target_map.get(parameter_token, []))
            mapped_parameter_name = parameter_token
            token_match = _sens._DSA_PARAMETER_TOKEN_RE.fullmatch(parameter_token)
            token_index = int(token_match.group(2)) if token_match else None
            if not direct_target_rows and token_index is not None and token_index in dsa_design_parameter_name_map:
                mapped_parameter_name = dsa_design_parameter_name_map[token_index]
                direct_target_rows = list(dsa_direct_target_map.get(mapped_parameter_name, []))

            target_rows = direct_target_rows
            mapping_mode = "inp_parameter"
            if not target_rows:
                if not dsa_parameter_map:
                    raise ValidationError(
                        "unable to map DSA field to a model-update parameter",
                        {"field": source_field_name, "field_prefix": field_prefix},
                    )
                parameter_row = _sens._resolve_dsa_parameter_row(dsa_parameter_map, source_field_name)
                target_rows = [parameter_row]
                mapped_parameter_name = str(parameter_row.get("parameter_name") or parameter_token)
                mapping_mode = "optimization_parameter"

            # A DSA field may match multiple design responses in the INP. We keep the
            # candidate with the largest overlap of result labels.
            candidate_specs = list(selected_response_specs)

            best_score = -1
            best_specs = []
            best_sensitivity_map = None
            best_response_map = None
            best_position = None
            best_response_field_meta = None

            for spec in candidate_specs:
                candidate_field_name = str(spec["field_name"])
                candidate_component = (
                    explicit_response["component"]
                    if explicit_response is not None
                    else spec.get("component")
                )
                candidate_component_index = (
                    explicit_response["component_index"]
                    if explicit_response is not None
                    else spec.get("component_index")
                )
                _, response_targets = _sens._design_response_target_labels(dsa_model, spec)
                response_target_set = {str(item) for item in response_targets}
                response_target_node_labels = _scoped_target_node_labels_for_instance(
                    response_targets,
                    instance_name=str(instance_name),
                )
                source_component = candidate_component
                if (
                        str(candidate_component or "").strip().upper() == "MISES"
                        and candidate_field_name != source_field_name
                ):
                    source_component = None

                if spec.get("region_type") == "NODE" and str(selected_position).upper() == "ELEMENT_NODAL":
                    if source_mode not in {"workspace", "registry"}:
                        raise ValidationError(
                            "node-based ELEMENT_NODAL response normalization currently requires a local workspace",
                            {
                                "field": source_field_name,
                                "instance": instance_name,
                                "source_mode": source_mode,
                            },
                        )
                    candidate_dsa_map = _sens._workspace_element_nodal_node_label_map(
                        resolved_workspace,
                        inp_model=dsa_model,
                        step=discovery["step"],
                        field=source_field_name,
                        instance=instance_name,
                        frame=resolved_frame,
                        aggregation=aggregation,
                        node_labels=response_target_node_labels,
                        component=source_component,
                        component_index=candidate_component_index,
                        result_group=result_group,
                    )
                else:
                    if source_mode in {"workspace", "registry"}:
                        candidate_dsa_map = _sens._workspace_result_label_map(
                            resolved_workspace,
                            step=discovery["step"],
                            field=source_field_name,
                            instance=instance_name,
                            position=selected_position,
                            frame=resolved_frame,
                            aggregation=aggregation,
                            component=source_component,
                            component_index=candidate_component_index,
                            result_group=result_group,
                        )
                    else:
                        candidate_dsa_map = client.get_result_label_map(
                            odb_id=odb_id,
                            instance=instance_name,
                            step=discovery["step"],
                            field=source_field_name,
                            position=selected_position,
                            frame=resolved_frame,
                            aggregation=aggregation,
                            component=source_component,
                            component_index=candidate_component_index,
                            scoped=True,
                        )
                candidate_dsa_map = _sens._normalize_vtu_label_map(candidate_dsa_map)
                if not candidate_dsa_map:
                    continue
                if response_target_set:
                    candidate_dsa_map = {
                        str(label): value
                        for label, value in candidate_dsa_map.items()
                        if str(label) in response_target_set
                    }
                if not candidate_dsa_map:
                    continue

                response_field_meta = _sens._resolve_response_field_meta(
                    source_mode=source_mode,
                    workspace=resolved_workspace,
                    client=client,
                    odb_id=odb_id,
                    step=discovery["step"],
                    instance=instance_name,
                    field=candidate_field_name,
                )
                candidate_position = _sens._pick_response_position(response_field_meta, spec.get("preferred_position"))
                cache_key = (
                    str(instance_name),
                    str(discovery["step"]),
                    candidate_field_name,
                    candidate_position,
                    candidate_component,
                    candidate_component_index,
                    int(resolved_frame),
                    str(aggregation),
                )
                if cache_key not in response_value_cache:
                    if spec.get("region_type") == "NODE" and str(candidate_position).upper() == "ELEMENT_NODAL":
                        if source_mode not in {"workspace", "registry"}:
                            raise ValidationError(
                                "node-based ELEMENT_NODAL response normalization currently requires a local workspace",
                                {
                                    "field": candidate_field_name,
                                    "instance": instance_name,
                                    "source_mode": source_mode,
                                },
                            )
                        cached_response_map = _sens._workspace_element_nodal_node_label_map(
                            resolved_workspace,
                            inp_model=dsa_model,
                            step=discovery["step"],
                            field=candidate_field_name,
                            instance=instance_name,
                            frame=resolved_frame,
                            aggregation=aggregation,
                            node_labels=response_target_node_labels,
                            component=candidate_component,
                            component_index=candidate_component_index,
                            result_group=result_group,
                        )
                    else:
                        if source_mode in {"workspace", "registry"}:
                            cached_response_map = _sens._workspace_result_label_map(
                                resolved_workspace,
                                step=discovery["step"],
                                field=candidate_field_name,
                                instance=instance_name,
                                position=candidate_position,
                                frame=resolved_frame,
                                aggregation=aggregation,
                                component=candidate_component,
                                component_index=candidate_component_index,
                                result_group=result_group,
                            )
                        else:
                            cached_response_map = client.get_result_label_map(
                                odb_id=odb_id,
                                instance=instance_name,
                                step=discovery["step"],
                                field=candidate_field_name,
                                position=candidate_position,
                                frame=resolved_frame,
                                aggregation=aggregation,
                                component=candidate_component,
                                component_index=candidate_component_index,
                                scoped=True,
                            )
                    response_value_cache[cache_key] = _sens._normalize_vtu_label_map(cached_response_map)

                candidate_response_map = {
                    str(label): value
                    for label, value in dict(response_value_cache[cache_key]).items()
                    if not response_target_set or str(label) in response_target_set
                }
                overlap = sorted(set(candidate_dsa_map.keys()) & set(candidate_response_map.keys()))
                score = len(overlap)
                if score <= 0:
                    continue
                if score > best_score:
                    best_score = score
                    best_specs = [spec]
                    best_sensitivity_map = candidate_dsa_map
                    best_response_map = candidate_response_map
                    best_position = candidate_position
                    best_response_field_meta = response_field_meta
                elif score == best_score:
                    best_specs.append(spec)

            if len(best_specs) > 1:
                raise ValidationError(
                    "multiple design responses match the requested DSA field",
                    {
                        "field_prefix": field_prefix,
                        "source_field": source_field_name,
                        "step": discovery["step"],
                        "instance": instance_name,
                        "matches": [
                            {
                                "field_name": str(spec["field_name"]),
                                "component": spec.get("component"),
                                "set_name": spec.get("set_name"),
                                "region_type": spec.get("region_type"),
                            }
                            for spec in best_specs
                        ],
                    },
                )
            if not best_specs or best_sensitivity_map is None or best_response_map is None or best_position is None:
                raise ValidationError(
                    "unable to resolve a normalized design-response sensitivity map",
                    {"field": source_field_name, "instance": instance_name, "field_prefix": field_prefix},
                )

            chosen_response_spec = best_specs[0]
            response_field_name = str(chosen_response_spec["field_name"])
            resolved_response_component = (
                explicit_response["component"]
                if explicit_response is not None
                else chosen_response_spec.get("component")
            )
            if (
                    explicit_response is None
                    and resolved_response_component is None
                    and best_response_field_meta is not None
            ):
                (
                    best_sensitivity_map,
                    best_response_map,
                    inferred_component,
                    _,
                ) = _sens._resolve_vector_design_response_component(
                    best_sensitivity_map,
                    best_response_map,
                    response_field_meta=best_response_field_meta,
                    response_field_name=response_field_name,
                    source_field_name=source_field_name,
                    instance_name=str(instance_name),
                )
                if inferred_component is not None:
                    resolved_response_component = inferred_component
            parameter_value = _sens._resolve_dsa_parameter_scalar_value(
                dsa_model,
                parameter_name=mapped_parameter_name,
                target_rows=target_rows,
            )

            existing_column_meta = column_meta_map.get(source_field_name)
            parameter_scatter = float(
                parameter_row.get("scatter", _sens._DEFAULT_PARAMETER_SCATTER)
                if mapping_mode == "optimization_parameter"
                else _sens._DEFAULT_PARAMETER_SCATTER
            )
            column_meta = {
                "field": source_field_name,
                "parameter_token": parameter_token,
                "parameter_name": mapped_parameter_name,
                "parameter_value": float(parameter_value),
                "mapping_mode": mapping_mode,
                "scatter": parameter_scatter,
                "element_mapping": _parameter_element_mapping_entry(
                    model=dsa_model,
                    source_field_name=source_field_name,
                    parameter_token=parameter_token,
                    mapped_parameter_name=mapped_parameter_name,
                    parameter_value=float(parameter_value),
                    target_rows=target_rows,
                    mapping_mode=mapping_mode,
                    scatter=parameter_scatter,
                ),
            }
            if existing_column_meta is not None and existing_column_meta != column_meta:
                raise ValidationError(
                    "inconsistent parameter metadata found across DSA result blocks",
                    {"field": source_field_name, "existing": existing_column_meta, "incoming": column_meta},
                )
            column_meta_map[source_field_name] = column_meta
            column_values = column_value_map.setdefault(source_field_name, {})

            matched_labels = sorted(set(best_sensitivity_map.keys()) & set(best_response_map.keys()))
            if not matched_labels:
                raise ValidationError(
                    "no overlapping response labels were found for DSA normalization",
                    {"source_field": source_field_name, "response_field": response_field_name, "instance": instance_name},
                )

            for response_label in matched_labels:
                row_key = _response_row_key(
                    instance=str(instance_name),
                    response_field=response_field_name,
                    response_component=resolved_response_component,
                    response_position=str(best_position),
                    response_label=str(response_label),
                )
                normalized_value = _sens._normalize_dsa_sensitivity_value(
                    best_sensitivity_map[response_label],
                    parameter_value=parameter_value,
                    response_value=best_response_map[response_label],
                    source_field=source_field_name,
                    response_field=response_field_name,
                )
                response_scalar = _scalarize(best_response_map[response_label], field=response_field_name, row_key=row_key)
                normalized_scalar = _scalarize(normalized_value, field=source_field_name, row_key=row_key)

                if row_key not in row_meta_map:
                    row_order.append(row_key)
                    row_meta_map[row_key] = {
                        "row_key": row_key,
                        "instance": str(instance_name),
                        "response_field": response_field_name,
                        "response_component": resolved_response_component,
                        "response_position": str(best_position),
                        "response_label": str(response_label),
                    }
                if row_key in row_response_map and not np.isclose(row_response_map[row_key], response_scalar):
                    raise ValidationError(
                        "inconsistent current response values found while building the normalized sensitivity matrix",
                        {
                            "row_key": row_key,
                            "existing_response_value": row_response_map[row_key],
                            "incoming_response_value": response_scalar,
                        },
                    )
                row_response_map[row_key] = response_scalar
                if row_key in column_values and not np.isclose(column_values[row_key], normalized_scalar):
                    raise ValidationError(
                        "conflicting normalized sensitivity values found while building the sensitivity matrix",
                        {
                            "row_key": row_key,
                            "field": source_field_name,
                            "existing_value": column_values[row_key],
                            "incoming_value": normalized_scalar,
                        },
                    )
                column_values[row_key] = normalized_scalar

    ordered_fields = _ordered_dsa_field_names(field_prefix, list(column_meta_map.keys()))
    ordered_rows = [row_meta_map[row_key] for row_key in row_order]
    ordered_columns = [column_meta_map[field_name] for field_name in ordered_fields]

    matrix = np.empty((len(ordered_rows), len(ordered_columns)), dtype=np.float64)
    for col_idx, field_name in enumerate(ordered_fields):
        values_for_field = column_value_map.get(field_name, {})
        for row_idx, row_meta in enumerate(ordered_rows):
            row_key = str(row_meta["row_key"])
            if row_key not in values_for_field:
                raise ValidationError(
                    "normalized sensitivity matrix is incomplete for the resolved response rows",
                    {"field": field_name, "missing_row": row_key},
                )
            matrix[row_idx, col_idx] = float(values_for_field[row_key])

    response_values = [float(row_response_map[str(item["row_key"])]) for item in ordered_rows]
    parameter_values = [float(item["parameter_value"]) for item in ordered_columns]

    return {
        "project_id": project_id,
        "odb_id": odb_id,
        "base_url": resolved_base_url if source_mode == "l3_api" else None,
        "workspace": resolved_workspace,
        "source_mode": source_mode,
        "workspace_built": workspace_built,
        "inp_path": resolved_inp_path,
        "step": discovery["step"],
        "instances": discovery["instances"],
        "frame": int(resolved_frame),
        "aggregation": aggregation,
        "field_prefix": field_prefix,
        "response_component": explicit_response["component"] if explicit_response is not None else None,
        "response_rows": ordered_rows,
        "parameter_columns": ordered_columns,
        "response_values": response_values,
        "parameter_values": parameter_values,
        "matrix": matrix.tolist(),
    }


def _copy_iteration_input(input_inp: str, output_dir: Path, iteration: int, *, base_stem: Optional[str] = None) -> Path:
    source_path = Path(input_inp).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    resolved_base_stem = str(base_stem or source_path.stem)
    copied_path = (output_dir / f"{resolved_base_stem}_iter{iteration}.inp").resolve()
    shutil.copyfile(str(source_path), str(copied_path))
    return copied_path


def _cleanup_iteration_solver_files(workdir: Path, job_name: str) -> List[str]:
    deleted: List[str] = []
    for suffix in _ITERATION_CLEANUP_SUFFIXES:
        path = (workdir / f"{job_name}{suffix}").resolve()
        if not path.exists() or not path.is_file():
            continue
        path.unlink()
        deleted.append(str(path))
    return deleted


def _run_iteration_solver(
        *,
        inp_path: Path,
        output_dir: Path,
        iteration: int,
        abaqus: str,
        job_name: Optional[str],
        cpus: Optional[int],
        interactive: bool,
        timeout_sec: Optional[int],
        extra_args: Optional[List[str]],
        python3: Optional[str],
        keep_raw: bool,
) -> dict:
    # Each Bayesian iteration solves the current INP first, then converts the new ODB
    # into a queryable workspace for sensitivity/result extraction.
    # Once the workspace has been built, the Abaqus process files are no longer
    # needed for the Bayesian loop, so they are removed to keep the work
    # directory small across many iterations.
    resolved_job_name = _solver._sanitize_job_name(job_name or inp_path.stem)
    command = _solver._build_abaqus_command(
        abaqus=abaqus,
        inp_path=inp_path,
        job_name=resolved_job_name,
        cpus=cpus,
        interactive=interactive,
        extra_args=extra_args,
    )
    solver_result = _solver._run_local_solver(
        command=command,
        workdir=inp_path.parent,
        artifact_stem=resolved_job_name,
        artifact_suffixes=_solver._ABAQUS_ARTIFACT_SUFFIXES,
        timeout_sec=timeout_sec,
    )
    if not solver_result.get("ok"):
        raise ValidationError(
            "abaqus sensitivity rerun failed during bayesian update",
            {"iteration": iteration, "solver": solver_result},
        )

    odb_path = solver_result.get("artifacts", {}).get("odb")
    if not odb_path:
        raise ValidationError(
            "solver completed without producing an odb artifact",
            {"iteration": iteration, "solver": solver_result},
        )

    workspace_dir = (output_dir / f"workspace_iter{iteration}").resolve()
    workspace_info = _sens.build_workspace_from_odb(
        project_id=None,
        odb_path=odb_path,
        workspace=str(workspace_dir),
        abaqus=abaqus,
        python3=python3,
        keep_raw=keep_raw,
    )
    deleted_process_files = _cleanup_iteration_solver_files(inp_path.parent, resolved_job_name)
    solver_result["deleted_process_files"] = deleted_process_files
    return {
        "job_name": resolved_job_name,
        "command_preview": command,
        "solver": solver_result,
        "workspace": workspace_info,
    }


def _safe_float_or_none(value):
    if value is None:
        return None
    if isinstance(value, np.ndarray):
        if value.size == 0:
            return None
        return float(value.reshape(-1)[0])
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        return float(value[0])
    return float(value)


def _collect_workspace_static_displacement_rows(
        *,
        workspace: str,
        step: Optional[str],
        instances: Optional[List[str]],
        frame: int,
        aggregation: str,
) -> dict:
    workspace_abs = _sens._workspace_path(workspace)
    conn = _sens._manifest_conn(workspace_abs)
    try:
        step_rows = [
            dict(row)
            for row in conn.execute(
                "SELECT step_name, step_number FROM steps ORDER BY step_number, step_name"
            ).fetchall()
        ]
        chosen_step = step or _sens._default_step_from_rows(step_rows)
        if not chosen_step:
            available = [str(row.get("step_name")) for row in step_rows if row.get("step_name") is not None]
            raise ValidationError("step is required because multiple steps are available", {"available": available})

        available_instances = [
            str(row["instance_name"])
            for row in conn.execute(
                """
                SELECT DISTINCT instance_name
                FROM result_blocks
                WHERE step_name = ? AND field_name = 'U' AND position = 'NODAL'
                ORDER BY instance_name
                """,
                (chosen_step,),
            ).fetchall()
        ]
        if not available_instances:
            raise NotFoundError(
                "nodal displacement field 'U' not found in workspace",
                {"workspace": workspace_abs, "step": chosen_step},
            )

        if instances:
            chosen_instances = [str(item) for item in instances]
            missing = [item for item in chosen_instances if item not in available_instances]
            if missing:
                raise NotFoundError(
                    "some instances are not available in the selected workspace result",
                    {"missing_instances": missing, "available_instances": available_instances},
                )
        else:
            chosen_instances = available_instances

        part_name_map = {}
        try:
            for row in conn.execute(
                "SELECT instance_name, part_name FROM instances ORDER BY instance_name"
            ).fetchall():
                part_name_map[str(row["instance_name"])] = row["part_name"]
        except Exception:
            part_name_map = {}
    finally:
        conn.close()

    rows = []
    for instance_name in chosen_instances:
        comp_maps = {
            "U1": _sens._workspace_result_label_map(
                workspace_abs,
                step=chosen_step,
                field="U",
                instance=instance_name,
                position="NODAL",
                frame=resolved_frame,
                aggregation=aggregation,
                component="U1",
            ),
            "U2": _sens._workspace_result_label_map(
                workspace_abs,
                step=chosen_step,
                field="U",
                instance=instance_name,
                position="NODAL",
                frame=resolved_frame,
                aggregation=aggregation,
                component="U2",
            ),
            "U3": _sens._workspace_result_label_map(
                workspace_abs,
                step=chosen_step,
                field="U",
                instance=instance_name,
                position="NODAL",
                frame=resolved_frame,
                aggregation=aggregation,
                component="U3",
            ),
        }
        scoped_labels = sorted(
            set(comp_maps["U1"].keys()) | set(comp_maps["U2"].keys()) | set(comp_maps["U3"].keys()),
            key=lambda item: int(str(item).split("::", 1)[-1]),
        )
        for scoped_label in scoped_labels:
            label_text = str(scoped_label).split("::", 1)[-1]
            rows.append(
                {
                    "instance_name": instance_name,
                    "part_name": part_name_map.get(instance_name),
                    "fem_node_label": int(label_text),
                    "u1": _safe_float_or_none(comp_maps["U1"].get(scoped_label)),
                    "u2": _safe_float_or_none(comp_maps["U2"].get(scoped_label)),
                    "u3": _safe_float_or_none(comp_maps["U3"].get(scoped_label)),
                    "extra_json": {"step_name": chosen_step, "frame_idx": int(resolved_frame)},
                }
            )

    return {
        "workspace": workspace_abs,
        "step_name": chosen_step,
        "frame_idx": int(resolved_frame),
        "instances": chosen_instances,
        "rows": rows,
    }


def _persist_model_update_static_results(
        *,
        project_id: int,
        batch_no: int,
        static_payload: dict,
) -> dict:
    ensure_tables_exist()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM t_mt_py_fem_model_update_static_result WHERE pid = %s AND batch_no = %s",
            (int(project_id), str(batch_no)),
        )
        insert_sql = """
            INSERT INTO t_mt_py_fem_model_update_static_result
            (pid, batch_no, step_name, frame_idx, instance_name, part_name, fem_node_label, u1, u2, u3, extra_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                step_name = VALUES(step_name),
                part_name = VALUES(part_name),
                u1 = VALUES(u1),
                u2 = VALUES(u2),
                u3 = VALUES(u3),
                extra_json = VALUES(extra_json),
                created_at = CURRENT_TIMESTAMP
        """
        preview = []
        for row in static_payload["rows"]:
            cursor.execute(
                insert_sql,
                (
                    int(project_id),
                    str(batch_no),
                    static_payload.get("step_name"),
                    int(static_payload.get("frame_idx", 0)),
                    row.get("instance_name"),
                    row.get("part_name"),
                    int(row["fem_node_label"]),
                    row.get("u1"),
                    row.get("u2"),
                    row.get("u3"),
                    json.dumps(row.get("extra_json") or {}, ensure_ascii=False),
                ),
            )
            if len(preview) < 20:
                preview.append(
                    {
                        "instance_name": row.get("instance_name"),
                        "part_name": row.get("part_name"),
                        "fem_node_label": int(row["fem_node_label"]),
                        "u1": row.get("u1"),
                        "u2": row.get("u2"),
                        "u3": row.get("u3"),
                    }
                )
        conn.commit()
        return {
            "project_id": int(project_id),
            "batch_no": str(batch_no),
            "step_name": static_payload.get("step_name"),
            "frame_idx": int(static_payload.get("frame_idx", 0)),
            "row_count": len(static_payload["rows"]),
            "rows_preview": preview,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _persist_final_iteration_static_outputs(
        *,
        project_id: int,
        batch_no: int,
        workspace: Optional[str],
        step: Optional[str],
        instances: Optional[List[str]],
        frame: int,
        aggregation: str,
) -> dict:
    if not workspace:
        return {"skipped": True, "reason": "workspace_not_available"}

    static_payload = _collect_workspace_static_displacement_rows(
        workspace=workspace,
        step=step,
        instances=instances,
        frame=frame,
        aggregation=aggregation,
    )
    stored_result = _persist_model_update_static_results(
        project_id=project_id,
        batch_no=batch_no,
        static_payload=static_payload,
    )

    analysis_error_result = None
    try:
        analysis_error_result = _inp.store_updated_static_analysis_error(
            project_id=project_id,
            fem_rows=static_payload["rows"],
            components=["UX", "UY", "UZ"],
            include_rotations=False,
        )
    except Exception as exc:
        analysis_error_result = {
            "skipped": True,
            "reason": str(exc),
        }

    return {
        "static_result": stored_result,
        "analysis_error": analysis_error_result,
    }


def _normalize_modal_parameter_columns(parameter_columns: Sequence[dict]) -> List[dict]:
    rows: List[dict] = []
    for index, raw_row in enumerate(parameter_columns or [], start=1):
        row = dict(raw_row or {})
        parameter_name = str(
            row.get("parameter_name")
            or row.get("param_name")
            or row.get("field")
            or f"parameter_{index}"
        ).strip()
        row["parameter_name"] = parameter_name or f"parameter_{index}"
        row["parameter_value"] = row.get("parameter_value", row.get("initial_value", row.get("initial")))
        row["param_type"] = str(row.get("param_type") or row.get("parameter_type") or row.get("type") or "").upper()
        rows.append(row)
    return rows


def _parse_modal_mac_pair_from_name(response_name: Any) -> Tuple[Optional[int], Optional[int]]:
    token = str(response_name or "").strip().upper()
    match = re.search(r"FE(\d+)_TEST(\d+)", token)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def _build_modal_response_payload(
        *,
        project_id: int,
        stored_response_rows: Sequence[dict],
        matrix: Sequence[Sequence[float]],
        mac_threshold: float,
        max_freq_error_ratio: Optional[float],
        matching_method: str,
) -> dict:
    stored_rows = [dict(item or {}) for item in (stored_response_rows or [])]
    has_frequency_rows = any(
        str(item.get("response_type") or item.get("type") or "").upper() in {"FREQ", "MODAL_FREQUENCY"}
        for item in stored_rows
    )
    matched_payload = (
        _inp.match_modal_modes(
            int(project_id),
            mac_threshold=float(mac_threshold),
            max_freq_error_ratio=max_freq_error_ratio,
            method=str(matching_method or "greedy"),
        )
        if has_frequency_rows
        else {"rows": []}
    )
    matched_rows = list(matched_payload.get("rows") or [])
    matched_by_fem = {
        int(row["fem_mode_no"]): dict(row)
        for row in matched_rows
        if row.get("fem_mode_no") is not None
    }
    catalog_payload = _inp.get_fe_response_catalog(int(project_id))
    catalog_by_name = {
        str(row.get("response_name") or "").strip(): dict(row)
        for row in (catalog_payload.get("responses") or [])
        if str(row.get("response_name") or "").strip()
    }

    selected_indexes: List[int] = []
    response_rows: List[dict] = []
    target_values: List[float] = []
    model_values: List[float] = []
    skipped_rows: List[dict] = []

    for index, raw_row in enumerate(stored_rows):
        row = dict(raw_row or {})
        mode_number = row.get("mode_number")
        response_type = str(row.get("response_type") or row.get("type") or "").upper()
        response_name = str(row.get("response_name") or "").strip()
        if response_type in {"FREQ", "MODAL_FREQUENCY"}:
            if mode_number is None:
                skipped_rows.append(
                    {
                        "seq_no": row.get("seq_no"),
                        "response_name": row.get("response_name"),
                        "response_type": response_type or None,
                        "mode_number": mode_number,
                        "reason": "mode_number_missing",
                    }
                )
                continue
            matched = matched_by_fem.get(int(mode_number))
            if not matched:
                skipped_rows.append(
                    {
                        "seq_no": row.get("seq_no"),
                        "response_name": row.get("response_name"),
                        "response_type": response_type,
                        "mode_number": int(mode_number),
                        "reason": "mode_not_in_modal_match_result",
                    }
                )
                continue

            selected_indexes.append(index)
            response_name = response_name or f"FREQ_MODE_{int(mode_number)}"
            tracking_name = f"{response_name}@FE{int(matched['fem_mode_no'])}_TEST{int(matched['test_mode_no'])}"
            response_rows.append(
                {
                    **row,
                    "response_name": response_name,
                    "response_type": "FREQ",
                    "mode_number": int(mode_number),
                    "tracking_name": tracking_name,
                    "fem_mode_no": int(matched["fem_mode_no"]),
                    "test_mode_no": int(matched["test_mode_no"]),
                    "mac": matched.get("mac"),
                    "freq_error_ratio": matched.get("freq_error_ratio"),
                }
            )
            model_values.append(float(matched["freq_fem"]))
            target_values.append(float(matched["freq_test"]))
            continue

        if response_type != "MODAL_MAC":
            skipped_rows.append(
                {
                    "seq_no": row.get("seq_no"),
                    "response_name": response_name or None,
                    "response_type": response_type or None,
                    "mode_number": mode_number,
                    "reason": "response_is_not_supported_modal_type",
                }
            )
            continue

        catalog_row = dict(catalog_by_name.get(response_name) or {})
        if not catalog_row:
            skipped_rows.append(
                {
                    "seq_no": row.get("seq_no"),
                    "response_name": response_name or None,
                    "response_type": response_type,
                    "mode_number": int(mode_number) if mode_number is not None else None,
                    "reason": "response_not_found_in_formal_catalog",
                }
            )
            continue

        catalog_extra = dict(catalog_row.get("extra_json") or {})
        fem_mode_no = catalog_extra.get("fem_mode_no", mode_number)
        test_mode_no = catalog_extra.get("test_mode_no")
        if fem_mode_no is None or test_mode_no is None:
            parsed_fem_mode_no, parsed_test_mode_no = _parse_modal_mac_pair_from_name(response_name)
            if fem_mode_no is None:
                fem_mode_no = parsed_fem_mode_no
            if test_mode_no is None:
                test_mode_no = parsed_test_mode_no
        if fem_mode_no is None or test_mode_no is None:
            skipped_rows.append(
                {
                    "seq_no": row.get("seq_no"),
                    "response_name": response_name or None,
                    "response_type": response_type,
                    "reason": "mac_pair_not_resolved",
                }
            )
            continue

        from .modal_mac_service import compute_project_modal_mac

        mac_payload = compute_project_modal_mac(
            project_id=int(project_id),
            test_mode_no=int(test_mode_no),
            fem_mode_no=int(fem_mode_no),
            mac_scale=100.0,
        )
        target_value = catalog_extra.get("target_value", catalog_extra.get("mac_target", 100.0))
        selected_indexes.append(index)
        tracking_name = f"{response_name}@FE{int(fem_mode_no)}_TEST{int(test_mode_no)}"
        response_rows.append(
            {
                **row,
                "response_name": response_name,
                "response_type": "MODAL_MAC",
                "mode_number": int(fem_mode_no),
                "tracking_name": tracking_name,
                "fem_mode_no": int(fem_mode_no),
                "test_mode_no": int(test_mode_no),
                "mac": float(mac_payload["mac"]),
                "freq_error_ratio": catalog_extra.get("freq_error_ratio"),
            }
        )
        model_values.append(float(mac_payload["mac"]))
        target_values.append(float(target_value))

    if not response_rows:
        raise ValidationError(
            "no matched modal responses are available for modal bayesian update",
            {
                "project_id": int(project_id),
                "matched_pair_count": len(matched_rows),
                "stored_response_count": len(stored_rows),
                "skipped_preview": skipped_rows[:20],
            },
        )

    matrix_arr = np.asarray(matrix, dtype=np.float64)
    selected_matrix = matrix_arr[selected_indexes, :]
    return {
        "response_rows": response_rows,
        "model_values": model_values,
        "target_values": target_values,
        "matched_rows": matched_rows,
        "matched_payload": matched_payload,
        "skipped_rows": skipped_rows,
        "matrix": selected_matrix,
    }


def _select_modal_frequency_matrix_rows(
        *,
        stored_response_rows: Sequence[dict],
        matrix: Sequence[Sequence[float]],
        response_rows: Sequence[dict],
) -> np.ndarray:
    selected_indexes: List[int] = []
    expected_pairs = [
        (
            str(item.get("response_type") or item.get("type") or "").upper(),
            int(item.get("mode_number")),
        )
        for item in (response_rows or [])
        if item.get("mode_number") is not None
    ]
    stored_pairs = [
        (
            str(item.get("response_type") or item.get("type") or "").upper(),
            int(item.get("mode_number")),
        )
        if item.get("mode_number") is not None else None
        for item in (stored_response_rows or [])
    ]
    used_indexes: set[int] = set()
    for expected_type, expected_mode in expected_pairs:
        match_index = None
        for index, pair in enumerate(stored_pairs):
            if index in used_indexes or pair is None:
                continue
            if pair == (expected_type, expected_mode):
                match_index = index
                break
        if match_index is None:
            raise ValidationError(
                "failed to align modal frequency sensitivity rows with the selected response rows",
                {
                    "expected_response_type": expected_type,
                    "expected_mode_number": expected_mode,
                    "available_preview": [pair for pair in stored_pairs[:20] if pair is not None],
                },
            )
        used_indexes.add(match_index)
        selected_indexes.append(match_index)

    matrix_arr = np.asarray(matrix, dtype=np.float64)
    if matrix_arr.ndim != 2:
        raise ValidationError(
            "stored modal sensitivity matrix must be two-dimensional",
            {"matrix_shape": list(matrix_arr.shape)},
        )
    return np.asarray(matrix_arr[selected_indexes, :], dtype=np.float64)


def _metadata_bound_vector(
        parameter_columns: Sequence[dict],
        *,
        request_value: Any,
        label: str,
        key_candidates: Sequence[str],
        metadata_key: str,
        fallback: float,
) -> Optional[np.ndarray]:
    if request_value is not None:
        return np.asarray(
            _vector_from_input(
                request_value,
                parameter_columns,
                label=label,
                key_candidates=key_candidates,
            ),
            dtype=np.float64,
        )
    values = []
    has_metadata = False
    for item in parameter_columns:
        raw_value = dict(item or {}).get(metadata_key)
        if raw_value is None:
            values.append(float(fallback))
            continue
        has_metadata = True
        values.append(float(raw_value))
    if not has_metadata:
        return None
    return np.asarray(values, dtype=np.float64)


def _predict_updated_response_values(
        *,
        response_values: Sequence[float],
        parameter_values: Sequence[float],
        updated_parameter_values: Sequence[float],
        normalized_sensitivity: Sequence[Sequence[float]],
        eps: float = 1e-12,
) -> np.ndarray:
    r_model = np.asarray(response_values, dtype=np.float64).reshape(-1)
    p_current = np.asarray(parameter_values, dtype=np.float64).reshape(-1)
    p_new = np.asarray(updated_parameter_values, dtype=np.float64).reshape(-1)
    s_norm = np.asarray(normalized_sensitivity, dtype=np.float64)
    if s_norm.shape != (len(r_model), len(p_current)):
        raise ValidationError(
            "normalized sensitivity matrix shape does not match modal response and parameter sizes",
            {
                "matrix_shape": list(s_norm.shape),
                "response_count": len(r_model),
                "parameter_count": len(p_current),
            },
        )

    delta_p = p_new - p_current
    safe_param = np.where(np.abs(p_current) > float(eps), p_current, np.where(p_current >= 0.0, float(eps), -float(eps)))
    jacobian = s_norm * r_model.reshape(-1, 1) / safe_param.reshape(1, -1)
    predicted = r_model + jacobian @ delta_p
    return np.asarray(predicted, dtype=np.float64).reshape(-1)


def _normalize_absolute_modal_sensitivity_matrix(
        *,
        absolute_sensitivity: Sequence[Sequence[float]],
        response_values: Sequence[float],
        parameter_values: Sequence[float],
        eps: float = 1e-12,
) -> np.ndarray:
    sensitivity = np.asarray(absolute_sensitivity, dtype=np.float64)
    r_model = np.asarray(response_values, dtype=np.float64).reshape(-1)
    p_current = np.asarray(parameter_values, dtype=np.float64).reshape(-1)
    if sensitivity.ndim != 2:
        raise ValidationError(
            "absolute modal sensitivity matrix must be two-dimensional",
            {"matrix_shape": list(sensitivity.shape)},
        )
    if sensitivity.shape != (len(r_model), len(p_current)):
        raise ValidationError(
            "absolute modal sensitivity matrix shape does not match modal response and parameter sizes",
            {
                "matrix_shape": list(sensitivity.shape),
                "response_count": len(r_model),
                "parameter_count": len(p_current),
            },
        )

    safe_response = np.where(
        np.abs(r_model) > float(eps),
        r_model,
        np.where(r_model >= 0.0, float(eps), -float(eps)),
    )
    normalized = sensitivity * p_current.reshape(1, -1) / safe_response.reshape(-1, 1)
    return np.asarray(normalized, dtype=np.float64)


def _update_bdf_parameter_values(
        *,
        input_bdf: str,
        parameter_columns: Sequence[dict],
        updated_parameter_values: Sequence[float],
        output_bdf: str,
) -> dict:
    input_path = _solver._abs_file(input_bdf, "input_bdf")
    output_path = Path(output_bdf).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    model = BDF(debug=False)
    model.read_bdf(str(input_path), xref=False)

    updated_rows = []
    missing_rows = []
    for index, row in enumerate(parameter_columns or []):
        item = dict(row or {})
        parameter_name = str(item.get("parameter_name") or item.get("param_name") or f"parameter_{index + 1}")
        parameter_type = str(item.get("param_type") or item.get("parameter_type") or item.get("type") or "").upper()
        material_id = item.get("material_id")
        if material_id is None:
            material_id = item.get("source_material_id")
        if material_id is None:
            missing_rows.append({"parameter_name": parameter_name, "reason": "material_id_missing"})
            continue
        material = model.materials.get(int(material_id))
        if material is None:
            missing_rows.append({"parameter_name": parameter_name, "material_id": int(material_id), "reason": "material_not_found"})
            continue
        if str(getattr(material, "type", "")).upper() != "MAT1":
            missing_rows.append(
                {
                    "parameter_name": parameter_name,
                    "material_id": int(material_id),
                    "material_type": str(getattr(material, "type", "")),
                    "reason": "unsupported_material_type",
                }
            )
            continue

        value = float(updated_parameter_values[index])
        if parameter_type == "E":
            material.e = value
            if getattr(material, "g", None) is not None:
                material.g = None
        elif parameter_type == "RHO":
            material.rho = value
        else:
            missing_rows.append(
                {
                    "parameter_name": parameter_name,
                    "material_id": int(material_id),
                    "parameter_type": parameter_type,
                    "reason": "unsupported_parameter_type",
                }
            )
            continue
        updated_rows.append(
            {
                "parameter_name": parameter_name,
                "parameter_type": parameter_type,
                "material_id": int(material_id),
                "updated_value": value,
            }
        )

    if not updated_rows:
        raise ValidationError(
            "no modal-frequency parameters could be written back into the bdf file",
            {"input_bdf": str(input_path), "missing_preview": missing_rows[:20]},
        )

    model.write_bdf(str(output_path), interspersed=False)
    return {
        "input_bdf": str(input_path),
        "output_bdf": str(output_path),
        "updated_count": len(updated_rows),
        "updated_preview": updated_rows[:20],
        "skipped_preview": missing_rows[:20],
    }


def _persist_model_update_modal_results(
        *,
        project_id: int,
        batch_no: int,
        modal_rows: Sequence[dict],
) -> dict:
    ensure_tables_exist()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM t_mt_py_fem_model_update_modal_result WHERE pid = %s AND batch_no = %s",
            (int(project_id), str(batch_no)),
        )
        insert_sql = """
            INSERT INTO t_mt_py_fem_model_update_modal_result
            (pid, batch_no, response_name, response_type, fem_mode_no, test_mode_no,
             freq_fem_initial, freq_fem_updated, freq_test, initial_relative_error, updated_relative_error, mac, extra_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                response_name = VALUES(response_name),
                response_type = VALUES(response_type),
                freq_fem_initial = VALUES(freq_fem_initial),
                freq_fem_updated = VALUES(freq_fem_updated),
                freq_test = VALUES(freq_test),
                initial_relative_error = VALUES(initial_relative_error),
                updated_relative_error = VALUES(updated_relative_error),
                mac = VALUES(mac),
                extra_json = VALUES(extra_json),
                created_at = CURRENT_TIMESTAMP
        """
        preview = []
        for row in modal_rows:
            cursor.execute(
                insert_sql,
                (
                    int(project_id),
                    str(batch_no),
                    row.get("response_name"),
                    row.get("response_type"),
                    int(row["fem_mode_no"]),
                    int(row["test_mode_no"]),
                    float(row["freq_fem_initial"]),
                    float(row["freq_fem_updated"]),
                    float(row["freq_test"]),
                    row.get("initial_relative_error"),
                    row.get("updated_relative_error"),
                    row.get("mac"),
                    json.dumps(row.get("extra_json") or {}, ensure_ascii=False),
                ),
            )
            if len(preview) < 20:
                preview.append(
                    {
                        "response_name": row.get("response_name"),
                        "fem_mode_no": int(row["fem_mode_no"]),
                        "test_mode_no": int(row["test_mode_no"]),
                        "freq_fem_initial": float(row["freq_fem_initial"]),
                        "freq_fem_updated": float(row["freq_fem_updated"]),
                        "freq_test": float(row["freq_test"]),
                        "updated_relative_error": row.get("updated_relative_error"),
                    }
                )
        conn.commit()
        return {
            "project_id": int(project_id),
            "batch_no": str(batch_no),
            "row_count": len(list(modal_rows or [])),
            "rows_preview": preview,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _persist_final_iteration_modal_outputs(
        *,
        project_id: int,
        batch_no: int,
        response_rows: Sequence[dict],
        initial_response_values: Sequence[float],
        updated_response_values: Sequence[float],
) -> dict:
    modal_rows = []
    for index, row_meta in enumerate(response_rows or []):
        row = dict(row_meta or {})
        initial_value = float(initial_response_values[index])
        updated_value = float(updated_response_values[index])
        target_value = float(row.get("target_value"))
        response_type = str(row.get("response_type") or "FREQ").upper()
        modal_rows.append(
            {
                "response_name": str(row.get("response_name") or f"response_{index + 1}"),
                "response_type": response_type,
                "fem_mode_no": int(row["fem_mode_no"]),
                "test_mode_no": int(row["test_mode_no"]),
                "freq_fem_initial": initial_value,
                "freq_fem_updated": updated_value,
                "freq_test": target_value,
                "initial_relative_error": _response_difference_percent(initial_value, target_value),
                "updated_relative_error": _response_difference_percent(updated_value, target_value),
                "mac": (
                    float(updated_value)
                    if response_type == "MODAL_MAC"
                    else (float(row["mac"]) if row.get("mac") is not None else None)
                ),
                "extra_json": {
                    "mode_number": int(row.get("mode_number") or row.get("fem_mode_no")),
                    "tracking_name": row.get("tracking_name"),
                    "freq_error_ratio_before_update": row.get("freq_error_ratio"),
                    "target_value": target_value,
                    "response_type": response_type,
                },
            }
        )
    stored_result = _persist_model_update_modal_results(
        project_id=project_id,
        batch_no=batch_no,
        modal_rows=modal_rows,
    )
    return {
        "modal_result": stored_result,
    }


def run_bayesian_update_workflow(
        *,
        project_id: int,
        batch_no: int = 1,
        input_inp: str,
        target_responses: Any,
        parameter_scatter: Any = None,
        response_scatter: Any = None,
        output_dir: Optional[str] = None,
        save_results: bool = True,
        odb_id: Optional[str] = None,
        base_url: Optional[str] = None,
        workspace: Optional[str] = None,
        odb_path: Optional[str] = None,
        step: Optional[str] = None,
        instances: Optional[List[str]] = None,
        field_prefix: str = "d_U_",
        response_component: Optional[str] = None,
        position: Optional[str] = None,
        aggregation: str = "max_abs",
        frame: int = 0,
        iterations: int = 1,
        exit_diff_percent: Optional[float] = None,
        damping: float = 1e-8,
        step_scale: float = 1.0,
        lower_bound: Any = None,
        upper_bound: Any = None,
        abaqus: Optional[str] = None,
        python3: Optional[str] = None,
        keep_raw: bool = False,
        timeout: int = 60,
        job_name: Optional[str] = None,
        cpus: Optional[int] = None,
        interactive: bool = True,
        run_solver: bool = False,
        timeout_sec: Optional[int] = None,
        extra_args: Optional[List[str]] = None,
        write_cloud_result: bool = False,
        cloud_result_group: Optional[str] = None,
        cloud_step_name: str = "BayesianUpdate",
        cloud_field_name: str = "PARAMETER_CLOUD",
        cloud_value_mode: str = "updated_value",
        progress_callback: Optional[Callable[[dict], None]] = None,
) -> dict:
    if int(iterations) <= 0:
        raise ValidationError("iterations must be > 0", {"iterations": iterations})
    if exit_diff_percent is not None and float(exit_diff_percent) < 0:
        raise ValidationError("exit_diff_percent must be >= 0", {"exit_diff_percent": exit_diff_percent})
    resolved_batch_no = _normalize_batch_no(batch_no)
    cleanup_result = _clear_bayesian_run_outputs(
        project_id=int(project_id),
        batch_no=resolved_batch_no,
    )
    safe_write_console_event(
        int(project_id),
        "模型修正开始",
        [
            f"批次号: {resolved_batch_no}",
            "已先清空上一轮模型修正结果",
            f"清理表数: {len(cleanup_result.get('deleted') or {})}",
        ],
    )

    input_path = _solver._abs_file(input_inp, "input_inp")
    input_base_stem = input_path.stem
    cleanup_root_dir: Optional[Path] = None
    if save_results:
        root_dir = _solver._abs_dir(output_dir, input_path.parent / f"{input_path.stem}_bayesian")
    else:
        root_dir = Path(tempfile.mkdtemp(prefix=f"{input_path.stem}_bayesian_")).resolve()
        cleanup_root_dir = root_dir

    try:
        current_inp = _copy_iteration_input(str(input_path), root_dir, 0, base_stem=input_base_stem)

        has_initial_source = bool(workspace or odb_path or odb_id)
        if int(iterations) > 1 and not run_solver:
            raise ValidationError(
                "run_solver must be enabled when iterations > 1",
                {"iterations": iterations, "run_solver": run_solver},
            )
        if not has_initial_source and not run_solver:
            raise ValidationError(
                "workspace, odb_path, or odb_id is required when run_solver is disabled",
                {"workspace": workspace, "odb_path": odb_path, "odb_id": odb_id, "run_solver": run_solver},
            )

        iteration_results = []
        stopped_early = False
        last_resolved_workspace: Optional[str] = None
        cloud_export_odb_id = str(odb_id or "").strip() or None
        cloud_export_base_url = base_url
        next_source = {
            "workspace": _normalize_optional_path(workspace),
            "odb_path": _normalize_optional_path(odb_path),
            "odb_id": odb_id,
            "base_url": base_url,
        }

        for iteration_index in range(int(iterations)):
            if progress_callback is not None:
                progress_callback(
                    {
                        "phase": "iteration",
                        "current_iteration": int(iteration_index) + 1,
                        "total_iterations": int(iterations),
                    }
                )
            if iteration_index == 0 and not has_initial_source:
                solver_payload = _run_iteration_solver(
                    inp_path=current_inp,
                    output_dir=root_dir,
                    iteration=iteration_index,
                    abaqus=abaqus,
                    job_name=f"{job_name}_iter{iteration_index}" if job_name else None,
                    cpus=cpus,
                    interactive=interactive,
                    timeout_sec=timeout_sec,
                    extra_args=extra_args,
                    python3=python3,
                    keep_raw=keep_raw,
                )
                next_source = {
                    "workspace": solver_payload["workspace"]["workspace"],
                    "odb_path": None,
                    "odb_id": None,
                    "base_url": None,
                }
            else:
                solver_payload = None

            matrix_payload = build_dsa_normalized_sensitivity_matrix(
                project_id=project_id,
                odb_id=next_source.get("odb_id"),
                base_url=next_source.get("base_url"),
                inp_path=str(current_inp),
                workspace=next_source.get("workspace"),
                odb_path=next_source.get("odb_path"),
                workspace_root=str(root_dir),
                step=step,
                instances=instances,
                field_prefix=field_prefix,
                response_component=response_component,
                position=position,
                aggregation=aggregation,
                frame=frame,
                abaqus=abaqus,
                python3=python3,
                keep_raw=keep_raw,
                timeout=timeout,
            )
            last_resolved_workspace = str(matrix_payload.get("workspace") or "") or last_resolved_workspace
            cloud_export_odb_id = (
                str(matrix_payload.get("odb_id") or next_source.get("odb_id") or "").strip()
                or cloud_export_odb_id
            )
            cloud_export_base_url = (
                str(next_source.get("base_url") or "").strip()
                or cloud_export_base_url
            )

            parameter_columns = list(matrix_payload["parameter_columns"])
            response_rows = list(matrix_payload["response_rows"])
            S_norm = np.asarray(matrix_payload["matrix"], dtype=np.float64)
            p_current = np.asarray(matrix_payload["parameter_values"], dtype=np.float64)
            r_model = np.asarray(matrix_payload["response_values"], dtype=np.float64)
            r_target = np.asarray(
                _vector_from_input(
                    target_responses,
                    response_rows,
                    label="target_responses",
                    key_candidates=("row_key", "response_label"),
                ),
                dtype=np.float64,
            )
            p_scatter = _resolve_scatter_vector(
                parameter_scatter,
                parameter_columns,
                label="parameter_scatter",
                key_candidates=("parameter_name", "field", "parameter_token"),
                default_value=_sens._DEFAULT_PARAMETER_SCATTER,
                metadata_key="scatter",
            )
            r_scatter = _resolve_scatter_vector(
                response_scatter,
                response_rows,
                label="response_scatter",
                key_candidates=("row_key", "response_label"),
                default_value=_sens._DEFAULT_RESPONSE_SCATTER,
            )

            lower_bound_values = None
            if lower_bound is not None:
                lower_bound_values = np.asarray(
                    _vector_from_input(
                        lower_bound,
                        parameter_columns,
                        label="lower_bound",
                        key_candidates=("parameter_name", "field", "parameter_token"),
                    ),
                    dtype=np.float64,
                )

            upper_bound_values = None
            if upper_bound is not None:
                upper_bound_values = np.asarray(
                    _vector_from_input(
                        upper_bound,
                        parameter_columns,
                        label="upper_bound",
                        key_candidates=("parameter_name", "field", "parameter_token"),
                    ),
                    dtype=np.float64,
                )

            exit_check = _evaluate_exit_condition(
                r_model.tolist(),
                r_target.tolist(),
                exit_diff_percent=exit_diff_percent,
            )
            effective_step_scale = 0.0 if exit_check and bool(exit_check.get("converged")) else float(step_scale)
            update_payload = bayesian_update_normalized(
                p_current=p_current,
                r_model=r_model,
                r_target=r_target,
                S_norm=S_norm,
                p_scatter=p_scatter,
                r_scatter=r_scatter,
                damping=damping,
                step_scale=effective_step_scale,
                lower_bound=lower_bound_values,
                upper_bound=upper_bound_values,
                p_ref=p_current,
            )

            parameter_updates = {
                str(column["parameter_name"]): float(update_payload["p_new"][col_idx])
                for col_idx, column in enumerate(parameter_columns)
            }
            parameter_element_mapping = []
            for col_idx, column in enumerate(parameter_columns):
                mapping_entry = _clone_jsonable(column.get("element_mapping") or {})
                mapping_entry["updated_parameter_value"] = float(update_payload["p_new"][col_idx])
                parameter_element_mapping.append(mapping_entry)

            next_inp = _copy_iteration_input(
                str(current_inp),
                root_dir,
                iteration_index + 1,
                base_stem=input_base_stem,
            )
            update_parameter_section_values(
                str(next_inp),
                parameter_updates,
                output_inp=str(next_inp),
            )
            iteration_metrics = _build_iteration_metrics(
                response_values=matrix_payload["response_values"],
                target_values=r_target.tolist(),
                response_scatter=r_scatter.tolist(),
                parameter_step=update_payload["dp"],
            )

            iteration_result = {
                "iteration": iteration_index + 1,
                "input_inp": str(current_inp) if save_results else None,
                "source": {
                    "workspace": matrix_payload.get("workspace") if save_results else None,
                    "source_mode": matrix_payload.get("source_mode"),
                    "workspace_built": matrix_payload.get("workspace_built"),
                    "odb_id": matrix_payload.get("odb_id"),
                },
                "sensitivity_matrix": matrix_payload["matrix"],
                "response_values": matrix_payload["response_values"],
                "target_responses": r_target.tolist(),
                "parameter_values": matrix_payload["parameter_values"],
                "parameter_scatter": p_scatter.tolist(),
                "response_scatter": r_scatter.tolist(),
                "parameter_columns": parameter_columns,
                "parameter_element_mapping": parameter_element_mapping,
                "response_rows": response_rows,
                "bayesian": _clone_jsonable(update_payload),
                "metrics": _clone_jsonable(iteration_metrics),
                "exit_check": _clone_jsonable(exit_check),
                "updated_inp": str(next_inp) if save_results else None,
                "solver": solver_payload if save_results else None,
            }

            current_inp = next_inp
            next_source = {"workspace": None, "odb_path": None, "odb_id": None, "base_url": None}
            if exit_check and bool(exit_check.get("converged")):
                stopped_early = True
            elif iteration_index < int(iterations) - 1:
                rerun_payload = _run_iteration_solver(
                    inp_path=current_inp,
                    output_dir=root_dir,
                    iteration=iteration_index + 1,
                    abaqus=abaqus,
                    job_name=f"{job_name}_iter{iteration_index + 1}" if job_name else None,
                    cpus=cpus,
                    interactive=interactive,
                    timeout_sec=timeout_sec,
                    extra_args=extra_args,
                    python3=python3,
                    keep_raw=keep_raw,
                )
                if save_results:
                    iteration_result["next_iteration_solver"] = rerun_payload
                next_source["workspace"] = rerun_payload["workspace"]["workspace"]

            if save_results:
                iteration_result["saved_artifacts"] = _save_iteration_artifacts(root_dir, iteration_result)
            iteration_results.append(iteration_result)
            _write_bayesian_iteration_console_log(
                project_id=int(project_id),
                batch_no=resolved_batch_no,
                iteration_result=iteration_result,
                stopped_early=stopped_early,
            )
            _persist_bayesian_tracking_results(
                project_id=project_id,
                batch_no=resolved_batch_no,
                iteration_results=iteration_results,
            )
            if stopped_early:
                break

        final_iteration = iteration_results[-1]
        cloud_result = None
        if write_cloud_result:
            resolved_cloud_odb_id = cloud_export_odb_id or _resolve_loaded_odb_id_for_workspace(last_resolved_workspace)
            if not resolved_cloud_odb_id:
                raise ValidationError(
                    "cloud export via external-field api requires odb_id or a workspace already loaded in the L3 registry",
                    {
                        "project_id": project_id,
                        "batch_no": resolved_batch_no,
                        "odb_id": odb_id,
                        "workspace": last_resolved_workspace,
                    },
                )
            cloud_result = _write_bayesian_cloud_result(
                odb_id=resolved_cloud_odb_id,
                base_url=cloud_export_base_url,
                batch_no=resolved_batch_no,
                iteration_results=iteration_results,
                result_group=cloud_result_group,
                step_name=cloud_step_name,
                field_name=cloud_field_name,
                value_mode=cloud_value_mode,
                timeout=timeout,
            )
        saved_artifacts = (
            _save_bayesian_history_artifacts(
                root_dir=root_dir,
                project_id=project_id,
                batch_no=resolved_batch_no,
                iteration_results=iteration_results,
            )
            if save_results
            else {}
        )
        final_static_output = None
        try:
            final_static_output = _persist_final_iteration_static_outputs(
                project_id=project_id,
                batch_no=resolved_batch_no,
                workspace=last_resolved_workspace,
                step=step,
                instances=instances,
                frame=frame,
                aggregation=aggregation,
            )
        except Exception as exc:
            final_static_output = {
                "skipped": True,
                "reason": str(exc),
            }
        try:
            update_work_condition_project_status(
                int(project_id),
                fixes_cal_status=1,
                fixes_result_status=1,
            )
        except Exception:
            pass
        result = {
            "project_id": project_id,
            "batch_no": resolved_batch_no,
            "cleanup_result": cleanup_result,
            "input_inp": str(input_path),
            "output_dir": str(root_dir) if save_results else None,
            "save_results": bool(save_results),
            "iterations": len(iteration_results),
            "requested_iterations": int(iterations),
            "stopped_early": bool(stopped_early),
            "exit_diff_percent": None if exit_diff_percent is None else float(exit_diff_percent),
            "field_prefix": field_prefix,
            "response_component": response_component,
            "step": step,
            "instances": [str(item) for item in (instances or [])],
            "final_updated_inp": final_iteration["updated_inp"] if save_results else None,
            "final_parameter_values": final_iteration["bayesian"]["p_new"],
            "parameter_columns": final_iteration["parameter_columns"],
            "response_rows": final_iteration["response_rows"],
            "iteration_results": iteration_results,
            "saved_artifacts": saved_artifacts,
            "cloud_result": cloud_result,
            "final_static_output": final_static_output,
        }
        safe_write_console_event(
            int(project_id),
            "模型修正完成",
            [
                f"批次号: {resolved_batch_no}",
                f"迭代次数: {len(iteration_results)}",
                f"提前终止: {'是' if stopped_early else '否'}",
                f"输出目录: {str(root_dir) if save_results else '-'}",
            ],
        )
        return result
    finally:
        if cleanup_root_dir is not None:
            shutil.rmtree(cleanup_root_dir, ignore_errors=True)


def run_modal_frequency_bayesian_update_workflow(
        *,
        project_id: int,
        batch_no: int = 1,
        sensitivity_batch_no: Optional[int] = None,
        input_bdf: Optional[str] = None,
        parameter_scatter: Any = None,
        response_scatter: Any = None,
        output_dir: Optional[str] = None,
        save_results: bool = True,
        iterations: int = 1,
        exit_diff_percent: Optional[float] = None,
        damping: float = 1e-8,
        step_scale: float = 1.0,
        lower_bound: Any = None,
        upper_bound: Any = None,
        mac_threshold: float = 0.7,
        max_freq_error_ratio: Optional[float] = 0.2,
        matching_method: str = "greedy",
        progress_callback: Optional[Callable[[dict], None]] = None,
) -> dict:
    resolved_batch_no = _normalize_batch_no(batch_no)
    resolved_sensitivity_batch_no = _normalize_batch_no(sensitivity_batch_no or batch_no)
    if int(iterations) <= 0:
        raise ValidationError("iterations must be > 0", {"iterations": iterations})

    stored_payload = _sens._load_stored_sensitivity_run(
        project_id=int(project_id),
        batch_no=resolved_sensitivity_batch_no,
    )
    parameter_columns = _normalize_modal_parameter_columns(stored_payload.get("parameter_columns") or [])
    if not parameter_columns:
        raise ValidationError(
            "stored modal sensitivity run does not contain any parameters",
            {"project_id": int(project_id), "batch_no": resolved_sensitivity_batch_no},
        )

    modal_payload = _build_modal_response_payload(
        project_id=int(project_id),
        stored_response_rows=stored_payload.get("response_rows") or [],
        matrix=stored_payload.get("matrix") or [],
        mac_threshold=float(mac_threshold),
        max_freq_error_ratio=max_freq_error_ratio,
        matching_method=str(matching_method or "greedy"),
    )
    response_rows = [dict(row) for row in (modal_payload.get("response_rows") or [])]
    for index, row in enumerate(response_rows):
        row["target_value"] = float(modal_payload["target_values"][index])

    input_bdf_path = _normalize_optional_path(input_bdf)
    input_base_stem = Path(input_bdf_path).stem if input_bdf_path else f"project_{int(project_id)}_modal"
    cleanup_result = _clear_bayesian_run_outputs(
        project_id=int(project_id),
        batch_no=resolved_batch_no,
    )

    safe_write_console_event(
        int(project_id),
        "模态频率模型修正开始",
        [
            f"批次号: {resolved_batch_no}",
            f"灵敏度批次: {resolved_sensitivity_batch_no}",
            f"匹配模态对数: {len(response_rows)}",
            "已先清空上一轮模型修正结果",
        ],
    )

    root_dir: Path
    cleanup_root_dir: Optional[Path] = None
    if save_results:
        root_dir = _solver._abs_dir(output_dir, Path(tempfile.gettempdir()) / f"{input_base_stem}_modal_bayesian")
    else:
        root_dir = Path(tempfile.mkdtemp(prefix=f"{input_base_stem}_modal_bayesian_")).resolve()
        cleanup_root_dir = root_dir

    try:
        raw_parameter_values = [row.get("parameter_value") for row in parameter_columns]
        if any(value is None for value in raw_parameter_values):
            raise ValidationError(
                "stored parameter initial values are required for modal bayesian update",
                {
                    "project_id": int(project_id),
                    "batch_no": resolved_sensitivity_batch_no,
                    "parameter_preview": parameter_columns[:10],
                },
            )
        current_parameter_values = np.asarray([float(value) for value in raw_parameter_values], dtype=np.float64)
        if any(not np.isfinite(value) for value in current_parameter_values.tolist()):
            raise ValidationError(
                "stored parameter initial values are required for modal bayesian update",
                {
                    "project_id": int(project_id),
                    "batch_no": resolved_sensitivity_batch_no,
                    "parameter_preview": parameter_columns[:10],
                },
            )

        normalized_matrix = np.asarray(modal_payload["matrix"], dtype=np.float64)
        current_response_values = np.asarray(modal_payload["model_values"], dtype=np.float64)
        initial_response_values = current_response_values.copy()
        target_response_values = np.asarray(modal_payload["target_values"], dtype=np.float64)

        p_scatter = _resolve_scatter_vector(
            parameter_scatter,
            parameter_columns,
            label="parameter_scatter",
            key_candidates=("parameter_name", "param_name", "param_code"),
            default_value=_sens._DEFAULT_PARAMETER_SCATTER,
        )
        r_scatter = _resolve_scatter_vector(
            response_scatter,
            response_rows,
            label="response_scatter",
            key_candidates=("tracking_name", "response_name", "response_code"),
            default_value=_sens._DEFAULT_RESPONSE_SCATTER,
        )
        lower_bound_values = _metadata_bound_vector(
            parameter_columns,
            request_value=lower_bound,
            label="lower_bound",
            key_candidates=("parameter_name", "param_name", "param_code"),
            metadata_key="lower_bound",
            fallback=-float("inf"),
        )
        upper_bound_values = _metadata_bound_vector(
            parameter_columns,
            request_value=upper_bound,
            label="upper_bound",
            key_candidates=("parameter_name", "param_name", "param_code"),
            metadata_key="upper_bound",
            fallback=float("inf"),
        )

        iteration_results = []
        stopped_early = False
        current_bdf_path = input_bdf_path

        for iteration_index in range(int(iterations)):
            if progress_callback is not None:
                progress_callback(
                    {
                        "phase": "iteration",
                        "current_iteration": int(iteration_index) + 1,
                        "total_iterations": int(iterations),
                    }
                )

            exit_check = _evaluate_exit_condition(
                current_response_values.tolist(),
                target_response_values.tolist(),
                exit_diff_percent=exit_diff_percent,
            )
            effective_step_scale = 0.0 if exit_check and bool(exit_check.get("converged")) else float(step_scale)
            update_payload = bayesian_update_normalized(
                p_current=current_parameter_values,
                r_model=current_response_values,
                r_target=target_response_values,
                S_norm=normalized_matrix,
                p_scatter=p_scatter,
                r_scatter=r_scatter,
                damping=damping,
                step_scale=effective_step_scale,
                lower_bound=lower_bound_values,
                upper_bound=upper_bound_values,
                p_ref=current_parameter_values,
            )

            updated_response_values = _predict_updated_response_values(
                response_values=current_response_values,
                parameter_values=current_parameter_values,
                updated_parameter_values=update_payload["p_new"],
                normalized_sensitivity=normalized_matrix,
            )
            iteration_metrics = _build_iteration_metrics(
                response_values=updated_response_values.tolist(),
                target_values=target_response_values.tolist(),
                response_scatter=r_scatter.tolist(),
                parameter_step=update_payload["dp"],
            )

            updated_bdf_result = None
            updated_bdf_path = None
            if current_bdf_path:
                iteration_dir = _iteration_dir(root_dir, iteration_index + 1)
                updated_bdf_path = iteration_dir / Path(current_bdf_path).name
                updated_bdf_result = _update_bdf_parameter_values(
                    input_bdf=current_bdf_path,
                    parameter_columns=parameter_columns,
                    updated_parameter_values=update_payload["p_new"],
                    output_bdf=str(updated_bdf_path),
                )

            iteration_result = {
                "iteration": iteration_index + 1,
                "input_bdf": str(current_bdf_path) if current_bdf_path else None,
                "sensitivity_batch_no": str(resolved_sensitivity_batch_no),
                "response_values_before_update": current_response_values.tolist(),
                "response_values": updated_response_values.tolist(),
                "target_responses": target_response_values.tolist(),
                "parameter_values": current_parameter_values.tolist(),
                "parameter_scatter": p_scatter.tolist(),
                "response_scatter": r_scatter.tolist(),
                "parameter_columns": parameter_columns,
                "parameter_element_mapping": [],
                "response_rows": response_rows,
                "sensitivity_matrix": normalized_matrix.tolist(),
                "bayesian": _clone_jsonable(update_payload),
                "metrics": _clone_jsonable(iteration_metrics),
                "exit_check": _clone_jsonable(exit_check),
                "updated_bdf": str(updated_bdf_path) if updated_bdf_path else None,
                "updated_bdf_result": updated_bdf_result,
            }
            if save_results:
                iteration_result["saved_artifacts"] = _save_iteration_artifacts(root_dir, iteration_result)
            iteration_results.append(iteration_result)

            if exit_check and bool(exit_check.get("converged")):
                stopped_early = True

            _write_bayesian_iteration_console_log(
                project_id=int(project_id),
                batch_no=resolved_batch_no,
                iteration_result=iteration_result,
                stopped_early=stopped_early,
            )
            _persist_bayesian_tracking_results(
                project_id=project_id,
                batch_no=resolved_batch_no,
                iteration_results=iteration_results,
            )

            if stopped_early:
                break

            current_parameter_values = np.asarray(update_payload["p_new"], dtype=np.float64)
            current_response_values = np.asarray(updated_response_values, dtype=np.float64)
            if updated_bdf_path is not None:
                current_bdf_path = str(updated_bdf_path)

        final_iteration = iteration_results[-1]
        saved_artifacts = (
            _save_bayesian_history_artifacts(
                root_dir=root_dir,
                project_id=project_id,
                batch_no=resolved_batch_no,
                iteration_results=iteration_results,
            )
            if save_results
            else {}
        )
        final_modal_output = _persist_final_iteration_modal_outputs(
            project_id=project_id,
            batch_no=resolved_batch_no,
            response_rows=response_rows,
            initial_response_values=initial_response_values.tolist(),
            updated_response_values=final_iteration["response_values"],
        )
        matched_payload = modal_payload.get("matched_payload") or {}

        try:
            update_work_condition_project_status(
                int(project_id),
                fixes_cal_status=1,
                fixes_result_status=1,
            )
        except Exception:
            pass
        result = {
            "project_id": int(project_id),
            "batch_no": str(resolved_batch_no),
            "sensitivity_batch_no": str(resolved_sensitivity_batch_no),
            "cleanup_result": cleanup_result,
            "input_bdf": input_bdf_path,
            "output_dir": str(root_dir) if save_results else None,
            "save_results": bool(save_results),
            "iterations": len(iteration_results),
            "requested_iterations": int(iterations),
            "stopped_early": bool(stopped_early),
            "exit_diff_percent": None if exit_diff_percent is None else float(exit_diff_percent),
            "final_updated_bdf": final_iteration.get("updated_bdf"),
            "final_parameter_values": final_iteration["bayesian"]["p_new"],
            "parameter_columns": parameter_columns,
            "response_rows": response_rows,
            "matched_modal_rows": matched_payload.get("rows") or [],
            "matched_pair_count": len(response_rows),
            "iteration_results": iteration_results,
            "saved_artifacts": saved_artifacts,
            "final_modal_output": final_modal_output,
            "skipped_response_rows_preview": modal_payload.get("skipped_rows", [])[:20],
        }
        safe_write_console_event(
            int(project_id),
            "模态频率模型修正完成",
            [
                f"批次号: {resolved_batch_no}",
                f"灵敏度批次: {resolved_sensitivity_batch_no}",
                f"迭代次数: {len(iteration_results)}",
                f"提前终止: {'是' if stopped_early else '否'}",
                f"输出目录: {str(root_dir) if save_results else '-'}",
            ],
        )
        return result
    finally:
        if cleanup_root_dir is not None:
            shutil.rmtree(cleanup_root_dir, ignore_errors=True)


def run_sol200_modal_frequency_bayesian_update_workflow(
        *,
        project_id: int,
        batch_no: int = 1,
        sensitivity_batch_no: Optional[int] = None,
        input_bdf: Optional[str] = None,
        parameter_scatter: Any = None,
        response_scatter: Any = None,
        output_dir: Optional[str] = None,
        save_results: bool = False,
        iterations: int = 1,
        exit_diff_percent: Optional[float] = None,
        damping: float = 1e-8,
        step_scale: float = 1.0,
        lower_bound: Any = None,
        upper_bound: Any = None,
        mac_threshold: float = 0.7,
        max_freq_error_ratio: Optional[float] = 0.2,
        matching_method: str = "greedy",
        settings: Optional[Dict[str, Any]] = None,
        nastran: Optional[str] = None,
        timeout_sec: Optional[int] = None,
        extra_args: Optional[List[str]] = None,
        write_cloud_result: bool = True,
        cloud_result_group: Optional[str] = None,
        cloud_step_name: str = "BayesianUpdate",
        cloud_field_name: str = "PARAMETER_RELATIVE_DELTA_PERCENT",
        progress_callback: Optional[Callable[[dict], None]] = None,
) -> dict:
    from services.model_update.analysis.nastran_sol200_service import run_sol200_and_store_workflow
    from services.model_update.analysis.project_path_service import resolve_project_workspace
    from services.model_update.importers.op2_service import _build_op2_parameter_columns_with_mappings
    from services.model_update.solver_prep.nastran_sol103 import extract_sol103_settings_from_bdf

    resolved_batch_no = _normalize_batch_no(batch_no)
    resolved_sensitivity_batch_no = _normalize_batch_no(sensitivity_batch_no or batch_no)
    if int(iterations) <= 0:
        raise ValidationError("iterations must be > 0", {"iterations": iterations})
    if exit_diff_percent is not None and float(exit_diff_percent) < 0:
        raise ValidationError("exit_diff_percent must be >= 0", {"exit_diff_percent": exit_diff_percent})

    cleanup_result = _clear_bayesian_run_outputs(
        project_id=int(project_id),
        batch_no=resolved_batch_no,
    )

    stored_payload = _sens._load_stored_sensitivity_run(
        project_id=int(project_id),
        batch_no=str(resolved_sensitivity_batch_no),
    )
    current_parameter_columns = _normalize_modal_parameter_columns(stored_payload.get("parameter_columns") or [])
    if not current_parameter_columns:
        raise ValidationError(
            "stored SOL200 sensitivity run does not contain any parameters",
            {"project_id": int(project_id), "batch_no": resolved_sensitivity_batch_no},
        )

    modal_payload = _build_modal_response_payload(
        project_id=int(project_id),
        stored_response_rows=stored_payload.get("response_rows") or [],
        matrix=stored_payload.get("matrix") or [],
        mac_threshold=float(mac_threshold),
        max_freq_error_ratio=max_freq_error_ratio,
        matching_method=str(matching_method or "greedy"),
    )
    response_rows = [dict(row) for row in (modal_payload.get("response_rows") or [])]
    for index, row in enumerate(response_rows):
        row["target_value"] = float(modal_payload["target_values"][index])
    if not response_rows:
        raise ValidationError(
            "no matched modal frequency responses are available for SOL200 Bayesian update",
            {"project_id": int(project_id), "batch_no": resolved_sensitivity_batch_no},
        )

    source = dict(stored_payload.get("source") or {})
    metadata_source_bdf = None
    metadata_localized_bdf = None
    metadata_path = _normalize_optional_path(source.get("metadata_path"))
    if metadata_path and os.path.exists(metadata_path):
        try:
            metadata_json = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
            metadata_localized_bdf = metadata_json.get("localized_input_bdf")
            metadata_source_bdf = metadata_json.get("source_input_bdf")
        except Exception:
            metadata_localized_bdf = None
            metadata_source_bdf = None
    # modal_reference_bdf_path:
    # keeps the caller's original modal-control intent (especially EIGRL),
    # but is not used as the writable model for parameter updates.
    modal_reference_bdf_path = (
        _normalize_optional_path(input_bdf)
        or _normalize_optional_path(metadata_source_bdf)
        or _normalize_optional_path(metadata_localized_bdf)
        or _normalize_optional_path(source.get("bdf_path"))
    )
    # update_source_bdf_path:
    # the actual writable/updateable model used by Bayesian iterations.
    # For all_elements_e this should prefer the localized BDF produced during
    # SOL200 sensitivity preparation so material/property IDs remain consistent.
    update_source_bdf_path = (
        _normalize_optional_path(metadata_localized_bdf)
        or _normalize_optional_path(source.get("bdf_path"))
        or _normalize_optional_path(input_bdf)
    )
    if not update_source_bdf_path:
        raise ValidationError(
            "SOL200 Bayesian update requires an input_bdf or a stored sensitivity source bdf_path",
            {"project_id": int(project_id), "batch_no": resolved_sensitivity_batch_no},
        )

    root_dir: Path
    cleanup_root_dir: Optional[Path] = None
    input_base_stem = Path(update_source_bdf_path).stem
    if save_results:
        root_dir = _solver._abs_dir(output_dir, Path(tempfile.gettempdir()) / f"{input_base_stem}_sol200_bayesian")
    else:
        root_dir = Path(tempfile.mkdtemp(prefix=f"{input_base_stem}_sol200_bayesian_")).resolve()
        cleanup_root_dir = root_dir

    settings_payload = dict(settings or {})
    settings_payload.setdefault("sol200.deck_mode", "include")
    settings_payload.setdefault("sol200.sensitivity_csv", True)
    settings_payload.setdefault("result.target", "F06")
    settings_payload.setdefault("post", -1)
    sol103_settings = dict(settings or {})
    if modal_reference_bdf_path:
        # Reuse EIGRL from the caller-provided/reference BDF unless the API
        # explicitly overrides dynamic.* settings.
        for key, value in extract_sol103_settings_from_bdf(str(modal_reference_bdf_path)).items():
            sol103_settings.setdefault(key, value)
    sol103_settings.setdefault("result.target", "OP2")
    sol103_settings.setdefault("post", -1)

    safe_write_console_event(
        int(project_id),
        "SOL200 模态频率模型修正开始",
        [
            f"批次号: {resolved_batch_no}",
            f"灵敏度批次: {resolved_sensitivity_batch_no}",
            f"匹配模态对数: {len(response_rows)}",
            "已先清空上一轮模型修正结果",
        ],
    )

    try:
        current_parameter_values = np.asarray(
            [float(row.get("parameter_value")) for row in current_parameter_columns],
            dtype=np.float64,
        )
        initial_parameter_values = current_parameter_values.copy()
        modal_matrix = modal_payload.get("matrix")
        initial_modal_run = _run_sol103_modal_response_values(
            input_bdf=str(update_source_bdf_path),
            response_rows=response_rows,
            output_bdf=str(root_dir / f"{input_base_stem}_initial_sol103.bdf"),
            settings=sol103_settings,
            nastran=nastran,
            timeout_sec=timeout_sec,
            extra_args=extra_args,
        )
        current_response_values = np.asarray(initial_modal_run["response_values"], dtype=np.float64)
        initial_response_values = current_response_values.copy()
        target_response_values = np.asarray(modal_payload["target_values"], dtype=np.float64)
        if modal_matrix is None:
            normalized_matrix = np.asarray([], dtype=np.float64)
        else:
            normalized_matrix = _normalize_absolute_modal_sensitivity_matrix(
                absolute_sensitivity=modal_matrix,
                response_values=current_response_values,
                parameter_values=current_parameter_values,
            )

        p_scatter = _resolve_scatter_vector(
            parameter_scatter,
            current_parameter_columns,
            label="parameter_scatter",
            key_candidates=("parameter_name", "param_name", "param_code"),
            default_value=_sens._DEFAULT_PARAMETER_SCATTER,
        )
        r_scatter = _resolve_scatter_vector(
            response_scatter,
            response_rows,
            label="response_scatter",
            key_candidates=("tracking_name", "response_name", "response_code"),
            default_value=_sens._DEFAULT_RESPONSE_SCATTER,
        )
        lower_bound_values = _metadata_bound_vector(
            current_parameter_columns,
            request_value=lower_bound,
            label="lower_bound",
            key_candidates=("parameter_name", "param_name", "param_code"),
            metadata_key="lower_bound",
            fallback=-float("inf"),
        )
        upper_bound_values = _metadata_bound_vector(
            current_parameter_columns,
            request_value=upper_bound,
            label="upper_bound",
            key_candidates=("parameter_name", "param_name", "param_code"),
            metadata_key="upper_bound",
            fallback=float("inf"),
        )

        current_bdf_path = str(update_source_bdf_path)
        iteration_results = []
        stopped_early = False
        final_sensitivity_payload = stored_payload
        final_output_bdf = None
        cloud_export_base_url: Optional[str] = None

        for iteration_index in range(int(iterations)):
            if progress_callback is not None:
                progress_callback(
                    {
                        "phase": "iteration",
                        "current_iteration": int(iteration_index) + 1,
                        "total_iterations": int(iterations),
                    }
                )

            update_payload = bayesian_update_normalized(
                p_current=current_parameter_values,
                r_model=current_response_values,
                r_target=target_response_values,
                S_norm=normalized_matrix,
                p_scatter=p_scatter,
                r_scatter=r_scatter,
                damping=damping,
                step_scale=float(step_scale),
                lower_bound=lower_bound_values,
                upper_bound=upper_bound_values,
                p_ref=current_parameter_values,
            )

            iteration_dir = _iteration_dir(root_dir, iteration_index + 1)
            updated_bdf_path = iteration_dir / Path(current_bdf_path).name
            updated_bdf_result = _update_bdf_parameter_values(
                input_bdf=current_bdf_path,
                parameter_columns=current_parameter_columns,
                updated_parameter_values=update_payload["p_new"],
                output_bdf=str(updated_bdf_path),
            )
            sol103_output_bdf = iteration_dir / f"{updated_bdf_path.stem}_sol103_iter{iteration_index + 1}.bdf"
            modal_run = _run_sol103_modal_response_values(
                input_bdf=str(updated_bdf_path),
                response_rows=response_rows,
                output_bdf=str(sol103_output_bdf),
                settings=sol103_settings,
                nastran=nastran,
                timeout_sec=timeout_sec,
                extra_args=extra_args,
            )
            sol200_output_bdf = iteration_dir / f"{updated_bdf_path.stem}_sol200_iter{iteration_index + 1}.bdf"
            sensitivity_run_no = f"{resolved_batch_no}_iter_{iteration_index + 1}"
            rerun_payload = run_sol200_and_store_workflow(
                project_id=int(project_id),
                batch_no=str(sensitivity_run_no),
                case_name=f"sol200_modal_bayesian_iter_{iteration_index + 1}",
                input_bdf=str(updated_bdf_path),
                output_bdf=str(sol200_output_bdf),
                parameters=_build_sol200_parameter_rows(current_parameter_columns, update_payload["p_new"]),
                responses=_build_sol200_response_rows(response_rows),
                settings=settings_payload,
                nastran=nastran,
                run_solver=True,
                timeout_sec=timeout_sec,
                extra_args=list(extra_args or []),
                write_cloud_result=False,
            )

            rerun_stored_payload = _sens._load_stored_sensitivity_run(
                project_id=int(project_id),
                batch_no=str(sensitivity_run_no),
            )
            updated_response_values = np.asarray(modal_run["response_values"], dtype=np.float64)
            rerun_matrix = rerun_stored_payload.get("matrix")
            if rerun_matrix is None:
                normalized_matrix = np.asarray([], dtype=np.float64)
            else:
                absolute_matrix = _select_modal_frequency_matrix_rows(
                    stored_response_rows=rerun_stored_payload.get("response_rows") or [],
                    matrix=rerun_matrix,
                    response_rows=response_rows,
                )
                normalized_matrix = _normalize_absolute_modal_sensitivity_matrix(
                    absolute_sensitivity=absolute_matrix,
                    response_values=updated_response_values,
                    parameter_values=np.asarray(update_payload["p_new"], dtype=np.float64),
                )
            rerun_parameter_columns = _normalize_modal_parameter_columns(rerun_stored_payload.get("parameter_columns") or [])
            workspace_path = _sens._workspace_path(resolve_project_workspace(int(project_id)))
            parameter_mappings = _build_op2_parameter_columns_with_mappings(
                workspace=workspace_path,
                bdf_path=str(updated_bdf_path),
                parameter_columns=rerun_parameter_columns,
            )

            iteration_metrics = _build_iteration_metrics(
                response_values=updated_response_values.tolist(),
                target_values=target_response_values.tolist(),
                response_scatter=r_scatter.tolist(),
                parameter_step=update_payload["dp"],
            )
            exit_check = _evaluate_exit_condition(
                updated_response_values.tolist(),
                target_response_values.tolist(),
                exit_diff_percent=exit_diff_percent,
            )
            iteration_result = {
                "iteration": iteration_index + 1,
                "analysis_run_id": rerun_stored_payload.get("analysis_run_id"),
                "input_bdf": str(current_bdf_path) if save_results else None,
                "sensitivity_batch_no": str(sensitivity_run_no),
                "response_values_before_update": current_response_values.tolist(),
                "response_values": updated_response_values.tolist(),
                "target_responses": target_response_values.tolist(),
                "parameter_values": current_parameter_values.tolist(),
                "parameter_scatter": p_scatter.tolist(),
                "response_scatter": r_scatter.tolist(),
                "parameter_columns": rerun_parameter_columns,
                "parameter_element_mapping": [
                    {
                        **dict(item),
                        "updated_parameter_value": float(update_payload["p_new"][index]),
                        "parameter_value": float(initial_parameter_values[index]),
                    }
                    for index, item in enumerate(parameter_mappings)
                ],
                "response_rows": response_rows,
                "sensitivity_matrix": normalized_matrix.tolist(),
                "bayesian": _clone_jsonable(update_payload),
                "metrics": _clone_jsonable(iteration_metrics),
                "exit_check": _clone_jsonable(exit_check),
                "updated_bdf": str(updated_bdf_path) if save_results else None,
                "updated_bdf_result": updated_bdf_result if save_results else None,
                "modal_solver": modal_run["solver_payload"] if save_results else None,
                "solver": rerun_payload if save_results else None,
            }
            if save_results:
                iteration_result["saved_artifacts"] = _save_iteration_artifacts(root_dir, iteration_result)
            iteration_results.append(iteration_result)

            _write_bayesian_iteration_console_log(
                project_id=int(project_id),
                batch_no=resolved_batch_no,
                iteration_result=iteration_result,
                stopped_early=bool(exit_check.get("converged")),
            )
            _persist_bayesian_tracking_results(
                project_id=project_id,
                batch_no=resolved_batch_no,
                iteration_results=iteration_results,
            )

            current_parameter_values = np.asarray(update_payload["p_new"], dtype=np.float64)
            current_parameter_columns = rerun_parameter_columns
            current_response_values = np.asarray(updated_response_values, dtype=np.float64)
            current_bdf_path = str(updated_bdf_path)
            final_output_bdf = str(updated_bdf_path) if save_results else None
            final_sensitivity_payload = rerun_stored_payload
            cloud_export_base_url = cloud_export_base_url or None
            stopped_early = bool(exit_check.get("converged"))
            if stopped_early:
                break

        final_iteration = iteration_results[-1]
        saved_artifacts = (
            _save_bayesian_history_artifacts(
                root_dir=root_dir,
                project_id=project_id,
                batch_no=resolved_batch_no,
                iteration_results=iteration_results,
            )
            if save_results
            else {}
        )
        final_modal_output = _persist_final_iteration_modal_outputs(
            project_id=project_id,
            batch_no=resolved_batch_no,
            response_rows=response_rows,
            initial_response_values=initial_response_values.tolist(),
            updated_response_values=current_response_values.tolist(),
        )
        cloud_result = None
        if write_cloud_result:
            workspace_path = _sens._workspace_path(resolve_project_workspace(int(project_id)))
            resolved_cloud_odb_id = _resolve_loaded_odb_id_for_workspace(workspace_path)
            if not resolved_cloud_odb_id:
                raise ValidationError(
                    "cloud export via external-field api requires the project workspace to be loaded in the L3 registry",
                    {"project_id": int(project_id), "workspace": workspace_path},
                )
            parameter_mappings = _build_op2_parameter_columns_with_mappings(
                workspace=workspace_path,
                bdf_path=str(current_bdf_path),
                parameter_columns=current_parameter_columns,
            )
            cloud_result = _write_sol200_final_parameter_cloud_result(
                odb_id=resolved_cloud_odb_id,
                base_url=cloud_export_base_url,
                batch_no=resolved_batch_no,
                parameter_columns=current_parameter_columns,
                parameter_mappings=parameter_mappings,
                initial_parameter_values=initial_parameter_values.tolist(),
                final_parameter_values=current_parameter_values.tolist(),
                result_group=cloud_result_group,
                step_name=cloud_step_name,
                field_name=cloud_field_name,
            )

        matched_payload = modal_payload.get("matched_payload") or {}
        try:
            update_work_condition_project_status(
                int(project_id),
                fixes_cal_status=1,
                fixes_result_status=1,
            )
        except Exception:
            pass
        result = {
            "project_id": int(project_id),
            "batch_no": str(resolved_batch_no),
            "sensitivity_batch_no": str(resolved_sensitivity_batch_no),
            "cleanup_result": cleanup_result,
            "input_bdf": str(update_source_bdf_path),
            "modal_reference_bdf": str(modal_reference_bdf_path) if modal_reference_bdf_path else None,
            "output_dir": str(root_dir) if save_results else None,
            "save_results": bool(save_results),
            "iterations": len(iteration_results),
            "requested_iterations": int(iterations),
            "stopped_early": bool(stopped_early),
            "exit_diff_percent": None if exit_diff_percent is None else float(exit_diff_percent),
            "final_updated_bdf": final_output_bdf,
            "final_parameter_values": current_parameter_values.tolist(),
            "parameter_columns": [
                {
                    **dict(column),
                    "parameter_name": str(column.get("parameter_name") or f"parameter_{index + 1}"),
                    "initial_value": float(initial_parameter_values[index]),
                    "final_value": float(current_parameter_values[index]),
                    "relative_delta_percent": _resolve_cloud_scalar_percent_value(
                        updated_value=float(current_parameter_values[index]),
                        baseline_value=float(initial_parameter_values[index]),
                    ),
                }
                for index, column in enumerate(current_parameter_columns)
            ],
            "response_rows": response_rows,
            "matched_modal_rows": matched_payload.get("rows") or [],
            "matched_pair_count": len(response_rows),
            "iteration_results": iteration_results,
            "saved_artifacts": saved_artifacts,
            "final_modal_output": final_modal_output,
            "cloud_result": cloud_result,
            "skipped_response_rows_preview": modal_payload.get("skipped_rows", [])[:20],
            "final_sensitivity_analysis_run_id": final_sensitivity_payload.get("analysis_run_id"),
        }
        safe_write_console_event(
            int(project_id),
            "SOL200 模态频率模型修正完成",
            [
                f"批次号: {resolved_batch_no}",
                f"灵敏度批次: {resolved_sensitivity_batch_no}",
                f"迭代次数: {len(iteration_results)}",
                f"提前终止: {'是' if stopped_early else '否'}",
                f"输出目录: {str(root_dir) if save_results else '-'}",
            ],
        )
        return result
    finally:
        if cleanup_root_dir is not None:
            shutil.rmtree(cleanup_root_dir, ignore_errors=True)


def run_bayesian_update_from_text(
        *,
        sensitivity_matrix_file: str,
        sensitivity_row_start: int,
        sensitivity_row_count: int,
        sensitivity_col_start: int = 1,
        model_response_file: str,
        model_response_row: int,
        model_response_col_start: int = 1,
        target_response_file: str,
        target_response_row: int,
        target_response_col_start: int = 1,
        parameter_names: List[str],
        parameter_scatter: Any = None,
        response_scatter: Any = None,
        input_inp: Optional[str] = None,
        parameter_values: Optional[Any] = None,
        damping: float = 1e-8,
        step_scale: float = 1.0,
        lower_bound: Any = None,
        upper_bound: Any = None,
        output_dir: Optional[str] = None,
        case_name: str = "bayesian_text_check",
) -> dict:
    S_norm = _read_text_matrix(
        sensitivity_matrix_file,
        row_start=int(sensitivity_row_start),
        row_count=int(sensitivity_row_count),
        col_start=int(sensitivity_col_start),
    )
    r_model = _read_text_row_vector(model_response_file, row=int(model_response_row), col_start=int(model_response_col_start))
    r_target = _read_text_row_vector(target_response_file, row=int(target_response_row), col_start=int(target_response_col_start))

    parameter_items = [
        {"parameter_name": str(name), "field": str(name), "parameter_token": str(name)}
        for name in parameter_names
    ]
    response_items = [{"row_key": f"r{i + 1}", "response_label": f"r{i + 1}"} for i in range(len(r_model))]

    if parameter_values is None:
        if not input_inp:
            raise ValidationError(
                "input_inp is required when parameter_values is not provided",
                {"input_inp": input_inp, "parameter_values": parameter_values},
            )
        p_current = _parameter_values_from_inp(input_inp, parameter_names)
    else:
        p_current = np.asarray(
            _vector_from_input(
                parameter_values,
                parameter_items,
                label="parameter_values",
                key_candidates=("parameter_name", "field", "parameter_token"),
            ),
            dtype=np.float64,
        )

    if len(r_target) != len(r_model):
        raise ValidationError(
            "target response vector length does not match model response length",
            {"target_len": len(r_target), "model_len": len(r_model)},
        )
    if S_norm.shape != (len(r_model), len(p_current)):
        raise ValidationError(
            "normalized sensitivity matrix shape does not match the supplied response and parameter sizes",
            {
                "matrix_shape": list(S_norm.shape),
                "response_count": len(r_model),
                "parameter_count": len(p_current),
            },
        )

    p_scatter = _resolve_scatter_vector(
        parameter_scatter,
        parameter_items,
        label="parameter_scatter",
        key_candidates=("parameter_name", "field", "parameter_token"),
        default_value=_sens._DEFAULT_PARAMETER_SCATTER,
    )
    r_scatter = _resolve_scatter_vector(
        response_scatter,
        response_items,
        label="response_scatter",
        key_candidates=("row_key", "response_label"),
        default_value=_sens._DEFAULT_RESPONSE_SCATTER,
    )
    lower_bound_values = None
    if lower_bound is not None:
        lower_bound_values = np.asarray(
            _vector_from_input(
                lower_bound,
                parameter_items,
                label="lower_bound",
                key_candidates=("parameter_name", "field", "parameter_token"),
            ),
            dtype=np.float64,
        )
    upper_bound_values = None
    if upper_bound is not None:
        upper_bound_values = np.asarray(
            _vector_from_input(
                upper_bound,
                parameter_items,
                label="upper_bound",
                key_candidates=("parameter_name", "field", "parameter_token"),
            ),
            dtype=np.float64,
        )

    update_payload = bayesian_update_normalized(
        p_current=p_current,
        r_model=r_model,
        r_target=r_target,
        S_norm=S_norm,
        p_scatter=p_scatter,
        r_scatter=r_scatter,
        damping=damping,
        step_scale=step_scale,
        lower_bound=lower_bound_values,
        upper_bound=upper_bound_values,
        p_ref=p_current,
    )
    iteration_metrics = _build_iteration_metrics(
        response_values=r_model.tolist(),
        target_values=r_target.tolist(),
        response_scatter=r_scatter.tolist(),
        parameter_step=update_payload["dp"],
    )

    result = {
        "case_name": str(case_name),
        "input_inp": _normalize_optional_path(input_inp),
        "sensitivity_matrix_file": os.path.abspath(sensitivity_matrix_file),
        "model_response_file": os.path.abspath(model_response_file),
        "target_response_file": os.path.abspath(target_response_file),
        "parameter_names": [str(name) for name in parameter_names],
        "parameter_values": p_current.tolist(),
        "parameter_scatter": p_scatter.tolist(),
        "response_scatter": r_scatter.tolist(),
        "sensitivity_matrix": S_norm.tolist(),
        "model_response": r_model.tolist(),
        "target_response": r_target.tolist(),
        "bayesian": _clone_jsonable(update_payload),
        "metrics": _clone_jsonable(iteration_metrics),
    }

    if output_dir:
        output_root = _solver._abs_dir(output_dir, Path.cwd() / str(case_name))
        saved = {
            "summary_json": _save_json(output_root / "bayesian_text_check.json", result),
            "sensitivity_matrix_txt": _save_matrix_txt(output_root / "sensitivity_matrix.txt", S_norm.tolist()),
            "model_response_txt": _save_vector_txt(output_root / "model_response.txt", r_model.tolist()),
            "target_response_txt": _save_vector_txt(output_root / "target_response.txt", r_target.tolist()),
            "parameter_values_txt": _save_vector_txt(output_root / "parameter_values.txt", p_current.tolist()),
            "updated_parameter_values_txt": _save_vector_txt(
                output_root / "updated_parameter_values.txt",
                result["bayesian"]["p_new"],
            ),
        }
        result["saved_artifacts"] = {"output_dir": str(output_root), "files": saved}

    return result
