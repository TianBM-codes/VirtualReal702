from pathlib import Path

from src import job_runner


def test_normalize_op2_modal_import_config_defaults_to_enabled():
    cfg = job_runner._normalize_op2_modal_import_config(None)

    assert cfg["subcase_id"] is None
    assert cfg["mode_numbers"] is None
    assert cfg["instance_name"] is None
    assert cfg["part_name"] is None
    assert cfg["overwrite"] is True
    assert cfg["async_submit"] is True


def test_normalize_op2_modal_import_config_respects_explicit_disable():
    assert job_runner._normalize_op2_modal_import_config('{"modal_import": false}') is None
    assert job_runner._normalize_op2_modal_import_config('{"modal_import": {"enabled": false}}') is None


def test_normalize_op2_modal_import_config_keeps_user_overrides():
    cfg = job_runner._normalize_op2_modal_import_config(
        '{"modal_import": {"subcase_id": 12, "mode_numbers": [1, 3], "overwrite": false, "async_submit": false}}'
    )

    assert cfg["subcase_id"] == 12
    assert cfg["mode_numbers"] == [1, 3]
    assert cfg["overwrite"] is False
    assert cfg["async_submit"] is False


def test_resolve_model_update_bdf_path_prefers_workspace_filename(tmp_path: Path):
    workspace = tmp_path / "project_18"
    workspace.mkdir()
    expected = workspace / "fem15_py.bdf"
    expected.write_text("BEGIN BULK\n", encoding="utf-8")

    resolved = job_runner._resolve_model_update_bdf_path(
        r"D:\WorkSpace\Temp\fem15_py.bdf",
        str(workspace),
    )

    assert resolved == str(expected.resolve())


def test_resolve_model_update_bdf_path_falls_back_to_existing_raw_path(tmp_path: Path):
    workspace = tmp_path / "project_18"
    workspace.mkdir()
    raw_bdf = tmp_path / "outside_model.bdf"
    raw_bdf.write_text("BEGIN BULK\n", encoding="utf-8")

    resolved = job_runner._resolve_model_update_bdf_path(
        str(raw_bdf),
        str(workspace),
    )

    assert resolved == str(raw_bdf.resolve())
