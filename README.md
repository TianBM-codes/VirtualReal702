# 仿真核心功能简化版

保留本机求解器调用、灵敏度分析、模型修正、变形显示、节点匹配和模态匹配，以及必要的模型/试验数据导入、结果查询与项目配置。

本分支移除远程集群对接、第三方兼容服务、独立对比示例、性能基准、历史交付材料及重复脚本。完整范围见 [核心功能清单](docs/Core-Review-Scope.md)，接口见 [核心接口索引](docs/Core-API-Index.md)。通用命名不代表不再需要外部求解运行环境，也不代表已通过保密测评。

## 运行

安装 Python 依赖：

```powershell
python -m pip install -r requirements.txt
```

参考 `service_config.example.json` 创建本机配置。外部解析提供方、求解命令及源码导出要求见 [源码适配说明](docs/Source-Delivery-Adaptation.md)。业务数据库继续支持既有配置，不修改生产数据。

```powershell
python app.py
```

Windows 默认关闭内置解析进程，在另一个终端启动：

```powershell
python src/job_runner.py
```

浏览器页面为 `/static/viewer.html` 和 `/static/modal_viewer.html`；Vue 前端位于 `viewer/`，保留三维显示及同步动画模块。静态文件目录仅为 `static/`，不发布 Python 工具源码。`src.l3.main:app` 仅供可视化服务使用，完整匹配与计算入口为 `app.py`。

## 验证与交付

按仓库约定，代理机器不运行测试。开发者在本机配置运行环境后执行：

```powershell
python -m pytest tests/ -v
```

验证完整流程：模型和试验数据导入 → 节点匹配 → 模态匹配 → 本机求解 → 灵敏度分析 → 模型修正 → 变形显示。

交付使用 `tools/export_source.py` 导出已提交源码，并提供交付目录之外的禁词策略文件。不要直接压缩工作目录或包含 `.git`、机器配置、模型数据、第三方安装目录。静态扫描不能替代功能联调或测评。

核心数据结构与设计仍以 [架构文档](ODB-Service-Architecture.md) 为准；本分支的实际功能范围以核心功能清单为准。
