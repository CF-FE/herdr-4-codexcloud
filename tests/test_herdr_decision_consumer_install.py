import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from install_herdr_decision_consumer import FILES, ROLE_RESOURCES, install


@unittest.skipUnless(shutil.which("git"), "Git is required for a fixed revision installation")
class DecisionConsumerInstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.destination = self.root / "installed"
        shutil.copytree(ROOT / ROLE_RESOURCES, self.source / ROLE_RESOURCES)
        for name in FILES:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, path)
        self.git("init", "-q")

    def git(self, *arguments):
        return subprocess.check_output(["git", *arguments], cwd=self.source, stderr=subprocess.STDOUT)

    def commit(self):
        self.git("add", "--all")
        self.git("-c", "user.name=Release Test", "-c", "user.email=test@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "Fixed fixture")
        return self.git("rev-parse", "HEAD").decode().strip()

    def test_installs_only_fixed_git_objects_with_closed_resources_and_receipt(self):
        unrelated = self.source / "local-runner-state.json"
        unrelated.write_text("{}")
        revision = self.commit()
        policy = "config/allowlists/agent-models.json"
        (self.source / policy).write_text("dirty worktree content")

        result = install(revision, self.destination, repository=self.source)
        receipt = json.loads((self.destination / "install-receipt.json").read_bytes())
        self.assertEqual(result, {"destination": str(self.destination.resolve()), **receipt})
        self.assertEqual(receipt["source_commit"], revision)
        files = {path.relative_to(self.destination).as_posix() for path in self.destination.rglob("*")
                 if path.is_file()}
        self.assertEqual(files, set(receipt["files"]) | {"install-receipt.json"})
        self.assertEqual((self.destination / "app" / policy).read_bytes(),
                         self.git("show", f"{revision}:{policy}"))
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o700)
        for name, digest in receipt["files"].items():
            path = self.destination / name
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("app/local-runner-state.json", files)

    def test_does_not_overwrite_existing_destination(self):
        revision = self.commit()
        self.destination.mkdir()
        state = self.destination / "state.json"
        state.write_text("original")

        with self.assertRaisesRegex(ValueError, "Destination already exists"):
            install(revision, self.destination, repository=self.source)

        self.assertEqual(state.read_text(), "original")
        self.assertEqual(set(self.destination.iterdir()), {state})

    def test_missing_role_resource_fails_before_publication(self):
        (self.source / ROLE_RESOURCES / "common.md").unlink()
        revision = self.commit()

        with self.assertRaises(subprocess.CalledProcessError) as error:
            install(revision, self.destination, repository=self.source)

        self.assertIn(b"Missing resource: common.md", error.exception.output)
        self.assertFalse(self.destination.exists())

    def test_swapped_valid_policy_scenarios_fail_before_publication(self):
        policies = self.source / "config/allowlists"
        agent, image = policies / "agent-models.json", policies / "image-models.json"
        agent_bytes, image_bytes = agent.read_bytes(), image.read_bytes()
        agent.write_bytes(image_bytes)
        image.write_bytes(agent_bytes)
        revision = self.commit()

        with self.assertRaisesRegex(ValueError, "Policy identity does not match its filename"):
            install(revision, self.destination, repository=self.source)

        self.assertFalse(self.destination.exists())


if __name__ == "__main__":
    unittest.main()
