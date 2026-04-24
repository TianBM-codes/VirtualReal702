from pathlib import Path
from types import SimpleNamespace

from services.model_update.analysis import sensitivity_service


class _WriteCursor:
    def __init__(self):
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(str(sql).split()), params))

    def close(self):
        return None


class _WriteConnection:
    def __init__(self):
        self.cursor_obj = _WriteCursor()
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


def test_rebuild_selected_parameters_from_inp_replaces_project_rows(monkeypatch, tmp_path: Path):
    inp_path = tmp_path / "demo.inp"
    inp_path.write_text("*Heading\n", encoding="utf-8")

    fake_model = SimpleNamespace(
        parameters={
            "T1": SimpleNamespace(scalar_value=0.01),
        },
        design_parameters=[
            SimpleNamespace(name="T1", order=1),
        ],
    )
    fake_conn = _WriteConnection()

    monkeypatch.setattr(sensitivity_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(sensitivity_service, "parse_inp", lambda path: fake_model)
    monkeypatch.setattr(
        sensitivity_service,
        "build_parameter_target_map",
        lambda model: {
            "T1": [
                {
                    "parameter_name": "T1",
                    "set_name": "SHELL1",
                    "set_type": "ELSET",
                    "set_scope": "PART",
                    "instance_name": None,
                    "part_name": "PART-1",
                    "source_keyword": "SHELL SECTION",
                    "component_name": "THICKNESS",
                }
            ]
        },
    )
    monkeypatch.setattr(sensitivity_service, "get_connection", lambda: fake_conn)

    result = sensitivity_service._rebuild_selected_parameters_from_inp(
        project_id=1001,
        inp_path=str(inp_path),
    )

    assert result["selected_parameter_count"] == 1
    assert result["selected_parameters_preview"][0]["parameter_name"] == "T1"
    assert result["selected_parameters_preview"][0]["quantity_code"] == "H"
    assert fake_conn.committed is True
    assert fake_conn.rolled_back is False
    assert fake_conn.cursor_obj.executed[0] == (
        "DELETE FROM t_mt_py_fem_selected_parameter WHERE pid = %s",
        (1001,),
    )
    assert fake_conn.cursor_obj.executed[1][0].startswith(
        "INSERT INTO t_mt_py_fem_selected_parameter"
    )
    inserted = fake_conn.cursor_obj.executed[1][1]
    assert inserted[1] == "T1"
    assert inserted[2] == "T1"
    assert inserted[3] == "H"
    assert inserted[5] == "SHELL1"
    assert inserted[9] == "PART-1"
    assert inserted[11] == 0.01
