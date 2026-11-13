# 技能源码

| 技能 | 当前源码版本 | 用途 |
| --- | --- | --- |
| [organization-collaboration](organization-collaboration/SKILL.md) | 2.3.3 | 组织岗位与原生宿主、项目主仓、任务关联、Cloud 路由与有界直派、可达汇报、人工介入清单、验收及工作流改进提案入口 |
| [github-projects-operations](github-projects-operations/SKILL.md) | 1.1.0 | Projects 查询、限定写入、回读与 UNKNOWN 对账 |
| [hindsight-memory](hindsight-memory/SKILL.md) | 1.0.0 | 同项目跨端记忆、显式 bank、带来源经验、异步确认与非阻塞弱连接 |
| [allowlist-management](allowlist-management/SKILL.md) | 1.1.0 | 场景白名单维护、离线校验与受控创建导航 |

组织协议与 Projects 技能及其随附资源已完整复制，各自在本仓维护；Hindsight 技能按官方固定来源独立适配，来源和许可见其 `SOURCE.json` 与 `LICENSE`。[初始复制清单](../docs/copy-manifest.json)保留组织协议 1.5.0、Projects 1.1.0 及最初 19 文件的历史摘要；[增量记录](../docs/copy-updates.json)单独保存后续固定来源和变更文件，不覆盖历史或自动同步。

1.5.2 导入来源为已合并的源 PR #11 固定提交 `a9d287c`；先前 PR #10 候选的 Draft／未合并状态仍按当时快照保留。导入文件变化查[增量记录](../docs/copy-updates.json)，采用过程查[状态沿革](../docs/history/status-through-2026-11-03.md#2026-10-31-可达汇报规则有限同步152)。1.6.0 起由本仓独立修订，岗位版本与迁移见[维护说明](organization-collaboration/references/maintenance.md#220直接联系与独立回填)，当前字节由本仓固定提交追溯，不改写历史导入摘要，也不宣称源仓已同步。

来源合并、文件存在与显式读取不等于端侧加载、原生发现、通道可达或业务验收。Projects 需目标宿主实际具备工具和对象权限；旧安装及在途任务保留原版本绑定。Herdr 的运行绑定见[试点指引](../docs/pilot.md#启动前绑定)，不由复制技能自动启用。
