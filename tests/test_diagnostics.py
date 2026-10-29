import json
import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from codex_activity_monitor.diagnostic_log import DiagnosticLog, owned_logs
from codex_activity_monitor.diagnostic_health import HealthMonitor
from codex_activity_monitor.engine import Engine
from codex_activity_monitor.integration import KEYS
from codex_activity_monitor.integration_status import fingerprint, startup_evidence, REVISION_ENV, EXPECTED_ENV
import test_app_control as controls


class DiagnosticLogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/tmp', prefix='cam-diag-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'diagnostics'

    def test_event_allowlist_never_serializes_private_context(self):
        log = DiagnosticLog(self.directory, 'management', managed=False)
        log.emit('management_failed', level='error', params={'errno': 13, 'type': 'PermissionError',
                 'reason': 'entry_missing', 'secret': 'PRIVATE_SECRET', 'text': 'PRIVATE_CONTENT'})
        self.assertTrue(log.close(2))
        text = (self.directory / 'management.jsonl').read_text()
        self.assertNotIn('PRIVATE', text)
        self.assertEqual(json.loads(text)['params'], {'errno': 13, 'type': 'PermissionError', 'reason': 'entry_missing'})
        self.assertTrue(owned_logs(self.directory))

    def test_blocked_disk_does_not_block_enqueue_and_queue_is_bounded(self):
        log = DiagnosticLog(self.directory, 'collector', managed=False, queue_items=8, queue_bytes=4096)
        gate, entered = threading.Event(), threading.Event()
        def blocked(frame):
            entered.set(); gate.wait(3)
        with patch.object(log, '_append', side_effect=blocked):
            log.emit('collector_started')
            self.assertTrue(entered.wait(1))
            started = time.monotonic()
            for index in range(1000):
                log.emit('collector_started', params={'attempt': index})
            self.assertLess(time.monotonic()-started, .3)
            stats = log.diagnostics()
            self.assertLessEqual(stats['pending'], 8)
            self.assertLessEqual(stats['pending_bytes'], 4096)
            self.assertGreater(stats['dropped'], 0)
            self.assertFalse(log.close(.01))
            gate.set()
            log.worker.join(1)

    def test_unowned_or_symlinked_directory_is_never_adopted(self):
        self.directory.mkdir()
        (self.directory / 'foreign').write_text('keep')
        log = DiagnosticLog(self.directory, 'gui', managed=True)
        log.emit('gui_started')
        log.close(.1)
        log.worker.join(1)
        self.assertEqual(list(self.directory.iterdir()), [self.directory / 'foreign'])
        self.assertGreater(log.diagnostics()['write_failures'], 0)
        other = Path(self.temp.name) / 'other'; other.mkdir()
        link = Path(self.temp.name) / 'link'; link.symlink_to(other)
        log = DiagnosticLog(link, 'gui', managed=False)
        log.emit('gui_started'); log.close(.1)
        log.worker.join(1)
        self.assertFalse(any(other.iterdir()))

    def test_concurrent_process_rotation_produces_only_complete_bounded_frames(self):
        script = """
import sys,time
from codex_activity_monitor.diagnostic_log import DiagnosticLog
log=DiagnosticLog(sys.argv[1],'bridge',managed=False,max_bytes=1600)
for i in range(20): log.emit('bridge_started',params={'attempt':i})
log.close(3)
"""
        children = [subprocess.Popen([sys.executable, '-B', '-c', script, str(self.directory)]) for _ in range(3)]
        for child in children:
            self.assertEqual(child.wait(timeout=5), 0)
        files = list(self.directory.glob('bridge.jsonl*'))
        self.assertLessEqual(len(files), 4)
        events = []
        for path in files:
            self.assertLessEqual(path.stat().st_size, 1600)
            events += [json.loads(line) for line in path.read_text().splitlines()]
        self.assertTrue(events)
        self.assertEqual(len({e['event_id'] for e in events}), len(events))

    def test_duplicate_errors_keep_first_occurrence_and_coalesce_remaining_count(self):
        log = DiagnosticLog(self.directory, 'collector', managed=False)
        for _ in range(12):
            log.emit('metadata_write_failed', level='error', component='metadata')
        self.assertTrue(log.close(2))
        events = [json.loads(line) for line in (self.directory / 'collector.jsonl').read_text().splitlines()]
        self.assertEqual(len(events), 2)
        self.assertEqual(sum(e['count'] for e in events), 12)
        self.assertEqual(events[1]['first_at'], events[0]['at'])

    def test_collector_output_is_nonblocking_and_rotates_during_run(self):
        runtime = Path(self.temp.name) / 'run'; runtime.mkdir()
        script = '''
import json,os,sys,time
from pathlib import Path
from codex_activity_monitor.diagnostic_log import CollectorOutputCapture
capture=CollectorOutputCapture(Path(sys.argv[1]))
started=time.monotonic(); skipped=0
for i in range(4096):
    try: os.write(2,b'x'*8192)
    except BlockingIOError: skipped+=1
elapsed=time.monotonic()-started
capture.close()
print(json.dumps({'elapsed':elapsed,'skipped':skipped}))
'''
        result = subprocess.run([sys.executable, '-B', '-c', script, str(runtime)], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(json.loads(result.stdout)['elapsed'], 2)
        files = [p for p in (runtime/'collector.log', runtime/'collector.previous.log') if p.exists()]
        self.assertTrue(files)
        self.assertTrue(all(p.stat().st_size <= 2*1024*1024 for p in files))

    def test_retention_prunes_other_component_files_without_collector(self):
        self.directory.mkdir()
        (self.directory / '.monitor-owned').write_text('codex-activity-monitor-diagnostics:1\n')
        old = self.directory / 'bridge.jsonl'; old.write_text('{}\n')
        with patch('codex_activity_monitor.diagnostic_log.time.time', return_value=time.time()+8*86400):
            log = DiagnosticLog(self.directory, 'gui', managed=False)
            log.emit('gui_started'); self.assertTrue(log.close(2))
        self.assertFalse(old.exists())
        self.assertTrue((self.directory / 'gui.jsonl').exists())

    def test_pending_worker_progress_deadlines_do_not_mark_idle_workers_as_stalled(self):
        log = Mock(instance='example'); log.diagnostics.return_value = {'last_error': None, 'dropped': 0}
        engine = Engine(asynchronous_counts=False); self.addCleanup(engine.close)
        history = Mock(); history.diagnostics.return_value = {'reading_age_ms': 6000, 'read_errors': 0, 'worker_alive': True}
        metadata = SimpleNamespace(error=None, diagnostics=lambda: {'writing_age_ms': 6000, 'worker_alive': True})
        monitor = HealthMonitor(log)
        first = monitor.snapshot(engine, history, metadata, None, {'writing_age_ms': 6000}, 1)['components']
        self.assertEqual({c['component'] for c in first if c['state'] == 'degraded'}, {'history', 'metadata', 'checkpoint'})
        history.diagnostics.return_value = {'reading_age_ms': 0, 'read_errors': 0, 'worker_alive': True}
        metadata.diagnostics = lambda: {'writing_age_ms': 0, 'worker_alive': True}
        recovered = monitor.snapshot(engine, history, metadata, None, {'writing_age_ms': 0}, 2)['components']
        self.assertFalse(any(c['state'] == 'degraded' for c in recovered))

    def test_health_recovers_with_historical_counts_and_local_storage_failure_is_scoped(self):
        log = Mock(instance='example')
        log.diagnostics.return_value = {'last_error': None, 'dropped': 0}
        engine = Engine(asynchronous_counts=False)
        self.addCleanup(engine.close)
        engine.backends['b'] = {'connected': True, 'parse_errors': 2, 'dropped_packets': 3}
        history = Mock(); history.diagnostics.return_value = {'read_errors': 0}
        storage = SimpleNamespace(error='metadata_write_failed')
        monitor = HealthMonitor(log)
        def components(now):
            return {c['component']: c for c in monitor.snapshot(engine, history, storage, None,
                     {'snapshot_write_failures': 7, 'current_error': None}, now)['components']}
        first = components(100)
        self.assertEqual(first['bridge']['state'], 'degraded')
        self.assertEqual(first['metadata']['state'], 'degraded')
        self.assertEqual(first['collector']['state'], 'healthy')
        self.assertEqual(first['checkpoint']['state'], 'healthy')
        storage.error = None
        recovered = components(106)
        self.assertEqual(recovered['bridge']['state'], 'healthy')
        self.assertEqual(recovered['bridge']['count'], 5)
        self.assertEqual(recovered['metadata']['state'], 'healthy')
        self.assertTrue(any(call.kwargs.get('transition') == 'recovered' for call in log.emit.call_args_list))


class IntegrationEvidenceTests(unittest.TestCase):
    setUp = controls.AppControlTests.setUp
    configure = controls.AppControlTests.configure
    receipt = controls.AppControlTests.receipt
    def test_revision_is_stable_and_startup_proof_uses_actual_environment(self):
        first = self.configure()['integration_status']['configuration_revision']
        self.assertEqual(self.configure()['integration_status']['configuration_revision'], first)
        env = dict(self.environment.values)
        env.update({REVISION_ENV: first, EXPECTED_ENV: fingerprint(env)})
        self.assertTrue(startup_evidence(env)['configuration_matches'])
        env[KEYS[0]] = '/old-cli'
        self.assertFalse(startup_evidence(env)['configuration_matches'])
        self.assertEqual(startup_evidence({}), {})

    def test_running_host_at_environment_change_requires_restart_until_identity_changes(self):
        self.services.dependencies = [{'pid': os.getpid(), 'reason': 'codex_running'}]
        with patch('codex_activity_monitor.app_session.process_identity', return_value='host-start'):
            status = self.configure()['integration_status']
            self.assertEqual(status['restart'], 'required')
        with patch('codex_activity_monitor.app_session.process_identity', return_value='new-start'):
            status = self.control.status()['integration_status']
            self.assertEqual(status['restart'], 'unknown')

    def test_configuration_changed_or_process_check_failed_is_not_reported_as_active(self):
        self.configure()
        self.environment.values[KEYS[0]] = '/user-change'
        result = self.control.status()['integration_status']
        self.assertEqual(result['configuration'], 'partial')
        self.services.dependencies = [{'reason': 'process_visibility_unknown'}]
        self.assertEqual(self.control.status()['integration_status']['restart'], 'unknown')

    def test_reconfigure_preserves_user_edit_for_later_restore(self):
        self.configure()
        self.environment.values[KEYS[0]] = '/later-user-choice'
        self.configure()
        self.control.restore()
        self.assertEqual(self.environment.values[KEYS[0]], '/later-user-choice')

    def test_reapplying_same_revision_does_not_invent_a_restart_for_an_existing_managed_host(self):
        self.configure()
        self.environment.values[KEYS[0]] = '/later-user-choice'
        self.services.dependencies = [{'pid': os.getpid(), 'reason': 'codex_running'}]
        with patch('codex_activity_monitor.app_session.process_identity', return_value='managed-host'):
            result = self.configure()['integration_status']
        self.assertNotEqual(result['restart'], 'required')

    def test_moving_app_requires_restart_even_when_launcher_environment_is_unchanged(self):
        self.configure()
        installed = self.environment.values.copy()
        moved = self.base / 'Updated App.app'
        self.app.rename(moved)
        self.services.dependencies = [{'pid': os.getpid(), 'reason': 'codex_running'}]
        with patch('codex_activity_monitor.app_session.process_identity', return_value='existing-host'):
            status = self.control.configure(moved, self.cli)['integration_status']
        self.assertEqual(self.environment.values, installed)
        self.assertEqual(status['restart'], 'required')

    def test_bridge_dependency_upgrade_at_same_path_requires_restart(self):
        from codex_activity_monitor import runtime
        original = runtime.source_identity
        def upgraded(*modules):
            identity = original(*modules)
            if 'events' in modules:
                return {**identity, 'source_sha256': 'updated-event-parser'}
            return identity
        first = self.configure()['integration_status']['configuration_revision']
        self.services.dependencies = [{'pid': os.getpid(), 'reason': 'codex_running'}]
        with patch('codex_activity_monitor.runtime.source_identity', side_effect=upgraded), \
             patch('codex_activity_monitor.app_session.process_identity', return_value='existing-host'):
            status = self.configure()['integration_status']
        self.assertNotEqual(status['configuration_revision'], first)
        self.assertEqual(status['restart'], 'required')

    def test_live_legacy_bridge_without_revision_requires_restart(self):
        self.configure()
        bridges = Path(self.control.status()['runtime']) / 'bridges'
        bridges.mkdir()
        (bridges / 'legacy.json').write_text(json.dumps({
            'pid': os.getpid(), 'identity': 'legacy-start', 'observation_protocol': 1}))
        with patch('codex_activity_monitor.app_session.process_identity', return_value='legacy-start'):
            status = self.control.status()['integration_status']
        self.assertEqual(status['old_bridges'], 1)
        self.assertEqual(status['restart'], 'required')
        with patch('codex_activity_monitor.app_session.process_identity', return_value='reused-pid'):
            status = self.control.status()['integration_status']
        self.assertEqual(status['old_bridges'], 0)
        self.assertNotEqual(status['restart'], 'required')
        with patch('codex_activity_monitor.app_session.process_identity', return_value=None):
            status = self.control.status()['integration_status']
        self.assertEqual(status['old_bridges'], 0)
        self.assertEqual(status['unknown_bridges'], 1)

    def test_reopening_unchanged_monitor_keeps_matching_bridge_without_restart(self):
        revision = self.configure()['integration_status']['configuration_revision']
        bridges = Path(self.control.status()['runtime']) / 'bridges'
        bridges.mkdir()
        (bridges / 'current.json').write_text(json.dumps({
            'pid': os.getpid(), 'identity': 'managed-start', 'observation_protocol': 1,
            'configuration_revision': revision, 'configuration_matches': True}))
        self.services.dependencies = [{'pid': os.getpid(), 'reason': 'codex_running'}]
        with patch('codex_activity_monitor.app_session.process_identity', return_value='managed-start'):
            status = self.configure()['integration_status']
        self.assertEqual(status['matching_bridges'], 1)
        self.assertEqual(status['restart'], 'not_required')

    def test_status_never_deletes_a_stale_bridge_registration(self):
        self.configure()
        bridges = Path(self.control.status()['runtime']) / 'bridges'; bridges.mkdir()
        path = bridges / 'old.json'
        path.write_text(json.dumps({'pid': os.getpid(), 'identity': 'old-process'}))
        with patch('codex_activity_monitor.app_session.process_identity', return_value='new-process'):
            self.control.status()
        self.assertTrue(path.exists())

    def test_restore_keeps_diagnostics_and_purge_refuses_unknown_log_files(self):
        self.configure()
        log = DiagnosticLog(self.control.root / 'diagnostics', 'management')
        log.emit('configure_completed'); self.assertTrue(log.close(2))
        self.control.restore()
        directory = self.control.root / 'diagnostics'
        self.assertTrue((directory / 'management.jsonl').exists())
        (directory / 'foreign').write_text('keep')
        with self.assertRaises(controls.ControlError) as failure:
            self.control.restore(purge=True)
        self.assertEqual(failure.exception.code, 'unowned_data')
        self.assertEqual((directory / 'foreign').read_text(), 'keep')
        (directory / 'foreign').unlink()
        self.assertEqual(self.control.restore(purge=True)['state'], 'purged')
        self.assertFalse(self.control.root.exists())

    def test_cli_stops_its_writer_before_restore_or_cleanup_can_remove_owned_files(self):
        from codex_activity_monitor.cli import main
        for action in ('restore', 'cleanup'):
            with self.subTest(action=action):
                writer = Mock()
                writer.close.return_value = True
                def purge(*args):
                    self.assertTrue(writer.close.called, 'purge must not race a live command writer')
                    return {'version': 1, 'ok': True, 'state': 'purged'}
                control = Mock(root=self.control.root)
                getattr(control, action).side_effect = purge
                with patch('codex_activity_monitor.app_control.AppController', return_value=control), \
                     patch('codex_activity_monitor.diagnostic_log.DiagnosticLog', return_value=writer) as factory, \
                     contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(['app', action, '--data-root', str(self.control.root)]), 0)
                self.assertEqual(factory.call_count, 1, 'a purged directory must not get a new writer')


if __name__ == '__main__':
    unittest.main()
