# 插件分发

本目录是本地 marketplace 根，清单为 [marketplace.json](.agents/plugins/marketplace.json)，名称 `cf-fe-herdr-local`，在宿主中显示为「CF-FE 本地协作」。

| 插件 | 作用与前提 |
| --- | --- |
| [Hindsight 项目记忆](hindsight-memory/README.md) | 为 Mac Codex App 与 CLI 提供同一用户级 MCP／Skill 插件；复用本机已部署的 Hindsight LAN 客户端，不附带凭据、模型或数据库 |

按已安装 CLI 的支持入口注册本目录，再安装 `hindsight-memory@cf-fe-herdr-local`。安装结果和已打开会话的工具加载分别核验；源码、清单或插件列表中的启用状态不能代替实际调用。

新分发目标沿 `plugin/<id>/` 维护清单、资源闭包和加载方式；源码归属见[架构](../docs/architecture.md)。修改来源包后使用宿主支持的更新／重装流程，不手改安装缓存。
