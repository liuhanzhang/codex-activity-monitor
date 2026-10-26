"""Asynchronous, metadata-only sidecar. A failed disk never blocks event forwarding."""

from copy import deepcopy
from contextlib import closing
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
import re
import threading
import time
from typing import Literal

# Explicit allowlist keeps future display/content additions out of the database.
FIELDS = frozenset(('id', 'scope_id', 'thread_id', 'turn_id', 'namespace', 'item_id', 'item_type',
                    'track', 'kind', 'label', 'tool_name', 'call_id', 'client_id', 'process_id',
                    'start_ms', 'end_ms', 'start_source', 'end_source', 'status', 'quality',
                    'mirrored', 'observation_instance', 'observation_backend', 'observed_until_ms', 'updated_ms',
                    'parent_thread_id', 'parent_turn_id', 'child_thread_id', 'operation', 'execution_confirmed',
                    'call_operation', 'call_target_scope', 'history_terminal', 'history_retired', 'exit_code'))


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


@dataclass(frozen=True, slots=True)
class MetadataWrite:
    identity: str
    kind: Literal['phase', 'turn', 'relation'] | None
    payload: str | None  # Encoded immutable allowlisted metadata, never source bodies.
    utf8_bytes: int


class TimelineStorage:
    def __init__(self, path, *, max_pending_records=4096, max_pending_bytes=2*1024*1024, on_exit=None):
        if max_pending_records < 1 or max_pending_bytes < 1:
            raise ValueError('invalid_metadata_budget')
        self.path = Path(path)
        self.pending = {}
        self.pending_bytes = 0
        self.inflight_records = self.inflight_bytes = 0
        self.max_pending_records, self.max_pending_bytes = max_pending_records, max_pending_bytes
        self.reset_pending = False
        self.queue_resets = self.discarded_records = 0
        self.peak_pending_records = self.peak_pending_bytes = 0
        self.on_exit = on_exit
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
        if kind not in ('phase', 'turn', 'relation'):
            raise ValueError('invalid_metadata_kind')
        safe = {k: deepcopy(v) for k, v in record.items() if k in FIELDS}
        # Labels are fixed categories/tool names, never generated command previews.
        payload = json.dumps(safe, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        identity = record['id']
        if not isinstance(identity, str) or not identity:
            raise ValueError('invalid_metadata_identity')
        self._enqueue(MetadataWrite(identity, kind, payload, len(identity.encode())+len(payload.encode())))

    def delete(self, key):
        if not isinstance(key, str) or not key:
            raise ValueError('invalid_metadata_identity')
        self._enqueue(MetadataWrite(key, None, None, len(key.encode())))

    def diagnostics(self):
        with self.lock:
            return {'pending_records': len(self.pending), 'pending_bytes': self.pending_bytes,
                    'inflight_records': self.inflight_records, 'inflight_bytes': self.inflight_bytes,
                    'max_pending_records': self.max_pending_records, 'max_pending_bytes': self.max_pending_bytes,
                    'peak_pending_records': self.peak_pending_records, 'peak_pending_bytes': self.peak_pending_bytes,
                    'queue_resets': self.queue_resets, 'discarded_records': self.discarded_records}

    def _enqueue(self, write):
        with self.lock:
            old = self.pending.get(write.identity)
            size = self.pending_bytes - (old.utf8_bytes if old else 0) + write.utf8_bytes
            count = len(self.pending) + (old is None)
            if size > self.max_pending_bytes or count > self.max_pending_records:
                # Partial deletes/updates must not leave a seemingly complete
                # older sidecar. Invalidate this derived cache on the next write.
                # Codex JSONL stays authoritative; the live book stays available.
                self.discarded_records += len(self.pending)
                self.pending.clear(); self.pending_bytes = 0
                self.reset_pending = True; self.queue_resets += 1
                self.error = 'metadata_queue_limit'
            if write.utf8_bytes <= self.max_pending_bytes:
                old = self.pending.get(write.identity)
                self.pending[write.identity] = write
                self.pending_bytes += write.utf8_bytes - (old.utf8_bytes if old else 0)
            else:
                self.discarded_records += 1
            self.peak_pending_records = max(self.peak_pending_records, len(self.pending))
            self.peak_pending_bytes = max(self.peak_pending_bytes, self.pending_bytes)
        self.wake.set()

    def _write_batch(self, batch, reset):
        with closing(sqlite3.connect(self.path, timeout=.2)) as db, db:
            if reset:
                db.execute('DELETE FROM records')
            for write in batch.values():
                if write.kind is None:
                    db.execute('DELETE FROM records WHERE id=?', (write.identity,))
                else:
                    db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?)', (write.identity, write.kind, write.payload))

    def _run(self):
        try:
            self._drain()
        finally:
            if self.on_exit:
                self.on_exit()

    def _drain(self):
        while True:
            self.wake.wait(.5)
            self.wake.clear()
            with self.lock:
                batch, self.pending = self.pending, {}
                reset, self.reset_pending = self.reset_pending, False
                self.pending_bytes = 0
                self.inflight_records = len(batch)
                self.inflight_bytes = sum(write.utf8_bytes for write in batch.values())
            if batch or reset:
                try:
                    self._write_batch(batch, reset)
                except (OSError, sqlite3.Error):
                    self.error = 'metadata_write_failed'
                    with self.lock:
                        self.reset_pending = True
                finally:
                    with self.lock:
                        self.inflight_records = self.inflight_bytes = 0
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
