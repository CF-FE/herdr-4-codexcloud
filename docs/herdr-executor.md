# Herdr 确定性执行适配器

修订日期：2026-11-03。状态：**固定源码与独立安装已验，一次限定执行通过，结果回传待自然 Stop／读回**。正式任务为 [CF-FE/herdr-4-codexcloud#1](https://github.com/CF-FE/herdr-4-codexcloud/issues/1)。本页统一维护该适配器的边界、接口和验收记录；全仓采用状态仍见[本仓状态](status.md)，Worker 定义仍见[双库入口](worker-library/README.md)。

## 决策与退出依据

```text
ARCHITECTURE TRIAGE
Path: ARCHITECTURE_GATE
Evidence: 新增 Lead 到 Herdr 的执行副作用边界，现有 Worker 工具只生成离线准备稿。
Boundary impact: 适配器核验 Lead 明确批准的请求，持久关联原生调用和产物；不接管任务决策。
Next action: 同一最小设计的接受及实施授权已取得，按冻结接口实现、隔离检查并开展获准试点。
```

`ARCHITECTURE_TRIAGE` 的路径为 `ARCHITECTURE_GATE`，其退出条件已满足：公子明确接受 **项目 Lead → 确定性 adapter → Herdr** 的职责边界并授权优化实现；原评审进一步明确采用下述最小设计。此处记录已接受的范围，不把文档落盘当新授权，也不重新申请同一批准。新增自动消费者、远端执行、Agent 创建或取消控制等实质扩展须另核受影响边界。

| 原建议或事实 | 保留、调整或拒绝 | 源依据与原因 |
| --- | --- | --- |
| 复用版本化 Worker／角色准备稿 | 保留 | [离线 `prepare`](worker-library/README.md#离线查询与准备)已有版本、资源摘要和待绑定项；它不派发 |
| 直接从 Mac 桥 jobs 表执行 | 调整为 Lead 提供完整 job envelope | [桥源码](https://github.com/CF-FE/task-mailbox-mcp/blob/main/mac/bridge_store.py)的 jobs 保存身份、路由和状态摘要，不保留完整任务正文；适配器不读取其私密数据库 |
| Codex Stop 作为所有 Worker 的完成协议 | 拒绝 | [既有 hook 约定](https://github.com/CF-FE/task-mailbox-mcp/blob/main/docs/hooks.md)仅覆盖相应 Codex 会话；Stop 不是业务完成或跨 harness 通用回执 |
| `agent prompt --wait` 返回即完成本轮 | 拒绝 | [等待语义](pilot.md#等待未知与恢复)不跟踪单个 turn，可能命中旧活动回合 |
| 首版复用 Mac 已有 Agent、显式调用 | 保留 | 不增加 Agent 生命周期、自动收件或跨机器控制面；原生目标仍须定向核验 |
| 独立 journal、一次 prompt、实际文件摘要 | 保留 | SQLite 与 Herdr 调用不能跨进程原子提交；不确定调用保留 UNKNOWN，不重发；产物按本次关联和真实字节核验 |
| collect 立即释放写权 | 拒绝 | 收集结果与负责人采用、业务验收、写权交还分别取证；Lead 必须显式 handback |

## 职责、许可与范围

Lead 负责读取正式任务及当前 Project 条目、核原迭代／未排期理由、授权与预算、当前写者、目标 Agent、输入、产物和汇报路径；通过原获准入口取得完整 job 记录，准备 envelope 并批准其精确摘要。摘要一致只证明内容关联，不证明信箱来源或文字授权真实。适配器只验证、记录和执行这个确定请求，不再规划任务、不解释正文以提升权限、不替 Lead 更新正式记录。

首版只操作 policy 允许的 **Mac 本地已有 Herdr session／pane／Agent**。Agent 必须处于可接收输入状态，原生身份、pane 和工作目录与请求一致。默认真实执行要求 `HERDR_ENV=1`，来自 Herdr 原生注入；不伪造环境变量、pane 身份，不使用 focused 或默认目标。Herdr 真实版本与字段核对依据固定源 `herdrdev/herdr@ee5bfb5041bb3e4a49d591c72308aae3c81ecdde`，TUI 0.9.5；`AgentInfo.agent` 是 kind，`name` 是 Agent 名，`agent_status` 是观察状态。该固定源 `src/api/schema/agents.rs` 明确 `launch_pending` 缺省 false 且 false 时不序列化；适配器仅按这一原生规则归一化，字段存在时须为 bool，true 仍拒绝就绪。

适配器不接管 Mac 协调桥的台账、OAuth、collector、hooks 或配置；桥仍由 [CF-FE/task-mailbox-mcp](https://github.com/CF-FE/task-mailbox-mcp)维护。适配器不自动收件、不新增 daemon、不创建 Agent、不提供任意 shell、不执行任务携带的脚本，不调用 Cloud／Win，不上传结果或写 GitHub。Project、信箱、Agent 原生状态及负责人验收保持各自权威。

policy、路径白名单、摘要和调用预算是适配器的边界防护，**不是 OS sandbox**，不能阻止另一个程序或 Agent 既有权限范围内的写入。已有 Agent 的模型、认证和权限由 Lead 核验，不因 `--kind` 或声明字段就视为相同配置。

## 冻结输入

所有输入 JSON 使用 UTF-8；canonical SHA-256 按 `sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False` 序列化计算。`payload_hash` 绑定完整 payload；`--approve-sha256` 绑定完整 envelope，任意字段变化都须重新核验。准备稿必须来自当前受控 catalog，其版本及正文资源摘要须逐项匹配；占位原生目标不能执行。

| 对象 | 字段与含义 |
| --- | --- |
| policy | `schema_version=1`、`repository='CF-FE/herdr-4-codexcloud'`、`project_id='herdr-4-codexcloud'`、`herdr_binary`、`allowed_roots`、`targets`；只另允许可选 `max_database_bytes`。空 targets 可安全安装，但不能派发 |
| policy.targets[] | `session`、`pane_id`、`agent_name`、`cwd`、`worker_id`、`backend_id`、`agent_kind`、`provider`、`model`、`auth_ref`、`permissions_ref`；这是 Lead 的明确许可清单，不是原生认证回执 |
| envelope | `schema_version=1`、`intent_id`、`job`、`draft`、`target`、`authority`、`budget`、`outputs` |
| envelope.job | `job_id`、`payload_hash`、完整 `payload`；不以桥摘要替代正文 |
| payload | `schema_version=1`、`origin_task_id`、`sender`、`recipient`、`report_route`、非空 `target`、文本 `content`，本项目执行另需 `tracking`；`target` 与 envelope.target 一致 |
| target | `session`、`pane_id`、`agent_name`、`cwd`，须命中 policy 的明确目标 |
| draft | 原 `worker_request_draft`：catalog 版本／摘要、Worker／backend、角色正文与资源摘要、完整 task、launch plan 和待绑定项；只能选 Mac 已有 Agent 模板 |
| draft.task | `task_id`、`project_id`、`goal`、`authorization_ref`、`dispatch_owner`、`report_to`、`report_route`、`owned_paths`、`input_refs`、`acceptance_criteria`、`output_refs`；与 job、policy 和 authority 关联一致 |
| authority | `task_ref`、`lead`、`writer`、`tracking_read_ref`、`owned_paths`、`start_before`、`deadline`；日期须有 UTC 时区且开始截止不晚于执行截止，不由脚本生成业务排期；`lead` 与 task.dispatch_owner 一致 |
| budget | `model_turns` 为整数 1、`rpc_calls` 为整数 1–100、`timeout_seconds` 为整数 1–60；只限制适配器提交一次 prompt，不通过 retry 或等待主动续轮 |
| outputs | `manifest_path`、1–100 个 `paths`；与 task.output_refs 一致，位于获准 owned paths 内，均是本次尚不存在且互不重复的新文件路径 |

`tracking` 采用与[信箱契约](https://github.com/CF-FE/task-mailbox-mcp/blob/main/SPEC.md)相容的字段，由本仓独立校验，未导入其 validator。必含 `repository`、`project_url`、正整数 `item_id`、`iteration`，以及同仓 `issue_url` 或原生 `draft_id` **恰有一个**；首版只接受本仓 `CF-FE/herdr-4-codexcloud` 和 CF-FE 组织 Project 的 HTTPS GitHub 引用，不接受 query／fragment／userinfo／port。`draft_id` 为 `DI_` 加 1–125 个 ASCII 字母、数字、下划线或连字符。迭代使用实际回读的正整数 `field_id` 与非空、至多 128 字符的 `iteration_id`，或 `{state:'UNSCHEDULED', reason:'明确原因'}`，reason 至多 512 字符；不从总览推造其他项目的日期。字段存在、引用语法正确或摘要通过均不证明 Projects 已实际核读；`tracking_read_ref` 由 Lead 关联原回读依据。

policy、envelope、job、target、authority、budget、outputs 与 manifest 按上述键集校验，未知控制字段拒绝；payload 可保留额外数据字段，但不据此增加权限。`intent_id`／`job_id` 为 1–128 个 ASCII 字母、数字及 `_.:-`，首字符须为字母或数字；拒绝 focused／current／default 等模糊目标和占位控制值。owned paths 须是 cwd 内已有的、最多 100 个不重复目录；路径须为规范绝对路径且没有符号链接。目标 pane 不能是当前 driver 自身。

本次正式任务已由唯一记录写者创建并回读，已关联既有总览且读回 In Progress。尚无 Herdr 专用正式板及原迭代，总览也无 Iteration；本次保持明确 `UNSCHEDULED`，不新建周期。私密 Project、item 与原生执行实值只保存在获准任务和本机配置，不进入公开示例。

## 显式操作

源码入口为 [scripts/herdr_executor.py](../scripts/herdr_executor.py)，只需已有 Python 3.10+ 标准库。以下 ABS、ID、HASH、REF 均为待替换参数，不可原样执行：

```text
python3 scripts/herdr_executor.py --state-dir ABS --policy ABS [--catalog-root ABS] preflight --envelope ABS
python3 scripts/herdr_executor.py --state-dir ABS --policy ABS [--catalog-root ABS] dispatch --envelope ABS --approve-sha256 HASH
python3 scripts/herdr_executor.py --state-dir ABS --policy ABS [--catalog-root ABS] inspect --intent-id ID
python3 scripts/herdr_executor.py --state-dir ABS --policy ABS [--catalog-root ABS] collect --intent-id ID
python3 scripts/herdr_executor.py --state-dir ABS --policy ABS [--catalog-root ABS] cancel --intent-id ID
python3 scripts/herdr_executor.py --state-dir ABS --policy ABS [--catalog-root ABS] release --intent-id ID --evidence-ref REF
```

| 操作 | 行为与副作用 |
| --- | --- |
| preflight | 核 policy／envelope／catalog／授权绑定，定向查询原生 Agent 与 pane；登记 PREPARED。不是零文件写入，也不发送 prompt |
| dispatch | 先将本次发送意图持久记为 DISPATCHING，再以 argv 向准确 Agent 发送一次完整 prompt（包含角色、完整 payload 和完整已核 draft.task）；不用 shell、不加 `--wait`。同 intent／同内容返回原记录，冲突拒绝 |
| inspect | 未释放请求定向 `agent get` 一次并保存观察；已 RELEASED／CANCELLED／EXPIRED 直接返回 journal 摘要。旧响应和 idle 不作为本次结果 |
| collect | 不调用 Herdr；读取指定 manifest 和产物，核本次关联、路径、字节数及实际 SHA-256，保存 COLLECTED；不释放占用或写权。已 COLLECTED 重复调用返回原收集记录 |
| cancel | 发送前可阻止执行；发送后只记 CANCEL_REQUESTED，不调用停止／kill／interrupt，不声称原生 Agent 已停止 |
| release | 仅在已收集结果后，由 Lead 提供 `evidence_ref` 显式登记交还；不操作原生进程、不代替负责人业务验收 |

stdout 只输出结构摘要，包含 `schema_version`、`intent_id`、`job_id`、`payload_hash`、`input_hash`、`state`、`rpc_used`、`rpc_limit`、`cancel_requested`、`cancel_requested_at`、`native`、`artifacts`、`detail`、`next_owner`、`business_accepted=false`；不输出 payload、完整 prompt、终端文本或原始 stderr。`input_hash` 是完整 envelope 的摘要，`next_owner` 始终为 `PROJECT_LEAD`，表示由 Lead 承接下一决定，不表示已释放。release 另记 `hand_back={state:'LEAD_REPORTED_HAND_BACK', evidence_ref:...}`；它表示 Lead 的交还报告，不是自动业务通过。错误只输出安全 `code` 与可用的 native `exit_code`；CLI 退出 0 仍不证明业务通过。

## Lead 结果回传

本首版不直接上传。Lead 核 `collect` 后将结构化摘要和原生执行／产物引用写成私密 result JSON，沿原 job 保留稳定 event ID。桥的原入口为：

```text
BRIDGE_PYTHON BRIDGE_APP/bridge.py --config BRIDGE_CONFIG bind --session-id REAL_LEAD_CODEX_SESSION_ID --job-id ORIGINAL_JOB_ID --cwd ALLOWED_PROJECT_CWD
BRIDGE_PYTHON BRIDGE_APP/bridge.py --config BRIDGE_CONFIG stage-report --session-id REAL_LEAD_CODEX_SESSION_ID --turn-id REAL_LEAD_CODEX_TURN_ID --event-id STABLE_RESULT_EVENT_ID --status REPORTED_COMPLETED --result-file PRIVATE_RESULT_JSON
```

session／turn 必须来自该 Lead 自己的真实 Codex hook 观察；Herdr target.session／pane／Agent 不能替代。`stage-report` 只代表等待绑定会话的自然 Stop，不能伪造 hook 或声明已发送。Stop 后由原 collector 以同 event/hash 补传，接收者读精确版本、核全件摘要并 ACK；上传回执、实际读取、业务验收分开记录。取消或失败使用对应的报告状态，不能把 COLLECTED 直接映射为业务完成。

## 状态、产物与保全

状态集为 `PREPARED / DISPATCHING / SUBMITTED / UNKNOWN / FAILED_NOT_DISPATCHED / EXPIRED / CANCEL_REQUESTED / CANCELLED / COLLECTED / RELEASED`。只读预检失败用 FAILED_NOT_DISPATCHED；启动期限已过且未发送用 EXPIRED。DISPATCHING 在原生调用前提交；发送阶段出错或崩溃后恢复为 UNKNOWN，不能据错误发生时间推断没发送，也不重新 prompt。同 intent 后续查询沿原 journal 和原目标对账。每次使用存量记录先核完整 envelope 的 canonical hash 与原 input_hash、intent ID；损坏拒绝，不按漂移路径读取产物或查询目标。这只检测意外漂移，不防止同 UID 同时重写内容和摘要。

manifest 使用 `schema_version=1`、`intent_id`、`job_id`、`payload_hash`、`status`、`files`、`summary`；status 只接受 `REPORTED_COMPLETED / REPORTED_FAILED`，每个 `files[]` 含 `path`、`bytes`、`sha256`，REPORTED_COMPLETED 的路径清单及顺序与 outputs.paths 精确一致；REPORTED_FAILED 可为计划内按原相对顺序的实际产物子集或空集，仍拒绝额外／重复路径并逐件核字节。报告状态仍是调用方报告；适配器核实际字节，不把名称、最后一条模型文本、shell 回显、native idle 或退出 0 当结果。缺 manifest、错误关联、越界路径、长度或摘要不一致均不能 collect；成功报告缺计划文件也拒绝，失败报告可保留上述合法子集／空集。

同一 journal 内，未释放请求保留目标及重叠 owned paths 的占用；只有 RELEASED、CANCELLED、EXPIRED 不再占用。COLLECTED 和 UNKNOWN 不自动释放，截止、取消请求或失联也不等于写者已停止；cancel_requested／cancel_requested_at 独立持久保存，collect 不抹去取消请求。跨 journal、其他协调者或直接 Herdr 控制不受此锁保护，Lead 仍须落实唯一派发者与当前写者；不能并行建另一 state 目录绕过占用。

## 安装与资源

安装根由主线程确定为独立 **HerdrExecutionAdapter**，使用 `app/scripts/`、`app/docs/worker-library/`、`state/`、根目录 `config.json` 与 `install-receipt.json`。复制固定源码和完整 catalog，记录 commit、文件摘要与实际解释器；安装态显式给出 `--catalog-root`，不依赖来源 checkout。不建立跨仓运行导入、软链接或自动同步，不新建 venv，不改 Mac 协调桥目录与 launchd。目录名和文件落盘不能代替真实安装回读。

安装命令（参数均须替换为实际固定值）：

```text
python3 scripts/install_herdr_executor.py --revision FULL_40_HEX_COMMIT --destination /ABS/HerdrExecutionAdapter --herdr-binary /ABS/herdr --allowed-root /ABS/PROJECT
/ACTUAL/PYTHON -B /ABS/HerdrExecutionAdapter/app/scripts/herdr_executor.py --state-dir /ABS/HerdrExecutionAdapter/state --policy /ABS/HerdrExecutionAdapter/config.json --catalog-root /ABS/HerdrExecutionAdapter/app/docs/worker-library --help
```

安装器只接收完整固定 commit，复制 Git 对象，拒绝已存在的目标目录与不可执行 Herdr 文件；不覆盖旧配置或 journal。receipt 记录实际 `sys.executable`、固定源与逐文件摘要。初装 policy.targets 为空，后续只有 Lead 已核的具体目标才能准入。

真实 CLI 要求 policy 为 mode 0600 的私有文件；state 目录 mode 0700，`executor.sqlite3` 与 `executor.lock` 为 mode 0600。journal 含完整 envelope／payload，只在私密 state 保存，不发布到仓库。每次命令取得同一 state 的非阻塞独占进程锁，冲突返回 `EXECUTOR_BUSY`；单实例范围仅是这个目录，不是跨机器 fencing。原生调用不持有 SQLite 事务，调用预算与意图先持久记账；不自动删除、vacuum 或清理原记录，不新建日志文件。

| 预算 | 首版限制与实际含义 |
| --- | --- |
| SQLite 主数据库 | 默认 32 MiB；policy.max_database_bytes 可配置，整数且至少 128 KiB。满额拒写，不删旧记录；不含 rollback journal、catalog、产物或其他进程文件，无 intent 条数自动清理 |
| 输入／catalog／prompt | policy、envelope、manifest 单 JSON 和 catalog 单文件各至多 1 MiB，完整 canonical envelope 及拼装 prompt 也至多 1 MiB；catalog 成员至多 256，拒绝符号链接 |
| 产物 | paths 至多 100 个，每份文件至多 1 MiB，manifest.summary 至多 64 KiB；无额外总产物字节配额 |
| RPC | 每 intent 的调用次数持久计数，调用前先扣；rpc_calls 为 1–100。单独 preflight 用 2 次只读 RPC；随后 dispatch 会重新预检 2 次并发 prompt 1 次，先后合计 5 次，inspect 每次另用 1 次。直接 dispatch 为 3 次；预算不足就失败，不自动补预算 |
| native 等待 | 一次 native 操作流程共享 monotonic 超时，timeout_seconds 为 1–60；每个调用不超过剩余时间；新投递受执行截止约束，超期后的只读 inspect 仍在持久 RPC／本次 timeout 预算内对账，不无限轮询 |
| native 输出 | stdout／stderr 捕获后各核至多 1 MiB；这是事后长度检查，不是流式内存硬上限或整机 RSS 保证 |
| 模型行为 | model_turns=1 约束适配器只提交一次 prompt；不能硬性统计或阻止 Agent 内部工具、子任务或模型行为，仍须依赖既有权限、prompt 合同及 Lead 监督 |

适配器资源上限不等于整台 Mac、Agent 进程或模型上下文硬上限；安装、模型执行和持续运行的实际资源证据分别记录。

## 验收记录

用 `PASS / PARTIAL / FAIL / UNKNOWN / NOT_RUN` 记录逐项证据；进行中为 IN_PROGRESS，不能将设计接受、源码测试、文件安装、真实调用和负责人验收合并。

| 层次 | 当前状态 | 最小证据与边界 |
| --- | --- | --- |
| 决策与实施授权 | 已接受 | 上述 gate 退出依据；不扩大到自动收件、Win／Cloud 或任意业务 |
| 源码与隔离合同检查 | PASS | 固定源码 c89d5c5；主执行与原 review 主线程分别直接61项通过，库／结构检查通过；模拟不代替真实运行 |
| 独立安装 | PASS | 24文件同时匹配receipt与固定Git对象；实际解释器、权限及安装后调用已核，Mac桥不变 |
| Herdr 原生入口与预检 | PASS | 真实driver注入ENV1，client/server均0.9.5且协议兼容，准确Agent/pane/cwd/kind匹配，预检2RPC |
| 一次合成模型回合 | PASS（限定） | 唯一prompt，完整payload／角色／task合同；41字节变换产物与独立计算的期望一致，累计6RPC；未用旧turn/等待代替 |
| collect 与 handback | PASS（分记） | 实际长度／SHA匹配；COLLECTED仍占用，随后Lead显式RELEASED，原生试点资源按创建ID清理 |
| Lead自然Stop／结果补传／读取 | PARTIAL | 已bind及stage-report，精确结果保全；仍WAITING_FOR_BOUND_SESSION_STOP，collector近期失败沿原退避，尚未声称上传或ACK |
| 负责人限定验收 | PARTIAL | 源码/安装已有独立读回；native/产物与自然Stop后的结果读全/ACK仍待原review绑定固定版本验收 |

公子已明确授权一次 desktop 创建并控制的独立 Mac 测试 session、项目测试目录、既有 Codex 模型及认证，**最多一个合成回合**。本次已从真实 Herdr 注入的 driver pane 执行适配器，保持默认 ENV1 条件；本文不伪造环境、不触发该试点。试点不改已有业务 pane、模型／认证、Mac 桥、网络或任务入口，不重复不确定发送。清理只限按真实创建 ID 绑定的测试资源，先保全 journal、原生回执、manifest 与产物；活动／UNKNOWN 先对账，不能靠关闭测试资源掩盖未决效果。

验收记录由主线程按实际发生结果就地更新，不另建“补充版”。源、安装、执行侧与负责人证据分别归属，未取得的 native facts 保持 NOT_RUN／UNKNOWN。

### 2026-11-03 固定版本及真实试点证据

实现提交为 `c89d5c5a63072f6f42afcd4a7e03360919c4f746`，已正常推送并独立 `git ls-remote` 回读。文件为 README、status、本规范、`scripts/herdr_executor.py`、`scripts/install_herdr_executor.py`、`tests/test_herdr_executor_contract.py`、`tests/test_herdr_executor_native_schema.py`；本次文档收尾不改变安装代码版本。源码 SHA-256 `68f56e6bd63074c871363952fffabfb2aed7ebf3c75ede6e72902c131e98596a`，installer `beecdd202ff66a4ad107543c438934024551a70b5b146b977e1e3ce34b870481`。

主执行侧直接通过规定三命令：`python3 scripts/worker_library.py check`（7/9/13/11）、`python3 scripts/check_repository.py`（mcp0/skill2/plugin0）、`python3 -m unittest discover -s tests -v`（61 PASS）。其中新增34项含目标／预算／撤权、一次发送与硬进程退出UNKNOWN、完整task实际投递、失败子集、取消保留、journal漂移和真实native缺省字段。审查发现的完整任务未传、无产物失败不能回收、存量journal未校验、原生false字段省略均先复现再修。原review主线程独立全61项、task／FAILED两项通过；其子审查journal／installer9项通过并由主线程绑定摘要复核；自检没有替代独立复核。

主执行侧从固定Git对象独立安装24文件，并逐一核receipt／Git／磁盘字节；原review主线程又只读核同24文件、实际解释器及SQLite integrity。无新venv、依赖、daemon或自动收件。安装前不可执行Herdr文件被拒，安装后CLI实际由原生driver调用；试点结束policy.targets清空，仅保留显式工具及历史journal，不留下长期outside控制许可。

本次使用公子额外明确授权的新命名Mac测试session；原生client/server0.9.5兼容，既有Codex与认证、实际模型/权限只在私密回执绑定。Lead通过原App实际submit/get同一完整payload、核hash及未取消，再从真实Herdr driver调用已装CLI。两次预检合计4RPC、一次prompt1RPC、一次inspect1RPC；只发1个模型prompt，无二发、备用模型、认证/网络切换或生产资产操作。输入只给随机片段及变换规则，不含完整预期输出。模型生成的文件为41字节，SHA-256为 `0727f68372173e47ddc55f339d19d8f91e48dde54b5a2c633d2f389282e35a68`，与Lead独立计算的字节完全一致；manifest身份、实际长度和摘要通过collect。COLLECTED与Lead显式RELEASED分记，business_accepted仍false。

先保全原件、journal和回执，再只按本次真实创建ID关闭测试workspace，停止/删除本次独立session，回读空布局及session目录不存在；原有pane/server未动。产物和私密证据留在获准本地位置，未进入public仓库；项目临时agent-state与既有local目录显式排除发布。

结果已使用本Lead真实Codex session／turn的原生hook观察bind及stage-report，Herdr目标session没有冒充这些身份。桥附加task_tracking后的精确canonical结果为2973字节，hash与稳定event保存在私密 `result-handoff.json`、`expected-result.json`。**当前仍等待本会话自然Stop；没有直接publish_result、伪造Stop或第二collector。** 原review按约定随后读精确版本、核全件与hash并ACK，再由实施侧核原outbox／同步正式记录。此阶段未通过前，任务继续Open／In Progress。

传输另有自然失败观察：2026-11-03T02:12:30Z最后成功，02:16:30Z collector为 `NETWORK_FAILED / UNKNOWN_CAUSE stage:CLIENT_OPEN type:RuntimeError`，仍沿原退避；不是明确认证诊断，不推定注册或凭据失效。本页保留PARTIAL，不因执行层PASS声称出件成功或长期稳定。
