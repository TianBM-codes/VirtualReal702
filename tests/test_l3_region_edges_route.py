"""区域边线兼容两阶段 L2 的独立边文件，并保留旧表面文件布局。"""
import asyncio
from types import SimpleNamespace

import h5py
import numpy as np
import pytest

from src.l3.api.routes import color_code
from src.l3.infra.l3be import build as l3be_build


INSTANCE = "PART-1-1"


def _model(tmp_path, monkeypatch, layout):
    geometry = tmp_path / "l2" / "geometry"
    geometry.mkdir(parents=True)
    coords = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
                       [2, 0, 0], [3, 0, 0]], dtype=np.float32)
    # 两个三角形构成一个四边形；渲染顶点可重复，边文件使用节点行号。
    edges = np.array([[0, 1], [1, 2], [2, 3], [3, 0], [1, 0], [4, 5]], dtype=np.int32)
    with h5py.File(geometry / f"{INSTANCE}_surface.h5", "w") as f:
        f.create_dataset("nodes/coords_global", data=coords)
        if layout in ("embedded", "both"):
            f.create_dataset("element_mesh_edges/edge_nodes",
                             data=edges if layout == "embedded" else edges[:1])
    if layout in ("sidecar", "both"):
        with h5py.File(geometry / f"{INSTANCE}_edges.h5", "w") as f:
            f.create_dataset("element_mesh_edges/edge_nodes", data=edges)
    idx = SimpleNamespace(
        workspace=str(tmp_path),
        render_indices={INSTANCE: np.array([[0, 1, 2], [3, 4, 5]], dtype=np.int32)},
        vtx_node_row={INSTANCE: np.array([0, 1, 2, 0, 2, 3], dtype=np.int32)},
        coords_global={INSTANCE: coords},
    )
    monkeypatch.setattr(color_code, "registry", SimpleNamespace(get=lambda _: idx))
    monkeypatch.setattr(color_code.color_service, "region_face_mask",
                        lambda *args: np.array([True, True]))
    expected = l3be_build([("edge_positions", coords[[0, 1, 0, 3, 1, 2, 2, 3]])])
    return geometry, coords, edges, expected


@pytest.mark.parametrize("layout", ["embedded", "sidecar", "both"])
def test_region_mesh_edges_reads_current_layout(tmp_path, monkeypatch, layout):
    _, _, _, expected = _model(tmp_path, monkeypatch, layout)
    response = asyncio.run(color_code.get_region_mesh_edges("odb", INSTANCE, "etype", "S4R"))
    assert response.headers["X-Edge-Count"] == "4"
    assert response.body == expected  # 去掉反向重复边及不属于该区域的边。


def test_region_mesh_edges_gain_advanced_sidecar(tmp_path, monkeypatch):
    geometry, _, edges, expected = _model(tmp_path, monkeypatch, "missing")
    first = asyncio.run(color_code.get_region_mesh_edges("odb", INSTANCE, "etype", "S4R"))
    assert first.headers["X-Edge-Count"] == "0"
    with h5py.File(geometry / f"{INSTANCE}_edges.h5", "w") as f:
        f.create_dataset("element_mesh_edges/edge_nodes", data=edges)
    second = asyncio.run(color_code.get_region_mesh_edges("odb", INSTANCE, "etype", "S4R"))
    assert second.headers["X-Edge-Count"] == "4"
    assert second.body == expected


def test_region_outline_excludes_quad_diagonal(tmp_path, monkeypatch):
    _, _, _, expected = _model(tmp_path, monkeypatch, "sidecar")
    response = asyncio.run(color_code.get_region_outline("odb", INSTANCE, "etype", "S4R"))
    assert response.headers["X-Edge-Count"] == "4"
    assert response.body == expected
