import h5py
import os

workspace = r'E:\code\odb-service-design\odb-service-design\tmp'

print("=== l2/geometry/PART-1-1_surface.h5 ===")
with h5py.File(os.path.join(workspace, 'l2', 'geometry', 'PART-1-1_surface.h5'), 'r') as f:
    def show(name, obj):
        if hasattr(obj, 'shape'):
            print('  /{:<40} {}'.format(name, obj.shape))
    f.visititems(show)

print()
print("=== l2/render/PART-1-1_render.h5 ===")
with h5py.File(os.path.join(workspace, 'l2', 'render', 'PART-1-1_render.h5'), 'r') as f:
    def show(name, obj):
        if hasattr(obj, 'shape'):
            print('  /{:<40} {}'.format(name, obj.shape))
    f.visititems(show)
