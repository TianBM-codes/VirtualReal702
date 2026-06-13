import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.model_update.analysis import inp_service
from src.inp import parse_inp


class _CreateParameterCursor:
    def __init__(self, capability_rows, existing_rows=None):
        self.capability_rows = [dict(row) for row in capability_rows]
        self.existing_rows = [dict(row) for row in (existing_rows or [])]
        self.last_sql = ""
        self.last_params = None
        self.inserted = []
        self.executemany_batches = []

    def execute(self, sql, params=None):
        self.last_sql = " ".join(sql.split())
        self.last_params = params
        if self.last_sql.startswith("INSERT INTO t_mt_py_fem_selected_parameter"):
            self.inserted.append(params)

    def executemany(self, sql, seq_params):
        self.last_sql = " ".join(sql.split())
        batch = list(seq_params)
        self.executemany_batches.append(batch)
        if self.last_sql.startswith("INSERT INTO t_mt_py_fem_selected_parameter"):
            self.inserted.extend(batch)

    def fetchall(self):
        if "FROM t_mt_py_fem_quantity_set_capability" in self.last_sql:
            params = list(self.last_params or [])
            rows = [dict(row) for row in self.capability_rows]
            idx = 1
            quantity_codes = set()
            while idx < len(params) and str(params[idx]).upper() in {"E", "T", "H", "RHO"}:
                quantity_codes.add(str(params[idx]).upper())
                idx += 1
            if quantity_codes:
                rows = [row for row in rows if str(row.get("quantity_code") or "").upper() in quantity_codes]

            if "AND set_name = %s" in self.last_sql:
                set_name = params[idx]
                idx += 1
                rows = [row for row in rows if row.get("set_name") == set_name]
                if "AND set_type = %s" in self.last_sql:
                    set_type = params[idx]
                    idx += 1
                    rows = [row for row in rows if row.get("set_type") == set_type]
                if "AND set_scope = %s" in self.last_sql:
                    set_scope = params[idx]
                    idx += 1
                    rows = [row for row in rows if row.get("set_scope") == set_scope]
                if "AND instance_name = %s" in self.last_sql:
                    instance_name = params[idx]
                    idx += 1
                    rows = [row for row in rows if row.get("instance_name") == instance_name]
                if "AND part_name = %s" in self.last_sql:
                    part_name = params[idx]
                    rows = [row for row in rows if row.get("part_name") == part_name]
                return rows

            if "AND set_name IN (" in self.last_sql:
                set_scope = params[idx]
                scope_identity_value = params[idx + 1]
                set_names = set(params[idx + 2:])
                rows = [row for row in rows if row.get("set_scope") == set_scope and row.get("set_name") in set_names]
                if "AND instance_name <=> %s" in self.last_sql:
                    rows = [row for row in rows if row.get("instance_name") == scope_identity_value]
                if "AND part_name <=> %s" in self.last_sql:
                    rows = [row for row in rows if row.get("part_name") == scope_identity_value]
                return rows

            return rows
        if "FROM t_mt_py_fem_selected_parameter" in self.last_sql:
            return [dict(row) for row in self.existing_rows]
        return []

    def close(self):
        return None


class _CreateParameterConnection:
    def __init__(self, capability_rows, existing_rows=None):
        self.cursor_obj = _CreateParameterCursor(capability_rows, existing_rows=existing_rows)
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


def _patch_catalog_connection(monkeypatch, connection):
    monkeypatch.setattr(inp_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(inp_service, "get_connection", lambda: connection)
    monkeypatch.setattr(inp_service._catalog, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(inp_service._catalog, "get_connection", lambda: connection)


def test_extract_quantity_set_capabilities_marks_assembly_property_sets_as_global(tmp_path: Path):
    inp_path = tmp_path / "assembly_property_set.inp"
    inp_path.write_text(
        "\n".join(
            [
                "*Heading",
                "*Material, name=MAT1",
                "*Elastic",
                "210000, 0.3",
                "*Part, name=P1",
                "*Node",
                "1, 0, 0, 0",
                "2, 1, 0, 0",
                "3, 1, 1, 0",
                "4, 0, 1, 0",
                "*Element, type=S4, elset=SET_SHELL",
                "1, 1, 2, 3, 4",
                "*Elset, elset=SET_SHELL",
                "1",
                "*Shell Section, elset=SET_SHELL, material=MAT1",
                "0.01",
                "*End Part",
                "*Assembly, name=Assembly",
                "*Instance, name=INST_A, part=P1",
                "*End Instance",
                "*Elset, elset=SET_SHELL, instance=INST_A",
                "1",
                "*End Assembly",
                "",
            ]
        ),
        encoding="utf-8",
    )

    model = parse_inp(str(inp_path))
    capabilities = inp_service._extract_quantity_set_capabilities(model)

    assembly_rows = [
        row for row in capabilities
        if row["set_name"] == "SET_SHELL" and row["set_scope"] == "ASSEMBLY"
    ]

    assert {(row["quantity_code"], row["set_role"]) for row in assembly_rows} == {
        ("E", "PROPERTY_SET"),
        ("T", "PROPERTY_SET"),
    }
    assert all(row["supports_global"] is True for row in assembly_rows)
    assert all(row["supports_local"] is True for row in assembly_rows)


def test_create_optimization_parameter_local_expands_one_row_per_element(monkeypatch):
    capability_rows = [
        {
            "quantity_code": "E",
            "set_name": "SET_SHELL",
            "set_type": "ELSET",
            "set_scope": "PART",
            "instance_name": None,
            "part_name": "P1",
            "set_role": "PROPERTY_SET",
            "element_family": "SHELL",
            "section_type": "SHELL",
            "material_name": "MAT1",
            "member_count": 2,
            "supports_global": 1,
            "supports_local": 1,
            "current_value": 210000.0,
            "extra_json": json.dumps(
                {
                    "element_labels": [1, 2],
                    "target_keys": ["PART::P1::1", "PART::P1::2"],
                    "target_keys_by_label": {
                        "1": ["PART::P1::1"],
                        "2": ["PART::P1::2"],
                    },
                    "element_values": {
                        "1": 210000.0,
                        "2": 220000.0,
                    },
                }
            ),
        }
    ]
    fake_conn = _CreateParameterConnection(capability_rows)
    _patch_catalog_connection(monkeypatch, fake_conn)

    result = inp_service.create_optimization_parameter(
        project_id=101,
        quantity_code="E",
        lower=100000.0,
        upper=300000.0,
        prob_id=2,
        selection_mode="LOCAL",
        set_name="SET_SHELL",
        set_type="ELSET",
        set_scope="PART",
        part_name="P1",
        parameter_name="E_GROUP",
        scatter=0.15,
    )

    assert result["created_parameter_count"] == 2
    assert result["selection_mode"] == "LOCAL"
    assert [item["parameter_name"] for item in result["created_parameters_preview"]] == ["E_GROUP_EL1", "E_GROUP_EL2"]
    assert fake_conn.committed is True
    assert fake_conn.rolled_back is False
    assert len(fake_conn.cursor_obj.executemany_batches) == 1
    assert [params[2] for params in fake_conn.cursor_obj.inserted] == ["E_GROUP_EL1", "E_GROUP_EL2"]
    assert [params[10] for params in fake_conn.cursor_obj.inserted] == [1, 2]
    assert [params[12] for params in fake_conn.cursor_obj.inserted] == [100000.0, 100000.0]
    assert [params[13] for params in fake_conn.cursor_obj.inserted] == [300000.0, 300000.0]
    assert [params[14] for params in fake_conn.cursor_obj.inserted] == [2, 2]
    assert result["lower"] == 100000.0
    assert result["upper"] == 300000.0
    assert result["prob_id"] == 2


def test_create_optimization_parameter_autofills_scope_from_unique_set_name(monkeypatch):
    capability_rows = [
        {
            "quantity_code": "E",
            "set_name": "SET_SHELL",
            "set_type": "ELSET",
            "set_scope": "PART",
            "instance_name": None,
            "part_name": "P1",
            "set_role": "PROPERTY_SET",
            "element_family": "SHELL",
            "section_type": "SHELL",
            "material_name": "MAT1",
            "member_count": 2,
            "supports_global": 1,
            "supports_local": 1,
            "current_value": 210000.0,
            "extra_json": json.dumps(
                {
                    "element_labels": [1, 2],
                    "target_keys": ["PART::P1::1", "PART::P1::2"],
                    "target_keys_by_label": {
                        "1": ["PART::P1::1"],
                        "2": ["PART::P1::2"],
                    },
                    "element_values": {
                        "1": 210000.0,
                        "2": 220000.0,
                    },
                }
            ),
        }
    ]
    fake_conn = _CreateParameterConnection(capability_rows)
    _patch_catalog_connection(monkeypatch, fake_conn)

    result = inp_service.create_optimization_parameter(
        project_id=101,
        quantity_code="E",
        lower=100000.0,
        upper=300000.0,
        selection_mode="GLOBAL",
        set_name="SET_SHELL",
        parameter_name="E_GROUP",
    )

    assert result["created_parameter_count"] == 1
    assert result["set_type"] == "ELSET"
    assert result["set_scope"] == "PART"
    assert result["part_name"] == "P1"
    inserted = fake_conn.cursor_obj.inserted[0]
    assert inserted[6] == "ELSET"
    assert inserted[7] == "PART"
    assert inserted[9] == "P1"


def test_create_optimization_parameter_requires_disambiguation_for_duplicate_set_name(monkeypatch):
    capability_rows = [
        {
            "quantity_code": "E",
            "set_name": "SET_SHELL",
            "set_type": "ELSET",
            "set_scope": "PART",
            "instance_name": None,
            "part_name": "P1",
            "set_role": "PROPERTY_SET",
            "element_family": "SHELL",
            "section_type": "SHELL",
            "material_name": "MAT1",
            "member_count": 1,
            "supports_global": 1,
            "supports_local": 1,
            "current_value": 210000.0,
            "extra_json": json.dumps(
                {
                    "element_labels": [1],
                    "target_keys": ["PART::P1::1"],
                    "target_keys_by_label": {"1": ["PART::P1::1"]},
                    "element_values": {"1": 210000.0},
                }
            ),
        },
        {
            "quantity_code": "E",
            "set_name": "SET_SHELL",
            "set_type": "ELSET",
            "set_scope": "ASSEMBLY",
            "instance_name": "INST-1",
            "part_name": "P1",
            "set_role": "PROPERTY_SET",
            "element_family": "SHELL",
            "section_type": "SHELL",
            "material_name": "MAT1",
            "member_count": 1,
            "supports_global": 1,
            "supports_local": 1,
            "current_value": 210000.0,
            "extra_json": json.dumps(
                {
                    "element_labels": [1],
                    "target_keys": ["INST::INST-1::1"],
                    "target_keys_by_label": {"1": ["INST::INST-1::1"]},
                    "element_values": {"1": 210000.0},
                }
            ),
        },
    ]
    fake_conn = _CreateParameterConnection(capability_rows)
    _patch_catalog_connection(monkeypatch, fake_conn)

    with pytest.raises(ValueError, match="multiple quantity/set capabilities matched"):
        inp_service.create_optimization_parameter(
            project_id=101,
            quantity_code="E",
            lower=100000.0,
            upper=300000.0,
            selection_mode="GLOBAL",
            set_name="SET_SHELL",
            parameter_name="E_GROUP",
        )


def test_create_optimization_parameter_manual_local_creates_virtual_set(monkeypatch):
    fake_conn = _CreateParameterConnection([])
    _patch_catalog_connection(monkeypatch, fake_conn)

    result = inp_service.create_optimization_parameter(
        project_id=101,
        quantity_code="H",
        lower=1.0,
        upper=3.0,
        prob_id=2,
        selection_mode="LOCAL",
        parameter_name="T_LOCAL_TEST",
        element_labels=[101, 102],
        current_value=2.5,
    )

    assert result["selection_mode"] == "LOCAL"
    assert result["manual_set_created"] is True
    assert result["set_name"].startswith("MANUAL_H_LOCAL_")
    assert result["created_parameter_count"] == 2
    assert [item["parameter_name"] for item in result["created_parameters_preview"]] == [
        "T_LOCAL_TEST_EL101",
        "T_LOCAL_TEST_EL102",
    ]
    assert [params[10] for params in fake_conn.cursor_obj.inserted] == [101, 102]
    assert fake_conn.committed is True


def test_create_optimization_parameter_manual_global_keeps_group_parameter(monkeypatch):
    fake_conn = _CreateParameterConnection([])
    _patch_catalog_connection(monkeypatch, fake_conn)

    result = inp_service.create_optimization_parameter(
        project_id=101,
        quantity_code="H",
        lower=1.0,
        upper=3.0,
        selection_mode="GLOBAL",
        parameter_name="T_GLOBAL_TEST",
        element_labels=[101, 102, 103],
        current_value=2.5,
    )

    assert result["selection_mode"] == "GLOBAL"
    assert result["manual_set_created"] is True
    assert result["created_parameter_count"] == 1
    assert result["created_parameters_preview"][0]["parameter_name"] == "T_GLOBAL_TEST"
    inserted = fake_conn.cursor_obj.inserted[0]
    assert inserted[10] is None
    assert '"element_labels": [101, 102, 103]' in inserted[18]


def test_create_optimization_parameter_manual_local_auto_resolves_current_value(monkeypatch):
    capability_rows = [
        {
            "quantity_code": "T",
            "set_name": "SET_SHELL",
            "set_type": "ELSET",
            "set_scope": "PART",
            "instance_name": None,
            "part_name": "P1",
            "set_role": "PROPERTY_SET",
            "element_family": "SHELL",
            "section_type": "SHELL",
            "material_name": "MAT1",
            "member_count": 3,
            "supports_global": 1,
            "supports_local": 1,
            "current_value": None,
            "extra_json": json.dumps(
                {
                    "element_labels": [101, 102, 103],
                    "element_values": {
                        "101": 0.0021,
                        "102": 0.0022,
                        "103": 0.0023,
                    },
                }
            ),
        }
    ]
    fake_conn = _CreateParameterConnection(capability_rows)
    _patch_catalog_connection(monkeypatch, fake_conn)

    result = inp_service.create_optimization_parameter(
        project_id=101,
        quantity_code="H",
        lower=0.001,
        upper=0.003,
        selection_mode="LOCAL",
        parameter_name="T_LOCAL_AUTO",
        element_labels=[101, 102, 103],
    )

    assert result["created_parameter_count"] == 3
    assert [item["current_value"] for item in result["created_parameters_preview"]] == [0.0021, 0.0022, 0.0023]


def test_create_optimization_parameter_manual_global_requires_unique_auto_value(monkeypatch):
    capability_rows = [
        {
            "quantity_code": "T",
            "set_name": "SET_SHELL",
            "set_type": "ELSET",
            "set_scope": "PART",
            "instance_name": None,
            "part_name": "P1",
            "set_role": "PROPERTY_SET",
            "element_family": "SHELL",
            "section_type": "SHELL",
            "material_name": "MAT1",
            "member_count": 2,
            "supports_global": 1,
            "supports_local": 1,
            "current_value": None,
            "extra_json": json.dumps(
                {
                    "element_labels": [101, 102],
                    "element_values": {
                        "101": 0.0021,
                        "102": 0.0035,
                    },
                }
            ),
        }
    ]
    fake_conn = _CreateParameterConnection(capability_rows)
    _patch_catalog_connection(monkeypatch, fake_conn)

    with pytest.raises(ValueError, match="manual GLOBAL parameter spans multiple current values"):
        inp_service.create_optimization_parameter(
            project_id=101,
            quantity_code="H",
            lower=0.001,
            upper=0.004,
            selection_mode="GLOBAL",
            parameter_name="T_GLOBAL_AUTO",
            element_labels=[101, 102],
        )


def test_create_optimization_parameter_rejects_same_quantity_overlap(monkeypatch):
    capability_rows = [
        {
            "quantity_code": "E",
            "set_name": "SET_SHELL",
            "set_type": "ELSET",
            "set_scope": "PART",
            "instance_name": None,
            "part_name": "P1",
            "set_role": "PROPERTY_SET",
            "element_family": "SHELL",
            "section_type": "SHELL",
            "material_name": "MAT1",
            "member_count": 2,
            "supports_global": 1,
            "supports_local": 1,
            "current_value": 210000.0,
            "extra_json": json.dumps(
                {
                    "element_labels": [1, 2],
                    "target_keys": ["PART::P1::1", "PART::P1::2"],
                    "target_keys_by_label": {
                        "1": ["PART::P1::1"],
                        "2": ["PART::P1::2"],
                    },
                    "element_values": {
                        "1": 210000.0,
                        "2": 220000.0,
                    },
                }
            ),
        }
    ]
    existing_rows = [
        {
            "quantity_code": "E",
            "set_name": "SET_EXISTING",
            "set_type": "ELSET",
            "set_scope": "PART",
            "instance_name": None,
            "part_name": "P1",
            "element_label": 2,
        }
    ]
    fake_conn = _CreateParameterConnection(capability_rows, existing_rows=existing_rows)
    _patch_catalog_connection(monkeypatch, fake_conn)

    with pytest.raises(ValueError, match="overlaps with an existing parameter"):
        inp_service.create_optimization_parameter(
            project_id=101,
            quantity_code="E",
            lower=100000.0,
            upper=300000.0,
            selection_mode="LOCAL",
            set_name="SET_SHELL",
            set_type="ELSET",
            set_scope="PART",
            part_name="P1",
            parameter_name="E_GROUP",
        )

    assert fake_conn.committed is False
    assert fake_conn.rolled_back is True


def test_create_optimization_parameter_rejects_overlap_with_existing_global_parameter(monkeypatch):
    capability_rows = [
        {
            "quantity_code": "E",
            "set_name": "SET_SHELL",
            "set_type": "ELSET",
            "set_scope": "PART",
            "instance_name": None,
            "part_name": "P1",
            "set_role": "PROPERTY_SET",
            "element_family": "SHELL",
            "section_type": "SHELL",
            "material_name": "MAT1",
            "member_count": 2,
            "supports_global": 1,
            "supports_local": 1,
            "current_value": 210000.0,
            "extra_json": json.dumps(
                {
                    "element_labels": [1, 2],
                    "target_keys": ["PART::P1::1", "PART::P1::2"],
                    "target_keys_by_label": {
                        "1": ["PART::P1::1"],
                        "2": ["PART::P1::2"],
                    },
                    "element_values": {
                        "1": 210000.0,
                        "2": 220000.0,
                    },
                }
            ),
        },
        {
            "quantity_code": "E",
            "set_name": "SET_EXISTING",
            "set_type": "ELSET",
            "set_scope": "PART",
            "instance_name": None,
            "part_name": "P1",
            "set_role": "PROPERTY_SET",
            "element_family": "SHELL",
            "section_type": "SHELL",
            "material_name": "MAT1",
            "member_count": 2,
            "supports_global": 1,
            "supports_local": 1,
            "current_value": 210000.0,
            "extra_json": json.dumps(
                {
                    "element_labels": [2, 3],
                    "target_keys": ["PART::P1::2", "PART::P1::3"],
                    "target_keys_by_label": {
                        "2": ["PART::P1::2"],
                        "3": ["PART::P1::3"],
                    },
                    "element_values": {
                        "2": 210000.0,
                        "3": 220000.0,
                    },
                }
            ),
        },
    ]
    existing_rows = [
        {
            "quantity_code": "E",
            "set_name": "SET_EXISTING",
            "set_type": "ELSET",
            "set_scope": "PART",
            "instance_name": None,
            "part_name": "P1",
            "element_label": None,
        }
    ]
    fake_conn = _CreateParameterConnection(capability_rows, existing_rows=existing_rows)
    _patch_catalog_connection(monkeypatch, fake_conn)

    with pytest.raises(ValueError, match="overlaps with an existing parameter"):
        inp_service.create_optimization_parameter(
            project_id=101,
            quantity_code="E",
            lower=100000.0,
            upper=300000.0,
            selection_mode="LOCAL",
            set_name="SET_SHELL",
            set_type="ELSET",
            set_scope="PART",
            part_name="P1",
            parameter_name="E_GROUP",
        )

    assert fake_conn.committed is False
    assert fake_conn.rolled_back is True


def test_build_import_inp_catalog_summary_returns_simplified_parameter_level_sets():
    e_description = inp_service._quantity_description("E", "E")
    t_description = inp_service._quantity_description("T", "T")
    result = inp_service._build_inp_parameter_options(
        supported_quantities=[
            {"quantity_code": "E", "quantity_name": "E", "enabled": 1, "sort_no": 1},
            {"quantity_code": "T", "quantity_name": "鍘氬害", "enabled": 1, "sort_no": 2},
        ],
        quantity_set_capabilities=[
            {
                "quantity_code": "E",
                "set_name": "SET_A",
                "set_scope": "PART",
                "set_type": "ELSET",
                "instance_name": None,
                "part_name": "P1",
                "supports_global": 1,
                "supports_local": 1,
            },
            {
                "quantity_code": "E",
                "set_name": "SET_A",
                "set_scope": "ASSEMBLY",
                "set_type": "ELSET",
                "instance_name": "INST-1",
                "part_name": "P1",
                "supports_global": 1,
                "supports_local": 0,
            },
            {
                "quantity_code": "E",
                "set_name": "SET_B",
                "set_scope": "PART",
                "set_type": "ELSET",
                "instance_name": None,
                "part_name": "P1",
                "supports_global": 0,
                "supports_local": 1,
            },
            {
                "quantity_code": "T",
                "set_name": "SET_SHELL",
                "set_scope": "PART",
                "set_type": "ELSET",
                "instance_name": None,
                "part_name": "P2",
                "supports_global": 1,
                "supports_local": 0,
            },
        ],
    )

    assert result == [
        {
            "parameter_name": "E",
            "description": e_description,
            "level": "GLOBAL",
            "sets": [{"rows": "SET_A"}, {"rows": "SET_B"}],
        },
        {
            "parameter_name": "T",
            "description": t_description,
            "level": "GLOBAL",
            "sets": [{"rows": "SET_SHELL"}],
        },
    ]


class _InpOptionsCursor:
    def __init__(self):
        self.last_sql = ""
        self.last_params = None

    def execute(self, sql, params=None):
        self.last_sql = " ".join(sql.split())
        self.last_params = params

    def fetchall(self):
        if "FROM t_mt_py_fem_supported_quantity" in self.last_sql:
            return [
                {"quantity_code": "E", "quantity_name": "E", "enabled": 1, "sort_no": 1},
                {"quantity_code": "T", "quantity_name": "鍘氬害", "enabled": 1, "sort_no": 2},
            ]
        if "FROM t_mt_py_fem_quantity_set_capability" in self.last_sql:
            return [
                {"quantity_code": "E", "set_name": "SET_A", "set_scope": "PART", "set_type": "ELSET", "instance_name": None, "part_name": "P1", "supports_global": 1, "supports_local": 0},
                {"quantity_code": "T", "set_name": "SET_B", "set_scope": "PART", "set_type": "ELSET", "instance_name": None, "part_name": "P1", "supports_global": 1, "supports_local": 1},
            ]
        return []

    def close(self):
        return None


class _InpOptionsConnection:
    def __init__(self):
        self.cursor_obj = _InpOptionsCursor()

    def cursor(self, dictionary=False):
        return self.cursor_obj

    def close(self):
        return None


def test_get_inp_parameter_options_reads_from_database(monkeypatch):
    e_description = inp_service._quantity_description("E", "E")
    t_description = inp_service._quantity_description("T", "T")
    monkeypatch.setattr(inp_service, "get_connection", lambda: _InpOptionsConnection())
    monkeypatch.setattr(inp_service._catalog, "get_connection", lambda: _InpOptionsConnection())

    result = inp_service.get_inp_parameter_options(101)

    assert result == [
        {
            "parameter_name": "E",
            "description": e_description,
            "level": "GLOBAL",
            "sets": [{"rows": "SET_A"}],
        },
        {
            "parameter_name": "T",
            "description": t_description,
            "level": "GLOBAL",
            "sets": [{"rows": "SET_B"}],
        },
    ]


def test_extract_legacy_material_rows_accepts_iso_elastic_type_alias():
    model = SimpleNamespace(
        materials={
            "MAT1": SimpleNamespace(
                elastic=SimpleNamespace(elastic_type="ISO", data=[(210000.0, 0.3)]),
                density_data=[(7.85e-09,)],
            )
        }
    )

    result = inp_service._extract_legacy_material_rows(model)

    assert result["overview_rows"] == [
        {
            "id": 1,
            "type": "ISO",
            "name": "MAT1",
        }
    ]
    assert result["isotropic_rows"] == [
        {
            "id": 1,
            "rho": 7.85e-09,
            "e": 210000.0,
            "nu": 0.3,
            "ge": 0.0,
        }
    ]


def test_extract_legacy_property_rows_includes_shell_element_set(tmp_path: Path):
    inp_path = tmp_path / "shell_property.inp"
    inp_path.write_text(
        "\n".join(
            [
                "*Heading",
                "*Material, name=MAT1",
                "*Elastic",
                "210000, 0.3",
                "*Part, name=P1",
                "*Node",
                "1, 0, 0, 0",
                "2, 1, 0, 0",
                "3, 1, 1, 0",
                "4, 0, 1, 0",
                "*Element, type=S4, elset=SET_SHELL",
                "1, 1, 2, 3, 4",
                "*Shell Section, elset=SET_SHELL, material=MAT1",
                "0.01",
                "*End Part",
            ]
        ),
        encoding="utf-8",
    )

    model = parse_inp(str(inp_path))
    result = inp_service._extract_legacy_property_rows(model)

    assert result["shell_rows"] == [
        {
            "id": 1,
            "thickness": 0.01,
            "nsm": 0.0,
            "theta": 0.0,
            "element_set": "SET_SHELL",
        }
    ]


def test_build_inp_parameter_options_uses_canonical_t_name_for_legacy_h_rows():
    description = inp_service._quantity_description("T", "T")
    result = inp_service._build_inp_parameter_options(
        supported_quantities=[
            {"quantity_code": "H", "quantity_name": "H", "enabled": 1, "sort_no": 1},
            {"quantity_code": "T", "quantity_name": "T", "enabled": 1, "sort_no": 2},
        ],
        quantity_set_capabilities=[
            {
                "quantity_code": "H",
                "set_name": "SET_H",
                "set_scope": "PART",
                "set_type": "ELSET",
                "instance_name": None,
                "part_name": "P1",
                "supports_global": 1,
                "supports_local": 1,
            },
            {
                "quantity_code": "T",
                "set_name": "SET_T",
                "set_scope": "PART",
                "set_type": "ELSET",
                "instance_name": None,
                "part_name": "P1",
                "supports_global": 1,
                "supports_local": 0,
            },
        ],
    )

    assert result == [
        {
            "parameter_name": "T",
            "description": description,
            "level": "GLOBAL",
            "sets": [{"rows": "SET_H"}, {"rows": "SET_T"}],
        },
    ]
