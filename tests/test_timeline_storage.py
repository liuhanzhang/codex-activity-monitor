import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from codex_progress_monitor.timeline import TimelineBook
from codex_progress_monitor.timeline_storage import TimelineStorage, read_records


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'timeline/metadata.sqlite3'

    def test_metadata_allowlist_excludes_full_content(self):
        store = TimelineStorage(self.path)
        store.put('phase', {'id': 'p', 'scope_id': '/probe', 'thread_id': 't', 'turn_id': 'u',
                           'kind': 'tool_input', 'text': 'PRIVATE_BODY', 'arguments': 'PRIVATE_CODE',
                           'output': 'PRIVATE_RESULT', 'status': 'completed',
                           'observation': {'status': 'active', 'text': 'PRIVATE_BASE',
                                           'observation': {'output': 'PRIVATE_NESTED'}}})
        store.close()
        records = read_records(self.path)
        self.assertEqual(len(records), 1)
        self.assertNotIn('PRIVATE', json.dumps(records))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_restart_does_not_revive_active_phase(self):
        store = TimelineStorage(self.path)
        store.put('phase', {'id': 'p', 'scope_id': '/probe', 'thread_id': 't', 'turn_id': 'u', 'status': 'active'})
        store.close()
        restored = TimelineStorage(self.path)
        book = TimelineBook(restored)
        self.assertEqual(book.phases['p']['status'], 'unknown')
        self.assertIn('collector_restarted', book.phases['p']['quality'])
        restored.close()

    def test_corrupt_database_is_isolated_without_touching_source(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b'corrupt metadata')
        store = TimelineStorage(self.path)
        self.assertEqual(store.error, 'metadata_rebuilt')
        self.assertEqual(len(list(self.path.parent.glob('metadata-unavailable-*'))), 1)
        store.close()

    def test_locked_database_is_preserved_without_rebuild(self):
        store = TimelineStorage(self.path)
        store.put('phase', {'id': 'retained', 'status': 'completed'})
        store.close()
        before = read_records(self.path)
        inode = self.path.stat().st_ino
        with closing(sqlite3.connect(self.path)) as owner:
            owner.execute('BEGIN EXCLUSIVE')
            try:
                with self.assertRaises(OSError):
                    unexpected = TimelineStorage(self.path)
                    self.addCleanup(unexpected.close)
            finally:
                owner.rollback()
        self.assertEqual(self.path.stat().st_ino, inode)
        self.assertEqual(read_records(self.path), before)
        self.assertEqual(list(self.path.parent.glob('metadata-unavailable-*')), [])

    def test_operational_failure_does_not_isolate_database(self):
        store = TimelineStorage(self.path)
        store.close()
        before = self.path.read_bytes()
        with patch('codex_progress_monitor.timeline_storage.sqlite3.connect',
                   side_effect=sqlite3.OperationalError('unable to open database file')):
            with self.assertRaises(OSError):
                TimelineStorage(self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob('metadata-unavailable-*')), [])

    def test_unsupported_schema_is_preserved_for_newer_reader(self):
        self.path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('PRAGMA user_version=2')
            db.execute('CREATE TABLE future_records (value TEXT)')
            db.execute("INSERT INTO future_records VALUES ('retained')")
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            unexpected = TimelineStorage(self.path)
            self.addCleanup(unexpected.close)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob('metadata-unavailable-*')), [])

    def test_slow_writer_bounds_pending_records_and_invalidates_partial_old_cache(self):
        entered, release = threading.Event(), threading.Event()
        original = TimelineStorage._write_batch
        def blocked(store, batch, reset):
            entered.set(); release.wait(3)
            return original(store, batch, reset)
        with patch.object(TimelineStorage, '_write_batch', blocked):
            store = TimelineStorage(self.path, max_pending_records=2, max_pending_bytes=1024)
            try:
                store.put('phase', {'id': 'old', 'status': 'active'})
                self.assertTrue(entered.wait(1))
                for number in range(10): store.put('phase', {'id': str(number), 'status': 'completed'})
                diagnostics = store.diagnostics()
                self.assertLessEqual(diagnostics['pending_records'], 2)
                self.assertLessEqual(diagnostics['pending_bytes'], 1024)
                self.assertEqual(diagnostics['inflight_records'], 1)
                self.assertGreater(diagnostics['queue_resets'], 0)
                self.assertEqual(store.error, 'metadata_queue_limit')
            finally:
                release.set(); store.close()
        records = read_records(self.path)
        self.assertNotIn('old', [value['id'] for _, value in records])
        self.assertIn('9', [value['id'] for _, value in records])

    def test_oversized_metadata_cannot_enter_the_queue_and_bodies_stay_excluded(self):
        store = TimelineStorage(self.path, max_pending_bytes=128)
        try:
            store.put('phase', {'id': 'too-large', 'label': 'x'*256})
            self.assertEqual(store.diagnostics()['pending_bytes'], 0)
            self.assertEqual(store.error, 'metadata_queue_limit')
            store.put('phase', {'id': 'safe', 'text': 'PRIVATE_BODY'*1024})
        finally: store.close()
        self.assertEqual(read_records(self.path), [('phase', {'id': 'safe'})])

    def test_failed_batch_cannot_resurrect_stale_rows_on_later_recovery(self):
        store = TimelineStorage(self.path)
        store.put('phase', {'id': 'old', 'status': 'active'}); store.close()
        failed = threading.Event()
        original = TimelineStorage._write_batch
        def fail_once(store, batch, reset):
            if not failed.is_set():
                failed.set(); raise sqlite3.OperationalError('synthetic disk failure')
            return original(store, batch, reset)
        with patch.object(TimelineStorage, '_write_batch', fail_once):
            store = TimelineStorage(self.path)
            store.put('phase', {'id': 'lost'})
            self.assertTrue(failed.wait(1))
            store.put('phase', {'id': 'latest'})
            store.close()
        self.assertEqual(read_records(self.path), [('phase', {'id': 'latest'})])

    def test_bounded_close_does_not_release_worker_ownership_before_io_ends(self):
        entered, release, exited = threading.Event(), threading.Event(), threading.Event()
        original = TimelineStorage._write_batch
        def blocked(store, batch, reset):
            entered.set(); release.wait(4)
            return original(store, batch, reset)
        with patch.object(TimelineStorage, '_write_batch', blocked):
            store = TimelineStorage(self.path, on_exit=exited.set)
            try:
                store.put('phase', {'id': 'p'})
                self.assertTrue(entered.wait(1))
                begin = time.monotonic(); store.close()
                self.assertLess(time.monotonic()-begin, 2.5)
                self.assertFalse(exited.is_set())
            finally: release.set()
            self.assertTrue(exited.wait(1))
            store.close()


if __name__ == '__main__':
    unittest.main()
