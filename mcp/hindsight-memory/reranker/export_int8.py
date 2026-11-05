#!/usr/bin/env python3
"""Convert a locally downloaded ModelScope BGE reranker to CPU ONNX Q8.

No model download or service operation is performed. Run in an isolated CPU
export environment, with requirements-export.txt and a CPU-only Torch build.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Local ModelScope safetensors directory")
    parser.add_argument("--output", type=Path, required=True, help="New or empty conversion directory")
    parser.add_argument("--modelscope-revision", required=True, help="Revision recorded by the ModelScope download")
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    output = args.output.resolve()
    if not source.is_dir() or not (source / "config.json").is_file() or not list(source.glob("*.safetensors")):
        parser.error("--source must contain config.json and local safetensors weights")
    if output == source or source in output.parents:
        parser.error("--output must be outside the source model directory")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        parser.error("--output must be new or empty; inspect any earlier conversion before retrying")

    # Optimum accepts a local model directory and local_files_only. Keep its
    # Transformers/tokenizer helpers offline too; never use a model registry ID.
    # https://github.com/huggingface/optimum-onnx/blob/v0.1.0/optimum/exporters/onnx/__main__.py
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
    # The FP32 BGE weights exceed the ONNX protobuf's single-file size limit.
    # https://github.com/huggingface/optimum-onnx/blob/v0.1.0/optimum/exporters/onnx/convert.py#L508
    os.environ["FORCE_ONNX_EXTERNAL_DATA"] = "1"

    import onnx
    from optimum.exporters.onnx import main_export
    from optimum.onnxruntime import ORTQuantizer
    from optimum.onnxruntime.configuration import AutoQuantizationConfig
    from transformers import AutoTokenizer

    output.mkdir(parents=True, exist_ok=True)
    fp32_dir = output / "fp32"
    int8_dir = output / "int8"
    source_files = []
    for path in sorted(source.rglob("*")):
        if path.is_file() and not any(part.startswith(".") for part in path.relative_to(source).parts):
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            source_files.append({"path": path.relative_to(source).as_posix(), "bytes": path.stat().st_size, "sha256": digest})

    print("Exporting local BAAI/bge-reranker-v2-m3 to FP32 ONNX on CPU.", flush=True)
    main_export(
        model_name_or_path=str(source),
        output=fp32_dir,
        task="text-classification",
        opset=17,
        device="cpu",
        dtype="fp32",
        local_files_only=True,
        trust_remote_code=False,
        do_validation=True,
    )

    # AVX2 is an 8-bit preset. In this pinned Optimum version it uses QUInt8
    # weights, not signed QInt8; the receipt records the actual dtype below.
    # https://github.com/huggingface/optimum-onnx/blob/v0.1.0/optimum/onnxruntime/configuration.py#L440
    quantization = AutoQuantizationConfig.avx2(is_static=False, reduce_range=False)
    quantizer = ORTQuantizer.from_pretrained(fp32_dir, file_name="model.onnx")
    print("Applying AVX2 dynamic 8-bit quantization; retaining external-data support.", flush=True)
    # ST's helper does not expose this external-data option; call Optimum directly.
    # https://github.com/huggingface/optimum-onnx/blob/v0.1.0/optimum/onnxruntime/quantization.py#L279
    quantizer.quantize(
        quantization_config=quantization,
        save_dir=int8_dir,
        file_suffix="q8_avx2",
        use_external_data_format=True,
    )
    AutoTokenizer.from_pretrained(str(source), local_files_only=True).save_pretrained(int8_dir)

    graphs = {}
    for stage, path in (("fp32", fp32_dir / "model.onnx"), ("int8", int8_dir / "model_q8_avx2.onnx")):
        # Passing a filename lets ONNX resolve external tensor data for large graphs.
        onnx.checker.check_model(str(path))
        graph = onnx.load(str(path), load_external_data=False)
        operators = Counter(node.op_type for node in graph.graph.node)
        initializers = Counter(onnx.TensorProto.DataType.Name(tensor.data_type) for tensor in graph.graph.initializer)
        quantized_weights = Counter(
            onnx.TensorProto.DataType.Name(tensor.data_type)
            for tensor in graph.graph.initializer
            if tensor.name.endswith("_quantized") and tensor.data_type in (onnx.TensorProto.INT8, onnx.TensorProto.UINT8)
        )
        external_data = sorted({
            entry.value
            for tensor in graph.graph.initializer
            for entry in tensor.external_data
            if entry.key == "location"
        })
        graphs[stage] = {
            "path": path.relative_to(output).as_posix(),
            "operator_counts": dict(sorted(operators.items())),
            "initializer_dtype_counts": dict(sorted(initializers.items())),
            "quantized_weight_dtype_counts": dict(sorted(quantized_weights.items())),
            "external_data": external_data,
        }
    if not graphs["int8"]["operator_counts"].get("MatMulInteger") or not graphs["int8"]["quantized_weight_dtype_counts"]:
        raise RuntimeError("The output does not contain verified dynamic integer MatMul and 8-bit weights")
    if set(graphs["int8"]["quantized_weight_dtype_counts"]) != {"UINT8"}:
        raise RuntimeError("The observed quantized weight dtype does not match the pinned AVX2 QUInt8 preset")

    artifacts = []
    for path in sorted(output.rglob("*")):
        if path.is_file():
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            artifacts.append({"path": path.relative_to(output).as_posix(), "bytes": path.stat().st_size, "sha256": digest})
    with Path(__file__).open("rb") as stream:
        exporter_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
    receipt = {
        "schema_version": 1,
        "status": "EXPORTED_QUANTIZED_GRAPH_CHECKED_NOT_BENCHMARKED",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "registry": "ModelScope",
            "model_id": "BAAI/bge-reranker-v2-m3",
            "revision": args.modelscope_revision,
            "revision_evidence": "caller-supplied download revision; local source bytes hashed below",
            "files": source_files,
        },
        "conversion": {
            "device": "cpu",
            "offline": True,
            "opset": 17,
            "fp32_export_validation_requested": True,
            "quantization": "dynamic AVX2 8-bit; unquantized float tensors remain",
            "weights_dtype": quantization.weights_dtype.name,
            "activations_dtype": quantization.activations_dtype.name,
            "per_channel": quantization.per_channel,
            "reduce_range": quantization.reduce_range,
            "configured_operators": [getattr(operator, "value", str(operator)) for operator in quantization.operators_to_quantize],
            "external_data_format": True,
            "exporter_sha256": exporter_sha256,
        },
        "versions": {name: version(name) for name in (
            "torch", "transformers", "optimum", "optimum-onnx", "onnx", "onnxruntime", "tokenizers", "huggingface-hub", "safetensors", "numpy"
        )},
        "graphs": graphs,
        "artifacts": artifacts,
    }
    receipt_path = output / "conversion-receipt.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Conversion receipt: {receipt_path}", flush=True)


if __name__ == "__main__":
    main()
