# herdr-4-codexcloud

面向云端 Dot 与执行机器的 Herdr 协作环境仓库。候选路线是 **Tailscale 私有网络＋SSH＋执行机上的 Herdr server**；本仓库维护协作知识、试点指引和离线检查。

本仓库独立维护。从 `mymcp-4-codexcloud` 复制的文件已落在本仓库内，运行与检查无需原仓库。复制不继承原仓库的连接、任职、任务或验收结果。

## 阅读入口

| 要做什么 | 入口 |
| --- | --- |
| 查看当前状态、已做检查与下一步 | [本仓状态](docs/status.md) |
| 理解职责、目录和采用边界 | [架构与范围](docs/architecture.md) |
| 选择跨 Agent 通信路线与准备 core 对照试点 | [跨 Agent 通信与协作指引](docs/agent-communication.md) |
| 准备一次有明确退出条件的真实试点 | [试点指引](docs/pilot.md) · [任务交接模板](docs/templates/pilot-task.md) |
| 明确岗位、派发、可达汇报与负责人验收 | [组织协作 1.5.2](skill/organization-collaboration/SKILL.md) |
| 查询或按授权维护 GitHub Projects | [Projects 操作 1.1.0](skill/github-projects-operations/SKILL.md) |
| 核对复制路径、固定版本和 SHA-256 | [初始复制清单](docs/copy-manifest.json) · [增量复制记录](docs/copy-updates.json) |

## 本地检查

使用已有 Python 3.10+，仅需标准库，在仓库根目录执行：

```bash
python3 scripts/check_repository.py
python3 -m unittest discover -s tests -v
```

检查器验证分类、入口文档、ID 和入口符号链接；测试覆盖检查器与随附的离线迭代日期脚本。它们不连接 Herdr、Tailscale、SSH 或 GitHub。

## 目录

| 目录 | 当前用途 |
| --- | --- |
| [skill/](skill/README.md) | 两份独立复制并在本仓维护的技能源码 |
| [mcp/](mcp/README.md) | MCP 分类索引；尚无服务条目 |
| [plugin/](plugin/README.md) | 分发分类索引；尚无插件条目 |
| [docs/](docs/architecture.md) | 范围、来源、试点和本仓验证记录 |
| `scripts/`、`tests/` | 离线检查及其行为测试 |

技能文件可按路径显式读取；原生安装、自动发现和目标宿主的工具连接需要分别验证。开始修改前读取 [AGENTS.md](AGENTS.md)。
