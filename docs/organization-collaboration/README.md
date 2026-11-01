# 组织协作评测资料

2026-11-01 按源交接清单接收的离线资料。它们按需读取，不进入技能默认入口，不是运行器、插件或真实业务验收。当前进展仍见[本仓状态](../status.md)。

| 资料 | 采用方式与范围 |
| --- | --- |
| [55 题输入](eval-inputs.json) | 从固定源原样复制；匿名 E／P／A 场景，仅为模型决策走查输入 |
| [评阅判据](eval-rubric.md) | 原样复制；唯一相对依赖为同目录输入；含答案，不能提前交给前向评测者 |
| [可达汇报 R01–R05](reporting-evals.md) | 从源需求页改编；输入与判据同页，只做文档审查资料 |
| [组织技能](../../skill/organization-collaboration/SKILL.md) | 本仓 1.5.2 规范入口；上述资料不另外制定岗位、权限或运行规则 |

## 来源与评测边界

历史源固定为 `CF-FE/mymcp-4-codexcloud@8481136f3a24289978ba207317c8b9b8397318e7`。两份原样文件及派生页的源摘要、目标摘要、大小、权限和采用方式记录在[增量来源](../copy-updates.json)；[初始清单](../copy-manifest.json)和两轮技能增量保留历史。

核当前资料时顺序叠加增量：本轮新增路径的 `previous_sha256` 为 `null`，不是覆盖旧文件；派生页同时记录源文件与目标文件的摘要，不能要求两者逐字相同。

源交接说明及机器清单是接收时另写、未提交的审查材料，其摘要单独登记，不冒称包含在上述固定提交。资料包是供审阅的快照，未作为分发包落入本仓；本仓读取与离线检查不依赖原 checkout 或临时 ZIP。来源许可没有额外确认，不补造或更改许可结论。

源方随后以 `02e6ca4c1cb201904abd2ef2f107f6dce957e051` 发布了[交接说明](https://github.com/CF-FE/mymcp-4-codexcloud/blob/02e6ca4c1cb201904abd2ef2f107f6dce957e051/docs/lody4dot/herdr-handoff.md)及[机器清单](https://github.com/CF-FE/mymcp-4-codexcloud/blob/02e6ca4c1cb201904abd2ef2f107f6dce957e051/docs/lody4dot/herdr-handoff-manifest.json)，两份字节摘要与接收时一致；该发布定位单独追加，不改变历史评测资料的 `8481136` 来源或接收时状态。

**本轮没有执行 55 题、R 系列盲答或真实工具实验。** 后续按具体变更挑选场景，前向评测只给指定输入和完整技能入口；评测者不得先读 rubric、R 表判据或历史答案。先保存并冻结原回答和实际读取范围，再由合适评阅者按行动、依据和矛盾评分，不用关键词命中冒充行为验证。

P21–P23 中的 Lody Projects 故障保持来源情境，不改名伪装成 Herdr 原生行为。源评测回答、239 项测试通过／1 项跳过、Dot 读取或旧业务回执均未导入为本仓结果。资料结构有效、模型决策走查、真实平台行为、目标端加载和负责人验收分别取证。

## 可选资料：固定引用，未落盘或激活

下列 10 份候选仅核资料清单与固定字节完整性，未在本轮逐份采用正文；精确路径与摘要见增量记录。需要时再明确用途与最小依赖，不作为 VPS 接入前置。

| 候选 | 固定来源与采用前需核项 |
| --- | --- |
| 6 份专业参考 | [visual](https://github.com/CF-FE/mymcp-4-codexcloud/blob/8481136f3a24289978ba207317c8b9b8397318e7/skill/lody4dot/references/roles/workspace/visual.md)、[research](https://github.com/CF-FE/mymcp-4-codexcloud/blob/8481136f3a24289978ba207317c8b9b8397318e7/skill/lody4dot/references/roles/workspace/research.md)、[quality](https://github.com/CF-FE/mymcp-4-codexcloud/blob/8481136f3a24289978ba207317c8b9b8397318e7/skill/lody4dot/references/roles/workspace/quality.md)、[runtime](https://github.com/CF-FE/mymcp-4-codexcloud/blob/8481136f3a24289978ba207317c8b9b8397318e7/skill/lody4dot/references/roles/workspace/runtime.md)、[knowledge](https://github.com/CF-FE/mymcp-4-codexcloud/blob/8481136f3a24289978ba207317c8b9b8397318e7/skill/lody4dot/references/roles/workspace/knowledge.md)、[dialectic](https://github.com/CF-FE/mymcp-4-codexcloud/blob/8481136f3a24289978ba207317c8b9b8397318e7/skill/lody4dot/references/roles/workspace/dialectic.md)。不是完整角色库；旧模型建议、G8 中转和平台绑定须按当前任务改编，角色名不产生任命或权限。 |
| 组织追溯与审计图 | [Mermaid 图源](https://github.com/CF-FE/mymcp-4-codexcloud/blob/8481136f3a24289978ba207317c8b9b8397318e7/docs/organization-collaboration/diagrams/traceability-and-audit.mmd)／[SVG](https://github.com/CF-FE/mymcp-4-codexcloud/blob/8481136f3a24289978ba207317c8b9b8397318e7/docs/organization-collaboration/diagrams/traceability-and-audit.svg)；若采用需配本仓说明，不迁 Lody 渲染器。 |
| Projects 操作图 | [Mermaid 图源](https://github.com/CF-FE/mymcp-4-codexcloud/blob/8481136f3a24289978ba207317c8b9b8397318e7/docs/github-projects-operations/diagrams/operation-flow.mmd)／[SVG](https://github.com/CF-FE/mymcp-4-codexcloud/blob/8481136f3a24289978ba207317c8b9b8397318e7/docs/github-projects-operations/diagrams/operation-flow.svg)；图不证明权限、自动派发或运行通过。 |

不复制 Lody 安装／认证／诊断／打包器、命令索引、旧角色登记、项目适配、源队列或历史验证报告。Gitee MCP 是独立服务，本轮无新增需求，不随 Lody 退出搬迁。

## 退出与运行交接待办的责任

准备放弃 Lody 后续路线不等于旧入口已停稳，也不授权立即停服、撤权、删除、迁移看板或重派。原负责人逐任务决定：新任务采用哪条路线、在途沿旧入口收尾还是在明确节点交接；原项目、协议版本与执行引用保留。

下面是受控交接字段目录，不在公开仓库填写私密实际值，也不建立第二份活动队列。由相应责任方在原正式入口补事实，再交接准确引用：

| 未决事实 | 原责任方与下一动作 |
| --- | --- |
| 任务、正式入口、验收与切换点 | 原项目负责人确认项目／任务、范围、采用版本、验收者及新旧切换点；未确认继续原入口。 |
| 执行与 UNKNOWN | 原派发／执行责任人沿任务、session／回合／请求核最后状态、已发生效果、下一检查点；不由资料接收方补造已停或未执行。 |
| 写权、进程与资源 | 当前写者及运行保管方给资产范围、停止／解除证据、拟接收者和承接回执；失联不等于交还。 |
| 输入、产物与恢复 | 原制作／保管方交版本、适用大小与摘要、原件位置、质量／负责人决定和恢复引用；音频原件仍留获准制作端。 |
| 机器与启动配置 | win-work／VPS 保管方提供当前 OS、SSH shell、Git sh、Agent 可执行文件、参数及 provider／模型／权限的脱敏观测。源码只确认显式选择已核映射的 `codex over Claude` 政策，不能证明真实配置等价。 |
| 汇报与承接 | 原派发者落实获准通道、推送或拉取者、检查点和未读／未交付残留；接收者确认实际承接，ACK 不等于接手或释放写权。 |

CF-FE Projects 的组织选择原则不自动绑定本仓实际 Project、Issue 路由、任职或写者。没有项目仓库时，默认环境 Issue 仓库是否从源仓切到本仓须明确切换点；不自动搬迁、新建或公开私密项目资料。

运行清单、UNKNOWN、写权、产物与遗留已完成或有效承接，且另有具体停用授权后，才处理旧服务和资源。Lody 专属旧验收可由原负责人决定“不再计划／被替代”；保留原未验及决定来源，不强行为退场补验或改记通过。未决项的业务责任仍在原责任方，本轮只接收资料，不接管它们。
