"""Configure the verified local Q8 reranker; restart stays an explicit host action."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(r"D:\Hindsight"))
    parser.add_argument("--benchmark", type=Path, help="Artifact-bound CPU benchmark receipt")
    parser.add_argument("--rollback", type=Path, help="Restore this receipt only if the configuration has not changed since")
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    config = root / "config/hindsight.env"
    current = config.read_bytes()
    current_sha = hashlib.sha256(current).hexdigest()
    if args.rollback:
        receipt = json.loads(args.rollback.read_text(encoding="utf-8"))
        if current_sha != receipt["after_sha256"]:
            raise RuntimeError("Configuration changed after deployment; reconcile it before rolling back")
        original = Path(receipt["backup_path"]).read_bytes()
        if hashlib.sha256(original).hexdigest() != receipt["before_sha256"]:
            raise RuntimeError("Configuration backup does not match its receipt")
        if hashlib.sha256(config.read_bytes()).hexdigest() != current_sha:
            raise RuntimeError("Configuration changed during rollback validation; reconcile it before retrying")
        config.write_bytes(original)
        print(json.dumps({"state": "CONFIG_RESTORED_RESTART_REQUIRED", "config_sha256": receipt["before_sha256"]}))
        return

    model = root / "models/bge-reranker-v2-m3-e099d4b9-q8"
    conversion_path = model / "conversion-receipt.json"
    conversion = json.loads(conversion_path.read_text(encoding="utf-8"))
    if conversion["source"]["registry"] != "ModelScope" or conversion["source"]["revision"] != "e099d4b9cdbd291b1569d416f19aaa6523570bc3":
        raise RuntimeError("Expected the pinned ModelScope conversion")
    checked = []
    for artifact in conversion["artifacts"]:
        if artifact["path"].startswith("int8/"):
            path = model / artifact["path"]
            hasher = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                    hasher.update(chunk)
            digest = hasher.hexdigest()
            if path.stat().st_size != artifact["bytes"] or digest != artifact["sha256"]:
                raise RuntimeError(f"Converted artifact no longer matches its receipt: {artifact['path']}")
            checked.append(artifact)
    graph = model / conversion["graphs"]["int8"]["path"]
    if not checked or not conversion["graphs"]["int8"]["quantized_weight_dtype_counts"]:
        raise RuntimeError("No verified Q8 model was found")
    benchmark_path = args.benchmark or root / "tmp/rerank-evaluation-20261105/benchmark-receipt.json"
    benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
    if (Path(benchmark["model_path"]).resolve() != graph.resolve()
            or Path(benchmark["tokenizer_path"]).resolve() != (model / "int8").resolve()
            or benchmark["execution_providers"] != ["CPUExecutionProvider"]):
        raise RuntimeError("The benchmark did not use this CPU model")
    snapshots = {str(Path(artifact["path"]).resolve()): artifact for artifact in benchmark.get("model_artifacts", [])}
    for artifact in checked:
        measured = snapshots.get(str((model / artifact["path"]).resolve()))
        if measured is None or measured["sha256"] != artifact["sha256"] or measured["bytes"] != artifact["bytes"]:
            raise RuntimeError("The benchmark did not bind all graph, external-weight and tokenizer bytes")
    if not benchmark["results"] or any(result["reranked_first_rank"] is None or result["reranked_first_rank"] > result["baseline_first_rank"] for result in benchmark["results"]):
        raise RuntimeError("The frozen-query benchmark did not preserve or improve the expected ranks")

    settings = {
        "HINDSIGHT_API_RERANKER_PROVIDER": "onnx",
        "HINDSIGHT_API_RERANKER_ONNX_MODEL_PATH": str(graph),
        "HINDSIGHT_API_RERANKER_ONNX_TOKENIZER_NAME_OR_PATH": str(model / "int8"),
        "HINDSIGHT_API_RERANKER_ONNX_THREADS": "4",
        "HINDSIGHT_API_RERANKER_ONNX_BATCH_SIZE": "8",
        "HINDSIGHT_API_RERANKER_ONNX_MAX_CONCURRENT": "1",
        "HINDSIGHT_API_RERANKER_ONNX_CPU_MEM_ARENA": "false",
        "HINDSIGHT_API_RERANKER_MAX_CANDIDATES": "100",
    }
    lines = current.decode("utf-8-sig").splitlines()
    retained = [line for line in lines if line.split("=", 1)[0].strip() not in settings]
    updated = ("\n".join(retained).rstrip() + "\n\n# Verified ModelScope BGE Q8 CPU reranker\n" + "\n".join(f"{key}={value}" for key, value in settings.items()) + "\n").encode("utf-8")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    evidence = root / "evidence" / ("reranker-cpu-" + stamp)
    evidence.mkdir(parents=True)
    backup = evidence / "hindsight.env.before"
    backup.write_bytes(current)
    receipt = {
        "state": "CONFIGURED_RESTART_REQUIRED", "before_sha256": current_sha,
        "after_sha256": hashlib.sha256(updated).hexdigest(), "backup_path": str(backup),
        "settings": settings, "model_artifacts": checked,
        "conversion_receipt_sha256": hashlib.sha256(conversion_path.read_bytes()).hexdigest(),
        "benchmark_receipt_sha256": hashlib.sha256(benchmark_path.read_bytes()).hexdigest(),
    }
    if hashlib.sha256(config.read_bytes()).hexdigest() != current_sha:
        raise RuntimeError("Configuration changed during model validation; reconcile it before retrying")
    config.write_bytes(updated)
    path = evidence / "deployment-receipt.json"
    path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps({"state": receipt["state"], "receipt": str(path), "config_sha256": receipt["after_sha256"], "model_files_verified": len(checked)}))


if __name__ == "__main__":
    main()
