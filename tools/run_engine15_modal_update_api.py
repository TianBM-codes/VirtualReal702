import argparse
import json
import time
from pathlib import Path

import requests


def _save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _call(session: requests.Session, *, base_url: str, name: str, path: str, payload: dict, timeout: int, log_dir: Path) -> dict:
    url = base_url.rstrip("/") + path
    start = time.time()
    response = session.post(url, json=payload, timeout=timeout)
    elapsed = time.time() - start
    try:
        body = response.json()
    except Exception:
        body = {"raw_text": response.text}
    record = {
        "name": name,
        "path": path,
        "status_code": response.status_code,
        "elapsed_sec": round(elapsed, 3),
        "request": payload,
        "response": body,
    }
    _save_json(log_dir / f"{name}.json", record)
    ok = response.status_code == 200 and (not isinstance(body, dict) or body.get("code", 200) == 200)
    print(f"{name}: http={response.status_code} elapsed={elapsed:.2f}s ok={ok}")
    if not ok:
        raise RuntimeError(f"{name} failed: {json.dumps(body, ensure_ascii=False)}")
    return body


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the engine15 modal-update workflow through HTTP APIs.")
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--project-id", type=int, default=15062215)
    parser.add_argument("--bdf", default=r"C:\FEMtools\3.7.1\examples\updating\engine\sol103_final.bdf")
    parser.add_argument("--unv", default=r"C:\FEMtools\3.7.1\examples\updating\engine\ema15.unv")
    parser.add_argument("--log-dir", default="temp/engine15_api_run")
    parser.add_argument("--run-bayesian", action="store_true")
    args = parser.parse_args()

    log_dir = Path(args.log_dir)
    session = requests.Session()
    summary = {"project_id": args.project_id, "base_url": args.base_url, "steps": []}

    steps = [
        (
            "01_import_unv",
            "/import/unv",
            {
                "file_path": args.unv,
                "project_id": args.project_id,
                "file_id": args.project_id,
                "clear_before_insert": True,
            },
            120,
        ),
        (
            "02_import_bdf",
            "/import/bdf",
            {
                "file_path": args.bdf,
                "project_id": args.project_id,
                "clear_before_insert": True,
            },
            120,
        ),
        (
            "03_sol103_run_and_store_modal",
            "/solver/nastran/sol103/run_and_store_modal",
            {
                "project_id": args.project_id,
                "input_bdf": args.bdf,
                "settings": {
                    "dynamic.vectors": 12,
                    "dynamic.fmin": 100.0,
                    "result.target": "OP2",
                    "post": -1,
                },
                "timeout_sec": 1200,
                "overwrite": True,
                "mode_numbers": list(range(1, 13)),
            },
            1800,
        ),
        (
            "04_match_nodes",
            "/match/nodes",
            {
                "project_id": args.project_id,
                "overwrite": True,
            },
            120,
        ),
        (
            "05_match_dofs",
            "/match/dofs",
            {
                "project_id": args.project_id,
                "overwrite": True,
            },
            120,
        ),
        (
            "06_compute_modal_correlation",
            "/correlation/modal/compute",
            {
                "project_id": args.project_id,
                "overwrite": True,
                "mac_threshold": 0,
            },
            120,
        ),
        (
            "07_modal_frequency_create_from_match",
            "/optimization/response/modal_frequency/create_from_match",
            {
                "project_id": args.project_id,
                "overwrite": True,
                "mac_threshold": 0,
                "max_freq_error_ratio": 1.0,
                "matching_method": "greedy",
                "scatter": 0.05,
            },
            120,
        ),
        (
            "08_sol200_preview_all_elements_e",
            "/solver/nastran/sol200/preview",
            {
                "project_id": args.project_id,
                "input_bdf": args.bdf,
                "parameter_preset": {
                    "preset": "all_elements_e",
                    "lower_scale": 0.01,
                    "upper_scale": 1000000.0,
                },
                "responses": [
                    {"name": "FREQ_MODE_1", "type": "FREQ", "mode_number": 1},
                    {"name": "FREQ_MODE_2", "type": "FREQ", "mode_number": 2},
                ],
                "settings": {
                    "sol200.deck_mode": "include",
                    "sol200.sensitivity_csv": True,
                    "result.target": "F06",
                    "post": -1,
                    "dynamic.vectors": 12,
                    "dynamic.fmin": 100.0,
                },
            },
            1200,
        ),
        (
            "09_sol200_run_and_store_all_elements_e",
            "/solver/nastran/sol200/run_and_store",
            {
                "project_id": args.project_id,
                "batch_no": str(args.project_id),
                "case_name": "engine15_all_elements_e_freq12",
                "input_bdf": args.bdf,
                "parameter_preset": {
                    "preset": "all_elements_e",
                    "lower_scale": 0.01,
                    "upper_scale": 1000000.0,
                },
                "responses": [
                    {"name": "FREQ_MODE_1", "type": "FREQ", "mode_number": 1},
                    {"name": "FREQ_MODE_2", "type": "FREQ", "mode_number": 2},
                ],
                "settings": {
                    "sol200.deck_mode": "include",
                    "sol200.sensitivity_csv": True,
                    "result.target": "F06",
                    "post": -1,
                    "dynamic.vectors": 12,
                    "dynamic.fmin": 100.0,
                    "dynamic.norm": "MASS",
                },
                "run_solver": True,
                "timeout_sec": 1800,
                "write_cloud_result": False,
            },
            2400,
        ),
    ]

    if args.run_bayesian:
        steps.append(
            (
                "10_bayesian_sol200_modal_frequency_run",
                "/optimization/bayesian/sol200/modal_frequency/run",
                {
                    "project_id": args.project_id,
                    "batch_no": args.project_id + 1,
                    "sensitivity_batch_no": args.project_id,
                    "input_bdf": args.bdf,
                    "save_results": True,
                    "iterations": 1,
                    "step_scale": 1.0,
                    "damping": 1e-8,
                    "mac_threshold": 0,
                    "max_freq_error_ratio": 1.0,
                    "matching_method": "greedy",
                    "settings": {
                        "sol200.deck_mode": "include",
                        "sol200.sensitivity_csv": True,
                        "result.target": "F06",
                        "post": -1,
                        "dynamic.vectors": 12,
                        "dynamic.fmin": 100.0,
                        "dynamic.norm": "MASS",
                    },
                    "timeout_sec": 1800,
                    "write_cloud_result": False,
                },
                3600,
            )
        )

    for name, path, payload, timeout in steps:
        result = _call(
            session,
            base_url=args.base_url,
            name=name,
            path=path,
            payload=payload,
            timeout=timeout,
            log_dir=log_dir,
        )
        summary["steps"].append(
            {
                "name": name,
                "path": path,
                "message": result.get("message"),
            }
        )

    _save_json(log_dir / "summary.json", summary)


if __name__ == "__main__":
    main()
