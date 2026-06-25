# -*- coding: utf-8 -*-
"""含壳/膜模型上,探测并验证"面内/面外主应力 + Abs 变体"的实现前提。

在装了 Abaqus 的机器上运行(Abaqus Python 2.7 或新版 Python 3):
    abaqus python tests/dump_abq_inplane_probe.py <odb> [field=S] [step=Step-1] [frame=1]

它回答 4 件事,作为实现"面内/面外/Abs 主应力"前的体检:

1) validInvariants:这个场在含壳模型里,Abaqus 报哪些有效不变量。
2) Abs 常量探测:abaqusConstants 里有没有 MAX_PRINCIPAL_ABS 之类、getScalarField 认不认。
3) 实体置灰判据:面内不变量标量场(getScalarField)到底"哪些单元类型有数据"
   —— 有=壳/膜(该出值),无=实体(该置灰)。这是生产里最可靠的判据。
4) 公式对照:在 INTEGRATION_POINT 上,用我们的 numpy 口径算 主应力/面内/面外/Abs,
   和 Abaqus getScalarField 的值逐块对(按值排序后比,免去顺序问题),报最大误差(应 ~0)。
   ※ 必须在积分点比:面内主应力非线性,EN 上 Abaqus 走外推标量(方法A)、我们走张量算
     (方法B)本就不等;积分点上两者都直接从张量算,才该一致。

不写任何文件。
"""
import sys
import numpy as np
from odbAccess import openOdb
import abaqusConstants as AC
from abaqusConstants import INTEGRATION_POINT


# ── 我们生产代码里的 numpy 口径(与 abaqus_dump._compute_invariants_numpy 一致) ──
def _principals_3d(d):
    d = np.asarray(d, dtype=np.float64)
    n = d.shape[0]
    c = np.zeros((n, 6))
    c[:, :min(d.shape[1], 6)] = d[:, :min(d.shape[1], 6)]
    T = np.empty((n, 3, 3))
    T[:, 0, 0] = c[:, 0]; T[:, 1, 1] = c[:, 1]; T[:, 2, 2] = c[:, 2]
    T[:, 0, 1] = T[:, 1, 0] = c[:, 3]
    T[:, 0, 2] = T[:, 2, 0] = c[:, 4]
    T[:, 1, 2] = T[:, 2, 1] = c[:, 5]
    return np.linalg.eigvalsh(T)  # 升序 [N,3]


def _inplane(d):
    d = np.asarray(d, dtype=np.float64)
    c11, c22, c12 = d[:, 0], d[:, 1], d[:, 3]
    avg = (c11 + c22) * 0.5
    r = np.sqrt(np.maximum(0.0, ((c11 - c22) * 0.5) ** 2 + c12 ** 2))
    return avg + r, avg - r


def _absmax(a, b):
    a = np.asarray(a, dtype=np.float64); b = np.asarray(b, dtype=np.float64)
    return np.where(np.abs(a) >= np.abs(b), a, b)


# inv_name -> 用 numpy 从张量 d[N,ncomp] 算出标量 [N]
def _our(inv, d):
    if inv == 'MAX_PRINCIPAL':           return _principals_3d(d)[:, 2]
    if inv == 'MID_PRINCIPAL':           return _principals_3d(d)[:, 1]
    if inv == 'MIN_PRINCIPAL':           return _principals_3d(d)[:, 0]
    if inv == 'MAX_INPLANE_PRINCIPAL':   return _inplane(d)[0]
    if inv == 'MIN_INPLANE_PRINCIPAL':   return _inplane(d)[1]
    if inv == 'OUTOFPLANE_PRINCIPAL':    return np.asarray(d, dtype=np.float64)[:, 2]
    if inv == 'MAX_PRINCIPAL_ABS':
        e = _principals_3d(d); return _absmax(e[:, 2], e[:, 0])
    raise KeyError(inv)


def _ip_blocks_by_key(field_pos):
    """{(inst,etype): 拼接后的扁平 data}。标量场 → [N];张量场 → [N,ncomp]。"""
    out = {}
    for b in field_pos.bulkDataBlocks:
        if b.instance is None:
            continue
        et = getattr(b, 'baseElementType', None) or getattr(b, 'elementType', '?')
        key = (b.instance.name, et)
        d = np.asarray(b.data, dtype=np.float64)
        out.setdefault(key, []).append(d)
    return dict((k, np.concatenate(v)) for k, v in out.items())


def _sorted_maxdiff(a, b):
    a = np.asarray(a, dtype=np.float64).ravel(); b = np.asarray(b, dtype=np.float64).ravel()
    a = a[np.isfinite(a)]; b = b[np.isfinite(b)]
    if a.size == 0 or b.size == 0 or a.size != b.size:
        return None, (a.size, b.size)
    return float(np.abs(np.sort(a) - np.sort(b)).max()), (a.size, b.size)


def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    odb_path = sys.argv[1]
    field = sys.argv[2] if len(sys.argv) > 2 else 'S'
    step  = sys.argv[3] if len(sys.argv) > 3 else 'Step-1'
    frame = int(sys.argv[4]) if len(sys.argv) > 4 else 1

    odb = openOdb(odb_path, readOnly=True)
    fo = odb.steps[step].frames[frame].fieldOutputs[field]

    # ── 1) validInvariants ───────────────────────────────────────────────────
    vinv = [str(i) for i in fo.validInvariants]
    print("=" * 72)
    print("1) field=%s  validInvariants:\n    %s" % (field, vinv))

    # ── 2) Abs 常量探测 ───────────────────────────────────────────────────────
    print("=" * 72)
    print("2) Abs 候选常量 是否存在 / getScalarField 认不认:")
    for name in ('MAX_PRINCIPAL_ABS', 'ABS_MAX_PRINCIPAL',
                 'MAX_IN_PLANE_PRINCIPAL_ABS', 'ABS_MAX_IN_PLANE_PRINCIPAL'):
        has = hasattr(AC, name); acc = None
        if has:
            try:
                fo.getScalarField(invariant=getattr(AC, name)); acc = True
            except Exception as e:
                acc = 'reject:%s' % e
        print("   %-28s const=%s  getScalarField=%s" % (name, has, acc))

    # ── 张量(IP) 一次取出 ─────────────────────────────────────────────────────
    tens = _ip_blocks_by_key(fo.getSubset(position=INTEGRATION_POINT))
    all_keys = sorted(tens.keys())

    # ── 3) 实体置灰判据: 面内标量场到底哪些单元类型有数据 ──────────────────────
    print("=" * 72)
    print("3) 面内不变量(MAX_INPLANE_PRINCIPAL)标量场, 各单元类型有无数据:")
    inplane_keys = set()
    try:
        ip_sf = _ip_blocks_by_key(
            fo.getScalarField(invariant=AC.MAX_INPLANE_PRINCIPAL).getSubset(position=INTEGRATION_POINT))
        inplane_keys = set(ip_sf.keys())
    except Exception as e:
        print("   [!] getScalarField(MAX_INPLANE_PRINCIPAL) 失败:", e)
    for key in all_keys:
        nc = tens[key].shape[1] if tens[key].ndim > 1 else 1
        print("   [%s | %s] ncomp=%d  面内有数据=%s  → %s"
              % (key[0], key[1], nc, key in inplane_keys,
                 "壳/膜(出值)" if key in inplane_keys else "实体(置灰)"))

    # ── 4) 公式对照(积分点, 按值排序比) ──────────────────────────────────────
    print("=" * 72)
    print("4) numpy 公式 vs Abaqus getScalarField, INTEGRATION_POINT 逐块最大误差:")
    inv_consts = {
        'MAX_PRINCIPAL': AC.MAX_PRINCIPAL, 'MIN_PRINCIPAL': AC.MIN_PRINCIPAL,
        'MAX_INPLANE_PRINCIPAL': AC.MAX_INPLANE_PRINCIPAL,
        'MIN_INPLANE_PRINCIPAL': AC.MIN_INPLANE_PRINCIPAL,
        'OUTOFPLANE_PRINCIPAL': AC.OUTOFPLANE_PRINCIPAL,
    }
    if hasattr(AC, 'MAX_PRINCIPAL_ABS'):
        inv_consts['MAX_PRINCIPAL_ABS'] = AC.MAX_PRINCIPAL_ABS

    for inv, const in inv_consts.items():
        try:
            abq = _ip_blocks_by_key(fo.getScalarField(invariant=const).getSubset(position=INTEGRATION_POINT))
        except Exception as e:
            print("   %-24s [getScalarField 失败: %s]" % (inv, e)); continue
        worst = 0.0; worst_key = None; nblk = 0; skipped = 0
        for key in all_keys:
            d = tens[key]
            if d.ndim < 2 or d.shape[1] < 4:
                continue
            if key not in abq:
                continue
            our = _our(inv, d)
            diff, sizes = _sorted_maxdiff(our, abq[key])
            if diff is None:
                skipped += 1; continue
            nblk += 1
            if diff > worst:
                worst = diff; worst_key = key
        tag = "  最差块=%s" % (worst_key,) if worst_key else ""
        print("   %-24s 比了%d块 跳过%d  最大误差=%.3g%s"
              % (inv, nblk, skipped, worst, tag))

    print("=" * 72)
    print("判读: 第4段误差全 ~0 → 公式与索引假设都对; 第3段'面内有数据'为壳的几种")
    print("      单元类型,实体应置灰; MAX_PRINCIPAL_ABS 若 const=True 则可走 getScalarField 读。")
    odb.close()


if __name__ == '__main__':
    main()
