"""Run the real export functions with ODB boundaries stubbed, without Abaqus.

The executable driver imports odbAccess and replaces stdout at import time.
Select its function definitions to isolate those interpreter side effects.
"""
import ast
import os
from pathlib import Path
import shutil
import time
from types import SimpleNamespace

import numpy as np
import pytest


def _exporter():
    source = Path(__file__).resolve().parents[1] / "src/l1/abaqus_dump.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names = {"mkdirs", "npsave", "safe", "_canon_inst", "_fmt_t",
             "_copy_assembly_set_files", "dump_assembly", "dump_sets"}
    definitions = ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef)
                                  and n.name in names], type_ignores=[])
    calls = {"node": 0, "elem": 0}

    def nodes(_set):
        calls["node"] += 1
        return {"PART": [3, 1, 2]}

    def elems(_set):
        calls["elem"] += 1
        return {"PART": [8, 7]}

    namespace = {"os": os, "shutil": shutil, "time": time, "np": np,
                 "_set_nodes_by_inst": nodes, "_set_elems_by_inst": elems,
                 "get_instance_transform": lambda _: np.eye(4)}
    exec(compile(definitions, str(source), "exec"), namespace)
    instance = SimpleNamespace(partName="P", nodeSets={"part_nodes": SimpleNamespace(
        nodes=[SimpleNamespace(label=9), SimpleNamespace(label=2)])},
        elementSets={"part_elems": SimpleNamespace(elements=[SimpleNamespace(label=20)])})
    odb = SimpleNamespace(rootAssembly=SimpleNamespace(instances={"PART": instance},
        nodeSets={"NODE_SET": object()}, elementSets={"ELEM_SET": object()}))
    return namespace, calls, odb


def test_completed_assembly_export_is_reused_without_second_odb_scan(tmp_path):
    exporter, calls, odb = _exporter()
    stale = tmp_path / "assembly/asmsets/STALE/PART"
    stale.mkdir(parents=True)
    np.save(stale / "node_labels.npy", np.array([999], dtype=np.int32))
    meta = {}
    exporter["dump_assembly"](odb, str(tmp_path), meta)
    assert calls == {"node": 1, "elem": 1}
    exporter["dump_sets"](odb, str(tmp_path), meta)
    assert calls == {"node": 1, "elem": 1}
    assert "_assembly_set_files" not in meta
    assert not (tmp_path / "sets/asmsets/STALE").exists()
    for relative in ("NODE_SET/PART/node_labels.npy", "ELEM_SET/PART/elem_labels.npy"):
        original = tmp_path / "assembly/asmsets" / relative
        canonical = tmp_path / "sets/asmsets" / relative
        assert canonical.read_bytes() == original.read_bytes()
    np.testing.assert_array_equal(np.load(tmp_path / "sets/partsets/P/node_sets/part_nodes.npy"), [2, 9])
    np.testing.assert_array_equal(np.load(tmp_path / "sets/partsets/P/elem_sets/part_elems.npy"), [20])


@pytest.mark.parametrize("complete_marker", [False, True])
def test_standalone_or_missing_export_falls_back_to_odb(tmp_path, complete_marker):
    exporter, calls, odb = _exporter()
    meta = {"_assembly_set_files": ["NODE_SET/PART/node_labels.npy"]} if complete_marker else {}
    exporter["dump_sets"](odb, str(tmp_path), meta)
    assert calls == {"node": 1, "elem": 1}
    np.testing.assert_array_equal(np.load(tmp_path / "sets/asmsets/NODE_SET/PART/node_labels.npy"), [1, 2, 3])
    np.testing.assert_array_equal(np.load(tmp_path / "sets/asmsets/ELEM_SET/PART/elem_labels.npy"), [7, 8])
    assert "_assembly_set_files" not in meta


def test_previous_workspace_files_are_not_reused_without_current_export_marker(tmp_path):
    exporter, calls, odb = _exporter()
    stale = tmp_path / "assembly/asmsets/NODE_SET/PART"
    stale.mkdir(parents=True)
    np.save(stale / "node_labels.npy", np.array([999], dtype=np.int32))
    exporter["dump_sets"](odb, str(tmp_path), {})
    assert calls == {"node": 1, "elem": 1}
    np.testing.assert_array_equal(np.load(tmp_path / "sets/asmsets/NODE_SET/PART/node_labels.npy"), [1, 2, 3])


def test_preexisting_element_export_is_not_assumed_to_be_current(tmp_path):
    exporter, calls, odb = _exporter()
    stale = tmp_path / "assembly/asmsets/ELEM_SET/PART"
    stale.mkdir(parents=True)
    np.save(stale / "elem_labels.npy", np.array([999], dtype=np.int32))
    meta = {}
    exporter["dump_assembly"](odb, str(tmp_path), meta)
    exporter["dump_sets"](odb, str(tmp_path), meta)
    assert calls == {"node": 2, "elem": 2}
    np.testing.assert_array_equal(np.load(tmp_path / "sets/asmsets/ELEM_SET/PART/elem_labels.npy"), [7, 8])
