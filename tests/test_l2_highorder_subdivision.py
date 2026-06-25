"""高阶单元中节点细分（A2）回归测试。

L2 把高阶单元的表面面拆成包含边中节点的更多小三角片，使中节点的场值
（往往是峰值/谷值所在）能进入云图与 legend。本测试覆盖纯逻辑部分：
boundary 模板、细分、build_render_faces 的展开与线性单元的原样透传。

参见 docs/l2/HighOrder-Midside-Subdivision-Design.md。
"""
import numpy as np

from src.l2.ingest import (
    FACE_DEFS_FULL,
    SHELL_FULL_FACE_BY_NFULL,
    _full_boundary_cols,
    _subdivide_boundary_cols,
    build_render_faces,
)


# ─── boundary 模板 ────────────────────────────────────────────────────────────

def test_full_boundary_cols_solid_codes():
    # C3D20 底面（face_seq=1）= 4 角 + 4 边中，列序绕面一圈
    assert _full_boundary_cols(7, 1, 20) == [0, 8, 1, 9, 2, 10, 3, 11]
    # C3D10 第一个三角面
    assert _full_boundary_cols(5, 1, 10) == [0, 4, 1, 5, 2, 6]
    # C3D15 第三个面是 8 节点四边面
    assert _full_boundary_cols(6, 3, 15) == [0, 6, 1, 13, 4, 9, 3, 12]
    # 越界 face_seq → None
    assert _full_boundary_cols(7, 99, 20) is None


def test_full_boundary_cols_quadratic_shell_by_nfull():
    # 二次壳按 n_full 区分（code 与线性壳共用）
    assert _full_boundary_cols(0, 1, 6) == SHELL_FULL_FACE_BY_NFULL[6]
    assert _full_boundary_cols(1, 1, 8) == SHELL_FULL_FACE_BY_NFULL[8]
    # 线性壳（n_full=3/4）无模板
    assert _full_boundary_cols(0, 1, 3) is None
    assert _full_boundary_cols(1, 1, 4) is None


# ─── 细分 ─────────────────────────────────────────────────────────────────────

def test_subdivide_tri_face_4_subtris():
    # 6 节点三角面 → 4 个小三角，覆盖全部 6 个节点
    tris = _subdivide_boundary_cols([0, 3, 1, 4, 2, 5])
    assert len(tris) == 4
    assert set(np.array(tris).ravel()) == {0, 1, 2, 3, 4, 5}
    assert (3, 4, 5) in tris  # 中心小三角全是中节点


def test_subdivide_quad_face_6_subtris():
    # 8 节点四边面 → 6 个小三角，覆盖全部 8 个节点
    tris = _subdivide_boundary_cols([0, 4, 1, 5, 2, 6, 3, 7])
    assert len(tris) == 6
    assert set(np.array(tris).ravel()) == {0, 1, 2, 3, 4, 5, 6, 7}


# ─── build_render_faces ───────────────────────────────────────────────────────

def _c3d20_bottom_face_inputs():
    """单个 C3D20 单元的底面（角节点行 0..3），全连接行 = 0..19。"""
    surf_fnc = np.array([[0, 1, 2, 3]], dtype=np.int32)            # 角节点行
    surf_elem_rows = np.array([0], dtype=np.int32)
    surf_face_seqs = np.array([1], dtype=np.uint8)
    surf_etype_codes = np.array([7], dtype=np.uint8)
    surf_etype_strs = np.array([b"C3D20"], dtype="S8")
    conn_full = {"C3D20": np.arange(20, dtype=np.int32).reshape(1, 20)}
    return (surf_fnc, surf_elem_rows, surf_face_seqs,
            surf_etype_codes, surf_etype_strs, conn_full)


def test_build_render_faces_c3d20_expands_to_6_tris_with_midnodes():
    (surf_fnc, er, fs, ec, es, conn_full) = _c3d20_bottom_face_inputs()

    rfnc, rer, rfs, rec, res = build_render_faces(
        surf_fnc, er, fs, ec, es, conn_full)

    # 1 个高阶四边面 → 6 个小三角
    assert rfnc.shape[0] == 6
    # 元数据继承自源单元/面
    assert (rer == 0).all()
    assert (rfs == 1).all()
    assert (rec == 7).all()
    assert (res == b"C3D20").all()

    # 底面全连接列 = [0,8,1,9,2,10,3,11]；细分后顶点应覆盖这 8 个节点行，
    # 其中 8/9/10/11 是边中节点——修复前它们永远不会出现在渲染顶点里。
    used = set(rfnc[:, :3].ravel().tolist())
    assert {8, 9, 10, 11}.issubset(used), "中节点未进入渲染三角"
    assert used == {0, 1, 2, 3, 8, 9, 10, 11}
    # 三角面只用前 3 列，第 4 列补 -1
    assert (rfnc[:, 3] == -1).all()


def test_build_render_faces_linear_passthrough_unchanged():
    # 线性单元（无 conn_full）原样透传，逐值不变
    surf_fnc = np.array([[0, 1, 2, 3], [4, 5, 6, -1]], dtype=np.int32)
    er = np.array([0, 1], dtype=np.int32)
    fs = np.array([1, 2], dtype=np.uint8)
    ec = np.array([4, 2], dtype=np.uint8)   # C3D8, C3D4
    es = np.array([b"C3D8", b"C3D4"], dtype="S8")

    rfnc, rer, rfs, rec, res = build_render_faces(surf_fnc, er, fs, ec, es, {})
    assert np.array_equal(rfnc, surf_fnc)
    assert np.array_equal(rer, er)
    assert np.array_equal(res, es)


def test_build_render_faces_mixed_linear_and_highorder():
    # 一个线性 C3D8 面 + 一个高阶 C3D20 底面
    surf_fnc = np.array([[10, 11, 12, 13], [0, 1, 2, 3]], dtype=np.int32)
    er = np.array([0, 0], dtype=np.int32)
    fs = np.array([1, 1], dtype=np.uint8)
    ec = np.array([4, 7], dtype=np.uint8)
    es = np.array([b"C3D8", b"C3D20"], dtype="S8")
    conn_full = {"C3D20": np.arange(20, dtype=np.int32).reshape(1, 20)}

    rfnc, rer, rfs, rec, res = build_render_faces(
        surf_fnc, er, fs, ec, es, conn_full)

    # 线性面 1 个 + 高阶面细分 6 个 = 7
    assert rfnc.shape[0] == 7
    # 线性面原样保留
    lin = res == b"C3D8"
    assert lin.sum() == 1
    assert np.array_equal(rfnc[lin][0], np.array([10, 11, 12, 13]))
    # 高阶细分含中节点
    ho = res == b"C3D20"
    assert ho.sum() == 6
    assert {8, 9, 10, 11}.issubset(set(rfnc[ho][:, :3].ravel().tolist()))


def test_build_render_faces_quadratic_shell_s6():
    # 6 节点二次三角壳：code 0 + n_full=6 → 4 个小三角，含中节点 3/4/5
    surf_fnc = np.array([[0, 1, 2]], dtype=np.int32)
    er = np.array([0], dtype=np.int32)
    fs = np.array([1], dtype=np.uint8)
    ec = np.array([0], dtype=np.uint8)
    es = np.array([b"S6"], dtype="S8")
    conn_full = {"S6": np.arange(6, dtype=np.int32).reshape(1, 6)}

    rfnc, rer, rfs, rec, res = build_render_faces(
        surf_fnc, er, fs, ec, es, conn_full)
    assert rfnc.shape[0] == 4
    assert set(rfnc[:, :3].ravel().tolist()) == {0, 1, 2, 3, 4, 5}
