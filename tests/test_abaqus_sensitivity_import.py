import json
import sqlite3

import h5py
import numpy as np
import pytest

from src.l1.manifest_schema import MANIFEST_SCHEMA
from src.l3.services.abaqus_sensitivity_import_service import import_abaqus_sensitivity


def test_dedicated_import_preserves_sparse_labels_and_mesh(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    with sqlite3.connect(workspace / "manifest.db") as connection:
        connection.executescript(MANIFEST_SCHEMA)
        connection.execute("INSERT INTO instances(instance_name) VALUES ('PART-1-1')")
    mesh = workspace / "l1" / "geometry"
    mesh.mkdir(parents=True)
    (mesh / "PART-1-1.h5").write_bytes(b"existing mesh")
    options = {
        "steps": ["LC1"], "frames": "last", "sensitivity_prefix": "d_U_",
        "response_field": "U", "display_name": "Sensitivity",
    }

    def run(command):
        request = json.loads(open(command[command.index("--request") + 1]).read())
        assert request["step"] == "LC1"
        assert request["frame"] is None
        from pathlib import Path
        output = Path(command[command.index("--out") + 1])
        np.save(output / "labels.npy", [38295])
        np.save(output / "values.npy", np.array([[1e-12, 2e-12, 3e-12]], dtype=np.float32))
        metadata = {
            "step": "LC1", "original_frame": 7, "frame_value": 1.0,
            "description": "Final", "procedure": "STATIC",
            "fields": [{"name": "d_U_E1", "components": ["U1", "U2", "U3"], "blocks": [
                {"instance": "PART-1-1", "labels": "labels.npy", "values": "values.npy"}]}],
        }
        (output / "metadata.json").write_text(json.dumps(metadata))
        return 0, ""

    stats = import_abaqus_sensitivity(
        odb_path="model.odb", workspace=str(workspace), result_group="sensitivity_batch_1",
        options=options, abaqus="abaqus", run_command=run,
    )
    assert stats["original_frame"] == 7 and stats["stored_frame"] == 0
    assert (mesh / "PART-1-1.h5").read_bytes() == b"existing mesh"
    with sqlite3.connect(workspace / "manifest.db") as connection:
        path, source = connection.execute("SELECT file_path, source FROM result_files").fetchone()
        assert source == "odb"
        assert connection.execute("SELECT frame_idx,frame_value FROM frames").fetchone() == (0, 1.0)
        assert connection.execute("SELECT n_entities,h5_path,label_path FROM result_blocks").fetchone() == (
            1, "/NODAL/PART-1-1", "/NODAL/PART-1-1/labels")
    with h5py.File(workspace / path) as handle:
        np.testing.assert_array_equal(handle["NODAL/PART-1-1/labels"][:], [38295])
        assert handle["NODAL/PART-1-1/data"].shape == (1, 1, 3)
    from services.model_update.analysis.sensitivity_service import _workspace_result_label_map
    values = _workspace_result_label_map(
        str(workspace), step="LC1", field="d_U_E1", instance="PART-1-1",
        position="NODAL", frame=0, aggregation="max_abs", component_index=2,
        result_group="sensitivity_batch_1",
    )
    assert values["PART-1-1::38295"] == pytest.approx(3e-12)


def test_dedicated_import_failure_does_not_register_results(tmp_path):
    with sqlite3.connect(tmp_path / "manifest.db") as connection:
        connection.executescript(MANIFEST_SCHEMA)
        connection.execute("INSERT INTO instances(instance_name) VALUES ('PART-1-1')")
    with pytest.raises(RuntimeError, match="failed"):
        import_abaqus_sensitivity(
            odb_path="model.odb", workspace=str(tmp_path), result_group="sensitivity_batch_1",
            options={"frames": "last", "steps": ["LC1"], "sensitivity_prefix": "d_U_",
                     "response_field": "U"},
            abaqus="abaqus", run_command=lambda command: (1, "failed"),
        )
    with sqlite3.connect(tmp_path / "manifest.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM result_files").fetchone()[0] == 0


def test_job_runner_uses_dedicated_import_only_when_explicitly_requested(monkeypatch, tmp_path):
    from src import job_runner
    from src.l3.services import abaqus_sensitivity_import_service as importer

    source = tmp_path / "model.odb"
    source.touch()
    calls = []
    monkeypatch.setattr(job_runner, "_log_job", lambda *args, **kwargs: None)
    monkeypatch.setattr(job_runner, "_update_result_group_status",
                        lambda *args, **kwargs: calls.append(args[2]))
    monkeypatch.setattr(importer, "import_abaqus_sensitivity",
                        lambda **kwargs: calls.append("dedicated") or {})
    assert job_runner._run_result_group(
        "1", "sensitivity_batch_1", str(source),
        json.dumps({"extractor": "abaqus_sensitivity"}), str(tmp_path))
    assert calls == ["dedicated", "ready"]

    class GenericRoute(Exception):
        pass

    def generic_options(options):
        assert options == {}
        raise GenericRoute()

    monkeypatch.setattr(job_runner, "_project_used_inp_geom", lambda project: False)
    monkeypatch.setattr(job_runner, "_odb_field_list", generic_options)
    with pytest.raises(GenericRoute):
        job_runner._run_result_group("1", "uploaded_odb", str(source), "{}", str(tmp_path))
    assert calls == ["dedicated", "ready"]
