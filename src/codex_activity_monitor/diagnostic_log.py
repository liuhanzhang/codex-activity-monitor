"""Best-effort bounded diagnostics. No caller waits for a filesystem or a peer."""

from collections import deque
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import selectors
import threading
import time
import uuid

ROLES = ('gui', 'management', 'collector', 'bridge')
MARKER = 'codex-activity-monitor-diagnostics:1\n'
PARAMS = {'errno', 'type', 'reason', 'exit_code', 'count', 'attempt', 'bytes', 'discarded', 'timeout_ms'}
TOKEN = re.compile(r'^[a-zA-Z0-9_.-]{1,128}$')


def safe_params(values):
    values = values if isinstance(values, dict) else {}
    return {key: value for key in PARAMS if (value := values.get(key)) is not None and
            (type(value) in (int, bool) or (isinstance(value, str) and TOKEN.fullmatch(value)))}


def owned_logs(directory):
    directory = Path(directory)
    if not directory.exists():
        return True
    if directory.is_symlink() or not directory.is_dir():
        return False
    try:
        if (directory / '.monitor-owned').is_symlink() or (directory / '.monitor-owned').read_text() != MARKER:
            return False
        allowed = {'.monitor-owned'} | {f'{role}{suffix}' for role in ROLES
                                        for suffix in ('.lock', '.jsonl', '.jsonl.1', '.jsonl.2', '.jsonl.3')}
        return all(p.name in allowed and p.is_file() and not p.is_symlink() for p in directory.iterdir())
    except OSError:
        return False


def log_directory(runtime):
    """Resolve only in workers/startup, never in the stdio offer hot path."""
    runtime = Path(runtime)
    try:
        managed = json.loads((runtime / 'app-managed.json').read_text())
        session = Path(managed['session_file'])
        if session.is_absolute() and session.name == 'observation.json':
            return session.parent / 'diagnostics', True
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return runtime / 'diagnostics', False


class DiagnosticLog:
    def __init__(self, directory, role, *, managed=True, max_bytes=2*1024*1024,
                 queue_items=256, queue_bytes=256*1024, retention=7*86400):
        if role not in ROLES:
            raise ValueError('invalid_diagnostic_role')
        self.directory, self.role, self.managed = Path(directory), role, managed
        self.max_bytes, self.queue_items, self.queue_bytes, self.retention = max_bytes, queue_items, queue_bytes, retention
        self.instance = uuid.uuid4().hex
        self.sequence = 0
        self.pending = deque()
        self.pending_bytes = 0
        self.condition = threading.Condition()
        self.stopping = False
        self.stop_deadline = None
        self.repeats = {}
        self.next_maintenance = 0
        self.stats = {'dropped': 0, 'write_failures': 0, 'last_error': None, 'written': 0}
        self.worker = threading.Thread(target=self._run, daemon=True, name=f'diagnostics-{role}')
        self.worker.start()

    @classmethod
    def for_runtime(cls, runtime, role):
        directory, managed = log_directory(runtime)
        return cls(directory, role, managed=managed)

    def emit(self, code, *, component=None, level='info', transition=None, params=None, operation_id=None):
        if not isinstance(code, str) or not TOKEN.fullmatch(code) or level not in ('info', 'warning', 'error'):
            return
        component = component or self.role
        if not isinstance(component, str) or not TOKEN.fullmatch(component):
            return
        with self.condition:
            if self.stopping:
                return
            now = time.monotonic()
            self._flush_repeats(now)
            identity = (code, component, level, transition, operation_id, json.dumps(safe_params(params), sort_keys=True))
            previous = self.repeats.get(identity)
            if previous and now - previous['started'] < 5:
                previous['count'] += 1
                previous['last_at'] = datetime.now(timezone.utc).isoformat()
                self.condition.notify()
                return
            self.sequence += 1
            event = {'version': 1, 'event_id': f'{self.instance}-{self.sequence}', 'sequence': self.sequence,
                     'at': datetime.now(timezone.utc).isoformat(), 'level': level, 'component': component,
                     'agent': 'codex', 'instance_id': self.instance, 'code': code, 'count': 1,
                     'message_key': f'diagnostics.{code}', 'params': safe_params(params)}
            if transition in ('started', 'updated', 'recovered'):
                event['transition'] = transition
            if operation_id and TOKEN.fullmatch(operation_id):
                event['operation_id'] = operation_id
            if len(self.repeats) >= 64:
                oldest = next(iter(self.repeats))
                self._flush_repeats(now, only=oldest)
            self.repeats[identity] = {'started': now, 'event': event, 'count': 0, 'last_at': event['at']}
            self._enqueue_event(event)

    def _enqueue_event(self, event):
        frame = json.dumps(event, ensure_ascii=False, separators=(',', ':')).encode() + b'\n'
        if len(frame) > 8192 or len(self.pending) >= self.queue_items or self.pending_bytes + len(frame) > self.queue_bytes:
            self.stats['dropped'] += 1
            return
        self.pending.append(frame)
        self.pending_bytes += len(frame)
        self.condition.notify()

    def _flush_repeats(self, now, force=False, only=None):
        for identity, value in list(self.repeats.items()):
            if not (force or identity == only or now-value['started'] >= 5):
                continue
            self.repeats.pop(identity, None)
            if value['count']:
                self.sequence += 1
                event = dict(value['event'], count=value['count'], first_at=value['event']['at'],
                             at=value['last_at'], sequence=self.sequence, event_id=f'{self.instance}-{self.sequence}')
                self._enqueue_event(event)

    def diagnostics(self):
        with self.condition:
            return dict(self.stats, pending=len(self.pending), pending_bytes=self.pending_bytes)

    def _prepare(self):
        parent = self.directory.parent
        # A logger must never turn a new/foreign root into an installation.
        if self.managed and (parent / '.monitor-owned').read_text() != 'codex-activity-monitor:1\n':
            raise OSError('unowned_diagnostic_root')
        if self.directory.is_symlink():
            raise OSError('symlink_diagnostic_root')
        self.directory.mkdir(mode=0o700, exist_ok=True)
        marker = self.directory / '.monitor-owned'
        if not marker.exists():
            fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as out:
                out.write(MARKER)
        elif marker.is_symlink() or marker.read_text() != MARKER:
            raise OSError('unowned_diagnostics')
        if time.monotonic() >= self.next_maintenance:
            self.next_maintenance = time.monotonic() + 60
            self._prune()

    def _prune(self):
        for role in ROLES:
            fd = os.open(self.directory / f'{role}.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'a') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                for suffix in ('', '.1', '.2', '.3'):
                    path = self.directory / f'{role}.jsonl{suffix}'
                    if path.is_symlink():
                        continue
                    try:
                        stat = path.stat()
                        if time.time() - getattr(stat, 'st_birthtime', stat.st_mtime) > self.retention:
                            path.unlink()
                    except FileNotFoundError:
                        pass

    def _append(self, frame):
        self._prepare()
        lock_fd = os.open(self.directory / f'{self.role}.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(lock_fd, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            path = self.directory / f'{self.role}.jsonl'
            if path.is_symlink():
                raise OSError('symlink_log')
            rotate = path.exists() and path.stat().st_size + len(frame) > self.max_bytes
            if rotate:
                for number in range(3, 0, -1):
                    destination = path.with_name(path.name + f'.{number}')
                    source = path if number == 1 else path.with_name(path.name + f'.{number-1}')
                    if source.is_symlink() or destination.is_symlink():
                        raise OSError('symlink_log')
                    if source.exists():
                        source.replace(destination)
            for number in range(1, 4):
                archive = path.with_name(path.name + f'.{number}')
                if archive.is_symlink():
                    raise OSError('symlink_log')
                if archive.exists() and time.time() - getattr(archive.stat(), 'st_birthtime', archive.stat().st_mtime) > self.retention:
                    archive.unlink()
            fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'ab') as out:
                out.write(frame)

    def _run(self):
        while True:
            with self.condition:
                while not self.pending and not self.stopping:
                    self._flush_repeats(time.monotonic())
                    if self.pending:
                        break
                    self.condition.wait(.25 if self.repeats else None)
                if not self.pending:
                    return
                if self.stopping and time.monotonic() >= self.stop_deadline:
                    self.stats['dropped'] += len(self.pending)
                    self.pending.clear(); self.pending_bytes = 0
                    return
                frame = self.pending[0]
            try:
                self._append(frame)
            except (OSError, ValueError) as error:
                with self.condition:
                    self.stats['write_failures'] += 1
                    self.stats['last_error'] = {'type': type(error).__name__, 'errno': getattr(error, 'errno', None)}
                    if self.stopping and time.monotonic() >= self.stop_deadline:
                        self.stats['dropped'] += len(self.pending)
                        self.pending.clear(); self.pending_bytes = 0
                        return
                    self.condition.wait(.01 if self.stopping else .25)
                continue
            with self.condition:
                self.pending.popleft()
                self.pending_bytes -= len(frame)
                self.stats['written'] += 1
                self.stats['last_error'] = None

    def close(self, timeout=.5):
        with self.condition:
            self._flush_repeats(time.monotonic(), force=True)
            self.stopping = True
            self.stop_deadline = time.monotonic() + timeout
            self.condition.notify()
        self.worker.join(timeout)
        return not self.worker.is_alive()


class RawCollectorLog(DiagnosticLog):
    """Compatibility output is drained independently of the disk writer."""

    def __init__(self, runtime):
        self.runtime = Path(runtime)
        super().__init__(runtime, 'collector', managed=False)

    def _append(self, frame):
        fd = os.open(self.runtime / 'collector-output.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._append_output(frame)

    def _append_output(self, frame):
        path = self.runtime / 'collector.log'
        if path.is_symlink():
            raise OSError('symlink_log')
        if path.exists() and (path.stat().st_size + len(frame) > self.max_bytes or
                              time.time() - getattr(path.stat(), 'st_birthtime', path.stat().st_mtime) > self.retention):
            path.replace(self.runtime / 'collector.previous.log')
        previous = self.runtime / 'collector.previous.log'
        if previous.exists() and not previous.is_symlink() and time.time() - getattr(previous.stat(), 'st_birthtime', previous.stat().st_mtime) > self.retention:
            previous.unlink()
        fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'ab') as out:
            out.write(frame)

    def enqueue(self, data):
        with self.condition:
            if self.stopping or len(self.pending) >= self.queue_items or self.pending_bytes + len(data) > self.queue_bytes:
                self.stats['dropped'] += 1
                return
            self.pending.append(data)
            self.pending_bytes += len(data)
            self.condition.notify()


class CollectorOutputCapture:
    """Collector-only fd redirection. Never used for management/protocol stdout."""

    def __init__(self, runtime):
        self.log = RawCollectorLog(runtime)
        self.saved = [os.dup(1), os.dup(2)]
        self.reader, writer = os.pipe()
        os.set_blocking(writer, False)
        os.dup2(writer, 1); os.dup2(writer, 2)
        os.close(writer)
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._drain, daemon=True, name='collector-output')
        self.thread.start()

    def _drain(self):
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(self.reader, selectors.EVENT_READ)
                while not self.stopping.is_set():
                    if selector.select(.1):
                        data = os.read(self.reader, 8192)
                        if not data:
                            return
                        self.log.enqueue(data)
        except OSError:
            pass
        finally:
            os.close(self.reader)

    def close(self):
        for descriptor, saved in zip((1, 2), self.saved):
            os.dup2(saved, descriptor)
            os.close(saved)
        self.thread.join(.25)
        self.stopping.set()
        self.log.close()
