"""ODB 字段白名单：deck_solver_dump 的匹配规则 + job_runner 的 --field-list 取值优先级。"""
import ast
import fnmatch
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def dump():
    # deck_solver_dump.py 导入即要求 odbAccess，这里只抽出纯函数单独执行
    src = (REPO / "src" / "l1" / "deck_solver_dump.py").read_text(encoding="utf-8")
    wanted = {"parse_field_list", "field_in_list", "split_region_field"}
    tree = ast.parse(src)
    mod = ast.Module(
        body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted],
        type_ignores=[])
    ns = {"fnmatch": fnmatch}
    exec(compile(mod, "deck_solver_dump_subset", "exec"), ns)
    return types.SimpleNamespace(**{k: ns[k] for k in wanted})


def test_parse_field_list_all_means_no_filter(dump):
    assert dump.parse_field_list(None) is None
    assert dump.parse_field_list("") is None
    assert dump.parse_field_list("all") is None
    assert dump.parse_field_list("U,*") is None
    assert dump.parse_field_list(" U , S ,") == ["U", "S"]


def test_field_in_list_matches_base_name_and_wildcards(dump):
    pats = dump.parse_field_list("U,S,E,LE,NT*,TEMP")
    for name in ("U", "S", "E", "LE", "NT11", "NT12", "TEMP"):
        assert dump.field_in_list(name, pats)
    for name in ("RF", "UR", "PE", "PEEQ", "STATUS", "SF", "EVOL", "d_U_T1"):
        assert not dump.field_in_list(name, pats)
    # 接触对后缀按基础名匹配
    region = "CPRESS   ASSEMBLY_S_SET-3_CNS_/ASSEMBLY_M_SURF-1"
    assert not dump.field_in_list(region, pats)
    assert dump.field_in_list(region, ["CPRESS"])


def test_runner_field_list_priority(monkeypatch):
    from src import job_runner as jr
    monkeypatch.setattr(jr, "ODB_FIELDS", "U,S")
    assert jr._odb_field_list() == "U,S"
    assert jr._odb_field_list({"fields": ["U", "NT*"]}) == "U,NT*"
    assert jr._odb_field_list({"fields": "all"}) == "all"
    assert jr._odb_field_list({"field_prefix": "SENS"}) == "all"
    monkeypatch.setattr(jr, "ODB_FIELDS", "")
    assert jr._odb_field_list() == "all"
