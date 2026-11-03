"""Cleanup failures and cancellation use disposable files and fake services only."""

import fcntl
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from codex_activity_monitor.app_control import AppController, ControlError
from codex_activity_monitor.diagnostic_log import DiagnosticLog
from codex_activity_monitor.integration import KEYS
import test_app_control as fixtures


class CleanupRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AppControlTests('test_status_does_not_create_data')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.control = self.fixture.control
        self.services = self.fixture.services
        self.environment = self.fixture.environment

    def pending_purge(self):
        self.fixture.configure()
        self.services.dependencies = [{'pid': 123, 'reason': 'codex_running'}]
        return self.control.restore(purge=True)

    def metadata_path(self, location):
        directory = self.control.root / location
        directory.mkdir(parents=True, exist_ok=True)
        if location == 'diagnostics':
            log = DiagnosticLog(directory, 'management')
            log.emit('configure_completed')
            self.assertTrue(log.close(2))
        return directory / '.DS_Store'

    def test_finder_metadata_does_not_block_purge_in_any_owned_directory(self):
        for location in ('.', 'diagnostics', 'timeline', 'run', 'run/bridges'):
            with self.subTest(location=location):
                self.fixture.configure()
                self.metadata_path(location).write_bytes(b'synthetic Finder metadata')
                self.assertEqual(self.control.restore(purge=True)['state'], 'purged')
                self.assertFalse(self.control.root.exists())

    def test_metadata_name_does_not_allow_directories_or_links(self):
        outside = self.fixture.base / 'keep.txt'
        outside.write_text('preserve this data')
        for location in ('.', 'diagnostics', 'timeline', 'run', 'run/bridges'):
            for kind in ('directory', 'link', 'dangling_link'):
                with self.subTest(location=location, kind=kind):
                    self.fixture.configure()
                    path = self.metadata_path(location)
                    if kind == 'directory':
                        path.mkdir()
                        (path / 'keep.txt').write_text('preserve directory contents')
                    else:
                        path.symlink_to(outside if kind == 'link' else outside.with_name('missing'))
                    with self.assertRaises(ControlError) as failure:
                        self.control.restore(purge=True)
                    self.assertEqual(failure.exception.code, 'unowned_data')
                    self.assertEqual(outside.read_text(), 'preserve this data')
                    if kind == 'directory':
                        self.assertEqual((path / 'keep.txt').read_text(), 'preserve directory contents')
                        (path / 'keep.txt').unlink()
                        path.rmdir()
                    else:
                        self.assertTrue(path.is_symlink())
                        path.unlink()
                    self.assertEqual(self.control.cleanup()['state'], 'purged')

    def test_cleanup_failure_survives_reopen_and_explicit_retry(self):
        self.fixture.configure()
        foreign = self.control.root / '.user-file'
        foreign.write_text('keep')
        with self.assertRaises(ControlError):
            self.control.restore(purge=True)
        reopened = AppController(self.control.root, environment=self.environment, services=self.services)
        status = reopened.status()
        self.assertEqual(status['state'], 'pending_cleanup')
        self.assertEqual(status['pending'], [])
        self.assertEqual(status['cleanup_error']['code'], 'unowned_data')
        self.assertEqual(status['cleanup_error']['display_message']['key'], 'control.unowned_data')
        self.assertTrue(status['can_cancel_cleanup'])
        self.assertEqual(foreign.read_text(), 'keep')
        foreign.unlink()
        self.assertEqual(reopened.cleanup()['state'], 'purged')

    def test_cancel_keeps_data_and_allows_new_configuration_with_current_baseline(self):
        self.pending_purge()
        foreign = self.control.root / 'keep.txt'
        foreign.write_text('keep')
        result = self.control.cancel_cleanup()
        self.assertEqual(result['state'], 'restored')
        self.assertFalse(result['purge'])
        self.assertFalse(result['can_cancel_cleanup'])
        self.assertEqual(self.environment.values, {})
        self.assertEqual(foreign.read_text(), 'keep')
        self.assertEqual(self.control.cleanup()['state'], 'restored')
        self.assertEqual(self.control.cancel_cleanup()['state'], 'restored')
        self.environment.values[KEYS[0]] = '/new-user-cli'
        with patch('codex_activity_monitor.app_session.process_identity', return_value='current-host'):
            self.fixture.configure()
        receipt = self.fixture.receipt()
        self.assertEqual(receipt['previous'][KEYS[0]], '/new-user-cli')
        self.assertEqual(receipt['restart_processes'], [{'pid': 123, 'identity': 'current-host'}])
        self.assertEqual(foreign.read_text(), 'keep')

    def test_cancel_after_blocked_purge_clears_persisted_failure(self):
        self.fixture.configure()
        foreign = self.control.root / 'keep.txt'
        foreign.write_text('keep')
        with self.assertRaises(ControlError):
            self.control.restore(purge=True)
        result = self.control.cancel_cleanup()
        self.assertIsNone(result['cleanup_error'])
        self.assertEqual(result['state'], 'restored')
        self.assertNotIn('cleanup_error', self.fixture.receipt())
        self.assertEqual(foreign.read_text(), 'keep')

    def test_cancel_cannot_bypass_incomplete_restore_or_enabled_integration(self):
        self.fixture.configure()
        with self.assertRaises(ControlError):
            self.control.cancel_cleanup()
        self.assertTrue(self.control.enabled.exists())
        self.environment.fail = KEYS[1]
        with self.assertRaises(ControlError):
            self.control.restore(purge=True)
        self.assertEqual(self.fixture.receipt()['state'], 'restoring')
        with self.assertRaises(ControlError) as failure:
            self.control.cancel_cleanup()
        self.assertEqual(failure.exception.code, 'cancel_cleanup_unavailable')
        self.assertTrue(self.fixture.receipt()['purge'])

    def test_cancel_requires_collector_to_have_finished(self):
        self.pending_purge()
        with (Path(self.fixture.receipt()['runtime']) / 'collector.lock').open('w') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(ControlError):
                self.control.cancel_cleanup()
            self.assertTrue(self.fixture.receipt()['purge'])
            self.assertEqual(self.fixture.receipt()['state'], 'pending_cleanup')
        self.assertEqual(self.control.cancel_cleanup()['state'], 'restored')

    def test_cancel_does_not_create_or_adopt_a_data_directory(self):
        self.assertEqual(self.control.cancel_cleanup()['state'], 'purged')
        self.assertFalse(self.control.root.exists())
        self.control.root.mkdir()
        (self.control.root / 'management.lock').touch()
        with self.assertRaises(ControlError):
            self.control.cancel_cleanup()
        self.assertEqual([p.name for p in self.control.root.iterdir()], ['management.lock'])

    def test_cancel_racing_a_completed_purge_never_recreates_data(self):
        self.pending_purge()
        entered = threading.Event()
        release = threading.Event()
        results, errors = {}, []

        def dependencies(receipt):
            entered.set()
            if not release.wait(3):
                raise RuntimeError('test cleanup did not release')
            return []

        def invoke(action):
            try:
                results[action] = getattr(self.control, action)()
            except Exception as error:
                errors.append(error)

        self.services.dependents = dependencies
        cleanup = threading.Thread(target=invoke, args=('cleanup',))
        cleanup.start()
        self.assertTrue(entered.wait(2))
        cancel = threading.Thread(target=invoke, args=('cancel_cleanup',))
        cancel.start()
        release.set()
        cleanup.join(3)
        cancel.join(3)
        self.assertFalse(cleanup.is_alive() or cancel.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results['cleanup']['state'], 'purged')
        self.assertEqual(results['cancel_cleanup']['state'], 'purged')
        self.assertFalse(self.control.root.exists())

    def test_cleanup_waiting_for_cancellation_keeps_data(self):
        self.pending_purge()
        foreign = self.control.root / 'keep.txt'
        foreign.write_text('keep')
        entered, release = threading.Event(), threading.Event()
        results, errors = {}, []
        stop = self.control._stop

        def stop_before_cancellation(receipt):
            entered.set()
            if not release.wait(3):
                raise RuntimeError('test cancellation did not release')
            stop(receipt)

        def invoke(action):
            try:
                results[action] = getattr(self.control, action)()
            except Exception as error:
                errors.append(error)

        self.control._stop = stop_before_cancellation
        cancel = threading.Thread(target=invoke, args=('cancel_cleanup',))
        cancel.start()
        self.assertTrue(entered.wait(2))
        cleanup = threading.Thread(target=invoke, args=('cleanup',))
        cleanup.start()
        release.set()
        cancel.join(3)
        cleanup.join(3)
        self.assertFalse(cleanup.is_alive() or cancel.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results['cancel_cleanup']['state'], 'restored')
        self.assertEqual(results['cleanup']['state'], 'restored')
        self.assertEqual(foreign.read_text(), 'keep')


if __name__ == '__main__':
    unittest.main()
