"""Compare every real update frame with raw HDF5 data and database history."""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

import h5py
import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from db import get_connection
from tools.odb_client import decode_l3be


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--project-id", required=True, type=int)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--batch-no", required=True, type=int)
    parser.add_argument("--instance", default="PART-1-1")
    args = parser.parse_args()
    workspace = Path(args.workspace).resolve()
    output = workspace / "cal" / "validation" / f"update_{args.batch_no}"
    output.mkdir(parents=True, exist_ok=True)
    base = f"{args.base_url.rstrip('/')}/api/odb/{args.project_id}"
    summaries = [json.loads(p.read_text(encoding="utf-8")) for p in
                 sorted((workspace / "cal" / "bayesian").glob("iteration_*/summary.json"))]
    assert summaries, "No iteration summaries"
    manifest = sqlite3.connect(workspace / "manifest.db")
    evidence = {"project_id": args.project_id, "batch_no": args.batch_no, "iterations": []}

    def get_json(path, params=None):
        response = requests.get(base + path, params=params, timeout=60)
        response.raise_for_status()
        payload = response.json()
        assert payload["code"] == 200, payload
        return payload["data"]

    def read_nodal(group, field, frame):
        relative_path = manifest.execute(
            "SELECT file_path FROM result_files WHERE result_group=? AND step_name='Step-1' AND field_name=?",
            (group, field),
        ).fetchone()[0]
        with h5py.File(workspace / relative_path.replace("\\", "/")) as handle:
            block = handle[f"NODAL/{args.instance}"]
            return block["labels"][:], block["data"][frame]

    geometry_response = requests.get(base + f"/geometry/{args.instance}/render-buffers", timeout=60)
    geometry_response.raise_for_status()
    geometry = decode_l3be(geometry_response.content)
    vertices = geometry["positions"]
    triangles = geometry["indices"]
    assert vertices.shape[0] > 0 and np.isfinite(vertices).all()
    assert triangles.size > 0 and triangles.min() >= 0 and triangles.max() < len(vertices)
    evidence["geometry"] = {"vertex_count": len(vertices), "triangle_count": len(triangles)}
    with h5py.File(workspace / f"l1/geometry/{args.instance}.h5") as handle:
        node_labels = handle["nodes/labels"][:]
    with h5py.File(workspace / f"l2/render/{args.instance}_render.h5") as handle:
        vertex_labels = node_labels[handle["render/vtx_node_row"][:]]
    assert len(vertex_labels) == len(vertices)

    def check_scalars(name, params, expected):
        ranges = get_json("/results/frame-scalar-range", {**params, "instances": args.instance})
        assert args.instance in ranges["instance_ranges"], ranges
        expected = np.asarray(expected, dtype=np.float64)
        assert np.isfinite(expected).all()
        expected_range = [float(expected.min()), float(expected.max())]
        actual_range = [ranges["global_min"], ranges["global_max"]]
        assert np.allclose(actual_range, expected_range, rtol=2e-5, atol=1e-30), (name, actual_range, expected_range)
        response = requests.get(base + "/results/frame-scalars", params={**params, "instance": args.instance}, timeout=60)
        response.raise_for_status()
        sections = decode_l3be(response.content)
        scalar = sections["u_per_vertex"]
        legend = sections["legend_range"]
        assert scalar.shape == (len(vertices),), (name, scalar.shape, len(vertices))
        assert np.isfinite(scalar).all() and np.isfinite(legend).all(), name
        assert np.allclose(legend, expected_range, rtol=2e-5, atol=1e-30), name
        span = expected_range[1] - expected_range[0]
        normalized = np.zeros_like(expected) if span < 1e-30 else np.clip((expected - expected_range[0]) / span, 0, 1)
        assert np.allclose(scalar, normalized, rtol=2e-5, atol=2e-5), (name, "Wrong vertex ordering or values")
        (output / f"{name}.l3be").write_bytes(response.content)
        return {"url": response.url, "status": response.status_code, "vertex_count": len(scalar),
                "finite_count": int(np.isfinite(scalar).sum()), "range": actual_range,
                "max_vertex_error": float(np.max(np.abs(scalar - normalized)))}

    groups = get_json("/sensitivity/result_groups")["result_groups"]
    evidence["sensitivity_step_frame"] = get_json("/sensitivity/step_frame")
    evidence["model_update_fields"] = get_json("/model-update/fields", {"result_group": f"bayesian_batch_{args.batch_no}"})
    assert evidence["model_update_fields"]["fields"]
    previous_response = None
    baseline = summaries[0]["parameter_values"][0]
    for index, summary in enumerate(summaries):
        assert summary["iteration"] == index + 1
        assert Path(summary["input_inp"]).exists() and Path(summary["updated_inp"]).exists()
        source = summary["display_results"]["source_result_group"]
        frame = max(row[0] for row in manifest.execute(
            "SELECT frame_idx FROM frames WHERE result_group=? AND step_name='Step-1'", (source,)))
        labels, displacement = read_nodal(source, "U", frame)
        nodal = dict(zip(labels.tolist(), displacement[:, 1].tolist()))
        raw_response = nodal[4]
        assert np.isclose(raw_response, summary["response_values"][0], rtol=1e-7)
        assert summary["target_responses"] == [0.58]
        if previous_response is not None:
            assert not np.isclose(previous_response, raw_response, atol=1e-7), "Reused an earlier iteration's response"
        previous_response = raw_response
        displacement_check = check_scalars(f"iter{index}_UY", {
            "result_group": source, "step": "Step-1", "field": "U", "component_idx": 1,
            "frame": frame,
        }, [nodal[int(label)] for label in vertex_labels])
        displacement_vectors = dict(zip(labels.tolist(), displacement.tolist()))
        expected_positions = vertices + np.asarray([displacement_vectors[int(label)] for label in vertex_labels])
        deformed_response = requests.get(base + "/results/deformed-positions", params={
            "result_group": source, "step": "Step-1", "frame": frame,
            "instance": args.instance, "scale": 1,
        }, timeout=60)
        deformed_response.raise_for_status()
        deformed = decode_l3be(deformed_response.content)["positions"]
        assert deformed.shape == vertices.shape and np.isfinite(deformed).all()
        assert np.allclose(deformed, expected_positions, atol=2e-7, rtol=2e-6), "Wrong deformed structure"
        (output / f"iter{index}_deformed_positions.l3be").write_bytes(deformed_response.content)
        deformation_check = {"url": deformed_response.url, "vertex_count": len(deformed),
                             "max_position_error": float(np.max(np.abs(deformed - expected_positions)))}
        sens_group = f"sensitivity_update_{args.batch_no}_iter{index}_U_4_U2"
        assert sens_group in groups
        fields = get_json("/sensitivity/fields", {"result_group": sens_group})["fields"]
        assert len(fields) == 1 and fields[0]["response_node_label"] == 4 and fields[0]["component"] == "U2"
        derivative_labels, derivatives = read_nodal(source, "d_U_E_SET_149", frame)
        derivative = float(derivatives[list(derivative_labels).index(4), 1])
        expected_matrix = derivative * summary["parameter_values"][0] / raw_response
        assert np.isclose(expected_matrix, summary["sensitivity_matrix"][0][0], rtol=1e-7)
        sensitivity_check = check_scalars(f"iter{index}_E_sensitivity", {
            "result_group": sens_group, "step": "Sensitivity", "field": fields[0]["field_name"], "frame": 0,
        }, np.full(len(vertices), derivative))
        delta = (summary["bayesian"]["p_new"][0] - baseline) / baseline * 100
        parameter_check = check_scalars(f"iter{index}_parameter_change", {
            "result_group": f"bayesian_batch_{args.batch_no}", "step": "BayesianUpdate",
            "field": "PARAMETER_RELATIVE_DELTA_PERCENT", "component_idx": 0, "frame": index,
        }, np.full(len(vertices), delta))
        evidence["iterations"].append({"evaluation_index": index, "E": summary["parameter_values"][0],
            "UY": raw_response, "target": 0.58, "error_percent": abs(raw_response / 0.58 - 1) * 100,
            "normalized_sensitivity": expected_matrix, "updated_E": summary["bayesian"]["p_new"][0],
            "displacement": displacement_check, "deformed_geometry": deformation_check,
            "sensitivity": sensitivity_check, "parameter_change": parameter_check})

    assert evidence["iterations"][-1]["error_percent"] <= 0.1
    assert summaries[-1]["parameter_values"] == summaries[-1]["bayesian"]["p_new"], "Final parameters not re-evaluated"
    connection = get_connection()
    cursor = connection.cursor(dictionary=True)
    cursor.execute("SELECT * FROM t_mt_py_fem_response_difference WHERE pid=%s AND batch_no=%s ORDER BY iteration",
                   (args.project_id, args.batch_no))
    tracking = cursor.fetchall()
    assert len(tracking) == len(summaries)
    for row, item in zip(tracking, evidence["iterations"]):
        assert np.isclose(row["cal_result_value"], item["UY"], rtol=2e-6)
        assert np.isclose(row["test_result_value"], 0.58, rtol=2e-6)
    cursor.execute("SELECT u2 FROM t_mt_py_fem_model_update_static_result WHERE pid=%s AND batch_no=%s AND instance_name=%s AND fem_node_label=4",
                   (args.project_id, args.batch_no, args.instance))
    final_row = cursor.fetchone()
    assert final_row and np.isclose(final_row["u2"], evidence["iterations"][-1]["UY"], rtol=2e-6)
    evidence["database_response_history"] = tracking
    evidence["database_final_UY"] = final_row["u2"]
    cursor.close()
    connection.close()
    manifest.close()
    (output / "iteration-api-validity.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
