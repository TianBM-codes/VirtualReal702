# -*- coding: utf-8 -*-
"""导出 Abaqus ODB 里某个场的"积分点(未平均)"原始值到 CSV，跨全部 instance。

在装了 Abaqus 的机器上运行(Abaqus Python 2.7):
    abaqus python tests/dump_abq_s.py <odb路径> [field=S] [step=Step-1] [frame=1]

导出列: instance, elemLabel, ip, <各分量...>
最后打印每个分量的全装配积分点 min/max，用于和 tests/dump_ours_s.py 的输出对照。
"""
import sys
import csv
from odbAccess import openOdb


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    odb_path = sys.argv[1]
    field = sys.argv[2] if len(sys.argv) > 2 else 'S'
    step = sys.argv[3] if len(sys.argv) > 3 else 'Step-1'
    frame = int(sys.argv[4]) if len(sys.argv) > 4 else 1

    odb = openOdb(odb_path, readOnly=True)
    if step not in odb.steps:
        print('[!] 没有 step', step, '可用:', list(odb.steps.keys()))
        sys.exit(1)
    frames = odb.steps[step].frames
    if frame >= len(frames):
        print('[!] frame', frame, '越界, 该 step 只有', len(frames), '帧')
        sys.exit(1)

    fo = frames[frame].fieldOutputs[field]
    comp_labels = list(fo.componentLabels)
    print('componentLabels =', comp_labels)

    rng = {}  # comp -> [min, max]
    out_csv = 'abaqus_%s_ip.csv' % field
    f = open(out_csv, 'wb')
    w = csv.writer(f)
    w.writerow(['instance', 'elemLabel', 'ip'] + comp_labels)

    for iname in odb.rootAssembly.instances.keys():
        inst = odb.rootAssembly.instances[iname]
        try:
            sub = fo.getSubset(region=inst)
        except Exception:
            continue
        for v in sub.values:
            d = list(v.data)
            w.writerow([iname, v.elementLabel, v.integrationPoint] + d)
            for ci, cl in enumerate(comp_labels):
                if ci >= len(d):
                    continue
                x = d[ci]
                if cl not in rng:
                    rng[cl] = [x, x]
                else:
                    if x < rng[cl][0]:
                        rng[cl][0] = x
                    if x > rng[cl][1]:
                        rng[cl][1] = x
    f.close()
    odb.close()

    print('已写出:', out_csv)
    for cl in comp_labels:
        if cl in rng:
            print('  %-6s 全装配积分点范围: min=%.5g  max=%.5g' % (cl, rng[cl][0], rng[cl][1]))


if __name__ == '__main__':
    main()
