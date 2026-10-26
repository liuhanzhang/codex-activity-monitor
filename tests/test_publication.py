import errno
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from codex_activity_monitor.publication import DetachedSnapshot, RecoveryWriter


class RecordingPublisher:
    def __init__(self):
        self.failures = 0
        self.last_error = None
        self.values = []
        self.entered = threading.Event()
        self.release = threading.Event()

    def publish(self, value):
        self.entered.set()
        self.release.wait(3)
        self.values.append(value)
        return True


class PublicationTests(unittest.TestCase):
    def test_handoff_owns_data_and_separates_private_recovery(self):
        source = {'schema_version': 1, 'threads': [{'id': 't'}], 'usage_recovery': {'private': 1}}
        captured = DetachedSnapshot.capture(source)
        source['threads'][0]['id'] = 'changed'
        copied = captured.recovery_value()
        copied['threads'].clear()
        live = json.loads(captured.encode('instance', 5))
        self.assertEqual(live['payload']['threads'], [{'id': 't'}])
        self.assertNotIn('usage_recovery', live['payload'])
        self.assertEqual(captured.recovery_value()['usage_recovery'], {'private': 1})

    def test_slow_write_coalesces_and_keeps_ownership_until_actual_exit(self):
        publisher = RecordingPublisher()
        exited = threading.Event()
        writer = RecoveryWriter(Path('/unused'), publisher=publisher, on_exit=exited.set)
        self.addCleanup(publisher.release.set)
        self.addCleanup(writer.close)
        writer.submit(DetachedSnapshot.capture({'n': 0}))
        self.assertTrue(publisher.entered.wait(1))
        for n in range(1, 101):
            writer.submit(DetachedSnapshot.capture({'n': n}))
        self.assertEqual(writer.diagnostics()['pending'], 1)
        self.assertEqual(writer.diagnostics()['coalesced'], 99)
        self.assertFalse(writer.close(timeout=.01))
        self.assertFalse(exited.is_set())
        self.assertFalse(writer.submit(DetachedSnapshot.capture({'n': 101})))
        publisher.release.set()
        self.assertTrue(exited.wait(1))
        self.assertEqual([v['n'] for v in publisher.values], [0, 100])

    def test_disk_failure_retries_latest_and_diagnostics_are_detached(self):
        class FailingPublisher(RecordingPublisher):
            def publish(self, value):
                if not self.release.is_set():
                    self.entered.set()
                    raise OSError(errno.ENOSPC, 'injected failure')
                self.values.append(value)
                return True
        publisher = FailingPublisher()
        writer = RecoveryWriter(Path('/unused'), publisher=publisher, retry_min=.02, retry_max=.04)
        self.addCleanup(writer.close)
        writer.submit(DetachedSnapshot.capture({'n': 0}))
        self.assertTrue(publisher.entered.wait(1))
        deadline = time.monotonic()+1
        while writer.diagnostics()['snapshot_write_failures'] == 0 and time.monotonic() < deadline:
            time.sleep(.005)
        diagnostic = writer.diagnostics()
        self.assertGreater(diagnostic['snapshot_write_failures'], 0)
        self.assertEqual(diagnostic['last_snapshot_error']['errno'], errno.ENOSPC)
        diagnostic['last_snapshot_error']['errno'] = 0
        self.assertEqual(writer.diagnostics()['last_snapshot_error']['errno'], errno.ENOSPC)
        writer.submit(DetachedSnapshot.capture({'n': 1}))
        publisher.release.set()
        self.assertTrue(writer.close())
        self.assertEqual([v['n'] for v in publisher.values], [1])

    def test_real_archive_keeps_recovery_and_exits_after_failed_final_write(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as directory:
            path = Path(directory)
            writer = RecoveryWriter(path)
            writer.submit(DetachedSnapshot.capture({'schema_version': 1, 'usage_recovery': {'n': 1}}),
                          instance_id='fixture', state_sequence=8)
            self.assertTrue(writer.close())
            saved = json.loads((path/'snapshot.json').read_text())
            self.assertEqual(saved['usage_recovery'], {'n': 1})
            self.assertEqual(saved['checkpoint']['instance_id'], 'fixture')
            self.assertEqual(saved['checkpoint']['state_sequence'], 8)
            self.assertIsInstance(saved['checkpoint']['encoded_at'], float)
            # No directory to receive atomic writes: close attempts at most the
            # final pending value, never an unbounded retry loop.
            failed = RecoveryWriter(path/'missing')
            failed.submit(DetachedSnapshot.capture({}))
            self.assertTrue(failed.close())
            self.assertGreater(failed.diagnostics()['snapshot_write_failures'], 0)
