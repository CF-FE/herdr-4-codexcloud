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

## 仓库原生技能快照

`skill/organization-collaboration/` 是组织技能的唯一编辑源。提交到 [`.agents/skills/organization-collaboration/`](../.agents/skills/organization-collaboration/SKILL.md) 的完整快照用于 Codex 仓库级发现，包含 `SKILL.md`、全部 `references/` 及其他随附资源；不得在快照中独立修改。修改源后，在仓库根显式运行：

```bash
python3 scripts/sync_native_skill.py
python3 scripts/check_repository.py
```

同步脚本仅重建该固定快照，不触及其他技能。仓库检查只核文件集合及逐文件字节，缺件、多件、漂移均报错，不自动修复；源与快照不能用符号链接替代。相对目录布局保持一致，本次布局迁移不改组织协议 2.3.3。现有 Mac／Win 分发仍从唯一源取材，根 `skills -> skill` 别名保持原样，不新增跨仓同步或宿主服务。

官方支持 [`.agents/skills` 仓库扫描及技能目录软链](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills)；完整快照是本仓的实施选择。同名技能不会合并，全局安装可能与本仓快照同名同版本。验证新会话时须分别记录实际检出提交、原生技能清单的名称／描述／来源路径、正文及引用读取、场景采用；来源须指向该工作区的 `.agents/skills/organization-collaboration/SKILL.md`。磁盘存在、AGENTS 引导读取、全局同名技能或启动后另起进程扫描均不能代证原会话启动发现。宿主不提供可审清单时保留未验，也不推定旧会话自动热更新。
