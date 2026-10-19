import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
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
                           'output': 'PRIVATE_RESULT', 'status': 'completed'})
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


if __name__ == '__main__':
    unittest.main()
