# Win 上游合同 · 1.0.1

拥有者：本仓 Hindsight 实施者；VPS 接入由指定部署仓独立维护。版本基线为 Hindsight API Slim 1.2.0、FastMCP 4.1.6、MCP SDK／types 2.3.0。运行参数读取忽略的 `.env` 及适用的 Win 私有部署副本，不在本合同写实际凭据。

## SSH 与 MCP

VPS 使用 `${HINDSIGHT_WIN_EASYTIER_HOST}`，Mac 使用 `${HINDSIGHT_WIN_LAN_HOST}`；用户及端口来自相应 SSH 字段。沿已有、已核 host key 的 SSH 通道，不分配 TTY。远端 PowerShell 设置进程 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`，调用：

```powershell
& '<HINDSIGHT_WIN_PYTHON>' '<HINDSIGHT_WIN_MCP_BRIDGE>'
```

stdin/stdout 是换行分隔的 UTF-8 JSON-RPC；日志只走 stderr，不能包装为交互 shell 输出。已验证 MCP 协议 `2025-03-26`；标准 `initialize`、`notifications/initialized`、`tools/list`。上游 Hindsight URL `/mcp/` 仅为 Win loopback 内部接口。

当前 39 工具：38 个在 schema 中要求非空显式 `bank_id`，唯一不带 bank 的工具是 `list_banks`。桥拒绝空值、前后空白及 `default`；未知的无 bank 工具也拒绝加载，升级时重新核合同。bank 组织记忆范围，不代替项目授权。

代理保留原始 MCP 工具字段。`retain` 额外接受可选 UUID `operation_id`：

- 不带 UUID：沿原生 MCP 行为，服务端生成操作 ID；丢失回执后不能凭 document 缺失自动重放。
- 带 UUID：必须提供稳定 `document_id`，桥调用同实例原生 REST async retain；响应仍是 MCP `accepted` 与原 operation ID。首次即传 UUID，后续 API 兜底用相同 ID，不改变正文、document 或 metadata。
- 返回 UNKNOWN／错误时保留原 ID 和原生诊断，不伪造 accepted。

## API 兜底 relay

同一 SSH 身份调用 `${HINDSIGHT_WIN_API_RELAY}`，不是新增 Win 公网端口：

```powershell
& '<HINDSIGHT_WIN_PYTHON>' '<HINDSIGHT_WIN_API_RELAY>'
```

stdin 一行 JSON，stdout 一个 JSON 对象。只允许下列 action，不接受任意 URL、HTTP method 或配置 root：

| action | 输入 | 原生内部请求 |
| --- | --- | --- |
| `recall` | `bank_id, query`；其余 body 放 `payload` | `POST /v1/default/banks/{bank}/memories/recall` |
| `reflect` | `bank_id, query`；其余 body 放 `payload` | `POST /v1/default/banks/{bank}/reflect` |
| `retain` | `bank_id, operation_id, document_id, payload` | `POST /v1/default/banks/{bank}/memories`，body 为 `items:[payload+document_id], async:true, operation_id` |
| `get_operation` | `bank_id, operation_id` | `GET .../operations/{operation_id}` |
| `get_document` | `bank_id, document_id` | `GET .../documents/{document_id}` |
| `list_operations` | `bank_id`；白名单筛选放 `payload` | `GET .../operations` |
| `list_banks` | 可选 `query/payload` | `GET /v1/default/banks` |
| `health` | 无项目参数 | `GET /health` |

`retain.payload` 是单条原生 item，包含 `content`，以及原生支持的 context／timestamp／tags／metadata／strategy／update_mode；来源和原文 SHA-256 放 metadata，不作为路由字段。metadata 的键值均为字符串。保持同一来源事件的 document 与内容摘要；新事件／不同写者使用不同来源 ID，修订明确替代范围。

回执含 `action/bank_id/operation_id/document_id`（适用时）、`state/http_status/body/attempts/retries/request_started`。`body` 保留原生 HTTP JSON；API relay 与 MCP 工具调用统一处理服务 token 和 API key 脱敏，保留状态、结构化内容、错误标志及元数据。

| state | 含义 |
| --- | --- |
| `NOT_SUBMITTED` | 本地校验、配置或连接前失败，尚未开始请求 |
| `UNKNOWN` | 发出后断线／超时、无效回执或写入 5xx 等；原 ID 保留待核 |
| `SUBMITTED` | 收到匹配的原生 async ACK；不等于提取或索引完成 |
| `RESPONSE` | 原生 HTTP/JSON 已读回；业务状态看 body，错误 HTTP 也保留 |

relay 不做重试。连接最多 15 秒，单次总预算最多 90 秒；`timeout_seconds` 可按调用者剩余预算缩短。VPS 独占上游重试，默认总计两次尝试，总预算最多 180 秒，协议切换不重置预算；平台更短时限优先。`Retry-After` 仅透传。

## 去重与对账边界

现场 1.2.0 `_resolve_retain_replay` 按 UUID 查 `async_operations`，同 bank 且 `batch_retain` 直接返回原操作；并发主键冲突也沿原记录 resolve。它不比较正文／document／hash：同 UUID 不得换内容；失败操作重放仍返回旧操作，不启动新提取。去重依赖操作记录仍存在，不作通用 exactly-once 承诺。

原生 MCP `get_operation` 返回 pending／processing／completed／failed／cancelled／not_found；`not_found` 不能转成 completed。`get_document` 可查 `original_text/content_hash/memory_unit_count`；服务端 hash 基于清洗后的文本，不能直接当原文 hash。仅文档缺失不证明未提交。`list_operations` 可按 bank、status、type、limit、offset 查询，父类型是 `batch_retain`；它没有 hash 身份过滤，不能据不唯一的条目擅自认领请求。

只有获得与原 CID／来源对应的完成回执，才能称该次写入完成。不能确认时把原 ID、来源、hash、最后状态和待补摘要留在原 Issue／任务；不新建后台队列。

## 认证与受控位置

VPS 只需已获准的 SSH 客户端身份和已核的 Win host key。Win 代理从私有 `config/shared.env` 读取 `HINDSIGHT_API_TENANT_API_KEY`，Token 不放入 SSH argv，也无需复制 OpenAI key／PG 密码。VPS 自己的 HTTPS MCP／API 客户端认证由其服务所有者维护，不复用信箱凭据。

SSH 凭据未备齐时须交出具体受控路径／公钥指纹缺口，不通过聊天传私钥。不通过更换数据库、bank 或服务身份解决认证错误。

## 固定来源

- [原生 MCP 工具](https://github.com/vectorize-io/hindsight/blob/34a138684a899b6ec34175f7a2b633860466d289/hindsight-api-slim/hindsight_api/mcp_tools.py)：retain 不支持预置 operation ID，故由本桥做兼容扩展。
- [原生 REST](https://github.com/vectorize-io/hindsight/blob/34a138684a899b6ec34175f7a2b633860466d289/hindsight-api-slim/hindsight_api/api/http.py)：async batch retain、operation 与 document 路由。
- 现场 memory_engine.py 的 SHA-256 为 `9e5494e0569aa9dbcd573282aa5992a150df44001998355abd509730defbd45c`；`_resolve_retain_replay`、事务内父子创建及并发回读已只读核验，未写业务测试材料。
