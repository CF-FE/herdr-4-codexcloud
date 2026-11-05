# MCP 服务

[hindsight-memory](hindsight-memory/README.md) 提供项目共享记忆的 Win stdio 代理、Mac LAN 客户端和受控 API 兜底；公网入口由指定部署仓独立维护。[实施合同](../docs/hindsight-memory-integration.md)拥有范围与验收要求。

本仓提供 [project-coordinator-tools](project-coordinator-tools/README.md)：按单件冻结目标运行的 stdio 组织工具。它使用同一固定发布中的协调模块，不创建网络服务或默认启用任何宿主权限。已有技能使用目标宿主实际提供的工具，不隐式安装或共享其他仓库的服务。

Task Mailbox 与 Mac 协调桥唯一维护于 [CF-FE/task-mailbox-mcp](https://github.com/CF-FE/task-mailbox-mcp)，个人 `create-network-sh` 只保留历史整合来源。固定来源见[逐文件清单](https://github.com/CF-FE/task-mailbox-mcp/blob/main/docs/source-updates.json)，接入与迁移见[服务验证记录](https://github.com/CF-FE/task-mailbox-mcp/blob/main/docs/verification.md)。本仓不保留服务实现副本或跨仓运行依赖。

Mac 桥负责持久收件／补传，Herdr 的 [Lead 显式 Mac 执行适配器](../docs/herdr-executor.md#验收记录)已完成首版限定验收；自动消费派发与 Dot 自动唤醒仍未实现。各 CLI、Dot、Cloud 的获准工具、认证及实际读写分别核验，当前采用范围统一见[状态页](../docs/status.md#vps-任务信箱与客户端接入进行中)。

有明确接入需求时再新增 `mcp/<id>/`，写清唯一源码、依赖、配置、工作目录及验证方法。整体边界见[架构](../docs/architecture.md)。
