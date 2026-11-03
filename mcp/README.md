# MCP 服务

当前没有 MCP 服务条目。已有技能使用目标宿主实际提供的工具，不在本仓隐式安装或共享其他仓库的服务。

Task Mailbox 与 Mac 协调桥唯一维护于 [CF-FE/task-mailbox-mcp](https://github.com/CF-FE/task-mailbox-mcp)，个人 `create-network-sh` 只保留历史整合来源。固定来源与接回记录见[逐文件清单](https://github.com/CF-FE/task-mailbox-mcp/blob/main/docs/source-updates.json)，接入与迁移证据见[服务验证记录](https://github.com/CF-FE/task-mailbox-mcp/blob/main/docs/verification.md)。本仓不保留实现副本或跨仓运行依赖；桥已常驻收件／补传，Herdr 自动执行 handler 未实现，按授权手工承接未验；Dot 自动唤醒未实现且无保证。

Dot、Mac 协调桥、分别配置的 Codex／Kimi，以及具体云任务实际具有获准 MCP 工具与认证的 Codex Cloud，可作为同一信箱的客户端。原任务结果交付与指定 Dot 读取的证据归属见[本仓采用边界](../docs/status.md#vps-任务信箱与客户端接入进行中)，服务逐项状态沿上述权威记录；不从桥成功推定所有 CLI 或 Cloud 已接通。

有明确接入需求时再新增 `mcp/<id>/`，写清唯一源码、依赖、配置、工作目录及验证方法。整体边界见[架构](../docs/architecture.md)。
