"""Asynchronous, metadata-only sidecar. A failed disk never blocks event forwarding."""

from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import re
import threading
import time

# Explicit allowlist keeps future display/content additions out of the database.
FIELDS = frozenset(('id', 'scope_id', 'thread_id', 'turn_id', 'namespace', 'item_id', 'item_type',
                    'track', 'kind', 'label', 'tool_name', 'call_id', 'client_id', 'process_id',
                    'start_ms', 'end_ms', 'start_source', 'end_source', 'status', 'quality',
                    'mirrored', 'observation_instance', 'observation_backend', 'observed_until_ms', 'updated_ms',
                    'parent_thread_id', 'parent_turn_id', 'child_thread_id', 'operation', 'execution_confirmed'))


def metadata_path(runtime):
    runtime = Path(runtime)
    try:
        managed = json.loads((runtime / 'app-managed.json').read_text())
        session = Path(managed['session_file'])
        if session.name == 'observation.json':
            return session.parent / 'timeline/metadata.sqlite3'
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return runtime / 'timeline/metadata.sqlite3'


class TimelineStorage:
    def __init__(self, path):
        self.path = Path(path)
        self.pending = {}
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.stopping = False
        self.error = None
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise OSError('Timeline storage must be a private regular path')
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        self._initialize()
        self.worker = threading.Thread(target=self._run, daemon=True, name='timeline-metadata')
        self.worker.start()

    def _initialize(self):
        try:
            with closing(sqlite3.connect(self.path, timeout=.2)) as db, db:
                version = db.execute('PRAGMA user_version').fetchone()[0]
                if version not in (0, 1):
                    raise ValueError('Unsupported timeline version')
                db.execute('CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, type TEXT NOT NULL, data TEXT NOT NULL)')
                db.execute('PRAGMA user_version=1')
            self.path.chmod(0o600)
        except sqlite3.DatabaseError as exc:
            # Busy/locked, read-only, I/O and permission failures do not prove
            # corruption. Preserve the original file and let collection degrade.
            code = getattr(exc, 'sqlite_errorcode', None)
            if code is None or code & 0xff not in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
                raise OSError('Timeline metadata unavailable') from exc
            self.path.rename(self.path.with_name('metadata-unavailable-' + str(time.time_ns()) + '.sqlite3'))
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute('CREATE TABLE records (id TEXT PRIMARY KEY, type TEXT NOT NULL, data TEXT NOT NULL)')
                db.execute('PRAGMA user_version=1')
            self.path.chmod(0o600)
            self.error = 'metadata_rebuilt'

    def load(self):
        return read_records(self.path)

    def put(self, kind, record):
        safe = {k: deepcopy(v) for k, v in record.items() if k in FIELDS}
        # Labels are fixed categories/tool names, never generated command previews.
        with self.lock:
            self.pending[record['id']] = (kind, safe)
        self.wake.set()

    def delete(self, key):
        with self.lock:
            self.pending[key] = None
        self.wake.set()

    def _run(self):
        while True:
            self.wake.wait(.5)
            self.wake.clear()
            with self.lock:
                batch, self.pending = self.pending, {}
            if batch:
                try:
                    with closing(sqlite3.connect(self.path, timeout=.2)) as db, db:
                        for key, value in batch.items():
                            if value is None:
                                db.execute('DELETE FROM records WHERE id=?', (key,))
                            else:
                                kind, record = value
                                db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?)',
                                           (key, kind, json.dumps(record, ensure_ascii=False, separators=(',', ':'))))
                except (OSError, sqlite3.Error):
                    self.error = 'metadata_write_failed'
            if self.stopping:
                with self.lock:
                    if not self.pending:
                        return

    def close(self):
        self.stopping = True
        self.wake.set()
        self.worker.join(timeout=2)


def read_records(path):
    path = Path(path).resolve()
    if not path.is_file():
        return []
    try:
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.2)) as db:
            if db.execute('PRAGMA user_version').fetchone()[0] != 1:
                return []
            return [(kind, json.loads(data)) for kind, data in db.execute('SELECT type,data FROM records')]
    except (OSError, sqlite3.Error, ValueError):
        return []


def owned_directory(path):
    path = Path(path)
    if not path.exists():
        return True
    if path.is_symlink() or not path.is_dir():
        return False
    return all(p.is_file() and not p.is_symlink() and
               (p.name in ('metadata.sqlite3','metadata.sqlite3-journal') or
                re.fullmatch(r'metadata-unavailable-\d+\.sqlite3', p.name)) for p in path.iterdir())
