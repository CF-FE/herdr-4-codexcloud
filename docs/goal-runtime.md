# 原线程与受限组织工具

本页补充 [完整目标合同](goal-handoff.md) 的实际执行入口。外部 `DELEGATE_GOAL`、原 job/event/版本/hash、现任项目负责人和原信箱回程不变。实现和隔离验证不授予真实任务准入；网络长期稳定性不是本入口的前置验收。

## 同一原生身份，单个进程受限

v1 原生策略仍按原 dedicated-home/profile 合同校验。v2 保留明确获准的原 `CODEX_HOME` 和原 endpoint，在当前进程传入受审配置覆盖；不建立空 home冒称可见原线程、不复制 session 或认证状态、不修改全局配置。

v2 单件 policy 引用固定 `process_config` 及其 SHA-256，替代命名 profile 字段。进程配置固定原 home/endpoint、禁用的 feature、受审原 MCP 服务名称清单及唯一受限 stdio 组织服务。解释器、服务代码、四个协调依赖、工具策略都属于必需摘要闭包；仅审核一个同名脚本或允许任意 argv 不足以放行。

CLI 覆盖表采用递归合并，写一个新 `mcp_servers` 表不会删除旧服务。因此实现逐项禁用已审的原服务，再核有效配置中的完整集合；未知新增服务、启用项偏差、配置层变化或字节漂移均拒绝继续。依据为固定版本的 [CLI层装配](https://github.com/openai/codex/blob/rust-v0.166.0-alpha.6/codex-rs/config/src/loader/mod.rs)、[有效配置合并](https://github.com/openai/codex/blob/rust-v0.166.0-alpha.6/codex-rs/config/src/state.rs)及[递归表合并](https://github.com/openai/codex/blob/rust-v0.166.0-alpha.6/codex-rs/config/src/merge.rs)。具体 CLI 二进制和协议版本仍由宿主固定核验。

每次新派发先核对工具策略中的项目、原任务、台账、任职和动作范围。宿主交还记录须载明原线程的审批策略与只读沙箱。v2 进程采用只读默认配置，恢复线程和启动回合时保留线程原有的只读沙箱及 `never` 或 `on-request` 审批策略，核对实际生效的工具。缺少原值或发现权限不符时停止派发。

这里的四项允许列表仅指 MCP 组织服务，不是完整的模型内建工具清单。固定 CLI 的 [utility 工具注册](https://github.com/openai/codex/blob/rust-v0.166.0-alpha.6/codex-rs/core/src/tools/spec_plan.rs)与 shell feature 分开，关闭 shell 不会自动删除 `apply_patch`。写入边界仍依赖已核的原生 read-only sandbox 和遇审批停止、不代答的通道行为；实际宿主还须核内建工具及沙箱，不能用 MCP 列表代验。

新启动的 app-server 内存态不能证明另一个 CLI/App 已交还原线程。v2 另要求 `owner_exclusivity` 宿主审阅件，固定原 home、线程、任职及单件绑定、原入口/交还证据、观察时间与短期有效期，最终原生开始前再核。它属于明确的宿主证明，不是已实现跨原生入口全局锁；缺少证据、仍有活动写者或结果不明时不能启用。该要求同时适用于可选审查者子联系。

## 实际 stdio 工具

[组织 MCP](../mcp/project-coordinator-tools/README.md) 随同一固定协调 release 交付，使用标准库 stdio，不部署额外常驻网络服务。每次调用绑定原项目、请求、job/hash、现任/generation、工作键、资产范围及期限。调用参数不提供替代身份、任意文件路径、命令或全局权限。

| 工具 | 行为与证据 |
| --- | --- |
| `goal_context` | 只读原协调台账，核现任、取消、期限及冻结范围；保留旧查询/既有安排关联 |
| `read_review_evidence` | 仅按固定 ref_id 读取允许的普通原件，核路径、尺寸和摘要；返回所核同一字节，不跟随任意调用方路径 |
| `coordinate_independent_review` | 先核已有安排及持久动作；首次联系前无合法执行者/通道/单件许可返回 BLOCKED。有额外许可时只向固定已存在原生对象交付一次证据核验子任务；已有意图的不明结果保留 UNKNOWN |
| `read_coordination_report` | 读取本单持久子动作和真实原生观察，不重新派发，不直接改父任务业务结果 |

普通 JSON/text 中的“accepted”是原件声明，摘要一致只证明材料身份，不能自动提升为原生接受。真实接受和进度由绑定线程的官方回读验证。工具不会直接绕过 `goal_update` 写父核心状态；现任负责人沿原生报告合同回报，协调器继续沿原 job 生成不可变版本。

默认没有 reviewer 绑定，额外 `review_contact_attempts` 为 0。项目负责人可以取得当前事实和具体 BLOCKED 原因，但工具不会自建身份或以角色模板代替执行者。独立听检仍须真实听辨能力、审者独立性和原件；ASR、文件技术检查、工具初始化或 native SUBMITTED 均不能代替。

当前四工具只读取受审的 JSON/text，没有 WAV 播放或音频感知入口。子任务用于核已有听检证据及报告缺口，不能完成新的听检。受限 v2 原生通道及子工具均拒绝 `independent_listening` 的 `COMPLETED`，避免父负责人或子审查者用原生来源真实的模型自述替代听辨事实。拒收的自报保留原生证据，未占用业务序号。负责人可沿同一动作、同一序号提交更正，例如提供已确认的审听人和首份回执时间。本版不能验收真实听检完成，新增听辨执行或原件验收适配器不在本次工程范围。

组织工具的 `role=project_owner` 用于已绑定负责人的原线程；`role=reviewer` 用于指定的独立审查者。听检安排、项目内回执和对上游的摘要按[默认分工](coordination-policy.md#1-默认分工与取舍)执行。

## 既有审查者的单件子联系

可选后端复用 `CodexGoalChannel`，只恢复已存在且已获准的审查者线程。任命、独立性、原生 endpoint、能力 policy/hash、范围和额外授权均固定；现任项目负责人和 generation 不变。这里的子任务目标身份不能用来更新 `projects.owner_principal`。

父 `DELEGATE_GOAL` 的 `dispatch_attempts=1` 仍只准一次负责人投递；它不隐含额外联系预算。只有具体宿主批准的 `review_contact_attempts=1` 才允许一次审查者子联系，生产写入和音频生成仍为零。子目标只允许核原件和回报，不再次开放组织委派。

联系前核原任务来源/取消/代次和精确范围，并沿原生历史查同一冻结子动作。完整原件证明不存在才可能继续；PRESENT沿原引用、UNKNOWN只对账。若原生已有动作但本地意图缺失，不能静默补造派发记录或再次发送。

复听使用原素材。历史轮次的制作回执或听检材料可标为 `prior_round`，用 `prior_round_ref` 指明其来源；这类记录不会拦住已获准的新一轮联系。本单已有联系意图时沿原动作收回执。

本地持久意图先于一次原生发送。超时、进程退出或回执丢失保留原子请求/action；重启和再次调用只读取同一动作。子报告随父 job/hash 关联，不新建 Mailbox job、不冒称新负责人、不借其它项目执行器或看板写权完成协调。

本地核验使用原协调台账和 Mac 桥已经收妥的签名原件。有效期内，远端信箱掉线时仍能按本地已知授权执行和听取项目回报。取消以本地收到的事件为准；结果按原事件进入发件队列，待联网补传。

## 单件安全配置与撤销责任

只有代码与隔离测试准备不涉及宿主启用。具体工具/资产/身份确认后，原宿主管理者准备精确方案，用户逐项确认新凭据、持续访问及安全配置；原业务目标授权不能变成自动批准权限弹窗。

1. 云端Dot沿原 Issue/Project、素材摘要和在途记录去重，一次定稿原 job/request、payload hash、检查点及期限。新的检查点在首次提交前更新；提交后不变更原 payload 或用新 ID 重试。
2. 宿主管理者核原 owner 与（可选）reviewer的实际原生身份、原入口独占/交还、合法认证复用、固定 CLI、进程覆盖、MCP 工具及服务端范围约束。任何未验证项列出，不能用自填 capability manifest 证明真实能力。
3. 在具体许可后配置本单 key、两端精确来源 grant、目标 policy/manifest和工具 policy；原 OAuth、collector、游标、全局配置、其它授权及源资产不动。先保持 pilot 为 null，不让准备步骤隐式派发。
4. 最后核所有摘要/绑定与原 job状态，再挂单件 `goal_channel_config` 和精确 pilot；这是实际放行开关。已有 pump 会执行，不另开第二个发送者。收取 ADMITTED、原生接受/阻塞及原信箱版本/hash分别记证。
5. 结果或终止回执安全进入原 outbox并核 event/hash后，只撤本单新加的 key/grant/pilot/通道引用和能力准入，保留历史。已排队事件由原 collector补传，不依赖新业务 grant。云端Dot可按原 job 查看最新进展，或从 GitHub Project 查看里程碑。日常交办不要求已读回报。项目负责人继续负责业务验收。

期限同时绑定 request.deadline、各 policy/manifest和独占证明；业务 grant schema没有自动 TTL字段。宿主管理者承担到期撤销，不能伪造未支持的 expires_at字段或默许无限保留。到期还未排队时停止新动作、保留原结果并报告补传缺口，任何延期另核对应权限。

最小准入材料由原宿主管理者准备，审核后逐件引用：

| 材料 | 必须固定的内容 |
| --- | --- |
| 原生 v2 policy | 原 home/endpoint、现任/代次、job/request/hash、工作键和资产范围、允许动作、固定 CLI、期限 |
| `process_config` | 原 home/endpoint、明确禁用的原 MCP 清单和 features、唯一同版本 stdio 服务、读取工具列表及固定私有工具 policy |
| capability manifest | 生效配置/层/环境摘要、完整源码与解释器摘要、动作到 MCP 工具的映射、独占审阅件引用及有效期 |
| `owner_exclusivity` | 宿主独立审阅、原入口交还依据、同一任务与线程、最后回合、原线程审批策略与沙箱、观察时间及不超过 900 秒的有效窗口；不能由新 reader 的 idle 自动生成 |
| 工具 policy | 只读父台账、单件本地动作目录、固定普通证据 ref/路径/hash/尺寸、role 与动作；默认 reviewer=null、review_contact_attempts=0 |
| 可选子联系材料 | 既有审者任命/独立性原件、独立线程、只读 reviewer 工具 policy、同单来源桥引用、额外一次联系许可；不改变父 owner |

这些是安装之外的实际准入配置，不能用示例、测试夹具或代理自填声明开通。完整字段与拒绝条件以 [原生校验器](../scripts/coordinator_goal_channel.py) 和 [工具校验器](../mcp/project-coordinator-tools/server.py) 为准。

桥端的配置顺序和收束责任见 [Task Mailbox 准入说明](https://github.com/CF-FE/task-mailbox-mcp/blob/main/docs/goal-runtime-admission.md)。此次外部信箱合同不变；建议先审并合入本仓实现，再合入引用它的桥端操作文档，实际部署与单件启用另行进行。

## 验证与未验范围

标准库测试覆盖真实临时 stdio进程、协议调用、字节范围校验、实际协调核心读回、模拟原生可执行程序、固定既有对象的一次联系、回执丢失对账和默认 BLOCKED。安装验证从固定 Git 对象复制完整闭包，在无 checkout/site-package依赖的临时目录启动工具帮助入口；不调用模型。

真实宿主的认证复用、原线程独占、实际 MCP 执行能力及独立听辨、业务接手和云端结果读回尚需按具体单件验证。测试中的 receiver是模拟原生入口，不表示现场已有合法听检执行者。网络间歇错误不增加长期稳定性门槛；发生未知结果仍必须保全并对账原动作。
