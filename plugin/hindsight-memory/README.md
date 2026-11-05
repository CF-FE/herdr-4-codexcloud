# Hindsight 项目记忆插件 · 1.0.0

供 Mac Codex App 与 Codex CLI 使用的本地插件，包含 Hindsight Skill 1.0.0 和 stdio 启动器。来源组件版本为 1.0.1；三个版本分别维护，固定复制来源及 SHA-256 见 [SOURCE.json](SOURCE.json)。

## 运行前提

本机已部署 `~/.config/hindsight/client.py`，它的私有配置、既有 LAN SSH 认证及 Hindsight 服务已经可用。启动器通过当前 Python 解释器直接执行该客户端；SSH、配置解析和传输行为继续由 MCP 组件维护。包内没有服务、模型、数据库、凭据、私有配置或采集 hooks。

根 [mcp.json](mcp.json) 使用 Agent Plugins 1.0 stdio 格式，以 `python3` 启动 `${PLUGIN_ROOT}/scripts/launch.py`。Mac 兼容清单使用 `/usr/bin/python3`，要求 Python 3.9+。安装后应以实际宿主解析到的解释器和 MCP 配置为准。

## 安装与核验

marketplace 根为本仓的 `plugin/` 目录，其中 `.agents/plugins/marketplace.json` 指向 `./hindsight-memory`，标识为 `hindsight-memory@cf-fe-herdr-local`。使用已安装 Codex CLI 支持的 `plugin marketplace add` 和 `plugin add` 流程注册、安装；安装操作由宿主侧执行。

优先使用同一用户级安装供 App 与 CLI 加载。切换时核对已有用户级 `hindsight` MCP 和同名 Skill 的来源，避免两套入口重复加载。源码修改应通过宿主支持的刷新或重装流程进入缓存，不能手改安装缓存。

注册与安装示例（在本仓根目录执行）：

```sh
codex plugin marketplace add ./plugin
codex plugin add hindsight-memory@cf-fe-herdr-local
codex plugin list --marketplace cf-fe-herdr-local --json
```

确认新入口可调用后再移除 Codex 的旧单独 `hindsight` MCP 条目，并保留其私有撤回副本；其他 Harness 的 MCP 和共享 Skill 保持各自使用。App 插件页的来源为「CF-FE 本地协作」。用户级安装由 App／CLI 共享，已打开的会话不保证热加载；在宿主刷新或自然恢复后核实际工具，不为此重启在途生产任务。

安装完成后分别核验 App 与 CLI 的插件发现、Skill 加载、stdio 初始化和工具发现。工具发现不等于记忆读写或业务验收；项目调用仍须显式使用已确认的 `bank_id`，并按 Skill 保留异步状态和来源。此包的离线检查不连接 Win、不调用模型。

清单按 [OpenAI 插件打包文档](https://developers.openai.com/plugins/build/plugins)维护。根 `plugin.json`／`mcp.json` 是便携入口；`.codex-plugin/plugin.json`／`.mcp.json` 保留本地 Codex 兼容入口，两种入口不假定合并。
