# Hindsight 项目记忆接入 · 1.0.0

本目录拥有 Win stdio MCP 代理、Mac LAN SSH 客户端和受控的 API 兜底 relay。运行的是原有 Hindsight 实例，不启动模型、数据库或第二套记忆服务。[本轮 Issue #13](https://github.com/CF-FE/herdr-4-codexcloud/issues/13) · [实施合同](../../docs/hindsight-memory-integration.md) · [上游接口](CONTRACT.md)

## 文件与依赖

- `mcp_bridge.py`：代理 Hindsight 1.2.0 多 bank MCP；要求显式项目 bank，拒绝 `default`；为 `retain` 增加可选的预分配 `operation_id`。
- `api_relay.py`：单次 JSON 请求／回执，通过 SSH 调用 Win loopback API。本层不重试、不创建持久队列。
- `mac_client.py`：透明转发 stdio MCP 到局域网 Win；不处理业务结果，不增加重试。需要 Python 3.9+ 和现有 SSH 认证。
- [requirements.txt](requirements.txt)：Win 代理运行依赖 FastMCP 4.1.6、Python 3.11+。已有部署复用已安装的兼容环境；仓库结构检查与单元测试仅需标准库。
- [.env.example](.env.example)：复制适用字段到公子指定、已忽略的仓库 `.env`。不提交实际地址、用户名、凭据或原始运行回执。

Hindsight API、PG、BGE 和开机任务继续由原部署维护；本目录不替换或重启这些服务。VPS 服务与重试策略唯一维护于公子指定的 [create-network-sh/fastmcp](https://github.com/ghyghoo8/create-network-sh/tree/main/fastmcp)，跨仓只复制固定版本，不运行跨仓导入或共享目录。

## Win 部署与本地客户端

把 `mcp_bridge.py`、`api_relay.py` 复制到私有运行根目录的 `scripts/`，部署时分别可命名为 `mcp-bridge.py`、`api_relay.py`。该根目录的 `config/mcp-client.env` 只需适用的 `HINDSIGHT_WIN_LOCAL_MCP_URL` 字段，来源为本仓 `.env`；`config/shared.env` 保留原 `HINDSIGHT_API_TENANT_API_KEY`。代理仅接受 HTTP loopback 上游，不新增监听端口。

Harness 用户级 stdio MCP 条目名为 `hindsight`，`command` 指向部署环境 Python，`args` 指向 `scripts/mcp-bridge.py`。不在各 Harness 内复制 Hindsight token、OpenAI key 或数据库密码。各客户端的 JSON／TOML／YAML 格式不同；只合并该条目，保留模型、其他 MCP 和会话覆盖。

已核用户级入口：

| 客户端 | MCP 位置 | Skill 位置 |
| --- | --- | --- |
| Codex（本轮仅 Mac） | `~/.codex/config.toml` 的 `mcp_servers.hindsight` | 用户 `.agents/skills` |
| Kimi 2.4.1 | `~/.kimi-code/mcp.json` 的 `mcpServers.hindsight` | 用户 `.agents/skills` 或 Kimi 专属 `.kimi-code/skills` |
| OMP 18.15.0 | `~/.omp/agent/mcp.json` 的 `mcpServers.hindsight` | `~/.omp/agent/skills` |
| Hermes 0.19.0 | 实际 `HERMES_HOME/config.yaml` 的 `mcp_servers.hindsight` | 实际 `HERMES_HOME/skills` |
| ZCode 3.16.3 | `~/.zcode/cli/config.json` 的 `mcp.servers.hindsight` | `~/.zcode/skills` |
| Zed | 用户设置的 `context_servers.hindsight` | 用户 `.agents/skills` |

Skill 源码位于 [skill/hindsight-memory](../../skill/hindsight-memory/SKILL.md)。Win Codex、Mac Claude 排除，DSH 暂缓；不为安装而打断已有生产会话。Kimi 新增 MCP 在之后创建的会话加入；其他 Harness 也须区分文件配置、会话发现和工具调用。

## Mac LAN 入口

将 `mac_client.py` 复制到私有用户目录，旁边的 `config-path` 文本文件写入本仓 `.env` 的绝对路径。用户级 MCP 的 `command` 为系统 Python，`args` 为这个客户端文件。`HINDSIGHT_NETWORK=lan` 选用 `HINDSIGHT_WIN_LAN_HOST`；EasyTier 地址独立保存在 `HINDSIGHT_WIN_EASYTIER_HOST`，不为 Mac 安装 VPN 或增加路由。

SSH 使用 BatchMode、已验证的主机 key 和现有用户身份。更换实际 IP 只修改 `.env`；当前连接在自然重连时读取新值。主机身份改变仍必须核验 SSH host key，不把新地址视为自动信任。

## VPS 超时和 API 兜底

VPS 入口首选 MCP；发现、协议或会话故障时，可在同一项目、同一授权、同一来源下调用 API relay。两条协议仍使用同一条 EasyTier／SSH 网络，不能绕过整段断网。

VPS 层拥有调用预算：连接 15 秒、单次最多 90 秒、总计最多 180 秒，默认总计两次尝试（首次加一次重试或 API 切换）；平台较短上限优先。Win relay 只执行一次 HTTP 请求，`timeout_seconds` 只能缩短其单次预算，不另开重试。Mac LAN 参数保持现状。

所有 VPS `retain` 首次即预分配 UUID 并传入 MCP 的可选 `operation_id`；同一尝试链保持 bank、document、正文和来源 metadata 完全相同。Win 代理用原生 REST async retain 保留该 UUID，后续 API 兜底复用它。不要原生 MCP 首发后另造新 UUID 重放。`accepted`／`SUBMITTED` 是提交回执，提取完成看原 operation。

## 验证、升级与撤回

先以 stdio `initialize`／`tools/list` 验证连通和 bank schema，不调用模型。协议扩展使用合成上游验证；已有部署、自启和 GPU 基线不重复验证。客户端矩阵记录配置、原生发现、调用及待验范围；云端采用须独立取得原生工具活动。

升级先核源提交和文件 hash，复制明确文件到私有运行目录；新 MCP 连接加载新代码，不重启整个 Hindsight 栈。更换依赖或上游版本前核工具 schema。撤回时移除本轮 `hindsight` MCP 条目与同名 Skill，不删除记忆 bank、业务检查点或其他客户端配置。API 兜底失败与未知写入沿原任务留待补信息，确定步骤照常继续。

本仓必需检查：`python3 scripts/check_repository.py` 和 `python3 -m unittest discover -s tests -v`。它们不构成目标宿主或云端采用证据。
