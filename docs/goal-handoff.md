# 完整目标交办

本合同实现组织协作 1.7.0 的完整目标交办，不改变组织岗位定义。主机部署情况见[当前状态](status.md#当前结论)。单件准入、现任负责人接手和业务验收沿原任务记录。

## 原任务与唯一负责人

业务请求使用 `DELEGATE_GOAL`，不能塞进 `QUERY_BLOCKER.content`。请求必须关联经原桥验证的 Project item、原 Issue/draft、原 `job_id`、payload hash、可信原生来源及当前负责人代次。项目必须已经登记；本操作不创建虚拟项目，不创建、撤换或并存第二位负责人。

保持一份协调台账。请求进入台账只代表 `ADMITTED`；固定目标的原生投递与负责人明确接受分别记录。缺少可执行通道时保留待承接，不用工具调用成功或查询答复代替接受。

## 请求合同

原 `coordinator_request` 绑定字段不变，增加 `goal`，`kind=DELEGATE_GOAL`；完整目标不从外层自由文本隐式提取。`goal` 必须包含：

| 字段 | 含义 |
| --- | --- |
| `objective`、`acceptance_criteria` | 完整目标与非空、去重的可核验终点 |
| `scope_refs`、`evidence_refs` | 固定工作范围和原件引用；本批资产引用应绑定版本与摘要 |
| `related_request_ids` | 旧查询、已安排任务的原引用；关联不是重新执行许可 |
| `work_key` | `canonical_hash({task_ref:规范原任务引用, scope_refs:排序后的范围引用})` |
| `allowed_actions` | 仅 `coordinate_existing_owner`、`verify_existing_evidence`、`plan_independent_review`、`report_progress` 的明确子集 |
| `next_check_at` | 有时区的下一检查时间，早于或等于截止且接单时尚未到期 |
| `review_requirement` | `none` 或 `independent_listening`；后者完成时必须有独立听检证据 |

规范任务引用取经验证的 Issue URL 或 draft ID，不用别名变更绕开去重。同项目同工作键，或者跨 Issue 范围部分重叠，拒绝另建执行；授权范围内的冲突响应给出原请求 ID 和工作键，可用 `goal_for_work` 查询原件。资产及独立审者标识须使用无首尾空白的可打印值。同键异内容不覆盖原件。已完成范围也不能借换措辞重复派发，新版本资产应使用新的不可变范围引用。

本版预算固定为 `dispatch_attempts=1, production_writes=0, audio_generations=0`。它允许向现任负责人交清目标，不授权生成音频、改变资产、创建凭据、切换负责人或批准权限弹窗。听检安排与实际听检能力仍须分别核验。

## 准入与原生执行

三层均使用独立准入，升级源码不扩旧授权：

1. 原桥的精确 job/hash grant 显式指定 `request_kind=DELEGATE_GOAL`；缺省仍只允许查询，探针不能业务调用。
2. 协调客户端的来源 grant 显式包含 `submit_goal`。原 `submit` 不包含完整目标。
3. 原生通道在 `prepare_goal` 核实权限与负责人可用后，才领取唯一派发意图并调用 `submit_goal`，并在最后执行点调用 `verify_current`，核原任务取消、来源、代次、目标和期限，执行自身实际的工具/资产权限。只实现 `submit_contact` 的旧只读驱动会拒绝目标派发。

负责人正在其他回合时保留原请求和派发额度。负责人可用时在事务中记录意图，再向同一原生目标发出一次。目标冻结在原件指定代次，负责人换任需沿原单处理交接。`SUBMITTED` 仅是投递证据；超时、回执错绑或结果不明记 `UNKNOWN`，重启或重复 pump 不会重发。恢复只沿原 action/原生引用读取。没有可靠外部原生幂等服务时，不宣称外部恰好一次执行。

新的凭据、持久准入或安全设置必须沿本次具体授权处理；不复用已结束任务的临时批准。不得关闭原生审批或通过替代执行端绕过拒绝。

## 现有 Codex 线程适配器

保留原 home/线程的 v2 进程配置及实际组织工具见[目标运行入口](goal-runtime.md)。下述 v1 策略仍保留，不能用新空 home 或旧查询 profile 推定目标线程与工具已经可用。

`coordinator_goal_channel.py` 提供可选的 `CodexGoalChannel`。协调桥私有配置的 `goal_channel_config` 引用单件策略文件；缺省不启用，且必须已有精确 pilot。策略绑定项目、原 job/hash、request、负责人/endpoint/代次、授权引用、工作键、资产范围、动作和有效期；固定 CLI 路径、摘要、版本、已存在的 profile、工作目录及私有动作记录目录。不会创建线程、profile、服务或凭据。

单件策略的 `codex_home` 指向该线程已有的私有 Codex 目录。在本机 CLI 上，`app-server` 使用该目录内的 `config.toml`，具名 MCP 检查使用同目录的 `<profile>.config.toml`。准入清单须覆盖两份配置，且内容逐字节相同。配置指定的动作必须与原件完全相符。

独立审核的 `capability_manifest` 与其摘要必须匹配上述单件绑定，并列出配置文件摘要、原生 `config/read` 的有效配置与层摘要、MCP 工具白名单及动作到工具的映射。适配器核验只读沙箱、关闭网页搜索及规定的内置执行能力，拒绝旧查询 profile、全部工具关闭、配置漂移和过期能力声明。实际组织工具仍须由宿主审核其范围限制与零生产/音频预算；工具名称和声明字段本身不能证明实现有效。预检返回 `HOST_ATTESTED`，不代表现场业务能力已经验收。

本机 `codex-cli 0.166.0-alpha.6` 的 shell 工具由 `shell_tool` 控制。`unified_exec` 选择执行后端，可能显示开启；仅在 shell 工具已经允许时才会注册终端执行能力。[该版本的工具注册逻辑](https://github.com/openai/codex/blob/rust-v0.166.0-alpha.6/codex-rs/core/src/tools/spec_plan.rs#L1243-L1273)。原件的 MCP 函数清单、恢复线程的实际工具、资产与资源范围分别核验；模型请求审批时，适配器停止并回报。

通过同一 app-server 连接恢复绑定的线程 UUID、核对实际沙箱与工具，再以 `turn/start` 发起唯一回合。动作目录先持久化，再投递；未知结果保留原意图，后续只经原生 app-server `thread/read` 取回原回合及同一动作的后续报告，不直接读取原生会话文件。stdout 只是诊断记录。每份业务报告必须同时匹配冻结提示、原生线程/回合、完整 receipt 和连续序号，原件摘要固化后不可更换；原生审批请求不会被本适配器答复。

`pump` 先回传已持久结果，原生通道离线不阻断补传；没有通道或预检失败则沿原任务回传等待报告，不消费新的派发次数。配置格式见 `POLICY_KEYS`、`CAPABILITY_KEYS` 及隔离测试夹具；测试 profile 和模拟工具不能直接用作真实宿主准入。

## 接手、进展和原任务回报

只有可信原生通道验证的 `goal_update` 观察可推进业务状态。来源主体、endpoint、generation、action、project/request 和 report hash 必须匹配。报告有连续 `seq`、观察时间、摘要、原件证据和下一责任人：

- `ACCEPTED`：负责人明确接手，保留下一检查时间。
- `IN_PROGRESS`：已经接手后的实质进展，保留下一检查时间。
- `BLOCKED`：明确阻塞及下一责任人/检查时间；仍是原任务，不能重派。
- `COMPLETED`：已接手，全部验收条件均有对应证据；此终态不允许继续新增业务报告。

同 seq 同报告幂等返回原版本，冲突拒绝；新 seq 不能跳号。晚到旧代、取消后或截止后的新观察只保留审计，不能推进任务。进行中的目标保持原负责人绑定；换任还需已有正式交接和原动作停止证据。

回放时，非终态报告的下一检查点按报告本身的观察时间校验。错过的检查点由已有等待机制处理，连贯的后续报告仍按序采纳。观察时间不得早于本次接单、倒退或晚于协调器当前时间；历史业务证据的原时间保留在证据引用中，不能伪装成本次新接手。

需要独立听检时，完成报告必须声明独立审者、制作来源、实际覆盖范围、方法和原件引用；方法仅接受 `human_listening` 或 `audio_perception`，不接受 ASR 或技术校验冒充听检。合同检查不等于系统亲自听过音频，原生证据及业务验收仍须核实。

每个已验证报告在同一事务中写入原请求的不可变 `results` 版本。沿原桥 outbox、稳定 event ID、core/wire hash 回传同一个 job。回执丢失先查原 outbox，不重派或重做结果。`COORDINATOR_WAIT_REPORT` 可报告期限、取消和等待状态，但不能充当业务完成。信箱 `REPORTED_COMPLETED` 表示这份报告已提交，不代表目标完成；目标状态读 `result.goal_state`。

云端继续沿原 job 读指定版本/hash并确认；读取确认不关闭未完成业务。检查点只触发原任务的确定性提醒，不自动获得新模型、Worker、生成或审批权限。

## 安装与验收边界

先运行两仓标准库回归和跨组件隔离验证，再按固定提交准备 release。保留原台账、历史请求/结果/ACK、OAuth、collector、游标、探针和其他项目准入。不得把本页当作生产部署或凭据配置授权。

实际项目启用前须具备一个经过审核、支持目标的原生通道，以及该单的可信来源、精确 grant、目标绑定和预算。没有这些条件时允许准备代码和完整任务包，不能写成“已交办”。真实验收必须取得现任负责人明确接手、下一检查时间及原 job 的真实结果版本/hash，且验证重复投递不会增加原生动作。

升级时先准备支持本合同的[信箱桥版本](https://github.com/CF-FE/task-mailbox-mcp/pull/4)，再切换协调员到固定发布。保留原授权、台账及写者。空闲配置返回 `NO_PILOT`，正式目标按单件启用。安装器也可核验旧版固定提交。

隔离验证命令：

```sh
python3 -m unittest discover -s tests -v
python3 scripts/worker_library.py check
python3 scripts/check_repository.py
python3 tests/integration_goal_bridge.py --mailbox-root /path/to/task-mailbox-mcp
```

信箱仓另跑 `python3 -m unittest discover -s tests -v`。跨仓夹具在两个独立进程中使用真实签名 Unix socket 与各自 SQLite，模拟原生业务观察和回执丢失；结果止于本地 outbox，不调用外部上传、真实模型或听检。完整上线验收仍需验证真实宿主工具、原生策略继承和原信箱云端回读。
