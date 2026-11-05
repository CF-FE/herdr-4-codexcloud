"""Outcome checks for deployment preservation and Q8 artifact integrity."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "mcp/hindsight-memory/reranker/configure_cpu.py"


class RerankerConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "config").mkdir()
        self.config = self.root / "config/hindsight.env"
        self.original = b"KEEP_THIS=unchanged\r\nHINDSIGHT_API_RERANKER_PROVIDER=rrf\r\n"
        self.config.write_bytes(self.original)
        self.model = self.root / "models/bge-reranker-v2-m3-e099d4b9-q8"
        (self.model / "int8").mkdir(parents=True)
        artifacts = []
        snapshots = []
        for name in ("model_q8_avx2.onnx", "model_q8_avx2.onnx.data", "tokenizer.json"):
            path = self.model / "int8" / name
            path.write_bytes(("fixture:" + name).encode())
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            artifacts.append({"path": "int8/" + name, "bytes": path.stat().st_size, "sha256": digest})
            snapshots.append({"path": str(path), "bytes": path.stat().st_size, "sha256": digest})
        conversion = {
            "source": {"registry": "ModelScope", "revision": "e099d4b9cdbd291b1569d416f19aaa6523570bc3"},
            "artifacts": artifacts,
            "graphs": {"int8": {"path": "int8/model_q8_avx2.onnx", "quantized_weight_dtype_counts": {"UINT8": 1}}},
        }
        (self.model / "conversion-receipt.json").write_text(json.dumps(conversion))
        folder = self.root / "tmp/rerank-evaluation-20261105"
        folder.mkdir(parents=True)
        self.benchmark = folder / "benchmark-receipt.json"
        self.benchmark.write_text(json.dumps({
            "model_path": str(self.model / "int8/model_q8_avx2.onnx"),
            "tokenizer_path": str(self.model / "int8"),
            "execution_providers": ["CPUExecutionProvider"], "model_artifacts": snapshots,
            "results": [{"baseline_first_rank": 5, "reranked_first_rank": 3}],
        }))

    def run_config(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), "--root", str(self.root), *args], capture_output=True, text=True)

    def test_configuration_preserves_values_and_rollback_refuses_later_edits(self):
        result = self.run_config()
        self.assertEqual(result.returncode, 0, result.stderr)
        deployed = self.config.read_bytes()
        self.assertIn(b"KEEP_THIS=unchanged", deployed)
        self.assertEqual(deployed.count(b"HINDSIGHT_API_RERANKER_PROVIDER="), 1)
        receipt_path = json.loads(result.stdout)["receipt"]
        concurrent = deployed + b"NEW_SETTING=preserve-this-too\n"
        self.config.write_bytes(concurrent)
        rejected = self.run_config("--rollback", receipt_path)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual(self.config.read_bytes(), concurrent)
        self.config.write_bytes(deployed)
        restored = self.run_config("--rollback", receipt_path)
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(self.config.read_bytes(), self.original)

    def test_modified_external_weights_are_rejected_before_configuration_changes(self):
        (self.model / "int8/model_q8_avx2.onnx.data").write_bytes(b"changed after conversion")
        result = self.run_config()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertFalse((self.root / "evidence").exists())

    def test_benchmark_must_bind_the_tokenizer_and_artifact_bytes(self):
        receipt = json.loads(self.benchmark.read_text())
        receipt["model_artifacts"] = receipt["model_artifacts"][:-1]
        self.benchmark.write_text(json.dumps(receipt))
        result = self.run_config()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config.read_bytes(), self.original)


if __name__ == "__main__":
    unittest.main()
