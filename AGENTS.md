# 仓库开发约定

- 先读 [README](README.md) 和 [本仓状态](docs/status.md)；涉及范围或执行绑定时读 [架构](docs/architecture.md) 与 [试点指引](docs/pilot.md)。
- 本仓独立维护。需要复用时复制明确版本并记录来源；不建立软链接、子模块、跨仓库导入、共享运行目录或自动同步。
- 能力源码按 `skill/<id>/`、`mcp/<id>/` 归属；`plugin/<id>/` 仅在存在明确分发目标时新增。当前 `mcp/`、`plugin/` 只有索引，不虚构服务、安装入口或插件清单。
- 组织协作任务按需读取 [组织技能](skill/organization-collaboration/SKILL.md)，复用公子及当前任务已明确的职责与授权；本仓文件不任命参与者。Projects 操作按 [对应技能](skill/github-projects-operations/SKILL.md) 和当前工具 schema 执行。
- Herdr 试点与运行状态以 [docs/status.md](docs/status.md) 为准。安装、入网、机器注册、启动或替换 server、真实任务派发及正式记录切换，须落在对应任务的有效授权内；仓库初始化不自动执行这些动作。
- 同一执行和待写资产只有一个控制入口。未知结果沿原任务、机器、session、pane 与产物引用对账，不自动重派或切换到 Lody；旧 Lody 任务沿原入口收尾。
- 不把 `--kind codex` 当作供应商、模型、认证和权限配置等价的证据；不以 `done/idle`、退出 0 或结构检查代替业务验收。
- 具体任务沿已确认的 Issue／Project 维护，切换正式记录时保留原引用；本仓采用摘要、接口规范与历史各按 [文档归属](docs/status.md#文档与来源归属)维护，避免重复展开。复制技能的语义修改按其版本规则记录，来源清单保留导入时的历史基线。
- Worker 模板与专业角色在 [双库入口](docs/worker-library/README.md)独立维护版本；按任务组合，组织任命、实时可用性和实际派发另核。`scripts/worker_library.py` 仅离线查询／生成准备稿，不执行或授权任务；新增定义后运行其 `check`。
- 不提交凭证、私密机器/会话配置、原始终端输出、依赖缓存和分发产物。例子使用占位值，检查与报告区分本地证据、目标宿主证据和负责人验收。
- 在仓库根运行 `python3 scripts/check_repository.py` 和 `python3 -m unittest discover -s tests -v`；仅需已有 Python 3.10+ 标准库。修改链接或复制内容时另核相关引用、来源 hash 和资源闭包。
