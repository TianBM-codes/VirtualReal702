from pathlib import Path
from types import SimpleNamespace

import numpy as np

from services.model_update.analysis import inp_service
from services.model_update.importers import bdf_service


def test_bdf_import_reports_stages_without_changing_storage(monkeypatch):
    calls = []
    cursor = SimpleNamespace(execute=lambda *args: None, close=lambda: None)
    conn = SimpleNamespace(cursor=lambda: cursor, commit=lambda: calls.append("committed"),
                           rollback=lambda: None, close=lambda: None)
    parser = SimpleNamespace(parse=lambda: None, GetDatabaseData=lambda: {})
    monkeypatch.setattr(bdf_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(bdf_service, "BDFParser", lambda path: parser)
    monkeypatch.setattr(bdf_service, "_build_bdf_property_set_capabilities", lambda p: [])
    monkeypatch.setattr(bdf_service, "_build_bdf_octree_node_data", lambda p: {
        "point_labels": np.asarray([1]), "bbox_min": np.zeros(3), "bbox_max": np.ones(3),
    })
    monkeypatch.setattr(bdf_service, "get_connection", lambda: conn)
    monkeypatch.setattr(bdf_service, "clear_fem_tables", lambda *args: None)
    monkeypatch.setattr(bdf_service, "write_capability_detail_h5", lambda *args, **kwargs: "existing-detail.h5")
    monkeypatch.setattr(bdf_service, "_save_octree_cache", lambda *args, **kwargs: "existing-index.npz")
    monkeypatch.setattr(bdf_service, "save_fem_model_dimensions", lambda **kwargs: {})
    monkeypatch.setattr(bdf_service, "upsert_project_config", lambda *args, **kwargs: {})
    monkeypatch.setattr(bdf_service, "safe_write_console_event", lambda *args, **kwargs: None)
    result = bdf_service.import_bdf_data("synthetic.bdf", 18, progress=lambda phase, message: calls.append(phase))
    assert calls == ["database_init", "read_bdf", "catalog", "node_data", "write_mysql", "write_detail",
                     "node_index", "register_index", "commit", "committed"]
    assert result["capability_detail_h5_path"] == "existing-detail.h5"
    assert Path(result["octree_cache_path"]).name == "existing-index.npz"


class _FakeElement:
    def __init__(self, pid):
        self.pid = pid

    def Pid(self):
        return self.pid


class _FakeProperty:
    def __init__(self, prop_type, mid, thickness=None):
        self.type = prop_type
        self.mid = mid
        self.t = thickness


class _FakeMaterial:
    def __init__(self, mat_type="MAT1", e=2.1e11, rho=7800.0):
        self.type = mat_type
        self.e = e
        self.rho = rho

    def raw_fields(self):
        return [self.type, self.rho, self.e]


def test_build_bdf_property_set_capabilities_adds_material_sets_with_rho():
    fake_bdf = SimpleNamespace(
        elements={
            1: _FakeElement(10),
            2: _FakeElement(10),
        },
        properties={
            10: _FakeProperty("PSHELL", 1001, thickness=0.01),
        },
        materials={
            1001: _FakeMaterial(),
        },
    )
    fake_parser = SimpleNamespace(bdf=fake_bdf)

    rows = bdf_service._build_bdf_property_set_capabilities(fake_parser)

    material_rows = [row for row in rows if row["set_name"] == "MAT1_1001"]
    property_rho_rows = [row for row in rows if row["set_name"] == "PROPERTY_10" and row["quantity_code"] == "RHO"]

    assert {(row["quantity_code"], row["set_type"]) for row in material_rows} == {
        ("E", "MATERIAL"),
        ("RHO", "MATERIAL"),
    }
    assert all(row["supports_global"] is True for row in material_rows)
    assert all(row["supports_local"] is True for row in material_rows)
    assert material_rows[1]["extra_json"]["material_id"] == 1001
    assert len(property_rho_rows) == 1


def test_build_inp_parameter_options_includes_rho_when_capability_exists():
    options = bdf_service._SUPPORTED_CORRECTION_QUANTITIES
    payload = [
        {
            "quantity_code": "RHO",
            "set_name": "MAT1_1001",
            "set_type": "MATERIAL",
            "set_scope": "PART",
            "instance_name": None,
            "part_name": "BDF_MODEL",
            "supports_global": 1,
            "supports_local": 1,
        }
    ]

    result = inp_service._build_inp_parameter_options(
        supported_quantities=options,
        quantity_set_capabilities=payload,
    )

    assert result == [
        {
            "parameter_name": "RHO",
            "description": "密度",
            "level": "GLOBAL",
            "sets": [{"rows": "MAT1_1001"}],
        }
    ]
