# 场景白名单

本目录保存创建、选型等场景使用的版本化策略。每个实际场景维护一个 JSON 文件；共同字段只描述身份、版本、状态和维护责任，条目结构由该场景定义。新增场景时再增加对应文件和校验，不预建空清单或通用策略语言。

公子已确认本次允许组合。Agent 策略在 Mac Codex `new-unit` 的候选筛选和原生启动前生效。图像模型清单已确认，调用接线与宿主验证待完成。

| 场景 | 唯一配置源 | 当前阶段 | 校验入口 |
| --- | --- | --- | --- |
| Agent 创建时的模型组合 | [agent-models.json](agent-models.json) | `active`，修订 4，Mac Codex `new-unit` 已接入 | [allowlists.py](../../scripts/allowlists.py) 的 `validate` / `preview` / `defaults` |
| 图像生成与编辑选型 | [image-models.json](image-models.json) | `active`，修订 2，清单已确认，调用待接 | 同一工具，显式指定图像策略文件 |

Git 中的 JSON 是唯一策略源。GitHub Project 记录维护任务和当前版本链接。创建端读取发布目录中的本地快照并记录修订号及摘要。环境能力事实仍由对应环境维护者提供。

## 谁维护

| 责任方 | 维护内容 |
| --- | --- |
| 项目协调员 | 组织级允许组合、修订版本及发布记录 |
| 环境维护者 | 实际可用的模型、强度、宿主能力及观察证据 |
| 项目负责人 | 提出任务需要，从有效范围选用组合 |
| 本仓维护者 | 文件格式、离线校验工具与后续创建入口接线 |

任职、创建授权和业务验收沿[原角色职责](../../docs/agent-hosts.md)办理。声明维护岗位不产生新的任命或主机权限；格式检查也不能证明授权真实有效。

## 共同字段

共同字段为 `schema_version`、`id`、`revision`、`status`、`owner_role` 和 `entries`。两个场景使用 `schema_version=1` 和 `owner_role=project_coordinator`。

| 字段 | 含义 |
| --- | --- |
| `schema_version` | 文件格式版本；字段合同变化时维护 |
| `id` | 场景标识，决定条目和预览能力文件的格式 |
| `revision` | 本场景策略修订号；默认值、条目或状态变化时递增 |
| `status` | `draft` 为待确认草案，`active` 表示已发布策略，`disabled` 表示停止用于新创建 |
| `entries` | 明确列出的完整组合；空数组不放行任何组合 |
| 文件 SHA-256 | 工具对读取的原文件计算，用于辨认具体字节，不替代修订号或发布状态 |

## Agent 模型组合

每条 `entries` 共同指定：

```text
environment_id + worker_id + backend_id + provider
              + model_type + model + reasoning_effort
```

另附 `evidence_ref` 指向能力或实际采用的依据。Worker、后端和环境须与[Worker 库](../../docs/worker-library/README.md)中的定义对应。一条记录就是一个组合，不把型号和强度拆成两个独立名单后自动交叉组合；不使用通配符。

`reasoning_effort: null` 只表达明确不适用。未知值、缺失字段或能力列表为空均不能代替不适用，也不表示任意强度。能力证据中只有显式列出 `null` 才能与该组合匹配。

### 环境默认值

修订 4 沿用 `default_environment_id=mac-work` 作为基准归属，统一默认值为 `default_model=gpt-6.1-sol`、`default_reasoning_effort=high`。这是版本化的策略值，读取它无需访问 Mac 主机配置。

- 其他环境可在 `environment_defaults` 中以环境 ID 指定 `model`、`reasoning_effort` 或两者。每个字段先取该环境的显式值，缺项继承 `mac-work` 基准。修改共享基准字段时，所有未覆盖的环境和字段都会取得新值。基准环境自身只通过共享字段调整。
- 当前 `win-work`、`codex-cloud` 和示例 `linux-herdr` 均从 `mac-work` 取得 `gpt-6.1-sol / high`。后续新增环境在没有覆盖项时同样继承。继承范围只有型号与推理强度，Worker、后端、供应商、模型类别、认证和能力证据分别核对。
- 默认值可先于环境条目登记。无精确白名单组合时，`defaults` 报出缺口；有条目后仍须用 `preview` 核对能力。默认候选必须同时符合有效型号与强度。其他精确匹配仍可用于显式选型查询，默认缺口不会触发回退。空策略返回零候选。
- 旧策略可只有 `default_reasoning_effort`。工具保留其按档筛选的预览行为，完整默认型号仍显示为未配置。旧策略也可省略全部默认值字段。

### Mac Codex 盘点

2026-11-09 盘点的两个 Agent 型号为 `gpt-6.1-sol` 和 `gpt-5.6-sol`。修订 2 在 `mac-work / mac-codex / current-default / openai / reasoning` 下逐项登记各自的 `low`、`medium`、`high`、`xhigh`、`max`、`ultra`，合计 12 个候选组合。各证据层分别记录：

| 观察 | 证据状态 | 策略处理 |
| --- | --- | --- |
| Mac Herdr 独立 Codex 工程单元的 `openai / reasoning / gpt-6.1-sol / low` | 原生回合读回实际配置。[模型配置证据](../../docs/agent-hosts.md#decision-model-selection) | 按工程回执登记 |
| 16:23 只读观察的 Mac Codex 默认配置为 `gpt-6.1-sol / xhigh` | 当时的本机配置快照，其中 provider、model_type 未显式设置，实际回合配置未读回 | 保留为历史观察，修订 3 策略基准采用 `high` |
| Mac Codex 模型缓存中两款均可见，各声明上述六档，输入为文本和图像 | 客户端能力声明，除上述工程回执外未逐一执行 | 两型号六档按确认的策略范围显式登记 |

OpenAI 将两款列为推理模型，接受文本和图像输入，直接输出为文本，支持通过 `image_generation` 工具生成图像。图像工具及账号能力按实际入口核验。两款 API 文档都列出 `low`、`medium`、`high`、`xhigh`、`max`，`gpt-5.6-sol` API 另列 `none`。Mac Codex 缓存的 `ultra` 限本次 Mac Codex 候选，其他宿主及 API 各查自身支持档位。[GPT-6.1 Sol 模型文档](https://developers.openai.com/api/docs/models/gpt-6.1-sol) · [GPT-5.6 Sol 模型文档](https://developers.openai.com/api/docs/models/gpt-5.6-sol)

### 其他环境盘点

2026-11-09 只读盘点汇总如下。默认值及支持档位取自所见配置或声明，实际回合的生效值须沿原入口读回。`UNKNOWN` 保留配置或入口尚未给出可核对值的情况。

| 环境与入口 | 配置中的后端／型号 | 所见默认推理强度与支持档位 |
| --- | --- | --- |
| Mac、Win Kimi Code | `managed:kimi-code / kimi-for-coding`，两端当前默认模型 | 默认 `max`，支持 `low`、`high`、`max` |
| Mac、Win Kimi Code | `managed:kimi-code` 下的 `k3`、`k3-256k`、`k3.1-pro`、`k3.1-pro-highspeed` | 各默认 `high`，支持 `low`、`high`、`max` |
| Mac Kimi Code | 另一个 `kimi` provider 下同名五款，含 `kimi-for-coding` 及上行四款 | 同样声明支持 `low`、`high`、`max` |
| Mac、Win Kimi Code | `kimi-for-coding-highspeed` | 声明支持 thinking，推理强度及支持档位 `UNKNOWN` |
| Mac Kimi Code | `kimi / kimi-k2.6` | 默认强度和支持档位 `UNKNOWN` |
| Win Kimi Code | `local-gpt / gpt-6.1-sol`，别名 `local-gpt/gpt-6.1-sol`，协议 `openai_responses` | 默认 `max`，支持 `high`、`max` |
| Win OMP | 动态发现模型，`gpt-6-luna` 为历史回合样本 | 当前默认模型和推理强度 `UNKNOWN` |
| Win Hermes | `cli-proxy / gpt-6-luna` | 当前默认 `high`，其余支持档位 `UNKNOWN` |
| Codex Cloud | `ready`、`runnable`，绑定 Herdr 与 Decision Service 两仓的 `main` | 环境工具未暴露模型及强度，均为 `UNKNOWN` |

Agent 条目限上节两个 Mac Codex 型号。其他入口仍需核对 Worker 与后端归属、型号及别名、模型类别和入口支持档位，再登记完整组合和接线。现有模板、默认值及旧回合沿原入口维护。[环境历史与原生回合](../../docs/local-agent-inventory.md#7-win-work-harness执行侧交接) · [现有环境与采用记录](../../docs/status.md#当前结论)

Decision Service 与 Hindsight 内部使用的 `gpt-6-luna` 是独立的判断路径，不因此进入 Agent 创建白名单。本表不包含私有配置原件、连接地址或凭据。

## 图像模型

`image-models` 按 `provider + model_type + model` 登记。每款模型独立列出操作 `operations`（`generate`、`edit`）及文档依据 `evidence_ref`。本场景把专用图像模型归类为 `image_generation`，其 Agent 推理档位明确不适用，`reasoning_effort` 必须为 `null`。

按公子本轮明确选定的型号，清单登记 OpenAI 的 `gpt-image-2.5-sunburst` 与 `gpt-image-2.5-flare`。生成和编辑操作取自官方文档。当前调用入口能否指定底层型号、账号能否调用、生成及编辑的实际回执仍待核验。图像参数沿各自入口定义。[Sunburst 模型文档](https://developers.openai.com/api/docs/models/gpt-image-2.5-sunburst) · [Flare 模型文档](https://developers.openai.com/api/docs/models/gpt-image-2.5-flare) · [图像生成指南](https://developers.openai.com/api/docs/guides/image-generation)

## 离线使用

在仓库根使用已有 Python 3.10+，仅需标准库：

```bash
python3 scripts/allowlists.py validate
python3 scripts/allowlists.py validate --file config/allowlists/agent-models.json
python3 scripts/allowlists.py defaults --environment mac-work --environment win-work \
  --environment codex-cloud --environment linux-herdr
python3 scripts/allowlists.py preview --capabilities /tmp/observed-capabilities.json
python3 scripts/allowlists.py validate --file config/allowlists/image-models.json
python3 scripts/allowlists.py preview --file config/allowlists/image-models.json --capabilities /tmp/observed-image-capabilities.json
```

`--file` 可指定待检查的策略文件。Agent 的 `validate` 和 `preview` 可用 `--library-root` 指定配套的本地 Worker 库。`defaults` 和图像场景无需 Worker 库。工具在本地离线运行。

预览所用能力文件为 JSON 对象，例如将下列内容保存为上例中的 `observed-capabilities.json`。这里只声明已有证据的组合，不把示例当作全量环境能力：

```json
{
  "capabilities": [
    {
      "environment_id": "mac-work",
      "worker_id": "mac-codex",
      "backend_id": "current-default",
      "provider": "openai",
      "model_type": "reasoning",
      "model": "gpt-6.1-sol",
      "supported_efforts": ["low"],
      "evidence_ref": "docs/agent-hosts.md#decision-model-selection"
    }
  ]
}
```

`environment_id` 可省略，由 Worker 库中的宿主归属核对；已有能力行可以保留 `tools`、`modalities`、`context_window` 等字段。本工具只求策略组合与能力组合的精确交集，任务的输入大小、工具需求等条件仍由原创建者检查。

图像预览的 `capabilities` 行填写 `provider`、`model_type`、`model`、`operations` 和 `evidence_ref`。按实际调用入口或账号提供有明确型号的能力证据；仅能看见图像工具而底层型号未知时，可用空数组 `{"capabilities":[]}`，该工具不产生任何型号匹配。

`validate` 返回场景、策略身份、修订号、状态、原文件摘要及条目数，Agent 场景另返回共享默认值及基准环境。`defaults` 按所查环境返回有效默认值和逐字段来源。`shared_default` 指向输出中的 `default_environment_id`，`environment_override` 表示该字段由所查环境设置。当前只有 `mac-work` 有默认组合条目，其余示例返回 `default_combination_not_allowlisted`。条目声明不等于已核 Worker 或能力，`defaults` 的 `worker_library_checked` 为 `false`，`capability_status` 为 `not_checked`。

`preview` 另返回 `matched_entries`、带原因的 `excluded_entries` 和能力来源。Agent 的 `default_matched_entries` 和 `default_excluded_scopes` 描述按环境默认型号和档位的筛选。图像匹配行用 `matched_operations` 给出逐操作来源，用 `excluded_operations` 标出同型号其余操作的能力缺口。匹配只表示输入文件声明相容，宿主可调用性及创建授权仍须分别确认。

三个命令均返回 `creation_enforced=false`、`dispatch_performed=false`。草案或停用状态为 `readiness=not_enabled`，有效策略为 `readiness=offline_only`。`valid` 仅表示本命令负责的检查通过。运行接线和宿主采用各自取证。

## Mac Codex 新工作单元

原创建者调用 [herdr_model_selection.py](../../scripts/herdr_model_selection.py) 的 `apply`，沿原工作流检查点、授权绑定及当前宿主能力文件接入。绑定中的旧型号与档位参数由本次选型替换。显式要求填写 `selection.requirements.models` 和 `selection.requirements.efforts`，缺少的字段分别使用环境默认值。程序把所需工具、模态、上下文、环境、Worker、后端、供应商和型号档位与已确认的完整条目求交，只有剩余组合进入 Decision Service。默认或显式组合不可用时返回 `unavailable`。程序将获准的供应商映射到原生参数，绑定中冲突的显式供应商覆盖会拒绝。

决策请求与启动意图各保存 `model_policy`，包含 `policy_id`、`revision` 和原文件 `policy_sha256`。目标就绪后、持久化启动意图前，程序从发布目录重新读取策略，核对冻结摘要及精确组合。策略变动或无法读取时返回 `stale` 和 `launch_outcome=not_started`。开始选型时缺少可用的 `active` 策略则返回 `policy_blocked`。已有启动意图的工作单元沿原绑定和冻结记录对账，原生启动、实际回合模型和业务验收分别取证。

程序覆盖 Mac Codex `new-unit`。协调员、项目负责人和 Worker 若通过 Herdr 手工新建，仍须由创建者核对允许组合。Win、Cloud 和图像调用各自待接，图像清单的 `active` 表示型号与操作已确认。

## 更新、接线与回退

1. 协调员汇总需求和环境证据，修改对应场景文件，递增 `revision`。Git 提交说明记录调整原因，Git 历史保留前后版本，不另建变更台账。
2. 运行 `validate`，再用现有能力证据运行 `preview`，核对允许组合、未匹配项及空集合。维护者沿有效授权确认和发布修订。

### Mac 固定发布

从仓库根用已合并到 `main` 的完整 SHA 准备新目录。安装器从该提交提取脚本、策略及 Worker 库闭包，校验包内两份策略，并写入 `install-receipt.json`。此时原调用路径仍指向原版本。

```bash
release_sha='<已合并的完整提交 SHA>'
consumer_root="$HOME/Library/Application Support/HerdrDecisionConsumer"
staged_release="$consumer_root.$release_sha"
previous_release="$consumer_root.8612a9a"
python3 scripts/install_herdr_decision_consumer.py \
  --revision "$release_sha" --destination "$staged_release"
```

核对新包回执的源码提交和文件摘要。暂停该入口并确认无在途 consumer 调用后，将原目录单独保留，再把新包切到原绝对路径。下例的备份名对应旧版 `8612a9a`，以后按当时版本选未占用的名字。外部 workflow、state 和 Herdr 原线程保留在原位置。

```bash
test -d "$staged_release" && test ! -e "$previous_release" && test ! -L "$previous_release" &&
  mv "$consumer_root" "$previous_release" &&
  mv "$staged_release" "$consumer_root"
```

切换后，从原绝对路径回读 `install-receipt.json` 并用包内 `app/scripts/allowlists.py validate` 核策略修订及摘要。记录源版本、宿主快照和调用入口采用状态。若两次移动之间失败，保持暂停并将原目录恢复到原绝对路径。

回退前同样暂停入口并核在途调用。沿同一终端的路径变量保留新版目录，再恢复已核摘要的旧包。

```bash
test -d "$previous_release" && test ! -e "$staged_release" && test ! -L "$staged_release" &&
  mv "$consumer_root" "$staged_release" &&
  mv "$previous_release" "$consumer_root"
```

旧版 `8612a9a` 缺少创建前白名单检查。回退到该版时暂停新建，仅沿原绑定对账已有启动意图。

导航型 [allowlist-management Skill](../../skill/allowlist-management/SKILL.md)帮助查找约定与使用工具。创建规则由接入的程序执行。
