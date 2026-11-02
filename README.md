# herdr-4-codexcloud

面向多项目的协作环境仓库：**Dot 全局协调 → mac-work 项目 Lead → VPS／hc 轻控制 → 执行机**；Codex Cloud 沿获准云任务入口独立执行。Dot 与实际提供获准 Pages 工具的 Cloud Worker 可直接参与 Space 协作，各机器保管本地资产。本仓维护候选架构、组件清单、资源整合与离线检查，尚未部署或接通新链路。

本仓库独立维护。从 `mymcp-4-codexcloud` 复制的文件已落在本仓库内，运行与检查无需原仓库。复制不继承原仓库的连接、任职、任务或验收结果。

## 阅读入口

| 要做什么 | 入口 |
| --- | --- |
| 查看当前状态、已做检查与下一步 | [本仓状态](docs/status.md) |
| 理解职责、目录和采用边界 | [架构与范围](docs/architecture.md) |
| 查询 mac-work、VPS、Cloud 与 win-work 的组件 | [各端组件清单](docs/architecture.md#各端组件清单) |
| 比较 EasyTier／Tailscale，准备三机网络与验收 | [独立组网方案与选型对照](docs/networking-plan.md) |
| 整合 Worker、协作空间、本地资产并分阶段验证 | [多项目协作迭代指引](docs/collaboration-iteration-guide.md) |
| 准备 Mac Lead → VPS 轻控制 → 执行机的分阶段接入 | [VPS 控制端试点方案](docs/vps-control-pilot.md) |
| 选择跨 Agent 通信路线与准备 core 对照试点 | [跨 Agent 通信与协作指引](docs/agent-communication.md) |
| 准备一次有明确退出条件的真实试点 | [试点指引](docs/pilot.md) · [任务交接模板](docs/templates/pilot-task.md) |
| 明确岗位、派发、可达汇报与负责人验收 | [组织协作 1.5.2](skill/organization-collaboration/SKILL.md) |
| 按需评阅协作情境与查看旧路线交接边界 | [组织评测与资料接收](docs/organization-collaboration/README.md) |
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
