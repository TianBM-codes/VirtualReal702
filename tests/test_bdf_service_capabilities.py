from types import SimpleNamespace

from services.model_update.analysis import inp_service
from services.model_update.importers import bdf_service


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
