# -*- coding: utf-8 -*-
"""对照"单元节点 Mises"的两种口径，看哪种和 Abaqus 云图一致、哪种会出负数。

在装 Abaqus 的机器上运行:
    abaqus python tests/dump_abq_mises_compare.py <odb> [field=S] [step=Step-1] [frame=1]

对 ELEMENT_NODAL 位置，打印三套 Mises 的全装配 + HEAD-1 的 min/max：

  A  scalarfield_en : getScalarField(MISES).getSubset(ELEMENT_NODAL)
                      —— 先在积分点算 Mises 标量、再外推标量(本平台现在存的就是这个)
  B1 block.mises     : 取 S 的 ELEMENT_NODAL 张量 block，直接读 block.mises
                      —— 先把张量外推到节点、再算 Mises(Abaqus 自带, 恒 ≥0)
  B2 numpy(EN tensor): 用 EN 张量分量自己按 Voigt 公式算 Mises(应与 B1 一致, 做交叉验证)

若 A 出负数、而 B1/B2 恒 ≥0 且与 Abaqus 云图对得上，则应改用 B 口径。
"""
import sys
import numpy as np
from odbAccess import openOdb
from abaqusConstants import ELEMENT_NODAL, MISES


def _mises_voigt(d):
    """d: [N,6] = S11,S22,S33,S12,S13,S23 → [N] Mises (恒 ≥0)。"""
    d = np.asarray(d, dtype=np.float64)
    if d.shape[1] < 6:
        pad = np.zeros((d.shape[0], 6 - d.shape[1]))
        d = np.concatenate([d, pad], axis=1)
    s11, s22, s33, s12, s13, s23 = (d[:, 0], d[:, 1], d[:, 2],
                                     d[:, 3], d[:, 4], d[:, 5])
    return np.sqrt(np.maximum(0.0,
        0.5 * ((s11 - s22) ** 2 + (s22 - s33) ** 2 + (s33 - s11) ** 2)
        + 3.0 * (s12 ** 2 + s13 ** 2 + s23 ** 2)))


class Acc(object):
    def __init__(self):
        self.g = [None, None]      # global min,max
        self.head = [None, None]   # HEAD-1 min,max
        self.neg = 0               # 负值个数(全装配)

    def add(self, inst, arr):
        a = np.asarray(arr, dtype=np.float64).ravel()
        a = a[np.isfinite(a)]
        if a.size == 0:
            return
        self.neg += int((a < 0).sum())
        lo, hi = float(a.min()), float(a.max())
        if self.g[0] is None:
            self.g = [lo, hi]
        else:
            self.g = [min(self.g[0], lo), max(self.g[1], hi)]
        if inst.upper().startswith("HEAD"):
            if self.head[0] is None:
                self.head = [lo, hi]
            else:
                self.head = [min(self.head[0], lo), max(self.head[1], hi)]

    def report(self, name):
        g = self.g; h = self.head
        gs = ("min=%.6g max=%.6g" % (g[0], g[1])) if g[0] is not None else "无数据"
        hs = ("min=%.6g max=%.6g" % (h[0], h[1])) if h[0] is not None else "无"
        print("  %-18s 全装配 %s   |  HEAD-1 %s   |  负值个数=%d"
              % (name, gs, hs, hs and self.neg))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    odb_path = sys.argv[1]
    field = sys.argv[2] if len(sys.argv) > 2 else "S"
    step  = sys.argv[3] if len(sys.argv) > 3 else "Step-1"
    frame = int(sys.argv[4]) if len(sys.argv) > 4 else 1

    odb = openOdb(odb_path, readOnly=True)
    fo  = odb.steps[step].frames[frame].fieldOutputs[field]

    en  = fo.getSubset(position=ELEMENT_NODAL)          # 张量 @ 单元节点
    sc_en = fo.getScalarField(invariant=MISES).getSubset(position=ELEMENT_NODAL)

    accA, accB1, accB2 = Acc(), Acc(), Acc()

    # A: 外推标量 Mises
    for b in sc_en.bulkDataBlocks:
        if b.instance is None:
            continue
        accA.add(b.instance.name, b.data)

    # B1 / B2: 来自 EN 张量 block
    b1_ok = True
    for b in en.bulkDataBlocks:
        if b.instance is None:
            continue
        nm = b.instance.name
        # B1: Abaqus 自带 block.mises
        if b1_ok:
            try:
                accB1.add(nm, b.mises)
            except Exception as exc:
                b1_ok = False
                print("  [!] block.mises 不可用，跳过 B1:", exc)
        # B2: 自己用张量分量算
        accB2.add(nm, _mises_voigt(np.array(b.data)))

    print("ELEMENT_NODAL Mises 三种口径对照 (field=%s, %s, frame=%d):" % (field, step, frame))
    accA.report("A scalarfield_en")
    if b1_ok:
        accB1.report("B1 block.mises")
    accB2.report("B2 numpy(EN tensor)")
    print("\n说明: A 若出负值而 B1/B2 恒 ≥0，则负数来自'先算标量再外推'的过冲；"
          "看你的 Abaqus 云图 Mises 最小值是负还是 ~0，即可判定该用哪种。")

    odb.close()


if __name__ == "__main__":
    main()
