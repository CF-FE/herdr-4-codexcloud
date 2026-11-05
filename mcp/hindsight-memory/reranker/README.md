# Hindsight BGE Q8 CPU 精排

本目录维护 Hindsight API Slim **1.2.0** 的原生 ONNX CPU reranker。使用公子指定的 **`BAAI/bge-reranker-v2-m3`、ModelScope 来源、8 位量化和 CPU 推理**，为已召回的查询／事实对重新评分。原 GPU BGE-M3 embedding 服务继续使用，不迁移模型、不重建记忆库，也不按某个 Skill 名称或文档 ID 人工加权。

## 模型与运行边界

| 项目 | 本次固定采用 |
| --- | --- |
| 原模型 | `BAAI/bge-reranker-v2-m3` |
| 唯一模型下载源 | [ModelScope 模型页](https://modelscope.cn/models/BAAI/bge-reranker-v2-m3) |
| ModelScope revision | `e099d4b9cdbd291b1569d416f19aaa6523570bc3` |
| 已核原始文件 | 8 文件，约 2.27 GB，逐项核来源 Size／SHA-256 |
| 转换结果 | 动态 AVX2 Q8，实际量化权重类型为 **UINT8／QUInt8**；仍含未量化浮点张量 |
| `int8` 目录文件总量 | **588,153,984 字节**，不是进程内存占用或全模型每个张量均为 8 位的承诺 |
| 推理 provider | 仅 `CPUExecutionProvider` |
| CPU 参数 | 4 线程、batch 8、最大并发 1、关闭 CPU memory arena |
| 生产精排候选上限 | 100；这是 CPU 延迟与候选覆盖之间的折中 |

量化检查不以文件名或压缩体积代证。[导出脚本](export_int8.py)检查 ONNX 图、`MatMulInteger`、实际 UINT8 量化权重及 external-data 引用，并记录未量化张量、依赖版本、来源文件和产物 SHA-256。启用前还要把实际 benchmark 模型与转换回执中的图及外部权重文件对应起来。

## 文件与执行顺序

以下操作属于明确授权的模型维护窗口。先从固定源码版本复制本目录，沿现有部署根运行；不要导入其他仓库的运行目录。命令示例从本目录运行，导出环境 Python 路径需换成实际独立环境。

### 1. 只从 ModelScope 下载

[download_modelscope.py](download_modelscope.py)固定模型、revision 和 8 文件闭包，重定向仅接受 ModelScope 域名；没有 Hugging Face 或其他 registry 兜底。已有完整文件先校验再复用；未完成文件保留为 `.part`，遇到已有 `.part` 会停止并要求先对账，不覆盖或静默重下。

```powershell
$RunPython = 'D:\Hindsight\app\.venv\Scripts\python.exe'
$SourceModel = 'D:\Hindsight\models\bge-reranker-v2-m3-e099d4b9'
$Q8Model = 'D:\Hindsight\models\bge-reranker-v2-m3-e099d4b9-q8'
& $RunPython .\download_modelscope.py $SourceModel
```

`source-listing.json` 和 `download-receipt.json` 留在模型目录。下载完成要核 `complete=true`、固定 revision 与全部文件摘要，不能只看退出码。

### 2. 在独立 CPU 导出环境转换

导出使用独立 venv、**Torch 2.11 的 `+cpu` 构建**和 [requirements-export.txt](requirements-export.txt)。该依赖组合使用 Transformers 4.57.6，不能装进原 SentenceTransformers 6.2 的 BGE 环境，也不能为转换替换现有 Hindsight runtime。CPU Torch 单独安装，导出 requirements 不隐式选择 Torch 构建。

```powershell
$ExportPython = '<CPU_EXPORT_VENV>\Scripts\python.exe'
uv pip install --python $ExportPython --torch-backend cpu 'torch==2.11.0'
uv pip install --python $ExportPython -r .\requirements-export.txt
& $ExportPython .\export_int8.py --source $SourceModel --output $Q8Model --modelscope-revision 'e099d4b9cdbd291b1569d416f19aaa6523570bc3'
```

`export_int8.py`只读本地 safetensors，设置离线模式、`local_files_only=True`、`trust_remote_code=False` 和 CPU 设备。输出目录必须新建或为空，不能位于源模型目录内。它先生成并请求验证 FP32 ONNX，再以 Optimum AVX2 preset 动态量化，保留大模型 external-data 支持。

`conversion-receipt.json` 的成功状态是 `EXPORTED_QUANTIZED_GRAPH_CHECKED_NOT_BENCHMARKED`：图转换及量化检查完成，尚不表示精排效果、生产采用或用户验收完成。

### 3. 准备原生运行依赖并做冻结候选对比

[requirements-runtime.txt](requirements-runtime.txt)包含 ONNX Runtime 1.31.1、Transformers 5.22.1 和 safetensors 0.8.0。运行精排不需要 Torch 或 GPU runtime；按现有 Hindsight 环境核对和补齐，不触碰独立 BGE 服务的环境。导出环境和运行环境的 Transformers 版本不同，不合并安装。

[benchmark_int8.py](benchmark_int8.py)调用固定 Hindsight 版本的原生 `OnnxCrossEncoder`，并断言 session 只有 `CPUExecutionProvider`。输入 case 包含 `query`、按原召回顺序冻结的 `facts`（`id/text/document_id`）和用于评价的 `expected_document_id`。模型只接收查询和事实文本；预期文档 ID 在评分完成后才用于统计名次。

```powershell
& $RunPython .\benchmark_int8.py --model "$Q8Model\int8\model_q8_avx2.onnx" --tokenizer "$Q8Model\int8" --cases '<FROZEN_CASE_1.json>' '<FROZEN_CASE_2.json>' --max-candidates 100 --output 'D:\Hindsight\tmp\rerank-evaluation-20261105\benchmark-100-bound-receipt.json'
```

输出回执不能覆盖已有文件；保留模型及 tokenizer 路径和各文件摘要、CPU provider、参数、耗时、依赖版本和前十条评分。配置脚本逐项比对模型图、external-data 和 tokenizer 的字节数及 SHA-256，不能仅凭路径认领 benchmark。冻结候选成绩只验证这一组候选的重排效果，不代替真实 MCP 召回。

### 4. 备份配置、应用并沿原 SYSTEM 入口重载

[runtime.env.example](runtime.env.example)列出本轮 reranker 字段。[configure_cpu.py](configure_cpu.py)先核固定 ModelScope 转换来源、全部 `int8/` 产物摘要、同模型 CPU benchmark 路径，以及冻结查询的目标名次未变差，然后备份原 `config/hindsight.env`，仅更新 reranker 参数。embedding、模型服务认证和其他 provider 配置仍归原文件维护。

```powershell
& $RunPython .\configure_cpu.py --root 'D:\Hindsight' --benchmark 'D:\Hindsight\tmp\rerank-evaluation-20261105\benchmark-100-bound-receipt.json'
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File .\Restart-HindsightCPU.ps1 -Root 'D:\Hindsight'
```

配置回执保存在部署根 `evidence/reranker-cpu-<timestamp>/deployment-receipt.json`，包含前后配置摘要、私有备份引用、转换及 benchmark 回执摘要、已核模型产物。`CONFIGURED_RESTART_REQUIRED`只表示配置已写入。

[Restart-HindsightCPU.ps1](Restart-HindsightCPU.ps1)核既有 startup task 的 SYSTEM 身份、已登记 API 进程树及监听归属，只停止该 API 树，再调用既有 `Hindsight-Stack-Startup` 入口补起缺失 API。它不注册或修改任务、不新增服务，原 PostgreSQL 和 BGE 保留。脚本返回 `SYSTEM_API_RESTART_REQUESTED`，不代证新 API 就绪；重载后分别核原 API 健康、实际 CPU 精排、PG／BGE 保全和真实 MCP 查询。

Web UI 不随本目录启动，继续使用[手动前台入口](../control-plane/README.md)，验证后保持关闭。此处对既有 API 的一次重载不改变 UI 的“无自启、可见终端、Ctrl+C 关闭”约定。

## 已取得的验证与局限

以下是 2026-11-05 本轮观测，不是全库质量或稳定耗时承诺。

| 范围 | 已核结果 |
| --- | --- |
| 冻结主查询的 287 个候选 | 目标 Skill 的直接来源首条由第 5 到第 3；整合摘要排第 1，其 8 条来源事实均追溯到目标模块 Skill |
| 冻结的可选字段规则对比 | 对应直接规则由第 33 到第 3 |
| 生产真实 MCP，原中文自然查询，不加 Skill 名称或 tag 过滤 | 整合摘要排第 1；直接回答可选字段的规则由原第 39 到第 4；返回的 34 条均有 reranker 分；本次请求耗时 **27,558 ms** |
| 生产真实 MCP，简化代码但保持原行为的对照查询 | code-simplification 的整合摘要排第 1，直接来源排第 2；32 条结果均有 reranker 分；本次请求耗时 **26,139 ms** |

CPU 精排增加了延迟。100 候选上限减少评分工作量，也可能排除更靠后的相关候选；4 线程、batch 8、并发 1 是本轮采用参数，不是所有机器的最优值。两个查询的验证不足以证明总体相关性、跨领域效果或长期吞吐。

本目录不新增记忆、不重导资料，也不按预期 Skill 调高分数。现有记忆抽取与自动整合状态沿原操作记录，不能因这轮精排通过改写它们的完成状态。

## 回退

先沿部署回执确认本次配置与当前配置仍一致，再使用该回执回退：

```powershell
& $RunPython .\configure_cpu.py --root 'D:\Hindsight' --rollback '<DEPLOYMENT_RECEIPT.json>'
```

脚本要求当前配置 SHA-256 仍等于回执的 `after_sha256`，且备份内容匹配 `before_sha256`，写入前再次复核；检测到后续变更时拒绝自动回退，须沿原配置记录对账。此检查不是跨进程锁，配置维护仍须保持单写者。成功只返回 `CONFIG_RESTORED_RESTART_REQUIRED`，生效仍使用上面的既有 SYSTEM API 重载入口。保留源模型、转换产物和证据，不删除 bank、原 GPU BGE 模型或数据库。

## 固定依据

- [ModelScope 模型页](https://modelscope.cn/models/BAAI/bge-reranker-v2-m3)，下载 revision 及文件完整性见下载回执。
- [Hindsight 1.2.0 `cross_encoder.py`](https://github.com/vectorize-io/hindsight/blob/34a138684a899b6ec34175f7a2b633860466d289/hindsight-api-slim/hindsight_api/engine/cross_encoder.py)，原生 ONNX CPU reranker 与参数。
- [Optimum ONNX 0.1.0 量化 preset](https://github.com/huggingface/optimum-onnx/blob/v0.1.0/optimum/onnxruntime/configuration.py)、[量化与 external-data 实现](https://github.com/huggingface/optimum-onnx/blob/v0.1.0/optimum/onnxruntime/quantization.py)。

源码变更在仓库根运行 `python3 scripts/check_repository.py` 和 `python3 -m unittest discover -s tests -v`；本轮由整合主线程执行。离线检查、目标宿主采用、生产 MCP 对照和负责人验收分别记录，运行配置、凭据、原始日志和模型大文件不提交仓库。
