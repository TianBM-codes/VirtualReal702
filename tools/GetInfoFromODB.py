from tools.odb_client import ODBClient

c = ODBClient()

# 读几何
geo = c.get_render_buffers(odb_id, "PART-1-1")
positions = geo["positions"]  # numpy [Rf*3, 3] float32

# 读结果云图颜色
colors = c.get_frame_colors(odb_id, "PART-1-1", "Step-1", "S", frame=5, component="MISES")
print(colors["val_min"], colors["val_max"])

# 点选
result = c.pick(odb_id, "PART-1-1", render_face_idx=1234, step="Step-1", field="U")
print(result["odb"]["elem_label"])
