from pathlib import Path
from types import SimpleNamespace

from services.model_update.analysis.solver_service import (
    _build_abaqus_command,
    _copy_missing_relative_includes,
    _build_project_result_parse_options,
    _build_nastran_command,
    _run_local_solver,
    run_abaqus_adjoint_job,
    run_abaqus_sensitivity_job,
    run_nastran_sol103_and_store_modal_results,
    run_nastran_sol103_job,
    run_solver_and_parse_project_result,
)
from services.model_update.solver_prep.nastran_sol103 import build_sol103_controls


def _write_text(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_run_abaqus_sensitivity_job_prepares_files_without_running_solver(tmp_path: Path):
    input_inp = _write_text(
        tmp_path / "model.inp",
        """*Heading
*Material, name=STEEL
*Elastic
210000., 0.3
*Elset, elset=EALL
1,
*Nset, nset=NRESP
1,
*Step
*Static
0.1, 1.0
*End Step
""",
    )

    result = run_abaqus_sensitivity_job(
        input_inp=str(input_inp),
        output_dir=str(tmp_path / "out"),
        run_solver=False,
    )

    assert result["workflow"] == "abaqus_sensitivity"
    assert Path(result["generated_files"]["design_parameter_inp"]).exists()
    assert Path(result["generated_files"]["analysis_inp"]).exists()
    assert result["solver"] is None
    assert result["command_preview"][0].lower().endswith("abaqus.bat") or result["command_preview"][0] == "abaqus"


def test_run_abaqus_sensitivity_job_copies_relative_include_dependencies(tmp_path: Path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _write_text(source_dir / "extra.inc", "*Comment\n")
    input_inp = _write_text(
        source_dir / "model.inp",
        """*Heading
*Include, input=extra.inc
*Material, name=STEEL
*Elastic
210000., 0.3
*Elset, elset=EALL
1,
*Nset, nset=NRESP
1,
*Step
*Static
0.1, 1.0
*End Step
""",
    )

    result = run_abaqus_sensitivity_job(
        input_inp=str(input_inp),
        output_dir=str(tmp_path / "out"),
        run_solver=False,
    )

    copied = result["generated_files"].get("copied_include_files") or []
    assert len(copied) == 1
    assert Path(copied[0]).exists()
    assert Path(copied[0]).read_text(encoding="utf-8") == "*Comment\n"


def test_run_abaqus_adjoint_job_prepares_inp_without_running_solver(tmp_path: Path):
    input_inp = _write_text(
        tmp_path / "shell_model.inp",
        """*Heading
*Part, name=P1
*Node
1, 0., 0., 0.
2, 1., 0., 0.
3, 1., 1., 0.
4, 0., 1., 0.
*Element, type=S4, elset=PANEL
1, 1, 2, 3, 4
*Nset, nset=NRESP
1,
*Shell Section, elset=PANEL, material=STEEL
2.5, 5
*Material, name=STEEL
*Elastic
210000., 0.3
*Step
*Static
0.1, 1.0
*End Step
""",
    )

    result = run_abaqus_adjoint_job(
        input_inp=str(input_inp),
        run_solver=False,
    )

    assert result["workflow"] == "abaqus_adjoint_shell"
    assert Path(result["generated_files"]["analysis_inp"]).exists()
    assert result["solver"] is None
    assert result["command_preview"][0].lower().endswith("abaqus.bat") or result["command_preview"][0] == "abaqus"


def test_copy_missing_relative_includes_only_copies_existing_relative_files(tmp_path: Path):
    source_dir = tmp_path / "src"
    target_dir = tmp_path / "out"
    source_dir.mkdir()
    target_dir.mkdir()
    _write_text(source_dir / "a.inc", "A\n")
    generated_inp = _write_text(
        target_dir / "generated.inp",
        """*Heading
*Include, input=a.inc
*Include, input=missing.inc
*Include, input=C:/absolute/path.inc
""",
    )
    source_inp = _write_text(source_dir / "model.inp", "*Heading\n")

    copied = _copy_missing_relative_includes(
        source_inp=source_inp,
        generated_inp=generated_inp,
        target_dir=target_dir,
    )

    assert copied == [str((target_dir / "a.inc").resolve())]
    assert (target_dir / "a.inc").read_text(encoding="utf-8") == "A\n"


def test_run_local_solver_marks_abaqus_stdout_errors_as_failure(monkeypatch, tmp_path: Path):
    from services.model_update.analysis import solver_service

    monkeypatch.setattr(
        solver_service.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="Abaqus Error: Analysis Input File Processor exited with an error\n",
            stderr="",
        ),
    )
    monkeypatch.setattr(solver_service, "_collect_artifacts", lambda *args, **kwargs: {})

    payload = _run_local_solver(
        command=["abaqus", "job=demo", "input=demo.inp"],
        workdir=tmp_path,
        artifact_stem="demo",
        artifact_suffixes=(".inp", ".odb"),
        timeout_sec=None,
    )

    assert payload["returncode"] == 0
    assert payload["ok"] is False


def test_run_nastran_sol103_job_converts_bdf_without_running_solver(tmp_path: Path):
    input_bdf = _write_text(
        tmp_path / "model.bdf",
        """SOL 101
CEND
BEGIN BULK
GRID,1,,0.,0.,0.
ENDDATA
""",
    )

    result = run_nastran_sol103_job(
        input_bdf=str(input_bdf),
        settings={"dynamic.vectors": 8},
        run_solver=False,
    )

    output_bdf = Path(result["output_bdf"])
    assert result["workflow"] == "nastran_sol103_run"
    assert output_bdf.exists()
    assert "SOL 103" in output_bdf.read_text(encoding="utf-8")
    assert result["solver"] is None
    assert result["command_preview"][0].lower().endswith("nastran.exe") or result["command_preview"][0] == "nastran"


def test_build_sol103_controls_embed_geometry_true_uses_post_minus_1():
    controls, _ = build_sol103_controls(
        {
            "result.target": "OP2",
            "embed_geometry": True,
        }
    )

    assert "PARAM   POST          -1" in controls


def test_build_sol103_controls_embed_geometry_false_uses_post_minus_2():
    controls, _ = build_sol103_controls(
        {
            "result.target": "OP2",
            "embed_geometry": False,
        }
    )

    assert "PARAM   POST          -2" in controls


def test_build_abaqus_command_uses_basename_and_runtime_flags():
    command = _build_abaqus_command(
        abaqus="abq2024",
        inp_path=Path(r"D:\tmp\job with space.inp"),
        job_name="demo_job",
        cpus=8,
        interactive=True,
        extra_args=["memory=8gb"],
    )

    assert command == [
        "abq2024",
        "job=demo_job",
        "input=job with space.inp",
        "interactive",
        "cpus=8",
        "memory=8gb",
    ]


def test_build_abaqus_command_uses_configured_default(monkeypatch):
    from services.model_update.analysis import solver_service

    monkeypatch.setattr(
        solver_service,
        "resolve_abaqus_command",
        lambda abaqus=None: "C:/SIMULIA/Commands/abaqus.bat" if not abaqus else abaqus,
    )

    command = _build_abaqus_command(
        abaqus=None,
        inp_path=Path(r"D:\tmp\demo.inp"),
        job_name="demo_job",
        cpus=None,
        interactive=False,
        extra_args=None,
    )

    assert command == [
        "C:/SIMULIA/Commands/abaqus.bat",
        "job=demo_job",
        "input=demo.inp",
    ]


def test_build_nastran_command_keeps_relative_bdf_name():
    command = _build_nastran_command(
        nastran="nastran.exe",
        bdf_path=Path(r"D:\tmp\case_sol103.bdf"),
        extra_args=["scr=yes", "old=no"],
    )

    assert command == [
        "nastran.exe",
        "case_sol103.bdf",
        "scr=yes",
        "old=no",
    ]


def test_build_project_result_parse_options_omits_dsa_field_prefix():
    options = _build_project_result_parse_options(
        step="Step-1",
        frame=0,
        field_prefix="d_U_",
    )

    assert options["steps"] == ["Step-1"]
    assert options["frames"] == [0]
    assert options["invariants"] == "none"
    assert "field_prefix" not in options


def test_build_project_result_parse_options_keeps_non_dsa_field_prefix():
    options = _build_project_result_parse_options(
        step="Step-1",
        frame=0,
        field_prefix="SENS",
    )

    assert options["field_prefix"] == "SENS"


def test_run_nastran_sol103_and_store_modal_results_uses_generated_bdf_for_op2_import(monkeypatch):
    from services.model_update.analysis import solver_service

    captured = {}

    monkeypatch.setattr(
        solver_service,
        "run_nastran_sol103_job",
        lambda **kwargs: {
            "input_bdf": kwargs["input_bdf"],
            "output_bdf": "D:/demo/model_sol103.bdf",
            "solver": {
                "ok": True,
                "artifacts_summary": {"op2_files": ["D:/demo/model_sol103.op2"]},
            },
        },
    )
    monkeypatch.setattr(
        solver_service,
        "build_modal_import_payload",
        lambda **kwargs: captured.update(kwargs) or {"modes": [], "warnings": []},
    )
    monkeypatch.setattr(
        "services.model_update.analysis.inp_service.import_fe_modal_results",
        lambda project_id, overwrite, modes: {"project_id": project_id, "overwrite": overwrite, "mode_count": len(modes)},
    )

    result = run_nastran_sol103_and_store_modal_results(
        project_id=9,
        input_bdf="D:/demo/model.bdf",
        output_bdf="D:/demo/model_sol103.bdf",
    )

    assert Path(captured["op2_path"]) == Path("D:/demo/model_sol103.op2")
    assert Path(captured["bdf_path"]) == Path("D:/demo/model_sol103.bdf")
    assert Path(result["op2_path"]) == Path("D:/demo/model_sol103.op2")


def test_run_solver_and_parse_project_result_for_inp_skips_project_creation(monkeypatch, tmp_path: Path):
    from services.model_update.analysis import solver_service

    captured = {}
    project_dir = tmp_path / "1001"
    project_dir.mkdir()
    input_inp = _write_text(project_dir / "case_a.inp", "*Heading\n")

    monkeypatch.setattr(
        solver_service,
        "run_abaqus_job",
        lambda **kwargs: {
            "job_name": kwargs.get("job_name") or "case_a",
            "solver": {
                "ok": True,
                "artifacts": {"odb": "D:/demo/case_a.odb"},
            },
        },
    )
    monkeypatch.setattr(
        solver_service,
        "_submit_project_result_group_and_wait",
        lambda **kwargs: captured.update(kwargs) or {
            "result_group": "case_a_result",
            "status": "ready",
        },
    )
    monkeypatch.setattr(solver_service, "_project_workspace", lambda project_id: project_dir.resolve())

    result = run_solver_and_parse_project_result(
        project_id=1001,
        input_file="case_a.inp",
    )

    assert result["workflow"] == "run_and_parse"
    assert result["project_id"] == 1001
    assert result["project_status"] == "not_checked"
    assert captured["project_id"] == 1001
    assert "result_group" in result


def test_run_solver_and_parse_project_result_for_bdf_uploads_op2(monkeypatch, tmp_path: Path):
    from services.model_update.analysis import solver_service

    project_dir = tmp_path / "1002"
    project_dir.mkdir()
    input_bdf = _write_text(
        project_dir / "demo.bdf",
        """SOL 101
CEND
BEGIN BULK
GRID,1,,0.,0.,0.
ENDDATA
""",
    )
    captured = {}

    monkeypatch.setattr(
        solver_service,
        "run_nastran_sol103_job",
        lambda **kwargs: {
            "output_bdf": "D:/demo/demo_sol103.bdf",
            "solver": {
                "ok": True,
                "artifacts_summary": {"op2_files": ["D:/demo/demo_sol103.op2"]},
            },
        },
    )
    monkeypatch.setattr(
        solver_service,
        "_submit_generic_project_result_group_and_wait",
        lambda **kwargs: captured.update(kwargs) or {
            "result_group": "demo_result",
            "status": "ready",
        },
    )
    monkeypatch.setattr(solver_service, "_project_workspace", lambda project_id: project_dir.resolve())

    result = run_solver_and_parse_project_result(
        project_id=1002,
        input_file="demo.bdf",
    )

    assert result["source_type"] == "bdf"
    assert result["solver_type"] == "nastran"
    assert Path(result["artifacts"]["result_file"]) == Path("D:/demo/demo_sol103.op2")
    assert captured["project_id"] == 1002
    assert Path(captured["source_path"]) == Path("D:/demo/demo_sol103.op2")


def test_run_solver_and_parse_project_result_accepts_absolute_path(monkeypatch, tmp_path: Path):
    from services.model_update.analysis import solver_service

    input_inp = _write_text(tmp_path / "absolute_case.inp", "*Heading\n")
    captured = {}

    monkeypatch.setattr(
        solver_service,
        "run_abaqus_job",
        lambda **kwargs: {
            "job_name": kwargs.get("job_name") or "absolute_case",
            "solver": {
                "ok": True,
                "artifacts": {"odb": "D:/demo/absolute_case.odb"},
            },
        },
    )
    monkeypatch.setattr(
        solver_service,
        "_submit_project_result_group_and_wait",
        lambda **kwargs: captured.update(kwargs) or {
            "result_group": "absolute_case_result",
            "status": "ready",
        },
    )

    result = run_solver_and_parse_project_result(
        project_id=1003,
        input_file=str(input_inp),
    )

    assert result["source_type"] == "inp"
    assert Path(result["artifacts"]["input_file"]) == input_inp.resolve()
