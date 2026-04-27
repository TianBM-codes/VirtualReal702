import json
from pathlib import Path

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

    def execute(self, sql, params=None):
        self.last_sql = " ".join(sql.split())
        self.last_params = params
        if self.last_sql.startswith("INSERT INTO t_mt_py_fem_selected_parameter"):
            self.inserted.append(params)

    def fetchall(self):
        if "FROM t_mt_py_fem_quantity_set_capability" in self.last_sql:
            return [dict(row) for row in self.capability_rows]
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
    monkeypatch.setattr(inp_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(inp_service, "get_connection", lambda: fake_conn)

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
    assert [item["parameter_name"] for item in result["created_parameters_preview"]] == ["E_GROUP#1", "E_GROUP#2"]
    assert fake_conn.committed is True
    assert fake_conn.rolled_back is False
    assert [params[2] for params in fake_conn.cursor_obj.inserted] == ["E_GROUP#1", "E_GROUP#2"]
    assert [params[10] for params in fake_conn.cursor_obj.inserted] == [1, 2]
    assert [params[12] for params in fake_conn.cursor_obj.inserted] == [100000.0, 100000.0]
    assert [params[13] for params in fake_conn.cursor_obj.inserted] == [300000.0, 300000.0]
    assert [params[14] for params in fake_conn.cursor_obj.inserted] == [2, 2]
    assert result["lower"] == 100000.0
    assert result["upper"] == 300000.0
    assert result["prob_id"] == 2


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
            "extra_json": json.dumps({"target_keys": ["PART::P1::2"]}),
        }
    ]
    fake_conn = _CreateParameterConnection(capability_rows, existing_rows=existing_rows)
    monkeypatch.setattr(inp_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(inp_service, "get_connection", lambda: fake_conn)

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
    result = inp_service._build_inp_parameter_options(
        supported_quantities=[
            {"quantity_code": "E", "quantity_name": "E", "enabled": 1, "sort_no": 1},
            {"quantity_code": "T", "quantity_name": "厚度", "enabled": 1, "sort_no": 2},
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
            "description": "杨氏模量",
            "level": "GLOBAL",
            "sets": ["SET_A"],
        },
        {
            "parameter_name": "E",
            "description": "杨氏模量",
            "level": "LOCAL",
            "sets": ["SET_A", "SET_B"],
        },
        {
            "parameter_name": "厚度",
            "description": "壳单元厚度",
            "level": "GLOBAL",
            "sets": ["SET_SHELL"],
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
                {"quantity_code": "T", "quantity_name": "厚度", "enabled": 1, "sort_no": 2},
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
    monkeypatch.setattr(inp_service, "get_connection", lambda: _InpOptionsConnection())

    result = inp_service.get_inp_parameter_options(101)

    assert result == [
        {
            "parameter_name": "E",
            "description": "杨氏模量",
            "level": "GLOBAL",
            "sets": ["SET_A"],
        },
        {
            "parameter_name": "厚度",
            "description": "壳单元厚度",
            "level": "GLOBAL",
            "sets": ["SET_B"],
        },
        {
            "parameter_name": "厚度",
            "description": "壳单元厚度",
            "level": "LOCAL",
            "sets": ["SET_B"],
        },
    ]
