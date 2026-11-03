# herdr-4-codexcloud

面向多项目的协作环境仓库：**Dot ↔ VPS Task Mailbox ↔ Mac 协调桥（Mac Coordination Bridge）→ 按授权承接 Herdr 内项目 Lead（Codex／Kimi CLI）→ 原生 saved-machine／LAN SSH → Win Herdr**。桥已在 Mac 常驻收件、记录会话信号并补传结果；原数据任务的交付与读取已有分层回执，Herdr 自动执行 handler、反向新任务及业务验收仍未通过，不能把图中的承接目标当作已执行。Mac → Win 的原生连接与只读查询已取得限定回执，旧 VPS Herdr 绑定和在途引用保留。**Codex Cloud 也可在任务实际具备获准 MCP 工具和认证时直接收发同一信箱**，其任务创建与生命周期仍沿原云入口；Dot 原生 Mac 任务另保留，未知执行不换通道重派。VPS 同时保留 Gitee MCP，HTTPS 经 ngrok／Caddy，各服务认证独立。实际采用边界见[本仓状态](docs/status.md)。

Task Mailbox 的代码、文档、配置、Skill／插件与 Issue 唯一维护于 [CF-FE/task-mailbox-mcp](https://github.com/CF-FE/task-mailbox-mcp)，个人 `create-network-sh` 仅保留历史整合来源。服务和 Mac 桥的逐项运行证据以该仓[验证记录](https://github.com/CF-FE/task-mailbox-mcp/blob/main/docs/verification.md)为准；本仓只维护 Herdr 采用边界，不复制服务或建立跨仓运行依赖。

Dot 与具备获准 Pages 工具的 Cloud Worker 可直接参与 Space 协作，各机器保管本地资产；源码沿 Git 固定版本，大产物不存 VPS 信箱。当前采用 TUI 的 `herdr`，不部署 hc／Herdr App。VPS ↔ Win 的 EasyTier 私网已建立；Mac 不装 VPN，经 Win 网关与专用路由管理 VPS，信箱客户端使用公网 HTTPS，Cloud 不需要为信箱加入 VPN。Win 本轮仅纳入 OMP／Hermes／Kimi，Windows Codex 暂不考虑；原生短对话报告不等于 Herdr 派发或业务验收。

本仓库独立维护。从 `mymcp-4-codexcloud` 复制的文件已落在本仓库内，运行与检查无需原仓库。复制不继承原仓库的连接、任职、任务或验收结果。

当前按公子确认的 **Mac 持续在线** 前提部署协调层和 CLI Proxy，Win 的代理入口可复用该服务；不建设 Mac 离线接管或备用推理，也不追加离线验收。

## 阅读入口

| 要做什么 | 入口 |
| --- | --- |
| 查看当前状态、已做检查与下一步 | [本仓状态](docs/status.md) |
| 按任务组合 Worker、后端与专业角色，准备原生派发草稿 | [Worker 与角色库](docs/worker-library/README.md) · [离线选型入口](docs/worker-library/README.md#离线查询与准备) |
| 查 Mac 能力及 Win OMP／Hermes／Kimi 的执行侧结果 | [Agent 与能力盘点](docs/local-agent-inventory.md) |
| 转给 Dot 评估 Mac → Herdr → 项目协调层 | [限定评估需求](docs/dot-mac-herdr-evaluation.md) |
| 查看 VPS 信箱接入进展及其与 Herdr 的分工 | [当前接入快照](docs/status.md#vps-任务信箱与客户端接入进行中) · [信箱验收分层](docs/dot-mac-herdr-evaluation.md#8-新增信箱链路分开验证不自动执行) |
| 理解职责、目录和采用边界 | [架构与范围](docs/architecture.md) |
| 按机器查要装什么、用途与部署顺序 | [三机部署表](docs/architecture.md#三机部署速查) · [组件图](docs/architecture.md#三机组件与调用图) |
| 查询 mac-work、VPS、Cloud 与 win-work 的详细组件 | [各端组件清单](docs/architecture.md#各端组件清单) |
| 查 Windows SSH 公钥免登教程与账户适配说明 | [SSH 依赖与免登参考](docs/architecture.md#ssh-依赖与已有组件复用) |
| 准备已选 EasyTier 网络与验收，查早期对照 | [独立组网方案](docs/networking-plan.md) |
| 整合 Worker、协作空间、本地资产并分阶段验证 | [多项目协作迭代指引](docs/collaboration-iteration-guide.md) |
| 查 VPS MCP 服务定位与既有控制支路 | [VPS 方案](docs/vps-control-pilot.md) |
| 查看跨 Agent 通信研究、当前 TUI 选择与 core 历史对照 | [跨 Agent 通信与协作指引](docs/agent-communication.md) |
| 准备一次有明确退出条件的真实试点 | [试点指引](docs/pilot.md) · [任务交接模板](docs/templates/pilot-task.md) |
| 明确岗位、派发、可达汇报与负责人验收 | [组织协作 1.5.2](skill/organization-collaboration/SKILL.md) |
| 按需评阅协作情境与查看旧路线交接边界 | [组织评测与资料接收](docs/organization-collaboration/README.md) |
| 查询或按授权维护 GitHub Projects | [Projects 操作 1.1.0](skill/github-projects-operations/SKILL.md) |
| 核对复制路径、固定版本和 SHA-256 | [初始复制清单](docs/copy-manifest.json) · [增量复制记录](docs/copy-updates.json) |

## 本地检查

使用已有 Python 3.10+，仅需标准库，在仓库根目录执行：

```bash
python3 scripts/worker_library.py check
python3 scripts/check_repository.py
python3 -m unittest discover -s tests -v
```

检查器验证分类、入口文档、ID 和入口符号链接；Worker 库检查另核两个注册表的版本与角色资源引用。测试覆盖本仓离线行为，它们不连接 Herdr、EasyTier、Tailscale、SSH 或 GitHub，也不创建 Worker、提交信箱或调用模型。

## 目录

| 目录 | 当前用途 |
| --- | --- |
| [skill/](skill/README.md) | 两份独立复制并在本仓维护的技能源码 |
| [mcp/](mcp/README.md) | MCP 分类索引；尚无服务条目 |
| [plugin/](plugin/README.md) | 分发分类索引；尚无插件条目 |
| [docs/](docs/architecture.md) | 范围、来源、试点和本仓验证记录 |
| `scripts/`、`tests/` | 离线检查及其行为测试 |

技能文件可按路径显式读取；原生安装、自动发现和目标宿主的工具连接需要分别验证。开始修改前读取 [AGENTS.md](AGENTS.md)。
