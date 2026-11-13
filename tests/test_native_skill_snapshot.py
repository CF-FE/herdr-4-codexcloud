import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SOURCE = Path("skill/organization-collaboration")
SNAPSHOT = Path(".agents/skills/organization-collaboration")


class NativeSkillSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "repository"
        for name in ("README.md", "AGENTS.md", "mcp/README.md", "skill/README.md", "plugin/README.md"):
            self.write(Path(name), b"# Documentation\n")
        self.write(SOURCE / "SKILL.md", b"# Organization\n")
        self.write(SOURCE / "references/nested/policy.md", b"Policy\r\n")
        self.write(SOURCE / "assets/example.bin", b"\x00\xff\x01")
        shutil.copytree(self.root / SOURCE, self.root / SNAPSHOT)

    def write(self, path, content):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target

    def run_script(self, script):
        return subprocess.run(
            [sys.executable, str(SCRIPTS / script), "--root", str(self.root)],
            cwd=self.root.parent, capture_output=True, text=True, encoding="utf-8",
        )

    def test_complete_snapshot_passes_from_an_unrelated_working_directory(self):
        result = self.run_script("check_repository.py")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_byte_drift_is_reported_without_repairing_it(self):
        target = self.write(SNAPSHOT / "assets/example.bin", b"\x00\xff\x02")
        result = self.run_script("check_repository.py")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(SNAPSHOT / "assets/example.bin"), result.stderr)
        self.assertIn("字节", result.stderr)
        self.assertEqual(target.read_bytes(), b"\x00\xff\x02")

    def test_missing_and_extra_resources_are_reported_together_without_repair(self):
        missing = self.root / SNAPSHOT / "references/nested/policy.md"
        missing.unlink()
        extra = self.write(SNAPSHOT / "references/obsolete.md", b"Old policy\n")
        result = self.run_script("check_repository.py")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(SNAPSHOT / "references/nested/policy.md"), result.stderr)
        self.assertIn(str(SNAPSHOT / "references/obsolete.md"), result.stderr)
        self.assertFalse(missing.exists())
        self.assertTrue(extra.exists())

    def test_missing_snapshot_or_source_is_rejected(self):
        shutil.rmtree(self.root / SNAPSHOT)
        result = self.run_script("check_repository.py")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(SNAPSHOT), result.stderr)
        shutil.rmtree(self.root / SOURCE)
        result = self.run_script("check_repository.py")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(SOURCE), result.stderr)

    def test_sync_replaces_only_the_fixed_snapshot_and_is_repeatable(self):
        self.write(SOURCE / "references/new.md", b"New policy\n")
        (self.root / SOURCE / "assets/example.bin").unlink()
        self.write(SNAPSHOT / "SKILL.md", b"Stale\n")
        self.write(SNAPSHOT / "obsolete.md", b"Old\n")
        sibling = self.write(Path(".agents/skills/other/SKILL.md"), b"Other\n")
        alias = self.root / "skills"
        alias.symlink_to("skill", target_is_directory=True)
        for _ in range(2):
            result = self.run_script("sync_native_skill.py")
            self.assertEqual(result.returncode, 0, result.stderr)
            result = self.run_script("check_repository.py")
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / SNAPSHOT / "obsolete.md").exists())
        self.assertFalse((self.root / SNAPSHOT / "assets/example.bin").exists())
        self.assertEqual(sibling.read_bytes(), b"Other\n")
        self.assertTrue(alias.is_symlink())

    def test_symlinked_snapshot_or_parent_is_rejected_without_external_writes(self):
        for relative in (SNAPSHOT, Path(".agents/skills"), Path(".agents")):
            with self.subTest(path=relative):
                target = self.root / relative
                outside = self.root.parent / "outside"
                target.rename(outside)
                target.symlink_to(outside, target_is_directory=True)
                try:
                    for script in ("check_repository.py", "sync_native_skill.py"):
                        result = self.run_script(script)
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn("符号链接", result.stderr)
                    self.assertTrue(target.is_symlink())
                finally:
                    target.unlink()
                    outside.rename(target)

    def test_symlinked_source_resource_does_not_replace_existing_snapshot(self):
        outside = self.root.parent / "outside.md"
        outside.write_bytes(b"External\n")
        (self.root / SOURCE / "references/external.md").symlink_to(outside)
        for script in ("check_repository.py", "sync_native_skill.py"):
            result = self.run_script(script)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("符号链接", result.stderr)
        self.assertFalse((self.root / SNAPSHOT / "references/external.md").exists())
        self.assertEqual((self.root / SNAPSHOT / "SKILL.md").read_bytes(), b"# Organization\n")

    def test_unreadable_source_directory_fails_without_deleting_snapshot_resources(self):
        references = self.root / SOURCE / "references"
        references.chmod(0)
        try:
            try:
                list(references.iterdir())
            except PermissionError:
                pass
            else:
                self.skipTest("host permits reading directories without permission bits")
            for script in ("check_repository.py", "sync_native_skill.py"):
                result = self.run_script(script)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Permission denied", result.stderr)
            self.assertEqual(
                (self.root / SNAPSHOT / "references/nested/policy.md").read_bytes(),
                b"Policy\r\n",
            )
        finally:
            references.chmod(0o755)


if __name__ == "__main__":
    unittest.main()
