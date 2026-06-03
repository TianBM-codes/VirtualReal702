from types import SimpleNamespace

from services.model_update.analysis.nastran_sol200_service import (
    _clone_property_with_material,
    _material_copy_with_new_id,
    run_sol200_and_store_workflow,
)
from services.model_update.solver_prep.nastran_sol200 import (
    _build_response_lines,
    build_sol200_controls,
    build_sol200_lines,
)


def test_build_sol200_controls_uses_plot_displacement_and_subcase_dessub_for_op2():
    lines = build_sol200_controls(
        {
            "result.target": "OP2",
            "dynamic.vectors": 8,
        }
    )

    assert "DISPLACEMENT(PLOT) = ALL" in lines
    assert "  DESSUB = 1" in lines


def test_build_sol200_controls_uses_formatted_dsaprt_when_csv_enabled(tmp_path):
    lines = build_sol200_controls(
        {
            "result.target": "OP2",
            "dynamic.vectors": 8,
        },
        sensitivity_csv_path=str(tmp_path / "sol200_sens.csv"),
    )

    assert "DSAPRT(FORMATTED,EXPORT,END=SENS)" in lines
    assert "PARAM,XYUNIT,52" in lines


def test_build_sol200_response_lines_allow_negative_lower_bound():
    lines = _build_response_lines(1, {"type": "FREQ", "name": "FREQ1", "mode_number": 1})

    assert "DCONSTR,1,1,-1.0E30,1.0E30" in lines


def test_build_sol200_design_lines_add_single_dscreen_for_freq_responses(tmp_path):
    input_bdf = tmp_path / "input.bdf"
    input_bdf.write_text("SOL 103\nCEND\nBEGIN BULK\nENDDATA\n", encoding="utf-8")

    payload = build_sol200_lines(
        input_bdf=str(input_bdf),
        parameters=[
            {
                "name": "E1",
                "type": "E",
                "material_id": 1001,
                "initial": 210000.0,
                "lower": 2000.0,
                "upper": 300000.0,
            }
        ],
        responses=[
            {"type": "FREQ", "name": "FREQ1", "mode_number": 1},
            {"type": "FREQ", "name": "FREQ2", "mode_number": 2},
        ],
        settings={"dynamic.norm": "MASS"},
    )

    dscreen_lines = [line for line in payload["response_lines"] if "DSCREEN" in line]
    assert dscreen_lines == ["DSCREEN  FREQ    -1.0E30"]


def test_build_sol200_lines_preserves_existing_eigrl_frequency_range(tmp_path):
    input_bdf = tmp_path / "input.bdf"
    input_bdf.write_text(
        "\n".join(
            [
                "SOL 103",
                "CEND",
                "BEGIN BULK",
                "EIGRL          1   100.0 10000.0      20                            MASS",
                "ENDDATA",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    payload = build_sol200_lines(
        input_bdf=str(input_bdf),
        parameters=[
            {
                "name": "E1",
                "type": "E",
                "material_id": 1001,
                "initial": 210000.0,
                "lower": 2000.0,
                "upper": 300000.0,
            }
        ],
        responses=[{"type": "FREQ", "name": "FREQ1", "mode_number": 1}],
        settings={"dynamic.norm": "MASS"},
    )

    eigrl_line = next(line for line in payload["control_lines"] if line.startswith("EIGRL,"))
    assert eigrl_line == "EIGRL,1,100.0,10000.0,20,,,,MASS"


def test_clone_property_with_material_updates_multimaterial_fields():
    prop = SimpleNamespace(pid=10, mid1=101, mid2=202, mid3=303, type="PCOMP")

    cloned = _clone_property_with_material(prop, new_pid=20, new_mid=404)

    assert cloned.pid == 20
    assert cloned.mid1 == 404
    assert cloned.mid2 == 404
    assert cloned.mid3 is None


def test_material_copy_with_new_id_clears_mat1_g():
    material = SimpleNamespace(mid=10, g=123.0, type="MAT1")

    cloned = _material_copy_with_new_id(material, new_mid=20)

    assert cloned.mid == 20
    assert cloned.g is None


def test_run_sol200_and_store_workflow_uses_generated_op2_and_metadata(monkeypatch, tmp_path):
    output_bdf = tmp_path / "demo_sol200.bdf"
    output_bdf.write_text("BEGIN BULK\nENDDATA\n", encoding="utf-8")
    op2_path = tmp_path / "demo_sol200.op2"
    op2_path.write_bytes(b"op2")
    metadata_path = tmp_path / "demo_sol200.bdf.sol200.json"
    metadata_path.write_text("{}", encoding="utf-8")

    captured = {}

    def fake_run_sol200_workflow(**kwargs):
        return {
            "input_bdf": str(tmp_path / "input.bdf"),
            "output_bdf": str(output_bdf),
            "generated_files": {"metadata_json": str(metadata_path)},
            "solver": {
                "artifacts_summary": {
                    "op2_files": [str(op2_path)],
                    "has_op2": True,
                }
            },
            "warnings": [],
        }

    def fake_store_sol200_sensitivity(**kwargs):
        captured.update(kwargs)
        return {"stored": True, "batch_no": kwargs["batch_no"]}

    monkeypatch.setattr(
        "services.model_update.analysis.nastran_sol200_service.run_sol200_workflow",
        fake_run_sol200_workflow,
    )
    monkeypatch.setattr(
        "services.model_update.analysis.nastran_sol200_service.store_sol200_sensitivity",
        fake_store_sol200_sensitivity,
    )

    payload = run_sol200_and_store_workflow(
        project_id=3,
        batch_no="1",
        case_name="modal_freq_sens",
        input_bdf=str(tmp_path / "input.bdf"),
        output_bdf=str(output_bdf),
        settings={"dynamic.norm": "MASS"},
    )

    assert captured["project_id"] == 3
    assert captured["op2_path"] == str(op2_path.resolve())
    assert captured["bdf_path"] == str(output_bdf.resolve())
    assert captured["metadata_json"] == str(metadata_path.resolve())
    assert payload["store"]["stored"] is True


def test_run_sol200_and_store_workflow_falls_back_to_sensitivity_csv(monkeypatch, tmp_path):
    output_bdf = tmp_path / "demo_sol200.bdf"
    output_bdf.write_text("BEGIN BULK\nENDDATA\n", encoding="utf-8")
    csv_path = tmp_path / "sol200_sens.csv"
    csv_path.write_text("dummy", encoding="utf-8")
    metadata_path = tmp_path / "demo_sol200.bdf.sol200.json"
    metadata_path.write_text("{}", encoding="utf-8")

    captured = {}

    def fake_run_sol200_workflow(**kwargs):
        return {
            "input_bdf": str(tmp_path / "input.bdf"),
            "output_bdf": str(output_bdf),
            "generated_files": {
                "metadata_json": str(metadata_path),
                "sensitivity_csv": str(csv_path),
            },
            "solver": {
                "artifacts_summary": {
                    "op2_files": [],
                    "has_op2": False,
                    "unit11_candidates": [],
                }
            },
            "warnings": [
                {
                    "code": "NASTRAN_OP2_NOT_FOUND",
                    "message": "solve completed without an OP2 file",
                }
            ],
        }

    def fake_store_sol200_sensitivity(**kwargs):
        captured.update(kwargs)
        return {"stored": True, "batch_no": kwargs["batch_no"]}

    monkeypatch.setattr(
        "services.model_update.analysis.nastran_sol200_service.run_sol200_workflow",
        fake_run_sol200_workflow,
    )
    monkeypatch.setattr(
        "services.model_update.analysis.nastran_sol200_service.store_sol200_sensitivity",
        fake_store_sol200_sensitivity,
    )

    payload = run_sol200_and_store_workflow(
        project_id=3,
        batch_no="1",
        case_name="modal_freq_sens",
        input_bdf=str(tmp_path / "input.bdf"),
        output_bdf=str(output_bdf),
        settings={"dynamic.norm": "MASS", "sol200.sensitivity_csv": True},
    )

    assert captured["op2_path"] is None
    assert captured["matrix_path"] == str(csv_path.resolve())
    assert payload["matrix_path"] == str(csv_path.resolve())
    assert payload["store"]["stored"] is True
