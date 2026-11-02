# Agent 与能力盘点：Mac 本机及 Win 执行侧

观察日期：2026-11-02（Asia/Shanghai）。第 1—6 节记录信箱接入前的 Mac 本机快照；第 7 节单列 Win 执行侧交接，不混用两端版本和验证结果。后续新增 Task Mailbox 的配置、OAuth 与工具发现状态见[本次接入快照](status.md#vps-任务信箱与客户端接入进行中)，不据旧计数认定新服务缺失或已生效。后续链路评估见[给 Dot 的需求](dot-mac-herdr-evaluation.md)。配置存在、运行时发现及业务调用分别报告。

## 1. Herdr 与两个本地 Agent

| 对象 | 当前证据 | 未验范围 |
| --- | --- | --- |
| Herdr TUI | 0.9.5；独立 session 的两个 pane 同时识别 Codex／Kimi，均为 idle、interactive_ready=true，启动画面已回读 | 项目任务、真实模型回合、Dot 定向委派及长期稳定性 |
| Codex CLI | 0.166.0-alpha.5.1；已补 `~/.local/bin/codex` 入口，复用 ChatGPT.app 随附程序；全新交互 shell 与实际 Herdr pane 均解析成功 | 这是应用随附版本，应用更新后需重新核路径／版本，不是独立固定安装 |
| Kimi CLI | 当前 2.4.1，复用 `~/.kimi-code/bin/kimi`；新进程仍被识别并就绪 | 初次 2.4.0 曾有托管 provider 刷新凭证警告；2.4.1 复核未再出现，不把旧提示推广为当前失败，也不据此宣布模型回合通过 |

本轮没有提交模型任务，没有登录、切换 provider、修改模型或安装新插件。能力盘点使用官方只读 `/mcp`、`/plugins`／`/plugins list` 和技能补全；未调用业务 MCP。所有检测 workspace／session 已按原 ID 清理，用户原默认 session 保留运行。

Mac 项目协调层采用 Herdr 托管的 Codex 或 Kimi CLI；每个项目的当前 Lead、目录、写权与实际派发入口仍按任务绑定，不因识别到两个 Agent 就自动产生双 Lead。

## 2. MCP：配置与运行时分列

Codex 原生 `mcp list --json` 列出 12 项用户配置，其中 11 enabled、1 disabled；实际 Herdr pane 内 `/mcp` 显示 15 个条目，含运行时／插件注入，**12 connected、2 failed、1 disabled，共发现 370 个工具**。两种统计范围不同，不能相减推导丢失服务。

| Codex 原生运行时名称 | 状态 | 发现工具数 |
| --- | --- | ---: |
| blender | connected | 9 |
| chrome-devtools | connected | 32 |
| codegraph | connected | 1 |
| codex_apps | connected | 251 |
| codex_worktrees | connected | 3 |
| cua_repl | connected | 3 |
| gitee-mcp | connected | 25 |
| github-projects | connected | 3 |
| mcp-server-chart | connected | 27 |
| node_repl | connected | 4 |
| openaiDeveloperDocs | connected | 5 |
| shadcn | connected | 7 |
| koboyo | failed | 0 |
| xcodebuildmcp | failed | 0 |
| codex_app | disabled | 0 |

failed 的根因尚未核实，没有自动换端点、重登或修配置；disabled 项保持原设置。connected／工具发现证明初始化与发现阶段，不等于任意工具动作、账号或业务验收通过。

公子确认 VPS 已部署 Gitee MCP 并登记 ChatGPT 云端；VPS 以 FastMCP 自建工具服务、按需 ngrok HTTPS 为主要定位。上述本机 `gitee-mcp` connected 的实例映射与云端登记／真实调用仍单独核验，不因名字相似或工具目录可见就认定同一服务已由 Dot 调用。本轮没有更改该云端登记、隧道或服务。

Kimi 2.4.1 的原生 `/mcp` 显示 **`plugin-kimi-datasource:data` connected，stdio，2 个工具**。它由启用的 `kimi-datasource 3.4.0` 提供；本次未调用这些数据工具。内部 `moonshot_search`／`moonshot_fetch` 配置另列，不冒充两个 MCP。

## 3. Plugin：原生清单优先于缓存

| 证据范围 | 结果 | 解读 |
| --- | --- | --- |
| Codex TUI `/plugins` Installed | 原生总数 22；逐项捕获 20 项显示名称与启用状态，另 2 项名称未完整采集 | 20 项内为 15 enabled、5 disabled；不能将其当作全部 22 项的启用总数 |
| Codex 用户配置 `[plugins]` | 15 个显式条目，14 enabled、`computer-use` disabled | 只是该配置层，不等于原生 Installed 清单；盘点前后这些 enable 字段相同 |
| Codex marketplace 原生列表 | 8 个市场 | 可列举市场不代表其中全部插件已安装；全量 `plugin list --json` 曾 30 秒超时，未把超时记为空列表 |
| Codex 磁盘缓存 | 31 个包来源目录、30 个不同插件名称 | 缓存不代表启用；`my-agent-skills 6.3.0` 有两份来源，未自动去重或删除 |
| Kimi 原生 `/plugins list` | `kimi-datasource 3.4.0` enabled，skills: 1，MCP: 1/1 | 与当前 managed 安装清单一致；旧 `~/.kimi` 迁移目录不重复计数 |

Codex 已逐项回读的 enabled 项：Build iOS Apps、Build macOS Apps、Build MCP Apps、Build Web Apps、Build Web Data Visualization、Cloud Environment、Documents、GitHub、gopls-lsp、Memory Curator、My Agent Skills、PDF、Plugin Creator、Presentations、Spreadsheets。

已逐项回读的 disabled 项：Default templates、Pages、Pets、Plugin Management、Sites。它们是本次 CLI 插件页的状态，不推出桌面会话中同名服务的连接或权限已取消。

## 4. Skill：文件、发现和调用分别取证

| 范围 | 本次数量与名称 | 边界 |
| --- | --- | --- |
| Codex 直接用户技能 | 5：herdr、interface-design、organization-collaboration、vercel-react-best-practices、ui-ux-pro-max | react-best-practices 目录的 frontmatter 名为 vercel-react-best-practices |
| Codex 系统技能文件 | 5：imagegen、openai-docs、review-agent、skill-creator、skill-installer | 文件存在不代表每个 CLI／当前 host 都暴露全部条目 |
| 用户共享 `.agents/skills` | 1：design-taste-frontend | 两个 CLI 的实际发现按各自规则核对 |
| Kimi 原生技能补全 | 2 个外部候选：design-taste-frontend、kimi-datasource:kimi-datasource | 仅打开补全，未按 Enter 调用技能 |
| 本仓 `skill/` 源码 | github-projects-operations、organization-collaboration | 按路径可读，不是 CLI 原生自动发现或安装证明 |
| 插件缓存内技能 | 143 个来源条目；各范围按 frontmatter 去重共 128 名 | 含重复来源和未启用包，不报告成 128 个当前可调用技能 |

Codex 全局 `herdr` 技能位于其用户技能目录，当前桌面会话已列出并读取；Kimi 默认扫描路径不包含该目录，不能宣称 Kimi 已自动加载。Kimi 如需操作指南，可在有效范围内读取 `herdr --skill`；新增技能安装／同步另按任务执行。官方操作技能要求真实 Herdr 窗格环境，不伪造 `HERDR_ENV`。

## 5. 桌面工具与 CLI 不是同一清单

当前 Codex 桌面会话的工具目录另有 380 个 MCP 方法，覆盖浏览器、GitHub／Projects、Gitee、Space、文档、图表、CodeGraph、Blender 等。这里的“目录可见”不等于每项业务调用已通过，也不能直接当成 Codex CLI 的 370 工具或 Kimi 的 2 工具。

第 1—6 节的原生 Herdr／CLI 结果由主会话直接回读；静态技能／插件缓存和配置枚举另经三个受委派只读盘点归并。完整脱敏 JSON、名称／版本／来源映射与状态保存在本机私密盘点成果；原始终端画面、实际会话 ID、认证和端点配置不进入仓库。第 7 节的旁支回报另标证据归属，不冒称本会话直接复核。

## 6. 后续范围

上述运行快照已作为[Worker 与角色库](worker-library/README.md)的证据引用：模板、明确后端和专业职责分别维护，按需组合版本与摘要，不将这里的安装／发现升级成实时可调度。库本身与具体实例、组织任职及业务结果分别验收。

信箱接入由原实施侧按[限定需求的分层验收](dot-mac-herdr-evaluation.md#8-新增信箱链路分开验证不自动执行)分别取得云端／Mac OAuth、工具与双向数据回执，再接已获准的 Herdr 执行；Cloud 作为条件客户端另验。原生 Mac 任务 → Herdr → 两个协调 Agent 的原有评估分支保留，不将本盘点当成任一完整链路通过。两个 failed MCP 按项目需要定向诊断；不默认启用 disabled 插件、重复安装技能或清理缓存。当前本批文档尚未提交推送。

## 7. win-work harness：执行侧交接

2026-11-02，win-work 配置旁支交接以下结果：通过获准 SSH 在 Windows 上运行各原生 CLI，完成限定短对话。来源会话读取接口不支持临时会话，本会话仅收到执行侧摘要，未取得原始输出／JSON 文件，也未重复模型测试；表中“通过”均指该旁支报告的原生 CLI 文本回合，不是本会话直接验收。

| Harness | Win 版本与配置 | 执行侧报告的限定结果 |
| --- | --- | --- |
| OMP | 18.15.0；`cli-proxy-chat` 使用 `openai-completions`，`cli-proxy-responses` 使用 `openai-responses`；本次模型均为 `gpt-6-luna`，未改默认模型选择 | 两协议均返回本次随机校验标记、退出 0；原生 JSON 的 assistant `message_end` 中 provider／api／model 匹配。最近两次耗时分别 7.92／7.08 秒 |
| Hermes | 0.19.0（版本输出另带 2026.7.20）；复用既有 Git 安装。按公子明确要求将默认替换为 `cli-proxy / gpt-6-luna`，`chat_completions`，不保留原默认或新增回退；使用 Hermes 专用凭据 | 新凭据模型列表请求 200，另有按新默认完成的真实短回复、退出 0，耗时 15.47 秒；前一项不代替后一项 |
| Kimi Code | 2.4.1；默认仍为官方 `kimi-code / kimi-for-coding`、已有 OAuth；另有既有 `local-gpt / gpt-6.1-sol`，协议 `openai_responses`，沿原凭据 | 官方默认与代理入口均返回本次随机校验标记、退出 0，分别 10.97／13.62 秒；`kimi doctor config` 通过，两个回合前后配置文件字节一致 |

这些耗时是单次调用样本，不是延迟排名、吞吐或稳定性结论。OMP 的代理列表可发现 26 个模型，只实测上述一个模型的两种协议。

后续仅在任务已明确获准时复用以下启动选择；命令表示入口选择，不授予模型回合或生产派发权限：

```powershell
omp --model cli-proxy-chat/gpt-6-luna
omp --model cli-proxy-responses/gpt-6-luna
hermes
```

OMP 配置在当前用户的 `.omp/agent/models.yml`；Hermes 应从现有 `HERMES_HOME` 定位 `config.yaml`，不能套用默认 `~/.hermes`；Kimi 配置在当前用户的 `.kimi-code/config.toml`。实际账号路径、代理地址、凭据及运行引用保留在宿主私密配置与执行侧原件，不写入本仓。三者的密钥归属分别保留，不为复用入口而统一或复制凭据。

代理入口指向 Mac 上的 CLI Proxy，因此 OMP 的两个测试入口、Hermes 当前默认和 Kimi 的 `local-gpt` 依赖该代理及 LAN 路径。公子确认 Mac 持续在线，当前接受这项依赖，不建设脱离 Mac 的备用推理或离线接管，也不要求相关离线测试。Kimi 官方默认入口另列，不把代理依赖推广到所有 Win 工作。

Hermes 测试参数 `-t none` 被该版提示 `Unknown toolsets`，不写入日常模板，也不能据此宣布工具已禁用或工具效果为零。Kimi 展示输出带 `•` 前缀，文本匹配应区分 UI 前缀与实际回复。工具执行、MCP／skill／plugin、真实业务和长期运行均未由这次文本测试覆盖。

Mac Herdr 的 `win-work / collab` 原生连接与只读查询已有准备侧回执；当时 `agents=[]` 只表示该 session 的识别快照。旁支回报 Win Herdr 0.9.5 帮助存在 `omp`／`kimi`／`hermes` kind，不能据此认定三个 Agent 当前已在 Herdr 内启动、识别或接受任务。**本次模型回合未通过 Herdr `agent start/prompt`，Mac Herdr → Win Herdr → Agent → 结果回收仍未验收。**

公子最新要求先忽略 Windows Codex，因此本轮不将其列为采用、待验或调度候选；Mac 协调层 Codex 的既有记录保持独立。ZCode／DeepSeek Harness 仅有历史安装记录，公子已要求暂不继续配置或测试。后续试点如获准，范围先绑定 OMP、Hermes 或 Kimi 中一个明确入口，再取得 Herdr 识别、目标引用、输入输出、退出码和原任务回收证据；本次交接不触发测试、迁移在途任务或改变写权。
