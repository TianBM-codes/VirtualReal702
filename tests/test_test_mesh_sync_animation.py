"""testMesh syncAnimation:试验网格 / FEM 模型同步动画数据的单元测试(不连 MySQL)。"""
import numpy as np
import pytest

import src.modal_service.service as svc


@pytest.fixture
def stub_test_mesh(monkeypatch):
    """两节点一线元的试验网格 + 一阶振型(z 向,最大复幅值 2.0)。"""
    monkeypatch.setattr(svc, "_db_node_elements", lambda pid: {
        "node_ids": np.array([1, 2]),
        "node2idx": {1: 0, 2: 1},
        "node_coords": [0.0, 0.0, 0.0, 10.0, 0.0, 0.0],
        "eles": [0, 1],
    })
    monkeypatch.setattr(svc, "_db_node_shapes", lambda pid, ids, order: {
        "order": order, "frequency": "462.39", "unit": "Hz",
        "real": [0.0, 0.0, 2.0, 0.0, 0.0, -1.0],
        "imag": [0.0] * 6,
    })
    monkeypatch.setattr(svc, "_db_modal_node_ids", lambda pid, order=None: {1, 2})


def _fem_stub(available=True, bbox=20.0, max_disp=4.0, **over):
    out = {
        "available": available, "reason": None if available else "stubbed",
        "step": "SUBCASE 1", "frame": 0, "result_group": None, "frequency": 454.7,
        "bbox_min": [0.0, 0.0, 0.0] if available else None,
        "bbox_max": [bbox, bbox, bbox] if available else None,
        "max_disp": max_disp if available else 0.0,
    }
    out.update(over)
    return out


def test_sync_scales_normalize_both_sides_to_same_target(stub_test_mesh, monkeypatch):
    monkeypatch.setattr(svc, "_fem_modal_stats",
                        lambda *a, **kw: _fem_stub(bbox=20.0, max_disp=4.0))
    r = svc.get_sync_animation(1, 1, n_frames=8)

    # 并集包围盒最大边 = 20(FEM 更大),target = 20/10 = 2.0
    assert r["sync"]["refSize"] == 20.0
    assert r["sync"]["targetDeform"] == 2.0
    # 试验最大复幅值 2.0 → scaleFactor = 1.0;FEM 最大位移 4.0 → scale = 0.5
    assert r["test"]["scaleFactor"] == pytest.approx(1.0)
    assert r["fem"]["scale"] == pytest.approx(0.5)
    # 两侧放大后的最大变形量一致,都是 targetDeform
    assert r["test"]["scaleFactor"] * 2.0 == pytest.approx(r["fem"]["scale"] * 4.0)


def test_sync_frames_match_fem_sin_phase(stub_test_mesh, monkeypatch):
    monkeypatch.setattr(svc, "_fem_modal_stats",
                        lambda *a, **kw: _fem_stub(bbox=10.0, max_disp=1.0))
    r = svc.get_sync_animation(1, 1, n_frames=8)
    frames = r["test"]["frames"]

    assert len(frames) == 8 and len(frames[0]) == 6
    # 第 0 帧 sin(0)=0:无变形,与 FEM modal-animation 第 0 帧一致
    assert frames[0] == [0.0, 0.0, 0.0, 10.0, 0.0, 0.0]
    # 第 2 帧 θ=π/2 sin=1:节点1 z 位移 = scaleFactor * real = 0.5 * 2.0 = targetDeform
    assert frames[2][2] == pytest.approx(r["sync"]["targetDeform"])


def test_sync_degrades_to_test_only_when_fem_unavailable(stub_test_mesh, monkeypatch):
    monkeypatch.setattr(svc, "_fem_modal_stats",
                        lambda *a, **kw: _fem_stub(available=False))
    r = svc.get_sync_animation(1, 1)

    assert r["fem"]["available"] is False
    assert r["fem"]["scale"] == 0.0
    # 退化为只按试验包围盒(最大边 10)归一化
    assert r["sync"]["refSize"] == 10.0
    assert r["test"]["scaleFactor"] == pytest.approx(10.0 / 10.0 / 2.0)
    assert r["test"]["frames"]


def test_sync_order_zero_returns_undeformed(stub_test_mesh, monkeypatch):
    monkeypatch.setattr(svc, "_fem_modal_stats", lambda *a, **kw: _fem_stub())
    r = svc.get_sync_animation(1, 0)
    assert r["test"]["componentData"] == [0.0, 0.0]
    assert r["test"]["frames"] == []


def test_sync_flip_and_include_frames_options(stub_test_mesh, monkeypatch):
    monkeypatch.setattr(svc, "_fem_modal_stats",
                        lambda *a, **kw: _fem_stub(bbox=10.0, max_disp=1.0))
    flipped = svc.get_sync_animation(1, 1, n_frames=8, flip=True)
    assert flipped["test"]["frames"][2][2] == pytest.approx(-flipped["sync"]["targetDeform"])

    no_frames = svc.get_sync_animation(1, 1, include_frames=False)
    assert no_frames["test"]["frames"] == []
    assert no_frames["test"]["scaleFactor"] > 0


def test_sync_coefficient_shrinks_both_sides_equally(stub_test_mesh, monkeypatch):
    monkeypatch.setattr(svc, "_fem_modal_stats",
                        lambda *a, **kw: _fem_stub(bbox=20.0, max_disp=4.0))
    base = svc.get_sync_animation(1, 1)
    half = svc.get_sync_animation(1, 1, coefficient=2.0)
    assert half["test"]["scaleFactor"] == pytest.approx(base["test"]["scaleFactor"] / 2.0)
    assert half["fem"]["scale"] == pytest.approx(base["fem"]["scale"] / 2.0)


def test_geometry_filters_nodes_without_current_mode_results(monkeypatch):
    monkeypatch.setattr(svc, "_db_node_elements", lambda pid: {
        "node_ids": np.array([1, 2, 3]),
        "node2idx": {1: 0, 2: 1, 3: 2},
        "node_coords": [0.0, 0.0, 0.0, 10.0, 0.0, 0.0, 20.0, 0.0, 0.0],
        "eles": [0, 1, 1, 2],
    })
    monkeypatch.setattr(svc, "_db_modal_node_ids", lambda pid, order=None: {1, 3} if order == 1 else {1, 2, 3})
    monkeypatch.setattr(svc, "_db_node_shapes", lambda pid, ids, order: {
        "order": order, "frequency": "1.0", "unit": "Hz",
        "real": [1.0, 0.0, 0.0, 0.0, 0.0, 3.0],
        "imag": [0.0] * 6,
    })

    result = svc.get_geometry(1, 1, 1.0, 1.0)

    assert result["ids"] == [1, 3]
    assert result["elementsIndex"] == []


def test_db_node_elements_skips_missing_element_nodes(monkeypatch):
    class _Cursor:
        def __init__(self):
            self.last_sql = ""

        def execute(self, sql, params=None):
            self.last_sql = " ".join(sql.split())

        def fetchall(self):
            if "FROM t_mt_py_test_node" in self.last_sql:
                return [(1, 0.0, 0.0, 0.0), (2, 1.0, 0.0, 0.0)]
            if "FROM t_mt_py_test_element" in self.last_sql:
                return [(1, 12288)]
            return []

        def close(self):
            return None

    class _Conn:
        def cursor(self):
            return _Cursor()

        def close(self):
            return None

    monkeypatch.setattr(svc, "get_connection", lambda: _Conn())

    result = svc._db_node_elements("demo")

    assert result["node_ids"].tolist() == [1, 2]
    assert result["eles"] == [0]
