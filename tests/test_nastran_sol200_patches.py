from types import SimpleNamespace
from pathlib import Path

from src.l3.core.errors import ValidationError
from services.model_update.analysis.nastran_sol200_service import (
    DEFAULT_PARAMETER_LOWER_SCALE,
    DEFAULT_PARAMETER_UPPER_SCALE,
    _clone_property_with_material,
    _build_all_used_material_e_rho_parameters,
    _localize_elements_e_parameters,
    _material_copy_with_new_id,
    run_sol200_and_store_workflow,
    run_sol200_modal_mac_and_store_workflow,
    sync_generate_run_and_store_sol200_workflow,
)
from webapi.models import NastranSol200SyncGenerateRunAndStoreRequest
from webapi.routers import solver as solver_router
from services.model_update.solver_prep.nastran_sol200 import (
    _build_response_lines,
    build_sol200_controls,
    build_sol200_lines,
)


def test_run_sol200_modal_mac_and_store_workflow_prefers_formatted_csv_preview(monkeypatch):
    from services.model_update.analysis import nastran_sol200_service
    from services.model_update.analysis import sensitivity_service
    from services.model_update.importers import op2_service

    preview_calls = []
    persisted = {}

    monkeypatch.setattr(nastran_sol200_service, "_set_project_sensitivity_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(nastran_sol200_service, "_log_project_workflow_failure", lambda *args, **kwargs: None)
    monkeypatch.setattr(nastran_sol200_service, "update_work_condition_project_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        nastran_sol200_service,
        "_resolve_sol200_config_sources",
        lambda **kwargs: (
            [],
            [
                {
                    "name": "MAC_MODE_FE1_TEST1",
                    "type": "MODAL_MAC",
                    "mode_number": 1,
                    "extra_json": {"fem_mode_no": 1, "test_mode_no": 1},
                }
            ],
        ),
    )
    monkeypatch.setattr(
        nastran_sol200_service,
        "expand_modal_mac_responses_to_sol200_displacements",
        lambda **kwargs: {
            "expanded_rows": [
                {
                    "name": "M1T1N67U1",
                    "type": "DISP",
                    "mode_number": 1,
                    "node_id": 67,
                    "component": "U1",
                    "extra_json": {
                        "source_response_name": "MAC_MODE_FE1_TEST1",
                        "fem_mode_no": 1,
                        "test_mode_no": 1,
                        "fem_node_label": 67,
                        "component": "U1",
                    },
                }
            ],
            "mapping_rows": [],
        },
    )
    monkeypatch.setattr(
        nastran_sol200_service,
        "run_sol200_workflow",
        lambda **kwargs: {
            "input_bdf": kwargs["input_bdf"],
            "output_bdf": kwargs.get("output_bdf") or "D:/demo/out.bdf",
            "generated_files": {"metadata_json": "D:/demo/meta.json"},
            "warnings": [],
        },
    )
    monkeypatch.setattr(nastran_sol200_service, "_resolve_generated_sol200_op2_path", lambda payload: None)
    monkeypatch.setattr(nastran_sol200_service, "_resolve_generated_sol200_matrix_path", lambda payload: "D:/demo/sens.csv")
    monkeypatch.setattr(nastran_sol200_service, "_pick_first_existing_path", lambda candidates: None)
    monkeypatch.setattr(
        nastran_sol200_service.Path,
        "exists",
        lambda self: str(self).lower().endswith(".csv"),
    )
    monkeypatch.setattr(
        nastran_sol200_service.Path,
        "is_file",
        lambda self: str(self).lower().endswith(".csv"),
    )
    monkeypatch.setattr(
        nastran_sol200_service.Path,
        "stat",
        lambda self: SimpleNamespace(st_size=128),
    )

    def fake_preview(**kwargs):
        preview_calls.append(
            {
                "response_names": kwargs.get("response_names"),
                "matrix_path": kwargs.get("matrix_path"),
                "op2_path": kwargs.get("op2_path"),
            }
        )
        return {
            "response_rows": [{"response_name": "M1T1N67U1", "response_type": "DISP", "mode_number": 1, "node_id": 67, "component": "U1"}],
            "parameter_columns": [{"parameter_name": "E1"}],
            "matrix_preview": [[1.0]],
            "warnings": [],
        }

    monkeypatch.setattr(op2_service, "preview_op2_sensitivity", fake_preview)
    monkeypatch.setattr(
        nastran_sol200_service,
        "build_modal_mac_matrix_from_displacement_sensitivity",
        lambda **kwargs: {
            "response_rows": [{"response_name": "MAC_MODE_FE1_TEST1", "response_type": "MODAL_MAC", "mode_number": 1}],
            "parameter_columns": [{"parameter_name": "E1"}],
            "matrix": [[91.5]],
        },
    )

    def fake_persist(**kwargs):
        persisted["matrix_payload"] = kwargs["matrix_payload"]
        return {"stored": True}

    monkeypatch.setattr(sensitivity_service, "_persist_sensitivity_matrix", fake_persist)

    payload = run_sol200_modal_mac_and_store_workflow(
        project_id=7,
        batch_no="3",
        case_name="mac_case",
        input_bdf="D:/demo/in.bdf",
        output_bdf="D:/demo/out.bdf",
        responses=[{"name": "MAC_MODE_FE1_TEST1", "type": "MODAL_MAC", "mode_number": 1, "extra_json": {"fem_mode_no": 1, "test_mode_no": 1}}],
        run_solver=False,
    )

    assert preview_calls == [
        {
            "response_names": ["M1T1N67U1"],
            "matrix_path": "D:/demo/sens.csv",
            "op2_path": None,
        }
    ]
    assert payload["matrix_path"] == "D:/demo/sens.csv"
    assert persisted["matrix_payload"]["response_rows"][0]["response_name"] == "MAC_MODE_FE1_TEST1"


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

    assert "DRESP1,1,FREQ1,FREQ,,,1" in lines
    assert "DCONSTR,1,1,-1.0E30,1.0E30" in lines


def test_build_sol200_response_lines_shorten_long_frequency_name():
    lines = _build_response_lines(1, {"type": "FREQ", "name": "FREQ_MODE_1", "mode_number": 1})

    assert "DRESP1,1,FREQ1,FREQ,,,1" in lines
    assert "FREQ_MODE_1" not in lines[0]


def test_build_sol200_response_lines_supports_modal_displacement_component():
    lines = _build_response_lines(
        2,
        {
            "type": "DISP",
            "name": "MODE1_NODE3_U3",
            "mode_number": 1,
            "node_id": 3,
            "component": "U3",
        },
    )

    assert "DRESP1,2,DISP2,DISP,,,3,1,3" in lines
    assert "DCONSTR,1,2,-1.0E30,1.0E30" in lines


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


def test_build_sol200_lines_include_mode_uses_plain_design_model_name(tmp_path):
    input_bdf = tmp_path / "input.bdf"
    output_bdf = tmp_path / "input_sol200.bdf"
    design_bdf = tmp_path / "design_model.bdf"
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
        responses=[{"type": "FREQ", "name": "FREQ1", "mode_number": 1}],
        settings={"dynamic.norm": "MASS", "sol200.deck_mode": "include"},
        output_bdf=str(output_bdf),
        output_design_bdf=str(design_bdf),
    )

    assert "INCLUDE 'design_model.bdf'" in payload["output_lines"]


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


def test_all_used_material_preset_uses_relaxed_default_bounds(tmp_path):
    input_bdf = tmp_path / "material_model.bdf"
    input_bdf.write_text(
        "\n".join(
            [
                "SOL 103",
                "CEND",
                "BEGIN BULK",
                "GRID,1,,0.,0.,0.",
                "GRID,2,,1.,0.,0.",
                "MAT1,1,210000.,,0.3,7.8E-9",
                "PROD,1,1,1.0",
                "CROD,1,1,1,2",
                "ENDDATA",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    parameters = _build_all_used_material_e_rho_parameters(
        input_bdf=str(input_bdf),
        preset={},
    )

    e_param = next(item for item in parameters if item["type"] == "E")
    rho_param = next(item for item in parameters if item["type"] == "RHO")
    assert e_param["lower"] == e_param["initial"] * DEFAULT_PARAMETER_LOWER_SCALE
    assert e_param["upper"] == e_param["initial"] * DEFAULT_PARAMETER_UPPER_SCALE
    assert rho_param["lower"] == rho_param["initial"] * DEFAULT_PARAMETER_LOWER_SCALE
    assert rho_param["upper"] == rho_param["initial"] * DEFAULT_PARAMETER_UPPER_SCALE


def test_all_elements_e_preset_uses_relaxed_default_bounds(tmp_path):
    input_bdf = tmp_path / "element_model.bdf"
    output_bdf = tmp_path / "element_model_localized.bdf"
    input_bdf.write_text(
        "\n".join(
            [
                "SOL 103",
                "CEND",
                "BEGIN BULK",
                "GRID,1,,0.,0.,0.",
                "GRID,2,,1.,0.,0.",
                "MAT1,1,210000.,,0.3",
                "PROD,1,1,1.0",
                "CROD,1,1,1,2",
                "ENDDATA",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    _, parameters, info = _localize_elements_e_parameters(
        input_bdf=str(input_bdf),
        output_bdf=str(output_bdf),
        preset={},
    )

    assert info["localized_element_count"] == 1
    assert parameters[0]["lower"] == parameters[0]["initial"] * DEFAULT_PARAMETER_LOWER_SCALE
    assert parameters[0]["upper"] == parameters[0]["initial"] * DEFAULT_PARAMETER_UPPER_SCALE


def test_run_sol200_and_store_workflow_uses_generated_op2_and_metadata(monkeypatch, tmp_path):
    output_bdf = tmp_path / "demo_sol200.bdf"
    output_bdf.write_text("BEGIN BULK\nENDDATA\n", encoding="utf-8")
    op2_path = tmp_path / "demo_sol200.op2"
    op2_path.write_bytes(b"op2")
    metadata_path = tmp_path / "demo_sol200.bdf.sol200.json"
    metadata_path.write_text("{}", encoding="utf-8")

    captured = {}
    status_calls = []

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
    monkeypatch.setattr(
        "services.model_update.analysis.nastran_sol200_service.update_work_condition_project_status",
        lambda project_id, **fields: status_calls.append((project_id, fields)),
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
    assert status_calls == [
        (3, {"sensitivity_status": 0}),
        (3, {"sensitivity_status": 1}),
    ]


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


def test_run_sol200_and_store_workflow_marks_failed_status_and_logs_console(monkeypatch, tmp_path):
    console_events = []
    status_calls = []

    monkeypatch.setattr(
        "services.model_update.analysis.nastran_sol200_service.run_sol200_workflow",
        lambda **kwargs: (_ for _ in ()).throw(ValidationError("solver failed", {})),
    )
    monkeypatch.setattr(
        "services.model_update.analysis.nastran_sol200_service.update_work_condition_project_status",
        lambda project_id, **fields: status_calls.append((project_id, fields)),
    )
    monkeypatch.setattr(
        "services.model_update.analysis.nastran_sol200_service.safe_write_console_event",
        lambda project_id, title, lines=None: console_events.append((project_id, title, list(lines or []))),
    )

    try:
        run_sol200_and_store_workflow(
            project_id=3,
            batch_no="1",
            case_name="modal_freq_sens",
            input_bdf=str(tmp_path / "input.bdf"),
            output_bdf=str(tmp_path / "demo_sol200.bdf"),
            settings={"dynamic.norm": "MASS"},
        )
    except ValidationError:
        pass
    else:  # pragma: no cover - defensive
        raise AssertionError("expected run_sol200_and_store_workflow to fail")

    assert status_calls == [
        (3, {"sensitivity_status": 0}),
        (3, {"sensitivity_status": 2}),
    ]
    assert console_events


def test_sync_generate_run_and_store_sol200_workflow_chains_existing_steps(monkeypatch, tmp_path):
    calls = []
    output_bdf = tmp_path / "demo_sol200.bdf"

    monkeypatch.setattr(
        "services.model_update.analysis.nastran_sol200_service.sync_sol200_config_from_catalog",
        lambda **kwargs: calls.append(("sync", kwargs)) or {"synced": True, "response_count": 2},
    )
    monkeypatch.setattr(
        "services.model_update.analysis.nastran_sol200_service.generate_sol200_workflow",
        lambda **kwargs: calls.append(("generate", kwargs)) or {"output_bdf": str(output_bdf), "generated": True},
    )
    monkeypatch.setattr(
        "services.model_update.analysis.nastran_sol200_service.run_sol200_and_store_workflow",
        lambda **kwargs: calls.append(("run_and_store", kwargs)) or {"stored": True, "output_bdf": str(output_bdf)},
    )

    payload = sync_generate_run_and_store_sol200_workflow(
        project_id=8,
        batch_no="11",
        case_name="auto_case",
        input_bdf=str(tmp_path / "input.bdf"),
        output_bdf=str(output_bdf),
        response_source="response_catalog",
        settings={"dynamic.norm": "MASS"},
        write_cloud_result=False,
    )

    assert [item[0] for item in calls] == ["sync", "generate", "run_and_store"]
    assert calls[0][1]["project_id"] == 8
    assert calls[1][1]["input_bdf"] == str(tmp_path / "input.bdf")
    assert calls[2][1]["output_bdf"] == str(output_bdf)
    assert payload["sync_config"]["synced"] is True
    assert payload["generate"]["generated"] is True
    assert payload["run_and_store"]["stored"] is True


def test_sync_generate_run_and_store_sol200_workflow_marks_failed_status_when_sync_step_fails(monkeypatch, tmp_path):
    status_calls = []
    console_events = []

    monkeypatch.setattr(
        "services.model_update.analysis.nastran_sol200_service.sync_sol200_config_from_catalog",
        lambda **kwargs: (_ for _ in ()).throw(
            ValidationError(
                "no SOL200-compatible responses could be resolved from response catalog",
                {"project_id": 8},
            )
        ),
    )
    monkeypatch.setattr(
        "services.model_update.analysis.nastran_sol200_service.update_work_condition_project_status",
        lambda project_id, **fields: status_calls.append((project_id, fields)),
    )
    monkeypatch.setattr(
        "services.model_update.analysis.nastran_sol200_service.safe_write_console_event",
        lambda project_id, title, lines=None: console_events.append((project_id, title, list(lines or []))),
    )

    try:
        sync_generate_run_and_store_sol200_workflow(
            project_id=8,
            batch_no="11",
            case_name="auto_case",
            input_bdf=str(tmp_path / "input.bdf"),
            output_bdf=str(tmp_path / "demo_sol200.bdf"),
            response_source="response_catalog",
            settings={"dynamic.norm": "MASS"},
            write_cloud_result=False,
        )
    except ValidationError as exc:
        assert "no SOL200-compatible responses could be resolved from response catalog" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected sync_generate_run_and_store_sol200_workflow to fail")

    assert status_calls == [
        (8, {"sensitivity_status": 2}),
    ]
    assert console_events


def test_sol200_sync_generate_run_and_store_kwargs_defaults_output_name(monkeypatch, tmp_path):
    input_bdf = tmp_path / "fem15_sol103.bdf"
    output_bdf = tmp_path / "fem15_sol200.bdf"

    monkeypatch.setattr(
        solver_router,
        "_resolve_sol200_input_bdf",
        lambda **kwargs: str(input_bdf),
    )
    monkeypatch.setattr(
        solver_router,
        "_resolve_project_local_output_file",
        lambda **kwargs: str(output_bdf),
    )

    body = NastranSol200SyncGenerateRunAndStoreRequest(
        project_id=20,
        input_bdf_name="fem15_sol103.bdf",
    )
    kwargs = solver_router._sol200_sync_generate_run_and_store_kwargs(body)

    assert kwargs["input_bdf"] == str(input_bdf)
    assert kwargs["output_bdf"] == str(output_bdf)
    assert kwargs["settings"]["sol200.deck_mode"] == "include"
    assert kwargs["settings"]["sol200.sensitivity_csv"] is True


def test_resolve_sol200_input_bdf_translates_original_project_source_name(monkeypatch, tmp_path):
    runtime_bdf = tmp_path / "project_bdf_abcd1234.bdf"
    runtime_bdf.write_text("CEND\nBEGIN BULK\nENDDATA\n", encoding="utf-8")

    class _FakeRepo:
        def __init__(self, db_path):
            self.db_path = db_path

        def get_project(self, project_id):
            return {
                "workspace": str(tmp_path),
                "inp_path": str(runtime_bdf),
                "source_file": runtime_bdf.name,
                "original_inp_path": str(tmp_path / "车门模型.bdf"),
                "original_source_file": "车门模型.bdf",
            }

        def resolve_workspace(self, stored, data_root):
            return str(tmp_path)

    monkeypatch.setattr(
        solver_router,
        "_resolve_project_local_input_with_fallback",
        lambda **kwargs: (_ for _ in ()).throw(ValidationError("input_bdf not found")),
    )
    monkeypatch.setattr(solver_router, "RegistryRepo", _FakeRepo)

    resolved = solver_router._resolve_sol200_input_bdf(
        project_id=20,
        explicit_path=str(tmp_path / "车门模型.bdf"),
        file_name=None,
    )

    assert resolved == str(runtime_bdf.resolve())
