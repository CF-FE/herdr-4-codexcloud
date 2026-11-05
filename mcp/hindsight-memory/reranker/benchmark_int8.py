"""Benchmark frozen recall candidates using Hindsight's native ONNX CPU reranker."""

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import time


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--cases", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-candidates", type=int, default=0, help="Use the first N fused candidates; 0 keeps all")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new output receipt; preserve earlier benchmark evidence")
    if args.max_candidates < 0:
        parser.error("--max-candidates must be nonnegative")
    artifacts = []
    paths = {path.resolve() for folder in (args.model.parent, args.tokenizer) for path in folder.rglob("*") if path.is_file()}
    for path in sorted(paths):
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        artifacts.append({"path": str(path), "bytes": path.stat().st_size, "sha256": digest})
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", CUDA_VISIBLE_DEVICES="-1", TOKENIZERS_PARALLELISM="false")
    from hindsight_api.engine.cross_encoder import OnnxCrossEncoder

    # https://github.com/vectorize-io/hindsight/blob/34a138684a899b6ec34175f7a2b633860466d289/hindsight-api-slim/hindsight_api/engine/cross_encoder.py
    reranker = OnnxCrossEncoder(
        model_path=str(args.model.resolve(strict=True)),
        tokenizer_name_or_path=str(args.tokenizer.resolve(strict=True)),
        batch_size=8, threads=4, max_concurrent=1, max_tokens=512,
    )
    started = time.perf_counter()
    await reranker.initialize()
    initialization_seconds = time.perf_counter() - started
    providers = reranker._session.get_providers()
    if providers != ["CPUExecutionProvider"]:
        raise RuntimeError(f"Expected CPU-only inference, got {providers}")
    results = []
    for path in args.cases:
        case = json.loads(path.read_text(encoding="utf-8"))
        facts = case["facts"][:args.max_candidates or None]
        # Only query and fact text go into the model. Expected document IDs are
        # used afterwards to measure rank; no document/name boosts are applied.
        started = time.perf_counter()
        scores = await reranker.predict([(case["query"], fact["text"]) for fact in facts])
        elapsed = time.perf_counter() - started
        if len(scores) != len(facts) or not all(math.isfinite(score) for score in scores):
            raise RuntimeError("Reranker did not return one finite score per candidate")
        order = sorted(range(len(facts)), key=lambda index: scores[index], reverse=True)
        expected = case["expected_document_id"]
        before = next((i + 1 for i, fact in enumerate(facts) if fact.get("document_id") == expected), None)
        after = next((rank + 1 for rank, index in enumerate(order) if facts[index].get("document_id") == expected), None)
        result = {
            "case": path.name, "query": case["query"], "candidates": len(facts),
            "expected_document_id": expected, "baseline_first_rank": before,
            "reranked_first_rank": after, "predict_seconds": elapsed,
            "top10": [{"rank": rank + 1, "baseline_rank": index + 1,
                       "fact_id": facts[index]["id"], "document_id": facts[index].get("document_id"),
                       "score_raw": scores[index], "text": facts[index]["text"]}
                      for rank, index in enumerate(order[:10])],
        }
        results.append(result)
        print(json.dumps({key: result[key] for key in ("case", "candidates", "baseline_first_rank", "reranked_first_rank", "predict_seconds")}), flush=True)
    receipt = {
        "scope": "frozen candidate reranking; production recall not yet verified",
        "provider": "onnx", "execution_providers": providers,
        "model_path": str(args.model), "tokenizer_path": str(args.tokenizer),
        "model_artifacts": artifacts,
        "max_candidates": args.max_candidates or None,
        "initialization_seconds": initialization_seconds,
        "threads": 4, "batch_size": 8, "max_concurrent": 1, "max_tokens": 512,
        "versions": {name: importlib.metadata.version(name) for name in ("hindsight-api-slim", "onnxruntime", "transformers")},
        "results": results,
    }
    args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
