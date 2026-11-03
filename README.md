# herdr-4-codexcloud

面向 Dot、Mac 项目 Lead 与机器／Cloud Worker 的协作工具与文档。提供 Worker／角色库、组织与 Projects 技能，以及由 Lead 显式调用的 Herdr 执行适配器。

## 使用入口

| 要做什么 | 直接阅读 |
| --- | --- |
| 查已交付、已部署和未验范围 | [当前结论](docs/status.md#当前结论) · [后续缺口](docs/status.md#后续缺口) |
| 查 G8 项目协调员 | [职责与入口](docs/project-coordinator.md#3-谁维护什么) · [本机交付与运行](docs/project-coordinator.md#7-本机交付与运行) |
| 理解分工与组件 | [架构总图](docs/architecture.md#整套协作分工) · [各端组件](docs/architecture.md#各端组件清单) |
| 使用执行适配器 | [输入合同](docs/herdr-executor.md#冻结输入) · [操作](docs/herdr-executor.md#显式操作) · [安装](docs/herdr-executor.md#安装与资源) · [验收](docs/herdr-executor.md#验收记录) |
| 选择 Worker 与角色 | [离线查询与准备](docs/worker-library/README.md#离线查询与准备) |
| 准备任务与安排多项目工作 | [试点绑定](docs/pilot.md#启动前绑定) · [任务模板](docs/templates/pilot-task.md) · [迭代路线](docs/collaboration-iteration-guide.md#4-分阶段迭代) |
| 查网络、SSH 与 VPS | [网络运维](docs/networking-plan.md#日常核对与恢复入口) · [VPS 专项指引](docs/vps-control-pilot.md) |
| 核对能力与通信入口 | [能力盘点](docs/local-agent-inventory.md) · [Dot 评估](docs/dot-mac-herdr-evaluation.md) · [通信选型](docs/agent-communication.md#1-当前建议与理由) |
| 明确协作责任、操作看板 | [组织协作技能](skill/organization-collaboration/SKILL.md) · [Projects 技能](skill/github-projects-operations/SKILL.md) · [离线评测资料](docs/organization-collaboration/README.md) |
| 核对来源与历史 | [初始清单](docs/copy-manifest.json) · [增量记录](docs/copy-updates.json) · [状态沿革](docs/history/status-through-2026-11-03.md) · [验收前设计分享图](docs/share-doc/super-agent-architecture.svg) |

Task Mailbox 与 Mac 协调桥由 [CF-FE/task-mailbox-mcp](https://github.com/CF-FE/task-mailbox-mcp) 独立维护；服务运行证据见其[验证记录](https://github.com/CF-FE/task-mailbox-mcp/blob/main/docs/verification.md)。本仓不共享其他仓库的运行目录。

## 本地检查

已有 Python 3.10+ 即可，仅使用标准库，在仓库根运行：

```bash
python3 scripts/worker_library.py check
python3 scripts/check_repository.py
python3 -m unittest discover -s tests -v
```

这些检查使用离线数据和模拟接口，不启动 Herdr、连接机器或调用模型。修改前读 [AGENTS.md](AGENTS.md)；源码分类见 [skill](skill/README.md)、[mcp](mcp/README.md) 和 [plugin](plugin/README.md)。
