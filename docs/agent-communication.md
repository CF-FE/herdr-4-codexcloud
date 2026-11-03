# 跨 Agent 通信选型与研究边界

<a id="跨-agent-通信与协作指引"></a>

整理日期：2026-11-03。本文保留通信路线的选择理由、TUI／core差异及固定研究来源；当前组件与连接见[架构](architecture.md)，执行判据见[试点指引](pilot.md)，运行状态只在[状态入口](status.md)维护。

## 1. 当前建议与理由

**当前采用Herdr TUI＋Task Mailbox传输＋Lead显式执行适配器。** TUI负责准确目标上的终端／Agent控制；信箱与Mac桥负责持久交接、会话信号和补传；Lead决定是否执行，[适配器](herdr-executor.md)保存意图、一次prompt和产物关联。这样分别解决机器控制、数据对账和执行可追溯问题，不把传输ACK当业务完成。

2026-11-03的已有Mac Agent／显式Lead切片已工程限定通过；它不证明自动消费、Win／Cloud扩展、生产业务或长期可用性。不再把2026-10-30的“core优先PILOT”建议当当前待执行门槛，不要求同时部署hc／Herdr App。具体证据与固定实现沿[适配器验收](herdr-executor.md#验收记录)。

## 2. 先区分两套 Herdr

下表为**2026-10-30官方文档与固定源码研究**，本轮未重新核验上游版本；目标端行为仍按实际版本与原生回执判断。

| 项目 | Herdr TUI | Herdr App／herdr-core |
| --- | --- | --- |
| 入口 | `herdr` CLI、TUI、server | `hc`核心CLI，App为可选客户端 |
| Agent状态 | 屏幕识别及集成报告，按版本核 | 程序通过OSC 7501报告，不按品牌或屏幕猜测 |
| 通信 | `agent prompt/read/wait`，可选插件 | `run/send/wait`、父子终端及系统通知 |
| 运行关系 | 独立Agent、server和状态 | 与TUI不共享Agent，不能机械替换命令 |

TUI研究源固定为 [`ee5bfb5`](https://github.com/herdrdev/herdr/tree/ee5bfb5041bb3e4a49d591c72308aae3c81ecdde)，当时核到发布[v0.9.5](https://github.com/herdrdev/herdr/releases/tag/v0.9.5)。core研究源固定为 [`dbcdcff`](https://github.com/herdrdev/herdr-core/blob/dbcdcffc779f7516d5815cbd6f0a37da2b5e6628/crates/herdr-core/Cargo.toml)，crate为0.1.14；当时官方latest发行入口403，未取得最新可安装版，不能用源码版本替代发行证据。后续版本观察留在[历史状态](history/status-through-2026-11-03.md)，本页不维护“最新版本”结论。

独立PTY不自动共享上下文，但运行时可以提供通信；不能把某插件限制推广成所有Agent都不能通信。参考：[产品区别](https://herdr.dev/#questions)、[core概览](https://herdr.dev/core/)、[TUI自动化](https://herdr.dev/tui/docs/agent-automation/)。

## 3. 通信路线比较

以下比较同样限定于2026-10-30研究快照，说明当时为什么没有整包采用；不是对今日版本的断言。

| 路线 | 研究得到的范围 | 本仓取舍 |
| --- | --- | --- |
| TUI原生CLI | 按准确机器与原生引用控制Agent | 已选控制入口；结果关联由任务与适配器补足 |
| herdr-core | 父子通知、发送及SSH远端控制 | 独立运行时与适配成本；旧评估为价值MEDIUM／成本MEDIUM／净收益UNCERTAIN，后由TUI选择替代 |
| Herdr Link 0.6.0 | 同一workspace内live named peers的工具适配 | 不覆盖跨机、持久inbox或业务ACK；不因工具封装连带安装 |
| Chatter 0.19.0 | 本机同Git仓库的共享聊天与任务辅助 | 研究版Linux/macOS，不覆盖原生Win；内部任务不能竞争正式记录 |
| AWP v0.6.0 | 持久发件、收件去重和daemon ACK | 增加身份、daemon、存储与连接维护；本仓已选独立Task Mailbox，不再并行建设第二通道 |
| A2A 1.0 | Agent服务的发现、任务和产物协议 | 现有CLI还需服务适配，当前没有采用这一层的需求证据 |
| MCP Agent Mail Rust v0.3.39 | 另一种持久邮箱 | 新增有状态服务且研究版有附加许可条款，本轮不复制或安装 |

### Herdr Link：工具化没有消除范围和投递限制

固定发布源 [`b795e8f`](https://github.com/LZHcode1986/herdr-link/blob/b795e8f3d6719c20fa7c8164d294be21914c308d/package.json)要求Node≥22.6.0。[协议](https://github.com/LZHcode1986/herdr-link/blob/b795e8f3d6719c20fa7c8164d294be21914c308d/PROTOCOL.md)不承诺跨workspace／机器、持久队列、离线投递、ACK或自动重试；`sent`只说明Herdr接受，`done`仍是普通文本。

TUI的worktree创建会打开独立workspace；因此不能直接组合成“每Worker一个官方worktree并通过Link互通”。同workspace下不同tab／cwd引用既有worktree，从[固定guard实现](https://github.com/LZHcode1986/herdr-link/blob/b795e8f3d6719c20fa7c8164d294be21914c308d/src/herdr.ts#L975-L1049)看可能不被拒绝，但本仓未实测。[TUI worktree语义](https://herdr.dev/tui/docs/cli-reference/#worktrees)与插件限制须分别核。工具封装可能减少CLI学习成本；研究没有可复核token／延迟对照，不记录“零学习／几乎零延迟”为收益。

### Chatter：共享聊天不等于可靠任务回执

固定源 [`0edc18f`](https://github.com/marcvermeeren/chatter/blob/0edc18f4a130a6e013b24f9090b07372c8c41feb/herdr-plugin.toml)要求Herdr≥0.8、Node≥22.5，列出Linux/macOS。[SQLite定位](https://github.com/marcvermeeren/chatter/blob/0edc18f4a130a6e013b24f9090b07372c8c41feb/src/db.ts#L15-L31)基于本机Git common-dir，同仓worktree可共享，不同机器clone不会自动共享。

[投递源码](https://github.com/marcvermeeren/chatter/blob/0edc18f4a130a6e013b24f9090b07372c8c41feb/src/team.ts#L296-L355)先记投递标记，再调用TUI prompt，CLI成功后记`read_at`。这不证明接收Agent已理解任务；标记与注入之间的崩溃窗口是源码推断，未经本仓故障实测，不能赋予恰好一次语义。

### 持久邮箱与远程协议：由实际需求触发

AWP的[投递语义](https://docs.agentwireprotocol.com/concepts/delivery)描述先落盘、收件commit后ACK及重连去重；ACK只到接收daemon，不到业务验收。固定[CHANGELOG](https://github.com/agentwireprotocol/awp/blob/cc6d6ac3dc165ceb15a9cfe3173afa726e6b55c8/CHANGELOG.md)仍把Herdr插件列为Unreleased，研究版核心／插件面向Linux/macOS。其[tailcat路线](https://docs.agentwireprotocol.com/guides/across-machines)不能继承既有Tailscale tailnet策略，更不能作为本仓现行EasyTier的隐式替换。

[A2A 1.0规范](https://a2a-protocol.org/v1.0.0/specification/)不自动赋予CLI可靠持久投递或业务幂等；[Agent Mail Rust说明](https://github.com/Dicklesworthstone/mcp_agent_mail_rust/blob/v0.3.39/README.md)与[许可证](https://github.com/Dicklesworthstone/mcp_agent_mail_rust/blob/v0.3.39/LICENSE)须连同部署成本评估。后续只有现有通道确实缺少已确认能力时，再定向复查受影响选项，不整包引入新平台。

## 4. 候选协作结构

2026-10-30的core／Tailscale候选图及当时分工保留在[固定历史稿](https://github.com/CF-FE/herdr-4-codexcloud/blob/9c704572930c721ad061bf44f774763e96096e52/docs/agent-communication.md#4-候选协作结构)。它不是当前部署路线；现行总图只在[架构](architecture.md)维护。worktree、workspace、session和通信身份是不同边界，不能从其中一种推导权限隔离或任务关联。

## 5. core 适配与可靠性边界

本节只解释为何旧候选需要额外验证，**不适用于当前TUI调用合同**。全部实现判断绑定core `dbcdcff`，没有目标端运行或恢复验收。

### 启动配置与状态报告

core只保存程序报告的OSC 7501状态；模板不安装Agent，`ready: quiet`只是等启动输出安静，不产生状态报告，见[模板](https://github.com/herdrdev/herdr-core/blob/dbcdcffc779f7516d5815cbd6f0a37da2b5e6628/docs/templates.md#L62-L88)。该快照的[内嵌手册](https://github.com/herdrdev/herdr-core/blob/dbcdcffc779f7516d5815cbd6f0a37da2b5e6628/crates/herdr-core/src/SKILL.md)要求Codex使用`--no-daemon`；它是旧路线待核适配条件，不能添加到任意Codex版本或替换既有provider配置。

### 发送、等待与对账

[输入实现](https://github.com/herdrdev/herdr-core/blob/dbcdcffc779f7516d5815cbd6f0a37da2b5e6628/crates/herdr-core/src/input.rs#L102-L213)仍以paste＋Enter投递，blocked默认拒绝；不默认用`--force`回答权限对话。`send --wait`的turn与原生活动变化关联，不提供独立业务请求ID；旧回合可能满足新发送后的等待是源码推断，不是本仓实测。

[跨机children等待](https://github.com/herdrdev/herdr-core/blob/dbcdcffc779f7516d5815cbd6f0a37da2b5e6628/crates/herdr-core/src/api/waiting.rs#L128-L187)会略过不可达／失败侧，返回all不能清除这些执行的UNKNOWN。父子通知有[独立outbox](https://github.com/herdrdev/herdr-core/blob/dbcdcffc779f7516d5815cbd6f0a37da2b5e6628/crates/herdr-core/src/remote.rs#L648-L659)，仍需[真实父终端绑定](https://github.com/herdrdev/herdr-core/blob/dbcdcffc779f7516d5815cbd6f0a37da2b5e6628/crates/herdr-core/src/notes.rs#L54-L57)；不能推广为普通业务消息持久投递或自动进入Dot聊天。

### 生命周期与权限

[固定生命周期说明](https://github.com/herdrdev/herdr-core/blob/dbcdcffc779f7516d5815cbd6f0a37da2b5e6628/README.md)区分连接断开、core重启、父进程退出和主机重启；holder、`--keep`或`--outlive-parent`各有自己的范围，不能据此声称主机重启后原任务续跑。[安全说明](https://github.com/herdrdev/herdr-core/blob/dbcdcffc779f7516d5815cbd6f0a37da2b5e6628/docs/security.md)也不把core或session定义为沙箱／组织权限代理。

## 6. 消息与结果的最小约定

使用[多项目任务关联与可达汇报](collaboration-iteration-guide.md#3-从第一条任务就保留的身份与证据)，不因更换通信工具另造任务状态。工具受理、接收者确认、固定产物和负责人验收是四层证据；插件的sent／read_at／done字段不能替代后两层。

## 7. 后续最小验证切片

按[试点指引](pilot.md#逐层验证)选择当前任务确实依赖的入口和能力，复用已有显式Mac验收，不要求再建两个Agent或比较core来证明选型。只有通信瓶颈的新证据、实际范围变化或上游不兼容，才复查对应候选；多项目与性能验证按[迭代指引](collaboration-iteration-guide.md#4-分阶段迭代)单独安排。

## 8. 证据范围与维护

研究采用官方文档、协议和固定源码；两段视频及旧布局页没有可核正文，未用作结论。[跨Agent委派提案#741](https://github.com/herdrdev/herdr/discussions/741)与[排队／发送者归因提案#2401](https://github.com/herdrdev/herdr/discussions/2401)只作需求背景，不当发布承诺；旧`agent.send`名称不是当前接口。

后续定向更新同一选型稿，分别标明研究日期、固定来源、推断和目标端证据。当前运行和验收只链接其权威记录，不在本页追加逐轮部署流水。
