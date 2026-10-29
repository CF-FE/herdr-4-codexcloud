# Herdr 限定试点指引

本页准备一条“云端 Dot 经 Tailscale＋SSH，在真实执行机完成可追溯任务”的验证路径。初始化未执行这条路径；实际进度统一见[本仓状态](status.md)。

## 启动前绑定

选择一台已获授权的执行机和非生产目录，用[任务模板](templates/pilot-task.md)关联现有任务。复用[组织上下文与汇报](../skill/organization-collaboration/references/context-and-reporting.md)、[规划与进度](../skill/organization-collaboration/references/planning-and-progress.md)中的有效责任和授权，不为读取或实现步骤重复建单。

执行前必须明确：

- 实际派发者、执行者、当前资产写者、汇报对象、验收者及正式记录入口。
- 任务输入的固定版本、产物、允许写入路径、验收条件、预算及停止/接手条件。
- 执行机 OS、SSH 目标及用户、已核 host key、Tailscale 连接方式和允许访问范围。
- Herdr 客户端、远端安装和运行中 server 的实际版本/兼容性，以及准确的已保存机器 profile 和 session。
- Agent 真实可执行文件、启动参数、供应商、模型、认证来源与权限。凭证只引用安全配置位置，不写入本模板。

win-work 既有 `codex over Claude` 是需要保持的配置约束；真实映射尚未核实。不能仅凭 `--kind codex` 宣称等价，也不能静默换用默认 Codex。Herdr 的 `--kind` 选择识别类型及其规范可执行文件，需按实际安装核对。[Agent 启动说明](https://herdr.dev/docs/agent-automation/#agent-identity-and-launch)

## 逐层验证

| 步骤 | 要取得的证据 | 不足时的处理 |
| --- | --- | --- |
| 1. 网络与身份 | Dot 到目标的实际连通、访问策略、SSH 用户与 host key | 保留具体失败，只暂停依赖此连接的动作 |
| 2. 执行环境 | 目标目录、CLI/运行中 server、配置映射与权限范围 | 不自动安装、升级或替换 server |
| 3. 单次派发 | 唯一任务引用、输入版本、目标 profile/session、返回的 workspace/pane/Agent 引用 | 返回不明先按原引用对账，不重发 |
| 4. 观察与交付 | 原生状态、实际产物路径/版本/摘要、与本次输入的对应关系 | 状态稳定或输出出现只作观察证据 |
| 5. 观察中断恢复 | 控制连接中断后沿原执行查询，核原进程与产物 | 不启动第二个 Agent 来冒充恢复 |
| 6. 复核与关闭 | 作者自检、约定复核、负责人绑定版本的验收回执 | 必验未验或产物不符保持未通过 |

步骤 3—6 只在试点任务已获得相应执行授权后开展。完成后比较派发/观察操作数、人工介入、恢复步骤与交付耗时，和同口径的原路线比较；没有基线时保留 UNKNOWN，不编造提效比例。

## Tailscale 与 SSH

公子已说明云端可安装 Tailscale；本任务尚无实际入网或 SSH 连通证据。

Tailscale 网络与 Tailscale SSH 分开核验。Windows 可选私网中的原生 OpenSSH；当前 Tailscale SSH 服务端仅支持 Linux 和指定的 macOS CLI 安装方式。[Tailscale SSH 限制](https://tailscale.com/docs/features/tailscale-ssh#limitations)

云端若使用用户态网络，`tailscaled` 提供 SOCKS5/HTTP 代理；不能假定普通 OpenSSH 自动使用该代理。按目标环境验证实际 SSH 路径，不静默改网络模式、代理、访问策略或端口。[用户态网络说明](https://tailscale.com/docs/concepts/userspace-networking)

## Herdr 目标选择

官方 CLI 支持已保存机器的远端 API 转发。`--machine` 接受准确 profile ID 或唯一且大小写匹配的 label，不能直接把任意 SSH hostname 当 profile。界面中选中的机器不会改变 CLI 路由；省略前缀仍沿 Local/session/socket。[CLI 远端选择](https://herdr.dev/docs/cli-reference/#saved-ssh-machines)

已确认目标与读取授权后，可按实际版本核对以下观察命令；本次初始化未执行它们。`PROFILE_ID` 必须来自真实列表，不能预测。

```bash
herdr --version
herdr machine list --json
herdr machine status "$PROFILE_ID" --json
herdr --machine "$PROFILE_ID" pane list
herdr --machine "$PROFILE_ID" agent list
```

`machine add` 会检查安装、启动请求的 server，也可能提出安装或替换运行中 server；不是只读探针。替换会结束其 pane 进程，须按本次任务范围处理。[机器连接与设置](https://herdr.dev/docs/connecting-machines/)

创建命令返回的 ID 必须原样保存；Agent 名称是当前存活进程的别名，不是永久执行标识。Dot 的外部协调采用获授权 CLI/SSH 入口，不伪造 `HERDR_ENV` 或 pane 环境。

## 等待、未知与恢复

`agent prompt --wait` 不跟踪单个 turn，已有活动回合结束也可能满足等待；`idle/done` 表示可接收输入，`blocked` 需要检查实际交互，`unknown` 不证明完成。因此回执必须关联本次任务、输入和产物，不仅记录 UI 状态。[状态与等待语义](https://herdr.dev/docs/agent-automation/#choose-the-control-surface)

超时或断线后先查询原执行、原产物与已发生效果。未查清前不自动重派、不转到 Lody，也不清除旧引用。需要后续新执行时，先明确原执行处置、资产写权和新的有效授权。

普通 detach 可保留运行中的进程；server 或主机重启后，原 pane 进程不保留。布局恢复、原生 Agent 会话续接与同一进程存活分别核验；headless 自动续接及 Dot 后台生命周期尚待实测。[会话恢复边界](https://herdr.dev/docs/session-state/)

## 退出与采用

只对这一台机器、这一任务及实际版本形成试点结论。通过后再明确该项目哪些新任务采用 Herdr；旧 Lody 任务仍沿原引用结束。试点不合适时，后续新任务可继续原路线，已发出的 Herdr 请求先完成对账。

正式结果由原项目负责人验收，记录写入该任务的现行入口并回读；初始化不创建或迁移正式 Project。操作约束沿[Projects 技能](../skill/github-projects-operations/SKILL.md)。

本页官方来源于 2026-10-29 核对；文档描述不代表目标端安装了相同能力。真实试点以记录的二进制、运行中 server、当前帮助和执行回执为准。
