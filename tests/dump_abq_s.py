# -*- coding: utf-8 -*-
"""导出 Abaqus ODB 里某个场的原始值到 CSV，跨全部 instance。

同时导出两种位置(都是"未平均"的原始数据)：
  - INTEGRATION_POINT：积分点原值              -> abaqus_<field>_ip.csv
  - ELEMENT_NODAL    ：积分点外推到单元节点后的值 -> abaqus_<field>_en.csv

在装了 Abaqus 的机器上运行(Abaqus Python 2.7 或新版 Python 3):
    abaqus python tests/dump_abq_s.py <odb路径> [field=S] [step=Step-1] [frame=1]

最后分别打印两种位置每个分量的全装配 min/max，用于和 tests/dump_ours_s.py 对照。
"""
import sys
import csv
from odbAccess import openOdb

try:
    from abaqusConstants import ELEMENT_NODAL, INTEGRATION_POINT
except Exception:
    ELEMENT_NODAL = None
    INTEGRATION_POINT = None


def _open_csv(path):
    # Abaqus 新版是 Python 3、老版是 2.7，csv 打开方式不同
    if sys.version_info[0] >= 3:
        return open(path, 'w', newline='')
    return open(path, 'wb')


def _dump(fo_pos, comp_labels, odb, out_csv, label_name):
    """fo_pos 已经是某个 position 的 FieldOutput；按 instance 切片导出。"""
    rng = {}
    f = _open_csv(out_csv)
    w = csv.writer(f)
    w.writerow(['instance', 'elemLabel', label_name] + comp_labels)

    for iname in odb.rootAssembly.instances.keys():
        inst = odb.rootAssembly.instances[iname]
        try:
            sub = fo_pos.getSubset(region=inst)
        except Exception:
            continue
        for v in sub.values:
            d = list(v.data)
            # ELEMENT_NODAL 用 nodeLabel，INTEGRATION_POINT 用 integrationPoint
            key2 = getattr(v, 'nodeLabel', None)
            if key2 is None:
                key2 = getattr(v, 'integrationPoint', -1)
            w.writerow([iname, v.elementLabel, key2] + d)
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

    print('已写出:', out_csv)
    for cl in comp_labels:
        if cl in rng:
            print('  %-6s 全装配范围: min=%.5g  max=%.5g' % (cl, rng[cl][0], rng[cl][1]))


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

    print('--- INTEGRATION_POINT (积分点原值) ---')
    fo_ip = fo
    if INTEGRATION_POINT is not None:
        try:
            fo_ip = fo.getSubset(position=INTEGRATION_POINT)
        except Exception:
            fo_ip = fo
    _dump(fo_ip, comp_labels, odb, 'abaqus_%s_ip.csv' % field, 'ip')

    print('--- ELEMENT_NODAL (外推到节点, 未平均) ---')
    if ELEMENT_NODAL is None:
        print('[!] 当前 Abaqus 不支持 abaqusConstants.ELEMENT_NODAL，跳过')
    else:
        try:
            fo_en = fo.getSubset(position=ELEMENT_NODAL)
            _dump(fo_en, comp_labels, odb, 'abaqus_%s_en.csv' % field, 'nodeLabel')
        except Exception as exc:
            print('[!] getSubset(ELEMENT_NODAL) 失败:', exc)

    odb.close()


if __name__ == '__main__':
    main()
