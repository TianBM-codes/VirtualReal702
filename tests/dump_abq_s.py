# -*- coding: utf-8 -*-
"""导出 Abaqus ODB 里某个场的原始值到 CSV，跨全部 instance。

同时导出两种位置(都是"未平均"的原始数据)：
  - INTEGRATION_POINT：积分点原值              -> abaqus_<field>_ip.csv
  - ELEMENT_NODAL    ：积分点外推到单元节点后的值 -> abaqus_<field>_en.csv

在装了 Abaqus 的机器上运行(Abaqus Python 2.7 或新版 Python 3):
    abaqus python tests/dump_abq_s.py <odb路径> [field=S] [step=Step-1] [frame=1] [invariant]

第 5 个参数是不变量(可选)：给了就用 getScalarField(invariant=...) 导出标量，
与本平台合成场 S_<INV> 对照。例如对 Mises：
    abaqus python tests/dump_abq_s.py model.odb S Step-1 1 MISES
        -> abaqus_S_MISES_ip.csv / abaqus_S_MISES_en.csv
这正是 Abaqus 默认 Mises 云图的口径(积分点算 Mises -> 外推标量 -> 平均)。

最后分别打印两种位置的全装配 min/max，用于和 tests/dump_ours_s.py 对照。
"""
import sys
import csv
from odbAccess import openOdb

try:
    from abaqusConstants import ELEMENT_NODAL, INTEGRATION_POINT
except Exception:
    ELEMENT_NODAL = None
    INTEGRATION_POINT = None

# 不变量名 -> abaqusConstants 常量(可用的才放进来)
INV_CONSTS = {}
for _name in ('MISES', 'TRESCA', 'PRESS', 'INV3', 'MAX_PRINCIPAL',
              'MID_PRINCIPAL', 'MIN_PRINCIPAL', 'MAGNITUDE'):
    try:
        INV_CONSTS[_name] = getattr(__import__('abaqusConstants'), _name)
    except Exception:
        pass


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
            raw = v.data
            # 标量场(不变量)的 v.data 是单个 float；张量/向量是数组
            d = list(raw) if hasattr(raw, '__len__') else [raw]
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
    inv  = sys.argv[5].upper() if len(sys.argv) > 5 else None

    odb = openOdb(odb_path, readOnly=True)
    if step not in odb.steps:
        print('[!] 没有 step', step, '可用:', list(odb.steps.keys()))
        sys.exit(1)
    frames = odb.steps[step].frames
    if frame >= len(frames):
        print('[!] frame', frame, '越界, 该 step 只有', len(frames), '帧')
        sys.exit(1)

    fo = frames[frame].fieldOutputs[field]

    # 不变量(标量)模式：getScalarField -> 单列；否则导出全部张量分量
    if inv is not None:
        if inv not in INV_CONSTS:
            print('[!] 不支持的不变量', inv, ' 可用:', sorted(INV_CONSTS.keys()))
            sys.exit(1)
        try:
            fo = fo.getScalarField(invariant=INV_CONSTS[inv])
        except Exception as exc:
            print('[!] getScalarField(%s) 失败: %s' % (inv, exc))
            sys.exit(1)
        comp_labels = [inv]
        tag = '%s_%s' % (field, inv)
        print('invariant =', inv, '(scalar via getScalarField)')
    else:
        comp_labels = list(fo.componentLabels)
        tag = field
        print('componentLabels =', comp_labels)

    print('--- INTEGRATION_POINT (积分点原值) ---')
    fo_ip = fo
    if INTEGRATION_POINT is not None:
        try:
            fo_ip = fo.getSubset(position=INTEGRATION_POINT)
        except Exception:
            fo_ip = fo
    _dump(fo_ip, comp_labels, odb, 'abaqus_%s_ip.csv' % tag, 'ip')

    print('--- ELEMENT_NODAL (外推到节点, 未平均) ---')
    if ELEMENT_NODAL is None:
        print('[!] 当前 Abaqus 不支持 abaqusConstants.ELEMENT_NODAL，跳过')
    else:
        try:
            fo_en = fo.getSubset(position=ELEMENT_NODAL)
            _dump(fo_en, comp_labels, odb, 'abaqus_%s_en.csv' % tag, 'nodeLabel')
        except Exception as exc:
            print('[!] getSubset(ELEMENT_NODAL) 失败:', exc)

    odb.close()


if __name__ == '__main__':
    main()
