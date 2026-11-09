# Super-Agent 分享图

本轮文档与图稿更新于 **2026-11-09（北京时间）**，复用 11 月 6 日分享材料的版式。全部为 **16:9**，提供可缩放 SVG 和 **3840 × 2160 PNG**；PPT 可直接插入 PNG，编辑排版使用生成源。图示表达当前职责与已确认接入路径，工程样本不代证生产迁移或自然业务效果，实时进展查[状态页](../status.md)。

| 顺序 | 讲什么 | SVG | 高清 PNG |
| --- | --- | --- | --- |
| 1 | 总架构：需求终端、Herdr 两层 Agent、独立 Decision Service 与共享记忆 | [总架构图](super-agent-architecture.svg) | [下载 PNG](super-agent-architecture.png) |
| 2 | 任务闭环：向下委派、负责人验收、异步回收，以及三类角色由谁创建 | [任务闭环](task-lifecycle.svg) | [下载 PNG](task-lifecycle.png) |
| 3 | 工作流：合法候选、有限判断、原端采用、结果反馈与数据归属 | [动态工作流](project-workflow.svg) | [下载 PNG](project-workflow.png) |
| 4 | 记忆拓扑：实际网关路径、Win 共享记忆与三类模型的边界 | [记忆拓扑](hindsight-topology.svg) | [下载 PNG](hindsight-topology.png) |

## 讲解要点

1. **需求、组织与执行各有责任。** 云端Dot调研拆解、委派和向公子汇报；项目负责人默认业务验收。组织岗位与本地 Worker 在 Herdr，获准的具体 Codex Cloud 作业是宿主例外。协调员依有效任命由独立受信宿主管理入口初始化或恢复；协调员安排负责人创建、绑定或交接；负责人或获准唯一派发者按任务选择、复用或创建 Worker。任命、技术创建和原生接手分别核验，见[宿主与生命周期](../agent-hosts.md)。
2. **判断建议沿原端采用。** 项目 workflow 或角色创建者提供合法候选。Mac Codex `new-unit` 已在源码接入本地策略快照，按宿主能力和允许组合筛选，启动前复核冻结策略。Decision Service 经 HTTP（程序）或 MCP（Agent）共用身份范围与判断核心，按需取记忆后使用业务 Luna 从合法候选中推荐。原执行器应用分支或模型配置。[白名单维护指南](../../config/allowlists/README.md) · [策略与接线范围](../agent-hosts.md#decision-model-selection)
3. **结果有两条回收路径。** Task Mailbox 查原单，Project／Issue 查项目，进度和阻塞随时回填，不等待已读 ACK。原执行器另向 Decision Service 直传 application／result，按原事件与修订关联。服务 SQLite 保存已收决策和反馈，原端保有检查点、待传事件与恢复责任。Hindsight webhook 已纳入执行计划，事件来源与生产启用待取证。
4. **三类模型分别看。** Decision Service 的业务 Luna、Win Hindsight recall／consolidation 的内部 Luna、角色实际执行模型与强度是三类用途，配置与凭据不合并。DS 实际使用 VPS gateway；native 是显式适配选项，无自动换路。Win 候选拒答补丁后继续 Luna，Q8 不是当前长期方案。

## 已验与待验

以下为本轮采用的既有回执，更新图稿没有重新调用模型、召回记忆或运行生产任务。

| 范围 | 图稿采用的结论与边界 |
| --- | --- |
| Decision Service 服务 | VPS 独立 HTTP／MCP 服务及持久 SQLite 已部署，共享记录与反馈语义；长期稳定性、准确率与效率收益未证明 |
| Herdr 消费者 | Mac 固定消费者 `8612a9a` 的真实合成工程闭环通过：原分支采用、原生模型／强度读回、联合结果修订和待传清零；生产角色与自然任务尚未迁移 |
| 插件与 Skill | Mac 本地插件 `0.1.1` 已安装，OAuth 与原生工具读回通过；Cloud `1.0.2` 已确认 OAuth、工具及 Skill 显式加载；不推定所有旧会话自动刷新 |
| Win Hindsight | `49e48b5c` 候选级拒答补丁于 11 月 9 日应用，继续 Luna；一次真实双 bank 判断与同记录读回通过。原始拒答原因仍 UNKNOWN，详细数字与范围沿[固定证据页](https://github.com/CF-FE/decision-service/blob/2e5ff94021d76b89c3bce30c8df38c8702ba0a9e/docs/evidence-and-open-questions.md#luna-局部拒答补丁与真实验证2026-11-09) |
| 原记忆接入与 Web | 11 月 5 日 Mac／VPS 文件对齐与只读调用、Skill 导入和来源读回已有独立回执。Web 仍按需前台启停；具体 Cloud 托管作业接入逐单核验 |
| 尚待自然使用证明 | 生产角色迁移、自然业务质量、整单效率与 token 收益、长期稳定性；Hindsight webhook／自动补偿未启用。生命周期图描述责任，不表示全部自动创建与恢复已实现 |

版本分别记录：本轮 Decision Service 资料固定于 [`2e5ff940`](https://github.com/CF-FE/decision-service/tree/2e5ff94021d76b89c3bce30c8df38c8702ba0a9e/docs)，VPS 服务发布 `2df05a48`、Mac 消费者 `8612a9a`、Win 补丁 [`49e48b5c`](https://github.com/CF-FE/herdr-4-codexcloud/commit/49e48b5c6a8a23d90d68a53dafbe9a37b680c6b3)属于不同版本轴，不把文档 HEAD 当作运行发布。

职责与权威说明：[总架构](../architecture.md)、[角色宿主](../agent-hosts.md)、[任务协作](../coordination-policy.md)、[工作流消费者](../project-workflow-development.md)、[Hindsight 合同](../hindsight-memory-integration.md)、[Web 使用](../../mcp/hindsight-memory/control-plane/README.md)。服务和插件主源归 [CF-FE/decision-service](https://github.com/CF-FE/decision-service)；Win 接入与补丁归本仓；VPS 记忆薄网关仍归 [ghyghoo8/create-network-sh](https://github.com/ghyghoo8/create-network-sh/tree/main/fastmcp/hindsight-memory)，上游引擎为 [vectorize-io/hindsight](https://github.com/vectorize-io/hindsight)。图中不含凭据、私有网络地址、bank 清单或记忆原文。

动态环境能力在 CF-FE Project 维护。Project 对白名单工作记录任务与当前版本链接，策略条目按场景保存在仓库版本化 JSON 中。[CF-FE 环境能力看板](https://github.com/orgs/CF-FE/projects/6/views/3)

## 更新与导出

可编辑排版源为 [build_diagrams.py](build_diagrams.py)，SVG／PNG 是同一次渲染的静态分享文件。使用已有 Matplotlib 与中文字体，在仓库根运行：

```bash
python3 docs/share-doc/build_diagrams.py
```

默认读取 Mac 的黑体字体；其他系统用 `--font /absolute/path/to/cjk-font.ttf` 指定已安装中文字体。SVG 保留文字，目标电脑字体不同可能改变显示，PNG 排版固定。生成脚本仅输出本目录图片，不访问服务或调用模型。

更改内容时先核对应文档、固定提交与原任务证据，同时导出两种格式，并实际查看中文、箭头、边界和遮挡。历史验收原件保持原版本。
