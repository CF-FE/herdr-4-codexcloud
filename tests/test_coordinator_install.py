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
    def test_legacy_release_without_tools_still_installs_and_incomplete_tools_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / 'repo'
            repo.mkdir()
            def git(*args):
                return subprocess.check_output(['git', '-c', 'user.name=Install fixture', '-c',
                    'user.email=fixture@example.invalid', '-c', 'commit.gpgsign=false', *args],
                    cwd=repo, text=True).strip()
            git('init', '--quiet')
            for name in installer.CORE_FILES:
                (repo / name).parent.mkdir(parents=True, exist_ok=True)
                (repo / name).write_bytes((ROOT / name).read_bytes())
            git('add', '--', *installer.CORE_FILES)
            git('commit', '--quiet', '-m', 'legacy fixture')
            first = git('rev-parse', 'HEAD')
            dest = Path(tmp) / 'private-app'
            prepared = installer.install(first, dest, repository=repo)
            self.assertNotIn(installer.GOAL_TOOLS, prepared['files'])
            self.assertEqual(installer.install(first, dest, repository=repo), prepared)
            (repo / installer.GOAL_TOOLS).parent.mkdir(parents=True)
            (repo / installer.GOAL_TOOLS).write_bytes((ROOT / installer.GOAL_TOOLS).read_bytes())
            git('add', '--', installer.GOAL_TOOLS)
            git('commit', '--quiet', '-m', 'incomplete fixture')
            second = git('rev-parse', 'HEAD')
            with self.assertRaisesRegex(ValueError, 'complete coordinator release'):
                installer.install(second, dest, repository=repo)
            self.assertFalse((dest / 'releases' / second).exists())

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
                (repo / name).parent.mkdir(parents=True, exist_ok=True)
                (repo / name).write_bytes((ROOT / name).read_bytes())
            git('add', '--', *installer.FILES)
            git('commit', '--quiet', '-m', 'fixture')
            first = git('rev-parse', 'HEAD')
            dest = Path(tmp) / 'private-app'
            prepared = installer.install(first, dest, repository=repo)
            release = Path(prepared['release'])
            self.assertTrue(set(installer.APPROVAL_FILES) <= prepared['files'].keys())
            plist = plistlib.loads((release / 'observer.plist').read_bytes())
            outcome = json.loads(subprocess.check_output(plist['ProgramArguments'], cwd=tmp, timeout=10))
            self.assertIsInstance(outcome, dict)
            notice = subprocess.run([prepared['python'], '-I', '-S', '-B', str(release / installer.CORE_FILES[0]),
                '--state-dir', prepared['state_dir'], 'approval_notice_snapshot', '--input', '-'],
                input=json.dumps({'thread_id': 'bound-thread', 'host_id': 'local', 'project_ids': ['fixture']}),
                cwd=tmp, capture_output=True, text=True, check=True, timeout=10)
            self.assertEqual(json.loads(notice.stdout), {'schema_version': 1, 'notices': [], 'next_after': 0, 'more': False})
            pump = plistlib.loads((release / 'bridge-pump.plist').read_bytes())
            self.assertNotEqual(plist['Label'], pump['Label'])
            config = dest / 'bridge-port.json'
            config.write_text(json.dumps({'v': 1, 'socket_path': str(dest / 'absent.sock'), 'key_id': 'probe-only',
                'key_hex': '00' * 32, 'recipient': 'fixture', 'grants': [], 'pilot': None}))
            config.chmod(0o600)
            observed = json.loads(subprocess.check_output(pump['ProgramArguments'], cwd=tmp, timeout=10))
            self.assertEqual(observed, {'state': 'NO_PILOT'})
            # The stdio tool must be usable from its fixed release, without the
            # checkout, site packages, a configuration or a model invocation.
            help_text = subprocess.check_output([prepared['python'], '-I', '-S', '-B',
                str(release / installer.GOAL_TOOLS), '--help'], cwd=tmp, timeout=10, text=True)
            self.assertIn('--policy', help_text)
            contact_help = subprocess.check_output([prepared['python'], '-I', '-S', '-B',
                str(release / installer.ROLE_CONTACT), '--help'], cwd=tmp, timeout=10, text=True)
            self.assertIn('resolve', contact_help)
            for directory in ('mcp', 'mcp/project-coordinator-tools'):
                self.assertEqual((release / directory).stat().st_mode & 0o777, 0o700)
            state = dest / 'state' / 'project-coordinator.sqlite3'
            self.assertTrue(state.is_file())
            sentinel = dest / 'state' / 'operator-recovery-reference'
            sentinel.write_text('retained-reference')
            (repo / installer.FILES[1]).write_text('# next tested release\n')
            git('add', '--', installer.FILES[1]); git('commit', '--quiet', '-m', 'fixture upgrade')
            second = git('rev-parse', 'HEAD')
            inbox = (Path(tmp) / 'local' / 'github-comments').resolve()
            inbox.mkdir(mode=0o700, parents=True)
            updated = installer.install(second, dest, repository=repo, github_inbox=inbox)
            observer = plistlib.loads((Path(updated['release']) / 'observer.plist').read_bytes())
            self.assertIn(str(inbox), observer['ProgramArguments'])
            subprocess.run(observer['ProgramArguments'], cwd=tmp, capture_output=True, check=True, timeout=10)
            self.assertNotEqual(prepared['release'], updated['release'])
            self.assertEqual(sentinel.read_text(), 'retained-reference')
            self.assertTrue(state.exists())
            self.assertEqual(installer.install(first, dest, repository=repo)['files'], prepared['files'])
            (release / 'mcp/project-coordinator-tools').chmod(0o755)
            with self.assertRaises(ValueError):
                installer.install(first, dest, repository=repo)
            (release / 'mcp/project-coordinator-tools').chmod(0o700)
            (release / installer.FILES[1]).write_text('damaged')
            with self.assertRaises(ValueError):
                installer.install(first, dest, repository=repo)
