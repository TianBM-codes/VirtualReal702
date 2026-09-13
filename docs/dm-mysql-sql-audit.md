# MySQL 到达梦兼容性 SQL 清单

本文档只整理当前项目中会影响达梦兼容的 MySQL 专属写法，不代表已经修改主程序。

## 主要问题

1. 连接层写死 MySQL
   - `db.py` 使用 `mysql.connector` 和 `mysql.connector.pooling.MySQLConnectionPool`。
   - `requirements.txt` 只有 MySQL 驱动，没有 `dmPython` 或 `pyodbc`。

2. DDL 写法偏 MySQL
   - `AUTO_INCREMENT`
   - `ENGINE=InnoDB DEFAULT CHARSET=utf8mb4`
   - 列/表级 `COMMENT`
   - 反引号标识符，例如 `` `time` ``
   - `KEY` / `UNIQUE KEY`
   - `TINYINT(1)`
   - `JSON`
   - `ON UPDATE CURRENT_TIMESTAMP`
   - `CHANGE COLUMN`

3. DML / 查询写法偏 MySQL
   - `ON DUPLICATE KEY UPDATE`
   - `VALUES(column)` 用在 upsert 更新子句里
   - `SHOW COLUMNS`
   - `information_schema.TABLES/COLUMNS`
   - `LIMIT n` 和 `LIMIT offset,count`
   - `NOW()`

## 高风险位置

- `db.py`
  - 建表 SQL 全集中在 `CREATE_TABLE_SQL_LIST`。
  - schema 自检使用 `information_schema`。
  - 列迁移使用 `ALTER TABLE ... CHANGE COLUMN`。

- `webapi/background_jobs.py`
  - 后台任务持久化使用 `ON DUPLICATE KEY UPDATE`。

- `services/model_update/analysis/*`
  - 多个服务使用 `ON DUPLICATE KEY UPDATE`、`LIMIT`、`NOW()`。

- `services/model_update/importers/*`
  - 导入 BDF/OP2/UNV 数据时大量使用 MySQL upsert。

- `tools/sqls/参数灵敏度响应.sql`
  - 手工查询里使用反引号和 `LIMIT 0,1000`。

## 建议替换方向

- `AUTO_INCREMENT`：达梦使用 `IDENTITY` 或序列。
- `ON DUPLICATE KEY UPDATE`：达梦使用 `MERGE INTO`。
- `JSON`：如果只存 JSON 字符串，优先用 `CLOB`/大文本字段。
- `TINYINT(1)`：用 `SMALLINT` 或 `NUMBER(1)`。
- `information_schema` / `SHOW COLUMNS`：改查达梦系统视图，如 `ALL_TAB_COLUMNS`。
- `LIMIT`：优先改为 `FETCH FIRST n ROWS ONLY` 或达梦兼容分页写法。
- `NOW()`：改为 `CURRENT_TIMESTAMP`。

## 数据一致性验证脚本

- MySQL 快照：`tools/db_compare_mysql.py`
- 达梦快照与比较：`tools/db_compare_dm.py`

这两个脚本只读数据库，不改主程序、不建表、不写业务数据。
