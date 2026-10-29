# 试点任务交接模板

这是待填写的准备模板，不是任务创建、派发或安装授权。已有任务包复用其字段，只补本次缺项；正式状态与验收仍沿原任务入口。

上下文、事件与回执使用[组织上下文模板](../../skill/organization-collaboration/references/context-and-reporting.md#绑定与确认)、[任务追溯](../../skill/organization-collaboration/references/task-traceability.md#三层记录与唯一关联)和[产物复核与验收回执](../../skill/organization-collaboration/references/context-and-reporting.md#产物复核与验收回执)。下面只补 Herdr 试点绑定，不另造统一成功状态。

## 原任务与允许范围

| 字段 | 本次值 |
| --- | --- |
| 唯一任务引用、正式记录入口 | UNKNOWN |
| 派发者、执行者、资产当前写者 | UNKNOWN |
| 汇报对象、复核者、负责人/验收者 | UNKNOWN |
| 有效授权及允许写入/运行范围 | UNKNOWN |
| 固定输入版本/摘要、预期产物和验收条件 | UNKNOWN |
| 预算、停止条件及未决承接 | UNKNOWN |

## 执行绑定

| 字段 | 本次值 |
| --- | --- |
| 控制端、实际执行机及 OS | UNKNOWN |
| Tailscale 网络模式、连接证据、SSH 目标/用户及 host key 核验 | UNKNOWN |
| Herdr CLI、远端安装及运行中 server 的版本 | UNKNOWN |
| 保存的机器 profile ID、准确 session | UNKNOWN |
| 原生 workspace、pane、Agent 名称及可取得的会话引用 | UNKNOWN |
| Agent 可执行文件、参数、供应商、模型和权限映射 | UNKNOWN |
| 安全认证配置的引用；不得填入凭证 | UNKNOWN |

## 观察与交付

沿原记录追加发生/观察时点、原生状态、调用结果和可证实的效果；未发起、已发送但结果未知和已取得产物分别如实记录。中断后保留原执行引用，不通过新派发填补未知。

产物按所在端记录路径、固定版本、适用的字节数/摘要和读取者；自检、独立复核、负责人验收各有其范围和回执。按[试点指引](../pilot.md)记录中断恢复及与原路线的实际比较，未取得基线记 UNKNOWN。
