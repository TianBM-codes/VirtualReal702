"""color-code 分类缓存 + 向量化回归测试。

按属性 / 截面 / 单元类型等分类着色时，"每个面属于哪一类"只取决于模型几何，
导入后不再变化 → 面分类编码 (codes, vals) 走统一缓存（内存 + 工作区磁盘
model 桶，elset 只进内存）；用户改色 / 改名（legend overrides）每次请求现查，
立即生效。逐面 Python 循环全部改为 numpy，结果与旧实现逐项一致。
"""
import os

import h5py
import numpy as np
import pytest

from src.inp import parse_inp
from src.inp.exporter import export_l1
from src.l3.infra.manifest_repo import ManifestRepo
from src.l3.services import cache_store
from src.l3.services import color_service as C

_INP = (
    "*Part, name=PA\n"
    "*Node\n"
    + "".join(f"{i}, {i}.0, 0.0, 0.0\n" for i in range(1, 25))
    + "*Element, type=C3D8R\n"
    "1, 1, 2, 3, 4, 5, 6, 7, 8\n"
    "2, 9, 10, 11, 12, 13, 14, 15, 16\n"
    "3, 17, 18, 19, 20, 21, 22, 23, 24\n"
    "*Elset, elset=SetA\n1, 2\n"
    "*Elset, elset=SetB\n3,\n"
    "*Solid Section, elset=SetA, material=Steel\n1.,\n"
    "*Solid Section, elset=SetB, material=Alu\n1.,\n"
    "*End Part\n"
    "*Assembly, name=Assembly\n"
    "*Instance, name=PA-1, part=PA\n*End Instance\n"
    "*End Assembly\n"
    "*Material, name=Steel\n*Elastic\n210000., 0.3\n"
    "*Material, name=Alu\n*Elastic\n70000., 0.3\n"
)


class _FakeIdx:
    def __init__(self, ws):
        self.workspace = ws
        self.odb_id = "t"
        self.is_render_ready = True
        self.legend_scan_cache = {}
        self.vtx_tri_idx = {}
        et, row = C._full_element_arrays(self, "PA-1")
        # 每个单元 2 个表面面，打乱顺序
        order = np.array([2, 0, 1, 0, 2, 1])
        self.source_elem_etype = {"PA-1": et[order]}
        self.render_source_elem_row = {"PA-1": row[order]}
        self.averaging_data = {"PA-1": {
            "elem_etype": et, "elem_row": row,
            "default_domain_id": np.array([7, 7, 3]),
        }}


@pytest.fixture(autouse=True)
def _clean():
    cache_store.clear_memory()
    yield
    cache_store.clear_memory()


def _build(tmp_path):
    inp = os.path.join(str(tmp_path), "m.inp")
    with open(inp, "w") as fh:
        fh.write(_INP)
    ws = os.path.join(str(tmp_path), "ws")
    os.makedirs(ws, exist_ok=True)
    export_l1(parse_inp(inp), ws)
    return _FakeIdx(ws)


def _boom(*args, **kwargs):
    raise AssertionError("cache miss: h5py.File was opened")


def test_material_codes_and_legend(tmp_path):
    idx = _build(tmp_path)
    colors, legend = C.get_color_code(idx, "PA-1", "material")
    names = [e["legend_key"] for e in legend]
    assert names == ["Alu", "Steel"]          # 按面首次出现顺序（第一个面是单元 3）
    assert colors.shape == (6 * 3, 3)


def test_second_call_hits_memory(tmp_path, monkeypatch):
    idx = _build(tmp_path)
    c1, l1 = C.get_color_code(idx, "PA-1", "material")
    monkeypatch.setattr(h5py, "File", _boom)
    c2, l2 = C.get_color_code(idx, "PA-1", "material")
    assert np.array_equal(c1, c2) and l1 == l2


def test_codes_survive_memory_clear_via_disk(tmp_path, monkeypatch):
    idx = _build(tmp_path)
    et = idx.source_elem_etype["PA-1"]
    row = idx.render_source_elem_row["PA-1"]
    codes1, vals1 = C._face_codes(idx, "PA-1", "material", None, et, row)
    assert list((tmp_path / "ws" / "l3_cache" / "model" / "arrays").glob("*.npz"))

    cache_store.clear_memory()
    monkeypatch.setattr(h5py, "File", _boom)
    codes2, vals2 = C._face_codes(idx, "PA-1", "material", None, et, row)
    assert np.array_equal(codes1, codes2) and vals1 == vals2


def test_elset_codes_not_written_to_disk(tmp_path):
    idx = _build(tmp_path)
    et = idx.source_elem_etype["PA-1"]
    row = idx.render_source_elem_row["PA-1"]
    C._face_codes(idx, "PA-1", "elset", ["PA-1.SETA"], et, row)
    assert not (tmp_path / "ws" / "l3_cache" / "model").exists()


def test_legend_override_applies_immediately(tmp_path):
    """分类编码已缓存时，用户改色 / 改名仍立即生效。"""
    idx = _build(tmp_path)
    _, legend = C.get_color_code(idx, "PA-1", "material")
    key = legend[0]["legend_key"]
    ManifestRepo(idx.workspace).set_legend_overrides("PA-1", "material", [
        {"legend_key": key, "display_name": "改名", "color_r": 1.0, "color_g": 0.0,
         "color_b": 0.0},
    ])
    colors, legend2 = C.get_color_code(idx, "PA-1", "material")
    first = next(e for e in legend2 if e["legend_key"] == key)
    assert first["name"] == "改名" and (first["r"], first["g"], first["b"]) == (1.0, 0.0, 0.0)
    assert any(np.allclose(c, [1.0, 0.0, 0.0]) for c in colors)


def test_warm_color_codes_then_no_h5(tmp_path, monkeypatch):
    idx = _build(tmp_path)
    n = C.warm_color_codes(idx)
    assert n >= 3            # etype / section / material / section_type / section_assignment
    et = idx.source_elem_etype["PA-1"]
    row = idx.render_source_elem_row["PA-1"]
    monkeypatch.setattr(h5py, "File", _boom)
    for scheme in ("etype", "section", "material"):
        C._face_codes(idx, "PA-1", scheme, None, et, row)


def test_region_face_mask_uses_codes(tmp_path):
    idx = _build(tmp_path)
    _, legend = C.get_color_code(idx, "PA-1", "section")
    labels = C._labels_from_section_id(
        idx, "PA-1", idx.source_elem_etype["PA-1"], idx.render_source_elem_row["PA-1"])
    for e in legend:
        mask = C.region_face_mask(idx, "PA-1", "section", e["legend_key"])
        assert mask.tolist() == [lab == e["legend_key"] for lab in labels]
    assert not C.region_face_mask(idx, "PA-1", "section", "NOPE").any()


def test_domain_per_face_matches_dict_reference():
    """向量化查表与旧的 {(etype, row): domain} 字典逐面查找一致（含重复键、缺失键）。"""
    rng = np.random.default_rng(3)
    names = np.array([b"S4R", b"S3", b"C3D8R"], dtype="S8")
    ne = 500
    a_et = names[rng.integers(0, 3, ne)]
    a_row = rng.integers(0, 200, ne)
    a_dom = rng.integers(0, 20, ne)
    f_et = names[rng.integers(0, 3, 2000)].astype("S16")
    f_row = rng.integers(0, 260, 2000)

    ref_lookup = {}
    for e, r, d in zip(a_et, a_row, a_dom):
        ref_lookup[(bytes(e).rstrip(b"\x00"), int(r))] = int(d)
    ref = [ref_lookup.get((bytes(e).rstrip(b"\x00"), int(r)), -1)
           for e, r in zip(f_et, f_row)]

    got = C._domain_per_face(
        {"elem_etype": a_et, "elem_row": a_row, "default_domain_id": a_dom}, f_et, f_row)
    assert got.tolist() == ref


def test_compact_codes_matches_dict_fromkeys():
    rng = np.random.default_rng(5)
    vals = ["a", "b", "c", "b", "d"]          # 含重复字符串
    codes = rng.integers(0, 5, 300)
    labels = [vals[c] for c in codes]
    new_codes, new_vals = C._compact_codes(codes, vals)
    assert new_vals == list(dict.fromkeys(labels))
    assert [new_vals[c] for c in new_codes] == labels
