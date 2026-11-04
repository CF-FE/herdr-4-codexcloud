# 受限项目协调工具

Owner：本仓项目协调层维护者。源码为本目录的 `server.py`，仅使用 Python 标准库及同一固定 release 的 `scripts/project_coordinator.py`、`coordinator_transport.py`、`coordinator_bridge.py`、`coordinator_goal_channel.py`。不跨仓导入、不读取原生 session 文件、不创建负责人、网络服务或凭据。

用途是让已绑定负责人读取本单上下文与固定原件，并在额外明确许可下联系已登记的既有独立审查者核验已有证据。当前宿主是否有合法执行者另核；首次联系前缺少合法审者或许可返回 `BLOCKED`，已有联系意图的不明结果保留 `UNKNOWN`。不把工具列表、提名或本地排队当接手。

入口（由已审原生进程按需管理 stdio）：

```sh
/absolute/approved/python -I -S -B \
  /absolute/fixed-release/mcp/project-coordinator-tools/server.py \
  --policy /absolute/private/single-job-tools.json
```

参数必须来自具体宿主审批，不能直接运行示例。启动不创建配置或自动发现任务。原生运行配置、工具策略及源文件闭包均须固定摘要；全局 profile、其它项目、看板写权及音频生成权限不继承。

工具按角色和允许动作收窄：`goal_context`、`read_review_evidence`、`coordinate_independent_review`、`read_coordination_report`。独立审查者仅获读取工具；报告沿原生通道回收，不开放再次委派。普通原件的 hash 校验只证明字节身份；明确接手须来自绑定原生线程的可信回读。

本版仅读取受审的 JSON/text，不提供 WAV 播放或音频感知。审查者子任务可核既有听检证据并报告缺口，不能据模型自述完成新的听检；`independent_listening` 子任务的 `COMPLETED` 会被拒绝。实际听检仍需具有合法听辨入口的执行者与可核对的独立原件。

合同、范围、子动作预算、幂等与单件启用/撤销步骤见 [目标运行入口](../../docs/goal-runtime.md)。源码及隔离测试不代表真实线程、独立听辨或宿主安全配置已经验收。
