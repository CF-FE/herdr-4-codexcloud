# Win Hindsight 1.2.0 召回排序拒答补丁

此补丁对应 [Decision Service Issue #6](https://github.com/CF-FE/decision-service/issues/6)。目标是让使用 OpenAI Decisions 的候选排序在部分候选被明确拒答时，仍返回有正常判断的候选。适用的 Windows 安装文件与 [Hindsight 固定上游提交](https://github.com/vectorize-io/hindsight/tree/34a138684a899b6ec34175f7a2b633860466d289/hindsight-api-slim/hindsight_api) 一致。三个目标文件的安装前后 SHA-256 和补丁摘要保存在 [source-lock.json](source-lock.json)。

- 解析器按题名核对排序批次，将显式拒答保留为独立标记。评分前移除对应候选，按原候选位置映射剩余答案。真实零概率仍进入原评分和筛选逻辑。
- 全部候选被拒答时，跨批汇总后仍抛出 `DecisionRefusedError`。排序的缺答、重复、未知题名和无效判断继续走错误路径。HTTP 错误、超时、已有重试和窗口二分沿原路径处理。
- 去重只比较仍在排序池中的候选，并使用其原有类型与 embedding 位置。去重题的拒答仍失败，缺答也作为错误处理。普通 `predict()`、consolidation 及其他 Decision Model 调用使用原严格拒答行为。
- 原 trace 可记录题名、`refused: true`、正常答案和已有 usage。新增告警只记录拒答题数与总题数。局部容错限于 OpenAI 召回候选排序。

## 离线校验

[补丁](recall-refusals-1.2.0.patch)可以在包含 `hindsight_api/` 的临时目录正向和逆向应用。先把三个原文件复制到隔离目录，逐一核对 `before_sha256`，再使用 Git 的补丁命令。每次应用后分别核对三个目标哈希。

```powershell
git -C $stage apply --check $patch
git -C $stage apply $patch
git -C $stage apply -R --check $patch
git -C $stage apply -R $patch
```

在完整的隔离 Hindsight 1.2.0 测试环境，用其中的 Python 运行 [合成回归测试](test_recall_refusals.py)。

```powershell
& $TestPython -B $TestFile -v
```

测试在 HTTP 交换边界返回合成应答，覆盖同批和跨批混合拒答、全拒答、正常和零概率答案、开启 prune、题名与字段校验、去重映射、trace、429、非重试 HTTP 错误、超时和窗口二分。Mac 本轮加载了与 Win 哈希一致的原模块和补丁模块，依赖使用对应版本的 Python、Pydantic、aiohttp、NumPy。Mac 隔离夹具用确定性计数替代 Win tokenizer，并隔离 trace 存储。结果属于本地行为验证。

## Win 维护窗口与回退

生产应用在独立审查和批准后沿既有维护入口办理。操作前确认安装版本、当前三文件哈希、原 SYSTEM 启动任务和 PG／BGE／API 监听归属。沿 Win 原生维护权限检查在途操作并保存脱敏回执。用现有保护权限备份三份原文件，核对备份哈希和 ACL。

复用 [原 API 重载入口的进程边界](../reranker/Restart-HindsightCPU.ps1)，核准并停止既有 API 进程树。在 API 停止期间替换三个经过哈希核验的文件，读取回完整的 `after_sha256` 后，沿既有 `Hindsight-Stack-Startup` 启动 API。源码加载检查覆盖这三个目标模块。记录新 API 进程、健康检查及 PG／BGE 进程身份，再按已批准的真实验证范围收证。

应用中断时，先将目标目录恢复到一套完整且已核对哈希的版本，再启动 API。正式回退时，核对当前文件符合 `after_sha256`、原备份符合 `before_sha256`，沿相同 API-only 窗口恢复三份原文件，逐一读回原哈希和 ACL，再走既有启动任务。遇到其他版本或并行文件变化，先对账维护记录。备份及回执留在部署宿主的私有位置。
