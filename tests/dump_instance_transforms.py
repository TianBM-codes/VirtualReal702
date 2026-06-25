"""导出每个 instance 的 4×4 变换矩阵(局部→全局)，重点看左上 3×3 旋转部分是否为单位阵。

用途: 配合 dump_ours_nodal.py / dump_abq_nodal.py 的逐 instance 分量对照——
若"分量对不上的 instance"正好就是"旋转 ≠ 单位阵的 instance"，则证实矢量场(U/UR/RF)
需要像几何那样施加 instance 旋转，而当前只对几何做了、对场没做。

用法:
    python tests/dump_instance_transforms.py <workspace>

输出每个 instance:
  - has_rotation: 旋转 3×3 是否明显偏离单位阵
  - translation : 平移量
  - 旋转矩阵本体
"""
import sys
import os
import numpy as np
import h5py


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    ws = sys.argv[1]
    asm = os.path.join(ws, "l1", "assembly.h5")
    if not os.path.exists(asm):
        print("[!] 找不到 assembly.h5:", asm)
        sys.exit(1)

    rotated = []
    with h5py.File(asm, "r") as f:
        if "instances" not in f:
            print("[!] assembly.h5 里没有 instances 组，顶层:", list(f.keys()))
            sys.exit(1)
        for inst in f["instances"]:
            g = f["instances/" + inst]
            if "transform" not in g:
                print("[%s] 无 transform → 视为单位阵" % inst)
                continue
            T = g["transform"][:]
            R = np.asarray(T)[:3, :3]
            t = np.asarray(T)[:3, 3]
            dev = float(np.abs(R - np.eye(3)).max())
            has_rot = dev > 1e-6
            if has_rot:
                rotated.append(inst)
            print("[%s] has_rotation=%s  max|R-I|=%.3g  translation=%s"
                  % (inst, has_rot, dev, np.array2string(t, precision=4)))
            if has_rot:
                print("    R=\n" + np.array2string(R, precision=5, suppress_small=True))

    print("\n=== 有旋转的 instance(%d 个) ===" % len(rotated))
    for i in rotated:
        print("  ", i)
    if not rotated:
        print("  (无) → 若 U 分量仍对不上，则不是 instance 旋转问题，需另查")


if __name__ == "__main__":
    main()
