# 仓库开发约定

## GitHub Organization

本仓及本组织所有现有、后续新增角色共同遵循[GitHub 组织规范](skill/organization-collaboration/SKILL.md#github-organization)：默认组织为 `CF-FE`，各项目使用自己的仓库。完整规则由组织协作技能统一维护，岗位正文、专业模板与任务包继承，不另设相互竞争的组织路由。

## 委派与岗位边界

普通会话内的临时子代理协作不产生组织岗位或业务验收权。本组织岗位沿 Herdr 原生绑定履职，唯一执行例外是经授权沿 Codex 路由派发的具体 Codex Cloud 作业。云端Dot负责交办、收报及向公子汇报，可指派验收者，不担任业务验收者。完整规则、绑定核对、入口缺失处理和操作指引见[组织协作共同规则](skill/organization-collaboration/SKILL.md#委派与岗位边界)，所有现有及新增角色共同继承。

## 开发与协作

- 先读 [README](README.md) 和 [本仓状态](docs/status.md)；涉及范围或执行绑定时读 [架构](docs/architecture.md) 与 [试点指引](docs/pilot.md)。
- 本仓独立维护。需要复用时复制明确版本并记录来源；不建立软链接、子模块、跨仓库导入、共享运行目录或自动同步。
- 能力源码按 `skill/<id>/`、`mcp/<id>/` 归属；`plugin/<id>/` 仅在存在明确分发目标时新增。受限组织工具位于 `mcp/project-coordinator-tools/`，使用同一固定发布的协调模块；源码存在不代表宿主启用。`plugin/` 包含 Hindsight 本地分发及 marketplace 元数据，按其 README 核安装与实际工具加载，不手改宿主缓存。
- 组织协作任务按需读取 [组织技能](skill/organization-collaboration/SKILL.md)，复用公子及当前任务已明确的职责与授权；本仓文件不任命参与者。Projects 操作按 [对应技能](skill/github-projects-operations/SKILL.md) 和当前工具 schema 执行。
- 云端Dot、协调层与 Cloud 路由按[协作约定](docs/coordination-policy.md)执行：完整目标优先交协调层；独立小迭代可依用户授权直派，仍保留项目归属、唯一派发与证据验收。协调员的广泛组织调度权不等于系统权限；`QUERY_BLOCKER` 不能承接目标或触发 Cloud。新的受控目标合同见[完整目标交办](docs/goal-handoff.md)，不能据源码存在推定宿主准入或真实执行已验。
- 历史称呼沿用云端Dot的参与者关联，原生身份与权限仍按本次任务核验。见[全局角色名称映射](docs/coordination-policy.md#全局角色名称映射)。
- 项目协调员沿现任绑定直接转交与恢复。GitHub 评论先在本机保存，再交独立记录者写回；协调员接单窗格继续处理其他消息。见[转交、恢复与待回填](docs/coordination-policy.md#协调员转交与待回填)。
- 日常 Herdr 工作需要收回结果或请负责人决定时，协调员在派发前沿原任务登记下一动作和复查时间。已有回执仍有未决事项时立即补登。切换工作或恢复负责人后先查同项目未结项。办理原生登记、延后和凭决定结项见[原任务待办](docs/project-coordinator.md#native-followups)。
- Herdr 试点与运行状态以 [docs/status.md](docs/status.md) 为准。安装、入网、机器注册、启动或替换 server、真实任务派发及正式记录切换，须落在对应任务的有效授权内；仓库初始化不自动执行这些动作。
- 同一执行和待写资产只有一个控制入口。未知结果沿原任务、机器、session、pane 与产物引用对账，不自动重派或切换到 Lody；旧 Lody 任务沿原入口收尾。
- 不把 `--kind codex` 当作供应商、模型、认证和权限配置等价的证据；不以 `done/idle`、退出 0 或结构检查代替业务验收。
- 具体任务沿已确认的 Issue／Project 维护，切换正式记录时保留原引用；本仓采用摘要、接口规范与历史各按 [文档归属](docs/status.md#文档与来源归属)维护，避免重复展开。复制技能的语义修改按其版本规则记录，来源清单保留导入时的历史基线。
- Worker 模板与专业角色在 [双库入口](docs/worker-library/README.md)独立维护版本；按任务组合，组织任命、实时可用性和实际派发另核。`scripts/worker_library.py` 仅离线查询／生成准备稿，不执行或授权任务；新增定义后运行其 `check`。
- 不提交凭证、私密机器/会话配置、原始终端输出、依赖缓存和分发产物。例子使用占位值，检查与报告区分本地证据、目标宿主证据和负责人验收。
- 在仓库根运行 `python3 scripts/check_repository.py` 和 `python3 -m unittest discover -s tests -v`；仅需已有 Python 3.10+ 标准库。修改链接或复制内容时另核相关引用、来源 hash 和资源闭包。
