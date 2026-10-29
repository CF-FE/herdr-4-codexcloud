import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_repository.py"


class RepositoryChecksTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "repository"
        self.root.mkdir()
        for name in ("README.md", "AGENTS.md", "mcp/README.md", "skill/README.md", "plugin/README.md"):
            self.write(name, "# Documentation\n")

    def write(self, name, content):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def run_check(self):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root)],
            cwd=self.root.parent,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def test_empty_collection_passes_and_does_not_claim_runtime_readiness(self):
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("mcp=0 skill=0 plugin=0", result.stdout)
        self.assertIn("仅结构检查，不代表运行可用", result.stdout)

    def test_valid_entries_are_counted_from_an_unrelated_working_directory(self):
        self.write("mcp/example-server/README.md", "# Server\n")
        self.write("skill/review/SKILL.md", "# Review\n")
        self.write("plugin/review-bundle/README.md", "# Bundle\n")
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("mcp=1 skill=1 plugin=1", result.stdout)

    def test_missing_skill_entrypoint_fails_with_its_path(self):
        (self.root / "skill" / "incomplete").mkdir()
        result = self.run_check()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("skill/incomplete/SKILL.md", result.stderr)

    def test_invalid_entry_id_fails_with_its_path(self):
        self.write("mcp/Bad Name/README.md", "# Server\n")
        result = self.run_check()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mcp/Bad Name", result.stderr)

    def test_whitespace_only_documentation_fails(self):
        self.write("plugin/README.md", " \n\t\n")
        result = self.run_check()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("plugin/README.md", result.stderr)

    def test_symlinked_entry_and_entrypoint_are_both_rejected(self):
        outside = self.root.parent / "outside"
        outside.mkdir()
        (outside / "README.md").write_text("# External\n", encoding="utf-8")
        (self.root / "mcp" / "external").symlink_to(outside, target_is_directory=True)
        target = self.root / "plugin" / "external"
        target.mkdir()
        (target / "README.md").symlink_to(outside / "README.md")
        result = self.run_check()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mcp/external", result.stderr)
        self.assertIn("plugin/external/README.md", result.stderr)

    def test_missing_category_and_multiple_missing_files_are_reported_together(self):
        (self.root / "skill" / "README.md").unlink()
        (self.root / "skill").rmdir()
        (self.root / "README.md").unlink()
        (self.root / "AGENTS.md").unlink()
        result = self.run_check()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("skill", result.stderr)
        self.assertIn("README.md", result.stderr)
        self.assertIn("AGENTS.md", result.stderr)

    def test_symlinked_category_is_rejected_without_following_it(self):
        (self.root / "mcp" / "README.md").unlink()
        (self.root / "mcp").rmdir()
        outside = self.root.parent / "outside"
        outside.mkdir()
        (self.root / "mcp").symlink_to(outside, target_is_directory=True)
        result = self.run_check()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mcp", result.stderr)
        self.assertIn("符号链接", result.stderr)


if __name__ == "__main__":
    unittest.main()
