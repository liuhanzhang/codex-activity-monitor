import json
import fcntl
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from codex_activity_monitor.app_control import AppController, ControlError, LaunchEnvironment
from codex_activity_monitor.integration import KEYS


class MemoryEnvironment:
    def __init__(self):
        self.values = {}
        self.fail = None

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        if self.fail == key:
            self.fail = None
            raise ControlError('injected_failure', 'injected')
        if value is None:
            self.values.pop(key, None)
        else:
            self.values[key] = value


class FakeServices:
    def __init__(self):
        self.dependencies = []

    def disable_legacy(self, runtime):
        pass

    def backup_legacy(self, runtime):
        return None

    def rollback_legacy(self, saved):
        pass

    def remove_gui_login(self, app):
        pass

    def dependents(self, receipt):
        return self.dependencies


class AppControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cam-app-', dir='/tmp')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.app = self.base / 'Fake App.app'
        self.entry = self.app / 'Contents/MacOS/MonitorApp'
        self.entry.parent.mkdir(parents=True)
        self.entry.write_text('#!/bin/sh\nexit 0\n')
        self.entry.chmod(0o700)
        self.cli = self.base / 'real cli'
        self.cli.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\ncat\nexit 17\n')
        self.cli.chmod(0o700)
        self.environment = MemoryEnvironment()
        self.services = FakeServices()
        self.control = AppController(self.base / 'data', environment=self.environment, services=self.services)
        self.enterContext(patch('codex_activity_monitor.cli.stop'))
        self.enterContext(patch('codex_activity_monitor.cli.running', return_value=False))

    def configure(self, **kwargs):
        return self.control.configure(self.app, self.cli, **kwargs)

    def receipt(self):
        return json.loads(self.control.receipt_path.read_text())

    def test_status_does_not_create_data(self):
        self.assertEqual(self.control.status()['state'], 'new')
        self.assertFalse(self.control.root.exists())

    def test_fresh_gui_installation_and_terminal_reader_share_default_runtime(self):
        from codex_activity_monitor.runtime import default_runtime, runtime_dir
        with patch('pathlib.Path.home', return_value=self.base / 'user'), patch.dict(os.environ):
            os.environ.pop('CODEX_ACTIVITY_MONITOR_RUNTIME', None)
            control = AppController(default_runtime().parent,
                                    environment=self.environment, services=self.services)
            result = control.configure(self.app, self.cli)
            self.assertEqual(Path(result['runtime']), runtime_dir())

    def test_exact_original_empty_unset_and_newline_survive_restore(self):
        originals = {KEYS[0]: '/original\npath\n', KEYS[1]: '', KEYS[2]: '1'}
        self.environment.values = originals.copy()
        self.configure()
        self.assertEqual(self.receipt()['previous'][KEYS[3]], None)
        result = self.control.restore()
        self.assertEqual(self.environment.values, originals)
        self.assertEqual(result['state'], 'restored')
        self.assertEqual(self.control.status()['state'], 'restored')

    def test_repeat_configure_keeps_first_backup(self):
        self.environment.values[KEYS[0]] = '/original'
        self.configure()
        first = self.receipt()['previous']
        self.configure()
        self.assertEqual(self.receipt()['previous'], first)

    def test_user_later_change_is_not_overwritten(self):
        self.configure()
        self.environment.values[KEYS[0]] = '/user-choice'
        self.control.restore()
        self.assertEqual(self.environment.values, {KEYS[0]: '/user-choice'})
        self.assertEqual(self.receipt()['key_results'][KEYS[0]], 'user_change_preserved')

    def test_redownload_does_not_replace_original_backup(self):
        self.environment.values[KEYS[0]] = '/before-monitor'
        self.configure()
        original = self.receipt()['previous']
        self.entry.unlink()
        replacement = self.base / 'Redownloaded.app'
        entry = replacement / 'Contents/MacOS/MonitorApp'
        entry.parent.mkdir(parents=True); entry.write_text('#!/bin/sh\nexit 0\n'); entry.chmod(0o700)
        self.control.configure(replacement, self.cli)
        self.assertEqual(self.receipt()['previous'], original)
        self.assertEqual(self.control.restore()['state'], 'restored')
        self.assertEqual(self.environment.values, {KEYS[0]: '/before-monitor'})

    def test_failed_configure_rolls_back_environment_and_keeps_backup(self):
        self.environment.values[KEYS[0]] = '/original'
        self.environment.fail = KEYS[2]
        with self.assertRaises(ControlError):
            self.configure()
        self.assertEqual(self.environment.values, {KEYS[0]: '/original'})
        self.assertEqual(self.receipt()['previous'][KEYS[0]], '/original')
        self.assertFalse(self.control.enabled.exists())
        self.assertFalse((Path(self.receipt()['runtime'])/'app-managed.json').exists())
        with self.assertRaises(ControlError):
            self.configure()
        self.assertEqual(self.receipt()['previous'][KEYS[0]], '/original')

    def test_failed_update_restores_previously_working_record_and_launcher(self):
        self.configure()
        old_record=self.receipt()
        launcher=self.control.root/'codex-observed'
        old_launcher=launcher.read_bytes()
        self.environment.fail=KEYS[2]
        with self.assertRaises(ControlError):
            self.configure()
        self.assertEqual(self.receipt()['state'],'configured')
        self.assertEqual(self.receipt()['previous'],old_record['previous'])
        self.assertEqual(launcher.read_bytes(),old_launcher)
        self.assertTrue(self.control.enabled.exists())

    def test_restore_failure_is_retryable_without_losing_baseline(self):
        self.configure()
        self.environment.fail = KEYS[1]
        with self.assertRaises(ControlError):
            self.control.restore()
        self.assertIn('previous', self.receipt())
        self.assertEqual(self.receipt()['state'], 'restoring')
        self.assertEqual(self.control.cleanup()['state'], 'restored')
        self.assertEqual(self.environment.values, {})

    def test_pending_cleanup_survives_reopen_and_blocks_reconfigure(self):
        self.configure()
        self.services.dependencies = [{'pid': 123, 'reason': 'codex_running'}]
        self.assertEqual(self.control.restore()['state'], 'pending_cleanup')
        self.assertEqual(self.environment.values, {})
        with self.assertRaises(ControlError):
            self.configure()
        reopened = AppController(self.control.root, environment=self.environment, services=self.services)
        self.assertEqual(reopened.status()['state'], 'pending_cleanup')
        self.services.dependencies = []
        self.assertEqual(reopened.cleanup()['state'], 'restored')

    def test_stale_snapshot_does_not_allow_cleanup_while_collector_lock_is_held(self):
        self.configure()
        path = Path(self.receipt()['runtime']) / 'collector.lock'
        with path.open('w') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ControlError, '仍在退出'):
                self.control.restore(purge=True)
            self.assertTrue(self.control.receipt_path.exists())
            self.assertEqual(self.receipt()['state'], 'restoring')
        self.assertEqual(self.control.cleanup()['state'], 'purged')

    def test_purge_waits_for_dependencies_then_removes_data(self):
        self.configure()
        self.services.dependencies = [{'pid': 123, 'reason': 'codex_running'}]
        result = self.control.restore(purge=True)
        self.assertEqual(result['state'], 'pending_cleanup')
        self.assertTrue(result['purge'])
        self.assertTrue(self.control.root.exists())
        self.services.dependencies = []
        self.assertEqual(self.control.cleanup()['state'], 'purged')
        self.assertFalse(self.control.root.exists())
        self.assertEqual(self.control.status()['state'], 'new')
        self.assertFalse(self.control.root.exists())

    def test_after_normal_restore_new_configure_takes_new_baseline(self):
        self.configure(); self.control.restore()
        self.environment.values[KEYS[0]] = '/changed-after-restore'
        self.configure()
        self.assertEqual(self.receipt()['previous'][KEYS[0]], '/changed-after-restore')

    def test_purge_refuses_unknown_runtime_files_without_deleting_them(self):
        self.configure()
        runtime = Path(self.receipt()['runtime'])
        foreign = runtime / 'keep-this.txt'; foreign.write_text('user data')
        with self.assertRaises(ControlError):
            self.control.restore(purge=True)
        self.assertEqual(foreign.read_text(), 'user data')
        self.assertTrue(self.control.receipt_path.exists())

    def test_unknown_record_version_is_not_overwritten(self):
        self.configure()
        receipt = self.receipt(); receipt['version'] = 99
        self.control.receipt_path.write_text(json.dumps(receipt))
        with self.assertRaises(ControlError):
            self.configure()
        self.assertEqual(self.receipt()['version'], 99)

    def test_legacy_empty_is_not_silently_interpreted(self):
        legacy = self.base / 'legacy'; legacy.mkdir()
        (legacy / 'desktop-environment.json').write_text(json.dumps({
            'previous': dict.fromkeys(KEYS, ''), 'installed': dict.fromkeys(KEYS, '')}))
        with self.assertRaisesRegex(ControlError, '无法区分'):
            self.configure(legacy_runtime=legacy)
        self.assertEqual(self.environment.values, {})
        self.configure(legacy_runtime=legacy, legacy_unset=True)
        self.assertEqual(self.receipt()['previous'], dict.fromkeys(KEYS))
        self.assertEqual(self.receipt()['legacy_backup']['record']['previous'], dict.fromkeys(KEYS, ''))
        self.assertEqual(self.receipt()['legacy_backup']['empty_resolution'], 'user_confirmed_monitor_introduced_unset')
        self.assertTrue((legacy / 'desktop-environment.json').exists())

    def test_second_gui_cannot_steal_or_stop_active_session(self):
        self.configure()
        with patch('codex_activity_monitor.cli.start'):
            first = self.control.resume(os.getpid())
        with patch('codex_activity_monitor.app_control.process_alive', return_value=True):
            with self.assertRaisesRegex(ControlError, '另一个 Codex Activity Monitor'):
                self.control.resume(os.getpid() + 1)
        with self.assertRaises(ControlError):
            self.control.stop(os.getpid() + 1)
        self.assertEqual(json.loads(self.control.session.read_text())['id'], first['session']['id'])

    def test_missing_python_falls_back_without_monitor_trace(self):
        python = self.base / 'python'
        python.write_text('#!/bin/sh\nexit 88\n'); python.chmod(0o700)
        self.configure(python=python)
        python.unlink()
        self.cli.write_text('#!/bin/sh\nprintf "%s|%s" "$RUST_LOG" "$*"\nexit 17\n')
        result = subprocess.run([str(self.control.root / 'codex-observed'), 'one', 'two words'],
                                env={**os.environ, 'RUST_LOG': 'warn'}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 17)
        self.assertEqual(result.stdout, 'warn|one two words')

    def test_missing_app_falls_back_with_args_stdio_and_exit_status(self):
        self.configure()
        self.entry.unlink()
        result = subprocess.run([str(self.control.root / 'codex-observed'), 'app-server', 'argument with spaces'],
                                input='payload\n', capture_output=True, text=True)
        self.assertEqual(result.returncode, 17)
        self.assertEqual(result.stdout, 'app-server\nargument with spaces\npayload\n')
        self.assertEqual(result.stderr, '')

    def test_migrated_old_launcher_has_same_missing_app_fallback(self):
        legacy = self.base / 'legacy'; legacy.mkdir()
        (legacy / 'login-start').write_text('#!/bin/sh\nexit 99\n')
        (legacy / 'desktop-environment.json').write_text(json.dumps({
            'previous': dict.fromkeys(KEYS), 'installed': dict.fromkeys(KEYS, '')}))
        self.configure(legacy_runtime=legacy)
        self.assertEqual(subprocess.run(['/bin/sh',str(legacy / 'login-start')]).returncode, 0)
        self.entry.unlink()
        result = subprocess.run([str(legacy / 'codex-observed'), 'old-path'], input='hello\n', capture_output=True, text=True)
        self.assertEqual(result.returncode, 17)
        self.assertEqual(result.stdout, 'old-path\nhello\n')

    def test_cleanup_can_resume_after_legacy_files_are_already_removed(self):
        legacy = self.base / 'legacy'; legacy.mkdir()
        (legacy / 'desktop-environment.json').write_text(json.dumps({
            'previous': dict.fromkeys(KEYS), 'installed': dict.fromkeys(KEYS, '')}))
        self.configure(legacy_runtime=legacy)
        self.services.dependencies = [{'reason': 'codex_running'}]
        self.assertEqual(self.control.restore()['state'], 'pending_cleanup')
        shutil.rmtree(legacy)
        self.services.dependencies = []
        self.assertEqual(self.control.cleanup()['state'], 'restored')

    def test_disabled_integration_uses_original_without_python(self):
        self.configure()
        self.control.enabled.unlink()
        result = subprocess.run([str(self.control.root / 'codex-observed'), 'arg'],
                                input='', capture_output=True, text=True)
        self.assertEqual(result.returncode, 17)
        self.assertEqual(result.stdout, 'arg\n')

    def test_no_original_cli_is_a_clear_nonrecursive_failure(self):
        self.configure()
        self.cli.unlink()
        result = subprocess.run([str(self.control.root / 'codex-observed')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 127)
        self.assertIn('原版 CLI 不可用', result.stderr)

    def test_resume_requires_configured_state_and_returns_session_identity(self):
        with self.assertRaises(ControlError):
            self.control.resume(os.getpid())
        self.configure()
        with patch('codex_activity_monitor.cli.start') as start:
            result = self.control.resume(os.getpid())
        start.assert_called_once()
        self.assertEqual(result['session']['owner_pid'], os.getpid())
        self.control.stop()
        self.assertFalse(self.control.session.exists())
        self.assertTrue(self.control.enabled.exists())


class LaunchEnvironmentTests(unittest.TestCase):
    def test_raw_output_distinguishes_absent_empty_and_trailing_newlines(self):
        for output, expected in ((b'', None), (b'\n', ''), (b'path\n', 'path'), (b'path\n\n', 'path\n')):
            with self.subTest(output=output), patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, output, b'')):
                self.assertEqual(LaunchEnvironment().get(KEYS[0]), expected)


if __name__ == '__main__':
    unittest.main()
