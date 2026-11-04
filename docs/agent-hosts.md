# 角色在哪里运行，怎样创建和调用

公子确定目标和授权，云端Dot沿自己的原生入口交办、查进度、向公子汇报。本仓组织中的协调员、项目负责人和本地执行／审查 Agent 实际履职时，由 **Herdr** 编排。唯一的执行例外是 **Codex 路由提交的 Codex Cloud 作业**，只有该作业在云端运行。下表用来找到已有目标，任职与权限仍以原项目的有效记录为准。[组织宿主规则](../skill/organization-collaboration/references/context-and-reporting.md#组织-agent-的原生宿主) · [协作分工](coordination-policy.md)

## 谁发起、谁实际工作、谁验收

| 组织岗位 | 实际形态与调用关系 |
| --- | --- |
| 总负责人 `organization_lead` | 本组织由云端Dot担任。公子在原入口给目标，云端Dot通常交给协调员，符合小迭代条件时也可直派 Cloud。保留同一发起身份。无需在 Mac 另建同名 Agent。 |
| 项目协调员 `project_coordinator` | 云端Dot指定或转达授权。宿主管理者核既有任职和在途任务，沿登记的 Mac Herdr Agent 恢复或办理首次绑定。协调员联系项目负责人、统筹资源、指挥获准的路由执行者，跟进到交付。 |
| 项目负责人 `project_owner` | 协调员找当前任职的 Herdr Agent 交目标。负责人分派执行和独立审查，默认验收项目成果。更换负责人须经有权任命方确认、交接原在途工作与写权，并核新任实际接手。 |
| 执行者 `executor` | 负责人或获准的唯一派发者绑定任务、专业角色和具体 Worker。执行者在原生目标交产物、版本、自检与待决项。 |
| 审查者 `reviewer`、流程审计者 `auditor` | 负责人按需另派独立审查者核指定版本。审计者按组织授权核跨来源记录与整改。两者按各自任务回报。审查、审计结论和最终放行分别留证。 |

云端Dot可在原任务另指验收者，自己不承担业务验收。非项目任务须明确指定。Codex 路由是一项派发／回收职责，同一目标只有一个获准的实际提交者。若由项目协调层 Agent 担任，路由执行者本体仍在 Herdr 内，只有它提交的具体作业进入 Codex Cloud。任命记录、当前运行绑定和实际接手回执分别回答“谁负责”“去哪找”“已接到什么”。[六个岗位正文](../skill/organization-collaboration/SKILL.md#按本次岗位读取) · [任职与交接](project-coordinator.md#agent-lifecycle)

## 选哪一个角色、Worker 和后端

每件任务从 13 个角色库条目中按需选择。下表前两行共 11 个可派专业角色，共用同一套流程。先在任务包中加载所选角色正文，选择能处理实际输入的 Worker 和后端，再绑定原生实例。跨项目借专长时只交本单材料与权限，验收仍归原项目。

| 类别 | 索引 ID 与名称 |
| --- | --- |
| 跨项目通用（8） | `workspace_visual` 视觉设计师、`workspace_research` 研究分析师、`workspace_quality` 质量审查员、`workspace_runtime` 运行工程师、`workspace_knowledge` 知识管理员、`workspace_dialectic` 辩证复核专员、`software_implementation` 软件实现、`artifact_processing` 产物处理 |
| 凡人项目专用（3） | `fanren_canon_story` 原著考据与剧情策划、`fanren_script_performance` 演义编剧与人物表演、`fanren_audio_production` 声音生产与字幕后期。选用时显式绑定 `--project fanren-work` 和本次项目合同。 |
| 仅供查阅的组织导航（2） | `cloud_dot_organization_lead` 云端Dot（组织总负责人导航）、`fanren_owner` 凡人项目负责人。实际联系上表的在任者，库不接受用这两项派发 Worker。 |

后端 ID 在所属 Worker 下解释。七个模板共有九个后端组合。查环境当前能力、证据和维护入口，沿[环境能力台账与宿主说明](worker-library/README.md#environment-capabilities)进入 CF-FE Project；下表只列本版的执行模板。

| Worker 模板 ID 与名称 | 后端 ID | 运行与调用 |
| --- | --- | --- |
| `mac-codex` Mac Codex 协调与执行模板 | `current-default` | Mac Herdr 命名 Agent，`--kind codex`。 |
| `mac-kimi` Mac Kimi 协调与执行模板 | `current-default` | 同一原生方式，`--kind kimi`。从本机核实际后端。 |
| `win-omp` Win OMP 原生 Harness 模板 | `cli-proxy-chat`、`cli-proxy-responses` | 经已保存的 Win 机器定向到原 Herdr，`--kind omp`。明确选定协议。 |
| `win-hermes` Win Hermes 当前默认模板 | `current-default` | 同一远端方式，`--kind hermes`。按目标机已核默认配置启动。 |
| `win-kimi` Win Kimi 双入口模板 | `official`、`local-gpt` | 同一远端方式，`--kind kimi`。分别核认证与模型别名。 |
| `win-command` Win 命令与文件处理模板 | `shell` | 在指定 Herdr shell pane 执行获准的具体脚本，收取退出状态和产物，无模型进程。 |
| `codex-cloud` Codex Cloud 原生任务模板 | `existing-environment` | 唯一云端作业例外，按下节的实际环境和任务 ID 创建／回收。 |

`--kind` 选择交互程序。供应商、模型、认证、工具和目录权限按本轮目标实核。库的 `select` 选候选，`prepare` 只生成含原生参数占位符的准备稿。旧 Lody 的迁移资料提供职责参考，在途旧单沿原入口收尾。[角色索引](worker-library/roles/index.json) · [Worker 与后端定义](worker-library/workers.json) · [离线选型示例](worker-library/README.md#离线查询与准备) · [旧任务交接](organization-collaboration/README.md#退出与运行交接待办的责任)

## 从原任务找到实例并收回结果

**Herdr。** 一个持久 session 可容纳多个 workspace，每个 workspace 下有 tab，tab 中有 pane，命名 Agent 是该 pane 内运行的交互程序（harness）。多个岗位可在一个 session 分别绑定目标。只有需要独立执行、独立审查或隔离工作时，才按授权分配专用 pane。相同 Agent 名可能存在于不同机器／session，须与原任务、主机、原生目标和任职代次一并核对。

1. 从原 Issue／Project 取目标、输入版本、授权和唯一派发者，查任职、已有实例、未决创建及资产写者。首次确需开专用 session 时，只有获准的宿主管理者可从外部真实终端执行 `herdr --session "$SESSION"` 启动或接入指定 session。后续发现和控制由真实 pane 内的 driver 执行。`test "${HERDR_ENV:-}" = 1` 失败时记录缺哪个入口，停在待绑定。下面按需分步运行，变量使用原任务核实的值。

   ```sh
   herdr session list --json
   herdr workspace list
   herdr tab list --workspace "$WORKSPACE"
   herdr pane list --workspace "$WORKSPACE"
   herdr agent list
   herdr agent get "$AGENT"
   ```

2. 复用身份相符的 Agent。确证无可用绑定、无未决创建且本单有创建权时，选择归本单且处于交互 shell 的 pane。若已获准新增独立 tab，可用 `herdr tab create --workspace "$WORKSPACE" --cwd "$TASK_DIR" --no-focus`，从返回的 `root_pane` 取 ID。远端先核已启用的 saved-machine 和兼容的运行中服务，从 `herdr --machine "$PROFILE" agent list` 取目标，后续对同一机器操作。

   ```sh
   herdr agent start "$AGENT" --kind codex --pane "$PANE" -- \
     --no-daemon \
     -c 'shell_environment_policy.inherit="all"' \
     -c 'shell_environment_policy.include_only=["PATH","HOME","USER","USERNAME","LOGNAME","SHELL","TERM","LANG","LC_*","TMPDIR","PWD","HERDR_ENV","HERDR_SESSION","HERDR_WORKSPACE_ID","HERDR_TAB_ID","HERDR_PANE_ID","HERDR_SOCKET_PATH","HERDR_TERMINAL_ID","CODEX_THREAD_ID","CODEX_HOME"]'
   ```

   这是 Mac Codex 的现场做法。默认 TUI 即使显示在 pane 内，共享 daemon 的工具进程也可能丢失 pane 身份。用本机 CLI 支持的 `--no-daemon` 启动，只在本次进程透传 Herdr 实际注入的 `HERDR_ENV`、`HERDR_SESSION`、`HERDR_WORKSPACE_ID`、`HERDR_TAB_ID`、`HERDR_PANE_ID`、`HERDR_SOCKET_PATH` 等必要字段，不手填值或改全局环境策略。由 Agent 在自身工具进程核实 `HERDR_ENV=1`、pane 匹配及 Herdr 的 `agent_session` 与本轮 Codex thread 一致，再以原生答复确认接手。其他 harness 使用各自已经核实的参数。

3. 先记录派发意图，核目标空闲且能接此任务，提交一次含原任务引用和交付要求的指令。读原生接手答复和本次产物／报告，交指定接收者。超时或掉线保留原意图，先查原目标、产物和写权。重建需先隔离旧写者、关联原未决效果并取得授权。终端 `done/idle` 或 prompt 等待结束均不能代替本次接手、验收与交还。[操作与恢复语义](pilot.md) · [显式适配器的证据](herdr-executor.md#验收记录)

   ```sh
   herdr agent prompt "$AGENT" "$TASK_BRIEF"
   herdr agent read "$AGENT" --source recent --lines 80
   ```

**Codex Cloud。** 唯一获准的路由执行者先核原任务、已发布且匹配该仓／分支的环境、创建权限和未决意图，才创建一次。保存平台实际返回的任务 ID。按同一 ID 拉状态和目标尝试的变更，将精确提交／PR／产物、验证及未验项交原负责人或指定接收者。合并、应用变更与发布各按本单授权办理。

```sh
codex cloud list --env "$ENV_ID" --json
codex cloud exec --env "$ENV_ID" --branch "$BRANCH" "$TASK_BRIEF"
codex cloud status "$TASK_ID"
codex cloud diff "$TASK_ID" --attempt 1
```

这些命令的语法已用 Herdr 0.9.5 和 Codex CLI 0.166.0-alpha.6 的静态帮助核对。Cloud 创建返回未知时，由原提交者核相同环境、执行和产物，再决定如何处理。一次列表未命中不足以重发。本仓 Cloud 环境已有发布与工作区编辑记录，协调程序自动触发 Cloud 尚待验证。Mac 的已有 Codex Agent 有一次显式工程切片，Win saved-machine 有定向清单回读。业务派发与结果取证逐任务判断。[当前证据](status.md#当前结论) · [Cloud 路由与代建条件](coordination-policy.md#4-协调员指挥-cloud-路由)

**后台组件各管一段。** Task Mailbox 和 Mac 桥持久收发、离线补传，定时查信与协调员的等待检查按原任务推进状态。协调核心保存任职、代次和意图。受限组织 MCP 只给已绑定主体提供单件上下文、证据和获准的一次独立审查联系。Lead 显式执行适配器当前只调用已绑定的 Mac Agent。Codex App 聊天、台账、定时器及工具回执均不能单独证明某个 Herdr 岗位已启动或接手。项目进度和验收沿原 Issue／Project 维护，信箱沿原单查业务回报，日常交付无需等待已读 ACK。[协调员运行合同](project-coordinator.md#7-本机交付与运行) · [受限工具](../mcp/project-coordinator-tools/README.md) · [进度读取](coordination-policy.md#弱连接下的进度与迭代)
