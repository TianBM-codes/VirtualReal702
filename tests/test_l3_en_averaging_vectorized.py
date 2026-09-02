"""_en_per_vertex_averaged 向量化回归测试。

该函数原先用两个逐三角形的 Python for 循环做 75% 条件平均, 在 700~800 万面片的
模型上耗时以分钟计, 已改写为纯 numpy 向量化(排序 + reduceat 分组统计)。

本测试保留一份逐三角形的朴素参考实现(与旧版语义一致, 唯一差别: 非有限值不参与
平均统计——旧版把 NaN 塞进 Python 列表后 max() 行为与遍历顺序相关, 属未定义行为;
新语义是确定性的: NaN 顶点照常渲灰, 但不污染邻居的平均), 用随机合成数据对拍,
要求逐位一致。

覆盖点:
- 实体(直接 data) + 壳(sp1 子组) 混合, 多分量取分量/取模长
- elem_row / local_node_idx 越界样本(结果块比几何块短)
- 结果文件缺失的单元类型(该 etype 三角形无数据但仍参与 Step4 邻居均值回填)
- 分量对壳块越界产生 NaN(混合实例选 S13/S23 场景)
- 阈值 0 / 0.75 / 1.0, 全同域, domain_id 数组过短(gidx 越界 → did=-1)
"""
import h5py
import numpy as np
import pytest

from src.l3.services.result_service import (
    _en_per_vertex_averaged,
    _extract_component,
)


def _reference_impl(
    f_h5, instance, frame_idx, component_idx,
    src_etype, src_elem_row, src_local_node_idx,
    vtx_node_row, render_indices, domain_id,
    avg_elem_etype, avg_elem_row, average_threshold=0.75,
):
    """逐三角形朴素实现(改写前的旧算法, 作为对拍基准)。"""
    Nv = len(vtx_node_row)
    Nt = len(src_elem_row)

    elem_to_gidx = {
        (avg_elem_etype[i].tobytes(), int(avg_elem_row[i])): i
        for i in range(len(avg_elem_row))
    }

    scalar_en_by_etype = {}
    num_frames = None
    for etype_bytes in np.unique(src_etype):
        etype_str = etype_bytes.decode("ascii").rstrip("\x00")
        grp_path = f"/ELEMENT_NODAL/{instance}/{etype_str}"
        if grp_path not in f_h5:
            continue
        grp = f_h5[grp_path]
        if "data" in grp:
            ds = grp["data"]
        else:
            sp_keys = sorted(k for k in grp.keys() if k.startswith("sp"))
            if not sp_keys:
                continue
            ds = grp["sp1" if "sp1" in grp else sp_keys[0]]["data"]
        if num_frames is None:
            num_frames = int(ds.shape[0])
        fd = ds[frame_idx]
        while fd.ndim > 3:
            fd = fd.mean(axis=2)
        if fd.ndim == 3:
            sc = _extract_component(
                fd.reshape(-1, fd.shape[-1]), component_idx
            ).reshape(fd.shape[0], fd.shape[1])
        else:
            sc = fd.astype(np.float32)
        scalar_en_by_etype[etype_bytes] = sc

    if not scalar_en_by_etype or num_frames is None:
        return None

    domain_node_vals = {}
    vtx_corner_val = np.full(Nv, np.nan, dtype=np.float32)

    for tri in range(Nt):
        eb = src_etype[tri]
        er = int(src_elem_row[tri])
        sc = scalar_en_by_etype.get(eb)
        if sc is None:
            continue
        gidx = elem_to_gidx.get((eb.tobytes(), er))
        did = int(domain_id[gidx]) if (gidx is not None and gidx < len(domain_id)) else -1
        for corner in range(3):
            vtx = int(render_indices[tri, corner])
            li = int(src_local_node_idx[tri, corner])
            if er >= sc.shape[0] or li >= sc.shape[1]:
                continue
            val = float(sc[er, li])
            vtx_corner_val[vtx] = val
            if did >= 0 and np.isfinite(val):
                nr = int(vtx_node_row[vtx])
                domain_node_vals.setdefault(did, {}).setdefault(nr, []).append(val)

    node_averaged = {}
    for did, node_dict in domain_node_vals.items():
        all_v = [v for lst in node_dict.values() for v in lst]
        d_range = max(all_v) - min(all_v) if all_v else 0.0
        for nr, lst in node_dict.items():
            spread = max(lst) - min(lst)
            if d_range < 1e-12 or spread <= average_threshold * d_range:
                node_averaged[(did, nr)] = float(sum(lst) / len(lst))

    scalar_vertex = vtx_corner_val.copy()
    for tri in range(Nt):
        eb = src_etype[tri]
        er = int(src_elem_row[tri])
        gidx = elem_to_gidx.get((eb.tobytes(), er))
        did = int(domain_id[gidx]) if (gidx is not None and gidx < len(domain_id)) else -1
        if did < 0:
            continue
        for corner in range(3):
            vtx = int(render_indices[tri, corner])
            nr = int(vtx_node_row[vtx])
            avg = node_averaged.get((did, nr))
            if avg is not None:
                scalar_vertex[vtx] = avg

    surf_valid = scalar_vertex[np.isfinite(scalar_vertex)]
    if surf_valid.size == 0:
        return None
    global_range = (float(surf_valid.min()), float(surf_valid.max()))
    return scalar_vertex, num_frames, global_range


def _build_case(rng, Nt, n_solid, n_shell, n_nodes,
                with_oob=True, with_missing_etype=False):
    """混合 solid(C3D8, 直接 data) + shell(S4R, sp1) 的随机合成场景。"""
    inst = "PART-1-1"
    etypes = [b"C3D8", b"S4R"]
    if with_missing_etype:
        etypes.append(b"CPS3")  # 结果文件里没有这个块

    src_etype = rng.choice(etypes, size=Nt).astype("S8")
    src_elem_row = np.where(
        src_etype == b"C3D8",
        rng.integers(0, n_solid, Nt),
        rng.integers(0, n_shell, Nt),
    ).astype(np.int32)
    if with_oob:  # 少量 er / li 越界(模拟结果块比几何块短)
        src_elem_row[rng.random(Nt) < 0.05] += max(n_solid, n_shell)

    src_local_node_idx = rng.integers(0, 4, (Nt, 3)).astype(np.int16)
    if with_oob:
        src_local_node_idx[rng.random(Nt) < 0.05, 0] = 7

    Nv = max(Nt // 2, 4)
    render_indices = rng.integers(0, Nv, (Nt, 3)).astype(np.int32)
    vtx_node_row = rng.integers(0, n_nodes, Nv).astype(np.int32)

    avg_elem_etype = np.concatenate([
        np.full(n_solid, b"C3D8"), np.full(n_shell, b"S4R")
    ]).astype("S8")
    avg_elem_row = np.concatenate([
        np.arange(n_solid), np.arange(n_shell)
    ]).astype(np.int32)
    domain_id = rng.integers(0, 5, n_solid + n_shell).astype(np.int32)

    f = h5py.File("mem.h5", "w", driver="core", backing_store=False)
    solid = rng.normal(0, 1, (2, n_solid + 3, 8, 6)).astype(np.float32)
    shell = rng.normal(0, 1, (2, n_shell + 3, 4, 4)).astype(np.float32)
    f.create_dataset(f"/ELEMENT_NODAL/{inst}/C3D8/data", data=solid)
    f.create_dataset(f"/ELEMENT_NODAL/{inst}/S4R/sp1/data", data=shell)
    f.create_dataset(f"/ELEMENT_NODAL/{inst}/S4R/sp2/data", data=shell * 2)

    args = dict(
        instance=inst, frame_idx=1,
        src_etype=src_etype, src_elem_row=src_elem_row,
        src_local_node_idx=src_local_node_idx,
        vtx_node_row=vtx_node_row, render_indices=render_indices,
        domain_id=domain_id,
        avg_elem_etype=avg_elem_etype, avg_elem_row=avg_elem_row,
    )
    return f, args


def _assert_same(f, args, component_idx, threshold):
    r_ref = _reference_impl(f, component_idx=component_idx,
                            average_threshold=threshold, **args)
    r_new = _en_per_vertex_averaged(f, component_idx=component_idx,
                                    average_threshold=threshold, **args)
    assert (r_ref is None) == (r_new is None)
    if r_ref is None:
        return
    sv_ref, nf_ref, gr_ref = r_ref
    sv_new, nf_new, gr_new = r_new
    assert nf_ref == nf_new
    # NaN 分布(渲灰的顶点)必须完全一致
    assert np.array_equal(np.isnan(sv_ref), np.isnan(sv_new))
    # 数值逐位一致(两边均值都以 float64 累加后落回 float32)
    finite = np.isfinite(sv_ref)
    assert np.array_equal(sv_ref[finite], sv_new[finite])
    assert gr_ref == gr_new


@pytest.mark.parametrize("component_idx", [None, 0, 3, 5])
@pytest.mark.parametrize("threshold", [0.0, 0.75, 1.0])
def test_vectorized_matches_reference(component_idx, threshold):
    """常规混合模型 + 越界样本: 各分量/阈值下与朴素实现逐位一致。"""
    rng = np.random.default_rng(42)
    f, args = _build_case(rng, Nt=4000, n_solid=600, n_shell=500, n_nodes=1500)
    try:
        _assert_same(f, args, component_idx, threshold)
    finally:
        f.close()


def test_missing_etype_block_still_backfills_neighbors():
    """结果文件缺 CPS3 块: 其三角形无数据, 但顶点仍能吃到同域邻居的平均值。"""
    rng = np.random.default_rng(7)
    f, args = _build_case(rng, Nt=3000, n_solid=400, n_shell=300, n_nodes=1000,
                          with_missing_etype=True)
    try:
        _assert_same(f, args, None, 0.75)
        _assert_same(f, args, 1, 0.75)
    finally:
        f.close()


def test_degenerate_domains():
    """全同域 与 domain_id 数组过短(gidx 越界 → did=-1)两个退化场景。"""
    rng = np.random.default_rng(3)
    f, args = _build_case(rng, Nt=2000, n_solid=300, n_shell=300, n_nodes=800,
                          with_oob=False)
    try:
        args["domain_id"] = np.zeros_like(args["domain_id"])
        _assert_same(f, args, None, 0.75)
        args["domain_id"] = args["domain_id"][:10]
        _assert_same(f, args, None, 0.75)
    finally:
        f.close()
