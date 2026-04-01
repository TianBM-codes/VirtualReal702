import h5py
import glob
import os
import numpy as np

workspace = r'E:\code\odb-service-design\odb-service-design\tmp'
l1_dir = os.path.join(workspace, 'l1')

# 几何
geom_path = os.path.join(l1_dir, 'geometry', 'PART-1-1.h5')
print("=== 几何 ===")
with h5py.File(geom_path, 'r') as f:
    print("节点数:", len(f['nodes/labels']))
    total = 0
    for etype in f['elements']:
        cnt = len(f['elements'][etype]['labels'])
        total += cnt
        print("  单元类型 {}: {}个".format(etype, cnt))
    print("单元总数:", total)

# 结果文件
print("\n=== 结果文件 ===")
results_dir = os.path.join(l1_dir, 'results')
for h5f in sorted(glob.glob(os.path.join(results_dir, '*.h5'))):
    fname = os.path.basename(h5f)
    with h5py.File(h5f, 'r') as f:
        frames = len(f['frame_index/frame_values'])
        comps = list(f['meta/components'].asstr())
        field_type = "标量" if len(comps) == 0 else \
                     "向量" if len(comps) <= 3 else "张量"
        note = "（无分量，正常）" if len(comps) == 0 else ""
        print("  {} : {}帧  {} 分量={}{}".format(
            fname, frames, field_type, comps, note))

        # 对标量字段，打印实际数据形状
        if len(comps) == 0:
            print("    数据集:")
            def show_ds(name, obj):
                if hasattr(obj, 'shape') and name != 'frame_index/frame_values':
                    print("      /{} shape={}".format(name, obj.shape))
            f.visititems(show_ds)
