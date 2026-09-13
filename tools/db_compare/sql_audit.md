# MySQL SQL 到达梦 SQL 审计报告

生成时间：2026-09-13T17:58:43
SQL 数量：403

## 状态统计

- matched: 2
- skipped: 401

## 明细

### SQL0001 db.py:1131

- 类型：`DROP`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DROP TABLE IF EXISTS {expr}
```

达梦候选:

```sql
DROP TABLE IF EXISTS {expr}
```

### SQL0002 db.py:1134

- 类型：`SELECT`
- 特性：`information_schema`
- 参数数量：`3`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s AND TABLE_NAME IN (%s, %s)
```

达梦候选:

```sql
SELECT TABLE_NAME FROM ALL_TABLES WHERE UPPER(OWNER)=UPPER(?) AND UPPER(TABLE_NAME) IN (UPPER(?), UPPER(?))
```

### SQL0003 db.py:1145

- 类型：`RENAME`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
RENAME TABLE {expr} TO {expr}
```

达梦候选:

```sql
RENAME TABLE {expr} TO {expr}
```

### SQL0004 db.py:1148

- 类型：`SELECT`
- 特性：`information_schema, limit`
- 参数数量：`3`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = %s LIMIT 1
```

达梦候选:

```sql
SELECT 1 FROM ALL_TAB_COLUMNS WHERE UPPER(OWNER)=UPPER(?) AND UPPER(TABLE_NAME)=UPPER(?) AND UPPER(COLUMN_NAME)=UPPER(?) FETCH FIRST 1 ROWS ONLY
```

### SQL0005 db.py:1160

- 类型：`ALTER`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
ALTER TABLE {expr} ADD COLUMN {expr}
```

达梦候选:

```sql
ALTER TABLE {expr} ADD COLUMN {expr}
```

### SQL0006 db.py:1217

- 类型：`UPDATE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
UPDATE t_mt_py_test_node SET origin_x = COALESCE(origin_x, x), origin_y = COALESCE(origin_y, y), origin_z = COALESCE(origin_z, z) WHERE origin_x IS NULL OR origin_y IS NULL OR origin_z IS NULL
```

达梦候选:

```sql
UPDATE t_mt_py_test_node SET origin_x = COALESCE(origin_x, x), origin_y = COALESCE(origin_y, y), origin_z = COALESCE(origin_z, z) WHERE origin_x IS NULL OR origin_y IS NULL OR origin_z IS NULL
```

### SQL0007 db.py:1226

- 类型：`UPDATE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
UPDATE t_mt_measuring_point_info SET x_position_ori = COALESCE(x_position_ori, x_position), y_position_ori = COALESCE(y_position_ori, y_position), z_position_ori = COALESCE(z_position_ori, z_position) WHERE x_position_ori IS NULL OR y_position_ori IS NULL OR z_position_ori IS NULL
```

达梦候选:

```sql
UPDATE t_mt_measuring_point_info SET x_position_ori = COALESCE(x_position_ori, x_position), y_position_ori = COALESCE(y_position_ori, y_position), z_position_ori = COALESCE(z_position_ori, z_position) WHERE x_position_ori IS NULL OR y_position_ori IS NULL OR z_position_ori IS NULL
```

### SQL0008 db.py:1335

- 类型：`SELECT`
- 特性：`information_schema, limit`
- 参数数量：`3`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = %s LIMIT 1
```

达梦候选:

```sql
SELECT 1 FROM ALL_TAB_COLUMNS WHERE UPPER(OWNER)=UPPER(?) AND UPPER(TABLE_NAME)=UPPER(?) AND UPPER(COLUMN_NAME)=UPPER(?) FETCH FIRST 1 ROWS ONLY
```

### SQL0009 db.py:1347

- 类型：`ALTER`
- 特性：`inline_comment`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
ALTER TABLE t_mt_py_fem_shell_property ADD COLUMN element_set VARCHAR(255) NULL COMMENT '单元集名称'
```

达梦候选:

```sql
ALTER TABLE t_mt_py_fem_shell_property ADD COLUMN element_set VARCHAR(255) NULL
```

### SQL0010 db.py:1458

- 类型：`SELECT`
- 特性：`information_schema, limit`
- 参数数量：`3`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = %s LIMIT 1
```

达梦候选:

```sql
SELECT 1 FROM ALL_TAB_COLUMNS WHERE UPPER(OWNER)=UPPER(?) AND UPPER(TABLE_NAME)=UPPER(?) AND UPPER(COLUMN_NAME)=UPPER(?) FETCH FIRST 1 ROWS ONLY
```

### SQL0011 db.py:1470

- 类型：`SELECT`
- 特性：`information_schema, limit`
- 参数数量：`3`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = %s LIMIT 1
```

达梦候选:

```sql
SELECT 1 FROM ALL_TAB_COLUMNS WHERE UPPER(OWNER)=UPPER(?) AND UPPER(TABLE_NAME)=UPPER(?) AND UPPER(COLUMN_NAME)=UPPER(?) FETCH FIRST 1 ROWS ONLY
```

### SQL0012 db.py:1483

- 类型：`ALTER`
- 特性：`inline_comment, backtick_identifier, change_column`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：CHANGE COLUMN 需要拆成达梦 ALTER TABLE RENAME COLUMN / MODIFY。；只自动执行只读 SELECT

MySQL:

```sql
ALTER TABLE t_mt_py_fem_layered_property CHANGE COLUMN `Offset` Offset_L DOUBLE NULL COMMENT 'Offset'
```

达梦候选:

```sql
ALTER TABLE t_mt_py_fem_layered_property CHANGE COLUMN "Offset" Offset_L DOUBLE NULL
```

### SQL0013 db.py:1499

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_test_node WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_test_node WHERE pid = {expr}
```

### SQL0014 db.py:1500

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_test_element WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_test_element WHERE pid = {expr}
```

### SQL0015 db.py:1501

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_test_modal_frequency WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_test_modal_frequency WHERE pid = {expr}
```

### SQL0016 db.py:1502

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_test_modal_shape WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_test_modal_shape WHERE pid = {expr}
```

### SQL0017 db.py:1503

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_test_modal_shape_imag WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_test_modal_shape_imag WHERE pid = {expr}
```

### SQL0018 db.py:1504

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_test_modal_shape_real WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_test_modal_shape_real WHERE pid = {expr}
```

### SQL0019 db.py:1505

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_test_static_result WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_test_static_result WHERE pid = {expr}
```

### SQL0020 db.py:1506

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_node_pairs WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_node_pairs WHERE pid = {expr}
```

### SQL0021 db.py:1507

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_dof_pairs WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_dof_pairs WHERE pid = {expr}
```

### SQL0022 db.py:1508

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_node_match WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_node_match WHERE pid = {expr}
```

### SQL0023 db.py:1509

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_dof_match WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_dof_match WHERE pid = {expr}
```

### SQL0024 db.py:1510

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_dynamic_response_catalog WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_dynamic_response_catalog WHERE pid = {expr}
```

### SQL0025 db.py:1511

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = {expr}
```

### SQL0026 db.py:1515

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_coord WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_coord WHERE pid = {expr}
```

### SQL0027 db.py:1516

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_material_overview WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_material_overview WHERE pid = {expr}
```

### SQL0028 db.py:1517

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_isotropic WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_isotropic WHERE pid = {expr}
```

### SQL0029 db.py:1518

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_ortho2d WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_ortho2d WHERE pid = {expr}
```

### SQL0030 db.py:1519

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_aniso3d WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_aniso3d WHERE pid = {expr}
```

### SQL0031 db.py:1520

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_property WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_property WHERE pid = {expr}
```

### SQL0032 db.py:1521

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_shell_property WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_shell_property WHERE pid = {expr}
```

### SQL0033 db.py:1522

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_beam_property WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_beam_property WHERE pid = {expr}
```

### SQL0034 db.py:1523

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_solid_property WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_solid_property WHERE pid = {expr}
```

### SQL0035 db.py:1524

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_layered_property WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_layered_property WHERE pid = {expr}
```

### SQL0036 db.py:1525

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_boundary WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_boundary WHERE pid = {expr}
```

### SQL0037 db.py:1526

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_node_pairs WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_node_pairs WHERE pid = {expr}
```

### SQL0038 db.py:1527

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_transform_operation WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_transform_operation WHERE pid = {expr}
```

### SQL0039 db.py:1528

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_dof_pairs WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_dof_pairs WHERE pid = {expr}
```

### SQL0040 db.py:1529

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_static_shape_pairs WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_static_shape_pairs WHERE pid = {expr}
```

### SQL0041 db.py:1530

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_quantity_set_capability WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_quantity_set_capability WHERE pid = {expr}
```

### SQL0042 db.py:1531

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_selected_parameter WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_selected_parameter WHERE pid = {expr}
```

### SQL0043 db.py:1532

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_sol200_parameter_config WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_sol200_parameter_config WHERE pid = {expr}
```

### SQL0044 db.py:1533

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_parameter_definition WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_parameter_definition WHERE pid = {expr}
```

### SQL0045 db.py:1534

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_parameter_target WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_parameter_target WHERE pid = {expr}
```

### SQL0046 db.py:1535

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_static_sensitivity_response_catalog WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_static_sensitivity_response_catalog WHERE pid = {expr}
```

### SQL0047 db.py:1536

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_sol200_response_config WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_sol200_response_config WHERE pid = {expr}
```

### SQL0048 db.py:1537

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_node_octree_cache WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_node_octree_cache WHERE pid = {expr}
```

### SQL0049 db.py:1538

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_node_match WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_node_match WHERE pid = {expr}
```

### SQL0050 db.py:1539

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_dof_match WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_dof_match WHERE pid = {expr}
```

### SQL0051 db.py:1540

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_dynamic_response_catalog WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_dynamic_response_catalog WHERE pid = {expr}
```

### SQL0052 db.py:1541

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_modal_result WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_modal_result WHERE pid = {expr}
```

### SQL0053 db.py:1542

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_static_result WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_static_result WHERE pid = {expr}
```

### SQL0054 db.py:1543

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = {expr}
```

### SQL0055 db.py:1544

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_response_difference WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_response_difference WHERE pid = {expr}
```

### SQL0056 db.py:1545

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_parameter_variation WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_parameter_variation WHERE pid = {expr}
```

### SQL0057 db.py:1546

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_tracking_iteration WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_tracking_iteration WHERE pid = {expr}
```

### SQL0058 db.py:1547

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_tracking_value WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_tracking_value WHERE pid = {expr}
```

### SQL0059 db.py:1548

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_bayesian_iteration_metric WHERE project_id = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_bayesian_iteration_metric WHERE project_id = {expr}
```

### SQL0060 db.py:1549

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_relevance_tracking WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_relevance_tracking WHERE pid = {expr}
```

### SQL0061 db.py:1550

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_analysis_error WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_analysis_error WHERE pid = {expr}
```

### SQL0062 db.py:1551

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_model_update_static_result WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_model_update_static_result WHERE pid = {expr}
```

### SQL0063 db.py:1552

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_model_update_modal_result WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_model_update_modal_result WHERE pid = {expr}
```

### SQL0064 db.py:1553

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_dac_dsf WHERE pid = {expr}
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_dac_dsf WHERE pid = {expr}
```

### SQL0065 db.py:16

- 类型：`CREATE`
- 特性：`engine_charset, inline_comment, backtick_identifier, key_index_ddl`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_console_log ( pid BIGINT NOT NULL COMMENT '工程ID', `time` BIGINT NOT NULL COMMENT '日志时间戳(毫秒)', log_text VARCHAR(2048) NOT NULL COMMENT 'HTML格式日志内容', KEY idx_pid_time (pid, `time`) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='控制台日志表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_console_log ( pid BIGINT NOT NULL, "time" BIGINT NOT NULL, log_text VARCHAR(2048) NOT NULL, );COMMENT='控制台日志表';
```

### SQL0066 db.py:24

- 类型：`CREATE`
- 特性：`inline_comment`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_test_node ( nid VARCHAR(100) NOT NULL COMMENT '主键ID', pid BIGINT NOT NULL COMMENT '工程ID', fid BIGINT NOT NULL COMMENT '文件ID', ics INT NOT NULL COMMENT '输入坐标系', ocs INT NOT NULL COMMENT '输出坐标系', x DOUBLE NOT NULL COMMENT 'X坐标值', y DOUBLE NOT NULL COMMENT 'Y坐标值', z DOUBLE NOT NULL COMMENT 'Z坐标值', origin_x DOUBLE NULL COMMENT '原始X坐标值', origin_y DOUBLE NULL COMMENT '原始Y坐标值', origin_z DOUBLE NULL COMMENT '原始Z坐标值', PRIMARY KEY (nid, pid, fid) ) COMMENT='模态试验测点坐标表'
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_test_node ( nid VARCHAR(100) NOT NULL, pid BIGINT NOT NULL, fid BIGINT NOT NULL, ics INT NOT NULL, ocs INT NOT NULL, x DOUBLE NOT NULL, y DOUBLE NOT NULL, z DOUBLE NOT NULL, origin_x DOUBLE NULL, origin_y DOUBLE NULL, origin_z DOUBLE NULL, PRIMARY KEY (nid, pid, fid) );
```

### SQL0067 db.py:40

- 类型：`CREATE`
- 特性：`inline_comment`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_test_element ( element_no INT NOT NULL COMMENT '单元编号', pid BIGINT NOT NULL COMMENT '工程ID', element_type VARCHAR(50) NOT NULL COMMENT '单元类型', point1 INT NULL COMMENT '节点1编号', point2 INT NULL COMMENT '节点2编号', point3 INT NULL COMMENT '节点3编号', point4 INT NULL COMMENT '节点4编号', PRIMARY KEY (element_no, pid) ) COMMENT='模态试验测试单元信息表'
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_test_element ( element_no INT NOT NULL, pid BIGINT NOT NULL, element_type VARCHAR(50) NOT NULL, point1 INT NULL, point2 INT NULL, point3 INT NULL, point4 INT NULL, PRIMARY KEY (element_no, pid) );
```

### SQL0068 db.py:52

- 类型：`CREATE`
- 特性：`inline_comment`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_test_modal_frequency ( mode_no INT NOT NULL COMMENT '振型编号', pid BIGINT NOT NULL COMMENT '工程ID', fid BIGINT NOT NULL COMMENT '文件ID', frequency DOUBLE NOT NULL COMMENT '频率值', damping DOUBLE NOT NULL COMMENT '阻尼比', eigenvalue_Re DOUBLE COMMENT '特征值实部', eigenvalue_Im DOUBLE COMMENT '特征值虚部', PRIMARY KEY (mode_no, pid, fid) ) COMMENT='模态试验频率数据表'
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_test_modal_frequency ( mode_no INT NOT NULL, pid BIGINT NOT NULL, fid BIGINT NOT NULL, frequency DOUBLE NOT NULL, damping DOUBLE NOT NULL, eigenvalue_Re DOUBLE, eigenvalue_Im DOUBLE, PRIMARY KEY (mode_no, pid, fid) );
```

### SQL0069 db.py:64

- 类型：`CREATE`
- 特性：`inline_comment, json_type`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_test_modal_shape ( mode_no INT NOT NULL COMMENT '振型编号', pid BIGINT NOT NULL COMMENT '工程ID', modal_shape JSON NOT NULL COMMENT '振型数据', PRIMARY KEY (mode_no, pid) ) COMMENT='试验振动模态数据表'
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_test_modal_shape ( mode_no INT NOT NULL, pid BIGINT NOT NULL, modal_shape CLOB NOT NULL, PRIMARY KEY (mode_no, pid) );
```

### SQL0070 db.py:72

- 类型：`CREATE`
- 特性：`inline_comment`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_test_modal_shape_imag ( mode_no INT NOT NULL COMMENT '振型编号', pid BIGINT NOT NULL COMMENT '工程ID', point INT NOT NULL COMMENT '节点ID', re_ux DOUBLE COMMENT 'x方向实部', im_ux DOUBLE COMMENT 'x方向虚部', re_uy DOUBLE COMMENT 'y方向实部', im_uy DOUBLE COMMENT 'y方向虚部', re_uz DOUBLE COMMENT 'z方向实部', im_uz DOUBLE COMMENT 'z方向虚部', PRIMARY KEY (mode_no, pid, point) ) COMMENT='试验振动模态数据表--复模态节点振型'
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_test_modal_shape_imag ( mode_no INT NOT NULL, pid BIGINT NOT NULL, point INT NOT NULL, re_ux DOUBLE, im_ux DOUBLE, re_uy DOUBLE, im_uy DOUBLE, re_uz DOUBLE, im_uz DOUBLE, PRIMARY KEY (mode_no, pid, point) );
```

### SQL0071 db.py:86

- 类型：`CREATE`
- 特性：`inline_comment`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_test_modal_shape_real ( mode_no INT NOT NULL COMMENT '振型编号', pid BIGINT NOT NULL COMMENT '工程ID', point INT NOT NULL COMMENT '节点ID', ux DOUBLE COMMENT 'x方向位移分量', uy DOUBLE COMMENT 'y方向位移分量', uz DOUBLE COMMENT 'z方向位移分量', PRIMARY KEY (mode_no, pid, point) ) COMMENT='试验振动模态数据表--复模态节点振型'
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_test_modal_shape_real ( mode_no INT NOT NULL, pid BIGINT NOT NULL, point INT NOT NULL, ux DOUBLE, uy DOUBLE, uz DOUBLE, PRIMARY KEY (mode_no, pid, point) );
```

### SQL0072 db.py:97

- 类型：`CREATE`
- 特性：`auto_increment, engine_charset, inline_comment, json_type, key_index_ddl`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_test_static_result ( id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID', pid BIGINT NOT NULL COMMENT '工程ID', fid BIGINT NOT NULL COMMENT '文件ID', load_case_no INT NOT NULL COMMENT '载荷工况号', result_no INT NOT NULL COMMENT '结果序号', point INT NOT NULL COMMENT '测点ID', ux DOUBLE NULL COMMENT 'X方向位移', uy DOUBLE NULL COMMENT 'Y方向位移', uz DOUBLE NULL COMMENT 'Z方向位移', rx DOUBLE NULL COMMENT 'X方向转角', ry DOUBLE NULL COMMENT 'Y方向转角', rz DOUBLE NULL COMMENT 'Z方向转角', load_factor DOUBLE NULL COMMENT '载荷因子', extra_json JSON NULL COMMENT '扩展信息', created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间', PRIMARY KEY (id), UNIQUE KEY uk_pid_fid_case_result_point (pid, fid, load_case_no, result_no, point), KEY idx_pid_case_result (pid, load_case_no, result_no), KEY idx_pid_point (pid, point) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='试验静力结果表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_test_static_result ( id BIGINT NOT NULL IDENTITY(1,1), pid BIGINT NOT NULL, fid BIGINT NOT NULL, load_case_no INT NOT NULL, result_no INT NOT NULL, point INT NOT NULL, ux DOUBLE NULL, uy DOUBLE NULL, uz DOUBLE NULL, rx DOUBLE NULL, ry DOUBLE NULL, rz DOUBLE NULL, load_factor DOUBLE NULL, extra_json CLOB NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (id), CONSTRAINT uk_pid_fid_case_result_point UNIQUE (pid, fid, load_case_no, result_no, point), );COMMENT='试验静力结果表';
```

### SQL0073 db.py:120

- 类型：`CREATE`
- 特性：`auto_increment, engine_charset, inline_comment, json_type, on_update_current_timestamp, key_index_ddl`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_test_frf_curve ( id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID', pid BIGINT NOT NULL COMMENT '工程ID', curve_name VARCHAR(255) NOT NULL COMMENT '曲线名称', curve_no INT NOT NULL COMMENT '曲线序号', title VARCHAR(255) NULL COMMENT '标题', frf_type VARCHAR(64) NULL COMMENT '频响类型', response_node INT NULL COMMENT '响应节点', response_dir INT NULL COMMENT '响应方向', reference_node INT NULL COMMENT '激励节点', reference_dir INT NULL COMMENT '激励方向', x_type INT NULL COMMENT '横坐标类型', y_type INT NULL COMMENT '纵坐标类型', denominator_type INT NULL COMMENT '分母物理量类型', z_type INT NULL COMMENT 'Z类型', ordinate_type INT NULL COMMENT '纵坐标数据类型', abscissa_spacing INT NULL COMMENT '横坐标间距类型', n_points INT NOT NULL DEFAULT 0 COMMENT '点数', source_file VARCHAR(1024) NULL COMMENT '源文件路径', extra_json JSON NULL COMMENT '扩展信息', created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间', updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间', PRIMARY KEY (id), UNIQUE KEY uk_pid_curve_name (pid, curve_name), KEY idx_pid_curve_no (pid, curve_no) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='试验FRF曲线信息表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_test_frf_curve ( id BIGINT NOT NULL IDENTITY(1,1), pid BIGINT NOT NULL, curve_name VARCHAR(255) NOT NULL, curve_no INT NOT NULL, title VARCHAR(255) NULL, frf_type VARCHAR(64) NULL, response_node INT NULL, response_dir INT NULL, reference_node INT NULL, reference_dir INT NULL, x_type INT NULL, y_type INT NULL, denominator_type INT NULL, z_type INT NULL, ordinate_type INT NULL, abscissa_spacing INT NULL, n_points INT NOT NULL DEFAULT 0, source_file VARCHAR(1024) NULL, extra_json CLOB NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (id), CONSTRAINT uk_pid_curve_name UNIQUE (pid, curve_name), );COMMENT='试验FRF曲线信息表';
```

### SQL0074 db.py:148

- 类型：`CREATE`
- 特性：`auto_increment, engine_charset, inline_comment, key_index_ddl`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_test_frf_point ( id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID', pid BIGINT NOT NULL COMMENT '工程ID', curve_id BIGINT NOT NULL COMMENT '曲线ID', point_no INT NOT NULL COMMENT '点序号', frequency DOUBLE NOT NULL COMMENT '频率', real_value DOUBLE NOT NULL COMMENT '实部', imag_value DOUBLE NOT NULL COMMENT '虚部', created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间', PRIMARY KEY (id), UNIQUE KEY uk_pid_curve_point (pid, curve_id, point_no), KEY idx_pid_curve_freq (pid, curve_id, frequency), KEY idx_curve_id (curve_id) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='试验FRF曲线点数据表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_test_frf_point ( id BIGINT NOT NULL IDENTITY(1,1), pid BIGINT NOT NULL, curve_id BIGINT NOT NULL, point_no INT NOT NULL, frequency DOUBLE NOT NULL, real_value DOUBLE NOT NULL, imag_value DOUBLE NOT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (id), CONSTRAINT uk_pid_curve_point UNIQUE (pid, curve_id, point_no), );COMMENT='试验FRF曲线点数据表';
```

### SQL0075 db.py:164

- 类型：`CREATE`
- 特性：`auto_increment, inline_comment`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_test_coord ( id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID', pid BIGINT NOT NULL COMMENT '工程ID', coord_no INT NOT NULL COMMENT '坐标编号', ref_coord_no INT NOT NULL COMMENT '参考坐标编号', PRIMARY KEY (id, pid) ) COMMENT='测试坐标信息表'
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_test_coord ( id INT NOT NULL IDENTITY(1,1), pid BIGINT NOT NULL, coord_no INT NOT NULL, ref_coord_no INT NOT NULL, PRIMARY KEY (id, pid) );
```

### SQL0076 db.py:173

- 类型：`CREATE`
- 特性：`auto_increment, inline_comment`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_coord ( id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID', pid BIGINT NOT NULL COMMENT '工程ID', fid BIGINT NOT NULL COMMENT '文件ID', coord_no INT NOT NULL COMMENT '坐标编号', ref_coord_no INT NOT NULL COMMENT '参考坐标编号', coord_type VARCHAR(32) NOT NULL COMMENT '坐标类型', x1 DOUBLE NOT NULL COMMENT '坐标分量X1', x2 DOUBLE NOT NULL COMMENT '坐标分量X2', x3 DOUBLE NOT NULL COMMENT '坐标分量X3', x4 DOUBLE NOT NULL COMMENT '坐标分量X4', x5 DOUBLE NOT NULL COMMENT '坐标分量X5', x6 DOUBLE NOT NULL COMMENT '坐标分量X6', x7 DOUBLE NOT NULL COMMENT '坐标分量X7', x8 DOUBLE NOT NULL COMMENT '坐标分量X8', x9 DOUBLE NOT NULL COMMENT '坐标分量X9', PRIMARY KEY (id, pid, fid) ) COMMENT='有限元坐标信息表'
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_coord ( id INT NOT NULL IDENTITY(1,1), pid BIGINT NOT NULL, fid BIGINT NOT NULL, coord_no INT NOT NULL, ref_coord_no INT NOT NULL, coord_type VARCHAR(32) NOT NULL, x1 DOUBLE NOT NULL, x2 DOUBLE NOT NULL, x3 DOUBLE NOT NULL, x4 DOUBLE NOT NULL, x5 DOUBLE NOT NULL, x6 DOUBLE NOT NULL, x7 DOUBLE NOT NULL, x8 DOUBLE NOT NULL, x9 DOUBLE NOT NULL, PRIMARY KEY (id, pid, fid) );
```

### SQL0077 db.py:193

- 类型：`CREATE`
- 特性：`inline_comment`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_material_overview ( Id INT NOT NULL COMMENT '主键ID', pid BIGINT NOT NULL COMMENT '工程ID', Type VARCHAR(32) NOT NULL COMMENT '材料名称', PRIMARY KEY (Id, pid) ) COMMENT='有限元模型材料总览表'
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_material_overview ( Id INT NOT NULL, pid BIGINT NOT NULL, Type VARCHAR(32) NOT NULL, PRIMARY KEY (Id, pid) );
```

### SQL0078 db.py:201

- 类型：`CREATE`
- 特性：`inline_comment`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_isotropic ( Id INT NOT NULL COMMENT '主键ID', pid BIGINT NOT NULL COMMENT '工程ID', RHO DOUBLE NOT NULL COMMENT '密度', E DOUBLE NOT NULL COMMENT '弹性模量', NU DOUBLE NOT NULL COMMENT '泊松比', GE DOUBLE NOT NULL COMMENT '材料阻尼', PRIMARY KEY (Id, pid) ) COMMENT='有限元模型各向同性材料表'
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_isotropic ( Id INT NOT NULL, pid BIGINT NOT NULL, RHO DOUBLE NOT NULL, E DOUBLE NOT NULL, NU DOUBLE NOT NULL, GE DOUBLE NOT NULL, PRIMARY KEY (Id, pid) );
```

### SQL0079 db.py:212

- 类型：`CREATE`
- 特性：`inline_comment`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_ortho2d ( Id INT NOT NULL COMMENT 'primary key', pid BIGINT NOT NULL COMMENT 'project id', RHO DOUBLE NULL COMMENT '密度', EX DOUBLE NULL COMMENT '弹性模量x方向', EY DOUBLE NULL COMMENT '弹性模量y方向', GXY DOUBLE NULL COMMENT '剪切模量XY', NUXY DOUBLE NULL COMMENT '泊松比XY', GXZ DOUBLE NULL COMMENT '剪切模量XZ', GYZ DOUBLE NULL COMMENT '剪切模量YZ', GE DOUBLE NULL COMMENT '材料阻尼', PRIMARY KEY (Id, pid) ) COMMENT='FEM orthotropic 2D materials'
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_ortho2d ( Id INT NOT NULL, pid BIGINT NOT NULL, RHO DOUBLE NULL, EX DOUBLE NULL, EY DOUBLE NULL, GXY DOUBLE NULL, NUXY DOUBLE NULL, GXZ DOUBLE NULL, GYZ DOUBLE NULL, GE DOUBLE NULL, PRIMARY KEY (Id, pid) );
```

### SQL0080 db.py:227

- 类型：`CREATE`
- 特性：`inline_comment`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_aniso3d ( Id INT NOT NULL COMMENT 'primary key', pid BIGINT NOT NULL COMMENT 'project id', RHO DOUBLE NULL COMMENT '密度', D11 DOUBLE NULL COMMENT 'D11', D12 DOUBLE NULL COMMENT 'D12', D13 DOUBLE NULL COMMENT 'D13', D14 DOUBLE NULL COMMENT 'D14', D15 DOUBLE NULL COMMENT 'D15', D16 DOUBLE NULL COMMENT 'D16', D22 DOUBLE NULL COMMENT 'D22', D23 DOUBLE NULL COMMENT 'D23', D24 DOUBLE NULL COMMENT 'D24', D25 DOUBLE NULL COMMENT 'D25', D26 DOUBLE NULL COMMENT 'D26', D33 DOUBLE NULL COMMENT 'D33', D34 DOUBLE NULL COMMENT 'D34', D35 DOUBLE NULL COMMENT 'D35', D36 DOUBLE NULL COMMENT 'D36', D44 DOUBLE NULL COMMENT 'D44', D45 DOUBLE NULL COMMENT 'D45', D46 DOUBLE NULL COMMENT 'D46', D55 DOUBLE NULL COMMENT 'D55', D56 DOUBLE NULL COMMENT 'D56', D66 DOUBLE NULL COMMENT 'D66', GE DOUBLE NULL COMMENT '材料阻尼', PRIMARY KEY (Id, pid) ) COMMENT='FEM anisotropic 3D materials'
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_aniso3d ( Id INT NOT NULL, pid BIGINT NOT NULL, RHO DOUBLE NULL, D11 DOUBLE NULL, D12 DOUBLE NULL, D13 DOUBLE NULL, D14 DOUBLE NULL, D15 DOUBLE NULL, D16 DOUBLE NULL, D22 DOUBLE NULL, D23 DOUBLE NULL, D24 DOUBLE NULL, D25 DOUBLE NULL, D26 DOUBLE NULL, D33 DOUBLE NULL, D34 DOUBLE NULL, D35 DOUBLE NULL, D36 DOUBLE NULL, D44 DOUBLE NULL, D45 DOUBLE NULL, D46 DOUBLE NULL, D55 DOUBLE NULL, D56 DOUBLE NULL, D66 DOUBLE NULL, GE DOUBLE NULL, PRIMARY KEY (Id, pid) );
```

### SQL0081 db.py:257

- 类型：`CREATE`
- 特性：`inline_comment`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_property ( Id INT NOT NULL COMMENT '主键ID', pid BIGINT NOT NULL COMMENT '工程ID', Type VARCHAR(32) NOT NULL COMMENT '属性名称', PRIMARY KEY (Id, pid) ) COMMENT='有限元模型属性表'
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_property ( Id INT NOT NULL, pid BIGINT NOT NULL, Type VARCHAR(32) NOT NULL, PRIMARY KEY (Id, pid) );
```

### SQL0082 db.py:265

- 类型：`CREATE`
- 特性：`inline_comment`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_shell_property ( Id INT NOT NULL COMMENT '主键ID', pid BIGINT NOT NULL COMMENT '工程ID', Thickness DOUBLE NOT NULL COMMENT '厚度', NSM DOUBLE NOT NULL COMMENT '非结构质量', THETA DOUBLE NOT NULL COMMENT '旋转角', element_set VARCHAR(255) NULL COMMENT '单元集名称', PRIMARY KEY (Id, pid) ) COMMENT='有限元模型壳单元属性表'
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_shell_property ( Id INT NOT NULL, pid BIGINT NOT NULL, Thickness DOUBLE NOT NULL, NSM DOUBLE NOT NULL, THETA DOUBLE NOT NULL, element_set VARCHAR(255) NULL, PRIMARY KEY (Id, pid) );
```

### SQL0083 db.py:276

- 类型：`CREATE`
- 特性：`inline_comment`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_beam_property ( Id INT NOT NULL COMMENT '主键ID', pid BIGINT NOT NULL COMMENT '工程ID', AX DOUBLE NOT NULL COMMENT '截面积', AY DOUBLE NOT NULL COMMENT 'Y向剪切变形缩减截面', AZ DOUBLE NOT NULL COMMENT 'Z向剪切变形缩减截面', IX DOUBLE NOT NULL COMMENT '抗扭惯性矩', IY DOUBLE NOT NULL COMMENT 'Y轴惯性矩', IZ DOUBLE NOT NULL COMMENT 'Z轴惯性矩', CW DOUBLE NOT NULL COMMENT '翘曲系数', YN DOUBLE NOT NULL COMMENT '中性轴Y向坐标', ZN DOUBLE NOT NULL COMMENT '中性轴Z向坐标', NSM DOUBLE NOT NULL COMMENT '非结构质量', PRIMARY KEY (Id, pid) ) COMMENT='有限元模型梁单元属性表'
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_beam_property ( Id INT NOT NULL, pid BIGINT NOT NULL, AX DOUBLE NOT NULL, AY DOUBLE NOT NULL, AZ DOUBLE NOT NULL, IX DOUBLE NOT NULL, IY DOUBLE NOT NULL, IZ DOUBLE NOT NULL, CW DOUBLE NOT NULL, YN DOUBLE NOT NULL, ZN DOUBLE NOT NULL, NSM DOUBLE NOT NULL, PRIMARY KEY (Id, pid) );
```

### SQL0084 db.py:293

- 类型：`CREATE`
- 特性：`inline_comment`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_solid_property ( Id INT NOT NULL COMMENT '主键ID', pid BIGINT NOT NULL COMMENT '工程ID', MID INT NULL COMMENT '材料ID', CID INT NULL COMMENT '坐标系ID', PRIMARY KEY (Id, pid) ) COMMENT='FEM solid properties'
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_solid_property ( Id INT NOT NULL, pid BIGINT NOT NULL, MID INT NULL, CID INT NULL, PRIMARY KEY (Id, pid) );
```

### SQL0085 db.py:302

- 类型：`CREATE`
- 特性：`inline_comment`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_layered_property ( Id INT NOT NULL COMMENT 'primary key', pid BIGINT NOT NULL COMMENT 'project id', Offset_L DOUBLE NULL COMMENT 'Offset', Theta DOUBLE NULL COMMENT 'Theta', GE DOUBLE NULL COMMENT 'GE', NSM DOUBLE NULL COMMENT 'NSM', Layers INT NULL COMMENT 'Layers', PRIMARY KEY (Id, pid) ) COMMENT='FEM layered properties'
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_layered_property ( Id INT NOT NULL, pid BIGINT NOT NULL, Offset_L DOUBLE NULL, Theta DOUBLE NULL, GE DOUBLE NULL, NSM DOUBLE NULL, Layers INT NULL, PRIMARY KEY (Id, pid) );
```

### SQL0086 db.py:314

- 类型：`CREATE`
- 特性：`inline_comment`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_boundary ( Id INT NOT NULL COMMENT '主键ID', pid BIGINT NOT NULL COMMENT '工程ID', Node BIGINT NOT NULL COMMENT '节点号', UX DOUBLE COMMENT 'X方向位移约束, 指定位移', UY DOUBLE COMMENT 'Y方向位移约束, 指定位移', UZ DOUBLE COMMENT 'Z方向位移约束, 指定位移', RX DOUBLE COMMENT 'X方向旋转约束, 指定位移', RY DOUBLE COMMENT 'Y方向旋转约束, 指定位移', RZ DOUBLE COMMENT 'Z方向旋转约束, 指定位移', PRIMARY KEY (Id, pid) ) COMMENT='模型约束条件'
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_boundary ( Id INT NOT NULL, pid BIGINT NOT NULL, Node BIGINT NOT NULL, UX DOUBLE, UY DOUBLE, UZ DOUBLE, RX DOUBLE, RY DOUBLE, RZ DOUBLE, PRIMARY KEY (Id, pid) );
```

### SQL0087 db.py:328

- 类型：`CREATE`
- 特性：`inline_comment`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_node_pairs ( pid INT NOT NULL COMMENT '工程ID', node INT NOT NULL COMMENT '节点编号', point INT NOT NULL COMMENT '点编号', distance FLOAT NOT NULL COMMENT '两点距离', x_offset FLOAT NOT NULL COMMENT 'X方向偏移量', y_offset FLOAT NOT NULL COMMENT 'Y方向偏移量', z_offset FLOAT NOT NULL COMMENT 'Z方向偏移量', PRIMARY KEY (pid, node, point) ) COMMENT='有限元节点配对信息表'
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_node_pairs ( pid INT NOT NULL, node INT NOT NULL, point INT NOT NULL, distance FLOAT NOT NULL, x_offset FLOAT NOT NULL, y_offset FLOAT NOT NULL, z_offset FLOAT NOT NULL, PRIMARY KEY (pid, node, point) );
```

### SQL0088 db.py:340

- 类型：`CREATE`
- 特性：`auto_increment, engine_charset, inline_comment, json_type, on_update_current_timestamp, key_index_ddl`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_transform_operation ( id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID', pid BIGINT NOT NULL COMMENT '工程ID', transform_type VARCHAR(32) NOT NULL COMMENT '变换类型（有限元/试验）', matrix4_json JSON NOT NULL COMMENT '4x4矩阵数据', created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间', updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间', PRIMARY KEY (id), UNIQUE KEY uk_pid_transform_type (pid, transform_type), KEY idx_pid_updated_at (pid, updated_at) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='空间匹配变换记录表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_transform_operation ( id BIGINT NOT NULL IDENTITY(1,1), pid BIGINT NOT NULL, transform_type VARCHAR(32) NOT NULL, matrix4_json CLOB NOT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (id), CONSTRAINT uk_pid_transform_type UNIQUE (pid, transform_type), );COMMENT='空间匹配变换记录表';
```

### SQL0089 db.py:353

- 类型：`UNKNOWN`
- 特性：`engine_charset, inline_comment, json_type, on_update_current_timestamp`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
-- project configuration table -- test_model_x/y/z: test model size -- fem_model_x/y/z: fem model size -- coefficients_json: coefficient map -- extra_json: extra config payload CREATE TABLE IF NOT EXISTS t_mt_py_project_config ( pid BIGINT NOT NULL COMMENT 'project id', test_model_x DOUBLE NULL COMMENT '试验模型x方向尺寸', test_model_y DOUBLE NULL COMMENT '试验模型y方向尺寸', test_model_z DOUBLE NULL COMMENT '试验模型z方向尺寸', fem_model_x DOUBLE NULL COMMENT '有限元模型x方向尺寸', fem_model_y DOUBLE NULL COMMENT '有限元模型y方向尺寸', fem_model_z DOUBLE NULL COMMENT '有限元模型z方向尺寸', coefficients_json JSON NULL COMMENT '系数对应表', extra_json JSON NULL COMMENT '额外系数对应表', created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'created time', updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'updated time', PRIMARY KEY (pid) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='project configuration';
```

达梦候选:

```sql
-- project configuration table -- test_model_x/y/z: test model size -- fem_model_x/y/z: fem model size -- coefficients_json: coefficient map -- extra_json: extra config payload CREATE TABLE IF NOT EXISTS t_mt_py_project_config ( pid BIGINT NOT NULL, test_model_x DOUBLE NULL, test_model_y DOUBLE NULL, test_model_z DOUBLE NULL, fem_model_x DOUBLE NULL, fem_model_y DOUBLE NULL, fem_model_z DOUBLE NULL, coefficients_json CLOB NULL, extra_json CLOB NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (pid) );COMMENT='project configuration';
```

### SQL0090 db.py:374

- 类型：`CREATE`
- 特性：`inline_comment`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_dof_pairs ( pid INT NOT NULL COMMENT '工程ID', node INT NOT NULL COMMENT '节点编号', point INT NOT NULL COMMENT '点编号', dof VARCHAR(32) NOT NULL COMMENT '自由度类型', cx FLOAT NOT NULL COMMENT 'X方向分量', cy FLOAT NOT NULL COMMENT 'Y方向分量', cz FLOAT NOT NULL COMMENT 'Z方向分量', PRIMARY KEY (pid, node, point) ) COMMENT='有限元自由度配对信息表'
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_dof_pairs ( pid INT NOT NULL, node INT NOT NULL, point INT NOT NULL, dof VARCHAR(32) NOT NULL, cx FLOAT NOT NULL, cy FLOAT NOT NULL, cz FLOAT NOT NULL, PRIMARY KEY (pid, node, point) );
```

### SQL0091 db.py:386

- 类型：`CREATE`
- 特性：`inline_comment`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_static_shape_pairs ( pid INT NOT NULL COMMENT '工程ID', fem_res VARCHAR(32) NOT NULL COMMENT '有限元计算结果', test_res VARCHAR(32) NOT NULL COMMENT '试验测试结果', DAC FLOAT NOT NULL COMMENT 'DAC(%)', DSF FLOAT NOT NULL COMMENT 'DSF', PRIMARY KEY (pid) ) COMMENT='有限元静力振型配对表'
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_static_shape_pairs ( pid INT NOT NULL, fem_res VARCHAR(32) NOT NULL, test_res VARCHAR(32) NOT NULL, DAC FLOAT NOT NULL, DSF FLOAT NOT NULL, PRIMARY KEY (pid) );
```

### SQL0092 db.py:396

- 类型：`CREATE`
- 特性：`engine_charset, inline_comment, tinyint_bool`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_supported_quantity ( quantity_code VARCHAR(32) NOT NULL COMMENT '修正量编码', quantity_name VARCHAR(200) NOT NULL COMMENT '修正量名称', unit VARCHAR(50) NULL COMMENT '单位', enabled TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否启用', sort_no INT NOT NULL DEFAULT 0 COMMENT '排序号', PRIMARY KEY (quantity_code) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='支持的修正量表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_supported_quantity ( quantity_code VARCHAR(32) NOT NULL, quantity_name VARCHAR(200) NOT NULL, unit VARCHAR(50) NULL, enabled SMALLINT NOT NULL DEFAULT 1, sort_no INT NOT NULL DEFAULT 0, PRIMARY KEY (quantity_code) );COMMENT='支持的修正量表';
```

### SQL0093 db.py:406

- 类型：`CREATE`
- 特性：`auto_increment, engine_charset, inline_comment, json_type, tinyint_bool, key_index_ddl`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_quantity_set_capability ( id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID', pid BIGINT NOT NULL COMMENT '工程ID', quantity_code VARCHAR(32) NOT NULL COMMENT '修正量编码', set_name VARCHAR(200) NOT NULL COMMENT '集合名称', set_type VARCHAR(32) NOT NULL COMMENT '集合类型', set_scope VARCHAR(32) NOT NULL COMMENT '集合范围', instance_name VARCHAR(200) NULL COMMENT '实例名称', part_name VARCHAR(200) NULL COMMENT '零件名称', set_role VARCHAR(64) NOT NULL COMMENT '集合角色', element_family VARCHAR(32) NULL COMMENT '单元族', section_type VARCHAR(64) NULL COMMENT '截面类型', material_name VARCHAR(200) NULL COMMENT '材料名称', member_count INT NOT NULL DEFAULT 0 COMMENT '成员数量', supports_global TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否支持全局参数', supports_local TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否支持局部参数', current_value DOUBLE NULL COMMENT '共享当前值', is_internal TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否为Abaqus自动生成的内部集合(_PickedSetNN)，1=内部 0=用户', extra_json JSON NULL COMMENT '扩展信息', PRIMARY KEY (id), UNIQUE KEY uk_pid_quantity_set_capability (pid, quantity_code, set_name, set_type, set_scope, instance_name, part_name), KEY idx_pid_quantity_code (pid, quantity_code), KEY idx_pid_set_name (pid, set_name, set_type, set_scope) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='修正量与集合能力目录表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_quantity_set_capability ( id BIGINT NOT NULL IDENTITY(1,1), pid BIGINT NOT NULL, quantity_code VARCHAR(32) NOT NULL, set_name VARCHAR(200) NOT NULL, set_type VARCHAR(32) NOT NULL, set_scope VARCHAR(32) NOT NULL, instance_name VARCHAR(200) NULL, part_name VARCHAR(200) NULL, set_role VARCHAR(64) NOT NULL, element_family VARCHAR(32) NULL, section_type VARCHAR(64) NULL, material_name VARCHAR(200) NULL, member_count INT NOT NULL DEFAULT 0, supports_global SMALLINT NOT NULL DEFAULT 0, supports_local SMALLINT NOT NULL DEFAULT 0, current_value DOUBLE NULL, is_internal SMALLINT NOT NULL DEFAULT 0, extra_json CLOB NULL, PRIMARY KEY (id), CONSTRAINT uk_pid_quantity_set_capability UNIQUE (pid, quantity_code, set_name, set_type, set_scope, instance_name, part_name), );COMMENT='修正量与集合能力目录表';
```

### SQL0094 db.py:432

- 类型：`CREATE`
- 特性：`auto_increment, engine_charset, inline_comment, json_type, key_index_ddl`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_selected_parameter ( id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID', pid BIGINT NOT NULL COMMENT '工程ID', parameter_group_name VARCHAR(200) NOT NULL COMMENT '参数组名称', parameter_name VARCHAR(200) NOT NULL COMMENT '参数名称', quantity_code VARCHAR(32) NOT NULL COMMENT '修正量编码', selection_mode VARCHAR(32) NOT NULL COMMENT '选择模式', set_name VARCHAR(200) NOT NULL COMMENT '集合名称', set_type VARCHAR(32) NOT NULL COMMENT '集合类型', set_scope VARCHAR(32) NOT NULL COMMENT '集合范围', instance_name VARCHAR(200) NULL COMMENT '实例名称', part_name VARCHAR(200) NULL COMMENT '零件名称', element_label BIGINT NULL COMMENT '单元标签', current_value DOUBLE NULL COMMENT '当前值', lower DOUBLE NOT NULL DEFAULT 0 COMMENT '下界', upper DOUBLE NOT NULL DEFAULT 0 COMMENT '上界', prob_id BIGINT NOT NULL DEFAULT 0 COMMENT '问题ID', scatter FLOAT NOT NULL DEFAULT 0.25 COMMENT '离散度', description VARCHAR(255) NOT NULL DEFAULT '' COMMENT '描述', extra_json JSON NULL COMMENT '扩展信息', created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间', PRIMARY KEY (id), UNIQUE KEY uk_pid_parameter_name (pid, parameter_name), KEY idx_pid_group_name (pid, parameter_group_name), KEY idx_pid_quantity_mode (pid, quantity_code, selection_mode) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='已选修正参数表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_selected_parameter ( id BIGINT NOT NULL IDENTITY(1,1), pid BIGINT NOT NULL, parameter_group_name VARCHAR(200) NOT NULL, parameter_name VARCHAR(200) NOT NULL, quantity_code VARCHAR(32) NOT NULL, selection_mode VARCHAR(32) NOT NULL, set_name VARCHAR(200) NOT NULL, set_type VARCHAR(32) NOT NULL, set_scope VARCHAR(32) NOT NULL, instance_name VARCHAR(200) NULL, part_name VARCHAR(200) NULL, element_label BIGINT NULL, current_value DOUBLE NULL, lower DOUBLE NOT NULL DEFAULT 0, upper DOUBLE NOT NULL DEFAULT 0, prob_id BIGINT NOT NULL DEFAULT 0, scatter FLOAT NOT NULL DEFAULT 0.25, description VARCHAR(255) NOT NULL DEFAULT '', extra_json CLOB NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (id), CONSTRAINT uk_pid_parameter_name UNIQUE (pid, parameter_name), );COMMENT='已选修正参数表';
```

### SQL0095 db.py:460

- 类型：`CREATE`
- 特性：`auto_increment, engine_charset, inline_comment, json_type, key_index_ddl`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_sol200_parameter_config ( id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID', pid BIGINT NOT NULL COMMENT '工程ID', parameter_no INT NOT NULL COMMENT '参数序号', parameter_name VARCHAR(200) NOT NULL COMMENT '参数名称', parameter_type VARCHAR(32) NOT NULL COMMENT '参数类型', property_id BIGINT NULL COMMENT '属性ID', material_id BIGINT NULL COMMENT '材料ID', element_id BIGINT NULL COMMENT '单元ID', initial_value DOUBLE NOT NULL COMMENT '初始值', lower_bound DOUBLE NULL COMMENT '下界', upper_bound DOUBLE NULL COMMENT '上界', extra_json JSON NULL COMMENT '扩展信息', created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间', PRIMARY KEY (id), UNIQUE KEY uk_pid_sol200_parameter_name (pid, parameter_name), UNIQUE KEY uk_pid_sol200_parameter_no (pid, parameter_no), KEY idx_pid_sol200_parameter_type (pid, parameter_type) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='SOL200 参数配置表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_sol200_parameter_config ( id BIGINT NOT NULL IDENTITY(1,1), pid BIGINT NOT NULL, parameter_no INT NOT NULL, parameter_name VARCHAR(200) NOT NULL, parameter_type VARCHAR(32) NOT NULL, property_id BIGINT NULL, material_id BIGINT NULL, element_id BIGINT NULL, initial_value DOUBLE NOT NULL, lower_bound DOUBLE NULL, upper_bound DOUBLE NULL, extra_json CLOB NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (id), CONSTRAINT uk_pid_sol200_parameter_name UNIQUE (pid, parameter_name), CONSTRAINT uk_pid_sol200_parameter_no UNIQUE (pid, parameter_no), );COMMENT='SOL200 参数配置表';
```

### SQL0096 db.py:481

- 类型：`CREATE`
- 特性：`auto_increment, engine_charset, inline_comment, json_type, key_index_ddl`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_sol200_response_config ( id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID', pid BIGINT NOT NULL COMMENT '工程ID', response_no INT NOT NULL COMMENT '响应序号', response_name VARCHAR(200) NOT NULL COMMENT '响应名称', response_type VARCHAR(32) NOT NULL COMMENT '响应类型', mode_number INT NULL COMMENT '模态阶次', extra_json JSON NULL COMMENT '扩展信息', created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间', PRIMARY KEY (id), UNIQUE KEY uk_pid_sol200_response_name (pid, response_name), UNIQUE KEY uk_pid_sol200_response_no (pid, response_no), KEY idx_pid_sol200_response_type (pid, response_type) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='SOL200 响应配置表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_sol200_response_config ( id BIGINT NOT NULL IDENTITY(1,1), pid BIGINT NOT NULL, response_no INT NOT NULL, response_name VARCHAR(200) NOT NULL, response_type VARCHAR(32) NOT NULL, mode_number INT NULL, extra_json CLOB NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (id), CONSTRAINT uk_pid_sol200_response_name UNIQUE (pid, response_name), CONSTRAINT uk_pid_sol200_response_no UNIQUE (pid, response_no), );COMMENT='SOL200 响应配置表';
```

### SQL0097 db.py:497

- 类型：`CREATE`
- 特性：`auto_increment, engine_charset, inline_comment, json_type, tinyint_bool, key_index_ddl`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_parameter_definition ( id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID', pid BIGINT NOT NULL COMMENT '工程ID', parameter_name VARCHAR(200) NOT NULL COMMENT '参数名称', expression VARCHAR(500) NULL COMMENT '参数表达式', scalar_value DOUBLE NULL COMMENT '参数标量值', is_design_parameter TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否为设计参数', design_order INT NULL COMMENT '设计参数顺序', extra_json JSON NULL COMMENT '扩展信息', PRIMARY KEY (id), UNIQUE KEY uk_pid_parameter_definition (pid, parameter_name), KEY idx_pid_design_parameter (pid, is_design_parameter, design_order) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='输入文件解析参数定义表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_parameter_definition ( id BIGINT NOT NULL IDENTITY(1,1), pid BIGINT NOT NULL, parameter_name VARCHAR(200) NOT NULL, expression VARCHAR(500) NULL, scalar_value DOUBLE NULL, is_design_parameter SMALLINT NOT NULL DEFAULT 0, design_order INT NULL, extra_json CLOB NULL, PRIMARY KEY (id), CONSTRAINT uk_pid_parameter_definition UNIQUE (pid, parameter_name), );COMMENT='输入文件解析参数定义表';
```

### SQL0098 db.py:512

- 类型：`CREATE`
- 特性：`auto_increment, engine_charset, inline_comment, json_type, key_index_ddl`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_parameter_target ( id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID', pid BIGINT NOT NULL COMMENT '工程ID', parameter_name VARCHAR(200) NOT NULL COMMENT '参数名称', target_type VARCHAR(32) NOT NULL COMMENT '目标类型', set_name VARCHAR(200) NOT NULL COMMENT '集合名称', set_type VARCHAR(32) NOT NULL COMMENT '集合类型', set_scope VARCHAR(32) NOT NULL COMMENT '集合范围', instance_name VARCHAR(200) NULL COMMENT '实例名称', part_name VARCHAR(200) NULL COMMENT '零件名称', source_keyword VARCHAR(100) NOT NULL COMMENT '来源关键字', source_path VARCHAR(255) NOT NULL COMMENT '来源路径', component_name VARCHAR(100) NULL COMMENT '分量名称', extra_json JSON NULL COMMENT '扩展信息', PRIMARY KEY (id), KEY idx_pid_parameter_target (pid, parameter_name), KEY idx_pid_target_set (pid, set_name, set_type, set_scope) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='输入文件解析参数目标映射表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_parameter_target ( id BIGINT NOT NULL IDENTITY(1,1), pid BIGINT NOT NULL, parameter_name VARCHAR(200) NOT NULL, target_type VARCHAR(32) NOT NULL, set_name VARCHAR(200) NOT NULL, set_type VARCHAR(32) NOT NULL, set_scope VARCHAR(32) NOT NULL, instance_name VARCHAR(200) NULL, part_name VARCHAR(200) NULL, source_keyword VARCHAR(100) NOT NULL, source_path VARCHAR(255) NOT NULL, component_name VARCHAR(100) NULL, extra_json CLOB NULL, PRIMARY KEY (id), );COMMENT='输入文件解析参数目标映射表';
```

### SQL0099 db.py:532

- 类型：`CREATE`
- 特性：`auto_increment, engine_charset, inline_comment, json_type, key_index_ddl`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_static_sensitivity_response_catalog ( id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID', pid BIGINT NOT NULL COMMENT '工程ID', response_no INT NOT NULL COMMENT '响应序号', request_no INT NOT NULL COMMENT '请求序号', step_name VARCHAR(200) NULL COMMENT '分析步名称', frequency INT NOT NULL DEFAULT 1 COMMENT '响应频次', region_type VARCHAR(32) NOT NULL COMMENT '区域类型', set_name VARCHAR(200) NOT NULL COMMENT '集合名称', set_scope VARCHAR(32) NULL COMMENT '集合范围', instance_name VARCHAR(200) NULL COMMENT '实例名称', part_name VARCHAR(200) NULL COMMENT '零件名称', variables_json JSON NULL COMMENT '变量信息', extra_json JSON NULL COMMENT '扩展信息', PRIMARY KEY (id), UNIQUE KEY uk_pid_design_response (pid, response_no, request_no), KEY idx_pid_design_response_step (pid, step_name) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='输入文件解析设计响应目录表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_static_sensitivity_response_catalog ( id BIGINT NOT NULL IDENTITY(1,1), pid BIGINT NOT NULL, response_no INT NOT NULL, request_no INT NOT NULL, step_name VARCHAR(200) NULL, frequency INT NOT NULL DEFAULT 1, region_type VARCHAR(32) NOT NULL, set_name VARCHAR(200) NOT NULL, set_scope VARCHAR(32) NULL, instance_name VARCHAR(200) NULL, part_name VARCHAR(200) NULL, variables_json CLOB NULL, extra_json CLOB NULL, PRIMARY KEY (id), CONSTRAINT uk_pid_design_response UNIQUE (pid, response_no, request_no), );COMMENT='输入文件解析设计响应目录表';
```

### SQL0100 db.py:552

- 类型：`CREATE`
- 特性：`auto_increment, engine_charset, inline_comment, key_index_ddl`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_node_octree_cache ( id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键', pid BIGINT NOT NULL COMMENT '项目ID', source_file_path VARCHAR(500) NOT NULL COMMENT '源文件路径', cache_file_path VARCHAR(500) NOT NULL COMMENT '缓存文件路径', node_count INT NOT NULL DEFAULT 0 COMMENT '节点数量', instance_count INT NOT NULL DEFAULT 0 COMMENT '实例数量', bbox_min VARCHAR(255) NULL COMMENT '整体包围盒最小值', bbox_max VARCHAR(255) NULL COMMENT '整体包围盒最大值', updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间', PRIMARY KEY (id), UNIQUE KEY uk_pid_source_file (pid, source_file_path(255)) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='节点八叉树缓存元数据';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_node_octree_cache ( id BIGINT NOT NULL IDENTITY(1,1), pid BIGINT NOT NULL, source_file_path VARCHAR(500) NOT NULL, cache_file_path VARCHAR(500) NOT NULL, node_count INT NOT NULL DEFAULT 0, instance_count INT NOT NULL DEFAULT 0, bbox_min VARCHAR(255) NULL, bbox_max VARCHAR(255) NULL, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (id), CONSTRAINT uk_pid_source_file UNIQUE (pid, source_file_path(255)) );COMMENT='节点八叉树缓存元数据';
```

### SQL0101 db.py:567

- 类型：`CREATE`
- 特性：`auto_increment, engine_charset, inline_comment, json_type, key_index_ddl`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_node_match ( id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键', pid BIGINT NOT NULL COMMENT '项目ID', test_node_id VARCHAR(100) NOT NULL COMMENT '试验测点编号', instance_name VARCHAR(200) NULL COMMENT '实例名称', fem_node_label BIGINT NOT NULL COMMENT '有限元节点号', distance DOUBLE NOT NULL COMMENT '距离', x_offset DOUBLE NOT NULL COMMENT 'X偏移', y_offset DOUBLE NOT NULL COMMENT 'Y偏移', z_offset DOUBLE NOT NULL COMMENT 'Z偏移', transform_json JSON NULL COMMENT '匹配时使用的变换', created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间', PRIMARY KEY (id), UNIQUE KEY uk_pid_test_node (pid, test_node_id), KEY idx_pid_instance (pid, instance_name) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='试验测点到有限元节点匹配结果';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_node_match ( id BIGINT NOT NULL IDENTITY(1,1), pid BIGINT NOT NULL, test_node_id VARCHAR(100) NOT NULL, instance_name VARCHAR(200) NULL, fem_node_label BIGINT NOT NULL, distance DOUBLE NOT NULL, x_offset DOUBLE NOT NULL, y_offset DOUBLE NOT NULL, z_offset DOUBLE NOT NULL, transform_json CLOB NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (id), CONSTRAINT uk_pid_test_node UNIQUE (pid, test_node_id), );COMMENT='试验测点到有限元节点匹配结果';
```

### SQL0102 db.py:585

- 类型：`CREATE`
- 特性：`auto_increment, engine_charset, inline_comment, json_type, key_index_ddl`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_dof_match ( id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID', pid BIGINT NOT NULL COMMENT '工程ID', test_node_id VARCHAR(100) NOT NULL COMMENT '试验测点编号', test_dof VARCHAR(32) NOT NULL COMMENT '试验自由度', instance_name VARCHAR(200) NULL COMMENT '实例名称', part_name VARCHAR(200) NULL COMMENT '零件名称', fem_node_label BIGINT NOT NULL COMMENT '有限元节点号', fem_dof VARCHAR(32) NOT NULL COMMENT '有限元自由度', direction_x DOUBLE NOT NULL COMMENT '方向X分量', direction_y DOUBLE NOT NULL COMMENT '方向Y分量', direction_z DOUBLE NOT NULL COMMENT '方向Z分量', match_score DOUBLE NOT NULL DEFAULT 0 COMMENT '匹配得分', transform_json JSON NULL COMMENT '变换信息', created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间', PRIMARY KEY (id), UNIQUE KEY uk_pid_test_dof (pid, test_node_id, test_dof), KEY idx_pid_fem_node (pid, instance_name, fem_node_label), KEY idx_pid_fem_dof (pid, fem_dof) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='有限元自由度匹配结果表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_dof_match ( id BIGINT NOT NULL IDENTITY(1,1), pid BIGINT NOT NULL, test_node_id VARCHAR(100) NOT NULL, test_dof VARCHAR(32) NOT NULL, instance_name VARCHAR(200) NULL, part_name VARCHAR(200) NULL, fem_node_label BIGINT NOT NULL, fem_dof VARCHAR(32) NOT NULL, direction_x DOUBLE NOT NULL, direction_y DOUBLE NOT NULL, direction_z DOUBLE NOT NULL, match_score DOUBLE NOT NULL DEFAULT 0, transform_json CLOB NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (id), CONSTRAINT uk_pid_test_dof UNIQUE (pid, test_node_id, test_dof), );COMMENT='有限元自由度匹配结果表';
```

### SQL0103 db.py:607

- 类型：`CREATE`
- 特性：`auto_increment, engine_charset, inline_comment, json_type, key_index_ddl`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_dynamic_response_catalog ( id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID', pid BIGINT NOT NULL COMMENT '工程ID', response_code VARCHAR(200) NOT NULL COMMENT '响应编码', response_name VARCHAR(255) NOT NULL COMMENT '响应名称', response_type VARCHAR(64) NOT NULL COMMENT '响应类型', entity_type VARCHAR(64) NOT NULL COMMENT '实体类型', test_mode_no INT NULL COMMENT '试验振型号', test_node_id VARCHAR(100) NULL COMMENT '试验测点编号', instance_name VARCHAR(200) NULL COMMENT '实例名称', part_name VARCHAR(200) NULL COMMENT '零件名称', fem_node_label BIGINT NULL COMMENT '有限元节点号', component VARCHAR(32) NULL COMMENT '响应分量', unit VARCHAR(50) NULL COMMENT '单位', scatter FLOAT NOT NULL DEFAULT 0.05 COMMENT '响应离散度', seq_no INT NULL COMMENT '显示顺序', source_table VARCHAR(100) NULL COMMENT '来源数据表', extra_json JSON NULL COMMENT '扩展信息', created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间', PRIMARY KEY (id), UNIQUE KEY uk_pid_response_code (pid, response_code), KEY idx_pid_response_type (pid, response_type), KEY idx_pid_seq (pid, seq_no) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='有限元响应目录表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_dynamic_response_catalog ( id BIGINT NOT NULL IDENTITY(1,1), pid BIGINT NOT NULL, response_code VARCHAR(200) NOT NULL, response_name VARCHAR(255) NOT NULL, response_type VARCHAR(64) NOT NULL, entity_type VARCHAR(64) NOT NULL, test_mode_no INT NULL, test_node_id VARCHAR(100) NULL, instance_name VARCHAR(200) NULL, part_name VARCHAR(200) NULL, fem_node_label BIGINT NULL, component VARCHAR(32) NULL, unit VARCHAR(50) NULL, scatter FLOAT NOT NULL DEFAULT 0.05, seq_no INT NULL, source_table VARCHAR(100) NULL, extra_json CLOB NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (id), CONSTRAINT uk_pid_response_code UNIQUE (pid, response_code), );COMMENT='有限元响应目录表';
```

### SQL0104 db.py:633

- 类型：`CREATE`
- 特性：`auto_increment, engine_charset, inline_comment, json_type, key_index_ddl`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_modal_result ( id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID', pid BIGINT NOT NULL COMMENT '工程ID', mode_no INT NOT NULL COMMENT '有限元振型号', frequency DOUBLE NULL COMMENT '频率值', instance_name VARCHAR(200) NULL COMMENT '实例名称', part_name VARCHAR(200) NULL COMMENT '零件名称', fem_node_label BIGINT NOT NULL COMMENT '有限元节点号', u1 DOUBLE NULL COMMENT 'X向位移U1', u2 DOUBLE NULL COMMENT 'Y向位移U2', u3 DOUBLE NULL COMMENT 'Z向位移U3', extra_json JSON NULL COMMENT '扩展信息', created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间', PRIMARY KEY (id), UNIQUE KEY uk_pid_mode_node (pid, mode_no, instance_name, fem_node_label), KEY idx_pid_mode (pid, mode_no) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='有限元模态结果表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_modal_result ( id BIGINT NOT NULL IDENTITY(1,1), pid BIGINT NOT NULL, mode_no INT NOT NULL, frequency DOUBLE NULL, instance_name VARCHAR(200) NULL, part_name VARCHAR(200) NULL, fem_node_label BIGINT NOT NULL, u1 DOUBLE NULL, u2 DOUBLE NULL, u3 DOUBLE NULL, extra_json CLOB NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (id), CONSTRAINT uk_pid_mode_node UNIQUE (pid, mode_no, instance_name, fem_node_label), );COMMENT='有限元模态结果表';
```

### SQL0105 db.py:652

- 类型：`CREATE`
- 特性：`auto_increment, engine_charset, inline_comment, json_type, key_index_ddl`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_static_result ( id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID', pid BIGINT NOT NULL COMMENT '工程ID', load_case_no INT NOT NULL DEFAULT 1 COMMENT '载荷工况号', instance_name VARCHAR(200) NULL COMMENT '实例名称', part_name VARCHAR(200) NULL COMMENT '零件名称', fem_node_label BIGINT NOT NULL COMMENT '有限元节点号', u1 DOUBLE NULL COMMENT 'X向位移U1', u2 DOUBLE NULL COMMENT 'Y向位移U2', u3 DOUBLE NULL COMMENT 'Z向位移U3', ur1 DOUBLE NULL COMMENT 'X向转角UR1', ur2 DOUBLE NULL COMMENT 'Y向转角UR2', ur3 DOUBLE NULL COMMENT 'Z向转角UR3', extra_json JSON NULL COMMENT '扩展信息', created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间', PRIMARY KEY (id), UNIQUE KEY uk_pid_static_case_node (pid, load_case_no, instance_name, fem_node_label), KEY idx_pid_static_case (pid, load_case_no), KEY idx_pid_static_node (pid, fem_node_label) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='有限元静力结果表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_static_result ( id BIGINT NOT NULL IDENTITY(1,1), pid BIGINT NOT NULL, load_case_no INT NOT NULL DEFAULT 1, instance_name VARCHAR(200) NULL, part_name VARCHAR(200) NULL, fem_node_label BIGINT NOT NULL, u1 DOUBLE NULL, u2 DOUBLE NULL, u3 DOUBLE NULL, ur1 DOUBLE NULL, ur2 DOUBLE NULL, ur3 DOUBLE NULL, extra_json CLOB NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (id), CONSTRAINT uk_pid_static_case_node UNIQUE (pid, load_case_no, instance_name, fem_node_label), );COMMENT='有限元静力结果表';
```

### SQL0106 db.py:674

- 类型：`CREATE`
- 特性：`auto_increment, engine_charset, inline_comment, json_type, key_index_ddl`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_modal_correlation ( id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID', pid BIGINT NOT NULL COMMENT '工程ID', test_mode_no INT NOT NULL COMMENT '试验振型号', fem_mode_no INT NOT NULL COMMENT '有限元振型号', dof_pair_count INT NOT NULL DEFAULT 0 COMMENT '自由度配对数量', dac DOUBLE NOT NULL COMMENT 'DAC百分比', dsf DOUBLE NOT NULL COMMENT 'DSF值', mac DOUBLE NOT NULL COMMENT 'MAC值', freq_test DOUBLE NULL COMMENT '试验频率', freq_fem DOUBLE NULL COMMENT '有限元频率', freq_error_ratio DOUBLE NULL COMMENT '频率误差比', flip BOOLEAN NOT NULL DEFAULT FALSE COMMENT '是否需要相位翻转', extra_json JSON NULL COMMENT '扩展信息', created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间', PRIMARY KEY (id), UNIQUE KEY uk_pid_mode_pair (pid, test_mode_no, fem_mode_no), KEY idx_pid_test_mode (pid, test_mode_no), KEY idx_pid_fem_mode (pid, fem_mode_no) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='有限元模态相关性表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_modal_correlation ( id BIGINT NOT NULL IDENTITY(1,1), pid BIGINT NOT NULL, test_mode_no INT NOT NULL, fem_mode_no INT NOT NULL, dof_pair_count INT NOT NULL DEFAULT 0, dac DOUBLE NOT NULL, dsf DOUBLE NOT NULL, mac DOUBLE NOT NULL, freq_test DOUBLE NULL, freq_fem DOUBLE NULL, freq_error_ratio DOUBLE NULL, flip BOOLEAN NOT NULL DEFAULT FALSE, extra_json CLOB NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (id), CONSTRAINT uk_pid_mode_pair UNIQUE (pid, test_mode_no, fem_mode_no), );COMMENT='有限元模态相关性表';
```

### SQL0107 db.py:696

- 类型：`CREATE`
- 特性：`auto_increment, engine_charset, inline_comment`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_response_overview ( id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键', pid BIGINT NOT NULL COMMENT '工程ID', response_type BIGINT NOT NULL COMMENT '响应类型', scatter FLOAT NOT NULL COMMENT '离散度', value FLOAT NOT NULL COMMENT '当前值', PRIMARY KEY (id) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='响应总览表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_response_overview ( id BIGINT NOT NULL IDENTITY(1,1), pid BIGINT NOT NULL, response_type BIGINT NOT NULL, scatter FLOAT NOT NULL, value FLOAT NOT NULL, PRIMARY KEY (id) );COMMENT='响应总览表';
```

### SQL0108 db.py:706

- 类型：`CREATE`
- 特性：`auto_increment, engine_charset, inline_comment`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_displacement_responses ( id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键', pid BIGINT NOT NULL COMMENT '工程ID', data_source VARCHAR(32) NOT NULL COMMENT '数据来源', load_case_no VARCHAR(32) NOT NULL COMMENT '载荷工况编号', node_label BIGINT NOT NULL COMMENT '节点编号', dof VARCHAR(32) NOT NULL COMMENT '自由度', scatter FLOAT NOT NULL COMMENT '离散度', value FLOAT NOT NULL COMMENT '当前值', sub_response_type VARCHAR(32) NOT NULL COMMENT '子响应类型', PRIMARY KEY (id) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='位移响应表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_displacement_responses ( id BIGINT NOT NULL IDENTITY(1,1), pid BIGINT NOT NULL, data_source VARCHAR(32) NOT NULL, load_case_no VARCHAR(32) NOT NULL, node_label BIGINT NOT NULL, dof VARCHAR(32) NOT NULL, scatter FLOAT NOT NULL, value FLOAT NOT NULL, sub_response_type VARCHAR(32) NOT NULL, PRIMARY KEY (id) );COMMENT='位移响应表';
```

### SQL0109 db.py:720

- 类型：`CREATE`
- 特性：`auto_increment, engine_charset, inline_comment`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_strain_responses ( id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键', pid BIGINT NOT NULL COMMENT '工程ID', data_source VARCHAR(32) NOT NULL COMMENT '数据来源', load_case_no VARCHAR(32) NOT NULL COMMENT '载荷工况编号', node_label BIGINT NOT NULL COMMENT '节点编号', ele_nodes VARCHAR(100) NOT NULL COMMENT '单元节点列表', group_type VARCHAR(32) NOT NULL COMMENT '分组', direction VARCHAR(32) NOT NULL COMMENT '方向', coord VARCHAR(32) NOT NULL COMMENT '坐标系', scatter FLOAT NOT NULL COMMENT '离散度', value FLOAT NOT NULL COMMENT '当前值', sub_response_type VARCHAR(32) NOT NULL COMMENT '子响应类型', PRIMARY KEY (id) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='应变响应表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_strain_responses ( id BIGINT NOT NULL IDENTITY(1,1), pid BIGINT NOT NULL, data_source VARCHAR(32) NOT NULL, load_case_no VARCHAR(32) NOT NULL, node_label BIGINT NOT NULL, ele_nodes VARCHAR(100) NOT NULL, group_type VARCHAR(32) NOT NULL, direction VARCHAR(32) NOT NULL, coord VARCHAR(32) NOT NULL, scatter FLOAT NOT NULL, value FLOAT NOT NULL, sub_response_type VARCHAR(32) NOT NULL, PRIMARY KEY (id) );COMMENT='应变响应表';
```

### SQL0110 db.py:737

- 类型：`CREATE`
- 特性：`auto_increment, engine_charset, inline_comment`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_stress_responses ( id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键', pid BIGINT NOT NULL COMMENT '工程ID', data_source VARCHAR(32) NOT NULL COMMENT '数据来源', load_case_no VARCHAR(32) NOT NULL COMMENT '载荷工况编号', node_label BIGINT NOT NULL COMMENT '节点编号', ele_nodes VARCHAR(100) NOT NULL COMMENT '单元节点列表', group_type VARCHAR(32) NOT NULL COMMENT '分组', direction VARCHAR(32) NOT NULL COMMENT '方向', coord VARCHAR(32) NOT NULL COMMENT '坐标系', scatter FLOAT NOT NULL COMMENT '离散度', value FLOAT NOT NULL COMMENT '当前值', sub_response_type VARCHAR(32) NOT NULL COMMENT '子响应类型', PRIMARY KEY (id) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='应力响应表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_stress_responses ( id BIGINT NOT NULL IDENTITY(1,1), pid BIGINT NOT NULL, data_source VARCHAR(32) NOT NULL, load_case_no VARCHAR(32) NOT NULL, node_label BIGINT NOT NULL, ele_nodes VARCHAR(100) NOT NULL, group_type VARCHAR(32) NOT NULL, direction VARCHAR(32) NOT NULL, coord VARCHAR(32) NOT NULL, scatter FLOAT NOT NULL, value FLOAT NOT NULL, sub_response_type VARCHAR(32) NOT NULL, PRIMARY KEY (id) );COMMENT='应力响应表';
```

### SQL0111 db.py:754

- 类型：`CREATE`
- 特性：`auto_increment, engine_charset, inline_comment, key_index_ddl`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_analysis_run ( id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键', project_id BIGINT NULL COMMENT '项目ID', case_name VARCHAR(200) NULL COMMENT '工况名称', run_no VARCHAR(100) NULL COMMENT '分析批次号', source_kind VARCHAR(32) NULL COMMENT '灵敏度结果来源类型', op2_path VARCHAR(1024) NULL COMMENT 'OP2结果文件路径', matrix_path VARCHAR(1024) NULL COMMENT '矩阵结果文件路径', bdf_path VARCHAR(1024) NULL COMMENT 'BDF文件路径', metadata_path VARCHAR(1024) NULL COMMENT '元数据文件路径', created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间', PRIMARY KEY (id), KEY idx_created_at (created_at) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='有限元分析任务表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_analysis_run ( id BIGINT NOT NULL IDENTITY(1,1), project_id BIGINT NULL, case_name VARCHAR(200) NULL, run_no VARCHAR(100) NULL, source_kind VARCHAR(32) NULL, op2_path VARCHAR(1024) NULL, matrix_path VARCHAR(1024) NULL, bdf_path VARCHAR(1024) NULL, metadata_path VARCHAR(1024) NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (id), );COMMENT='有限元分析任务表';
```

### SQL0112 db.py:770

- 类型：`CREATE`
- 特性：`auto_increment, engine_charset, inline_comment, key_index_ddl`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_sensitivity_matrix_response ( id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键', project_id BIGINT NULL COMMENT '项目ID', analysis_run_id BIGINT NOT NULL COMMENT '分析任务ID', response_code VARCHAR(100) NOT NULL COMMENT '响应编码', response_name VARCHAR(200) NOT NULL COMMENT '响应名称', response_type VARCHAR(32) NULL COMMENT '响应类型', mode_number INT NULL COMMENT '模态阶次', unit VARCHAR(50) NULL COMMENT '单位', seq_no INT NULL COMMENT '显示顺序', PRIMARY KEY (id), UNIQUE KEY uk_run_response_code (analysis_run_id, response_code), KEY idx_run_seq (analysis_run_id, seq_no) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='有限元响应定义表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_sensitivity_matrix_response ( id BIGINT NOT NULL IDENTITY(1,1), project_id BIGINT NULL, analysis_run_id BIGINT NOT NULL, response_code VARCHAR(100) NOT NULL, response_name VARCHAR(200) NOT NULL, response_type VARCHAR(32) NULL, mode_number INT NULL, unit VARCHAR(50) NULL, seq_no INT NULL, PRIMARY KEY (id), CONSTRAINT uk_run_response_code UNIQUE (analysis_run_id, response_code), );COMMENT='有限元响应定义表';
```

### SQL0113 db.py:786

- 类型：`CREATE`
- 特性：`auto_increment, engine_charset, inline_comment, key_index_ddl`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_sensitivity_matrix_parameter ( id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键', project_id BIGINT NULL COMMENT '项目ID', analysis_run_id BIGINT NOT NULL COMMENT '分析任务ID', param_code VARCHAR(100) NOT NULL COMMENT '参数编码', param_name VARCHAR(200) NOT NULL COMMENT '参数名称', param_type VARCHAR(32) NULL COMMENT '参数类型', material_id BIGINT NULL COMMENT '材料ID', property_id BIGINT NULL COMMENT '属性ID', element_id BIGINT NULL COMMENT '单元ID', source_material_id BIGINT NULL COMMENT '源材料ID', source_property_id BIGINT NULL COMMENT '源属性ID', initial_value DOUBLE NULL COMMENT '初始值', lower_bound DOUBLE NULL COMMENT '下界', upper_bound DOUBLE NULL COMMENT '上界', unit VARCHAR(50) NULL COMMENT '单位', seq_no INT NULL COMMENT '显示顺序', PRIMARY KEY (id), UNIQUE KEY uk_run_param_code (analysis_run_id, param_code), KEY idx_run_seq (analysis_run_id, seq_no) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='有限元参数定义表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_sensitivity_matrix_parameter ( id BIGINT NOT NULL IDENTITY(1,1), project_id BIGINT NULL, analysis_run_id BIGINT NOT NULL, param_code VARCHAR(100) NOT NULL, param_name VARCHAR(200) NOT NULL, param_type VARCHAR(32) NULL, material_id BIGINT NULL, property_id BIGINT NULL, element_id BIGINT NULL, source_material_id BIGINT NULL, source_property_id BIGINT NULL, initial_value DOUBLE NULL, lower_bound DOUBLE NULL, upper_bound DOUBLE NULL, unit VARCHAR(50) NULL, seq_no INT NULL, PRIMARY KEY (id), CONSTRAINT uk_run_param_code UNIQUE (analysis_run_id, param_code), );COMMENT='有限元参数定义表';
```

### SQL0114 db.py:809

- 类型：`CREATE`
- 特性：`auto_increment, engine_charset, inline_comment, key_index_ddl`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_sensitivity_matrix_result ( id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键', project_id BIGINT NULL COMMENT '项目ID', analysis_run_id BIGINT NOT NULL COMMENT '分析任务ID', parameter_id BIGINT NOT NULL COMMENT '参数ID', response_id BIGINT NOT NULL COMMENT '响应ID', sensitivity_value DECIMAL(24,12) NOT NULL COMMENT '灵敏度值', PRIMARY KEY (id), UNIQUE KEY uk_run_param_resp (analysis_run_id, parameter_id, response_id), KEY idx_run_param (analysis_run_id, parameter_id), KEY idx_run_resp (analysis_run_id, response_id), KEY idx_run_resp_value (analysis_run_id, response_id, sensitivity_value) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='有限元灵敏度结果表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_sensitivity_matrix_result ( id BIGINT NOT NULL IDENTITY(1,1), project_id BIGINT NULL, analysis_run_id BIGINT NOT NULL, parameter_id BIGINT NOT NULL, response_id BIGINT NOT NULL, sensitivity_value DECIMAL(24,12) NOT NULL, PRIMARY KEY (id), CONSTRAINT uk_run_param_resp UNIQUE (analysis_run_id, parameter_id, response_id), );COMMENT='有限元灵敏度结果表';
```

### SQL0115 db.py:824

- 类型：`CREATE`
- 特性：`auto_increment, engine_charset, inline_comment, key_index_ddl`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_analysis_error ( id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID', pid BIGINT NOT NULL COMMENT '工程ID', load_case_no INT NOT NULL DEFAULT 1 COMMENT '载荷工况号', result_no INT NOT NULL DEFAULT 1 COMMENT '结果序号', point_no VARCHAR(64) NOT NULL COMMENT '测点号', node_no VARCHAR(128) NULL COMMENT '节点号', component_name VARCHAR(32) NOT NULL COMMENT '分量名称', point_value FLOAT COMMENT '测点值', initial_node_value FLOAT COMMENT '初始节点值', initial_relative_error FLOAT COMMENT '初始相对误差', initial_abs_error REAL COMMENT '初始绝对误差', updated_node_value FLOAT COMMENT '修正后节点值', updated_relative_error FLOAT COMMENT '修正后相对误差', updated_abs_error REAL COMMENT '修正后绝对误差', sensor_type_id BIGINT COMMENT '传感器类型ID', PRIMARY KEY (id), UNIQUE KEY uk_pid_case_result_point_component (pid, load_case_no, result_no, point_no, component_name), KEY idx_pid (pid), KEY idx_pid_point (pid, point_no) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='误差分析表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_analysis_error ( id BIGINT NOT NULL IDENTITY(1,1), pid BIGINT NOT NULL, load_case_no INT NOT NULL DEFAULT 1, result_no INT NOT NULL DEFAULT 1, point_no VARCHAR(64) NOT NULL, node_no VARCHAR(128) NULL, component_name VARCHAR(32) NOT NULL, point_value FLOAT, initial_node_value FLOAT, initial_relative_error FLOAT, initial_abs_error REAL, updated_node_value FLOAT, updated_relative_error FLOAT, updated_abs_error REAL, sensor_type_id BIGINT, PRIMARY KEY (id), CONSTRAINT uk_pid_case_result_point_component UNIQUE (pid, load_case_no, result_no, point_no, component_name), );COMMENT='误差分析表';
```

### SQL0116 db.py:847

- 类型：`CREATE`
- 特性：`auto_increment, engine_charset, inline_comment, json_type, key_index_ddl`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_model_update_static_result ( id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID', pid BIGINT NOT NULL COMMENT '工程ID', batch_no VARCHAR(32) NOT NULL COMMENT '批次号', step_name VARCHAR(200) NULL COMMENT '分析步名称', frame_idx INT NOT NULL DEFAULT 0 COMMENT '帧序号', instance_name VARCHAR(200) NULL COMMENT '实例名称', part_name VARCHAR(200) NULL COMMENT '零件名称', fem_node_label BIGINT NOT NULL COMMENT '有限元节点号', u1 DOUBLE NULL COMMENT 'X向位移', u2 DOUBLE NULL COMMENT 'Y向位移', u3 DOUBLE NULL COMMENT 'Z向位移', extra_json JSON NULL COMMENT '扩展信息', created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间', PRIMARY KEY (id), UNIQUE KEY uk_pid_batch_frame_node (pid, batch_no, frame_idx, instance_name, fem_node_label), KEY idx_pid_batch (pid, batch_no) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='模型修正最终位移结果表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_model_update_static_result ( id BIGINT NOT NULL IDENTITY(1,1), pid BIGINT NOT NULL, batch_no VARCHAR(32) NOT NULL, step_name VARCHAR(200) NULL, frame_idx INT NOT NULL DEFAULT 0, instance_name VARCHAR(200) NULL, part_name VARCHAR(200) NULL, fem_node_label BIGINT NOT NULL, u1 DOUBLE NULL, u2 DOUBLE NULL, u3 DOUBLE NULL, extra_json CLOB NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (id), CONSTRAINT uk_pid_batch_frame_node UNIQUE (pid, batch_no, frame_idx, instance_name, fem_node_label), );COMMENT='模型修正最终位移结果表';
```

### SQL0117 db.py:867

- 类型：`CREATE`
- 特性：`auto_increment, engine_charset, inline_comment, json_type, key_index_ddl`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_model_update_modal_result ( id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID', pid BIGINT NOT NULL COMMENT '工程ID', batch_no VARCHAR(32) NOT NULL COMMENT '批次号', response_name VARCHAR(200) NOT NULL COMMENT '响应名称', response_type VARCHAR(32) NOT NULL DEFAULT 'FREQ' COMMENT '响应类型', fem_mode_no INT NOT NULL COMMENT 'FEM 模态阶次', test_mode_no INT NOT NULL COMMENT '试验模态阶次', freq_fem_initial DOUBLE NULL COMMENT '修正前 FEM 频率', freq_fem_updated DOUBLE NULL COMMENT '修正后 FEM 频率', freq_test DOUBLE NULL COMMENT '目标试验频率', initial_relative_error DOUBLE NULL COMMENT '修正前相对误差(%)', updated_relative_error DOUBLE NULL COMMENT '修正后相对误差(%)', mac DOUBLE NULL COMMENT '匹配使用的 MAC', extra_json JSON NULL COMMENT '扩展信息', created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间', PRIMARY KEY (id), UNIQUE KEY uk_pid_batch_modal_pair (pid, batch_no, fem_mode_no, test_mode_no), KEY idx_pid_batch (pid, batch_no) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='模型修正最终模态频率结果表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_model_update_modal_result ( id BIGINT NOT NULL IDENTITY(1,1), pid BIGINT NOT NULL, batch_no VARCHAR(32) NOT NULL, response_name VARCHAR(200) NOT NULL, response_type VARCHAR(32) NOT NULL DEFAULT 'FREQ', fem_mode_no INT NOT NULL, test_mode_no INT NOT NULL, freq_fem_initial DOUBLE NULL, freq_fem_updated DOUBLE NULL, freq_test DOUBLE NULL, initial_relative_error DOUBLE NULL, updated_relative_error DOUBLE NULL, mac DOUBLE NULL, extra_json CLOB NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (id), CONSTRAINT uk_pid_batch_modal_pair UNIQUE (pid, batch_no, fem_mode_no, test_mode_no), );COMMENT='模型修正最终模态频率结果表';
```

### SQL0118 db.py:889

- 类型：`CREATE`
- 特性：`engine_charset, inline_comment`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_dac_dsf ( pid BIGINT NOT NULL COMMENT '工程ID', dac FLOAT COMMENT 'DAC指标值', dsf FLOAT COMMENT 'DSF指标值', PRIMARY KEY (pid) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='模态相关性指标表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_dac_dsf ( pid BIGINT NOT NULL, dac FLOAT, dsf FLOAT, PRIMARY KEY (pid) );COMMENT='模态相关性指标表';
```

### SQL0119 db.py:897

- 类型：`CREATE`
- 特性：`inline_comment`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_tracking_value ( pid BIGINT NOT NULL COMMENT '工程ID', batch_no INT NOT NULL DEFAULT 1 COMMENT '批次号', tracking_type VARCHAR(10) NOT NULL COMMENT '跟踪类型（参数跟踪/响应跟踪）', tracking_name VARCHAR(100) NOT NULL COMMENT '跟踪名称', iteration INT NOT NULL COMMENT '迭代步', track_value FLOAT COMMENT '跟踪值', PRIMARY KEY (pid, batch_no, tracking_type, tracking_name, iteration) ) COMMENT='跟踪值表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_tracking_value ( pid BIGINT NOT NULL, batch_no INT NOT NULL DEFAULT 1, tracking_type VARCHAR(10) NOT NULL, tracking_name VARCHAR(100) NOT NULL, iteration INT NOT NULL, track_value FLOAT, PRIMARY KEY (pid, batch_no, tracking_type, tracking_name, iteration) );
```

### SQL0120 db.py:908

- 类型：`CREATE`
- 特性：`inline_comment`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_tracking_iteration ( pid BIGINT NOT NULL COMMENT '工程ID', batch_no INT NOT NULL DEFAULT 1 COMMENT '批次号', iterations INT COMMENT '迭代次数', PRIMARY KEY (pid, batch_no) ) COMMENT='跟踪迭代表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_tracking_iteration ( pid BIGINT NOT NULL, batch_no INT NOT NULL DEFAULT 1, iterations INT, PRIMARY KEY (pid, batch_no) );
```

### SQL0121 db.py:916

- 类型：`CREATE`
- 特性：`inline_comment`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_bayesian_iteration_metric ( project_id BIGINT NOT NULL COMMENT 'project id', batch_no INT NOT NULL DEFAULT 1 COMMENT 'batch number', iteration INT NOT NULL COMMENT 'iteration number', ccabs DOUBLE COMMENT 'CCABS convergence metric', rel_res DOUBLE COMMENT 'relative residual norm', ra_norm DOUBLE COMMENT 'model response norm', re_norm DOUBLE COMMENT 'target response norm', dr_norm DOUBLE COMMENT 'response residual norm', dx_norm DOUBLE COMMENT 'parameter update norm', max_abs_dparam DOUBLE COMMENT 'maximum absolute parameter update', mean_abs_response_diff DOUBLE COMMENT 'mean absolute response difference percent', max_abs_response_diff DOUBLE COMMENT 'maximum absolute response difference percent', PRIMARY KEY (project_id, batch_no, iteration) ) COMMENT='bayesian iteration metrics';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_bayesian_iteration_metric ( project_id BIGINT NOT NULL, batch_no INT NOT NULL DEFAULT 1, iteration INT NOT NULL, ccabs DOUBLE, rel_res DOUBLE, ra_norm DOUBLE, re_norm DOUBLE, dr_norm DOUBLE, dx_norm DOUBLE, max_abs_dparam DOUBLE, mean_abs_response_diff DOUBLE, max_abs_response_diff DOUBLE, PRIMARY KEY (project_id, batch_no, iteration) );
```

### SQL0122 db.py:933

- 类型：`CREATE`
- 特性：`inline_comment`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_relevance_tracking ( pid BIGINT NOT NULL COMMENT '工程ID', iteration INT NOT NULL COMMENT '迭代步', type VARCHAR(32) NOT NULL COMMENT '指标类型', value FLOAT COMMENT '指标值', PRIMARY KEY (pid, iteration, type) ) COMMENT='模型修正相关性指标跟踪表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_relevance_tracking ( pid BIGINT NOT NULL, iteration INT NOT NULL, type VARCHAR(32) NOT NULL, value FLOAT, PRIMARY KEY (pid, iteration, type) );
```

### SQL0123 db.py:942

- 类型：`CREATE`
- 特性：`inline_comment`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_parameter_variation ( pid BIGINT NOT NULL COMMENT '工程ID', batch_no INT NOT NULL DEFAULT 1 COMMENT '批次号', parameter_name VARCHAR(100) NOT NULL COMMENT '参数名称', parameter_hierarchy VARCHAR(10) COMMENT '参数层级', parameter_type VARCHAR(10) COMMENT '参数类型', parameter_scope VARCHAR(32) COMMENT '参数范围', ori_value FLOAT COMMENT '原始值', result_value FLOAT COMMENT '结果值', parameter_variation FLOAT COMMENT '参数变化', PRIMARY KEY (pid, batch_no, parameter_name) ) COMMENT='参数变化表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_parameter_variation ( pid BIGINT NOT NULL, batch_no INT NOT NULL DEFAULT 1, parameter_name VARCHAR(100) NOT NULL, parameter_hierarchy VARCHAR(10), parameter_type VARCHAR(10), parameter_scope VARCHAR(32), ori_value FLOAT, result_value FLOAT, parameter_variation FLOAT, PRIMARY KEY (pid, batch_no, parameter_name) );
```

### SQL0124 db.py:956

- 类型：`CREATE`
- 特性：`inline_comment`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_response_difference ( pid INTEGER NOT NULL COMMENT '工程ID', batch_no INT NOT NULL DEFAULT 1 COMMENT '批次号', response_name VARCHAR(100) NOT NULL COMMENT '响应名称', iteration INT NOT NULL COMMENT '迭代次数', cal_result_value FLOAT COMMENT '计算结果值', test_result_value FLOAT COMMENT '测试结果值', response_diff FLOAT COMMENT '响应差异(%)', PRIMARY KEY (pid, batch_no, response_name, iteration) ) COMMENT='响应差异表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_fem_response_difference ( pid INTEGER NOT NULL, batch_no INT NOT NULL DEFAULT 1, response_name VARCHAR(100) NOT NULL, iteration INT NOT NULL, cal_result_value FLOAT, test_result_value FLOAT, response_diff FLOAT, PRIMARY KEY (pid, batch_no, response_name, iteration) );
```

### SQL0125 db.py:968

- 类型：`CREATE`
- 特性：`auto_increment, engine_charset, inline_comment`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_measuring_point_info( id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID', measuring_point_name VARCHAR(64) NOT NULL COMMENT '测点名称', project_id BIGINT NOT NULL COMMENT '项目ID', sensor_type_id BIGINT NOT NULL COMMENT '传感器类型ID', x_position DOUBLE NOT NULL COMMENT 'X坐标', y_position DOUBLE NOT NULL COMMENT 'Y坐标', z_position DOUBLE NOT NULL COMMENT 'Z坐标', x_position_ori DOUBLE NULL COMMENT '原始X坐标', y_position_ori DOUBLE NULL COMMENT '原始Y坐标', z_position_ori DOUBLE NULL COMMENT '原始Z坐标', x_angle DOUBLE NOT NULL COMMENT '角度x', y_angle DOUBLE NOT NULL COMMENT '角度y', z_angle DOUBLE NOT NULL COMMENT '角度z', data_source VARCHAR(32) NOT NULL COMMENT '数据来源', PRIMARY KEY (id, measuring_point_name) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='测点信息表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_measuring_point_info( id BIGINT NOT NULL IDENTITY(1,1), measuring_point_name VARCHAR(64) NOT NULL, project_id BIGINT NOT NULL, sensor_type_id BIGINT NOT NULL, x_position DOUBLE NOT NULL, y_position DOUBLE NOT NULL, z_position DOUBLE NOT NULL, x_position_ori DOUBLE NULL, y_position_ori DOUBLE NULL, z_position_ori DOUBLE NULL, x_angle DOUBLE NOT NULL, y_angle DOUBLE NOT NULL, z_angle DOUBLE NOT NULL, data_source VARCHAR(32) NOT NULL, PRIMARY KEY (id, measuring_point_name) );COMMENT='测点信息表';
```

### SQL0126 db.py:987

- 类型：`CREATE`
- 特性：`auto_increment, engine_charset, inline_comment`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_channel_info ( id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID', channel_name VARCHAR(64) NOT NULL COMMENT '通道名称', measure_point_id BIGINT NOT NULL COMMENT '测点ID', project_id BIGINT NOT NULL COMMENT '项目ID', direction INT NOT NULL COMMENT '方向:1-x, 2-y, 3-z', data_operate CHAR(1) NOT NULL COMMENT '+ - 操作类型', PRIMARY KEY (id) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='通道信息表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_channel_info ( id BIGINT NOT NULL IDENTITY(1,1), channel_name VARCHAR(64) NOT NULL, measure_point_id BIGINT NOT NULL, project_id BIGINT NOT NULL, direction INT NOT NULL, data_operate CHAR(1) NOT NULL, PRIMARY KEY (id) );COMMENT='通道信息表';
```

### SQL0127 db.py:998

- 类型：`CREATE`
- 特性：`engine_charset, inline_comment, tinyint_bool, key_index_ddl`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_background_task ( task_id VARCHAR(64) NOT NULL COMMENT '任务ID', task_type VARCHAR(128) NOT NULL COMMENT '任务类型', interface_code VARCHAR(128) NULL COMMENT '接口编码', task_kind VARCHAR(32) NOT NULL DEFAULT 'internal' COMMENT '任务分类', project_id BIGINT NULL COMMENT '项目ID', status VARCHAR(32) NOT NULL COMMENT '任务状态', execute_count INT NOT NULL DEFAULT 0 COMMENT '已执行次数', max_execute_count INT NOT NULL DEFAULT 3 COMMENT '最大执行次数', handler_module VARCHAR(255) NULL COMMENT '处理函数模块', handler_name VARCHAR(128) NULL COMMENT '处理函数名称', pass_task_id TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否传递task_id', submitted_at VARCHAR(64) NULL COMMENT '提交时间', started_at VARCHAR(64) NULL COMMENT '开始时间', finished_at VARCHAR(64) NULL COMMENT '结束时间', request_json LONGTEXT NULL COMMENT '请求JSON', kwargs_json LONGTEXT NULL COMMENT '执行参数JSON', progress_json LONGTEXT NULL COMMENT '进度JSON', result_json LONGTEXT NULL COMMENT '结果JSON', error_json LONGTEXT NULL COMMENT '错误JSON', PRIMARY KEY (task_id), KEY idx_task_status (status), KEY idx_project_interface (project_id, interface_code), KEY idx_project_status (project_id, status) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='模型修正后台任务表';
```

达梦候选:

```sql
CREATE TABLE IF NOT EXISTS t_mt_py_background_task ( task_id VARCHAR(64) NOT NULL, task_type VARCHAR(128) NOT NULL, interface_code VARCHAR(128) NULL, task_kind VARCHAR(32) NOT NULL DEFAULT 'internal', project_id BIGINT NULL, status VARCHAR(32) NOT NULL, execute_count INT NOT NULL DEFAULT 0, max_execute_count INT NOT NULL DEFAULT 3, handler_module VARCHAR(255) NULL, handler_name VARCHAR(128) NULL, pass_task_id SMALLINT NOT NULL DEFAULT 0, submitted_at VARCHAR(64) NULL, started_at VARCHAR(64) NULL, finished_at VARCHAR(64) NULL, request_json LONGTEXT NULL, kwargs_json LONGTEXT NULL, progress_json LONGTEXT NULL, result_json LONGTEXT NULL, error_json LONGTEXT NULL, PRIMARY KEY (task_id), );COMMENT='模型修正后台任务表';
```

### SQL0128 webapi/background_jobs.py:109

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`19`
- 验证状态：`skipped`
- 说明：UPSERT 表名是运行时动态值，需要按实际表名生成 MERGE。；参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO {expr} ( task_id, task_type, interface_code, task_kind, project_id, status, execute_count, max_execute_count, handler_module, handler_name, pass_task_id, submitted_at, started_at, finished_at, request_json, kwargs_json, progress_json, result_json, error_json ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE task_type = VALUES(task_type), interface_code = VALUES(interface_code), task_kind = VALUES(task_kind), project_id = VALUES(project_id), status = VALUES(status), execute_count = VALUES(execute_count), max_execute_count = VALUES(max_execute_count), handler_module = VALUES(handler_module), handler_name = VALUES(handler_name), pass_task_id = VALUES(pass_task_id), submitted_at = VALUES(submitted_at), started_at = VALUES(started_at), finished_at = VALUES(finished_at), request_json = VALUES(request_json), kwargs_json = VALUES(kwargs_json), progress_json = VALUES(progress_json), result_json = VALUES(result_json), error_json = VALUES(error_json)
```

达梦候选:

```sql
INSERT INTO {expr} ( task_id, task_type, interface_code, task_kind, project_id, status, execute_count, max_execute_count, handler_module, handler_name, pass_task_id, submitted_at, started_at, finished_at, request_json, kwargs_json, progress_json, result_json, error_json ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON DUPLICATE KEY UPDATE task_type = VALUES(task_type), interface_code = VALUES(interface_code), task_kind = VALUES(task_kind), project_id = VALUES(project_id), status = VALUES(status), execute_count = VALUES(execute_count), max_execute_count = VALUES(max_execute_count), handler_module = VALUES(handler_module), handler_name = VALUES(handler_name), pass_task_id = VALUES(pass_task_id), submitted_at = VALUES(submitted_at), started_at = VALUES(started_at), finished_at = VALUES(finished_at), request_json = VALUES(request_json), kwargs_json = VALUES(kwargs_json), progress_json = VALUES(progress_json), result_json = VALUES(result_json), error_json = VALUES(error_json)
```

### SQL0129 webapi/background_jobs.py:170

- 类型：`SELECT`
- 特性：`limit`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT task_id, task_type, interface_code, task_kind, project_id, status, execute_count, max_execute_count, handler_module, handler_name, pass_task_id, submitted_at, started_at, finished_at, request_json, kwargs_json, progress_json, result_json, error_json FROM {expr} WHERE task_id = %s LIMIT 1
```

达梦候选:

```sql
SELECT task_id, task_type, interface_code, task_kind, project_id, status, execute_count, max_execute_count, handler_module, handler_name, pass_task_id, submitted_at, started_at, finished_at, request_json, kwargs_json, progress_json, result_json, error_json FROM {expr} WHERE task_id = ? FETCH FIRST 1 ROWS ONLY
```

### SQL0130 webapi/background_jobs.py:195

- 类型：`SELECT`
- 特性：`limit`
- 参数数量：`2`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT task_id, task_type, interface_code, task_kind, project_id, status, execute_count, max_execute_count, handler_module, handler_name, pass_task_id, submitted_at, started_at, finished_at, request_json, kwargs_json, progress_json, result_json, error_json FROM {expr} WHERE project_id = %s AND interface_code = %s ORDER BY submitted_at DESC, task_id DESC LIMIT 1
```

达梦候选:

```sql
SELECT task_id, task_type, interface_code, task_kind, project_id, status, execute_count, max_execute_count, handler_module, handler_name, pass_task_id, submitted_at, started_at, finished_at, request_json, kwargs_json, progress_json, result_json, error_json FROM {expr} WHERE project_id = ? AND interface_code = ? ORDER BY submitted_at DESC, task_id DESC FETCH FIRST 1 ROWS ONLY
```

### SQL0131 webapi/background_jobs.py:415

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
SELECT task_id, task_type, interface_code, task_kind, project_id, status, execute_count, max_execute_count, handler_module, handler_name, pass_task_id, submitted_at, started_at, finished_at, request_json, kwargs_json, progress_json, result_json, error_json FROM {expr} WHERE status IN ('submitted', 'running') ORDER BY submitted_at ASC, task_id ASC
```

达梦候选:

```sql
SELECT task_id, task_type, interface_code, task_kind, project_id, status, execute_count, max_execute_count, handler_module, handler_name, pass_task_id, submitted_at, started_at, finished_at, request_json, kwargs_json, progress_json, result_json, error_json FROM {expr} WHERE status IN ('submitted', 'running') ORDER BY submitted_at ASC, task_id ASC
```

### SQL0132 src/modal_service/service.py:113

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT nid, x, y, z FROM t_mt_py_test_node WHERE pid=%s ORDER BY nid
```

达梦候选:

```sql
SELECT nid, x, y, z FROM t_mt_py_test_node WHERE pid=? ORDER BY nid
```

### SQL0133 src/modal_service/service.py:122

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT point1, point2 FROM t_mt_py_test_element WHERE pid=%s ORDER BY element_no
```

达梦候选:

```sql
SELECT point1, point2 FROM t_mt_py_test_element WHERE pid=? ORDER BY element_no
```

### SQL0134 src/modal_service/service.py:170

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT s.modal_shape FROM t_mt_py_test_modal_shape s WHERE s.pid=%s
```

达梦候选:

```sql
SELECT s.modal_shape FROM t_mt_py_test_modal_shape s WHERE s.pid=?
```

### SQL0135 src/modal_service/service.py:235

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`2`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT f.mode_no, f.frequency, s.modal_shape FROM t_mt_py_test_modal_frequency f LEFT JOIN t_mt_py_test_modal_shape s ON f.pid=s.pid AND f.mode_no=s.mode_no WHERE f.pid=%s AND f.mode_no=%s ORDER BY f.mode_no
```

达梦候选:

```sql
SELECT f.mode_no, f.frequency, s.modal_shape FROM t_mt_py_test_modal_frequency f LEFT JOIN t_mt_py_test_modal_shape s ON f.pid=s.pid AND f.mode_no=s.mode_no WHERE f.pid=? AND f.mode_no=? ORDER BY f.mode_no
```

### SQL0136 src/modal_service/service.py:273

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT mode_no, frequency FROM t_mt_py_test_modal_frequency WHERE pid=%s ORDER BY mode_no
```

达梦候选:

```sql
SELECT mode_no, frequency FROM t_mt_py_test_modal_frequency WHERE pid=? ORDER BY mode_no
```

### SQL0137 src/l3/api/routes/projects.py:159

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM {expr} WHERE {expr} = %s
```

达梦候选:

```sql
DELETE FROM {expr} WHERE {expr} = ?
```

### SQL0138 services/model_update/analysis/bayesian_service.py:386

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT load_case_no, result_no, MAX(created_at) AS latest_created_at FROM t_mt_py_test_static_result WHERE pid = %s GROUP BY load_case_no, result_no ORDER BY latest_created_at DESC, load_case_no, result_no
```

达梦候选:

```sql
SELECT load_case_no, result_no, MAX(created_at) AS latest_created_at FROM t_mt_py_test_static_result WHERE pid = ? GROUP BY load_case_no, result_no ORDER BY latest_created_at DESC, load_case_no, result_no
```

### SQL0139 services/model_update/analysis/bayesian_service.py:1363

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`2`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM {expr} WHERE project_id = %s AND batch_no = %s
```

达梦候选:

```sql
DELETE FROM {expr} WHERE project_id = ? AND batch_no = ?
```

### SQL0140 services/model_update/analysis/bayesian_service.py:1368

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM {expr} WHERE pid = %s
```

达梦候选:

```sql
DELETE FROM {expr} WHERE pid = ?
```

### SQL0141 services/model_update/analysis/bayesian_service.py:1370

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`2`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM {expr} WHERE pid = %s AND batch_no = %s
```

达梦候选:

```sql
DELETE FROM {expr} WHERE pid = ? AND batch_no = ?
```

### SQL0142 services/model_update/analysis/bayesian_service.py:1372

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`3`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_tracking_iteration (pid, batch_no, iterations) VALUES (%s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_tracking_iteration (pid, batch_no, iterations) VALUES (?, ?, ?)
```

### SQL0143 services/model_update/analysis/bayesian_service.py:1402

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`12`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_bayesian_iteration_metric (project_id, batch_no, iteration, ccabs, rel_res, ra_norm, re_norm, dr_norm, dx_norm, max_abs_dparam, mean_abs_response_diff, max_abs_response_diff) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_bayesian_iteration_metric (project_id, batch_no, iteration, ccabs, rel_res, ra_norm, re_norm, dr_norm, dx_norm, max_abs_dparam, mean_abs_response_diff, max_abs_response_diff) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
```

### SQL0144 services/model_update/analysis/bayesian_service.py:1431

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`4`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_relevance_tracking (pid, iteration, type, value) VALUES (%s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_relevance_tracking (pid, iteration, type, value) VALUES (?, ?, ?, ?)
```

### SQL0145 services/model_update/analysis/bayesian_service.py:1447

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`7`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_response_difference (pid, batch_no, response_name, iteration, cal_result_value, test_result_value, response_diff) VALUES (%s, %s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_response_difference (pid, batch_no, response_name, iteration, cal_result_value, test_result_value, response_diff) VALUES (?, ?, ?, ?, ?, ?, ?)
```

### SQL0146 services/model_update/analysis/bayesian_service.py:1463

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`6`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_tracking_value (pid, batch_no, tracking_type, tracking_name, iteration, track_value) VALUES (%s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_tracking_value (pid, batch_no, tracking_type, tracking_name, iteration, track_value) VALUES (?, ?, ?, ?, ?, ?)
```

### SQL0147 services/model_update/analysis/bayesian_service.py:1487

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`6`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_tracking_value (pid, batch_no, tracking_type, tracking_name, iteration, track_value) VALUES (%s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_tracking_value (pid, batch_no, tracking_type, tracking_name, iteration, track_value) VALUES (?, ?, ?, ?, ?, ?)
```

### SQL0148 services/model_update/analysis/bayesian_service.py:1512

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`9`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_parameter_variation (pid, batch_no, parameter_name, parameter_hierarchy, parameter_type, parameter_scope, ori_value, result_value, parameter_variation) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_parameter_variation (pid, batch_no, parameter_name, parameter_hierarchy, parameter_type, parameter_scope, ori_value, result_value, parameter_variation) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
```

### SQL0149 services/model_update/analysis/bayesian_service.py:3239

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`2`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM t_mt_py_fem_model_update_static_result WHERE pid = %s AND batch_no = %s
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_model_update_static_result WHERE pid = ? AND batch_no = ?
```

### SQL0150 services/model_update/analysis/bayesian_service.py:3258

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`11`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_model_update_static_result (pid, batch_no, step_name, frame_idx, instance_name, part_name, fem_node_label, u1, u2, u3, extra_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE step_name = VALUES(step_name), part_name = VALUES(part_name), u1 = VALUES(u1), u2 = VALUES(u2), u3 = VALUES(u3), extra_json = VALUES(extra_json), created_at = CURRENT_TIMESTAMP
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_model_update_static_result t USING (SELECT ? AS pid, ? AS batch_no, ? AS step_name, ? AS frame_idx, ? AS instance_name, ? AS part_name, ? AS fem_node_label, ? AS u1, ? AS u2, ? AS u3, ? AS extra_json FROM DUAL) s ON (t.pid = s.pid AND t.batch_no = s.batch_no AND t.frame_idx = s.frame_idx AND t.instance_name = s.instance_name AND t.fem_node_label = s.fem_node_label) WHEN MATCHED THEN UPDATE SET t.step_name = s.step_name, t.part_name = s.part_name, t.u1 = s.u1, t.u2 = s.u2, t.u3 = s.u3, t.extra_json = s.extra_json, t.created_at = CURRENT_TIMESTAMP WHEN NOT MATCHED THEN INSERT (pid, batch_no, step_name, frame_idx, instance_name, part_name, fem_node_label, u1, u2, u3, extra_json) VALUES (s.pid, s.batch_no, s.step_name, s.frame_idx, s.instance_name, s.part_name, s.fem_node_label, s.u1, s.u2, s.u3, s.extra_json)
```

### SQL0151 services/model_update/analysis/bayesian_service.py:3990

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`2`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM t_mt_py_fem_model_update_modal_result WHERE pid = %s AND batch_no = %s
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_model_update_modal_result WHERE pid = ? AND batch_no = ?
```

### SQL0152 services/model_update/analysis/bayesian_service.py:4013

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`13`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_model_update_modal_result (pid, batch_no, response_name, response_type, fem_mode_no, test_mode_no, freq_fem_initial, freq_fem_updated, freq_test, initial_relative_error, updated_relative_error, mac, extra_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE response_name = VALUES(response_name), response_type = VALUES(response_type), freq_fem_initial = VALUES(freq_fem_initial), freq_fem_updated = VALUES(freq_fem_updated), freq_test = VALUES(freq_test), initial_relative_error = VALUES(initial_relative_error), updated_relative_error = VALUES(updated_relative_error), mac = VALUES(mac), extra_json = VALUES(extra_json), created_at = CURRENT_TIMESTAMP
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_model_update_modal_result t USING (SELECT ? AS pid, ? AS batch_no, ? AS response_name, ? AS response_type, ? AS fem_mode_no, ? AS test_mode_no, ? AS freq_fem_initial, ? AS freq_fem_updated, ? AS freq_test, ? AS initial_relative_error, ? AS updated_relative_error, ? AS mac, ? AS extra_json FROM DUAL) s ON (t.pid = s.pid AND t.batch_no = s.batch_no AND t.fem_mode_no = s.fem_mode_no AND t.test_mode_no = s.test_mode_no) WHEN MATCHED THEN UPDATE SET t.response_name = s.response_name, t.response_type = s.response_type, t.freq_fem_initial = s.freq_fem_initial, t.freq_fem_updated = s.freq_fem_updated, t.freq_test = s.freq_test, t.initial_relative_error = s.initial_relative_error, t.updated_relative_error = s.updated_relative_error, t.mac = s.mac, t.extra_json = s.extra_json, t.created_at = CURRENT_TIMESTAMP WHEN NOT MATCHED THEN INSERT (pid, batch_no, response_name, response_type, fem_mode_no, test_mode_no, freq_fem_initial, freq_fem_updated, freq_test, initial_relative_error, updated_relative_error, mac, extra_json) VALUES (s.pid, s.batch_no, s.response_name, s.response_type, s.fem_mode_no, s.test_mode_no, s.freq_fem_initial, s.freq_fem_updated, s.freq_test, s.initial_relative_error, s.updated_relative_error, s.mac, s.extra_json)
```

### SQL0153 services/model_update/analysis/console_log_service.py:57

- 类型：`INSERT`
- 特性：`values_function, backtick_identifier`
- 参数数量：`3`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_console_log (pid, `time`, log_text) VALUES (%s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_console_log (pid, "time", log_text) VALUES (?, ?, ?)
```

### SQL0154 services/model_update/analysis/fem_catalog_service.py:211

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM {expr} WHERE pid = %s
```

达梦候选:

```sql
DELETE FROM {expr} WHERE pid = ?
```

### SQL0155 services/model_update/analysis/fem_catalog_service.py:712

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT CAST(nid AS CHAR) AS test_node_id, x AS x_position, y AS y_position, z AS z_position FROM t_mt_py_test_node WHERE pid = %s ORDER BY nid
```

达梦候选:

```sql
SELECT CAST(nid AS CHAR) AS test_node_id, x AS x_position, y AS y_position, z AS z_position FROM t_mt_py_test_node WHERE pid = ? ORDER BY nid
```

### SQL0156 services/model_update/analysis/fem_catalog_service.py:725

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT measuring_point_name AS test_node_id, x_position, y_position, z_position FROM t_mt_measuring_point_info WHERE project_id = %s ORDER BY measuring_point_name
```

达梦候选:

```sql
SELECT measuring_point_name AS test_node_id, x_position, y_position, z_position FROM t_mt_measuring_point_info WHERE project_id = ? ORDER BY measuring_point_name
```

### SQL0157 services/model_update/analysis/fem_catalog_service.py:1308

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`3`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_material_overview (Id, pid, Type) VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE Type = VALUES(Type)
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_material_overview t USING (SELECT ? AS Id, ? AS pid, ? AS Type FROM DUAL) s ON (t.Id = s.Id AND t.pid = s.pid) WHEN MATCHED THEN UPDATE SET t.Type = s.Type WHEN NOT MATCHED THEN INSERT (Id, pid, Type) VALUES (s.Id, s.pid, s.Type)
```

### SQL0158 services/model_update/analysis/fem_catalog_service.py:1324

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`6`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_isotropic (Id, pid, RHO, E, NU, GE) VALUES (%s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE RHO = VALUES(RHO), E = VALUES(E), NU = VALUES(NU), GE = VALUES(GE)
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_isotropic t USING (SELECT ? AS Id, ? AS pid, ? AS RHO, ? AS E, ? AS NU, ? AS GE FROM DUAL) s ON (t.Id = s.Id AND t.pid = s.pid) WHEN MATCHED THEN UPDATE SET t.RHO = s.RHO, t.E = s.E, t.NU = s.NU, t.GE = s.GE WHEN NOT MATCHED THEN INSERT (Id, pid, RHO, E, NU, GE) VALUES (s.Id, s.pid, s.RHO, s.E, s.NU, s.GE)
```

### SQL0159 services/model_update/analysis/fem_catalog_service.py:1340

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`3`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_property (Id, pid, Type) VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE Type = VALUES(Type)
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_property t USING (SELECT ? AS Id, ? AS pid, ? AS Type FROM DUAL) s ON (t.Id = s.Id AND t.pid = s.pid) WHEN MATCHED THEN UPDATE SET t.Type = s.Type WHEN NOT MATCHED THEN INSERT (Id, pid, Type) VALUES (s.Id, s.pid, s.Type)
```

### SQL0160 services/model_update/analysis/fem_catalog_service.py:1356

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`6`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_shell_property (Id, pid, Thickness, NSM, THETA, element_set) VALUES (%s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE Thickness = VALUES(Thickness), NSM = VALUES(NSM), THETA = VALUES(THETA), element_set = VALUES(element_set)
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_shell_property t USING (SELECT ? AS Id, ? AS pid, ? AS Thickness, ? AS NSM, ? AS THETA, ? AS element_set FROM DUAL) s ON (t.Id = s.Id AND t.pid = s.pid) WHEN MATCHED THEN UPDATE SET t.Thickness = s.Thickness, t.NSM = s.NSM, t.THETA = s.THETA, t.element_set = s.element_set WHEN NOT MATCHED THEN INSERT (Id, pid, Thickness, NSM, THETA, element_set) VALUES (s.Id, s.pid, s.Thickness, s.NSM, s.THETA, s.element_set)
```

### SQL0161 services/model_update/analysis/fem_catalog_service.py:1382

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`12`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_beam_property (Id, pid, AX, AY, AZ, IX, IY, IZ, CW, YN, ZN, NSM) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE AX = VALUES(AX), AY = VALUES(AY), AZ = VALUES(AZ), IX = VALUES(IX), IY = VALUES(IY), IZ = VALUES(IZ), CW = VALUES(CW), YN = VALUES(YN), ZN = VALUES(ZN), NSM = VALUES(NSM)
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_beam_property t USING (SELECT ? AS Id, ? AS pid, ? AS AX, ? AS AY, ? AS AZ, ? AS IX, ? AS IY, ? AS IZ, ? AS CW, ? AS YN, ? AS ZN, ? AS NSM FROM DUAL) s ON (t.Id = s.Id AND t.pid = s.pid) WHEN MATCHED THEN UPDATE SET t.AX = s.AX, t.AY = s.AY, t.AZ = s.AZ, t.IX = s.IX, t.IY = s.IY, t.IZ = s.IZ, t.CW = s.CW, t.YN = s.YN, t.ZN = s.ZN, t.NSM = s.NSM WHEN NOT MATCHED THEN INSERT (Id, pid, AX, AY, AZ, IX, IY, IZ, CW, YN, ZN, NSM) VALUES (s.Id, s.pid, s.AX, s.AY, s.AZ, s.IX, s.IY, s.IZ, s.CW, s.YN, s.ZN, s.NSM)
```

### SQL0162 services/model_update/analysis/fem_catalog_service.py:1411

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`9`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_boundary (Id, pid, Node, UX, UY, UZ, RX, RY, RZ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE Node = VALUES(Node), UX = VALUES(UX), UY = VALUES(UY), UZ = VALUES(UZ), RX = VALUES(RX), RY = VALUES(RY), RZ = VALUES(RZ)
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_boundary t USING (SELECT ? AS Id, ? AS pid, ? AS Node, ? AS UX, ? AS UY, ? AS UZ, ? AS RX, ? AS RY, ? AS RZ FROM DUAL) s ON (t.Id = s.Id AND t.pid = s.pid) WHEN MATCHED THEN UPDATE SET t.Node = s.Node, t.UX = s.UX, t.UY = s.UY, t.UZ = s.UZ, t.RX = s.RX, t.RY = s.RY, t.RZ = s.RZ WHEN NOT MATCHED THEN INSERT (Id, pid, Node, UX, UY, UZ, RX, RY, RZ) VALUES (s.Id, s.pid, s.Node, s.UX, s.UY, s.UZ, s.RX, s.RY, s.RZ)
```

### SQL0163 services/model_update/analysis/fem_catalog_service.py:1429

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`7`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_parameter_definition (pid, parameter_name, expression, scalar_value, is_design_parameter, design_order, extra_json) VALUES (%s, %s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_parameter_definition (pid, parameter_name, expression, scalar_value, is_design_parameter, design_order, extra_json) VALUES (?, ?, ?, ?, ?, ?, ?)
```

### SQL0164 services/model_update/analysis/fem_catalog_service.py:1446

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`12`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_parameter_target (pid, parameter_name, target_type, set_name, set_type, set_scope, instance_name, part_name, source_keyword, source_path, component_name, extra_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_parameter_target (pid, parameter_name, target_type, set_name, set_type, set_scope, instance_name, part_name, source_keyword, source_path, component_name, extra_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
```

### SQL0165 services/model_update/analysis/fem_catalog_service.py:1472

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`5`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_supported_quantity (quantity_code, quantity_name, unit, enabled, sort_no) VALUES (%s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE quantity_name = VALUES(quantity_name), unit = VALUES(unit), enabled = VALUES(enabled), sort_no = VALUES(sort_no)
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_supported_quantity t USING (SELECT ? AS quantity_code, ? AS quantity_name, ? AS unit, ? AS enabled, ? AS sort_no FROM DUAL) s ON (t.quantity_code = s.quantity_code) WHEN MATCHED THEN UPDATE SET t.quantity_name = s.quantity_name, t.unit = s.unit, t.enabled = s.enabled, t.sort_no = s.sort_no WHEN NOT MATCHED THEN INSERT (quantity_code, quantity_name, unit, enabled, sort_no) VALUES (s.quantity_code, s.quantity_name, s.unit, s.enabled, s.sort_no)
```

### SQL0166 services/model_update/analysis/fem_catalog_service.py:1505

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`17`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_quantity_set_capability (pid, quantity_code, set_name, set_type, set_scope, instance_name, part_name, set_role, element_family, section_type, material_name, member_count, is_internal, supports_global, supports_local, current_value, extra_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE set_role = VALUES(set_role), element_family = VALUES(element_family), section_type = VALUES(section_type), material_name = VALUES(material_name), member_count = VALUES(member_count), is_internal = VALUES(is_internal), supports_global = VALUES(supports_global), supports_local = VALUES(supports_local), current_value = VALUES(current_value), extra_json = VALUES(extra_json)
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_quantity_set_capability t USING (SELECT ? AS pid, ? AS quantity_code, ? AS set_name, ? AS set_type, ? AS set_scope, ? AS instance_name, ? AS part_name, ? AS set_role, ? AS element_family, ? AS section_type, ? AS material_name, ? AS member_count, ? AS is_internal, ? AS supports_global, ? AS supports_local, ? AS current_value, ? AS extra_json FROM DUAL) s ON (t.pid = s.pid AND t.quantity_code = s.quantity_code AND t.set_name = s.set_name AND t.set_type = s.set_type AND t.set_scope = s.set_scope AND t.instance_name = s.instance_name AND t.part_name = s.part_name) WHEN MATCHED THEN UPDATE SET t.set_role = s.set_role, t.element_family = s.element_family, t.section_type = s.section_type, t.material_name = s.material_name, t.member_count = s.member_count, t.is_internal = s.is_internal, t.supports_global = s.supports_global, t.supports_local = s.supports_local, t.current_value = s.current_value, t.extra_json = s.extra_json WHEN NOT MATCHED THEN INSERT (pid, quantity_code, set_name, set_type, set_scope, instance_name, part_name, set_role, element_family, section_type, material_name, member_count, is_internal, supports_global, supports_local, current_value, extra_json) VALUES (s.pid, s.quantity_code, s.set_name, s.set_type, s.set_scope, s.instance_name, s.part_name, s.set_role, s.element_family, s.section_type, s.material_name, s.member_count, s.is_internal, s.supports_global, s.supports_local, s.current_value, s.extra_json)
```

### SQL0167 services/model_update/analysis/fem_catalog_service.py:1538

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function, now_function`
- 参数数量：`7`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_node_octree_cache (pid, source_file_path, cache_file_path, node_count, instance_count, bbox_min, bbox_max, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW()) ON DUPLICATE KEY UPDATE cache_file_path = VALUES(cache_file_path), node_count = VALUES(node_count), instance_count = VALUES(instance_count), bbox_min = VALUES(bbox_min), bbox_max = VALUES(bbox_max), updated_at = NOW()
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_node_octree_cache t USING (SELECT ? AS pid, ? AS source_file_path, ? AS cache_file_path, ? AS node_count, ? AS instance_count, ? AS bbox_min, ? AS bbox_max, CURRENT_TIMESTAMP AS updated_at FROM DUAL) s ON (t.pid = s.pid AND t.source_file_path = s.source_file_path) WHEN MATCHED THEN UPDATE SET t.cache_file_path = s.cache_file_path, t.node_count = s.node_count, t.instance_count = s.instance_count, t.bbox_min = s.bbox_min, t.bbox_max = s.bbox_max, t.updated_at = CURRENT_TIMESTAMP WHEN NOT MATCHED THEN INSERT (pid, source_file_path, cache_file_path, node_count, instance_count, bbox_min, bbox_max, updated_at) VALUES (s.pid, s.source_file_path, s.cache_file_path, s.node_count, s.instance_count, s.bbox_min, s.bbox_max, s.updated_at)
```

### SQL0168 services/model_update/analysis/fem_catalog_service.py:1606

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT parameter_name, expression, scalar_value, is_design_parameter, design_order, extra_json FROM t_mt_py_fem_parameter_definition WHERE pid = %s ORDER BY is_design_parameter DESC, design_order, parameter_name
```

达梦候选:

```sql
SELECT parameter_name, expression, scalar_value, is_design_parameter, design_order, extra_json FROM t_mt_py_fem_parameter_definition WHERE pid = ? ORDER BY is_design_parameter DESC, design_order, parameter_name
```

### SQL0169 services/model_update/analysis/fem_catalog_service.py:1614

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT parameter_name, target_type, set_name, set_type, set_scope, instance_name, part_name, source_keyword, source_path, component_name, extra_json FROM t_mt_py_fem_parameter_target WHERE pid = %s ORDER BY parameter_name, source_path
```

达梦候选:

```sql
SELECT parameter_name, target_type, set_name, set_type, set_scope, instance_name, part_name, source_keyword, source_path, component_name, extra_json FROM t_mt_py_fem_parameter_target WHERE pid = ? ORDER BY parameter_name, source_path
```

### SQL0170 services/model_update/analysis/fem_catalog_service.py:1623

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT response_no, request_no, step_name, frequency, region_type, set_name, variables_json, extra_json FROM t_mt_py_fem_static_sensitivity_response_catalog WHERE pid = %s ORDER BY response_no, request_no
```

达梦候选:

```sql
SELECT response_no, request_no, step_name, frequency, region_type, set_name, variables_json, extra_json FROM t_mt_py_fem_static_sensitivity_response_catalog WHERE pid = ? ORDER BY response_no, request_no
```

### SQL0171 services/model_update/analysis/fem_catalog_service.py:1631

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`matched`
- 说明：前 1000 行结果哈希一致

MySQL:

```sql
SELECT quantity_code, quantity_name, unit, enabled, sort_no FROM t_mt_py_fem_supported_quantity WHERE enabled = 1 ORDER BY sort_no, quantity_code
```

达梦候选:

```sql
SELECT quantity_code, quantity_name, unit, enabled, sort_no FROM t_mt_py_fem_supported_quantity WHERE enabled = 1 ORDER BY sort_no, quantity_code
```

### SQL0172 services/model_update/analysis/fem_catalog_service.py:1639

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT quantity_code, set_name, set_type, set_scope, instance_name, part_name, set_role, element_family, section_type, material_name, member_count, is_internal, supports_global, supports_local, current_value, extra_json FROM t_mt_py_fem_quantity_set_capability WHERE pid = %s ORDER BY quantity_code, set_scope, set_type, set_name, instance_name, part_name
```

达梦候选:

```sql
SELECT quantity_code, set_name, set_type, set_scope, instance_name, part_name, set_role, element_family, section_type, material_name, member_count, is_internal, supports_global, supports_local, current_value, extra_json FROM t_mt_py_fem_quantity_set_capability WHERE pid = ? ORDER BY quantity_code, set_scope, set_type, set_name, instance_name, part_name
```

### SQL0173 services/model_update/analysis/fem_catalog_service.py:1651

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT parameter_group_name, parameter_name, quantity_code, selection_mode, set_name, set_type, set_scope, instance_name, part_name, element_label, current_value, lower, upper, prob_id, scatter, description, extra_json, created_at FROM t_mt_py_fem_selected_parameter WHERE pid = %s ORDER BY created_at DESC, parameter_name
```

达梦候选:

```sql
SELECT parameter_group_name, parameter_name, quantity_code, selection_mode, set_name, set_type, set_scope, instance_name, part_name, element_label, current_value, lower, upper, prob_id, scatter, description, extra_json, created_at FROM t_mt_py_fem_selected_parameter WHERE pid = ? ORDER BY created_at DESC, parameter_name
```

### SQL0174 services/model_update/analysis/fem_catalog_service.py:1660

- 类型：`SELECT`
- 特性：`limit`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT source_file_path, cache_file_path, node_count, instance_count, bbox_min, bbox_max, updated_at FROM t_mt_py_fem_node_octree_cache WHERE pid = %s ORDER BY updated_at DESC LIMIT 1
```

达梦候选:

```sql
SELECT source_file_path, cache_file_path, node_count, instance_count, bbox_min, bbox_max, updated_at FROM t_mt_py_fem_node_octree_cache WHERE pid = ? ORDER BY updated_at DESC FETCH FIRST 1 ROWS ONLY
```

### SQL0175 services/model_update/analysis/fem_catalog_service.py:1669

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT COUNT(*) AS cnt FROM t_mt_py_fem_node_match WHERE pid = %s
```

达梦候选:

```sql
SELECT COUNT(*) AS cnt FROM t_mt_py_fem_node_match WHERE pid = ?
```

### SQL0176 services/model_update/analysis/fem_catalog_service.py:1671

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT COUNT(*) AS cnt FROM t_mt_py_fem_dof_match WHERE pid = %s
```

达梦候选:

```sql
SELECT COUNT(*) AS cnt FROM t_mt_py_fem_dof_match WHERE pid = ?
```

### SQL0177 services/model_update/analysis/fem_catalog_service.py:1673

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT COUNT(*) AS cnt FROM t_mt_py_fem_dynamic_response_catalog WHERE pid = %s
```

达梦候选:

```sql
SELECT COUNT(*) AS cnt FROM t_mt_py_fem_dynamic_response_catalog WHERE pid = ?
```

### SQL0178 services/model_update/analysis/fem_catalog_service.py:1675

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT COUNT(DISTINCT mode_no) AS cnt FROM t_mt_py_fem_modal_result WHERE pid = %s
```

达梦候选:

```sql
SELECT COUNT(DISTINCT mode_no) AS cnt FROM t_mt_py_fem_modal_result WHERE pid = ?
```

### SQL0179 services/model_update/analysis/fem_catalog_service.py:1679

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT COUNT(*) AS cnt FROM t_mt_py_fem_modal_correlation WHERE pid = %s
```

达梦候选:

```sql
SELECT COUNT(*) AS cnt FROM t_mt_py_fem_modal_correlation WHERE pid = ?
```

### SQL0180 services/model_update/analysis/fem_catalog_service.py:1681

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT COUNT(*) AS cnt FROM t_mt_py_fem_material_overview WHERE pid = %s
```

达梦候选:

```sql
SELECT COUNT(*) AS cnt FROM t_mt_py_fem_material_overview WHERE pid = ?
```

### SQL0181 services/model_update/analysis/fem_catalog_service.py:1683

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT COUNT(*) AS cnt FROM t_mt_py_fem_isotropic WHERE pid = %s
```

达梦候选:

```sql
SELECT COUNT(*) AS cnt FROM t_mt_py_fem_isotropic WHERE pid = ?
```

### SQL0182 services/model_update/analysis/fem_catalog_service.py:1685

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT COUNT(*) AS cnt FROM t_mt_py_fem_property WHERE pid = %s
```

达梦候选:

```sql
SELECT COUNT(*) AS cnt FROM t_mt_py_fem_property WHERE pid = ?
```

### SQL0183 services/model_update/analysis/fem_catalog_service.py:1687

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT COUNT(*) AS cnt FROM t_mt_py_fem_shell_property WHERE pid = %s
```

达梦候选:

```sql
SELECT COUNT(*) AS cnt FROM t_mt_py_fem_shell_property WHERE pid = ?
```

### SQL0184 services/model_update/analysis/fem_catalog_service.py:1689

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT COUNT(*) AS cnt FROM t_mt_py_fem_beam_property WHERE pid = %s
```

达梦候选:

```sql
SELECT COUNT(*) AS cnt FROM t_mt_py_fem_beam_property WHERE pid = ?
```

### SQL0185 services/model_update/analysis/fem_catalog_service.py:1691

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT COUNT(*) AS cnt FROM t_mt_py_fem_boundary WHERE pid = %s
```

达梦候选:

```sql
SELECT COUNT(*) AS cnt FROM t_mt_py_fem_boundary WHERE pid = ?
```

### SQL0186 services/model_update/analysis/fem_catalog_service.py:1729

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`matched`
- 说明：前 1000 行结果哈希一致

MySQL:

```sql
SELECT quantity_code, quantity_name, unit, enabled, sort_no FROM t_mt_py_fem_supported_quantity WHERE enabled = 1 ORDER BY sort_no, quantity_code
```

达梦候选:

```sql
SELECT quantity_code, quantity_name, unit, enabled, sort_no FROM t_mt_py_fem_supported_quantity WHERE enabled = 1 ORDER BY sort_no, quantity_code
```

### SQL0187 services/model_update/analysis/fem_catalog_service.py:1737

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT quantity_code, set_name, set_type, set_scope, instance_name, part_name, set_role, element_family, section_type, material_name, member_count, is_internal, supports_global, supports_local, current_value, extra_json FROM t_mt_py_fem_quantity_set_capability WHERE pid = %s AND supports_global=1 ORDER BY quantity_code, set_scope, set_type, set_name, instance_name, part_name
```

达梦候选:

```sql
SELECT quantity_code, set_name, set_type, set_scope, instance_name, part_name, set_role, element_family, section_type, material_name, member_count, is_internal, supports_global, supports_local, current_value, extra_json FROM t_mt_py_fem_quantity_set_capability WHERE pid = ? AND supports_global=1 ORDER BY quantity_code, set_scope, set_type, set_name, instance_name, part_name
```

### SQL0188 services/model_update/analysis/fem_catalog_service.py:1930

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`2`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT quantity_code, set_name, set_type, set_scope, instance_name, part_name, set_role, element_family, section_type, material_name, member_count, supports_global, supports_local, current_value, extra_json FROM t_mt_py_fem_quantity_set_capability WHERE pid = %s AND quantity_code IN ({expr}) AND set_name = %s
```

达梦候选:

```sql
SELECT quantity_code, set_name, set_type, set_scope, instance_name, part_name, set_role, element_family, section_type, material_name, member_count, supports_global, supports_local, current_value, extra_json FROM t_mt_py_fem_quantity_set_capability WHERE pid = ? AND quantity_code IN ({expr}) AND set_name = ?
```

### SQL0189 services/model_update/analysis/fem_catalog_service.py:2067

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT set_name, current_value, extra_json FROM t_mt_py_fem_quantity_set_capability WHERE pid = %s AND quantity_code IN ({expr})
```

达梦候选:

```sql
SELECT set_name, current_value, extra_json FROM t_mt_py_fem_quantity_set_capability WHERE pid = ? AND quantity_code IN ({expr})
```

### SQL0190 services/model_update/analysis/fem_catalog_service.py:2147

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`2`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT set_name, set_type, set_scope, instance_name, part_name, set_role, current_value, extra_json FROM t_mt_py_fem_quantity_set_capability WHERE pid = %s AND quantity_code = %s AND supports_local = 1 ORDER BY CASE WHEN set_scope = 'ASSEMBLY' THEN 0 ELSE 1 END, CASE WHEN set_role = 'PROPERTY_SET' THEN 0 ELSE 1 END, set_name, instance_name, part_name
```

达梦候选:

```sql
SELECT set_name, set_type, set_scope, instance_name, part_name, set_role, current_value, extra_json FROM t_mt_py_fem_quantity_set_capability WHERE pid = ? AND quantity_code = ? AND supports_local = 1 ORDER BY CASE WHEN set_scope = 'ASSEMBLY' THEN 0 ELSE 1 END, CASE WHEN set_role = 'PROPERTY_SET' THEN 0 ELSE 1 END, set_name, instance_name, part_name
```

### SQL0191 services/model_update/analysis/fem_catalog_service.py:2222

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`2`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT set_scope, instance_name, part_name, element_label, extra_json FROM t_mt_py_fem_selected_parameter WHERE pid = %s AND quantity_code = %s
```

达梦候选:

```sql
SELECT set_scope, instance_name, part_name, element_label, extra_json FROM t_mt_py_fem_selected_parameter WHERE pid = ? AND quantity_code = ?
```

### SQL0192 services/model_update/analysis/fem_catalog_service.py:2327

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`19`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_selected_parameter (pid, parameter_group_name, parameter_name, quantity_code, selection_mode, set_name, set_type, set_scope, instance_name, part_name, element_label, current_value, lower, upper, prob_id, scatter, description, usage_scope, extra_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_selected_parameter (pid, parameter_group_name, parameter_name, quantity_code, selection_mode, set_name, set_type, set_scope, instance_name, part_name, element_label, current_value, lower, upper, prob_id, scatter, description, usage_scope, extra_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
```

### SQL0193 services/model_update/analysis/fem_catalog_service.py:2329

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`19`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_selected_parameter (pid, parameter_group_name, parameter_name, quantity_code, selection_mode, set_name, set_type, set_scope, instance_name, part_name, element_label, current_value, lower, upper, prob_id, scatter, description, usage_scope, extra_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_selected_parameter (pid, parameter_group_name, parameter_name, quantity_code, selection_mode, set_name, set_type, set_scope, instance_name, part_name, element_label, current_value, lower, upper, prob_id, scatter, description, usage_scope, extra_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
```

### SQL0194 services/model_update/analysis/fem_catalog_service.py:2332

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`19`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_selected_parameter (pid, parameter_group_name, parameter_name, quantity_code, selection_mode, set_name, set_type, set_scope, instance_name, part_name, element_label, current_value, lower, upper, prob_id, scatter, description, usage_scope, extra_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_selected_parameter (pid, parameter_group_name, parameter_name, quantity_code, selection_mode, set_name, set_type, set_scope, instance_name, part_name, element_label, current_value, lower, upper, prob_id, scatter, description, usage_scope, extra_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
```

### SQL0195 services/model_update/analysis/fem_catalog_service.py:2460

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT set_name, set_type, set_scope, instance_name, part_name, element_label, extra_json FROM t_mt_py_fem_selected_parameter WHERE pid = %s AND quantity_code IN ({expr})
```

达梦候选:

```sql
SELECT set_name, set_type, set_scope, instance_name, part_name, element_label, extra_json FROM t_mt_py_fem_selected_parameter WHERE pid = ? AND quantity_code IN ({expr})
```

### SQL0196 services/model_update/analysis/fem_catalog_service.py:2559

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`19`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_selected_parameter (pid, parameter_group_name, parameter_name, quantity_code, selection_mode, set_name, set_type, set_scope, instance_name, part_name, element_label, current_value, lower, upper, prob_id, scatter, description, usage_scope, extra_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_selected_parameter (pid, parameter_group_name, parameter_name, quantity_code, selection_mode, set_name, set_type, set_scope, instance_name, part_name, element_label, current_value, lower, upper, prob_id, scatter, description, usage_scope, extra_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
```

### SQL0197 services/model_update/analysis/fem_catalog_service.py:2561

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`19`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_selected_parameter (pid, parameter_group_name, parameter_name, quantity_code, selection_mode, set_name, set_type, set_scope, instance_name, part_name, element_label, current_value, lower, upper, prob_id, scatter, description, usage_scope, extra_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_selected_parameter (pid, parameter_group_name, parameter_name, quantity_code, selection_mode, set_name, set_type, set_scope, instance_name, part_name, element_label, current_value, lower, upper, prob_id, scatter, description, usage_scope, extra_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
```

### SQL0198 services/model_update/analysis/fem_catalog_service.py:2564

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`19`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_selected_parameter (pid, parameter_group_name, parameter_name, quantity_code, selection_mode, set_name, set_type, set_scope, instance_name, part_name, element_label, current_value, lower, upper, prob_id, scatter, description, usage_scope, extra_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_selected_parameter (pid, parameter_group_name, parameter_name, quantity_code, selection_mode, set_name, set_type, set_scope, instance_name, part_name, element_label, current_value, lower, upper, prob_id, scatter, description, usage_scope, extra_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
```

### SQL0199 services/model_update/analysis/fem_catalog_service.py:2620

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`3`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT set_name, set_type, set_scope, instance_name, part_name, element_label FROM t_mt_py_fem_selected_parameter WHERE pid = %s AND quantity_code IN ({expr}) AND set_scope = %s AND {expr} <=> %s
```

达梦候选:

```sql
SELECT set_name, set_type, set_scope, instance_name, part_name, element_label FROM t_mt_py_fem_selected_parameter WHERE pid = ? AND quantity_code IN ({expr}) AND set_scope = ? AND {expr} <=> ?
```

### SQL0200 services/model_update/analysis/fem_catalog_service.py:2639

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`3`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT set_name, set_type, set_scope, instance_name, part_name, extra_json FROM t_mt_py_fem_quantity_set_capability WHERE pid = %s AND quantity_code IN ({expr}) AND set_scope = %s AND {expr} <=> %s AND set_name IN ({expr})
```

达梦候选:

```sql
SELECT set_name, set_type, set_scope, instance_name, part_name, extra_json FROM t_mt_py_fem_quantity_set_capability WHERE pid = ? AND quantity_code IN ({expr}) AND set_scope = ? AND {expr} <=> ? AND set_name IN ({expr})
```

### SQL0201 services/model_update/analysis/fem_catalog_service.py:2736

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`19`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_selected_parameter (pid, parameter_group_name, parameter_name, quantity_code, selection_mode, set_name, set_type, set_scope, instance_name, part_name, element_label, current_value, lower, upper, prob_id, scatter, description, usage_scope, extra_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_selected_parameter (pid, parameter_group_name, parameter_name, quantity_code, selection_mode, set_name, set_type, set_scope, instance_name, part_name, element_label, current_value, lower, upper, prob_id, scatter, description, usage_scope, extra_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
```

### SQL0202 services/model_update/analysis/fem_catalog_service.py:2738

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`19`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_selected_parameter (pid, parameter_group_name, parameter_name, quantity_code, selection_mode, set_name, set_type, set_scope, instance_name, part_name, element_label, current_value, lower, upper, prob_id, scatter, description, usage_scope, extra_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_selected_parameter (pid, parameter_group_name, parameter_name, quantity_code, selection_mode, set_name, set_type, set_scope, instance_name, part_name, element_label, current_value, lower, upper, prob_id, scatter, description, usage_scope, extra_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
```

### SQL0203 services/model_update/analysis/fem_catalog_service.py:2741

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`19`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_selected_parameter (pid, parameter_group_name, parameter_name, quantity_code, selection_mode, set_name, set_type, set_scope, instance_name, part_name, element_label, current_value, lower, upper, prob_id, scatter, description, usage_scope, extra_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_selected_parameter (pid, parameter_group_name, parameter_name, quantity_code, selection_mode, set_name, set_type, set_scope, instance_name, part_name, element_label, current_value, lower, upper, prob_id, scatter, description, usage_scope, extra_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
```

### SQL0204 services/model_update/analysis/fem_catalog_service.py:2840

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT response_no, request_no, step_name, frequency, region_type, set_name, set_scope, instance_name, part_name, variables_json, extra_json FROM t_mt_py_fem_static_sensitivity_response_catalog WHERE pid = %s
```

达梦候选:

```sql
SELECT response_no, request_no, step_name, frequency, region_type, set_name, set_scope, instance_name, part_name, variables_json, extra_json FROM t_mt_py_fem_static_sensitivity_response_catalog WHERE pid = ?
```

### SQL0205 services/model_update/analysis/fem_catalog_service.py:2869

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT COALESCE(MAX(response_no), 0) AS max_no FROM t_mt_py_fem_static_sensitivity_response_catalog WHERE pid = %s
```

达梦候选:

```sql
SELECT COALESCE(MAX(response_no), 0) AS max_no FROM t_mt_py_fem_static_sensitivity_response_catalog WHERE pid = ?
```

### SQL0206 services/model_update/analysis/fem_catalog_service.py:2894

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`12`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_static_sensitivity_response_catalog (pid, response_no, request_no, step_name, frequency, region_type, set_name, set_scope, instance_name, part_name, variables_json, extra_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_static_sensitivity_response_catalog (pid, response_no, request_no, step_name, frequency, region_type, set_name, set_scope, instance_name, part_name, variables_json, extra_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
```

### SQL0207 services/model_update/analysis/fem_catalog_service.py:2967

- 类型：`SELECT`
- 特性：`limit`
- 参数数量：`2`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT test_node_id, instance_name, fem_node_label FROM t_mt_py_fem_node_match WHERE pid = %s AND test_node_id = %s LIMIT 2
```

达梦候选:

```sql
SELECT test_node_id, instance_name, fem_node_label FROM t_mt_py_fem_node_match WHERE pid = ? AND test_node_id = ? FETCH FIRST 2 ROWS ONLY
```

### SQL0208 services/model_update/analysis/fem_catalog_service.py:3018

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT response_no, request_no, step_name, frequency, region_type, set_name, set_scope, instance_name, part_name, variables_json, extra_json FROM t_mt_py_fem_static_sensitivity_response_catalog WHERE pid = %s ORDER BY response_no ASC, request_no ASC
```

达梦候选:

```sql
SELECT response_no, request_no, step_name, frequency, region_type, set_name, set_scope, instance_name, part_name, variables_json, extra_json FROM t_mt_py_fem_static_sensitivity_response_catalog WHERE pid = ? ORDER BY response_no ASC, request_no ASC
```

### SQL0209 services/model_update/analysis/fem_catalog_service.py:3071

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM t_mt_py_fem_static_sensitivity_response_catalog WHERE pid = %s
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_static_sensitivity_response_catalog WHERE pid = ?
```

### SQL0210 services/model_update/analysis/fem_correlation_service.py:199

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT measuring_point_name, sensor_type_id FROM t_mt_measuring_point_info WHERE project_id = %s ORDER BY id, measuring_point_name
```

达梦候选:

```sql
SELECT measuring_point_name, sensor_type_id FROM t_mt_measuring_point_info WHERE project_id = ? ORDER BY id, measuring_point_name
```

### SQL0211 services/model_update/analysis/fem_correlation_service.py:257

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`3`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT point, ux, uy, uz, rx, ry, rz, load_factor, extra_json FROM t_mt_py_test_static_result WHERE pid = %s AND load_case_no = %s AND result_no = %s ORDER BY point
```

达梦候选:

```sql
SELECT point, ux, uy, uz, rx, ry, rz, load_factor, extra_json FROM t_mt_py_test_static_result WHERE pid = ? AND load_case_no = ? AND result_no = ? ORDER BY point
```

### SQL0212 services/model_update/analysis/fem_correlation_service.py:270

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT DISTINCT load_case_no FROM t_mt_py_fem_static_result WHERE pid = %s ORDER BY load_case_no
```

达梦候选:

```sql
SELECT DISTINCT load_case_no FROM t_mt_py_fem_static_result WHERE pid = ? ORDER BY load_case_no
```

### SQL0213 services/model_update/analysis/fem_correlation_service.py:315

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT DISTINCT load_case_no, result_no FROM t_mt_py_test_static_result WHERE pid = %s ORDER BY load_case_no, result_no
```

达梦候选:

```sql
SELECT DISTINCT load_case_no, result_no FROM t_mt_py_test_static_result WHERE pid = ? ORDER BY load_case_no, result_no
```

### SQL0214 services/model_update/analysis/fem_correlation_service.py:323

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT DISTINCT load_case_no FROM t_mt_py_fem_static_result WHERE pid = %s ORDER BY load_case_no
```

达梦候选:

```sql
SELECT DISTINCT load_case_no FROM t_mt_py_fem_static_result WHERE pid = ? ORDER BY load_case_no
```

### SQL0215 services/model_update/analysis/fem_correlation_service.py:455

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT measuring_point_name, sensor_type_id FROM t_mt_measuring_point_info WHERE project_id = %s ORDER BY id, measuring_point_name
```

达梦候选:

```sql
SELECT measuring_point_name, sensor_type_id FROM t_mt_measuring_point_info WHERE project_id = ? ORDER BY id, measuring_point_name
```

### SQL0216 services/model_update/analysis/fem_correlation_service.py:543

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`11`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_analysis_error (pid, load_case_no, result_no, point_no, node_no, component_name, point_value, {expr}_node_value, {expr}_relative_error, {expr}_abs_error, sensor_type_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE node_no = VALUES(node_no), {expr} {expr}_node_value = VALUES({expr}_node_value), {expr}_relative_error = VALUES({expr}_relative_error), {expr}_abs_error = VALUES({expr}_abs_error), sensor_type_id = VALUES(sensor_type_id)
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_analysis_error t USING (SELECT ? AS pid, ? AS load_case_no, ? AS result_no, ? AS point_no, ? AS node_no, ? AS component_name, ? AS point_value, ? AS {expr}_node_value, ? AS {expr}_relative_error, ? AS {expr}_abs_error, ? AS sensor_type_id FROM DUAL) s ON (t.pid = s.pid AND t.load_case_no = s.load_case_no AND t.result_no = s.result_no AND t.point_no = s.point_no AND t.component_name = s.component_name) WHEN MATCHED THEN UPDATE SET t.node_no = s.node_no, t.{expr}_relative_error = s.{expr}_relative_error, t.{expr}_abs_error = s.{expr}_abs_error, t.sensor_type_id = s.sensor_type_id WHEN NOT MATCHED THEN INSERT (pid, load_case_no, result_no, point_no, node_no, component_name, point_value, {expr}_node_value, {expr}_relative_error, {expr}_abs_error, sensor_type_id) VALUES (s.pid, s.load_case_no, s.result_no, s.point_no, s.node_no, s.component_name, s.point_value, s.{expr}_node_value, s.{expr}_relative_error, s.{expr}_abs_error, s.sensor_type_id)
```

### SQL0217 services/model_update/analysis/fem_correlation_service.py:574

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT DISTINCT load_case_no, result_no FROM t_mt_py_test_static_result WHERE pid = %s ORDER BY load_case_no, result_no
```

达梦候选:

```sql
SELECT DISTINCT load_case_no, result_no FROM t_mt_py_test_static_result WHERE pid = ? ORDER BY load_case_no, result_no
```

### SQL0218 services/model_update/analysis/fem_correlation_service.py:614

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT test_node_id, instance_name, fem_node_label FROM t_mt_py_fem_node_match WHERE pid = %s ORDER BY test_node_id
```

达梦候选:

```sql
SELECT test_node_id, instance_name, fem_node_label FROM t_mt_py_fem_node_match WHERE pid = ? ORDER BY test_node_id
```

### SQL0219 services/model_update/analysis/fem_correlation_service.py:682

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`2`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT load_case_no, instance_name, part_name, fem_node_label, u1, u2, u3, ur1, ur2, ur3, extra_json FROM t_mt_py_fem_static_result WHERE pid = %s AND load_case_no = %s ORDER BY instance_name, fem_node_label
```

达梦候选:

```sql
SELECT load_case_no, instance_name, part_name, fem_node_label, u1, u2, u3, ur1, ur2, ur3, extra_json FROM t_mt_py_fem_static_result WHERE pid = ? AND load_case_no = ? ORDER BY instance_name, fem_node_label
```

### SQL0220 services/model_update/analysis/fem_correlation_service.py:693

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT test_node_id, instance_name, fem_node_label FROM t_mt_py_fem_node_match WHERE pid = %s ORDER BY test_node_id
```

达梦候选:

```sql
SELECT test_node_id, instance_name, fem_node_label FROM t_mt_py_fem_node_match WHERE pid = ? ORDER BY test_node_id
```

### SQL0221 services/model_update/analysis/fem_correlation_service.py:795

- 类型：`SELECT`
- 特性：`limit`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT test_node_id FROM t_mt_py_fem_node_match WHERE pid = %s LIMIT 1
```

达梦候选:

```sql
SELECT test_node_id FROM t_mt_py_fem_node_match WHERE pid = ? FETCH FIRST 1 ROWS ONLY
```

### SQL0222 services/model_update/analysis/fem_correlation_service.py:838

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`3`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_dac_dsf (pid, dac, dsf) VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE dac = VALUES(dac), dsf = VALUES(dsf)
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_dac_dsf t USING (SELECT ? AS pid, ? AS dac, ? AS dsf FROM DUAL) s ON (t.pid = s.pid) WHEN MATCHED THEN UPDATE SET t.dac = s.dac, t.dsf = s.dsf WHEN NOT MATCHED THEN INSERT (pid, dac, dsf) VALUES (s.pid, s.dac, s.dsf)
```

### SQL0223 services/model_update/analysis/fem_correlation_service.py:872

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT mode_no, point, ux, uy, uz FROM t_mt_py_test_modal_shape_real WHERE pid = %s ORDER BY mode_no, point
```

达梦候选:

```sql
SELECT mode_no, point, ux, uy, uz FROM t_mt_py_test_modal_shape_real WHERE pid = ? ORDER BY mode_no, point
```

### SQL0224 services/model_update/analysis/fem_correlation_service.py:885

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT mode_no, point, re_ux, re_uy, re_uz, im_ux, im_uy, im_uz FROM t_mt_py_test_modal_shape_imag WHERE pid = %s ORDER BY mode_no, point
```

达梦候选:

```sql
SELECT mode_no, point, re_ux, re_uy, re_uz, im_ux, im_uy, im_uz FROM t_mt_py_test_modal_shape_imag WHERE pid = ? ORDER BY mode_no, point
```

### SQL0225 services/model_update/analysis/fem_correlation_service.py:901

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT mode_no, modal_shape FROM t_mt_py_test_modal_shape WHERE pid = %s ORDER BY mode_no
```

达梦候选:

```sql
SELECT mode_no, modal_shape FROM t_mt_py_test_modal_shape WHERE pid = ? ORDER BY mode_no
```

### SQL0226 services/model_update/analysis/fem_correlation_service.py:925

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT mode_no, frequency, instance_name, fem_node_label, u1, u2, u3 FROM t_mt_py_fem_modal_result WHERE pid = %s ORDER BY mode_no, instance_name, fem_node_label
```

达梦候选:

```sql
SELECT mode_no, frequency, instance_name, fem_node_label, u1, u2, u3 FROM t_mt_py_fem_modal_result WHERE pid = ? ORDER BY mode_no, instance_name, fem_node_label
```

### SQL0227 services/model_update/analysis/fem_correlation_service.py:946

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT mode_no, frequency FROM t_mt_py_test_modal_frequency WHERE pid = %s ORDER BY mode_no
```

达梦候选:

```sql
SELECT mode_no, frequency FROM t_mt_py_test_modal_frequency WHERE pid = ? ORDER BY mode_no
```

### SQL0228 services/model_update/analysis/fem_correlation_service.py:956

- 类型：`SELECT`
- 特性：`limit`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT test_modal_data_type FROM t_mt_work_condition_project WHERE project_id = %s LIMIT 1
```

达梦候选:

```sql
SELECT test_modal_data_type FROM t_mt_work_condition_project WHERE project_id = ? FETCH FIRST 1 ROWS ONLY
```

### SQL0229 services/model_update/analysis/fem_correlation_service.py:1053

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT test_node_id, test_dof, instance_name, fem_node_label, fem_dof, direction_x, direction_y, direction_z FROM t_mt_py_fem_dof_match WHERE pid = %s ORDER BY test_node_id, test_dof
```

达梦候选:

```sql
SELECT test_node_id, test_dof, instance_name, fem_node_label, fem_dof, direction_x, direction_y, direction_z FROM t_mt_py_fem_dof_match WHERE pid = ? ORDER BY test_node_id, test_dof
```

### SQL0230 services/model_update/analysis/fem_correlation_service.py:1083

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = %s
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = ?
```

### SQL0231 services/model_update/analysis/fem_correlation_service.py:1084

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM t_mt_py_fem_static_shape_pairs WHERE pid = %s
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_static_shape_pairs WHERE pid = ?
```

### SQL0232 services/model_update/analysis/fem_correlation_service.py:1192

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`13`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_modal_correlation (pid, test_mode_no, fem_mode_no, dof_pair_count, dac, dsf, msf, mac, freq_test, freq_fem, freq_error_ratio, flip, extra_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE dof_pair_count = VALUES(dof_pair_count), dac = VALUES(dac), dsf = VALUES(dsf), msf = VALUES(msf), mac = VALUES(mac), freq_test = VALUES(freq_test), freq_fem = VALUES(freq_fem), freq_error_ratio = VALUES(freq_error_ratio), flip = VALUES(flip), extra_json = VALUES(extra_json), created_at = CURRENT_TIMESTAMP
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_modal_correlation t USING (SELECT ? AS pid, ? AS test_mode_no, ? AS fem_mode_no, ? AS dof_pair_count, ? AS dac, ? AS dsf, ? AS msf, ? AS mac, ? AS freq_test, ? AS freq_fem, ? AS freq_error_ratio, ? AS flip, ? AS extra_json FROM DUAL) s ON (t.pid = s.pid AND t.test_mode_no = s.test_mode_no AND t.fem_mode_no = s.fem_mode_no) WHEN MATCHED THEN UPDATE SET t.dof_pair_count = s.dof_pair_count, t.dac = s.dac, t.dsf = s.dsf, t.msf = s.msf, t.mac = s.mac, t.freq_test = s.freq_test, t.freq_fem = s.freq_fem, t.freq_error_ratio = s.freq_error_ratio, t.flip = s.flip, t.extra_json = s.extra_json, t.created_at = CURRENT_TIMESTAMP WHEN NOT MATCHED THEN INSERT (pid, test_mode_no, fem_mode_no, dof_pair_count, dac, dsf, msf, mac, freq_test, freq_fem, freq_error_ratio, flip, extra_json) VALUES (s.pid, s.test_mode_no, s.fem_mode_no, s.dof_pair_count, s.dac, s.dsf, s.msf, s.mac, s.freq_test, s.freq_fem, s.freq_error_ratio, s.flip, s.extra_json)
```

### SQL0233 services/model_update/analysis/fem_correlation_service.py:1215

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`5`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_static_shape_pairs (pid, fem_res, test_res, DAC, DSF) VALUES (%s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE fem_res = VALUES(fem_res), test_res = VALUES(test_res), DAC = VALUES(DAC), DSF = VALUES(DSF)
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_static_shape_pairs t USING (SELECT ? AS pid, ? AS fem_res, ? AS test_res, ? AS DAC, ? AS DSF FROM DUAL) s ON (t.pid = s.pid) WHEN MATCHED THEN UPDATE SET t.fem_res = s.fem_res, t.test_res = s.test_res, t.DAC = s.DAC, t.DSF = s.DSF WHEN NOT MATCHED THEN INSERT (pid, fem_res, test_res, DAC, DSF) VALUES (s.pid, s.fem_res, s.test_res, s.DAC, s.DSF)
```

### SQL0234 services/model_update/analysis/fem_correlation_service.py:1269

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT test_mode_no, fem_mode_no, mac, msf FROM t_mt_py_fem_modal_correlation WHERE pid = %s ORDER BY fem_mode_no, test_mode_no
```

达梦候选:

```sql
SELECT test_mode_no, fem_mode_no, mac, msf FROM t_mt_py_fem_modal_correlation WHERE pid = ? ORDER BY fem_mode_no, test_mode_no
```

### SQL0235 services/model_update/analysis/fem_correlation_service.py:1403

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT mode_no, MIN(frequency) AS frequency FROM t_mt_py_fem_modal_result WHERE pid = %s AND mode_no IS NOT NULL AND frequency IS NOT NULL GROUP BY mode_no ORDER BY mode_no
```

达梦候选:

```sql
SELECT mode_no, MIN(frequency) AS frequency FROM t_mt_py_fem_modal_result WHERE pid = ? AND mode_no IS NOT NULL AND frequency IS NOT NULL GROUP BY mode_no ORDER BY mode_no
```

### SQL0236 services/model_update/analysis/fem_correlation_service.py:1538

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT test_mode_no, fem_mode_no, dof_pair_count, dac, dsf, msf, mac, freq_test, freq_fem, freq_error_ratio, flip FROM t_mt_py_fem_modal_correlation WHERE pid = %s ORDER BY fem_mode_no, test_mode_no
```

达梦候选:

```sql
SELECT test_mode_no, fem_mode_no, dof_pair_count, dac, dsf, msf, mac, freq_test, freq_fem, freq_error_ratio, flip FROM t_mt_py_fem_modal_correlation WHERE pid = ? ORDER BY fem_mode_no, test_mode_no
```

### SQL0237 services/model_update/analysis/fem_matching_service.py:50

- 类型：`SELECT`
- 特性：`limit`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT source_file_path, cache_file_path, node_count, instance_count, bbox_min, bbox_max, updated_at FROM t_mt_py_fem_node_octree_cache WHERE pid = %s ORDER BY updated_at DESC LIMIT 1
```

达梦候选:

```sql
SELECT source_file_path, cache_file_path, node_count, instance_count, bbox_min, bbox_max, updated_at FROM t_mt_py_fem_node_octree_cache WHERE pid = ? ORDER BY updated_at DESC FETCH FIRST 1 ROWS ONLY
```

### SQL0238 services/model_update/analysis/fem_matching_service.py:84

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function, now_function`
- 参数数量：`7`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_node_octree_cache (pid, source_file_path, cache_file_path, node_count, instance_count, bbox_min, bbox_max, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW()) ON DUPLICATE KEY UPDATE cache_file_path = VALUES(cache_file_path), node_count = VALUES(node_count), instance_count = VALUES(instance_count), bbox_min = VALUES(bbox_min), bbox_max = VALUES(bbox_max), updated_at = NOW()
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_node_octree_cache t USING (SELECT ? AS pid, ? AS source_file_path, ? AS cache_file_path, ? AS node_count, ? AS instance_count, ? AS bbox_min, ? AS bbox_max, CURRENT_TIMESTAMP AS updated_at FROM DUAL) s ON (t.pid = s.pid AND t.source_file_path = s.source_file_path) WHEN MATCHED THEN UPDATE SET t.cache_file_path = s.cache_file_path, t.node_count = s.node_count, t.instance_count = s.instance_count, t.bbox_min = s.bbox_min, t.bbox_max = s.bbox_max, t.updated_at = CURRENT_TIMESTAMP WHEN NOT MATCHED THEN INSERT (pid, source_file_path, cache_file_path, node_count, instance_count, bbox_min, bbox_max, updated_at) VALUES (s.pid, s.source_file_path, s.cache_file_path, s.node_count, s.instance_count, s.bbox_min, s.bbox_max, s.updated_at)
```

### SQL0239 services/model_update/analysis/fem_matching_service.py:217

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM t_mt_py_fem_node_pairs WHERE pid = %s
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_node_pairs WHERE pid = ?
```

### SQL0240 services/model_update/analysis/fem_matching_service.py:218

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM t_mt_py_fem_node_match WHERE pid = %s
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_node_match WHERE pid = ?
```

### SQL0241 services/model_update/analysis/fem_matching_service.py:219

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM t_mt_py_fem_dof_match WHERE pid = %s
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_dof_match WHERE pid = ?
```

### SQL0242 services/model_update/analysis/fem_matching_service.py:220

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM t_mt_py_fem_dynamic_response_catalog WHERE pid = %s
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_dynamic_response_catalog WHERE pid = ?
```

### SQL0243 services/model_update/analysis/fem_matching_service.py:221

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = %s
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = ?
```

### SQL0244 services/model_update/analysis/fem_matching_service.py:239

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`9`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_node_match (pid, test_node_id, instance_name, fem_node_label, distance, x_offset, y_offset, z_offset, transform_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE instance_name = VALUES(instance_name), fem_node_label = VALUES(fem_node_label), distance = VALUES(distance), x_offset = VALUES(x_offset), y_offset = VALUES(y_offset), z_offset = VALUES(z_offset), transform_json = VALUES(transform_json), created_at = CURRENT_TIMESTAMP
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_node_match t USING (SELECT ? AS pid, ? AS test_node_id, ? AS instance_name, ? AS fem_node_label, ? AS distance, ? AS x_offset, ? AS y_offset, ? AS z_offset, ? AS transform_json FROM DUAL) s ON (t.pid = s.pid AND t.test_node_id = s.test_node_id) WHEN MATCHED THEN UPDATE SET t.instance_name = s.instance_name, t.fem_node_label = s.fem_node_label, t.distance = s.distance, t.x_offset = s.x_offset, t.y_offset = s.y_offset, t.z_offset = s.z_offset, t.transform_json = s.transform_json, t.created_at = CURRENT_TIMESTAMP WHEN NOT MATCHED THEN INSERT (pid, test_node_id, instance_name, fem_node_label, distance, x_offset, y_offset, z_offset, transform_json) VALUES (s.pid, s.test_node_id, s.instance_name, s.fem_node_label, s.distance, s.x_offset, s.y_offset, s.z_offset, s.transform_json)
```

### SQL0245 services/model_update/analysis/fem_matching_service.py:262

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`7`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_node_pairs (pid, node, point, distance, x_offset, y_offset, z_offset) VALUES (%s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE distance = VALUES(distance), x_offset = VALUES(x_offset), y_offset = VALUES(y_offset), z_offset = VALUES(z_offset)
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_node_pairs t USING (SELECT ? AS pid, ? AS node, ? AS point, ? AS distance, ? AS x_offset, ? AS y_offset, ? AS z_offset FROM DUAL) s ON (t.pid = s.pid AND t.node = s.node AND t.point = s.point) WHEN MATCHED THEN UPDATE SET t.distance = s.distance, t.x_offset = s.x_offset, t.y_offset = s.y_offset, t.z_offset = s.z_offset WHEN NOT MATCHED THEN INSERT (pid, node, point, distance, x_offset, y_offset, z_offset) VALUES (s.pid, s.node, s.point, s.distance, s.x_offset, s.y_offset, s.z_offset)
```

### SQL0246 services/model_update/analysis/fem_matching_service.py:322

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT id, test_node_id, instance_name, fem_node_label FROM t_mt_py_fem_node_match WHERE pid = %s ORDER BY id
```

达梦候选:

```sql
SELECT id, test_node_id, instance_name, fem_node_label FROM t_mt_py_fem_node_match WHERE pid = ? ORDER BY id
```

### SQL0247 services/model_update/analysis/fem_matching_service.py:337

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT CAST(nid AS CHAR) AS test_node_id, x AS x_position, y AS y_position, z AS z_position FROM t_mt_py_test_node WHERE pid = %s ORDER BY nid
```

达梦候选:

```sql
SELECT CAST(nid AS CHAR) AS test_node_id, x AS x_position, y AS y_position, z AS z_position FROM t_mt_py_test_node WHERE pid = ? ORDER BY nid
```

### SQL0248 services/model_update/analysis/fem_matching_service.py:350

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT id, measuring_point_name, x_position, y_position, z_position FROM t_mt_measuring_point_info WHERE project_id = %s ORDER BY id
```

达梦候选:

```sql
SELECT id, measuring_point_name, x_position, y_position, z_position FROM t_mt_measuring_point_info WHERE project_id = ? ORDER BY id
```

### SQL0249 services/model_update/analysis/fem_matching_service.py:401

- 类型：`SELECT`
- 特性：`limit`
- 参数数量：`2`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT matrix4_json FROM t_mt_py_fem_transform_operation WHERE pid = %s AND transform_type = %s LIMIT 1
```

达梦候选:

```sql
SELECT matrix4_json FROM t_mt_py_fem_transform_operation WHERE pid = ? AND transform_type = ? FETCH FIRST 1 ROWS ONLY
```

### SQL0250 services/model_update/analysis/fem_matching_service.py:429

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`3`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_transform_operation (pid, transform_type, matrix4_json) VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE matrix4_json = VALUES(matrix4_json), updated_at = CURRENT_TIMESTAMP
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_transform_operation t USING (SELECT ? AS pid, ? AS transform_type, ? AS matrix4_json FROM DUAL) s ON (t.pid = s.pid AND t.transform_type = s.transform_type) WHEN MATCHED THEN UPDATE SET t.matrix4_json = s.matrix4_json, t.updated_at = CURRENT_TIMESTAMP WHEN NOT MATCHED THEN INSERT (pid, transform_type, matrix4_json) VALUES (s.pid, s.transform_type, s.matrix4_json)
```

### SQL0251 services/model_update/analysis/fem_matching_service.py:440

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT nid, fid, x, y, z, origin_x, origin_y, origin_z FROM t_mt_py_test_node WHERE pid = %s ORDER BY nid, fid
```

达梦候选:

```sql
SELECT nid, fid, x, y, z, origin_x, origin_y, origin_z FROM t_mt_py_test_node WHERE pid = ? ORDER BY nid, fid
```

### SQL0252 services/model_update/analysis/fem_matching_service.py:462

- 类型：`UPDATE`
- 特性：`portable_or_unknown`
- 参数数量：`6`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
UPDATE t_mt_py_test_node SET x = %s, y = %s, z = %s WHERE pid = %s AND nid = %s AND fid = %s
```

达梦候选:

```sql
UPDATE t_mt_py_test_node SET x = ?, y = ?, z = ? WHERE pid = ? AND nid = ? AND fid = ?
```

### SQL0253 services/model_update/analysis/fem_matching_service.py:474

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT id, x_position, y_position, z_position, x_position_ori, y_position_ori, z_position_ori FROM t_mt_measuring_point_info WHERE project_id = %s ORDER BY id
```

达梦候选:

```sql
SELECT id, x_position, y_position, z_position, x_position_ori, y_position_ori, z_position_ori FROM t_mt_measuring_point_info WHERE project_id = ? ORDER BY id
```

### SQL0254 services/model_update/analysis/fem_matching_service.py:495

- 类型：`UPDATE`
- 特性：`portable_or_unknown`
- 参数数量：`5`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
UPDATE t_mt_measuring_point_info SET x_position = %s, y_position = %s, z_position = %s WHERE project_id = %s AND id = %s
```

达梦候选:

```sql
UPDATE t_mt_measuring_point_info SET x_position = ?, y_position = ?, z_position = ? WHERE project_id = ? AND id = ?
```

### SQL0255 services/model_update/analysis/fem_matching_service.py:525

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT transform_type, matrix4_json FROM t_mt_py_fem_transform_operation WHERE pid = %s
```

达梦候选:

```sql
SELECT transform_type, matrix4_json FROM t_mt_py_fem_transform_operation WHERE pid = ?
```

### SQL0256 services/model_update/analysis/fem_matching_service.py:633

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT test_node_id, instance_name, fem_node_label, transform_json FROM t_mt_py_fem_node_match WHERE pid = %s ORDER BY test_node_id
```

达梦候选:

```sql
SELECT test_node_id, instance_name, fem_node_label, transform_json FROM t_mt_py_fem_node_match WHERE pid = ? ORDER BY test_node_id
```

### SQL0257 services/model_update/analysis/fem_matching_service.py:660

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM t_mt_py_fem_dof_match WHERE pid = %s
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_dof_match WHERE pid = ?
```

### SQL0258 services/model_update/analysis/fem_matching_service.py:661

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM t_mt_py_fem_dynamic_response_catalog WHERE pid = %s
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_dynamic_response_catalog WHERE pid = ?
```

### SQL0259 services/model_update/analysis/fem_matching_service.py:662

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = %s
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = ?
```

### SQL0260 services/model_update/analysis/fem_matching_service.py:720

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`12`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_dof_match (pid, test_node_id, test_dof, instance_name, part_name, fem_node_label, fem_dof, direction_x, direction_y, direction_z, match_score, transform_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE instance_name = VALUES(instance_name), part_name = VALUES(part_name), fem_node_label = VALUES(fem_node_label), fem_dof = VALUES(fem_dof), direction_x = VALUES(direction_x), direction_y = VALUES(direction_y), direction_z = VALUES(direction_z), match_score = VALUES(match_score), transform_json = VALUES(transform_json), created_at = CURRENT_TIMESTAMP
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_dof_match t USING (SELECT ? AS pid, ? AS test_node_id, ? AS test_dof, ? AS instance_name, ? AS part_name, ? AS fem_node_label, ? AS fem_dof, ? AS direction_x, ? AS direction_y, ? AS direction_z, ? AS match_score, ? AS transform_json FROM DUAL) s ON (t.pid = s.pid AND t.test_node_id = s.test_node_id AND t.test_dof = s.test_dof) WHEN MATCHED THEN UPDATE SET t.instance_name = s.instance_name, t.part_name = s.part_name, t.fem_node_label = s.fem_node_label, t.fem_dof = s.fem_dof, t.direction_x = s.direction_x, t.direction_y = s.direction_y, t.direction_z = s.direction_z, t.match_score = s.match_score, t.transform_json = s.transform_json, t.created_at = CURRENT_TIMESTAMP WHEN NOT MATCHED THEN INSERT (pid, test_node_id, test_dof, instance_name, part_name, fem_node_label, fem_dof, direction_x, direction_y, direction_z, match_score, transform_json) VALUES (s.pid, s.test_node_id, s.test_dof, s.instance_name, s.part_name, s.fem_node_label, s.fem_dof, s.direction_x, s.direction_y, s.direction_z, s.match_score, s.transform_json)
```

### SQL0261 services/model_update/analysis/fem_matching_service.py:754

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT id, measuring_point_name, sensor_type_id FROM t_mt_measuring_point_info WHERE project_id = %s ORDER BY id, measuring_point_name
```

达梦候选:

```sql
SELECT id, measuring_point_name, sensor_type_id FROM t_mt_measuring_point_info WHERE project_id = ? ORDER BY id, measuring_point_name
```

### SQL0262 services/model_update/analysis/fem_matching_service.py:788

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT id, measure_point_id, direction, data_operate FROM t_mt_channel_info WHERE project_id = %s ORDER BY measure_point_id, id
```

达梦候选:

```sql
SELECT id, measure_point_id, direction, data_operate FROM t_mt_channel_info WHERE project_id = ? ORDER BY measure_point_id, id
```

### SQL0263 services/model_update/analysis/fem_matching_service.py:834

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`12`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_dof_match (pid, test_node_id, test_dof, instance_name, part_name, fem_node_label, fem_dof, direction_x, direction_y, direction_z, match_score, transform_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE instance_name = VALUES(instance_name), part_name = VALUES(part_name), fem_node_label = VALUES(fem_node_label), fem_dof = VALUES(fem_dof), direction_x = VALUES(direction_x), direction_y = VALUES(direction_y), direction_z = VALUES(direction_z), match_score = VALUES(match_score), transform_json = VALUES(transform_json), created_at = CURRENT_TIMESTAMP
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_dof_match t USING (SELECT ? AS pid, ? AS test_node_id, ? AS test_dof, ? AS instance_name, ? AS part_name, ? AS fem_node_label, ? AS fem_dof, ? AS direction_x, ? AS direction_y, ? AS direction_z, ? AS match_score, ? AS transform_json FROM DUAL) s ON (t.pid = s.pid AND t.test_node_id = s.test_node_id AND t.test_dof = s.test_dof) WHEN MATCHED THEN UPDATE SET t.instance_name = s.instance_name, t.part_name = s.part_name, t.fem_node_label = s.fem_node_label, t.fem_dof = s.fem_dof, t.direction_x = s.direction_x, t.direction_y = s.direction_y, t.direction_z = s.direction_z, t.match_score = s.match_score, t.transform_json = s.transform_json, t.created_at = CURRENT_TIMESTAMP WHEN NOT MATCHED THEN INSERT (pid, test_node_id, test_dof, instance_name, part_name, fem_node_label, fem_dof, direction_x, direction_y, direction_z, match_score, transform_json) VALUES (s.pid, s.test_node_id, s.test_dof, s.instance_name, s.part_name, s.fem_node_label, s.fem_dof, s.direction_x, s.direction_y, s.direction_z, s.match_score, s.transform_json)
```

### SQL0264 services/model_update/analysis/fem_matching_service.py:883

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT test_node_id, test_dof, instance_name, part_name, fem_node_label, fem_dof, direction_x, direction_y, direction_z, match_score, transform_json, created_at FROM t_mt_py_fem_dof_match WHERE pid = %s ORDER BY test_node_id, test_dof
```

达梦候选:

```sql
SELECT test_node_id, test_dof, instance_name, part_name, fem_node_label, fem_dof, direction_x, direction_y, direction_z, match_score, transform_json, created_at FROM t_mt_py_fem_dof_match WHERE pid = ? ORDER BY test_node_id, test_dof
```

### SQL0265 services/model_update/analysis/fem_response_service.py:39

- 类型：`SELECT`
- 特性：`limit`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT project_config FROM t_mt_work_condition_project WHERE project_id = %s LIMIT 1
```

达梦候选:

```sql
SELECT project_config FROM t_mt_work_condition_project WHERE project_id = ? FETCH FIRST 1 ROWS ONLY
```

### SQL0266 services/model_update/analysis/fem_response_service.py:88

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM t_mt_py_fem_dynamic_response_catalog WHERE pid = %s
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_dynamic_response_catalog WHERE pid = ?
```

### SQL0267 services/model_update/analysis/fem_response_service.py:96

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT mode_no, frequency FROM t_mt_py_test_modal_frequency WHERE pid = %s ORDER BY mode_no
```

达梦候选:

```sql
SELECT mode_no, frequency FROM t_mt_py_test_modal_frequency WHERE pid = ? ORDER BY mode_no
```

### SQL0268 services/model_update/analysis/fem_response_service.py:125

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT test_node_id, test_dof, instance_name, part_name, fem_node_label, fem_dof, direction_x, direction_y, direction_z, match_score FROM t_mt_py_fem_dof_match WHERE pid = %s ORDER BY test_node_id, test_dof
```

达梦候选:

```sql
SELECT test_node_id, test_dof, instance_name, part_name, fem_node_label, fem_dof, direction_x, direction_y, direction_z, match_score FROM t_mt_py_fem_dof_match WHERE pid = ? ORDER BY test_node_id, test_dof
```

### SQL0269 services/model_update/analysis/fem_response_service.py:187

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`16`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_dynamic_response_catalog (pid, response_code, response_name, response_type, entity_type, test_mode_no, test_node_id, instance_name, part_name, fem_node_label, component, unit, scatter, seq_no, source_table, extra_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE response_name = VALUES(response_name), response_type = VALUES(response_type), entity_type = VALUES(entity_type), test_mode_no = VALUES(test_mode_no), test_node_id = VALUES(test_node_id), instance_name = VALUES(instance_name), part_name = VALUES(part_name), fem_node_label = VALUES(fem_node_label), component = VALUES(component), unit = VALUES(unit), scatter = VALUES(scatter), seq_no = VALUES(seq_no), source_table = VALUES(source_table), extra_json = VALUES(extra_json), created_at = CURRENT_TIMESTAMP
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_dynamic_response_catalog t USING (SELECT ? AS pid, ? AS response_code, ? AS response_name, ? AS response_type, ? AS entity_type, ? AS test_mode_no, ? AS test_node_id, ? AS instance_name, ? AS part_name, ? AS fem_node_label, ? AS component, ? AS unit, ? AS scatter, ? AS seq_no, ? AS source_table, ? AS extra_json FROM DUAL) s ON (t.pid = s.pid AND t.response_code = s.response_code) WHEN MATCHED THEN UPDATE SET t.response_name = s.response_name, t.response_type = s.response_type, t.entity_type = s.entity_type, t.test_mode_no = s.test_mode_no, t.test_node_id = s.test_node_id, t.instance_name = s.instance_name, t.part_name = s.part_name, t.fem_node_label = s.fem_node_label, t.component = s.component, t.unit = s.unit, t.scatter = s.scatter, t.seq_no = s.seq_no, t.source_table = s.source_table, t.extra_json = s.extra_json, t.created_at = CURRENT_TIMESTAMP WHEN NOT MATCHED THEN INSERT (pid, response_code, response_name, response_type, entity_type, test_mode_no, test_node_id, instance_name, part_name, fem_node_label, component, unit, scatter, seq_no, source_table, extra_json) VALUES (s.pid, s.response_code, s.response_name, s.response_type, s.entity_type, s.test_mode_no, s.test_node_id, s.instance_name, s.part_name, s.fem_node_label, s.component, s.unit, s.scatter, s.seq_no, s.source_table, s.extra_json)
```

### SQL0270 services/model_update/analysis/fem_response_service.py:227

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT response_code, response_name, response_type, entity_type, test_mode_no, test_node_id, instance_name, part_name, fem_node_label, component, unit, scatter, seq_no, enabled, selection_source, solver_scope, source_table, extra_json, created_at, updated_at FROM t_mt_py_fem_dynamic_response_catalog WHERE pid = %s ORDER BY seq_no, response_code
```

达梦候选:

```sql
SELECT response_code, response_name, response_type, entity_type, test_mode_no, test_node_id, instance_name, part_name, fem_node_label, component, unit, scatter, seq_no, enabled, selection_source, solver_scope, source_table, extra_json, created_at, updated_at FROM t_mt_py_fem_dynamic_response_catalog WHERE pid = ? ORDER BY seq_no, response_code
```

### SQL0271 services/model_update/analysis/fem_response_service.py:259

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT parameter_group_name, parameter_name, quantity_code, selection_mode, set_name, set_type, set_scope, instance_name, part_name, element_label, current_value, lower, upper, prob_id, scatter, description, usage_scope, extra_json, created_at FROM t_mt_py_fem_selected_parameter WHERE pid = %s
```

达梦候选:

```sql
SELECT parameter_group_name, parameter_name, quantity_code, selection_mode, set_name, set_type, set_scope, instance_name, part_name, element_label, current_value, lower, upper, prob_id, scatter, description, usage_scope, extra_json, created_at FROM t_mt_py_fem_selected_parameter WHERE pid = ?
```

### SQL0272 services/model_update/analysis/fem_response_service.py:325

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT parameter_name FROM t_mt_py_fem_selected_parameter WHERE pid = %s AND parameter_name IN ({expr})
```

达梦候选:

```sql
SELECT parameter_name FROM t_mt_py_fem_selected_parameter WHERE pid = ? AND parameter_name IN ({expr})
```

### SQL0273 services/model_update/analysis/fem_response_service.py:342

- 类型：`UPDATE`
- 特性：`portable_or_unknown`
- 参数数量：`3`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
UPDATE t_mt_py_fem_selected_parameter SET usage_scope = %s WHERE pid = %s AND parameter_name = %s
```

达梦候选:

```sql
UPDATE t_mt_py_fem_selected_parameter SET usage_scope = ? WHERE pid = ? AND parameter_name = ?
```

### SQL0274 services/model_update/analysis/fem_response_service.py:456

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM t_mt_py_fem_dynamic_response_catalog WHERE pid = %s AND response_type IN ({expr})
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_dynamic_response_catalog WHERE pid = ? AND response_type IN ({expr})
```

### SQL0275 services/model_update/analysis/fem_response_service.py:535

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT mode_no, frequency FROM t_mt_py_fem_modal_result WHERE pid = %s GROUP BY mode_no, frequency ORDER BY mode_no
```

达梦候选:

```sql
SELECT mode_no, frequency FROM t_mt_py_fem_modal_result WHERE pid = ? GROUP BY mode_no, frequency ORDER BY mode_no
```

### SQL0276 services/model_update/analysis/fem_response_service.py:649

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`19`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_dynamic_response_catalog (pid, response_code, response_name, response_type, entity_type, test_mode_no, test_node_id, instance_name, part_name, fem_node_label, component, unit, scatter, seq_no, enabled, selection_source, solver_scope, source_table, extra_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE response_name = VALUES(response_name), response_type = VALUES(response_type), entity_type = VALUES(entity_type), test_mode_no = VALUES(test_mode_no), component = VALUES(component), unit = VALUES(unit), scatter = VALUES(scatter), seq_no = VALUES(seq_no), enabled = VALUES(enabled), selection_source = VALUES(selection_source), solver_scope = VALUES(solver_scope), source_table = VALUES(source_table), extra_json = VALUES(extra_json), updated_at = CURRENT_TIMESTAMP
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_dynamic_response_catalog t USING (SELECT ? AS pid, ? AS response_code, ? AS response_name, ? AS response_type, ? AS entity_type, ? AS test_mode_no, ? AS test_node_id, ? AS instance_name, ? AS part_name, ? AS fem_node_label, ? AS component, ? AS unit, ? AS scatter, ? AS seq_no, ? AS enabled, ? AS selection_source, ? AS solver_scope, ? AS source_table, ? AS extra_json FROM DUAL) s ON (t.pid = s.pid AND t.response_code = s.response_code) WHEN MATCHED THEN UPDATE SET t.response_name = s.response_name, t.response_type = s.response_type, t.entity_type = s.entity_type, t.test_mode_no = s.test_mode_no, t.component = s.component, t.unit = s.unit, t.scatter = s.scatter, t.seq_no = s.seq_no, t.enabled = s.enabled, t.selection_source = s.selection_source, t.solver_scope = s.solver_scope, t.source_table = s.source_table, t.extra_json = s.extra_json, t.updated_at = CURRENT_TIMESTAMP WHEN NOT MATCHED THEN INSERT (pid, response_code, response_name, response_type, entity_type, test_mode_no, test_node_id, instance_name, part_name, fem_node_label, component, unit, scatter, seq_no, enabled, selection_source, solver_scope, source_table, extra_json) VALUES (s.pid, s.response_code, s.response_name, s.response_type, s.entity_type, s.test_mode_no, s.test_node_id, s.instance_name, s.part_name, s.fem_node_label, s.component, s.unit, s.scatter, s.seq_no, s.enabled, s.selection_source, s.solver_scope, s.source_table, s.extra_json)
```

### SQL0277 services/model_update/analysis/fem_response_service.py:709

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT mode_no, frequency FROM t_mt_py_fem_modal_result WHERE pid = %s GROUP BY mode_no, frequency ORDER BY mode_no
```

达梦候选:

```sql
SELECT mode_no, frequency FROM t_mt_py_fem_modal_result WHERE pid = ? GROUP BY mode_no, frequency ORDER BY mode_no
```

### SQL0278 services/model_update/analysis/fem_response_service.py:762

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT COALESCE(MAX(seq_no), 0) AS max_seq_no FROM t_mt_py_fem_dynamic_response_catalog WHERE pid = %s
```

达梦候选:

```sql
SELECT COALESCE(MAX(seq_no), 0) AS max_seq_no FROM t_mt_py_fem_dynamic_response_catalog WHERE pid = ?
```

### SQL0279 services/model_update/analysis/fem_response_service.py:928

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT test_mode_no, fem_mode_no, mac, freq_test, freq_fem, freq_error_ratio FROM t_mt_py_fem_modal_correlation WHERE pid = %s AND ( {expr} ) ORDER BY fem_mode_no, test_mode_no
```

达梦候选:

```sql
SELECT test_mode_no, fem_mode_no, mac, freq_test, freq_fem, freq_error_ratio FROM t_mt_py_fem_modal_correlation WHERE pid = ? AND ( {expr} ) ORDER BY fem_mode_no, test_mode_no
```

### SQL0280 services/model_update/analysis/fem_result_service.py:177

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM t_mt_py_fem_modal_result WHERE pid = %s
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_modal_result WHERE pid = ?
```

### SQL0281 services/model_update/analysis/fem_result_service.py:178

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = %s
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = ?
```

### SQL0282 services/model_update/analysis/fem_result_service.py:227

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`10`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_modal_result (pid, mode_no, frequency, instance_name, part_name, fem_node_label, u1, u2, u3, extra_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE frequency = VALUES(frequency), part_name = VALUES(part_name), u1 = VALUES(u1), u2 = VALUES(u2), u3 = VALUES(u3), extra_json = VALUES(extra_json), created_at = CURRENT_TIMESTAMP
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_modal_result t USING (SELECT ? AS pid, ? AS mode_no, ? AS frequency, ? AS instance_name, ? AS part_name, ? AS fem_node_label, ? AS u1, ? AS u2, ? AS u3, ? AS extra_json FROM DUAL) s ON (t.pid = s.pid AND t.mode_no = s.mode_no AND t.instance_name = s.instance_name AND t.fem_node_label = s.fem_node_label) WHEN MATCHED THEN UPDATE SET t.frequency = s.frequency, t.part_name = s.part_name, t.u1 = s.u1, t.u2 = s.u2, t.u3 = s.u3, t.extra_json = s.extra_json, t.created_at = CURRENT_TIMESTAMP WHEN NOT MATCHED THEN INSERT (pid, mode_no, frequency, instance_name, part_name, fem_node_label, u1, u2, u3, extra_json) VALUES (s.pid, s.mode_no, s.frequency, s.instance_name, s.part_name, s.fem_node_label, s.u1, s.u2, s.u3, s.extra_json)
```

### SQL0283 services/model_update/analysis/fem_result_service.py:274

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT mode_no, frequency, instance_name, part_name, fem_node_label, u1, u2, u3, extra_json, created_at FROM t_mt_py_fem_modal_result WHERE pid = %s ORDER BY mode_no, instance_name, fem_node_label
```

达梦候选:

```sql
SELECT mode_no, frequency, instance_name, part_name, fem_node_label, u1, u2, u3, extra_json, created_at FROM t_mt_py_fem_modal_result WHERE pid = ? ORDER BY mode_no, instance_name, fem_node_label
```

### SQL0284 services/model_update/analysis/fem_result_service.py:299

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM t_mt_py_fem_modal_result WHERE pid = %s
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_modal_result WHERE pid = ?
```

### SQL0285 services/model_update/analysis/fem_result_service.py:300

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = %s
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = ?
```

### SQL0286 services/model_update/analysis/fem_result_service.py:370

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT mode_no, frequency, instance_name, part_name, fem_node_label, u1, u2, u3, extra_json, created_at FROM t_mt_py_fem_modal_result WHERE pid = %s ORDER BY mode_no, instance_name, fem_node_label
```

达梦候选:

```sql
SELECT mode_no, frequency, instance_name, part_name, fem_node_label, u1, u2, u3, extra_json, created_at FROM t_mt_py_fem_modal_result WHERE pid = ? ORDER BY mode_no, instance_name, fem_node_label
```

### SQL0287 services/model_update/analysis/fem_result_service.py:870

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`12`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_static_result (pid, load_case_no, instance_name, part_name, fem_node_label, u1, u2, u3, ur1, ur2, ur3, extra_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE part_name = VALUES(part_name), u1 = VALUES(u1), u2 = VALUES(u2), u3 = VALUES(u3), ur1 = VALUES(ur1), ur2 = VALUES(ur2), ur3 = VALUES(ur3), extra_json = VALUES(extra_json), created_at = CURRENT_TIMESTAMP
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_static_result t USING (SELECT ? AS pid, ? AS load_case_no, ? AS instance_name, ? AS part_name, ? AS fem_node_label, ? AS u1, ? AS u2, ? AS u3, ? AS ur1, ? AS ur2, ? AS ur3, ? AS extra_json FROM DUAL) s ON (t.pid = s.pid AND t.load_case_no = s.load_case_no AND t.instance_name = s.instance_name AND t.fem_node_label = s.fem_node_label) WHEN MATCHED THEN UPDATE SET t.part_name = s.part_name, t.u1 = s.u1, t.u2 = s.u2, t.u3 = s.u3, t.ur1 = s.ur1, t.ur2 = s.ur2, t.ur3 = s.ur3, t.extra_json = s.extra_json, t.created_at = CURRENT_TIMESTAMP WHEN NOT MATCHED THEN INSERT (pid, load_case_no, instance_name, part_name, fem_node_label, u1, u2, u3, ur1, ur2, ur3, extra_json) VALUES (s.pid, s.load_case_no, s.instance_name, s.part_name, s.fem_node_label, s.u1, s.u2, s.u3, s.ur1, s.ur2, s.ur3, s.extra_json)
```

### SQL0288 services/model_update/analysis/fem_result_service.py:921

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM t_mt_py_fem_static_result WHERE pid = %s
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_static_result WHERE pid = ?
```

### SQL0289 services/model_update/analysis/fem_result_service.py:1015

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`2`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM t_mt_py_fem_static_result WHERE pid = %s AND load_case_no = %s
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_static_result WHERE pid = ? AND load_case_no = ?
```

### SQL0290 services/model_update/analysis/fem_result_service.py:1091

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT load_case_no, instance_name, part_name, fem_node_label, u1, u2, u3, ur1, ur2, ur3, extra_json, created_at FROM t_mt_py_fem_static_result WHERE pid = %s ORDER BY load_case_no, instance_name, fem_node_label
```

达梦候选:

```sql
SELECT load_case_no, instance_name, part_name, fem_node_label, u1, u2, u3, ur1, ur2, ur3, extra_json, created_at FROM t_mt_py_fem_static_result WHERE pid = ? ORDER BY load_case_no, instance_name, fem_node_label
```

### SQL0291 services/model_update/analysis/fem_result_service.py:1099

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`2`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT load_case_no, instance_name, part_name, fem_node_label, u1, u2, u3, ur1, ur2, ur3, extra_json, created_at FROM t_mt_py_fem_static_result WHERE pid = %s AND load_case_no = %s ORDER BY load_case_no, instance_name, fem_node_label
```

达梦候选:

```sql
SELECT load_case_no, instance_name, part_name, fem_node_label, u1, u2, u3, ur1, ur2, ur3, extra_json, created_at FROM t_mt_py_fem_static_result WHERE pid = ? AND load_case_no = ? ORDER BY load_case_no, instance_name, fem_node_label
```

### SQL0292 services/model_update/analysis/modal_mac_service.py:312

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT test_node_id, instance_name, fem_node_label FROM t_mt_py_fem_node_match WHERE pid = %s ORDER BY test_node_id, instance_name, fem_node_label
```

达梦候选:

```sql
SELECT test_node_id, instance_name, fem_node_label FROM t_mt_py_fem_node_match WHERE pid = ? ORDER BY test_node_id, instance_name, fem_node_label
```

### SQL0293 services/model_update/analysis/model_update_meta_service.py:74

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`6`
- 验证状态：`skipped`
- 说明：UPSERT 未找到可用于 MERGE ON 的主键/唯一键，需要先确认冲突键。；参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_manual_parameter (pid, parameter_name, parameter_type, scatter, upper_bound, lower_bound) VALUES (%s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE parameter_type = VALUES(parameter_type), scatter = VALUES(scatter), upper_bound = VALUES(upper_bound), lower_bound = VALUES(lower_bound), updated_at = CURRENT_TIMESTAMP
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_manual_parameter (pid, parameter_name, parameter_type, scatter, upper_bound, lower_bound) VALUES (?, ?, ?, ?, ?, ?) ON DUPLICATE KEY UPDATE parameter_type = VALUES(parameter_type), scatter = VALUES(scatter), upper_bound = VALUES(upper_bound), lower_bound = VALUES(lower_bound), updated_at = CURRENT_TIMESTAMP
```

### SQL0294 services/model_update/analysis/nastran_sol200_service.py:172

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT COALESCE(MAX(parameter_no), 0) AS max_no FROM t_mt_py_fem_sol200_parameter_config WHERE pid = %s
```

达梦候选:

```sql
SELECT COALESCE(MAX(parameter_no), 0) AS max_no FROM t_mt_py_fem_sol200_parameter_config WHERE pid = ?
```

### SQL0295 services/model_update/analysis/nastran_sol200_service.py:181

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`11`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_sol200_parameter_config (pid, parameter_no, parameter_name, parameter_type, property_id, material_id, element_id, initial_value, lower_bound, upper_bound, extra_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_sol200_parameter_config (pid, parameter_no, parameter_name, parameter_type, property_id, material_id, element_id, initial_value, lower_bound, upper_bound, extra_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
```

### SQL0296 services/model_update/analysis/nastran_sol200_service.py:240

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT parameter_no, parameter_name, parameter_type, property_id, material_id, element_id, initial_value, lower_bound, upper_bound, extra_json FROM t_mt_py_fem_sol200_parameter_config WHERE pid = %s ORDER BY parameter_no ASC
```

达梦候选:

```sql
SELECT parameter_no, parameter_name, parameter_type, property_id, material_id, element_id, initial_value, lower_bound, upper_bound, extra_json FROM t_mt_py_fem_sol200_parameter_config WHERE pid = ? ORDER BY parameter_no ASC
```

### SQL0297 services/model_update/analysis/nastran_sol200_service.py:285

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM t_mt_py_fem_sol200_parameter_config WHERE pid = %s
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_sol200_parameter_config WHERE pid = ?
```

### SQL0298 services/model_update/analysis/nastran_sol200_service.py:355

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT COALESCE(MAX(response_no), 0) AS max_no FROM t_mt_py_fem_sol200_response_config WHERE pid = %s
```

达梦候选:

```sql
SELECT COALESCE(MAX(response_no), 0) AS max_no FROM t_mt_py_fem_sol200_response_config WHERE pid = ?
```

### SQL0299 services/model_update/analysis/nastran_sol200_service.py:364

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`6`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_sol200_response_config (pid, response_no, response_name, response_type, mode_number, extra_json) VALUES (%s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_sol200_response_config (pid, response_no, response_name, response_type, mode_number, extra_json) VALUES (?, ?, ?, ?, ?, ?)
```

### SQL0300 services/model_update/analysis/nastran_sol200_service.py:413

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT response_no, response_name, response_type, mode_number, extra_json FROM t_mt_py_fem_sol200_response_config WHERE pid = %s ORDER BY response_no ASC
```

达梦候选:

```sql
SELECT response_no, response_name, response_type, mode_number, extra_json FROM t_mt_py_fem_sol200_response_config WHERE pid = ? ORDER BY response_no ASC
```

### SQL0301 services/model_update/analysis/nastran_sol200_service.py:454

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM t_mt_py_fem_sol200_response_config WHERE pid = %s
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_sol200_response_config WHERE pid = ?
```

### SQL0302 services/model_update/analysis/pbs_service.py:325

- 类型：`SELECT`
- 特性：`limit`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT project_config FROM t_mt_work_condition_project WHERE project_id = %s LIMIT 1
```

达梦候选:

```sql
SELECT project_config FROM t_mt_work_condition_project WHERE project_id = ? FETCH FIRST 1 ROWS ONLY
```

### SQL0303 services/model_update/analysis/project_config_service.py:79

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT pid, test_model_x, test_model_y, test_model_z, fem_model_x, fem_model_y, fem_model_z, coefficients_json, extra_json FROM t_mt_py_project_config WHERE pid = %s
```

达梦候选:

```sql
SELECT pid, test_model_x, test_model_y, test_model_z, fem_model_x, fem_model_y, fem_model_z, coefficients_json, extra_json FROM t_mt_py_project_config WHERE pid = ?
```

### SQL0304 services/model_update/analysis/project_config_service.py:119

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`9`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_project_config (pid, test_model_x, test_model_y, test_model_z, fem_model_x, fem_model_y, fem_model_z, coefficients_json, extra_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE test_model_x = VALUES(test_model_x), test_model_y = VALUES(test_model_y), test_model_z = VALUES(test_model_z), fem_model_x = VALUES(fem_model_x), fem_model_y = VALUES(fem_model_y), fem_model_z = VALUES(fem_model_z), coefficients_json = VALUES(coefficients_json), extra_json = VALUES(extra_json), updated_at = CURRENT_TIMESTAMP
```

达梦候选:

```sql
MERGE INTO t_mt_py_project_config t USING (SELECT ? AS pid, ? AS test_model_x, ? AS test_model_y, ? AS test_model_z, ? AS fem_model_x, ? AS fem_model_y, ? AS fem_model_z, ? AS coefficients_json, ? AS extra_json FROM DUAL) s ON (t.pid = s.pid) WHEN MATCHED THEN UPDATE SET t.test_model_x = s.test_model_x, t.test_model_y = s.test_model_y, t.test_model_z = s.test_model_z, t.fem_model_x = s.fem_model_x, t.fem_model_y = s.fem_model_y, t.fem_model_z = s.fem_model_z, t.coefficients_json = s.coefficients_json, t.extra_json = s.extra_json, t.updated_at = CURRENT_TIMESTAMP WHEN NOT MATCHED THEN INSERT (pid, test_model_x, test_model_y, test_model_z, fem_model_x, fem_model_y, fem_model_z, coefficients_json, extra_json) VALUES (s.pid, s.test_model_x, s.test_model_y, s.test_model_z, s.fem_model_x, s.fem_model_y, s.fem_model_z, s.coefficients_json, s.extra_json)
```

### SQL0305 services/model_update/analysis/project_config_service.py:227

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT x_position, y_position, z_position FROM t_mt_measuring_point_info WHERE project_id = %s ORDER BY id, measuring_point_name
```

达梦候选:

```sql
SELECT x_position, y_position, z_position FROM t_mt_measuring_point_info WHERE project_id = ? ORDER BY id, measuring_point_name
```

### SQL0306 services/model_update/analysis/project_source_service.py:53

- 类型：`SELECT`
- 特性：`limit`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT source_file_path FROM t_mt_py_fem_node_octree_cache WHERE pid = %s ORDER BY updated_at DESC, id DESC LIMIT 1
```

达梦候选:

```sql
SELECT source_file_path FROM t_mt_py_fem_node_octree_cache WHERE pid = ? ORDER BY updated_at DESC, id DESC FETCH FIRST 1 ROWS ONLY
```

### SQL0307 services/model_update/analysis/project_status_service.py:14

- 类型：`SHOW`
- 特性：`show_columns`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：只自动执行只读 SELECT

MySQL:

```sql
SHOW COLUMNS FROM t_mt_work_condition_project
```

达梦候选:

```sql
SELECT COLUMN_NAME, DATA_TYPE, NULLABLE, DATA_DEFAULT FROM ALL_TAB_COLUMNS WHERE UPPER(TABLE_NAME)=UPPER('t_mt_work_condition_project') ORDER BY COLUMN_ID
```

### SQL0308 services/model_update/analysis/project_status_service.py:47

- 类型：`UPDATE`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
UPDATE t_mt_work_condition_project SET {expr} WHERE project_id = %s
```

达梦候选:

```sql
UPDATE t_mt_work_condition_project SET {expr} WHERE project_id = ?
```

### SQL0309 services/model_update/analysis/sensitivity_service.py:90

- 类型：`UPDATE`
- 特性：`portable_or_unknown`
- 参数数量：`2`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
UPDATE t_mt_work_condition_project SET sensitivity_status = %s WHERE project_id = %s
```

达梦候选:

```sql
UPDATE t_mt_work_condition_project SET sensitivity_status = ? WHERE project_id = ?
```

### SQL0310 services/model_update/analysis/sensitivity_service.py:863

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT response_no, request_no, step_name, frequency, region_type, set_name, set_scope, instance_name, part_name, variables_json, extra_json FROM t_mt_py_fem_static_sensitivity_response_catalog WHERE pid = %s ORDER BY response_no ASC, request_no ASC
```

达梦候选:

```sql
SELECT response_no, request_no, step_name, frequency, region_type, set_name, set_scope, instance_name, part_name, variables_json, extra_json FROM t_mt_py_fem_static_sensitivity_response_catalog WHERE pid = ? ORDER BY response_no ASC, request_no ASC
```

### SQL0311 services/model_update/analysis/sensitivity_service.py:1093

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT quantity_code, set_name, set_type, set_scope, instance_name, part_name, extra_json FROM t_mt_py_fem_quantity_set_capability WHERE pid = %s AND quantity_code IN ('T', 'H') ORDER BY CASE WHEN quantity_code = 'T' THEN 0 ELSE 1 END, set_name, set_scope, instance_name, part_name
```

达梦候选:

```sql
SELECT quantity_code, set_name, set_type, set_scope, instance_name, part_name, extra_json FROM t_mt_py_fem_quantity_set_capability WHERE pid = ? AND quantity_code IN ('T', 'H') ORDER BY CASE WHEN quantity_code = 'T' THEN 0 ELSE 1 END, set_name, set_scope, instance_name, part_name
```

### SQL0312 services/model_update/analysis/sensitivity_service.py:2357

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`2`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT id FROM t_mt_py_fem_analysis_run WHERE project_id = %s AND run_no = %s ORDER BY id DESC
```

达梦候选:

```sql
SELECT id FROM t_mt_py_fem_analysis_run WHERE project_id = ? AND run_no = ? ORDER BY id DESC
```

### SQL0313 services/model_update/analysis/sensitivity_service.py:2374

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_sensitivity_matrix_result WHERE analysis_run_id IN ({expr})
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_sensitivity_matrix_result WHERE analysis_run_id IN ({expr})
```

### SQL0314 services/model_update/analysis/sensitivity_service.py:2378

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_sensitivity_matrix_response WHERE analysis_run_id IN ({expr})
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_sensitivity_matrix_response WHERE analysis_run_id IN ({expr})
```

### SQL0315 services/model_update/analysis/sensitivity_service.py:2382

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_sensitivity_matrix_parameter WHERE analysis_run_id IN ({expr})
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_sensitivity_matrix_parameter WHERE analysis_run_id IN ({expr})
```

### SQL0316 services/model_update/analysis/sensitivity_service.py:2410

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`0`
- 验证状态：`skipped`
- 说明：动态拼接 SQL 需要运行时上下文

MySQL:

```sql
DELETE FROM t_mt_py_fem_analysis_run WHERE id IN ({expr})
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_analysis_run WHERE id IN ({expr})
```

### SQL0317 services/model_update/analysis/sensitivity_service.py:2414

- 类型：`UPDATE`
- 特性：`portable_or_unknown`
- 参数数量：`7`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
UPDATE t_mt_py_fem_analysis_run SET case_name = %s, source_kind = %s, op2_path = %s, matrix_path = %s, bdf_path = %s, metadata_path = %s WHERE id = %s
```

达梦候选:

```sql
UPDATE t_mt_py_fem_analysis_run SET case_name = ?, source_kind = ?, op2_path = ?, matrix_path = ?, bdf_path = ?, metadata_path = ? WHERE id = ?
```

### SQL0318 services/model_update/analysis/sensitivity_service.py:2437

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`8`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_analysis_run ( project_id, case_name, run_no, source_kind, op2_path, matrix_path, bdf_path, metadata_path ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_analysis_run ( project_id, case_name, run_no, source_kind, op2_path, matrix_path, bdf_path, metadata_path ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
```

### SQL0319 services/model_update/analysis/sensitivity_service.py:2501

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`8`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_sensitivity_matrix_response ( project_id, analysis_run_id, response_code, response_name, response_type, mode_number, unit, seq_no ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_sensitivity_matrix_response ( project_id, analysis_run_id, response_code, response_name, response_type, mode_number, unit, seq_no ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
```

### SQL0320 services/model_update/analysis/sensitivity_service.py:2524

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`15`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_sensitivity_matrix_parameter ( project_id, analysis_run_id, param_code, param_name, param_type, material_id, property_id, element_id, source_material_id, source_property_id, initial_value, lower_bound, upper_bound, unit, seq_no ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_sensitivity_matrix_parameter ( project_id, analysis_run_id, param_code, param_name, param_type, material_id, property_id, element_id, source_material_id, source_property_id, initial_value, lower_bound, upper_bound, unit, seq_no ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
```

### SQL0321 services/model_update/analysis/sensitivity_service.py:2555

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`5`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_sensitivity_matrix_result ( project_id, analysis_run_id, parameter_id, response_id, sensitivity_value ) VALUES (%s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_sensitivity_matrix_result ( project_id, analysis_run_id, parameter_id, response_id, sensitivity_value ) VALUES (?, ?, ?, ?, ?)
```

### SQL0322 services/model_update/analysis/sensitivity_service.py:2622

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`8`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_sensitivity_matrix_response ( project_id, analysis_run_id, response_code, response_name, response_type, mode_number, unit, seq_no ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_sensitivity_matrix_response ( project_id, analysis_run_id, response_code, response_name, response_type, mode_number, unit, seq_no ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
```

### SQL0323 services/model_update/analysis/sensitivity_service.py:2643

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`15`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_sensitivity_matrix_parameter ( project_id, analysis_run_id, param_code, param_name, param_type, material_id, property_id, element_id, source_material_id, source_property_id, initial_value, lower_bound, upper_bound, unit, seq_no ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_sensitivity_matrix_parameter ( project_id, analysis_run_id, param_code, param_name, param_type, material_id, property_id, element_id, source_material_id, source_property_id, initial_value, lower_bound, upper_bound, unit, seq_no ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
```

### SQL0324 services/model_update/analysis/sensitivity_service.py:2697

- 类型：`SELECT`
- 特性：`limit`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT id, project_id, case_name, run_no, source_kind, op2_path, matrix_path, bdf_path, metadata_path, created_at FROM t_mt_py_fem_analysis_run WHERE project_id = %s ORDER BY id DESC LIMIT 1
```

达梦候选:

```sql
SELECT id, project_id, case_name, run_no, source_kind, op2_path, matrix_path, bdf_path, metadata_path, created_at FROM t_mt_py_fem_analysis_run WHERE project_id = ? ORDER BY id DESC FETCH FIRST 1 ROWS ONLY
```

### SQL0325 services/model_update/analysis/sensitivity_service.py:2708

- 类型：`SELECT`
- 特性：`limit`
- 参数数量：`2`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT id, project_id, case_name, run_no, source_kind, op2_path, matrix_path, bdf_path, metadata_path, created_at FROM t_mt_py_fem_analysis_run WHERE project_id = %s AND run_no = %s ORDER BY id DESC LIMIT 1
```

达梦候选:

```sql
SELECT id, project_id, case_name, run_no, source_kind, op2_path, matrix_path, bdf_path, metadata_path, created_at FROM t_mt_py_fem_analysis_run WHERE project_id = ? AND run_no = ? ORDER BY id DESC FETCH FIRST 1 ROWS ONLY
```

### SQL0326 services/model_update/analysis/sensitivity_service.py:2726

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT id, response_code, response_name, response_type, mode_number, unit, seq_no FROM t_mt_py_fem_sensitivity_matrix_response WHERE analysis_run_id = %s ORDER BY seq_no ASC, id ASC
```

达梦候选:

```sql
SELECT id, response_code, response_name, response_type, mode_number, unit, seq_no FROM t_mt_py_fem_sensitivity_matrix_response WHERE analysis_run_id = ? ORDER BY seq_no ASC, id ASC
```

### SQL0327 services/model_update/analysis/sensitivity_service.py:2737

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT id, param_code, param_name, param_type, material_id, property_id, element_id, source_material_id, source_property_id, initial_value, lower_bound, upper_bound, unit, seq_no FROM t_mt_py_fem_sensitivity_matrix_parameter WHERE analysis_run_id = %s ORDER BY seq_no ASC, id ASC
```

达梦候选:

```sql
SELECT id, param_code, param_name, param_type, material_id, property_id, element_id, source_material_id, source_property_id, initial_value, lower_bound, upper_bound, unit, seq_no FROM t_mt_py_fem_sensitivity_matrix_parameter WHERE analysis_run_id = ? ORDER BY seq_no ASC, id ASC
```

### SQL0328 services/model_update/analysis/sensitivity_service.py:2749

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT parameter_id, response_id, sensitivity_value FROM t_mt_py_fem_sensitivity_matrix_result WHERE analysis_run_id = %s
```

达梦候选:

```sql
SELECT parameter_id, response_id, sensitivity_value FROM t_mt_py_fem_sensitivity_matrix_result WHERE analysis_run_id = ?
```

### SQL0329 services/model_update/analysis/sensitivity_service.py:3856

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT id, parameter_group_name, parameter_name, quantity_code, selection_mode, set_name, set_type, set_scope, instance_name, part_name, element_label, lower, upper, prob_id, scatter, current_value AS scalar_value, usage_scope, extra_json FROM t_mt_py_fem_selected_parameter WHERE pid = %s ORDER BY created_at ASC, id ASC
```

达梦候选:

```sql
SELECT id, parameter_group_name, parameter_name, quantity_code, selection_mode, set_name, set_type, set_scope, instance_name, part_name, element_label, lower, upper, prob_id, scatter, current_value AS scalar_value, usage_scope, extra_json FROM t_mt_py_fem_selected_parameter WHERE pid = ? ORDER BY created_at ASC, id ASC
```

### SQL0330 services/model_update/analysis/test_unit_service.py:67

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT x, y, z FROM t_mt_py_test_node WHERE pid = %s ORDER BY nid
```

达梦候选:

```sql
SELECT x, y, z FROM t_mt_py_test_node WHERE pid = ? ORDER BY nid
```

### SQL0331 services/model_update/analysis/test_unit_service.py:84

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT x_position, y_position, z_position FROM t_mt_measuring_point_info WHERE project_id = %s ORDER BY id
```

达梦候选:

```sql
SELECT x_position, y_position, z_position FROM t_mt_measuring_point_info WHERE project_id = ? ORDER BY id
```

### SQL0332 services/model_update/analysis/test_unit_service.py:133

- 类型：`UPDATE`
- 特性：`portable_or_unknown`
- 参数数量：`7`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
UPDATE t_mt_py_test_node SET x = x * %s, y = y * %s, z = z * %s, origin_x = origin_x * %s, origin_y = origin_y * %s, origin_z = origin_z * %s WHERE pid = %s
```

达梦候选:

```sql
UPDATE t_mt_py_test_node SET x = x * ?, y = y * ?, z = z * ?, origin_x = origin_x * ?, origin_y = origin_y * ?, origin_z = origin_z * ? WHERE pid = ?
```

### SQL0333 services/model_update/analysis/test_unit_service.py:148

- 类型：`UPDATE`
- 特性：`portable_or_unknown`
- 参数数量：`7`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
UPDATE t_mt_measuring_point_info SET x_position = x_position * %s, y_position = y_position * %s, z_position = z_position * %s, x_position_ori = x_position_ori * %s, y_position_ori = y_position_ori * %s, z_position_ori = z_position_ori * %s WHERE project_id = %s
```

达梦候选:

```sql
UPDATE t_mt_measuring_point_info SET x_position = x_position * ?, y_position = y_position * ?, z_position = z_position * ?, x_position_ori = x_position_ori * ?, y_position_ori = y_position_ori * ?, z_position_ori = z_position_ori * ? WHERE project_id = ?
```

### SQL0334 services/model_update/importers/ansys_mu_common.py:153

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`5`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_supported_quantity (quantity_code, quantity_name, unit, enabled, sort_no) VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE quantity_name=VALUES(quantity_name), unit=VALUES(unit), enabled=VALUES(enabled), sort_no=VALUES(sort_no)
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_supported_quantity t USING (SELECT ? AS quantity_code, ? AS quantity_name, ? AS unit, ? AS enabled, ? AS sort_no FROM DUAL) s ON (t.quantity_code = s.quantity_code) WHEN MATCHED THEN UPDATE SET t.quantity_name = s.quantity_name, t.unit = s.unit, t.enabled = s.enabled, t.sort_no = s.sort_no WHEN NOT MATCHED THEN INSERT (quantity_code, quantity_name, unit, enabled, sort_no) VALUES (s.quantity_code, s.quantity_name, s.unit, s.enabled, s.sort_no)
```

### SQL0335 services/model_update/importers/ansys_mu_common.py:167

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`3`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_material_overview (Id, pid, Type) VALUES (%s,%s,%s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_material_overview (Id, pid, Type) VALUES (?,?,?)
```

### SQL0336 services/model_update/importers/ansys_mu_common.py:171

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`6`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_isotropic (Id, pid, RHO, E, NU, GE) VALUES (%s,%s,%s,%s,%s,%s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_isotropic (Id, pid, RHO, E, NU, GE) VALUES (?,?,?,?,?,?)
```

### SQL0337 services/model_update/importers/ansys_mu_common.py:182

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`3`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_property (Id, pid, Type) VALUES (%s,%s,%s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_property (Id, pid, Type) VALUES (?,?,?)
```

### SQL0338 services/model_update/importers/ansys_mu_common.py:186

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`5`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_shell_property (Id, pid, Thickness, NSM, THETA) VALUES (%s,%s,%s,%s,%s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_shell_property (Id, pid, Thickness, NSM, THETA) VALUES (?,?,?,?,?)
```

### SQL0339 services/model_update/importers/ansys_mu_common.py:193

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`16`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_quantity_set_capability (pid, quantity_code, set_name, set_type, set_scope, instance_name, part_name, set_role, element_family, section_type, material_name, member_count, supports_global, supports_local, current_value, extra_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE set_role=VALUES(set_role), element_family=VALUES(element_family), section_type=VALUES(section_type), material_name=VALUES(material_name), member_count=VALUES(member_count), supports_global=VALUES(supports_global), supports_local=VALUES(supports_local), current_value=VALUES(current_value), extra_json=VALUES(extra_json)
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_quantity_set_capability t USING (SELECT ? AS pid, ? AS quantity_code, ? AS set_name, ? AS set_type, ? AS set_scope, ? AS instance_name, ? AS part_name, ? AS set_role, ? AS element_family, ? AS section_type, ? AS material_name, ? AS member_count, ? AS supports_global, ? AS supports_local, ? AS current_value, ? AS extra_json FROM DUAL) s ON (t.pid = s.pid AND t.quantity_code = s.quantity_code AND t.set_name = s.set_name AND t.set_type = s.set_type AND t.set_scope = s.set_scope AND t.instance_name = s.instance_name AND t.part_name = s.part_name) WHEN MATCHED THEN UPDATE SET t.set_role = s.set_role, t.element_family = s.element_family, t.section_type = s.section_type, t.material_name = s.material_name, t.member_count = s.member_count, t.supports_global = s.supports_global, t.supports_local = s.supports_local, t.current_value = s.current_value, t.extra_json = s.extra_json WHEN NOT MATCHED THEN INSERT (pid, quantity_code, set_name, set_type, set_scope, instance_name, part_name, set_role, element_family, section_type, material_name, member_count, supports_global, supports_local, current_value, extra_json) VALUES (s.pid, s.quantity_code, s.set_name, s.set_type, s.set_scope, s.instance_name, s.part_name, s.set_role, s.element_family, s.section_type, s.material_name, s.member_count, s.supports_global, s.supports_local, s.current_value, s.extra_json)
```

### SQL0340 services/model_update/importers/ansys_mu_common.py:216

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`7`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_node_octree_cache (pid, source_file_path, cache_file_path, node_count, instance_count, bbox_min, bbox_max, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP) ON DUPLICATE KEY UPDATE cache_file_path=VALUES(cache_file_path), node_count=VALUES(node_count), instance_count=VALUES(instance_count), bbox_min=VALUES(bbox_min), bbox_max=VALUES(bbox_max), updated_at=CURRENT_TIMESTAMP
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_node_octree_cache t USING (SELECT ? AS pid, ? AS source_file_path, ? AS cache_file_path, ? AS node_count, ? AS instance_count, ? AS bbox_min, ? AS bbox_max, CURRENT_TIMESTAMP AS updated_at FROM DUAL) s ON (t.pid = s.pid AND t.source_file_path = s.source_file_path) WHEN MATCHED THEN UPDATE SET t.cache_file_path = s.cache_file_path, t.node_count = s.node_count, t.instance_count = s.instance_count, t.bbox_min = s.bbox_min, t.bbox_max = s.bbox_max, t.updated_at = CURRENT_TIMESTAMP WHEN NOT MATCHED THEN INSERT (pid, source_file_path, cache_file_path, node_count, instance_count, bbox_min, bbox_max, updated_at) VALUES (s.pid, s.source_file_path, s.cache_file_path, s.node_count, s.instance_count, s.bbox_min, s.bbox_max, s.updated_at)
```

### SQL0341 services/model_update/importers/bdf_octree_bundle_import.py:32

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM t_mt_py_fem_quantity_set_capability WHERE pid = %s
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_quantity_set_capability WHERE pid = ?
```

### SQL0342 services/model_update/importers/bdf_octree_bundle_import.py:33

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM t_mt_py_fem_node_octree_cache WHERE pid = %s
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_node_octree_cache WHERE pid = ?
```

### SQL0343 services/model_update/importers/bdf_octree_bundle_import.py:46

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`5`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_supported_quantity (quantity_code, quantity_name, unit, enabled, sort_no) VALUES (%s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE quantity_name = VALUES(quantity_name), unit = VALUES(unit), enabled = VALUES(enabled), sort_no = VALUES(sort_no)
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_supported_quantity t USING (SELECT ? AS quantity_code, ? AS quantity_name, ? AS unit, ? AS enabled, ? AS sort_no FROM DUAL) s ON (t.quantity_code = s.quantity_code) WHEN MATCHED THEN UPDATE SET t.quantity_name = s.quantity_name, t.unit = s.unit, t.enabled = s.enabled, t.sort_no = s.sort_no WHEN NOT MATCHED THEN INSERT (quantity_code, quantity_name, unit, enabled, sort_no) VALUES (s.quantity_code, s.quantity_name, s.unit, s.enabled, s.sort_no)
```

### SQL0344 services/model_update/importers/bdf_octree_bundle_import.py:66

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`16`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_quantity_set_capability (pid, quantity_code, set_name, set_type, set_scope, instance_name, part_name, set_role, element_family, section_type, material_name, member_count, supports_global, supports_local, current_value, extra_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE set_role = VALUES(set_role), element_family = VALUES(element_family), section_type = VALUES(section_type), material_name = VALUES(material_name), member_count = VALUES(member_count), supports_global = VALUES(supports_global), supports_local = VALUES(supports_local), current_value = VALUES(current_value), extra_json = VALUES(extra_json)
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_quantity_set_capability t USING (SELECT ? AS pid, ? AS quantity_code, ? AS set_name, ? AS set_type, ? AS set_scope, ? AS instance_name, ? AS part_name, ? AS set_role, ? AS element_family, ? AS section_type, ? AS material_name, ? AS member_count, ? AS supports_global, ? AS supports_local, ? AS current_value, ? AS extra_json FROM DUAL) s ON (t.pid = s.pid AND t.quantity_code = s.quantity_code AND t.set_name = s.set_name AND t.set_type = s.set_type AND t.set_scope = s.set_scope AND t.instance_name = s.instance_name AND t.part_name = s.part_name) WHEN MATCHED THEN UPDATE SET t.set_role = s.set_role, t.element_family = s.element_family, t.section_type = s.section_type, t.material_name = s.material_name, t.member_count = s.member_count, t.supports_global = s.supports_global, t.supports_local = s.supports_local, t.current_value = s.current_value, t.extra_json = s.extra_json WHEN NOT MATCHED THEN INSERT (pid, quantity_code, set_name, set_type, set_scope, instance_name, part_name, set_role, element_family, section_type, material_name, member_count, supports_global, supports_local, current_value, extra_json) VALUES (s.pid, s.quantity_code, s.set_name, s.set_type, s.set_scope, s.instance_name, s.part_name, s.set_role, s.element_family, s.section_type, s.material_name, s.member_count, s.supports_global, s.supports_local, s.current_value, s.extra_json)
```

### SQL0345 services/model_update/importers/bdf_octree_bundle_import.py:89

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`7`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_node_octree_cache (pid, source_file_path, cache_file_path, node_count, instance_count, bbox_min, bbox_max, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP) ON DUPLICATE KEY UPDATE cache_file_path = VALUES(cache_file_path), node_count = VALUES(node_count), instance_count = VALUES(instance_count), bbox_min = VALUES(bbox_min), bbox_max = VALUES(bbox_max), updated_at = CURRENT_TIMESTAMP
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_node_octree_cache t USING (SELECT ? AS pid, ? AS source_file_path, ? AS cache_file_path, ? AS node_count, ? AS instance_count, ? AS bbox_min, ? AS bbox_max, CURRENT_TIMESTAMP AS updated_at FROM DUAL) s ON (t.pid = s.pid AND t.source_file_path = s.source_file_path) WHEN MATCHED THEN UPDATE SET t.cache_file_path = s.cache_file_path, t.node_count = s.node_count, t.instance_count = s.instance_count, t.bbox_min = s.bbox_min, t.bbox_max = s.bbox_max, t.updated_at = CURRENT_TIMESTAMP WHEN NOT MATCHED THEN INSERT (pid, source_file_path, cache_file_path, node_count, instance_count, bbox_min, bbox_max, updated_at) VALUES (s.pid, s.source_file_path, s.cache_file_path, s.node_count, s.instance_count, s.bbox_min, s.bbox_max, s.updated_at)
```

### SQL0346 services/model_update/importers/bdf_service.py:355

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`5`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_supported_quantity (quantity_code, quantity_name, unit, enabled, sort_no) VALUES (%s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE quantity_name = VALUES(quantity_name), unit = VALUES(unit), enabled = VALUES(enabled), sort_no = VALUES(sort_no)
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_supported_quantity t USING (SELECT ? AS quantity_code, ? AS quantity_name, ? AS unit, ? AS enabled, ? AS sort_no FROM DUAL) s ON (t.quantity_code = s.quantity_code) WHEN MATCHED THEN UPDATE SET t.quantity_name = s.quantity_name, t.unit = s.unit, t.enabled = s.enabled, t.sort_no = s.sort_no WHEN NOT MATCHED THEN INSERT (quantity_code, quantity_name, unit, enabled, sort_no) VALUES (s.quantity_code, s.quantity_name, s.unit, s.enabled, s.sort_no)
```

### SQL0347 services/model_update/importers/bdf_service.py:372

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`3`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_material_overview (Id, pid, Type) VALUES (%s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_material_overview (Id, pid, Type) VALUES (?, ?, ?)
```

### SQL0348 services/model_update/importers/bdf_service.py:387

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`6`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_isotropic (Id, pid, RHO, E, NU, GE) VALUES (%s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_isotropic (Id, pid, RHO, E, NU, GE) VALUES (?, ?, ?, ?, ?, ?)
```

### SQL0349 services/model_update/importers/bdf_service.py:407

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`10`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_ortho2d (Id, pid, RHO, EX, EY, GXY, NUXY, GXZ, GYZ, GE) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_ortho2d (Id, pid, RHO, EX, EY, GXY, NUXY, GXZ, GYZ, GE) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
```

### SQL0350 services/model_update/importers/bdf_service.py:464

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`25`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_aniso3d (Id, pid, RHO, D11, D12, D13, D14, D15, D16, D22, D23, D24, D25, D26, D33, D34, D35, D36, D44, D45, D46, D55, D56, D66, GE) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_aniso3d (Id, pid, RHO, D11, D12, D13, D14, D15, D16, D22, D23, D24, D25, D26, D33, D34, D35, D36, D44, D45, D46, D55, D56, D66, GE) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
```

### SQL0351 services/model_update/importers/bdf_service.py:494

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`3`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_property (Id, pid, Type) VALUES (%s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_property (Id, pid, Type) VALUES (?, ?, ?)
```

### SQL0352 services/model_update/importers/bdf_service.py:509

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`5`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_shell_property (Id, pid, Thickness, NSM, THETA) VALUES (%s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_shell_property (Id, pid, Thickness, NSM, THETA) VALUES (?, ?, ?, ?, ?)
```

### SQL0353 services/model_update/importers/bdf_service.py:529

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`12`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_beam_property (Id, pid, AX, AY, AZ, IX, IY, IZ, CW, YN, ZN, NSM) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_beam_property (Id, pid, AX, AY, AZ, IX, IY, IZ, CW, YN, ZN, NSM) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
```

### SQL0354 services/model_update/importers/bdf_service.py:549

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`4`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_solid_property (Id, pid, MID, CID) VALUES (%s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_solid_property (Id, pid, MID, CID) VALUES (?, ?, ?, ?)
```

### SQL0355 services/model_update/importers/bdf_service.py:563

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`7`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_layered_property (Id, pid, Offset_L, Theta, GE, NSM, Layers) VALUES (%s, %s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_layered_property (Id, pid, Offset_L, Theta, GE, NSM, Layers) VALUES (?, ?, ?, ?, ?, ?, ?)
```

### SQL0356 services/model_update/importers/bdf_service.py:589

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`9`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_boundary (Id, pid, Node, UX, UY, UZ, RX, RY, RZ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_fem_boundary (Id, pid, Node, UX, UY, UZ, RX, RY, RZ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
```

### SQL0357 services/model_update/importers/bdf_service.py:624

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`16`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_quantity_set_capability (pid, quantity_code, set_name, set_type, set_scope, instance_name, part_name, set_role, element_family, section_type, material_name, member_count, supports_global, supports_local, current_value, extra_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE set_role = VALUES(set_role), element_family = VALUES(element_family), section_type = VALUES(section_type), material_name = VALUES(material_name), member_count = VALUES(member_count), supports_global = VALUES(supports_global), supports_local = VALUES(supports_local), current_value = VALUES(current_value), extra_json = VALUES(extra_json)
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_quantity_set_capability t USING (SELECT ? AS pid, ? AS quantity_code, ? AS set_name, ? AS set_type, ? AS set_scope, ? AS instance_name, ? AS part_name, ? AS set_role, ? AS element_family, ? AS section_type, ? AS material_name, ? AS member_count, ? AS supports_global, ? AS supports_local, ? AS current_value, ? AS extra_json FROM DUAL) s ON (t.pid = s.pid AND t.quantity_code = s.quantity_code AND t.set_name = s.set_name AND t.set_type = s.set_type AND t.set_scope = s.set_scope AND t.instance_name = s.instance_name AND t.part_name = s.part_name) WHEN MATCHED THEN UPDATE SET t.set_role = s.set_role, t.element_family = s.element_family, t.section_type = s.section_type, t.material_name = s.material_name, t.member_count = s.member_count, t.supports_global = s.supports_global, t.supports_local = s.supports_local, t.current_value = s.current_value, t.extra_json = s.extra_json WHEN NOT MATCHED THEN INSERT (pid, quantity_code, set_name, set_type, set_scope, instance_name, part_name, set_role, element_family, section_type, material_name, member_count, supports_global, supports_local, current_value, extra_json) VALUES (s.pid, s.quantity_code, s.set_name, s.set_type, s.set_scope, s.instance_name, s.part_name, s.set_role, s.element_family, s.section_type, s.material_name, s.member_count, s.supports_global, s.supports_local, s.current_value, s.extra_json)
```

### SQL0358 services/model_update/importers/bdf_service.py:649

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`7`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_node_octree_cache (pid, source_file_path, cache_file_path, node_count, instance_count, bbox_min, bbox_max, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP) ON DUPLICATE KEY UPDATE cache_file_path = VALUES(cache_file_path), node_count = VALUES(node_count), instance_count = VALUES(instance_count), bbox_min = VALUES(bbox_min), bbox_max = VALUES(bbox_max), updated_at = CURRENT_TIMESTAMP
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_node_octree_cache t USING (SELECT ? AS pid, ? AS source_file_path, ? AS cache_file_path, ? AS node_count, ? AS instance_count, ? AS bbox_min, ? AS bbox_max, CURRENT_TIMESTAMP AS updated_at FROM DUAL) s ON (t.pid = s.pid AND t.source_file_path = s.source_file_path) WHEN MATCHED THEN UPDATE SET t.cache_file_path = s.cache_file_path, t.node_count = s.node_count, t.instance_count = s.instance_count, t.bbox_min = s.bbox_min, t.bbox_max = s.bbox_max, t.updated_at = CURRENT_TIMESTAMP WHEN NOT MATCHED THEN INSERT (pid, source_file_path, cache_file_path, node_count, instance_count, bbox_min, bbox_max, updated_at) VALUES (s.pid, s.source_file_path, s.cache_file_path, s.node_count, s.instance_count, s.bbox_min, s.bbox_max, s.updated_at)
```

### SQL0359 services/model_update/importers/op2_modal_bundle_import.py:24

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM t_mt_py_fem_modal_result WHERE pid = %s
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_modal_result WHERE pid = ?
```

### SQL0360 services/model_update/importers/op2_modal_bundle_import.py:25

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = %s
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = ?
```

### SQL0361 services/model_update/importers/op2_modal_bundle_import.py:72

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`10`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_modal_result (pid, mode_no, frequency, instance_name, part_name, fem_node_label, u1, u2, u3, extra_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE frequency = VALUES(frequency), part_name = VALUES(part_name), u1 = VALUES(u1), u2 = VALUES(u2), u3 = VALUES(u3), extra_json = VALUES(extra_json), created_at = CURRENT_TIMESTAMP
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_modal_result t USING (SELECT ? AS pid, ? AS mode_no, ? AS frequency, ? AS instance_name, ? AS part_name, ? AS fem_node_label, ? AS u1, ? AS u2, ? AS u3, ? AS extra_json FROM DUAL) s ON (t.pid = s.pid AND t.mode_no = s.mode_no AND t.instance_name = s.instance_name AND t.fem_node_label = s.fem_node_label) WHEN MATCHED THEN UPDATE SET t.frequency = s.frequency, t.part_name = s.part_name, t.u1 = s.u1, t.u2 = s.u2, t.u3 = s.u3, t.extra_json = s.extra_json, t.created_at = CURRENT_TIMESTAMP WHEN NOT MATCHED THEN INSERT (pid, mode_no, frequency, instance_name, part_name, fem_node_label, u1, u2, u3, extra_json) VALUES (s.pid, s.mode_no, s.frequency, s.instance_name, s.part_name, s.fem_node_label, s.u1, s.u2, s.u3, s.extra_json)
```

### SQL0362 services/model_update/importers/op2_modal_bundle_import.py:78

- 类型：`INSERT`
- 特性：`on_duplicate_key_update, values_function`
- 参数数量：`10`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_fem_modal_result (pid, mode_no, frequency, instance_name, part_name, fem_node_label, u1, u2, u3, extra_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE frequency = VALUES(frequency), part_name = VALUES(part_name), u1 = VALUES(u1), u2 = VALUES(u2), u3 = VALUES(u3), extra_json = VALUES(extra_json), created_at = CURRENT_TIMESTAMP
```

达梦候选:

```sql
MERGE INTO t_mt_py_fem_modal_result t USING (SELECT ? AS pid, ? AS mode_no, ? AS frequency, ? AS instance_name, ? AS part_name, ? AS fem_node_label, ? AS u1, ? AS u2, ? AS u3, ? AS extra_json FROM DUAL) s ON (t.pid = s.pid AND t.mode_no = s.mode_no AND t.instance_name = s.instance_name AND t.fem_node_label = s.fem_node_label) WHEN MATCHED THEN UPDATE SET t.frequency = s.frequency, t.part_name = s.part_name, t.u1 = s.u1, t.u2 = s.u2, t.u3 = s.u3, t.extra_json = s.extra_json, t.created_at = CURRENT_TIMESTAMP WHEN NOT MATCHED THEN INSERT (pid, mode_no, frequency, instance_name, part_name, fem_node_label, u1, u2, u3, extra_json) VALUES (s.pid, s.mode_no, s.frequency, s.instance_name, s.part_name, s.fem_node_label, s.u1, s.u2, s.u3, s.extra_json)
```

### SQL0363 services/model_update/importers/op2_modal_to_mysql_direct.py:54

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM t_mt_py_fem_modal_result WHERE pid = %s
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_modal_result WHERE pid = ?
```

### SQL0364 services/model_update/importers/op2_modal_to_mysql_direct.py:55

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = %s
```

达梦候选:

```sql
DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = ?
```

### SQL0365 services/model_update/importers/txt2mysql.py:166

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`11`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_test_node (nid, pid, fid, ics, ocs, x, y, z, origin_x, origin_y, origin_z) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_test_node (nid, pid, fid, ics, ocs, x, y, z, origin_x, origin_y, origin_z) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
```

### SQL0366 services/model_update/importers/txt2mysql.py:192

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`7`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_test_element (element_no, pid, element_type, point1, point2, point3, point4) VALUES (%s, %s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_test_element (element_no, pid, element_type, point1, point2, point3, point4) VALUES (?, ?, ?, ?, ?, ?, ?)
```

### SQL0367 services/model_update/importers/txt2mysql.py:231

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`7`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_test_modal_frequency (mode_no, pid, fid, frequency, damping, eigenvalue_Re, eigenvalue_Im) VALUES (%s, %s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_test_modal_frequency (mode_no, pid, fid, frequency, damping, eigenvalue_Re, eigenvalue_Im) VALUES (?, ?, ?, ?, ?, ?, ?)
```

### SQL0368 services/model_update/importers/txt2mysql.py:240

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`3`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_test_modal_shape (mode_no, pid, modal_shape) VALUES (%s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_test_modal_shape (mode_no, pid, modal_shape) VALUES (?, ?, ?)
```

### SQL0369 services/model_update/importers/txt2mysql.py:251

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`6`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_test_modal_shape_real (mode_no, pid, point, ux, uy, uz) VALUES (%s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_test_modal_shape_real (mode_no, pid, point, ux, uy, uz) VALUES (?, ?, ?, ?, ?, ?)
```

### SQL0370 services/model_update/importers/unv_frf_service.py:230

- 类型：`SELECT`
- 特性：`limit`
- 参数数量：`2`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT id FROM t_mt_py_test_frf_curve WHERE pid = %s AND curve_name = %s LIMIT 1
```

达梦候选:

```sql
SELECT id FROM t_mt_py_test_frf_curve WHERE pid = ? AND curve_name = ? FETCH FIRST 1 ROWS ONLY
```

### SQL0371 services/model_update/importers/unv_frf_service.py:233

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`18`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_test_frf_curve ( pid, curve_name, curve_no, title, frf_type, response_node, response_dir, reference_node, reference_dir, x_type, y_type, denominator_type, z_type, ordinate_type, abscissa_spacing, n_points, source_file, extra_json ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_test_frf_curve ( pid, curve_name, curve_no, title, frf_type, response_node, response_dir, reference_node, reference_dir, x_type, y_type, denominator_type, z_type, ordinate_type, abscissa_spacing, n_points, source_file, extra_json ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
```

### SQL0372 services/model_update/importers/unv_frf_service.py:259

- 类型：`UPDATE`
- 特性：`portable_or_unknown`
- 参数数量：`17`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
UPDATE t_mt_py_test_frf_curve SET curve_no = %s, title = %s, frf_type = %s, response_node = %s, response_dir = %s, reference_node = %s, reference_dir = %s, x_type = %s, y_type = %s, denominator_type = %s, z_type = %s, ordinate_type = %s, abscissa_spacing = %s, n_points = %s, source_file = %s, extra_json = %s WHERE id = %s
```

达梦候选:

```sql
UPDATE t_mt_py_test_frf_curve SET curve_no = ?, title = ?, frf_type = ?, response_node = ?, response_dir = ?, reference_node = ?, reference_dir = ?, x_type = ?, y_type = ?, denominator_type = ?, z_type = ?, ordinate_type = ?, abscissa_spacing = ?, n_points = ?, source_file = ?, extra_json = ? WHERE id = ?
```

### SQL0373 services/model_update/importers/unv_frf_service.py:283

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`2`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM t_mt_py_test_frf_point WHERE pid = %s AND curve_id = %s
```

达梦候选:

```sql
DELETE FROM t_mt_py_test_frf_point WHERE pid = ? AND curve_id = ?
```

### SQL0374 services/model_update/importers/unv_frf_service.py:286

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`2`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM t_mt_py_test_frf_point WHERE pid = %s AND curve_id = %s
```

达梦候选:

```sql
DELETE FROM t_mt_py_test_frf_point WHERE pid = ? AND curve_id = ?
```

### SQL0375 services/model_update/importers/unv_frf_service.py:292

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`6`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_test_frf_point (pid, curve_id, point_no, frequency, real_value, imag_value) VALUES (%s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_test_frf_point (pid, curve_id, point_no, frequency, real_value, imag_value) VALUES (?, ?, ?, ?, ?, ?)
```

### SQL0376 services/model_update/importers/unv_frf_service.py:324

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT curve_name FROM t_mt_py_test_frf_curve WHERE pid = %s ORDER BY curve_no, curve_name
```

达梦候选:

```sql
SELECT curve_name FROM t_mt_py_test_frf_curve WHERE pid = ? ORDER BY curve_no, curve_name
```

### SQL0377 services/model_update/importers/unv_frf_service.py:413

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT curve_name FROM t_mt_py_test_frf_curve WHERE pid = %s ORDER BY curve_no, curve_name
```

达梦候选:

```sql
SELECT curve_name FROM t_mt_py_test_frf_curve WHERE pid = ? ORDER BY curve_no, curve_name
```

### SQL0378 services/model_update/importers/unv_frf_service.py:426

- 类型：`SELECT`
- 特性：`limit`
- 参数数量：`2`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT id, curve_name FROM t_mt_py_test_frf_curve WHERE pid = %s AND curve_name = %s LIMIT 1
```

达梦候选:

```sql
SELECT id, curve_name FROM t_mt_py_test_frf_curve WHERE pid = ? AND curve_name = ? FETCH FIRST 1 ROWS ONLY
```

### SQL0379 services/model_update/importers/unv_frf_service.py:440

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`2`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT frequency, real_value, imag_value FROM t_mt_py_test_frf_point WHERE pid = %s AND curve_id = %s ORDER BY point_no
```

达梦候选:

```sql
SELECT frequency, real_value, imag_value FROM t_mt_py_test_frf_point WHERE pid = ? AND curve_id = ? ORDER BY point_no
```

### SQL0380 services/model_update/importers/unv_service.py:54

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT f.mode_no, f.frequency, s.modal_shape FROM t_mt_py_test_modal_frequency f LEFT JOIN t_mt_py_test_modal_shape s ON f.pid = s.pid AND f.mode_no = s.mode_no WHERE f.pid = %s ORDER BY f.mode_no
```

达梦候选:

```sql
SELECT f.mode_no, f.frequency, s.modal_shape FROM t_mt_py_test_modal_frequency f LEFT JOIN t_mt_py_test_modal_shape s ON f.pid = s.pid AND f.mode_no = s.mode_no WHERE f.pid = ? ORDER BY f.mode_no
```

### SQL0381 services/model_update/importers/unv_service.py:173

- 类型：`UPDATE`
- 特性：`portable_or_unknown`
- 参数数量：`2`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
UPDATE t_mt_work_condition_project SET test_modal_data_type = %s WHERE project_id = %s
```

达梦候选:

```sql
UPDATE t_mt_work_condition_project SET test_modal_data_type = ? WHERE project_id = ?
```

### SQL0382 services/model_update/importers/unv_service.py:179

- 类型：`UPDATE`
- 特性：`portable_or_unknown`
- 参数数量：`2`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
UPDATE t_mt_work_condition_project SET test_data_status = %s WHERE project_id = %s
```

达梦候选:

```sql
UPDATE t_mt_work_condition_project SET test_data_status = ? WHERE project_id = ?
```

### SQL0383 services/model_update/importers/unv_service.py:193

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`7`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_test_modal_frequency (mode_no, pid, fid, frequency, damping, eigenvalue_Re, eigenvalue_Im) VALUES (%s, %s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_test_modal_frequency (mode_no, pid, fid, frequency, damping, eigenvalue_Re, eigenvalue_Im) VALUES (?, ?, ?, ?, ?, ?, ?)
```

### SQL0384 services/model_update/importers/unv_service.py:211

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`3`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_test_modal_shape (mode_no, pid, modal_shape) VALUES (%s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_test_modal_shape (mode_no, pid, modal_shape) VALUES (?, ?, ?)
```

### SQL0385 services/model_update/importers/unv_service.py:230

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`6`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_test_modal_shape_real (mode_no, pid, point, ux, uy, uz) VALUES (%s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_test_modal_shape_real (mode_no, pid, point, ux, uy, uz) VALUES (?, ?, ?, ?, ?, ?)
```

### SQL0386 services/model_update/importers/unv_service.py:247

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`9`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_test_modal_shape_imag (mode_no, pid, point, re_ux, re_uy, re_uz, im_ux, im_uy, im_uz) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_test_modal_shape_imag (mode_no, pid, point, re_ux, re_uy, re_uz, im_ux, im_uy, im_uz) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
```

### SQL0387 services/model_update/importers/unv_service.py:295

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`13`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_test_static_result (pid, fid, load_case_no, result_no, point, ux, uy, uz, rx, ry, rz, load_factor, extra_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_test_static_result (pid, fid, load_case_no, result_no, point, ux, uy, uz, rx, ry, rz, load_factor, extra_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
```

### SQL0388 services/model_update/importers/unv_service.py:317

- 类型：`DELETE`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
DELETE FROM t_mt_measuring_point_info WHERE project_id = %s
```

达梦候选:

```sql
DELETE FROM t_mt_measuring_point_info WHERE project_id = ?
```

### SQL0389 services/model_update/importers/unv_service.py:341

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`10`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_measuring_point_info (measuring_point_name, project_id, sensor_type_id, x_position, y_position, z_position, x_position_ori, y_position_ori, z_position_ori, data_source) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_measuring_point_info (measuring_point_name, project_id, sensor_type_id, x_position, y_position, z_position, x_position_ori, y_position_ori, z_position_ori, data_source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
```

### SQL0390 services/model_update/importers/unv_service.py:356

- 类型：`UPDATE`
- 特性：`portable_or_unknown`
- 参数数量：`2`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
UPDATE t_mt_measuring_point_info SET measuring_point_name = %s WHERE id = %s
```

达梦候选:

```sql
UPDATE t_mt_measuring_point_info SET measuring_point_name = ? WHERE id = ?
```

### SQL0391 services/model_update/importers/unv_service.py:446

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`2`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT id, point, ux, uy, uz FROM t_mt_py_test_static_result WHERE pid = %s AND id = %s
```

达梦候选:

```sql
SELECT id, point, ux, uy, uz FROM t_mt_py_test_static_result WHERE pid = ? AND id = ?
```

### SQL0392 services/model_update/importers/unv_service.py:463

- 类型：`SELECT`
- 特性：`limit`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT load_case_no, result_no FROM t_mt_py_test_static_result WHERE pid = %s ORDER BY load_case_no, result_no, id LIMIT 1
```

达梦候选:

```sql
SELECT load_case_no, result_no FROM t_mt_py_test_static_result WHERE pid = ? ORDER BY load_case_no, result_no, id FETCH FIRST 1 ROWS ONLY
```

### SQL0393 services/model_update/importers/unv_service.py:482

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`3`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT id, point, ux, uy, uz FROM t_mt_py_test_static_result WHERE pid = %s AND load_case_no = %s AND result_no = %s ORDER BY point, id
```

达梦候选:

```sql
SELECT id, point, ux, uy, uz FROM t_mt_py_test_static_result WHERE pid = ? AND load_case_no = ? AND result_no = ? ORDER BY point, id
```

### SQL0394 services/model_update/importers/unv_service.py:600

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`11`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_test_node (nid, pid, fid, ics, ocs, x, y, z, origin_x, origin_y, origin_z) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
```

达梦候选:

```sql
INSERT INTO t_mt_py_test_node (nid, pid, fid, ics, ocs, x, y, z, origin_x, origin_y, origin_z) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
```

### SQL0395 services/model_update/importers/unv_service.py:627

- 类型：`INSERT`
- 特性：`values_function`
- 参数数量：`7`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
INSERT INTO t_mt_py_test_element ( element_no, pid, element_type, point1, point2, point3, point4 ) VALUES ( %s, %s, %s, %s, %s, %s, %s )
```

达梦候选:

```sql
INSERT INTO t_mt_py_test_element ( element_no, pid, element_type, point1, point2, point3, point4 ) VALUES ( ?, ?, ?, ?, ?, ?, ? )
```

### SQL0396 services/model_update/importers/unv_service.py:724

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT nid, x, y, z FROM t_mt_py_test_node WHERE pid = %s ORDER BY nid
```

达梦候选:

```sql
SELECT nid, x, y, z FROM t_mt_py_test_node WHERE pid = ? ORDER BY nid
```

### SQL0397 services/model_update/importers/unv_service.py:735

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT point1, point2 FROM t_mt_py_test_element WHERE pid = %s ORDER BY element_no
```

达梦候选:

```sql
SELECT point1, point2 FROM t_mt_py_test_element WHERE pid = ? ORDER BY element_no
```

### SQL0398 services/model_update/importers/unv_service.py:794

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT id, measuring_point_name, sensor_type_id, x_position, y_position, z_position FROM t_mt_measuring_point_info WHERE project_id = %s ORDER BY id
```

达梦候选:

```sql
SELECT id, measuring_point_name, sensor_type_id, x_position, y_position, z_position FROM t_mt_measuring_point_info WHERE project_id = ? ORDER BY id
```

### SQL0399 services/model_update/importers/unv_service.py:804

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT load_case_no, result_no, point_no, component_name, initial_relative_error, updated_relative_error FROM t_mt_py_fem_analysis_error WHERE pid = %s ORDER BY load_case_no DESC, result_no DESC, point_no, component_name
```

达梦候选:

```sql
SELECT load_case_no, result_no, point_no, component_name, initial_relative_error, updated_relative_error FROM t_mt_py_fem_analysis_error WHERE pid = ? ORDER BY load_case_no DESC, result_no DESC, point_no, component_name
```

### SQL0400 services/model_update/importers/unv_service.py:845

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT CAST(nid AS CHAR) AS test_node_id, x AS x_position, y AS y_position, z AS z_position FROM t_mt_py_test_node WHERE pid = %s ORDER BY nid
```

达梦候选:

```sql
SELECT CAST(nid AS CHAR) AS test_node_id, x AS x_position, y AS y_position, z AS z_position FROM t_mt_py_test_node WHERE pid = ? ORDER BY nid
```

### SQL0401 services/model_update/importers/unv_service.py:857

- 类型：`SELECT`
- 特性：`portable_or_unknown`
- 参数数量：`1`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT id, measuring_point_name, sensor_type_id, x_position, y_position, z_position FROM t_mt_measuring_point_info WHERE project_id = %s ORDER BY id
```

达梦候选:

```sql
SELECT id, measuring_point_name, sensor_type_id, x_position, y_position, z_position FROM t_mt_measuring_point_info WHERE project_id = ? ORDER BY id
```

### SQL0402 services/model_update/importers/unv_service.py:889

- 类型：`SELECT`
- 特性：`limit`
- 参数数量：`2`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT nid, x, y, z FROM t_mt_py_test_node WHERE pid = %s AND nid = %s ORDER BY fid LIMIT 1
```

达梦候选:

```sql
SELECT nid, x, y, z FROM t_mt_py_test_node WHERE pid = ? AND nid = ? ORDER BY fid FETCH FIRST 1 ROWS ONLY
```

### SQL0403 services/model_update/importers/unv_service.py:910

- 类型：`SELECT`
- 特性：`limit`
- 参数数量：`4`
- 验证状态：`skipped`
- 说明：参数化 SQL 需要业务样例参数

MySQL:

```sql
SELECT id, measuring_point_name, sensor_type_id, x_position, y_position, z_position FROM t_mt_measuring_point_info WHERE project_id = %s AND x_position = %s AND y_position = %s AND z_position = %s ORDER BY id LIMIT 1
```

达梦候选:

```sql
SELECT id, measuring_point_name, sensor_type_id, x_position, y_position, z_position FROM t_mt_measuring_point_info WHERE project_id = ? AND x_position = ? AND y_position = ? AND z_position = ? ORDER BY id FETCH FIRST 1 ROWS ONLY
```
