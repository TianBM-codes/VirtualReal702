# -*- coding: utf-8 -*-
"""导出 Abaqus ODB 里某个 NODAL 矢量场(U / UR / RF ...)的原始值，
逐 instance、逐分量、外加幅值(MAGNITUDE)统计 min/max，和 tests/dump_ours_nodal.py 对照。

U / UR / RF 这类节点场在 ODB 里就是 NODAL 位置；Abaqus 默认按全局坐标系报告。

在装了 Abaqus 的机器上运行(Abaqus Python 2.7 或新版 Python 3):
    abaqus python tests/dump_abq_nodal.py <odb> [field=U] [step=Step-1] [frame=1]

输出:
  - 控制台: 每个 instance 各分量 + 幅值的 min/max，最后再打印全装配 global 汇总
  - CSV: abaqus_<field>_nodal.csv (instance, nodeLabel, comp..., MAG)

判读要点(和 ours 对照):
  - 只有某些 instance 的分量对不上、幅值对  → 那些 instance 多半有旋转，坐标系口径不一致
  - 各 instance 都对、只是 global 不一致      → 我们这边全局并集漏了节点
  - Abaqus 这里 UR 各 instance 也全 0         → 模型物理上 UR 就该是 0(纯实体)，不是我们的 bug
  - Abaqus 这里 UR 非 0、我们全 0             → L1 提取把 UR 丢了/填零了，是 bug
"""
import sys
import csv
from odbAccess import openOdb


def _open_csv(path):
    if sys.version_info[0] >= 3:
        return open(path, 'w', newline='')
    return open(path, 'wb')


def _mag(d):
    s = 0.0
    for x in d:
        s += x * x
    return s ** 0.5


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    odb_path = sys.argv[1]
    field = sys.argv[2] if len(sys.argv) > 2 else 'U'
    step  = sys.argv[3] if len(sys.argv) > 3 else 'Step-1'
    frame = int(sys.argv[4]) if len(sys.argv) > 4 else 1

    odb = openOdb(odb_path, readOnly=True)
    if step not in odb.steps:
        print('[!] 没有 step', step, ' 可用:', list(odb.steps.keys()))
        sys.exit(1)
    frames = odb.steps[step].frames
    if frame >= len(frames):
        print('[!] frame', frame, '越界, 该 step 只有', len(frames), '帧')
        sys.exit(1)
    fo = frames[frame].fieldOutputs[field]
    comp_labels = list(fo.componentLabels)
    print('field=%s  componentLabels=%s  position(s)=%s'
          % (field, comp_labels, [str(l.position) for l in fo.locations]))

    out_csv = 'abaqus_%s_nodal.csv' % field
    f = _open_csv(out_csv)
    w = csv.writer(f)
    w.writerow(['instance', 'nodeLabel'] + comp_labels + ['MAG'])

    g_rng = {}   # 全装配 {comp: [min,max,count]}

    def upd(rng, key, x):
        if key not in rng:
            rng[key] = [x, x, 1]
        else:
            if x < rng[key][0]:
                rng[key][0] = x
            if x > rng[key][1]:
                rng[key][1] = x
            rng[key][2] += 1

    for iname in odb.rootAssembly.instances.keys():
        inst = odb.rootAssembly.instances[iname]
        try:
            sub = fo.getSubset(region=inst)
        except Exception:
            continue
        i_rng = {}
        n = 0
        for v in sub.values:
            raw = v.data
            d = list(raw) if hasattr(raw, '__len__') else [raw]
            m = _mag(d)
            nlab = getattr(v, 'nodeLabel', -1)
            w.writerow([iname, nlab] + d + [m])
            for ci, cl in enumerate(comp_labels):
                if ci < len(d):
                    upd(i_rng, cl, d[ci])
                    upd(g_rng, cl, d[ci])
            upd(i_rng, 'MAG', m)
            upd(g_rng, 'MAG', m)
            n += 1
        if n:
            print('[instance %s] n_node=%d' % (iname, n))
            for cl in comp_labels + ['MAG']:
                if cl in i_rng:
                    lo, hi, c = i_rng[cl]
                    print('  %-8s min=%-13.6g max=%-13.6g (n=%d)' % (cl, lo, hi, c))

    f.close()
    print('\n已写出:', out_csv)
    print('=== 全装配(global) field=%s %s frame=%d ===' % (field, step, frame))
    for cl in comp_labels + ['MAG']:
        if cl in g_rng:
            lo, hi, c = g_rng[cl]
            print('  %-8s min=%-13.6g max=%-13.6g (n=%d)' % (cl, lo, hi, c))

    odb.close()


if __name__ == '__main__':
    main()
