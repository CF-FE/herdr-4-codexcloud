# 本仓当前状态

证据截至 **2026-11-03（Asia/Shanghai）**。本页维护 Herdr 项目的当前采用摘要，具体任务以原 Issue／Project 为准，接口与逐项验收以所属规范为准。版本、工具计数与运行健康均为对应时点的观测，不是实时监控。此前逐轮记录已冻结到[状态沿革](history/status-through-2026-11-03.md)，不再与本页并列维护现状。

## 当前结论

| 对象 | 已取得的证据 | 尚不包含 |
| --- | --- | --- |
| Lead 显式 Mac 执行适配器 | 源码已发布、独立安装；已有 Mac Codex Agent 的一次合成执行、产物核验、结果回传及独立工程验收通过；[Herdr #1](https://github.com/CF-FE/herdr-4-codexcloud/issues/1) Closed/completed，原 Project 条目 Done | 自动收件派发、Win／Cloud 执行扩展、任意业务任务、长期稳定性 |
| Task Mailbox／Mac 协调桥 | 专用桥迁移与三项修复已安装；原任务持久收发、精确结果读取／ACK、Mac 收取 ACK 已验 | Dot 发起新任务、Mac 发起反向新任务、各 CLI／Cloud 独立接入及长期可用性 |
| Worker／角色库 | 主干提供 Worker 库 1.0.0、角色库 1.0.1，含 7 个 Worker、9 个后端、13 个角色。已安装的显式执行适配器按自己的固定目录制稿和校验 | 所有模板都已运行、角色已任命、全库已部署到 Win／Cloud |
| Mac → Win Herdr | TUI 0.9.5 原生 saved-machine／LAN SSH 的保存连接、reachable 及定向 Agent 清单回读通过 | Win Herdr 内模型派发、业务产物回收与生产写权切换 |
| VPS ↔ Win／Mac → VPS 网络 | EasyTier 双节点、Win 限定主机转发、Mac 主机路由、分段 SSH 及一次网络服务重建后的恢复已验 | 主机重启、持续质量、容量或所有拒绝规则的验收 |
| Win OMP／Hermes／Kimi | 执行侧报告 SSH 下原生 CLI 短对话通过；[具体版本和入口](local-agent-inventory.md#7-win-work-harness执行侧交接)分列 | 本评审直接重验、Herdr 派发、工具／生产业务验收；Windows Codex 不在当前采用范围 |
| 本仓技能 | 组织协作 1.5.2、Projects 1.1.0 源码可按路径读取，来源闭包已核 | 所有目标宿主原生安装、自动发现和工具权限 |
| 云端Dot与 Mac 项目协调员 | 凡人第 1 代负责人收到一次只读查询并答复。原云端Dot实际读取原件 v1，并于 19:33 北京时间确认已读。公子转交原端回执，信箱与 Mac 桥已核齐，本单业务密钥已撤回。[单件验收](project-coordinator.md#可信入口核验p0) | 无人值守的云端身份核验、长期稳定性待验。凡人首集的独立听检及正式验收由项目另行跟进 |
| Codex Cloud 环境 | 11 月 3 日经原生环境清单核实，`herdr-4-codexcloud` 已发布，状态 `ready`、`runnable=true`。唯一仓库绑定按仓库 ID 与 GitHub 核对，指向本仓 `main` | Cloud Worker 实际运行、工具权限及成果回传待验 |

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

桥负责收件、会话感知和结果补传；Lead 显式调用的执行适配器属于 Herdr 项目。信箱自动消费／无人值守派发、Dot 自动唤醒尚未实现。各 CLI 和 Cloud 的身份、工具及读写不继承专用桥的成功回执。

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
3. **Win／Cloud 扩展**。Cloud 环境已发布，下一项是核验具体 Cloud 任务的实际工具、权限与回传。Win 的 Herdr 定向派发及业务产物仍待验。
4. **多项目与生产**：真实项目产物、并行容量、生产业务验收及新旧写控制切换未验；选择下一步沿[迭代路线](collaboration-iteration-guide.md#4-分阶段迭代)，本列表不启动任务。
5. **目标工具与恢复**：Worker 自身 Projects／Space 工具、权限、资源读取，技能原生发现，主机重启、未来版本兼容及所依赖的网络拒绝用例分别待核。只限制依赖这些条件的动作，不重做已经通过的独立切片。

## 本地检查

检查命令见 [README](../README.md#本地检查)。2026-11-03 本次文档整理的结构检查、Worker 库检查和 61 项测试通过，仓内链接／锚点及来源摘要核对通过。冻结历史经逆向校对，与整理前原件字节一致；源码、复制技能、来源清单与角色定义未变。以上是文档与离线证据，不是新的模型调用、安装、入网或故障测试。
