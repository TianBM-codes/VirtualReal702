"""
接触输出场（CPRESS / CSHEAR / COPEN / CSLIP）支持的单元测试。

覆盖：
  - src/l1/abaqus_dump_parallel.py  group_fields_for_workers()
    （字段名带接触对后缀时按基名分组，保证同组不跨 worker）
  - src/l3/services/query_service.py  _read_pick_result() 的 NODAL 稀疏分支
    （结果 HDF5 带 /NODAL/<inst>/labels 时按 label 对齐，而不是按几何行号直索引）

src/l1/abaqus_dump.py 的 split_region_field 不在此 import（约定：该模块
import 需要 odbAccess）；abaqus_dump_parallel 里的分组逻辑与其保持一致，
由本文件覆盖。

设计文档：docs/Contact-Field-Support.md
"""
import h5py
import numpy as np
import pytest

from src.l1.abaqus_dump_parallel import group_fields_for_workers
from src.l3.services.query_service import _read_pick_result


# ---------------------------------------------------------------------------
# group_fields_for_workers — 并行 launcher 的逻辑字段分组
# ---------------------------------------------------------------------------

class TestGroupFieldsForWorkers:

    def test_plain_fields_stay_single(self):
        units = group_fields_for_workers(["S", "U", "RF"])
        assert units == [["S"], ["U"], ["RF"]]

    def test_contact_pairs_merge_by_base_name(self):
        fields = [
            "CPRESS   ASSEMBLY_S_SET-3_CNS_/ASSEMBLY_M_SURF-1",
            "CPRESS   ASSEMBLY_S_SET-9_CNS_/ASSEMBLY_M_SURF-2",
            "CSHEAR1   ASSEMBLY_S_SET-3_CNS_/ASSEMBLY_M_SURF-1",
            "U",
        ]
        units = group_fields_for_workers(fields)
        assert units == [
            [
                "CPRESS   ASSEMBLY_S_SET-3_CNS_/ASSEMBLY_M_SURF-1",
                "CPRESS   ASSEMBLY_S_SET-9_CNS_/ASSEMBLY_M_SURF-2",
            ],
            ["CSHEAR1   ASSEMBLY_S_SET-3_CNS_/ASSEMBLY_M_SURF-1"],
            ["U"],
        ]

    def test_suffix_without_slash_or_assembly_not_treated_as_region(self):
        # 只有含 '/' 或以 ASSEMBLY 开头的后缀才算 region，其他名字原样保留
        units = group_fields_for_workers(["FOO BAR", "FOO BAZ"])
        assert units == [["FOO BAR"], ["FOO BAZ"]]

    def test_assembly_prefix_suffix_counts_as_region(self):
        units = group_fields_for_workers([
            "COPEN ASSEMBLY_GENERAL_CONTACT_DOMAIN",
            "COPEN ASSEMBLY_OTHER_DOMAIN",
        ])
        assert units == [[
            "COPEN ASSEMBLY_GENERAL_CONTACT_DOMAIN",
            "COPEN ASSEMBLY_OTHER_DOMAIN",
        ]]


# ---------------------------------------------------------------------------
# _read_pick_result — NODAL 稀疏场的 label 对齐
# ---------------------------------------------------------------------------

INSTANCE = "PART-1-1"
STEP = "Step-1"


def _write_geometry(workspace, node_labels):
    path = workspace / "l1" / "geometry" / f"{INSTANCE}.h5"
    with h5py.File(path, "w") as f:
        f.create_group("nodes").create_dataset(
            "labels", data=np.asarray(node_labels, dtype=np.int32))
    return path


def _write_result(workspace, field, data, labels=None):
    """data: [num_frames, N, ncomp]；labels 给定时写稀疏 labels 数据集。"""
    path = workspace / "l1" / "results" / f"{STEP}__{field}.h5"
    with h5py.File(path, "w") as f:
        grp = f.create_group("NODAL").create_group(INSTANCE)
        grp.create_dataset("data", data=np.asarray(data, dtype=np.float32))
        if labels is not None:
            grp.create_dataset("labels", data=np.asarray(labels, dtype=np.int32))
    return path


@pytest.fixture
def contact_workspace(workspace):
    """几何 6 个节点（label 5..30），CPRESS 只在 3 个节点（10/20/30）上有值。"""
    _write_geometry(workspace, [5, 10, 15, 20, 25, 30])
    # 2 帧 × 3 节点 × 1 分量；帧 1 的值 = label 值（便于断言）
    data = np.array([
        [[0.0], [0.0], [0.0]],
        [[10.0], [20.0], [-30.0]],
    ])
    _write_result(workspace, "CPRESS", data, labels=[10, 20, 30])
    return workspace


class TestSparseNodalPick:

    def _pick(self, workspace, *, rows, mode, selected=None, frame=1):
        return _read_pick_result(
            workspace=str(workspace),
            instance=INSTANCE,
            face_node_rows=rows,
            elem_row=0,
            etype_str="C3D8",
            frame_idx=frame,
            step=STEP,
            field="CPRESS",
            component=None,
            component_idx=None,
            pick_mode=mode,
            selected_node_row=selected,
        )

    def test_node_pick_on_contact_node_maps_by_label(self, contact_workspace):
        # 几何行 1 = label 10 → 稀疏数据行 0 → 值 10.0
        # （旧实现会拿几何行号 1 直索引稀疏数组，错取 label 20 的值）
        info = self._pick(contact_workspace, rows=[1], mode="node", selected=1)
        assert info is not None
        assert info.position == "NODAL"
        assert info.raw_value == pytest.approx(10.0)

    def test_node_pick_keeps_sign_for_scalar_field(self, contact_workspace):
        # label 30（几何行 5）的值是 -30，标量场必须保号（不能被 abs 掉）
        info = self._pick(contact_workspace, rows=[5], mode="node", selected=5)
        assert info.raw_value == pytest.approx(-30.0)

    def test_node_pick_outside_contact_returns_none(self, contact_workspace):
        # 几何行 2 = label 15，不在接触面上 → NODAL 分支无数据 → None
        info = self._pick(contact_workspace, rows=[2], mode="node", selected=2)
        assert info is None

    def test_element_pick_averages_only_nodes_with_data(self, contact_workspace):
        # 三个角节点：行 1(=10, 有值 10.0)、行 3(=20, 有值 20.0)、行 2(=15, 无值)
        info = self._pick(contact_workspace, rows=[1, 3, 2], mode="element")
        assert info is not None
        assert info.raw_values == [pytest.approx(10.0), pytest.approx(20.0)]
        assert info.display_value == pytest.approx(15.0)

    def test_element_pick_all_nodes_without_data_returns_none(self, contact_workspace):
        info = self._pick(contact_workspace, rows=[0, 2, 4], mode="element")
        assert info is None

    def test_dense_field_without_labels_keeps_row_indexing(self, workspace):
        # 无 labels 数据集（旧 workspace 的稠密场）→ 按几何行号直索引，行为不变
        _write_geometry(workspace, [5, 10, 15, 20, 25, 30])
        data = np.arange(12, dtype=np.float32).reshape(2, 6, 1)
        _write_result(workspace, "CPRESS", data, labels=None)
        info = self._pick(workspace, rows=[3], mode="node", selected=3)
        assert info.raw_value == pytest.approx(float(data[1, 3, 0]))

    def test_dense_field_with_full_labels_is_identity(self, workspace):
        # labels == 几何全节点：label 对齐是恒等映射，与旧行为一致
        _write_geometry(workspace, [5, 10, 15, 20, 25, 30])
        data = np.arange(12, dtype=np.float32).reshape(2, 6, 1)
        _write_result(workspace, "CPRESS", data,
                      labels=[5, 10, 15, 20, 25, 30])
        info = self._pick(workspace, rows=[3], mode="node", selected=3)
        assert info.raw_value == pytest.approx(float(data[1, 3, 0]))
