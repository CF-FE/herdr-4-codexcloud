# Herdr 限定试点指引

修订日期：2026-11-03。本页按已确定的 **Mac 本地 Herdr 原生 saved machine／`--machine` → LAN／SSH → Win 既有 `collab`** 主链路准备验证；Dot 全局协调，Mac 的 Herdr 托管 Codex／Kimi 项目 Lead，Cloud 沿独立获准入口执行。VPS 继续承载 FastMCP／按需 ngrok／Gitee 工具服务及旧 profile／在途任务，不是机器任务必经层。实际采用边界见[本仓状态](status.md)，信箱／桥的运行明细沿其权威记录。

公子明确 Mac 会一直在线，作为本方案的设计与部署前提；Win 的代理分支可依赖 Mac 上的 CLI Proxy，官方 Kimi 入口按自身配置使用。当前试点不要求 Mac 离线后的备用入口、接管或独立推理，也不以未测离线作为阻塞；这一前提不冒充连续在线可用性实测。

公子已选择 **Herdr TUI（`herdr` CLI／server）**，替换 2026-10-30 [通信指引](agent-communication.md)的 core 优先建议；本轮不要求安装 hc／Herdr App 或比较两栈。本页命令和恢复语义只适用于 TUI，不能直接换成 hc 命令或套用 core 生命周期。

此前网络 Goal 已建立 VPS／Win EasyTier 2.6.4，限定回程、SSH 和网络服务重建后的自动转发恢复均已验证；Mac 不装 VPN。旧 VPS profile 的 94 字节小文件及双窗格交叠输出也已核，模型调用为 0。它们只覆盖旧入口，不能转记为 Mac 原生新入口的执行证据。

本轮 Mac 原生连接配置与只读验收已通过主会话直接回读：`machine add` 退出 0，machine 清单由 0 变为 1，保存 label `win-work`、session `collab`、enabled=true；按真实返回 ID 查询 status 为 reachable，`herdr --machine` 的 Agent 查询退出 0、`result.agents=[]`。预检的 `collab` 已运行、default 停止，本轮未启动或替换 server／session。生产写控制未移交，未提交任务或模型 prompt、启动 Agent 或操作 focused；原 VPS profile／在途执行保留。普通 SSH 预检不替代上述原生回执，空 Agent 清单不构成失败，Dot、模型、业务、正式写权及恢复另验。

## Dot 原生入口与 Task Mailbox 分别验收

Dot 原生 Mac 任务的 E0—E5 沿[原限定需求](dot-mac-herdr-evaluation.md#4-有界测试步骤与通过条件)核身份、准确委派、Herdr／Lead 交互和原任务回收；新增信箱不是该入口已通过的替代证据，也不授权超时后重派或换路。

数据支路为 **Dot ↔ ngrok HTTPS ↔ Caddy 的信箱路由 ↔ VPS 持久 Task Mailbox ↔ Mac 协调桥**，Codex／Kimi 独立 MCP 客户端各自另核。桥已常驻主动收件、会话感知与显式结果补传；桌面 Lead 显式调用独立[Herdr 执行适配器](herdr-executor.md)定向 Mac 既有 Codex，本次一次 prompt、真实产物、collect／Lead release 及原 review 读取／ACK 已工程限定通过。信箱与桥本身不执行 Herdr／模型，自动消费／无人值守派发未实现；Win／Cloud、Kimi、Dot 新派任务与生产业务不继承该回执。

Mac 桥迁移基线依据服务仓固定文档 `ca8a480`；原数据任务由桌面云端 App 发起、Mac 回传并由原桌面会话读回，指定 Dot 读取另按公子回报及服务精确 ACK 回读记录。本次适配器链路由桌面 Lead 承接，真实自然 Stop 后原 collector 上送同一结果 v1，原 review 读全核摘要并 ACK，本地已收取该 ACK；具体证据统一见[适配器验收](herdr-executor.md#验收记录)及[服务权威记录](https://github.com/CF-FE/task-mailbox-mcp/blob/main/docs/verification.md)。反向新任务与长期稳定性未验，瞬态 CLIENT_OPEN 故障根因仍 UNKNOWN；本次文档收尾不重复调用或改连接。

Codex Cloud 也可作为条件客户端：只有对应云任务实际具备信箱 MCP 工具、OAuth 身份与动作权限时，才直接访问同一公网 HTTPS 端点，不必经 Dot／Mac 转述或连接 VPN。该路径目前未验，不将 Cloud 的额外接入作为本次 Dot ↔ Mac 数据闭环前置；Dot 插件创建及 Mac CLI 配置不证明 Cloud 已接入。信箱提交不会创建云任务或唤醒 Worker，Cloud 仍沿原生任务入口和成果验收运行；条件依据见[架构信箱章节](architecture.md#task-mailbox持久收发与本地承接)。

按原任务有效授权分别保存以下证据，不因本文新增测试或部署：

| 层 | 需要取得的证据 | 不能替代的下一层 |
| --- | --- | --- |
| B0 身份与真实工具 | 按本轮选用的 Dot／专用 Mac 协调桥入口分别核 OAuth 身份、实际工具和必要动作；Codex／Kimi 原 CLI 与 Cloud 具体任务另列，不作专用桥的前置；Gitee 继续原认证 | 共享 HTTPS 路由或配置模板不等于客户端已登录／获权；桥成功也不代验其他客户端 |
| B1 Dot → Mac 数据闭环 | 新的稳定 `job_id`、`payload_hash` 关联原任务；Dot 提交随机片段，Mac 主动读到对应内容，再发布变换结果；Dot 读取精确结果版本／摘要并确认 | 工具接受提交、持久入库不代表 Mac 已读取，更不代表 Herdr 执行 |
| B2 Mac → Dot 数据闭环 | 交换方向，绑定另一 job 与 nonce；各侧实际调用及结果读取分别留证，不用模拟客户端或本机会话替代 Dot | 一向通过不推出双向通过；读确认不等于业务验收或写权交还 |
| B3 获准本地执行承接 | 桌面 Lead 显式调用适配器 → Mac 既有 Codex 一次执行 → collect／Lead release → 原 Stop／collector → 原 review 读取及 ACK，本次工程限定通过；新任务仍核自身授权与准确目标 | 不将反向数据验证设为独立获准执行的前置；不代验 Dot 原入口／Kimi／Win／Cloud、自动派发或生产业务，详见[规范](herdr-executor.md) |

同 ID 同内容先查原记录，内容冲突明确停止；结果保持不可变版本与摘要，`ack_result_read` 仅确认准确版本已读，`request_cancel` 仅登记请求。超时或 UNKNOWN 沿原 job、原执行和当前写者对账，不切换 Dot 原生入口或重复投递。大产物保留固定引用，正式成果仍由有权负责人验收。

服务、Mac 桥、客户端模板与部署唯一维护于外部 [CF-FE/task-mailbox-mcp](https://github.com/CF-FE/task-mailbox-mcp)，个人整合仓仅为历史来源；本仓不复制或共享运行库。本页占位端点为 `https://mcp.example.com/mailbox/mcp`。具体服务契约及运行证据由该仓维护，本仓只记录[架构边界](architecture.md#task-mailbox持久收发与本地承接)及[Herdr 当前采用](status.md)。

## 官方操作技能与外部协调

2026-11-02，按公子要求将[官方 Herdr skill](https://github.com/herdrdev/herdr/blob/ee5bfb5041bb3e4a49d591c72308aae3c81ecdde/skills/herdr/SKILL.md)原样安装到 mac-work 当前 Codex 用户的全局 `~/.codex/skills/herdr/`，已由后续桌面会话的技能目录列出并按路径读取。固定提交为 `ee5bfb5041bb3e4a49d591c72308aae3c81ecdde`，15,171 字节，SHA-256 为 `fbd9d502767f7dff29c211505a0bd9ba34e980ed4cc456247e2adc1642883984`；与本机 TUI 0.9.5 的 `herdr --skill` 输出逐字节一致，来源收据随全局副本保管。安装和实际技能发现分别取证，新 CLI 的完整运行时技能清单仍按各自实际发现取证。

该官方技能用于 **Herdr 管理窗格内的 Agent**，要求真实的 `HERDR_ENV=1`；全局安装不取消其范围，不设置或伪造该变量及 pane 身份。当前项目 Lead 采用 Mac Herdr 内的 Codex／Kimi CLI，机器主链路使用 Mac 本地 saved machine／`--machine` 直连 Win，不经 VPS 控制层。外部桌面任务如何定向操作这些 Lead 仍按有效授权与明确 session／pane 接线，不把安装技能当外部控制权限。全局副本只对当前机器／账号生效，不由仓库同步到其他机器或 Cloud。VPS 不运行 Agent，无需额外安装技能；Win 端需要自动发现时再按对应 Agent 账号目录准备。Kimi 默认扫描路径不含 Codex 用户技能目录，不能宣称已自动加载。

## 启动前绑定

选择一台已获授权的执行机和非生产目录，用[任务模板](templates/pilot-task.md)关联现有任务。复用[组织上下文与汇报](../skill/organization-collaboration/references/context-and-reporting.md)、[规划与进度](../skill/organization-collaboration/references/planning-and-progress.md)中的有效责任和授权，不为读取或实现步骤重复建单。

执行前必须明确：

- 实际派发者、执行者、当前资产写者、汇报对象、验收者及正式记录入口。
- 任务输入的固定版本、产物、允许写入路径、验收条件、预算及停止/接手条件。
- 执行机 OS、SSH 目标及用户、已核 host key、Mac → Win 的实际 LAN／SSH 身份及允许访问范围；VPS 旧支路的身份和原执行分开保留，不为普通续查重复确认已有授权。
- Herdr 客户端、远端安装和运行中 server 的实际版本/兼容性，以及准确的已保存机器 profile 和 session。
- Agent 真实可执行文件、启动参数、供应商、模型、认证来源与权限。凭证只引用安全配置位置，不写入本模板。

Win 后续试点范围先从 OMP、Hermes、Kimi 中选择一个明确入口；Windows Codex 按公子要求暂不纳入配置、验证或调度。已有[原生 CLI 执行侧结果](local-agent-inventory.md#7-win-work-harness执行侧交接)不代替 Herdr 托管验收；Mac Codex 项目协调层保持独立。Herdr 的 `--kind` 选择识别类型及规范可执行文件，不证明供应商、模型、认证与权限等价，仍需按实际安装和任务入口核对。[Agent 启动说明](https://herdr.dev/docs/agent-automation/#agent-identity-and-launch)

## 逐层验证

| 步骤 | 要取得的证据 | 不足时的处理 |
| --- | --- | --- |
| 1. 网络与身份 | 分别核 Dot 原生入口、Mac → Win LAN／SSH 身份与 host key；Mac 本地原生 machine／status 查询另取证 | 普通 SSH 加 Win CLI 不代替 Mac 原生机器连接验收 |
| 2. 执行环境 | 准确 profile 与 Win 已运行的 `collab`、CLI／server 版本、原生 pane／Agent 清单及权限范围 | 不启动默认 session、Agent 或替换 server；空 Agent 清单有效 |
| 3. 单次派发 | 唯一任务引用、输入版本、目标 profile/session、返回的 workspace/pane/Agent 引用 | 返回不明先按原引用对账，不重发 |
| 4. 观察与交付 | 原生状态、实际产物路径/版本/摘要、与本次输入的对应关系 | 状态稳定或输出出现只作观察证据 |
| 5. 结果不明时对账 | 请求超时或连接中断时沿原执行查询，核原状态、写者与产物 | 保留 UNKNOWN，不重派或启用第二个 Agent；不为本轮主动制造断网 |
| 6. 复核与关闭 | 作者自检、约定复核、负责人绑定版本的验收回执 | 必验未验或产物不符保持未通过 |

本轮只开展连接配置与步骤 1—2 的只读核验；步骤 3—6 的实际派发、产物及业务验收另按任务授权执行，不因连接通过自动移交写控制。后续完成任务闭环时再比较操作数、人工介入、恢复步骤与交付耗时；没有基线时保留 UNKNOWN，不编造提效比例。

## 当前网络与 SSH

Mac 原生 Herdr 直接沿 LAN／SSH 访问 Win `collab`，不通过 VPS、MCP 或新桥接。独立网络服务保持原状：EasyTier 仅部署 VPS／Win；Win 只宣告 Mac 主机 `/32` 回程，Mac 保留到 VPS 的持久主机路由，默认出口与 DNS 不变。它不是 Mac 直连 Win 的前置，也没有发布整个 LAN。网络范围与策略见[独立组网方案](networking-plan.md)，SSH 组件归属见[架构组件清单](architecture.md#各端组件清单)。

EasyTier 不替代 Win 的 OpenSSH Server，也不提供任务或文件权限。Windows 的 Herdr 采用完整发行包，保留 `herdr.exe` 与包内 ConPTY 运行文件；实际 Agent、shell、Git／脚本依赖分别核验，不默认改成 WSL。[Windows 包说明](https://herdr.dev/tui/docs/install/#windows-archive)

早期“云端经 Tailscale＋SSH”是历史候选；当前只在 Win 启用公子明确要求的限定子网代理，不让 Mac 或 Codex Cloud 入 VPN。新增机器、额外代理／服务端口和真实业务派发仍按对应任务的有效授权执行。

## Herdr 目标选择

官方 CLI 支持已保存机器的远端 API 转发。`--machine` 接受准确 profile ID 或唯一且大小写匹配的 label，不能直接把任意 SSH hostname 当 profile。界面中选中的机器不会改变 CLI 路由；省略前缀仍沿 Local/session/socket。[CLI 远端选择](https://herdr.dev/docs/cli-reference/#saved-ssh-machines)

以下原生观察命令均在 **mac-work 的本地 Herdr CLI** 执行。先读取本地 machine 清单，`PROFILE_ID` 只能使用本次真实返回、准确指向 Win `collab` 的 ID；不把 SSH 别名、VPS 的旧 profile ID 或 focused 选择当作该目标。不经 SSH 到 Win 后再运行同名 CLI 来替代这条验收。

```bash
herdr --version
herdr machine list --json
herdr machine status "$PROFILE_ID" --json
herdr --machine "$PROFILE_ID" pane list
herdr --machine "$PROFILE_ID" agent list
```

本轮保存连接须明确复用现有程序及已运行 `collab`。`machine add` 本身可能安装、启动或升级远端程序，不是只读探针；若提出替换 server、启动其他 session 或扩大目标范围，不执行该扩展。只保存并查询准确机器，不启动 Agent 或输入业务命令；后台重连仅发现已有安装。[机器连接与设置](https://herdr.dev/tui/docs/connecting-machines/)

machine／status 的目标、reachable、session 和 pane／Agent 清单要关联同一真实 profile；`agent list` 返回空表不构成失败，不为补齐清单创建 Agent。配置文件写入、SSH 预检成功或命令退出 0 均不足以单独证明这条原生读取完成。后续创建命令返回的 ID 必须原样保存，Agent 名称不是永久执行标识；不伪造 `HERDR_ENV`、pane 环境或使用 focused 快捷目标。

历史 VPS profile 注册时，外层脚本因 Windows stderr 编码未取得原退出码；随后沿原列表／目标 session 对账，确认保存和可达后才继续，没有再次注册。该回执仅归旧 VPS 入口，不能用于证明 Mac 新 profile 已保存；输出采集失败不能推断远端无效果。

各 CLI 的输出格式分别处理：本版本 `pane read` 返回终端文本，不能把所有命令统一当 JSON。历史 VPS 小任务另用独立整行标记、`wait-output`、原 pane 回读及同一 Win 时钟的时间戳核交叠；这不属于本轮 Mac 只读验收。`pane run` 仅提交输入，不把回显或等待返回当完成；本轮不执行此类写操作。

## 等待、未知与恢复

`agent prompt --wait` 不跟踪单个 turn，已有活动回合结束也可能满足等待；`idle/done` 表示可接收输入，`blocked` 需要检查实际交互，`unknown` 不证明完成。因此回执必须关联本次任务、输入和产物，不仅记录 UI 状态。[状态与等待语义](https://herdr.dev/docs/agent-automation/#choose-the-control-surface)

超时或断线后先查询原执行、原产物与已发生效果。未查清前不自动重派、不转到 Lody，也不清除旧引用。需要后续新执行时，先明确原执行处置、资产写权和新的有效授权。

普通 detach 可保留运行中的进程；server 或主机重启后的进程、布局与 Agent 会话是不同状态，不能用布局恢复冒称原执行继续。既有网络恢复记录保留原范围，本轮不新增重启、断网、备用入口或接管测试；当前任务仍按真实结果和原生引用对账。[会话恢复边界](https://herdr.dev/docs/session-state/)

## 退出与采用

只对这一台机器、这一任务及实际版本形成试点结论。通过后再明确该项目哪些新任务采用 Herdr；旧 Lody 任务仍沿原引用结束。试点不合适时，后续新任务可继续原路线，已发出的 Herdr 请求先完成对账。

正式结果由原项目负责人验收，记录写入该任务的现行入口并回读；初始化不创建或迁移正式 Project。操作约束沿[Projects 技能](../skill/github-projects-operations/SKILL.md)。

本页原 CLI／恢复来源于 2026-10-29 核对，选型与安装边界于 2026-11-02 修订，2026-11-03 仅对齐信箱归属与桥接入状态；官方文档描述不代表目标端运行了相同能力。真实试点以记录的二进制、运行中 server、当前帮助和执行回执为准。
