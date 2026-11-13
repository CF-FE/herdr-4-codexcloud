# Herdr 限定试点指引

修订日期：2026-11-03。本文维护试点的通用准备、原生目标选择、分层验证和退出规则；每次执行只适用该任务的有效授权与预算。当前结果见[状态](status.md)，已通过的显式 Mac 首版切片见[适配器验收](herdr-executor.md#验收记录)，不在此复制运行流水。

当前选用 Herdr TUI（`herdr`），不套用独立产品 `hc` 的参数或生命周期。Mac 持续在线是已接受前提；不因开展新试点重新加入离线接管、备用推理或主机重启测试。组件与网络范围由[架构](architecture.md)和[组网方案](networking-plan.md)维护。

## Dot 原生入口与 Task Mailbox 分别验收

按本次实际采用的路径取证。独立入口的未验项只限制依赖它的工作，不要求所有方向通过后才开展一项已有授权的执行。

| 层 | 要保存的证据 | 不能据此推导 |
| --- | --- | --- |
| B0 身份与工具 | 本次选用的客户端实际身份、工具与允许动作；专用桥、Dot、Codex／Kimi原客户端及Cloud分别记录 | 共享HTTPS或桥授权成功不代表其他客户端已接通 |
| B1 发送至Mac | 实际发送者、原job／payload hash、Mac读取及结果版本、指定接收者全量回读 | 桌面发起不冒称Dot发起，入库不等于执行 |
| B2 反向数据 | 仅在任务需要时，以独立关联验证反方向发送、读取及精确ACK | 一向成功不证明双向；反向未验不阻断无依赖工作 |
| B3 显式执行与回传 | Lead核完整输入、准确原生目标，一次派发；核产物／交还与原链回传 | 信箱不自动执行；Mac切片不代验Kimi、Win／Cloud或生产业务 |

Dot自身的原生Mac任务路径使用[专门评估](dot-mac-herdr-evaluation.md#4-有界测试步骤与通过条件)。信箱数据合同由[CF-FE/task-mailbox-mcp](https://github.com/CF-FE/task-mailbox-mcp/blob/main/SPEC.md)维护；Lead的真实session／turn、自然Stop、补传与ACK流程只在[适配器规范](herdr-executor.md#lead-结果回传)维护。收到、读取、采用、验收和写权交还仍是不同事实。

## 官方操作技能与外部协调

官方[Herdr技能固定源](https://github.com/herdrdev/herdr/blob/ee5bfb5041bb3e4a49d591c72308aae3c81ecdde/skills/herdr/SKILL.md)适用于真实Herdr管理窗格中的Agent，要求原生注入的`HERDR_ENV=1`；不得伪造环境或pane身份。2026-11-02 Mac安装与读取证据保留在[历史记录](history/status-through-2026-11-03.md)，不推定其他CLI、账号或机器自动安装／发现。

外部桌面或 Dot 任务沿已授权的原任务联系受管 driver，由 driver 在 Herdr 内核对和操作目标。组织 Agent 的宿主归属见[原生宿主约定](../skill/organization-collaboration/references/context-and-reporting.md#组织-agent-的原生宿主)。适配器的已有 Agent 范围与历史单次测试的 session 创建例外见[执行合同](herdr-executor.md#职责许可与范围)。

## 启动前绑定

用[任务模板](templates/pilot-task.md)关联原任务和[组织上下文](../skill/organization-collaboration/references/context-and-reporting.md)。已有有效责任与授权直接复用，只补本次依赖的缺项：

- 原项目／任务、正式入口、唯一派发者、当前写者、报告接收者与验收者。
- 固定输入、允许动作与目录、产物、检查标准、调用／时间预算和停止条件。
- 实际宿主、目录、Agent可执行文件、参数、供应商／模型／认证／权限。启动或恢复模型会话前核对[Herdr 会话模型规则](../config/allowlists/README.md#herdr-session-model-policy)。凭证只保留安全位置引用。
- 本地目标或准确saved-machine／session／pane／Agent；远端另核SSH身份、host key与客户端／运行中server兼容性。
- 当前活动、未决请求、共享资源和报告拉取责任；无冲突才放行依赖动作。

`--kind`只是识别／启动类型，不证明模型或权限等价。Win当前范围为OMP、Hermes、Kimi；Windows Codex未纳入。独立原生CLI回合不代替Herdr托管验收，详见[能力盘点](local-agent-inventory.md)。

## 逐层验证

| 步骤 | 最小证据 | 缺口处理 |
| --- | --- | --- |
| 1. 入口与身份 | 本次调用端、准确目标及获权范围；远端机器路由另核 | 设备在线、SSH登录或别人调用成功不能代验 |
| 2. 执行环境 | 实际CLI／server、目录、Agent与pane对应关系、就绪与权限 | 空清单是有效观察，不为补齐清单创建Agent |
| 3. 单次派发 | 任务／输入版本、唯一调用意图、真实返回引用 | 受理不明保留UNKNOWN，不二发 |
| 4. 产物与交付 | 本次输入对应的真实文件、版本／字节／摘要及接收者读取 | 回显、idle、退出0或通知不替代成果 |
| 5. 原请求对账 | 原状态、已发生效果、写者、产物与待交付报告 | 不另建替代执行；无授权不主动制造故障 |
| 6. 复核与关闭 | 自检、约定独立复核、负责人绑定版本的决定与正式回读 | 必验未验或结果不符不得最终通过 |

本项目已有Mac Agent／显式Lead的一次合成切片通过，不自动放行新项目或其他宿主。新试点按自己的范围选步骤，复用适用证据；没有对照基线时不编造提效比例。

## 当前网络与 SSH

物理路线、安装与恢复证据使用[组网方案](networking-plan.md)和[组件清单](architecture.md#各端组件清单)。Mac原生连接Win的路由不能由旧VPS支路回执代证；EasyTier不替代SSH身份、文件权限或业务授权。网络变更与主机恢复不因本文成为新测试前置。

## Herdr 目标选择

以下为TUI 0.9.5已核命令形态；每次依当前实际帮助和返回字段定向。`--machine`使用准确profile ID或唯一、大小写匹配的label，不能填任意SSH hostname。界面所选机器不改变CLI路由，省略前缀仍沿本地session／socket。

```bash
herdr --version
herdr machine list --json
herdr machine status "$PROFILE_ID" --json
herdr --machine "$PROFILE_ID" pane list
herdr --machine "$PROFILE_ID" agent list
```

`PROFILE_ID`必须来自本次获准入口的真实清单；不混用Mac与VPS的profile，不用focused代替目标。`machine add`可能安装、启动或升级远端程序，**不是只读探针**；保存连接与此类副作用分别核授权。适配器只操作policy允许的本地已有Agent，具体命令沿其规范，不使用上述远端模板扩展支持范围。

保留原生返回的ID及观察时间。`agent list=[]`不证明整机没有进程；`pane read`的终端文本也不能统一按JSON解析。`pane run`只提交输入，需以独立结果标记或实际产物证明执行，输入回显不足以通过。

## 等待、未知与恢复

`agent prompt --wait`不跟踪单个turn，旧活动回合结束也可能满足等待；`idle/done`、`blocked`、`unknown`按原生含义记录。成果必须绑定本次任务、输入及产物；本仓适配器不用`--wait`作为完成协议。

超时、断线或回执不完整时，先沿原请求核受理、执行、效果、文件及写者；不自动重派、转到Lody、换模型或删除旧引用。取消请求、失联和期限到达都不证明原进程停止。需要新执行时，先明确原执行处置与写权交接，再在有效授权内建立新关联。

普通detach、server重启、主机重启及布局恢复是不同状态，不能用布局重建声称原程序续跑。适配器`COLLECTED`不释放占用，Lead显式`release`也不代替业务验收；以[状态与保全合同](herdr-executor.md#状态产物与保全)为准。

## 退出与采用

发生明确拒绝、目标冲突、越界动作、预算耗尽或结果无法归属时，停止受影响分支，保留证据、未决资源与下一责任人。其他无依赖且已获准的工作可继续。清理仅限本次有明确原生ID的资源；活动／UNKNOWN未对账时不能靠强制关闭掩盖效果。

试点结论绑定任务、机器、版本和实际覆盖范围。由原项目负责人决定后续哪些新任务采用；旧Lody／VPS任务沿原入口收尾。正式写回使用[Projects技能](../skill/github-projects-operations/SKILL.md)，不因为一次试点自动迁移看板或生产写控制。

本页原生命令来源于2026-10-29至11-02核对，本次文档整理未重新验证外部版本：[机器选择](https://herdr.dev/docs/cli-reference/#saved-ssh-machines)、[连接副作用](https://herdr.dev/tui/docs/connecting-machines/)、[状态与等待](https://herdr.dev/docs/agent-automation/#choose-the-control-surface)、[生命周期](https://herdr.dev/docs/session-state/)。历史实测见[冻结记录](history/status-through-2026-11-03.md)，当前结果只在其权威入口更新。
