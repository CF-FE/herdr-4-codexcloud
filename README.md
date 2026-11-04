# herdr-4-codexcloud

Super-Agent 的需求入口由公子与云端Dot组成，下游分为项目协调层和 Worker 执行层。云端Dot交办和汇报，项目负责人或另行指定的验收者决定成果是否放行。[全局架构图](docs/architecture.md#整套协作分工) · [协作规范](docs/coordination-policy.md#1-默认分工与取舍)

## 使用入口

| 要做什么 | 直接阅读 |
| --- | --- |
| 查已交付、已部署和未验范围 | [当前结论](docs/status.md#当前结论) · [后续缺口](docs/status.md#后续缺口) |
| 决定云端Dot直派还是协调层承接 | [分工与取舍](docs/coordination-policy.md#1-默认分工与取舍) · [小迭代边界](docs/coordination-policy.md#3-云端dot小迭代直派) · [协调员指挥 Cloud 的目标合同](docs/coordination-policy.md#4-协调员指挥-cloud-路由) |
| 核对角色称呼 | [云端Dot与历史称呼](docs/coordination-policy.md#全局角色名称映射) |
| 查每个角色在哪里运行、怎样创建和调用 | [组织岗位、专业角色与 Worker 的宿主和调用示例](docs/agent-hosts.md) |
| 查云端Dot、协调员、项目负责人和 Worker | [任务归属](docs/project-coordinator.md#3-谁维护什么) · [生命周期](docs/project-coordinator.md#agent-lifecycle) · [Hooks 与适用范围](docs/project-coordinator.md#lifecycle-hooks) · [本机交付](docs/project-coordinator.md#7-本机交付与运行) |
| 交办完整目标并沿原任务回报 | [目标交办合同与准入](docs/goal-handoff.md) |
| 保留原线程并使用受限组织工具 | [目标运行入口](docs/goal-runtime.md) · [组织 MCP](mcp/project-coordinator-tools/README.md) |
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
