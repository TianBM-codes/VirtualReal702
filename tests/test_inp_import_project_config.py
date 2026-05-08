import numpy as np

from services.model_update.analysis import inp_service


class _ImportCursor:
    def __init__(self):
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def close(self):
        return None


class _ImportConnection:
    def __init__(self):
        self.cursor_obj = _ImportCursor()
        self.committed = False
        self.rolled_back = False

    def cursor(self, dictionary=False):
        return self.cursor_obj

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        return None


def test_import_inp_catalog_saves_fem_dimensions(monkeypatch):
    fake_conn = _ImportConnection()
    saved = []

    monkeypatch.setattr(inp_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(inp_service, "get_connection", lambda: fake_conn)
    monkeypatch.setattr(inp_service, "_clear_import_inp_catalog_tables", lambda cursor, project_id: None)
    monkeypatch.setattr(inp_service, "_extract_legacy_material_rows", lambda model: {"overview_rows": [], "isotropic_rows": []})
    monkeypatch.setattr(inp_service, "_extract_legacy_property_rows", lambda model: {"overview_rows": [], "shell_rows": [], "beam_rows": []})
    monkeypatch.setattr(inp_service, "_extract_legacy_boundary_rows", lambda model: [])
    monkeypatch.setattr(inp_service, "extract_parameter_definition_rows", lambda model: [])
    monkeypatch.setattr(inp_service, "extract_parameter_target_rows", lambda model: [])
    monkeypatch.setattr(inp_service, "extract_design_response_rows", lambda model: [])
    monkeypatch.setattr(inp_service, "_extract_quantity_set_capabilities", lambda model: [])
    monkeypatch.setattr(
        inp_service,
        "_collect_global_nodes",
        lambda model: {
            "entries": [{"instance_name": "INST-1"}],
            "point_labels": [1, 2],
            "bbox_min": np.array([1.0, 2.0, 3.0], dtype=np.float64),
            "bbox_max": np.array([11.0, 7.0, 9.0], dtype=np.float64),
        },
    )
    monkeypatch.setattr(inp_service, "_save_octree_cache", lambda **kwargs: "demo.node_octree.npz")
    monkeypatch.setattr(inp_service, "safe_write_console_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        inp_service,
        "save_fem_model_dimensions",
        lambda **kwargs: saved.append(kwargs) or {"fem_model_dims": {"x": 10.0, "y": 5.0, "z": 6.0}},
    )

    result = inp_service.import_inp_catalog(
        file_path="demo.inp",
        project_id=101,
        model=object(),
    )

    assert fake_conn.committed is True
    assert fake_conn.rolled_back is False
    assert len(saved) == 1
    assert saved[0]["project_id"] == 101
    assert saved[0]["cursor"] is fake_conn.cursor_obj
    assert np.array_equal(saved[0]["bbox_min"], np.array([1.0, 2.0, 3.0], dtype=np.float64))
    assert np.array_equal(saved[0]["bbox_max"], np.array([11.0, 7.0, 9.0], dtype=np.float64))
    assert result["project_config"] == {"fem_model_dims": {"x": 10.0, "y": 5.0, "z": 6.0}}
