# 本仓当前状态

本页维护 Herdr 项目的当前采用摘要。最近一次共享记忆部署对齐核验于 **2026-11-05（北京时间）** 完成；协调员与 Mac 桥原部署记录分别保留。各组件的版本、工具计数和健康状态均以对应观测时点为准。具体任务沿原 Issue／Project 跟踪，逐项验收见所属规范。[状态沿革](history/status-through-2026-11-03.md)保留此前记录。

## 当前结论

**2026-11-06，凡人负责人的履职配置缺口已修复并部署到 Mac。** 恢复工具采用 [`ce4311f`](https://github.com/CF-FE/herdr-4-codexcloud/commit/ce4311f4e1b296e4a17694866bd85856473fc9dc)，原等待检查服务已加载固定发布并正常运行。经公子明确授权，现任负责人沿原 Herdr 线程在凡人主仓使用 `project-owner-v1`（`workspace-write`、`on-request`），实际读到了 Herdr、组织协作、负责人／规划技能与项目工作流，并从自身工具进程成功查询三个既有 Worker。协调员核实了原任职、线程及完整启动参数，已解除维护暂停；原提醒程序恢复运行，在途工作的唯一派发者保留。

并行规则原已具备，本次补齐的是实际执行能力。源码检查运行 284 项测试（4 项跳过）；现场通过原角色接手核验，没有重发生产测试单。后续由负责人安排独立就绪分支并作业务验收，原协调员持续收回结果；这次修复不代表凡人整集完成，也不放行另行等待决定的 Win 主机维护。[运行配置与升级](agent-hosts.md#负责人运行配置与升级) · [定向联络与恢复](project-coordinator.md#nonblocking-contact)

**2026-11-05，Hindsight 代码已合并，Mac 与 VPS 运行文件已对齐 main，只读调用通过。** Herdr [PR #14](https://github.com/CF-FE/herdr-4-codexcloud/pull/14) 合并版本为 `84a68b2`，VPS [PR #1](https://github.com/ghyghoo8/create-network-sh/pull/1) 合并版本为 `da3356f`。已部署文件与两仓 main 一致，保留原安装来源，不为修改来源标签重启服务。[部署核验原记录](https://github.com/CF-FE/herdr-4-codexcloud/issues/13#issuecomment-6655425331)

| 接入部分 | 已核结果 | 仍需区分 |
| --- | --- | --- |
| Mac Codex 本地插件／LAN 客户端 | 客户端及插件缓存共 11 文件与 Herdr main 一致，插件已安装启用；18:12:10 从实际安装入口发现 39 工具，`list_banks` 只读成功 | 已打开会话是否加载、项目经验召回和写入分别取证，不由包内入口检查代验 |
| VPS Hindsight 入口 | 19 个发布文件和 systemd unit 与 VPS 仓 main 一致；原固定发布 `3daaa98` 进程运行，云端 OAuth MCP 成功读取原项目操作 | 固定发布名早于 merge 提交，但文件相同；此次核验未改运行字节、配置、OAuth 或 JWT |
| 云端Dot／Codex Cloud | 云端Dot已有项目级只读回执；公子随后回报云端客户端完成成功样本读取并提交新写入，新操作与文档已独立核为完成、可读 | 云端提交来源为公子回报；该样本不代证所有客户端或 Codex Cloud saved environment 的完整作业采用 |
| Win Web 管理端 | 官方 Control Plane 1.2.0 已安装，已有 bank、配置和原操作页面检查通过；Ctrl+C 后 UI 关闭、原后端保留 | 脚本前台手动启停，不自启、不隐藏后台。UI 源码新增尚未包含在上述 main 对齐回执中 |

Web 启动脚本为 `D:\Hindsight\scripts\Start-HindsightUI.cmd`，本机访问 `http://localhost:9999`；Mac 经现有 LAN SSH 隧道访问。完整操作只维护于[管理端说明](../mcp/hindsight-memory/control-plane/README.md)。本页采用的是安装侧原生回执，UI 当前按需关闭，不把安装完成等同于常驻服务。

**SYSTEM 代理缺口已修复，提取与读回已恢复。** 18:35:39（北京时间）重载 API 后，原生进程环境已确认代理生效；PG、BGE、模型、凭据与重试预算保留。复用获准的 Skill 重试取得首个成功样本，未另发提取测试。[修复与首项成功回执](https://github.com/CF-FE/herdr-4-codexcloud/issues/13#issuecomment-6655961110)

截至 18:55:49，公子指定的 27 个 Skill、40 个源文件均已导入本项目 bank；27 项提取及子操作全部 `completed`，共 1,515 条记忆，原文、来源版本与 SHA-256 全部读回一致。自然语言召回在不含 Skill 名称、不加文档过滤时命中目标 Skill；初次观测首项排名第 3，随后进行了下述精排迭代。后台自动整合与来源文档提取分开记录，不把 1,515 当作长期固定计数。

**Win Q8 CPU 精排已部署并复审通过。** 公子指定魔搭来源的 `bge-reranker-v2-m3`、Q8 和 CPU 推理，维护源码提交为 `c31e5bb`。原中文查询中目标 Skill 的整合摘要排第 1，直接回答可选字段的规则从原第 39 到第 4；对照查询的目标摘要也排第 1。相同冻结候选的离线对比另行确认重排收益。生产使用 4 线程、batch 8、并发 1、最多 100 个候选，两次实际 MCP 调用约 26–28 秒；这是节省 GPU 与排序、延迟的取舍，不是全库效果保证。9 份已安装维护文件及 7 份模型图／权重／分词器产物与回执对应，原 PG、GPU BGE-M3、记忆数据和 UI 手动启停方式保留。详见[精排来源、对比与回退](../mcp/hindsight-memory/reranker/README.md)。

公子回报由云端提交的新操作 `bdd6bfea-0d24-4b52-884d-c1ded7869863` 于 18:59:08 完成，父子操作无错误；原文与来源读回一致，实际提取 1 条记忆。最初的 `9856142e…` 及 Skill 首次尝试 `0dded0a4…` 仍保留历史 `failed`，修复不会自动重跑旧操作。上述回执沿 [Issue #13](https://github.com/CF-FE/herdr-4-codexcloud/issues/13)维护；各 Harness 原生采用及长期稳定性在自然任务中补充，不追加本轮测试。

协议、角色分工和统一验收见[实施文档](hindsight-memory-integration.md)，设计取舍见[ADR-002](decisions/002-shared-project-memory.md)，分享材料见[图集](share-doc/README.md)。

Mac 日常原生交办有独立的[原任务待办与恢复入口](project-coordinator.md#native-followups)。现任协调员可登记待决定事项，角色恢复时查未结项，依据对应岗位的原生决定结单。专用提醒程序需要在已绑定的 Herdr 宿主启用；现场生产与成果验收仍以项目原单为准。

**2026-11-04（北京时间），Mac Herdr 项目协调员已接手非开发事务。** 协调员在专用 Herdr 窗格中核实了工具进程继承的原生身份，Herdr 识别的 Agent 会话与实际 Codex 线程一致，然后返回了职责、下一检查点和交接回执。现有角色、服务和业务任务沿原记录继续。凡人 batch02 下一步仅获准由原写者只读对账，生产仍待负责人放行。[岗位和原生调用方式](agent-hosts.md)

**2026-11-04 13:11（北京时间），弱连接协作迭代已部署到 Mac。** 原两项协调员服务已加载 [协调员 #6，`a7ae323`](https://github.com/CF-FE/herdr-4-codexcloud/pull/6)，Mac 桥已加载 [信箱 #6，`95dec8b`](https://github.com/CF-FE/task-mailbox-mcp/pull/6)。13:11:47 两端签名握手通过，原台账和授权保留。13:11:41 一轮远端收件仍出现切换前已有的间歇连接失败。本机跨仓隔离联调中，桥断开期间负责人两个模拟报告先落本地，重连后按原单补传，只发生一次派发。此项未在生产发送测试单。[弱连接协作约定](coordination-policy.md#弱连接下的进度与迭代)

凡人 Issue #2 的首批 22 段旁白已在 win-work 完成模型听检，首份回执于 11 月 4 日 13:32（北京时间）取得。负责人采纳了现有原音候选，后续工作继续。公子可随时更新 60 秒定向听审意见。负责人沿原单跟进相应片段，并决定正式成品验收。[逐项听审清单](https://github.com/CF-FE/fanren-work/issues/2#issuecomment-6625500276) · [负责人当前决定](https://github.com/CF-FE/fanren-work/issues/2#issuecomment-6623347130)

凡人项目自行维护 [Windows INT4 环境重建指南](https://github.com/CF-FE/fanren-work/blob/main/docs/audio-environment-windows-int4.md)和[旧 Lody 工作区巡检记录](https://github.com/CF-FE/fanren-work/blob/main/docs/session-lifecycle-management.md)。

**2026-11-04 12:45（北京时间），Mac 协调员已升级到 [PR #5 合并版本 `785dc2d`](https://github.com/CF-FE/herdr-4-codexcloud/commit/785dc2defeba28538d180f534106d20762a3f66a)。** 五份运行文件匹配固定提交，原等待检查和桥接轮询服务各已自然运行两次，最近退出码均为 0。本机桥签名握手通过，原数据库 11 张表与切换前备份一致。轮询配置为 `NO_PILOT`。凡人 22 段听检已从本机沿 [Project #5 的原 Issue 交办](https://github.com/CF-FE/fanren-work/issues/2#issuecomment-6622784733)，现任负责人在原生会话明确接手安排和验收。本件使用项目入口，未创建信箱业务单。实际听检进展沿原 Issue 更新。[目标运行入口](goal-runtime.md)

**2026-11-04 10:49（北京时间），Mac 首次部署完整目标交办。** [协调员 PR #4](https://github.com/CF-FE/herdr-4-codexcloud/pull/4) 的合并提交 [`b08fb24`](https://github.com/CF-FE/herdr-4-codexcloud/commit/b08fb24ce5a892925593bc59a1c7e16da95aa14e) 已由等待检查与信箱轮询两个 launchd 服务实际加载，两者均已运行并正常退出。已安装的客户端与 Mac 桥签名握手通过。原有 4 件查询、联系与结果全部保留，数据库完整性检查通过。轮询返回 `NO_PILOT`，业务授权和新增目标数均为 0。[目标合同与单件准入](goal-handoff.md)

配套 [Task Mailbox PR #4](https://github.com/CF-FE/task-mailbox-mcp/pull/4) 的合并提交 [`560b9f5`](https://github.com/CF-FE/task-mailbox-mcp/commit/560b9f5de160caf989cfe48d6bc5a83fb0a7bd1a) 已在 Mac 桥生效。10:48:31 完成一轮远端收件，10:49:11 再次出现升级前已见的间歇连接失败，本地端口仍就绪。VPS 服务源码无变更。[信箱宿主核验记录](https://github.com/CF-FE/task-mailbox-mcp/blob/main/docs/verification.md#当前状态)

| 对象 | 已取得的证据 | 尚不包含 |
| --- | --- | --- |
| Lead 显式 Mac 执行适配器 | 源码已发布、独立安装；已有 Mac Codex Agent 的一次合成执行、产物核验、结果回传及独立工程验收通过；[Herdr #1](https://github.com/CF-FE/herdr-4-codexcloud/issues/1) Closed/completed，原 Project 条目 Done | 自动收件派发、Win／Cloud 执行扩展、任意业务任务、长期稳定性 |
| Task Mailbox／Mac 协调桥 | 专用桥迁移与三项修复已安装；原任务持久收发、精确结果读取／ACK、Mac 收取 ACK 已验；云端Dot单件计划首发见下方协调员回执 | 通用业务新任务派发、Mac 发起反向新任务、各 CLI／Cloud 独立接入及长期可用性 |
| Worker／角色库 | 主干提供 Worker 库 1.0.0、角色库 1.0.1，含 7 个 Worker、9 个后端、13 个角色。已安装的显式执行适配器按自己的固定目录制稿和校验 | 所有模板都已运行、角色已任命、全库已部署到 Win／Cloud |
| Mac → Win Herdr | TUI 0.9.5 原生 saved-machine／LAN SSH 的保存连接、reachable 及定向 Agent 清单回读通过 | Win Herdr 内模型派发、业务产物回收与生产写权切换 |
| VPS ↔ Win／Mac → VPS 网络 | EasyTier 双节点、Win 限定主机转发、Mac 主机路由、分段 SSH 及一次网络服务重建后的恢复已验 | 主机重启、持续质量、容量或所有拒绝规则的验收 |
| Win OMP／Hermes／Kimi | 执行侧报告 SSH 下原生 CLI 短对话通过；[具体版本和入口](local-agent-inventory.md#7-win-work-harness执行侧交接)分列 | 本评审直接重验、Herdr 派发、工具／生产业务验收；Windows Codex 不在当前采用范围 |
| 本仓技能与协作规范 | [组织协作 2.2.0](../skill/organization-collaboration/SKILL.md)、Projects 1.1.0。云端Dot指派验收者，项目负责人维护[主仓和项目级规范](coordination-policy.md#项目主仓与项目级规范)。协调员直接联系现任者，GitHub 评论可[暂存并独立回填](project-coordinator.md#nonblocking-contact)。 | 各端实际加载和新关系的采用需分端核对。协调员自动创建 Cloud 作业并回收成果的链路仍待验证。 |
| 云端Dot与 Mac 项目协调员 | 采用既有定时查信。[凡人负责人答复的定时读回](project-coordinator.md#fanren-scheduled-read)和[原计划首发、Mac 自动回传](project-coordinator.md#scheduled-first-submit)已验证 | 长期运行稳定性 |
| Codex Cloud 环境 | 11 月 3 日经原生环境清单核实，`herdr-4-codexcloud` 已发布，状态 `ready`、`runnable=true`。唯一仓库绑定按仓库 ID 与 GitHub 核对，指向本仓 `main`；11 月 4 日本次 Cloud 工作区已进行仓库文档编辑与[离线检查](#本地检查) | 协调员触发 Cloud、Cloud 信箱接入、指定接收者的成果读回及生产业务；本次工具权限不推广到其他任务 |

运行前提保持 **Mac 持续在线**：项目 Lead 和 CLI Proxy 在 Mac，Win 的代理入口可依赖它；不建设 Mac 离线接管。当前采用 Herdr TUI，core／`hc` 和 Herdr App 属于历史对照。职责与路径见[架构](architecture.md)，实际操作前沿[试点绑定](pilot.md#启动前绑定)核当前目标与授权。

<a id="lead-确定性执行适配器首版工程限定验收通过"></a>

## 执行适配器

实现、合同、安装命令及逐层验收统一在[执行适配器规范](herdr-executor.md)，不在此重复操作手册。

| 固定交付 | 回执 |
| --- | --- |
| 安装源码 | [`c89d5c5`](https://github.com/CF-FE/herdr-4-codexcloud/commit/c89d5c5a63072f6f42afcd4a7e03360919c4f746)；24 个文件同时匹配安装 receipt、磁盘与固定 Git 对象 |
| 工程验收基线 | [报告 `9c70457`](https://github.com/CF-FE/herdr-4-codexcloud/blob/9c704572930c721ad061bf44f774763e96096e52/docs/herdr-executor.md)；作者与评审主线程分别运行 61 项测试，另有 2 项主线程及 9 项子审查独立增量 |
| 真实样本 | 实施侧从真实 Herdr driver 执行唯一 prompt，产物 41 字节；评审从远端原 payload 独立计算并核原件。COLLECTED 与 Lead 显式 RELEASED 分记，专用测试资源按原 ID 清理 |
| 交付闭环 | 真实 Lead session／turn 暂存 → 自然 Stop → 原 collector 上传 v1 → 评审读全 2973 字节并核摘要、ACK → Mac 同 ACK 入账；评审另直接核本地 SENT／HANDLED，待收和待补传均为 0 |

公子授权的桌面控制例外仅覆盖这一次独立 Mac 测试会话、既有模型／认证和一个合成回合；它不是后续任务的通用例外。安装配置现无活动目标，新增任务须显式绑定自身目标。读取 ACK、测试写权交还和业务验收各自有证据；该样本的 `business_accepted` 仍为 false。

<a id="vps-任务信箱独立仓库开发"></a>
<a id="vps-任务信箱与客户端接入进行中"></a>

## Task Mailbox 与 Mac 协调桥

唯一维护仓为 [CF-FE/task-mailbox-mcp](https://github.com/CF-FE/task-mailbox-mcp)，服务当前状态以其[验证记录](https://github.com/CF-FE/task-mailbox-mcp/blob/main/docs/verification.md)为准。本轮采用的固定回执为 [`6179582`](https://github.com/CF-FE/task-mailbox-mcp/blob/6179582aa7253016a96e3036ba031fd614e29ecb/docs/verification.md)；个人 `create-network-sh` 只保留历史整合来源。

- Mac 桥源码 `87cfb655` 已安装到 `~/Library/Application Support/MacCoordinationBridge`，11 个运行文件已核；迁移保全原台账／OAuth／配置，新 hook 已获信任并有原生触发证据。
- 三项修复为收发预算公平性、安全故障原因／阶段保留、只读 HELD 分页。135 项标准库回归、9 项无外网 SDK smoke 与独立增量支持此次工程验收。
- VPS 后续只读复核显示服务文件与维护源码相同，无需部署变更；原安装来源字段保留，不把相同文件改称新 HEAD 已部署。
- 指定 Dot 的读取来自公子回报及实施侧服务 ACK 回读；不能写成评审直接操作 Dot，或 Dot 已发起完整执行链路。
- `AUTH_FAILED` 与间歇 `CLIENT_OPEN RuntimeError` 的现场根因仍为 `UNKNOWN`。本次结果约 6 分钟后由原 collector 依既定退避补传，不能外推长期稳定性；[Task Mailbox #1](https://github.com/CF-FE/task-mailbox-mcp/issues/1)保持 Open／In Progress。

桥负责收件、会话感知和结果补传。Mac 协调员轮询已安装，完整目标按单件授权派发。云端Dot定时查信，也可从原项目看板读取进展。旧事件唤醒分支已退役。[信箱 Issue #2](https://github.com/CF-FE/task-mailbox-mcp/issues/2)

<a id="当前执行-goal网络与协作环境就绪"></a>

## 网络与执行端

2026-11-02 网络 Goal 已完成限定准备与验证，N1–N5 原回执保留在[历史记录](history/status-through-2026-11-03.md#当前执行-goal网络与协作环境就绪)。Mac 不装 VPN；Mac → Win 经 LAN SSH，Mac → VPS 内网管理经 Win 网关，信箱走公网 HTTPS。具体组件、路由与恢复只在[网络指引](networking-plan.md)维护。

Mac 原生 Herdr 查询 Win 时返回 `agents=[]`，只说明该次识别快照为 0 个 Agent。旧 VPS profile／session／在途任务仍沿原入口收尾，同一未知执行不从 Mac 或其他入口重派。原小文件、双 pane 与 SSH 旁支结果不转记为新控制入口的业务验收。

[能力盘点](local-agent-inventory.md)保存 2026-11-02 的版本、插件／工具发现与 Win 执行侧报告；它不是实时可用清单。EasyTier 2.6.4 的已知 ICMP 拒绝用例限制、主机恢复路径与正向连通证据分列于[版本边界](networking-plan.md#5-版本安全与维护取舍)。

## 文档与来源归属

| 内容 | 唯一详述位置 |
| --- | --- |
| 当前采用摘要／后续缺口 | 本页；具体任务仍在原 Issue／Project |
| 职责、组件、源码与资产归属 | [架构](architecture.md) |
| 云端Dot上游定位、协调员调度范围、小迭代直派与 Cloud 路由边界 | [协作约定](coordination-policy.md)；通用岗位语义在组织技能维护 |
| 协调员、负责人、Worker 的生效、交接、恢复与退出 | [生命周期设计依据](project-coordinator.md#agent-lifecycle)，各运行端的 [hooks 与证据边界](project-coordinator.md#lifecycle-hooks) |
| 网络、SSH、服务恢复 | [网络指引](networking-plan.md)；VPS 特有事项见[专项指引](vps-control-pilot.md) |
| 多项目合同与分阶段路线 | [迭代指引](collaboration-iteration-guide.md) |
| 通用执行准备、UNKNOWN 与退出 | [试点指引](pilot.md)及[任务模板](templates/pilot-task.md) |
| 显式适配器接口与验收 | [执行规范](herdr-executor.md) |
| Worker／角色定义与版本 | [双库入口](worker-library/README.md)，原始来源见其[source-manifest](worker-library/source-manifest.json) |
| 组织／Projects 技能与评测 | [技能索引](../skill/README.md)、[评测资料](organization-collaboration/README.md) |
| 导入及阶段历史 | [初始清单](copy-manifest.json) → [增量清单](copy-updates.json)；[冻结状态沿革](history/status-through-2026-11-03.md) |

后续同步按初始清单再逐次叠加增量，不重写历史摘要、不自动跨仓同步。2026-10-31 的候选 PR 与后续合并状态各保留其时点；源仓测试或旧 Lody 业务回执不作为本仓验收。

## 后续缺口

1. **传输稳定性**：沿服务仓原任务核长期运行、反向新任务与原异常原因，保留 UNKNOWN。
2. **Dot 原生入口**：已有局部用户回报；具体工具发现、目标绑定、定向执行和回读仍按[专项评估](dot-mac-herdr-evaluation.md)取证，不由网络或桌面试点代证。
3. **Win／Cloud 扩展**。Cloud 环境已发布，具体 Cloud 任务的实际工具、权限与回传分别取证；协调员指挥 Cloud 路由仍是[目标合同](coordination-policy.md#4-协调员指挥-cloud-路由)，不由云端Dot的独立直派样本或信箱 ACK 代验。Win 的 Herdr 定向派发及业务产物仍待验。
4. **多项目与生产**：真实项目产物、并行容量、生产业务验收及新旧写控制切换未验；选择下一步沿[迭代路线](collaboration-iteration-guide.md#4-分阶段迭代)，本列表不启动任务。
5. **目标工具与恢复**：Worker 自身 Projects／Space 工具、权限、资源读取，技能原生发现，主机重启、未来版本兼容及所依赖的网络拒绝用例分别待核。只限制依赖这些条件的动作，不重做已经通过的独立切片。

## 本地检查

检查命令见 [README](../README.md#本地检查)。2026-11-03 文档整理时，结构检查、Worker 库检查和 61 项测试通过，仓内链接／锚点及来源摘要核对通过；冻结历史与整理前原件字节一致，当轮源码、复制技能、来源清单与角色定义未变。

2026-11-04 协作规范修订在本次 Cloud 工作区通过结构检查、Worker 库检查和现有 100 项标准库测试。此次修改文档与组织技能，运行源码、历史导入清单及 Worker／角色库未变；这是仓库级文档与离线验证样本，不证明 Mac 协调员能触发 Cloud、信箱自动派发、目标宿主部署或生产业务通过。


2026-11-04 完整目标交办合并前，协调仓结构检查、Worker 库检查及 155 项测试通过。用已合并的信箱代码做本机跨仓联调，模拟丢失回执后原目标只投递一次，两份报告沿原任务回收。

2026-11-04 受限运行入口候选在隔离环境完成结构检查、Worker 库检查及 186 项标准库测试（185 通过，1 项真实 CLI smoke 按本轮范围跳过）。其中组织工具 15 项覆盖真实 stdio 与模拟原生程序，安装 2 项覆盖固定发布闭包；跨仓双进程签名联调保留一次模拟投递、两份原任务结果及丢失回执恢复。独立源码审查和增量复验完成。这些结果不表示真实身份认证、原线程独占、宿主沙箱或实际听检已验收；未部署、配置凭据或派发真实任务。
