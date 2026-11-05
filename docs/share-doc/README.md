# Super-Agent 分享图

为 2026-11-06 分享准备，内容观测截至 **2026-11-05**。全部为 **16:9**，提供可缩放 SVG 和 **3840 × 2160 PNG**；PPT 可直接插入 PNG，需缩放或编辑时使用 SVG。图中文字按已确认职责和实际接入边界编排，实时进展仍查[状态页](../status.md)。

| 顺序 | 讲什么 | SVG | 高清 PNG |
| --- | --- | --- | --- |
| 1 | 总架构：需求终端、两层 Agent、三类执行环境和共享基础设施 | [总架构图](super-agent-architecture.svg) | [下载 PNG](super-agent-architecture.png) |
| 2 | 任务闭环：完整目标向下委派、负责人验收、两个入口异步回收、阻塞升级 | [任务闭环](task-lifecycle.svg) | [下载 PNG](task-lifecycle.png) |
| 3 | 项目工作流：程序执行、模型决策、原生任务和跨 Worker 成果交接 | [动态工作流](project-workflow.svg) | [下载 PNG](project-workflow.png) |
| 4 | Hindsight：云端／Mac／Win 共享项目经验，Web 管理端手动前台启停 | [记忆拓扑](hindsight-topology.svg) | [下载 PNG](hindsight-topology.png) |

## 讲解要点

1. **双层 Agent 加需求终端。** 云端Dot调研、确定方案、委派和向公子汇报；下游为协调组织层与 Worker 层。项目默认由现任负责人验收。组织岗位和本地 Worker 在 Herdr；Codex Cloud 的具体托管作业沿独立路由执行。
2. **弱连接下继续推进。** Task Mailbox 看原单，GitHub Project／Issue 看项目；进展可异步回填，不以云端Dot已读作为执行或验收门槛。明确项目归属的工作由负责人组织。
3. **项目拥有流程。** 负责人维护 `workflows/`、集合 README 和项目规范；确定步骤由程序运行，新的判断交模型。运行检查点、项目状态、规范与可检索经验分别有自己的维护位置。
4. **经验共享，状态沿原单。** Hindsight 同项目共享稳定 bank，Mac 经 LAN SSH，云端经 VPS／EasyTier。Web UI 已安装，脚本在可见终端前台运行，Ctrl+C 关闭，不自启。只读调用通过与成功提取入库是两项结果。

## 已验与待验

| 内容 | 这批图采用的结论 |
| --- | --- |
| Mac／VPS 部署 | Herdr main `84a68b2`、VPS main `da3356f`；Mac 共 11 文件、VPS 19 文件及 unit 已核一致，实际只读调用通过 |
| Mac Codex | 用户级共享插件已安装启用，实际包入口发现 39 工具、`list_banks` 成功；各已打开会话不由此推定热加载 |
| 云端客户端 | 已有项目级读取；公子回报的新写入经原操作独立核为 completed，文档原文与来源一致，提取 1 条记忆 |
| Codex Cloud | 公子已回传云端读取结果；具体托管作业的环境与采用仍逐单记录，图中虚线表示按任务接入，不由一个客户端代证全部环境 |
| Win Web UI | 官方 1.2.0 已安装并核已有 bank、配置、原操作页面；关闭后原 API／PG／BGE 保留。对应脚本新增不包含在上述 main 对齐回执中 |
| 后端提取与召回 | 27 个 Skill／40 文件导入完成、1,515 条记忆及原文来源读回通过；后续已启用魔搭 BGE Q8 CPU 精排，两条查询的目标摘要均排第 1，实测约 26–28 秒。历史失败保留；[精排限制与回退](../../mcp/hindsight-memory/reranker/README.md)另述 |

来源：[总架构](../architecture.md)、[任务协作](../coordination-policy.md)、[项目工作流](../project-workflow-development.md)、[Hindsight 合同](../hindsight-memory-integration.md)、[Web 使用](../../mcp/hindsight-memory/control-plane/README.md)、[部署核验原记录](https://github.com/CF-FE/herdr-4-codexcloud/issues/13#issuecomment-6655425331)。图中不包含凭据、私有网络地址或会话配置。

动态环境能力在 [CF-FE Project #6「环境能力」](https://github.com/orgs/CF-FE/projects/6/views/3)维护。新增 `linux-herdr` 等环境时更新能力卡、核实际入口；不从图中的示例推定该环境已存在或通过验证。

## 更新与导出

可编辑排版源为 [build_diagrams.py](build_diagrams.py)，SVG／PNG 是同一次渲染的静态分享文件。使用已有 Matplotlib 与中文字体，在仓库根运行：

```bash
python3 docs/share-doc/build_diagrams.py
```

默认读取 Mac 的黑体字体；其他系统用 `--font /absolute/path/to/cjk-font.ttf` 指定已安装中文字体。SVG 保留文字，目标电脑字体不同可能改变显示，PNG 的排版固定。生成脚本仅输出本目录图片，不访问服务或调用模型。

更改分享内容时先核对应文档、固定提交与原任务证据，再更新排版源并同时导出两种格式。旧图中“云端Dot参与 Router、组织业务验收”等已失效表述已随本次总图更新；历史验收原件保持原版本。
