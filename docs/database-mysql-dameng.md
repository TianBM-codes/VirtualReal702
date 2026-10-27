# MySQL / 达梦业务数据库配置与适配

`service_config.json` 的 `DB_DIALECT` 选择业务库：`mysql`（默认）或 `dm`。
修改后需要重启后端进程。每个进程只连接一种业务库，不会同时写入两边。
ODB 工作区的 `manifest.db` 和任务注册库 `registry.db` 继续由 SQLite 管理。

## 配置

本机 `service_config.json` 已加入达梦连接信息，默认仍为 `mysql`。
该文件原本就在 `.gitignore` 中，含真实密码，不提交到仓库。
仓库内的 `service_config.example.json` 提供无密码示例，方便其他部署环境配置。

| 配置项 | 使用场景 | 默认值 |
| --- | --- | --- |
| DB_DIALECT | 选择后端数据库 | mysql |
| DB_HOST / DB_PORT / DB_USER / DB_PASSWORD | MySQL 连接 | 保留原配置 |
| DB_DATABASE / DB_CHARSET | MySQL 数据库及字符集 | 保留原配置 |
| DM_HOST / DM_PORT | 达梦地址 | localhost / 5236 |
| DM_USER / DM_PASSWORD | 达梦账号和密码 | SYSDBA / 空 |
| DM_SCHEMA | 存放业务表的模式 | DB_SIMU_REAL_TEST |
| DB_POOL_SIZE | 每个进程的连接上限 | 10 |
| DB_CONNECT_TIMEOUT | 获取连接和登录等待秒数 | 5 |
| DB_ENSURE_TABLES | 启动及首次访问时是否检查/升级表结构 | true |
| DB_INIT_ON_STARTUP | 是否启动时预热数据库 | true |

环境变量优先于 JSON。例如 `DB_DIALECT=dm` 可以覆盖文件中的 `mysql`。
MySQL 的 `DB_*` 和达梦的 `DM_*` 连接信息分别保存，切换时只需修改类型。
达梦的账号和模式是两回事：`SYSDBA` 是登录身份，业务表在
`DB_SIMU_REAL_TEST`，因此连接后还会执行 `SET SCHEMA`。

达梦环境需要安装 `dmPython` 及匹配版本的 DPI 客户端；仅使用 MySQL 的部署
不需要安装它，程序只在选择 `dm` 后加载该驱动。

## 达梦实例前提

这一版适配面向大小写不敏感的 DM8 实例（`CASE_SENSITIVE=0`），并要求
`COMPATIBLE_MODE=4`。连接时会检查这些条件及空字符串是否区别于 NULL，
不符合时明确报错，不自行修改数据库实例配置。

这是数据含义方面的要求，不只是 SQL 语法要求：MySQL 的联合唯一键中，只要
某一列是 NULL，多条记录可以共存；达梦在其他兼容模式下可能采取不同规则。
加载级中的空工况、空测量 ID 也必须保持空字符串，不能变成 NULL。

可由数据库管理员查看现有设置：

```sql
SELECT SF_GET_CASE_SENSITIVE_FLAG() FROM DUAL;
SELECT PARA_NAME, PARA_VALUE FROM V$DM_INI
WHERE PARA_NAME IN ('COMPATIBLE_MODE', 'DDL_AUTO_COMMIT');
SELECT CASE WHEN '' IS NULL THEN 1 ELSE 0 END FROM DUAL;
```

如需调整实例参数，请按达梦版本的管理要求操作；程序不负责改变全库参数。

## 请求如何执行

前端继续发送原来的 HTTP 请求。后端业务服务仍调用 `get_connection()`。
选择 MySQL 时沿用原连接池和原 SQL；选择达梦时由 `db_dameng.py` 提供连接、
游标和 SQL 适配，业务服务仍自行决定何时提交或回滚。

- `%s` 改为达梦绑定参数 `?`；值单独传给驱动，字符串正文中的 SQL 关键字不会被替换。
- 支持 LIMIT 分页、NOW()、CAST(... AS CHAR)、反引号标识符和字典结果。
- 将 SHOW COLUMNS、information_schema 的表/列/索引查询映射到达梦系统视图。
- 自增数值 ID 通过刚写入记录的 ROWID 回查，避免误把 dmPython 返回的物理行标识当成数字 ID。
- 普通批量写入使用驱动批量绑定；重复键写入逐条处理，保持调用方事务边界。

之前审计报告生成的 MERGE 是迁移候选，不作为本次运行时代码使用。
实际重复键写入采用“检查所有已声明唯一键、锁定目标行、更新或插入”的流程。
如果并发插入碰到重复键，使用保存点恢复这一条操作后再定位目标行，保留之前
同一事务中已做的工作。NULL 参与的唯一键按 MySQL 规则处理，前缀索引也保留
前缀长度。如果不同唯一键命中了两条不同的记录，明确报错，避免更新错行。
这种异常冲突的行为比 MySQL 的选择规则更严格，需要先修正输入或数据。

普通 UPDATE/DELETE 的 rowcount 使用达梦驱动返回值；upsert 的 rowcount 是
实际操作行数，不模拟 MySQL 的“更新一行记作两行”统计。当前调用方只对普通
更新/删除使用该值。重复键写入比单条 MERGE 多往返，批量结果导入应关注耗时。

## 表结构

保留 `db.py` 为业务表定义入口。达梦游标将表定义转换成 IDENTITY、CLOB、
SMALLINT、独立索引和虚拟列；引擎、字符集、MySQL COMMENT 语法不发送给达梦。
原 JSON 列同时增加 IS JSON 检查，避免 CLOB 接受 MySQL 原本会拒绝的无效 JSON。
列注释暂不复制为达梦 COMMENT ON 语句，不影响业务字段值。
索引名按表名和原名生成稳定短名称，避免达梦中不同表的同名索引冲突。
`source_file_path(255)` 对应 SUBSTR 表达式索引，保留按前缀判断重复的含义。

自动更新时间通过行触发器维护，检查实际字段变化，显式指定更新时间时保留
调用方值。启动迁移完成后会再次核对索引和触发器，以兼容旧表缺列的情况。
导入库上已有的额外触发器、约束应由管理员确认，程序不自动删除这些对象。

启用 `DB_ENSURE_TABLES` 会执行原程序已有的建表、补列、旧表迁移/清理操作，
达梦还会建立本程序管理的索引及触发器。DDL 可能隐式提交，不能依靠回滚撤销
整个表结构升级；正式部署前应先在备份库验证。结构已准备好后可设置为 false。
schema 必须预先存在；程序不会新建登录用户或替业务库创建 schema。

## 验证状态和本地命令

本次遵照仓库约定不在 Codex 环境执行测试，也未启动主程序执行业务库迁移。
静态检查不能替代真实达梦写入验证。此前报告中仅两条无参数查询一致，不能
证明全部 SQL 已经通过验证；此前两库快照也存在数据差异。

补充了离线回归测试和可选双库集成测试。开发者本地执行：

```powershell
python -m pytest tests/test_db_dameng.py -v
$env:RUN_MYSQL_DM_INTEGRATION='1'
python -m pytest tests/test_db_dameng_integration.py -v
```

双库测试从本地配置或环境变量读取凭据，只在两个库各创建一张随机名称的
`odb_verify_*` 临时验证表，并在结束时删除，不执行主程序的全库迁移。
它比较新增、重复键更新、含 NULL 的唯一键、中文大文本、显式 ID 批量写入和
回滚后的数据结果。通过后再执行完整业务测试并切换配置。

SQL 语法和接口参考：[dmPython 接口](https://eco.dameng.com/document/dm/zh-cn/pm/dmpython-interface.html)、
[达梦触发器](https://eco.dameng.com/document/dm/zh-cn/pm/trigger)、
[唯一键 NULL 语义](https://eco.dameng.com/community/question/048cafe9019434242b92595b6e3c97df)。
