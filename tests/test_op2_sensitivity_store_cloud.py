from pathlib import Path

from services.model_update.importers import op2_service


def test_resolve_cloud_target_instances_falls_back_without_manifest(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = op2_service._resolve_cloud_target_instances(
        str(workspace),
        {"parameter_name": "E1"},
    )

    assert result == ["BDF_MODEL"]


def test_store_op2_sensitivity_cloud_reuses_store_and_writes_cloud(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "manifest.db").write_text("", encoding="utf-8")

    preview_payload = {
        "source": {
            "source_kind": "op2",
            "op2_path": "D:/demo/model.op2",
            "bdf_path": "D:/demo/model.bdf",
            "metadata_path": "D:/demo/model.bdf.sol200.json",
        },
        "response_rows": [{"response_name": "FREQ_MODE_1", "response_type": "FREQ", "mode_number": 1}],
        "parameter_columns": [{"parameter_name": "E1", "type": "E", "material_id": 2001}],
        "matrix_preview": [[0.25]],
        "warnings": [{"code": "demo"}],
    }

    monkeypatch.setattr(op2_service, "preview_op2_sensitivity", lambda **kwargs: preview_payload)

    from services.model_update.analysis import sensitivity_service

    def fake_persist(**kwargs):
        assert kwargs["matrix_payload"]["matrix"] == [[0.25]]
        return {
            "analysis_run_id": 8,
            "project_id": kwargs["project_id"],
            "batch_no": kwargs["batch_no"],
            "case_name": kwargs["case_name"],
            "response_count": 1,
            "parameter_count": 1,
            "point_count": 1,
        }

    monkeypatch.setattr(sensitivity_service, "_persist_sensitivity_matrix", fake_persist)
    monkeypatch.setattr(
        sensitivity_service,
        "_workspace_path",
        lambda path: str(Path(path).resolve()),
    )

    from services.model_update.analysis import project_path_service

    monkeypatch.setattr(project_path_service, "resolve_project_workspace", lambda project_id: str(workspace))

    captured = {}

    def fake_build_mappings(**kwargs):
        captured["mapping_workspace"] = kwargs["workspace"]
        captured["mapping_bdf_path"] = kwargs["bdf_path"]
        return [
            {
                "parameter_name": "E1",
                "type": "E",
                "material_id": 2001,
                "element_mapping": {
                    "target_kind": "cell",
                    "targets_by_scope": {"BDF_MODEL": [101, 102]},
                },
            }
        ]

    monkeypatch.setattr(op2_service, "_build_op2_parameter_columns_with_mappings", fake_build_mappings)

    def fake_write_cloud(**kwargs):
        captured["cloud_kwargs"] = kwargs
        return {
            "result_group": "sol200_cloud",
            "step": "Sensitivity",
            "field": "SENSITIVITY_CLOUD",
            "frame_count": 1,
            "components": ["SENSITIVITY"],
        }

    monkeypatch.setattr(op2_service, "_write_element_cloud_result_to_workspace", fake_write_cloud)

    result = op2_service.store_op2_sensitivity_cloud(
        project_id=5,
        batch_no="2",
        case_name="sol200_case",
        op2_path="D:/demo/model.op2",
        cloud_result_group="sol200_cloud",
    )

    assert result["analysis_run_id"] == 8
    assert result["workflow"] == "op2_sensitivity_store_cloud"
    assert result["workspace"] == str(workspace.resolve())
    assert result["warnings"] == [{"code": "demo"}]
    assert result["cloud_result"]["result_group"] == "sol200_cloud"
    assert captured["mapping_workspace"] == str(workspace.resolve())
    assert captured["mapping_bdf_path"] == "D:/demo/model.bdf"
    assert captured["cloud_kwargs"]["result_group"] == "sol200_cloud"
    assert captured["cloud_kwargs"]["matrix_payload"]["workspace"] == str(workspace.resolve())
    assert captured["cloud_kwargs"]["matrix_payload"]["parameter_columns"][0]["element_mapping"]["targets_by_scope"]["BDF_MODEL"] == [101, 102]
