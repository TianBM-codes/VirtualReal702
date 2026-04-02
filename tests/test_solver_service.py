from pathlib import Path

from services.model_update.analysis.solver_service import (
    _build_abaqus_command,
    _build_nastran_command,
    run_abaqus_adjoint_job,
    run_abaqus_sensitivity_job,
    run_nastran_sol103_job,
)


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
    assert result["command_preview"][0] == "abaqus"


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
    assert result["command_preview"][0] == "abaqus"


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
    assert result["workflow"] == "nastran_sol103"
    assert output_bdf.exists()
    assert "SOL 103" in output_bdf.read_text(encoding="utf-8")
    assert result["solver"] is None
    assert result["command_preview"][0] == "nastran"


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
