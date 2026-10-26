import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from codex_activity_monitor.app_control import ControlError
from codex_activity_monitor.integration import KEYS
from codex_activity_monitor.runtime import ROOT
from codex_activity_monitor.test_profile import FileEnvironment, controller


class TestProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cam-profile-', dir='/tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_explicit_marker_and_separate_data_root_are_required(self):
        with self.assertRaises(ControlError):
            controller(self.root)
        (self.root / 'profile.json').write_text(json.dumps({'kind': 'codex-activity-monitor-disposable-test'}))
        control, cli = controller(self.root)
        self.assertEqual(control.root, self.root / 'data')
        self.assertEqual(cli, self.root / 'original-cli')
        (self.root / 'data').symlink_to(self.root / 'elsewhere')
        with self.assertRaises(ControlError):
            controller(self.root)

    def test_environment_roundtrips_across_processes_without_launchctl(self):
        env = FileEnvironment(self.root / 'environment.json')
        env.set(KEYS[0], '')
        env.set(KEYS[1], 'original\n')
        self.assertEqual(FileEnvironment(env.path).get(KEYS[0]), '')
        self.assertIsNone(env.get(KEYS[3]))
        env.set(KEYS[0], None)
        self.assertIsNone(env.get(KEYS[0]))

    def test_cli_profile_never_imports_actual_legacy_runtime(self):
        (self.root / 'profile.json').write_text(json.dumps({'kind': 'codex-activity-monitor-disposable-test'}))
        result = subprocess.run([sys.executable, '-m', 'codex_activity_monitor.cli', 'app', 'status',
                                 '--test-profile', str(self.root), '--legacy-runtime', str(ROOT / '.local/runtime')],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['data_root'], str(self.root / 'data'))
        self.assertFalse((self.root / 'data').exists())

    def test_old_cli_install_commands_refuse_gui_managed_runtime(self):
        runtime = self.root / 'managed'; runtime.mkdir()
        (runtime / 'app-managed.json').write_text('{}')
        for action in ('setup', 'uninstall'):
            result = subprocess.run([sys.executable, '-m', 'codex_activity_monitor.cli', '--runtime', str(runtime), action],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn('GUI 管理', result.stderr)
            self.assertTrue((runtime / 'app-managed.json').exists())


if __name__ == '__main__':
    unittest.main()
