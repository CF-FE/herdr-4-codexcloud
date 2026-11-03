import importlib.util
import json
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('install_pc', ROOT / 'scripts/install_project_coordinator.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class CoordinatorInstallation(unittest.TestCase):
    def test_fixed_release_and_timer_preserve_state_across_upgrade_and_reject_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / 'repo'
            repo.mkdir()
            def git(*args):
                return subprocess.check_output(['git', '-c', 'user.name=Install fixture', '-c',
                    'user.email=fixture@example.invalid', '-c', 'commit.gpgsign=false', *args],
                    cwd=repo, text=True).strip()
            git('init', '--quiet')
            (repo / 'scripts').mkdir()
            for name in installer.FILES:
                (repo / name).write_bytes((ROOT / name).read_bytes())
            git('add', '--', *installer.FILES)
            git('commit', '--quiet', '-m', 'fixture')
            first = git('rev-parse', 'HEAD')
            dest = Path(tmp) / 'private-app'
            prepared = installer.install(first, dest, repository=repo)
            release = Path(prepared['release'])
            plist = plistlib.loads((release / 'observer.plist').read_bytes())
            outcome = json.loads(subprocess.check_output(plist['ProgramArguments'], cwd=tmp, timeout=10))
            self.assertIsInstance(outcome, dict)
            pump = plistlib.loads((release / 'bridge-pump.plist').read_bytes())
            self.assertNotEqual(plist['Label'], pump['Label'])
            config = dest / 'bridge-port.json'
            config.write_text(json.dumps({'v': 1, 'socket_path': str(dest / 'absent.sock'), 'key_id': 'probe-only',
                'key_hex': '00' * 32, 'recipient': 'fixture', 'grants': [], 'pilot': None}))
            config.chmod(0o600)
            observed = json.loads(subprocess.check_output(pump['ProgramArguments'], cwd=tmp, timeout=10))
            self.assertEqual(observed, {'state': 'NO_PILOT'})
            state = dest / 'state' / 'project-coordinator.sqlite3'
            self.assertTrue(state.is_file())
            sentinel = dest / 'state' / 'operator-recovery-reference'
            sentinel.write_text('retained-reference')
            (repo / installer.FILES[1]).write_text('# next tested release\n')
            git('add', '--', installer.FILES[1]); git('commit', '--quiet', '-m', 'fixture upgrade')
            second = git('rev-parse', 'HEAD')
            updated = installer.install(second, dest, repository=repo)
            self.assertNotEqual(prepared['release'], updated['release'])
            self.assertEqual(sentinel.read_text(), 'retained-reference')
            self.assertTrue(state.exists())
            self.assertEqual(installer.install(first, dest, repository=repo)['files'], prepared['files'])
            (release / installer.FILES[1]).write_text('damaged')
            with self.assertRaises(ValueError):
                installer.install(first, dest, repository=repo)
