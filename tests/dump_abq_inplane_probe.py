# -*- coding: utf-8 -*-
"""含壳/膜模型上,探测并验证"面内/面外主应力 + Abs 变体"的实现前提。

在装了 Abaqus 的机器上运行(Abaqus Python 2.7 或新版 Python 3):
    abaqus python tests/dump_abq_inplane_probe.py <odb> [field=S] [step=Step-1] [frame=1]

它回答 4 件事,作为实现"面内/面外/Abs 主应力"前的体检:

1) validInvariants:这个场在含壳模型里,Abaqus 报哪些有效不变量
   —— 看面内/面外项是否出现(决定我们"按 Abaqus 清单生成"是否够用)。
2) Abs 常量探测:abaqusConstants 里有没有 ABS_MAX_PRINCIPAL 之类、getScalarField
   认不认 —— 决定 Abs 是"从 ODB 读"还是"我们 numpy 自己算"。
3) 逐(instance, 单元类型)块:打印分量标签/个数(壳是 3 还是 4 分量,直接关系到
   我们 [11,22,33,12] 的索引假设),并报告该块有没有面内数据(壳有/实体无),
   验证"实体单元面内置灰"的判据。
4) 公式对照:用我们的 numpy 口径算 主应力/面内/面外/Abs,和 Abaqus 自带的 block
   属性逐点对,报最大误差(应 ~0),证明公式正确。

只读 ELEMENT_NODAL 位置(云图口径)。不写任何文件。
"""
import sys
import numpy as np
from odbAccess import openOdb
import abaqusConstants as AC
from abaqusConstants import ELEMENT_NODAL


# ── 我们生产代码里的 numpy 口径(与 abaqus_dump._compute_invariants_numpy 一致) ──
def _principals_3d(d):
    """d:[N,ncomp] (顺序假设 11,22,33,12,13,23) → 升序特征值 [N,3]。"""
    d = np.asarray(d, dtype=np.float64)
    n, nc = d.shape
    c = np.zeros((n, 6))
    c[:, :min(nc, 6)] = d[:, :min(nc, 6)]
    T = np.empty((n, 3, 3))
    T[:, 0, 0] = c[:, 0]; T[:, 1, 1] = c[:, 1]; T[:, 2, 2] = c[:, 2]
    T[:, 0, 1] = T[:, 1, 0] = c[:, 3]
    T[:, 0, 2] = T[:, 2, 0] = c[:, 4]
    T[:, 1, 2] = T[:, 2, 1] = c[:, 5]
    return np.linalg.eigvalsh(T)  # 升序


def _inplane(d):
    """面内主应力 (用 11,22,12) → (maxIP, minIP)。"""
    d = np.asarray(d, dtype=np.float64)
    c11, c22, c12 = d[:, 0], d[:, 1], d[:, 3]
    avg = (c11 + c22) * 0.5
    r = np.sqrt(np.maximum(0.0, ((c11 - c22) * 0.5) ** 2 + c12 ** 2))
    return avg + r, avg - r


def _signed_absmax(a, b):
    """逐元素返回 a,b 中绝对值大的那个(保留符号)。"""
    a = np.asarray(a, dtype=np.float64); b = np.asarray(b, dtype=np.float64)
    return np.where(np.abs(a) >= np.abs(b), a, b)


def _maxdiff(x, y):
    x = np.asarray(x, dtype=np.float64).ravel()
    y = np.asarray(y, dtype=np.float64).ravel()
    m = np.isfinite(x) & np.isfinite(y)
    if not m.any():
        return None
    return float(np.abs(x[m] - y[m]).max())


def _try_attr(block, name):
    """安全取 block 的不变量属性(壳才有面内,实体取不到时返回 None)。"""
    try:
        v = getattr(block, name)
        if v is None:
            return None
        return np.asarray(v, dtype=np.float64)
    except Exception:
        return None


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
    print("=" * 70)
    print("1) field=%s  validInvariants:" % field)
    print("   ", vinv)
    print("   面内/面外是否出现:",
          [k for k in ('MAX_INPLANE_PRINCIPAL', 'MIN_INPLANE_PRINCIPAL',
                       'OUTOFPLANE_PRINCIPAL') if k in vinv])
    print("   含 ABS 的项:", [k for k in vinv if 'ABS' in k.upper()])

    # ── 2) Abs 常量探测 ───────────────────────────────────────────────────────
    print("=" * 70)
    print("2) abaqusConstants 里的 Abs 候选常量是否存在 / getScalarField 认不认:")
    for name in ('ABS_MAX_PRINCIPAL', 'MAX_PRINCIPAL_ABS', 'ABSMAXPRINCIPAL',
                 'MAX_IN_PLANE_PRINCIPAL_ABS', 'ABS_MAX_IN_PLANE_PRINCIPAL'):
        has_const = hasattr(AC, name)
        accepted = None
        if has_const:
            try:
                fo.getScalarField(invariant=getattr(AC, name))
                accepted = True
            except Exception as e:
                accepted = 'getScalarField拒绝: %s' % e
        print("   %-28s 常量存在=%s  getScalarField=%s" % (name, has_const, accepted))

    # ── 3)+4) 逐 (instance, 单元类型) 块 ──────────────────────────────────────
    print("=" * 70)
    print("3)+4) ELEMENT_NODAL 逐块: 分量 / 是否有面内数据 / 公式误差")
    en = fo.getSubset(position=ELEMENT_NODAL)

    seen = {}   # (inst, etype) -> 汇总
    for b in en.bulkDataBlocks:
        if b.instance is None:
            continue
        inst = b.instance.name
        etype = getattr(b, 'baseElementType', None) or getattr(b, 'elementType', '?')
        key = (inst, etype)
        data = np.asarray(b.data, dtype=np.float64)
        if data.ndim == 1:
            data = data[:, None]
        comp_labels = list(getattr(b, 'componentLabels', []) or [])

        # Abaqus 自带属性(壳才有面内)
        abq = {
            'maxP':   _try_attr(b, 'maxPrincipal'),
            'minP':   _try_attr(b, 'minPrincipal'),
            'maxIP':  _try_attr(b, 'maxInPlanePrincipal'),
            'minIP':  _try_attr(b, 'minInPlanePrincipal'),
            'outP':   _try_attr(b, 'outOfPlanePrincipal'),
        }
        has_inplane = abq['maxIP'] is not None

        # 我们的 numpy 口径
        diffs = {}
        if data.shape[1] >= 4:
            eig = _principals_3d(data)            # 升序 [N,3]
            our_maxP, our_minP = eig[:, 2], eig[:, 0]
            if abq['maxP'] is not None:
                diffs['maxP'] = _maxdiff(our_maxP, abq['maxP'])
            if abq['minP'] is not None:
                diffs['minP'] = _maxdiff(our_minP, abq['minP'])
            # Abs-max principal vs Abaqus max(|maxP|,|minP|)(自带属性交叉验证)
            if abq['maxP'] is not None and abq['minP'] is not None:
                our_absmaxP = _signed_absmax(our_maxP, our_minP)
                ref_absmaxP = _signed_absmax(abq['maxP'], abq['minP'])
                diffs['absMaxP'] = _maxdiff(our_absmaxP, ref_absmaxP)
            if has_inplane:
                our_maxIP, our_minIP = _inplane(data)
                diffs['maxIP'] = _maxdiff(our_maxIP, abq['maxIP'])
                diffs['minIP'] = _maxdiff(our_minIP, abq['minIP'])
                if abq['outP'] is not None:
                    diffs['outP'] = _maxdiff(data[:, 2], abq['outP'])  # 面外 = S33
                if abq['minIP'] is not None:
                    our_absIP = _signed_absmax(our_maxIP, our_minIP)
                    ref_absIP = _signed_absmax(abq['maxIP'], abq['minIP'])
                    diffs['absMaxIP'] = _maxdiff(our_absIP, ref_absIP)

        if key not in seen:
            seen[key] = (comp_labels, data.shape[1], has_inplane, diffs)

    for (inst, etype), (cl, nc, has_ip, diffs) in sorted(seen.items()):
        print("-" * 70)
        print("[%s | %s]  ncomp=%d  components=%s" % (inst, etype, nc, cl))
        print("   有面内数据(=壳/膜)? %s" % has_ip)
        if diffs:
            print("   公式 vs Abaqus 最大误差:",
                  {k: ("%.3g" % v if v is not None else None) for k, v in diffs.items()})
        else:
            print("   (ncomp<4, 未算不变量)")

    print("=" * 70)
    print("判读:")
    print("  - 若壳块 components 是 4 个含 S33、且 maxIP/minIP/outP 误差 ~0 → 我们的索引")
    print("    假设[11,22,33,12]和面内公式都对,可放心实现。")
    print("  - 若壳块只有 3 个分量(无 S33) → 我们的 _compute_invariants_numpy 需要按壳")
    print("    的分量顺序特判,否则 ncomp<4 会直接返回 None(算不出)。")
    print("  - '有面内数据=壳/膜' 为 True 的单元类型,正是面内不变量该出值的范围;")
    print("    False(实体)的应置灰 —— 这就是 EN/NODAL 实体置灰的判据。")
    odb.close()


if __name__ == '__main__':
    main()
