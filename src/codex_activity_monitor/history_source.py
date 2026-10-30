"""Read-only selection of the current rollout and its bounded inherited ranges."""
from collections import OrderedDict
from contextlib import closing
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import tomllib
from typing import Callable

from .agent_presentation import agent_metadata

MAX_HEADER = 1024 * 1024
MAX_LINEAGE = 64
MAX_LINEAGE_SECONDS = 1.0
MAX_DISCOVERY_FILES = 20000
MAX_DISCOVERY_SECONDS = .25
MAX_BOUNDARY_RECORD = 8 * 1024 * 1024
STATE_DATABASE = 'state_5.sqlite'
_FILENAME = re.compile(r'^rollout-\d{4}-\d\d-\d\dT\d\d-\d\d-\d\d-(?P<thread>[^_]+?)(?:_(?P<rollout>[^_]+))?\.jsonl$')


class HistoryUnavailable(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def integer(value):
    return type(value) is int and 0 <= value <= 2**63-1


def rollout_ids(path):
    if path.name.endswith('.jsonl.zst'):
        path = path.with_suffix('')
    match = _FILENAME.fullmatch(path.name)
    return (match['thread'], match['rollout'] or match['thread']) if match else None


def managed_path(scope, value):
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise HistoryUnavailable('history_path_invalid')
    path = path.resolve()
    roots = [(Path(scope)/name).resolve() for name in ('sessions', 'archived_sessions')]
    if not any(path.is_relative_to(root) for root in roots):
        raise HistoryUnavailable('history_path_outside_scope')
    return path


def read_header(path):
    if path.name.endswith('.jsonl.zst') or not path.exists() and Path(str(path)+'.zst').is_file():
        raise HistoryUnavailable('history_compressed_unsupported')
    if not path.is_file():
        raise HistoryUnavailable('history_path_not_regular')
    with path.open('rb') as stream:
        raw = stream.readline(MAX_HEADER+1)
        stat = os.fstat(stream.fileno())
    if len(raw) > MAX_HEADER or not raw.endswith(b'\n'):
        raise HistoryUnavailable('history_header_invalid')
    try:
        row = json.loads(raw)
        payload = row['payload']
        if (row['type'] != 'session_meta' or not isinstance(payload, dict) or
                not isinstance(payload.get('id'), str) or not 0 < len(payload['id']) <= 256):
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise HistoryUnavailable('history_header_invalid') from None
    safe = {k: payload[k] for k in ('id', 'history_mode', 'history_base',
            'subagent_history_start_ordinal', 'attached_thread') if k in payload}
    if isinstance(safe.get('history_base'), dict):
        safe['history_base'] = {k: safe['history_base'][k] for k in
            ('thread_id', 'end_ordinal_exclusive', 'end_byte_offset') if k in safe['history_base']}
    return Header(path, payload['id'], safe, len(raw), hashlib.sha256(raw).hexdigest(),
                  (stat.st_dev, stat.st_ino), row.get('ordinal'), agent_metadata(payload))


@dataclass(frozen=True)
class Header:
    path: Path
    owner: str
    payload: dict
    length: int
    digest: str
    fingerprint: tuple
    ordinal: int | None
    agent: dict

    @property
    def rollout(self):
        ids = rollout_ids(self.path)
        if ids and ids[0] != self.owner:
            raise HistoryUnavailable('history_identity_mismatch')
        return ids[1] if ids else self.owner


@dataclass(frozen=True)
class SourcePart:
    header: Header
    start: int = 0
    end: int | None = None
    end_ordinal: int | None = None
    minimum_ordinal: int | None = None


@dataclass(frozen=True)
class HistorySource:
    path: Path
    thread: str
    parts: tuple[SourcePart, ...]
    quality: tuple[str, ...] = ()
    guard: Callable[[], bool] | None = field(default=None, compare=False, repr=False)

    @property
    def branched(self):
        header = self.parts[0].header
        return bool(header.payload.get('history_base') or header.rollout != self.thread)

    @property
    def generation(self):
        value = [(str(p.header.path), p.header.owner, p.header.digest, p.header.fingerprint,
                  p.start, p.end, p.end_ordinal, p.minimum_ordinal) for p in self.parts]
        return hashlib.sha256(json.dumps((str(self.path), value, self.quality), separators=(',', ':')).encode()).hexdigest()

    def open(self):
        from .history_stream import SourceStream
        return SourceStream(self)


class RolloutResolver:
    """Hints are optional; SQLite's selected paginated path overrides stale hints."""
    def __init__(self, *, discovery_ttl=10, max_lineage=MAX_LINEAGE, max_lineage_seconds=MAX_LINEAGE_SECONDS):
        self.hints = OrderedDict()
        self.discoveries = OrderedDict()
        self.discovery_ttl = discovery_ttl
        self.max_lineage = max_lineage
        self.max_lineage_seconds = max_lineage_seconds
        self.boundaries = OrderedDict()
        self.authoritative = OrderedDict()

    def hint(self, scope, thread, value):
        key = scope, thread
        if value is None:
            self.hints.pop(key, None)
        else:
            safe = {k: v for k, v in value.items() if k in ('path', 'history_mode', 'sqlite_home') and
                    isinstance(v, str) and 0 < len(v) <= 4096}
            self.hints[key] = (time.monotonic(), safe)
            self.hints.move_to_end(key)
            while len(self.hints) > 512:
                self.hints.popitem(last=False)
        self.discoveries.pop(key, None)

    def _database(self, scope, hint):
        home = Path(scope)
        configured = hint.get('sqlite_home')
        try:
            config_path = home/'config.toml'
            if config_path.is_file():
                if config_path.stat().st_size > MAX_HEADER:
                    raise HistoryUnavailable('history_state_configuration_unknown')
                with config_path.open('rb') as stream:
                    config = tomllib.load(stream)
                profiles = config.get('profiles', {})
                if isinstance(profiles, dict) and any(isinstance(p, dict) and 'sqlite_home' in p for p in profiles.values()):
                    # The running backend's profile cannot be inferred from user TOML alone.
                    raise HistoryUnavailable('history_state_configuration_unknown')
                configured = config.get('sqlite_home', configured)
        except (OSError, ValueError):
            raise HistoryUnavailable('history_state_configuration_unknown') from None
        if configured is not None:
            if not isinstance(configured, str) or not Path(configured).is_absolute():
                raise HistoryUnavailable('history_state_configuration_unknown')
            home = Path(configured)
        return home/STATE_DATABASE

    def _state(self, scope, thread, hint):
        database = self._database(scope, hint)
        if not database.exists():
            return None
        try:
            with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True, timeout=.05)) as db:
                value = db.execute('SELECT rollout_path, history_mode FROM threads WHERE id=?', (thread,)).fetchone()
        except sqlite3.Error:
            raise HistoryUnavailable('history_state_unavailable') from None
        if value is None:
            return None
        path, mode = value
        if not isinstance(path, str) or mode not in ('legacy', 'paginated'):
            raise HistoryUnavailable('history_state_unavailable')
        return path, mode

    def _hint(self, key):
        at, value = self.hints.get(key, (0, {}))
        # Configuration survives; a once-observed path is not a permanent pointer.
        return value if time.monotonic()-at <= 10 else {k: v for k, v in value.items() if k == 'sqlite_home'}

    def _selected(self, scope, thread):
        hint = self._hint((scope, thread))
        try:
            state = self._state(scope, thread, hint)
        except HistoryUnavailable:
            if hint.get('path'):
                return managed_path(scope, hint['path']), None, ('history_state_unavailable',)
            raise
        if state:
            self.authoritative[(scope, thread)] = state
            self.authoritative.move_to_end((scope, thread))
            while len(self.authoritative) > 512:
                self.authoritative.popitem(last=False)
        elif self.authoritative.get((scope, thread), (None, None))[1] == 'paginated':
            # Disappearing metadata cannot revive a previously abandoned branch.
            raise HistoryUnavailable('history_selected_unavailable')
        if state:
            path, mode = state
            try:
                selected = managed_path(scope, path)
                head = read_header(selected)
                if head.owner != thread:
                    raise HistoryUnavailable('history_identity_mismatch')
                return selected, (path, mode), ()
            except (OSError, ValueError) as error:
                if mode == 'paginated':
                    if getattr(error, 'reason', None) == 'history_compressed_unsupported':
                        raise
                    raise HistoryUnavailable('history_selected_unavailable') from None
        if hint.get('path'):
            return managed_path(scope, hint['path']), None, ()
        candidates = []
        compressed = False
        for path in self._discover(scope, thread):
            if path.name.endswith('.jsonl.zst'):
                compressed = True
                continue
            try:
                head = read_header(path)
                if head.owner == thread:
                    candidates.append(path)
            except (OSError, ValueError):
                continue
        if compressed:
            raise HistoryUnavailable('history_compressed_unsupported')
        if len(candidates) > 1:
            raise HistoryUnavailable('history_selection_ambiguous')
        return (candidates[0], None, ()) if candidates else (None, None, ())

    def _discover(self, scope, identity):
        key = scope, identity
        now = time.monotonic()
        cached = self.discoveries.get(key)
        if (cached and now-cached[0] < self.discovery_ttl and all(p.is_file() for p in cached[1]) and
                all(p.exists() and p.stat().st_mtime_ns == stamp for p, stamp in cached[2])):
            self.discoveries.move_to_end(key)
            return cached[1]
        results = []
        visited = 0
        for name in ('sessions', 'archived_sessions'):
            base = Path(scope)/name
            for parent, directories, files in os.walk(base, followlinks=False):
                directories[:] = [d for d in directories if not (Path(parent)/d).is_symlink()]
                for filename in files:
                    visited += 1
                    if visited > MAX_DISCOVERY_FILES or time.monotonic()-now > MAX_DISCOVERY_SECONDS:
                        raise HistoryUnavailable('history_discovery_budget')
                    if not filename.endswith(('.jsonl', '.jsonl.zst')):
                        continue
                    path = Path(parent)/filename
                    if filename.endswith('.jsonl.zst') and path.with_suffix('').is_file():
                        continue
                    ids = rollout_ids(path)
                    if (ids and identity in ids) or filename.endswith(identity+'.jsonl'):
                        try:
                            path = managed_path(scope, path)
                            if path.is_file():
                                results.append(path)
                        except (OSError, ValueError):
                            continue
        if results:
            directories = {Path(scope)/name for name in ('sessions', 'archived_sessions')}
            for path in results:
                directories.update(p for p in path.parents if p.is_relative_to(Path(scope)) and p != Path(scope))
            stamps = tuple((p, p.stat().st_mtime_ns) for p in sorted(directories) if p.is_dir())
            self.discoveries[key] = now, tuple(results), stamps
            self.discoveries.move_to_end(key)
            while len(self.discoveries) > 512:
                self.discoveries.popitem(last=False)
        return tuple(results)

    def _ancestor(self, scope, identity):
        matches = []
        compressed = False
        for path in self._discover(scope, identity):
            if path.name.endswith('.jsonl.zst'):
                ids = rollout_ids(path)
                compressed |= bool(ids and ids[1] == identity)
                continue
            try:
                head = read_header(path)
                if head.rollout == identity:
                    matches.append(head)
            except (OSError, ValueError):
                continue
        if len(matches) != 1:
            if not matches and compressed:
                raise HistoryUnavailable('history_compressed_unsupported')
            raise HistoryUnavailable('history_lineage_missing' if not matches else 'history_lineage_ambiguous')
        return matches[0]

    def _boundary(self, head, reference):
        end, ordinal = reference.get('end_byte_offset'), reference.get('end_ordinal_exclusive')
        if not integer(end) or not integer(ordinal) or not integer(head.ordinal) or ordinal <= head.ordinal:
            raise HistoryUnavailable('history_lineage_invalid')
        stat = head.path.stat()
        key = str(head.path), head.digest, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, end, ordinal
        if key in self.boundaries:
            self.boundaries.move_to_end(key)
            return end, ordinal
        with head.path.open('rb') as stream:
            if not head.length <= end <= os.fstat(stream.fileno()).st_size:
                raise HistoryUnavailable('history_lineage_invalid')
            count = min(end, 512)
            while True:
                start = end-count
                stream.seek(start)
                raw = stream.read(count)
                if not start or b'\n' in raw[:-1] or count >= MAX_BOUNDARY_RECORD:
                    break
                count = min(end, count*2, MAX_BOUNDARY_RECORD)
        if not raw.endswith(b'\n'):
            raise HistoryUnavailable('history_lineage_invalid')
        last_start = raw.rfind(b'\n', 0, len(raw)-1)+1
        if start and not last_start:
            raise HistoryUnavailable('history_boundary_budget')
        try:
            last = json.loads(raw[last_start:])
            if last.get('ordinal') != ordinal-1:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise HistoryUnavailable('history_lineage_invalid') from None
        self.boundaries[key] = True
        while len(self.boundaries) > 1024:
            self.boundaries.popitem(last=False)
        return end, ordinal

    def resolve(self, scope, thread):
        started = time.monotonic()
        scope_path = Path(scope).expanduser()
        if not scope_path.is_absolute():
            return None
        selected, state, selection_quality = self._selected(scope, thread)
        if selected is None:
            return None
        try:
            root = read_header(selected)
        except FileNotFoundError:
            raise HistoryUnavailable('history_selected_unavailable') from None
        if root.owner != thread:
            raise HistoryUnavailable('history_identity_mismatch')
        current = root
        segments = []
        seen = set()
        quality = set(selection_quality)
        limit = None
        minimum = root.payload.get('subagent_history_start_ordinal')
        special = minimum is not None or root.payload.get('attached_thread') is True
        if special:
            quality.add('history_special_inheritance_unsupported')
            if minimum is not None and not integer(minimum):
                raise HistoryUnavailable('history_lineage_invalid')
        while True:
            if time.monotonic()-started > self.max_lineage_seconds:
                raise HistoryUnavailable('history_lineage_budget')
            if current.rollout in seen or len(seen) >= self.max_lineage:
                raise HistoryUnavailable('history_lineage_invalid')
            seen.add(current.rollout)
            if limit is not None and current.payload.get('history_mode') != 'paginated':
                raise HistoryUnavailable('history_lineage_invalid')
            end, ordinal = self._boundary(current, limit) if limit else (None, None)
            segments.append(SourcePart(current, current.length, end, ordinal, minimum if current is root else None))
            base = current.payload.get('history_base')
            if not base or special:
                break
            if current.payload.get('history_mode') != 'paginated' or not isinstance(base, dict):
                raise HistoryUnavailable('history_lineage_invalid')
            identity = base.get('thread_id')
            if not isinstance(identity, str) or not 0 < len(identity) <= 256 or any(c in identity for c in ('/', '\\', '\0')):
                raise HistoryUnavailable('history_lineage_invalid')
            # The header ordinal is the inherited prefix's exclusive end.
            if current.ordinal != base.get('end_ordinal_exclusive'):
                raise HistoryUnavailable('history_lineage_invalid')
            try:
                current = self._ancestor(scope, identity)
            except HistoryUnavailable as exc:
                if exc.reason in ('history_lineage_missing', 'history_compressed_unsupported'):
                    quality.add('history_inheritance_unavailable')
                    quality.add(exc.reason)
                    break
                raise
            limit = base
        parts = (SourcePart(root, 0, root.length), *reversed(segments))
        # Keep native offsets and native file stamps for the ordinary single-file case.
        if len(segments) == 1 and not root.payload.get('history_base') and not special:
            parts = (SourcePart(root),)
        def guard():
            if state is not None:
                return self._state(scope, thread, self._hint((scope, thread))) == state
            hint = self._hint((scope, thread))
            if hint.get('path') and managed_path(scope, hint['path']) != selected:
                return False
            try:
                current_state = self._state(scope, thread, hint)
            except HistoryUnavailable:
                return bool(selection_quality) and bool(hint.get('path'))
            return current_state is None or managed_path(scope, current_state[0]) == selected
        return HistorySource(selected, thread, tuple(parts), tuple(sorted(quality)), guard)
