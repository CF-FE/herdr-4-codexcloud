# Worker 与角色库

版本：Worker 库 **1.0.0**，角色库 **1.0.1**；更新日期：2026-11-03。

供云端Dot与 Mac 项目协调层按任务选择执行端、组装角色与任务包，再沿获准原生入口创建 Worker、派发和回收。当前包含 **7 个 Worker 模板、9 个后端配置、13 个角色定义，其中 11 个专业角色可参与选型，2 个仅供组织导航**。

选型关系为：**Worker 模板 + 明确后端 + 专业角色 + 必要项目合同 + 本次任务／权限／原生目标**。库维护定义；实际实例、在途执行、写者和验收仍归原任务。源码与 JSON 均随本仓读取，无网络服务、运行数据库、模型调用或跨仓导入依赖。

## 辩证结论与采用边界

| 取舍 | 本库采用 |
| --- | --- |
| 角色固定到某个模型，还是按任务组合 | 角色独立描述职责、方法与交付；模板独立描述宿主、harness 和模型入口，不预建全部组合 |
| 配置存在，还是已经可调度 | 区分安装、识别、原生文本、Herdr 回收、工具和业务证据；`active` 是模板生命周期，实时状态始终需另核 |
| 每项职责常驻一个 Agent，还是按需实例化 | 不把角色数变成进程数；上下文隔离、依赖和负载决定实例数，独立 QA 看是否参与该版本制作 |
| 统一入口，还是重建调度平台 | 增加离线查询、筛选和准备稿；实际创建／派发沿 Herdr 或 Cloud 原生入口，信箱只传数据 |
| 复用全部旧角色规则，还是保留职责后适配 | 接收固定 14 文件闭包；保留项目硬约束，移除旧平台 Save、模型排序和隐含任职，来源与改编分别记账 |
| 自动挑“最快／最便宜”，还是先满足任务约束 | 先按角色需求、宿主、资产和声明能力硬筛，显示证据缺口；不从单次耗时推性能排名，不自动选首项或改模型 |

边界记录（`BOUNDARY_NOTE`）：本轮新增本仓的离线知识接口与两个版本化注册表；组织任命、正式项目状态、实际创建权限、控制入口、运行凭据和外部副作用所有者均不变。没有接入新的自动派发器，也没有为建库启动模型或迁移旧任务。

## 唯一维护位置

| 内容 | 维护位置 |
| --- | --- |
| Worker 模板、后端、声明能力、依赖与验证快照 | [workers.json](workers.json) |
| 角色元数据、版本、生命周期、项目和可选方法 | [roles/index.json](roles/index.json) |
| 通用专业合同 | [roles/common.md](roles/common.md) |
| 每项专业职责的唯一正文 | `roles/workspace/` 与 `roles/projects/`，按索引精确读取 |
| 原件身份、首次改编摘要与排除项 | [source-manifest.json](source-manifest.json) |
| 两库的版本变化与采用记录 | [CHANGELOG.md](CHANGELOG.md) |
| 本次任务的完整字段示例 | [示例任务](examples/review-task.json)，是假数据，不是授权 |
| 实际创建／派发／回读约定 | [试点指引](../pilot.md) · [任务模板](../templates/pilot-task.md) |
| 当前环境、信箱接入与真实证据 | [本仓状态](../status.md) · [盘点快照](../local-agent-inventory.md) |

组织岗位及责任复用[组织协议](../../skill/organization-collaboration/SKILL.md)。专业角色不授予组织职位、工具访问、写权、生产、安装或发布权限。外部方法的 `bundled: false` 表示未随库交付，不声明目标 Worker 已安装。

## Worker 初始目录

| 模板 ID | 位置与入口 | 选用条件与当前证据 |
| --- | --- | --- |
| `codex-cloud` | 原生 Cloud 任务，目标已发布环境 | 输入／依赖适合云端的实现、测试、文档、复核候选；具体环境、模型、权限与调用未由本库验证，不默认具备任意 MCP／Space |
| `mac-codex` | Mac Herdr 内的现有 Codex CLI | 本机代码、文档、研究、复核候选；安装与类型识别已验，模型、工具和业务回合另验，默认模型不在库内猜测 |
| `mac-kimi` | Mac Herdr 内的现有 Kimi CLI | 复用该端现有模型与认证；本机识别不继承 Win 的模型回合证据 |
| `win-omp` | Mac 原生 Herdr → Win，`omp` | `cli-proxy-chat`／`cli-proxy-responses` 两个显式入口，模型均为已报告的 `gpt-6-luna`；文本回合为旁支报告，Herdr 回收未验 |
| `win-hermes` | 同一机器主链，`hermes` | 复用公子指定的新默认 CLI Proxy／`gpt-6-luna`，不保留旧默认或自动回退；原生文本回合已有执行侧报告 |
| `win-kimi` | 同一机器主链，`kimi` | `official` 与 `local-gpt` 分列；两者文本回合已有执行侧报告，凭据与模型入口不混用 |
| `win-command` | 同一机器主链的获准 shell pane | 已有 PowerShell／Python／FFmpeg 等确定性处理候选；无需为工具变换额外开模型，不据安装记录宣称生产模型或媒体感知已就绪 |

Mac 持续在线，OMP／Hermes／Kimi 代理分支可以依赖 Mac CLI Proxy；Kimi 官方分支不强加该依赖。Mac → Win 仍经本地 saved-machine／LAN SSH，VPS 信箱不是机器中转。Windows Codex、ZCode、DeepSeek Harness 本轮不进入选型；VPS、MCP 服务和专业角色也不当作 Worker 实例。

`capabilities` 是用于初筛的**声明能力**，包括 `text-reasoning`、`repository-read`、`repository-edit`、`shell`、`artifact-review`、`research`、`file-transform`、`visual-inspection`、`audio-tools`。命中标签后仍须核所选后端的工具、依赖、权限和真实输入；例如 FFmpeg 存在不证明 Qwen、声线或听觉审查可用。各角色正文与项目合同中的额外条件不会因标签命中而消失。

<a id="角色初始目录"></a>

## 角色目录

| 类型 | 角色 ID | 用途 |
| --- | --- | --- |
| 跨项目专业职责 | `software_implementation`、`artifact_processing` | 本次新增的代码实现、确定性文件／媒体变换职责 |
| 共享专业职责 | `workspace_visual`、`workspace_research`、`workspace_quality`、`workspace_runtime`、`workspace_knowledge`、`workspace_dialectic` | 视觉、研究、独立质量复核、运行诊断、知识整理、方案辩证 |
| 显式项目适配 | `fanren_canon_story`、`fanren_script_performance`、`fanren_audio_production` | 仅在选择 `fanren-work` 并取得其当前输入、授权及冻结合同后使用 |
| 组织导航，不派发 | `cloud_dot_organization_lead`、`fanren_owner` | 云端Dot与凡人负责人的职责导航；只读 `role` 也支持旧导航 ID，`select`／`prepare` 不可将导航当执行角色 |

共享角色按需加载 `common + 指定角色`；显式项目再加该项目合同。项目角色必须给匹配的 `--project`；任务文件已指向库内已知项目时，也必须显式选择该项目，避免漏装项目硬约束。未知项目的共享角色仍须由原任务提供该项目规则，不自动套用凡人项目。

## 离线查询与准备

使用已有 Python 3.10+ 标准库，从仓库根运行；脚本默认按自身位置找库，与当前 shell 目录无关。输出为 JSON，可由 Dot、项目协调 Agent 或脚本读取；无需安装新依赖。

```bash
python3 scripts/worker_library.py check
python3 scripts/worker_library.py list --host win-work
python3 scripts/worker_library.py roles
python3 scripts/worker_library.py roles --project fanren-work
python3 scripts/worker_library.py role workspace_quality --render
python3 scripts/worker_library.py role fanren_audio_production --project fanren-work --render
python3 scripts/worker_library.py select --role software_implementation --host win-work
python3 scripts/worker_library.py select --role artifact_processing --worker win-command
python3 scripts/worker_library.py prepare --worker win-omp --backend cli-proxy-responses --role workspace_quality --task-file docs/worker-library/examples/review-task.json
```

`--root` 可在子命令前指定另一份完整本地库作离线检查。`select` 只返回候选和证据，不排序推荐、不启动实例；没有匹配项时返回空列表，不放宽条件找替代。多后端 Worker 的 `prepare` 必须明确 `--backend`，不选列表首项。停用模板／角色不参与新选择，组织导航只可查阅。非法版本、重复 ID、缺资源、项目错配、路径逃逸或符号链接失败非零，错误为 JSON，不输出部分提示词。

`prepare` 返回 `worker_request_draft`，包含两个库版本、索引摘要、Worker／后端配置、角色正文及逐资源摘要、任务数据、待绑定项和原生调用参数模板；**不创建运行实例、不提交信箱、不投递 prompt，也不验证文字授权的真实性**。状态始终为 `REQUIRES_RUNTIME_PREFLIGHT`，实时状态为 `UNKNOWN`；模板中的原生 ID、专用 pane、目录、实际模型／权限等须按原任务回读绑定。不要直接执行带占位符的模板，或把 `agent prompt --wait` 状态当作本次结果验收。

原生 Herdr 参数依据本机 0.9.5 `--help` 核过：`agent start NAME --kind KIND --pane ID -- [AGENT_ARG]...` 在**已有交互 shell pane**启动，`--machine` 明确定向远端；Kimi 2.4.1 `--model` 选择配置别名。语法核对不替代 Win 端实际映射与回合验证。Cloud 由实际提供的获准原生任务工具创建；库不伪造统一 shell 创建命令。`win-command` 则须绑定原任务允许的具体脚本与 pane API。

Win Kimi 代理分支的原生文本结果不提供精确 CLI 别名证据，因此启动参数暂留 `<VERIFIED_LOCAL_GPT_MODEL_ALIAS>`；请求稿明确列为待绑定，不把 provider 名 `local-gpt` 或库内后端 ID 当成已核的模型别名。

## 从选择到真实派发

本项目已有 Mac Codex Agent 可由 Lead 按[执行适配器的输入合同](../herdr-executor.md#冻结输入)及[显式操作](../herdr-executor.md#显式操作)承接准备稿；该首版已取得一次限定工程验收，不创建 Agent，也不覆盖 Win／Cloud。下面描述各类 Worker 的通用交接过程，真实创建仍属于各自获准原生入口。

1. Dot 或已确认的项目协调者读取任务范围、验收、资产位置和预算，选择角色；项目任务同时绑定相应合同。
2. 初筛模板／后端，核实际宿主、工具、模型、认证、容量及目标输入可达性；只查询选中目标，模板不是实时占用账。
3. 准备请求稿，固定库／模板／角色版本和内容摘要；补原任务、唯一派发者、写者、原生目标、报告路由、产物和停止条件。
4. 具备有效授权与条件的实际执行者沿 Herdr／Cloud 原生接口创建独立 Worker，保存真实返回引用。信箱如可用，只负责交接这些任务数据；同一未决执行不因入口变化重派。
5. 回收原生结果、固定产物及检查证据，由指定接收者实际读取。自检、独立复核、负责人验收与写权交还分别记录；库内不维护另一份活动任务队列。

`workspace_quality` 必须是未参与被审版本制作／修改的上下文；改角色名、换模型名或自称审查者不产生独立性。项目音频的全文／完整话轮、完整解码、真实听审与生产冻结等要求保留，未知工具或缺件只限制相应动作，不生成生产许可。

## 维护与迭代

| 变化 | 修改位置与版本规则 |
| --- | --- |
| Worker 的入口、模型、依赖或适用能力改变 | 改对应 `workers[]`，递增模板 `version` 和 Worker `library_version`；不复用同 ID／同版本表达不同语义 |
| 角色职责、验收要求或可用状态改变 | 改唯一正文／角色条目，递增 `definition_version` 与角色 `library_version`；保留 ID，破坏性替换用新 ID 或主版本 |
| 所有角色共用合同改变 | 改 `common.md`，递增 `common.version` 与角色库版本；按影响记录采用范围 |
| 项目合同改变 | 改该项目唯一合同并递增项目／角色库版本；必须有对应项目权威与替代依据，不能静默删硬要求 |
| 新增／退役 | 新条目独立 ID；`lifecycle` 为 `active / paused / retired`，停用不等于停止已运行实例，在途仍按已固定任务包处理 |
| 新验收证据 | 对应后端更新证据状态、归属、观察日和引用，并记本次覆盖；不把别端或旧路径结果升级为主线通过 |
| 公共 JSON／查询接口不兼容 | 更新 `schema_version` 与 CLI／测试；不要让旧消费者静默按新字段执行 |

每次变更在 [CHANGELOG](CHANGELOG.md)记录：对象、前后版本、理由、影响、证据和未验范围。定义更新只供后续明确接手点采用，不能给运行中 Worker 热换角色或替换原模型。准备稿内保留完整提示词与摘要；发布时再关联本仓固定提交。未提交的工作区不能称已发布版本。

初始来源清单保留首次源字节和本仓初版改编摘要，不随日常正文修改重写；后续采用外部增量须另留版本与摘要记录，不自动同步。角色库来自专业职责闭包，未接收 Lody 代码、队列、运行身份、执行规范或第三方方法全文。角色定义完整不等于外部项目依赖／模型／生产能力已具备。

```bash
python3 scripts/worker_library.py check
python3 scripts/check_repository.py
python3 -m unittest discover -s tests -v
```

检查覆盖结构、精确选择、版本／资源、准备稿与失败行为；不会 SSH、启动 Agent、调用模型或写外部应用。真实可调用性和业务采用仍沿[当前状态](../status.md)和对应原任务验收。
