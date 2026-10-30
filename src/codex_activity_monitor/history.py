"""Reader facade and one weighted cache for source directories and turn indices.

Content rendering and source scanning have no Engine reference. This provider
owns their mutable caches; callers receive detached views under its reader lock.
"""

from collections import OrderedDict
from copy import copy, deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
import threading
import time
from typing import Literal

# Preserve the established read helpers at this facade.
from .history_content import (ABSENT, CANONICAL_TYPES, fields, item_content, raw_section, result_texts,
                              section, source_truncated, text_content, tool_content)
from .history_index import HistoryDirectory, HistoryDetails, HistoryFacts, HistoryMetadata, RolloutIndex, canonical_item, timestamp
from .history_read import HistoryChanged, HistoryRead
from .history_source import RolloutResolver
from .resource_budget import retained_bytes
from .timeline import phase_id

@dataclass(frozen=True, slots=True)
class HistoryCacheKey:
    scope: str
    thread: str
    kind: Literal['full', 'directory', 'turn', 'facts', 'detail', 'timeline']
    turn: str | None = None
    phase: str | None = None


@dataclass(frozen=True, slots=True)
class HistoryCacheStats:
    indices: int
    cache_bytes: int
    max_cache_bytes: int
    evictions: int
    measured_monotonic: float


class HistoryProvider:
    def __init__(self, capacity=12, max_cache_bytes=64*1024*1024, max_turn_indices=1024):
        if capacity < 0 or max_cache_bytes < 0 or max_turn_indices < 0:
            raise ValueError('invalid_history_budget')
        self.resolver = RolloutResolver()
        self.sources = OrderedDict()
        self.source_epochs = {}
        self.source_sequence = 0
        self.input_versions = OrderedDict()
        self.indices = OrderedDict()
        self.lock = threading.RLock()
        self.missing = OrderedDict()
        self.capacity = capacity
        self.max_cache_bytes = max_cache_bytes
        self.max_turn_indices = max_turn_indices
        self.weights = {}
        self.evictions = 0
        self._stats = HistoryCacheStats(0, 0, max_cache_bytes, 0, time.monotonic())

    def view(self, scope, thread):
        """Detach mutable index metadata before releasing the reader lock."""
        with self.lock:
            index = self.index(scope, thread)
            if index is None:
                return None
            return self._view(index)

    def selected_view(self, scope, thread, turn):
        """Read and cache one turn; source size cannot force a full phase rebuild."""
        with self.lock:
            self._source(scope, thread)
            cached = self._cached_selection(scope, thread, {turn})
            if cached is not None:
                return cached
            for attempt in range(2):
                directory = self.index(scope, thread, directory_only=True)
                if directory is None:
                    return None
                try:
                    return self.turn_view(directory, {turn})
                except HistoryChanged:
                    if attempt: raise

    def cached_path(self, scope, thread):
        with self.lock:
            return next((index.path for key, index in reversed(self.indices.items())
                         if (key.scope, key.thread) == (scope, thread)), None)

    def detail_view(self, scope, thread, turn, selected_id, observed_phase=None):
        """One phase and explicit call dependencies, independent of turn width."""
        key = HistoryCacheKey(scope, thread, 'detail', turn, selected_id)
        with self.lock:
            self._source(scope, thread)
            cached = self.indices.get(key)
            if cached is not None and cached.hint_signature == HistoryDetails.hint_key(observed_phase):
                try:
                    with HistoryRead(cached.path, thread, cached.fingerprint, cached.header_digest, cached.source) as reader:
                        if reader.before == cached.file_stamp:
                            result = self._view(cached)
                            self.indices.move_to_end(key)
                            return result
                except (OSError, HistoryChanged):
                    self.invalidate(scope, thread)
            for attempt in range(2):
                directory = self.index(scope, thread, directory_only=True)
                if directory is None:
                    return None
                try:
                    index = directory.load_detail(turn, selected_id, observed_phase)
                except HistoryChanged:
                    self.invalidate(scope, thread)
                    if attempt: raise
                    continue
                self.indices[key] = index
                self.indices.move_to_end(key)
                self._budget(key, index)
                return self._view(index)

    def turn_view(self, directory, turns):
        """Caller holds the provider lock and its catalogue for this query."""
        return self._turn_views(directory, turns, 'turn')

    def timeline_view(self, directory, turns):
        """Full phase metadata for a timeline, without unused content references."""
        return self._turn_views(directory, turns, 'timeline')

    def _turn_views(self, directory, turns, kind):
        parts = []
        for turn in turns:
            key = HistoryCacheKey(directory.scope, directory.thread, kind, turn)
            index = self.indices.get(key)
            if index is None or index.file_stamp != directory.file_stamp or index.generation != directory.generation:
                try:
                    index = directory.load_metadata(turn) if kind == 'timeline' else directory.load_turn(turn)
                except HistoryChanged:
                    self.invalidate(directory.scope, directory.thread)
                    raise
                self.indices[key] = index
            self.indices.move_to_end(key)
            directory_key = HistoryCacheKey(directory.scope, directory.thread, 'directory')
            if directory_key in self.indices:
                self.indices.move_to_end(directory_key)
            self._budget(key, index)
            parts.append(index)
        return self._selection_view(directory, parts, projection=kind)

    def _cached_selection(self, scope, thread, turns, *, required_ids=None):
        """A directory eviction need not invalidate verified, unchanged turns.

        Check one open source handle, header and file stamp for the whole
        selection. Any change falls back to incremental source discovery.
        Caller owns the reader lock, never an Engine/domain lock.
        """
        self._source(scope, thread)
        if not turns:
            return None
        kind = 'turn' if required_ids is None else 'facts'
        keys = [HistoryCacheKey(scope, thread, kind, turn) for turn in turns]
        parts = [self.indices.get(key) for key in keys]
        if any(part is None for part in parts):
            return None
        if required_ids is not None and any(not required_ids.get(key.turn, set()) <= part.selected_ids
                                            for key, part in zip(keys, parts)):
            return None
        source = parts[0]
        if any((part.path, part.file_stamp, part.generation) !=
               (source.path, source.file_stamp, source.generation) for part in parts):
            return None
        try:
            with HistoryRead(source.path, thread, source.fingerprint, source.header_digest, source.source) as reader:
                if reader.before != source.file_stamp:
                    return None
                result = self._selection_view(source, parts, projection='facts' if required_ids is not None else 'turn')
        except (OSError, HistoryChanged):
            self.invalidate(scope, thread)
            return None
        for key in keys:
            self.indices.move_to_end(key)
        return result

    @classmethod
    def _selection_view(cls, source, parts, *, projection: Literal['turn', 'facts', 'timeline'] = 'turn'):
        facts = projection == 'facts'
        selected_ids = frozenset(key for part in parts for key in part.selected_ids) if facts else ()
        if facts:
            result = HistoryFacts(source.path, source.scope, source.thread, selected_ids)
        elif projection == 'timeline':
            result = HistoryMetadata(source.path, source.scope, source.thread)
        else:
            result = RolloutIndex(source.path, source.scope, source.thread)
        if facts:
            result.seed_phase_ids = frozenset(key for part in parts for key in part.seed_phase_ids)
        for name in ('offset', 'fingerprint', 'anchor', 'turn', 'turn_ref', 'file_stamp', 'header_digest',
                     'mode', 'revision', 'generation', 'read_complete', 'source', 'source_epoch'):
            setattr(result, name, getattr(source, name))
        result.agent_metadata = source.agent_metadata
        result.quality = set(source.quality)
        for index in parts:
            for name in ('turns', 'phases', 'refs', 'outputs', 'relations'):
                getattr(result, name).update(getattr(index, name))
            result.quality.update(index.quality)
        return cls._view(result)

    @staticmethod
    def _view(index, turn=None):
        result = copy(index)
        phases = {k: p for k, p in index.phases.items() if turn is None or p['turn_id'] == turn}
        result.phases = deepcopy(phases)
        result.turns = deepcopy({k: t for k, t in index.turns.items() if turn is None or t['turn_id'] == turn})
        result.refs = {k: r for k, r in index.refs.items() if k in phases}  # immutable HistoryReference
        result.outputs = {k: r for k, r in index.outputs.items() if turn is None or k[0] == turn}
        result.relations = deepcopy({k: r for k, r in index.relations.items() if turn is None or
            r['parent_turn_id'] == turn or r['child_thread_id'] == index.thread})
        result.agent_metadata = deepcopy(index.agent_metadata)
        result.quality = set(index.quality)
        return result

    def invalidate(self, scope, thread):
        with self.lock:
            for key in list(self.indices):
                if (key.scope, key.thread) == (scope, thread):
                    self.indices.pop(key, None); self.weights.pop(key, None)
            self._advance_source((scope, thread))
            self._publish_stats()

    def _advance_source(self, key):
        self.source_sequence += 1
        values = {k: v for k, v in self.source_epochs.items() if k != key}
        values[key] = self.source_sequence
        if len(values) > 1024:
            values.pop(next(iter(values)))
        self.source_epochs = values

    def diagnostics(self):
        # The event loop must never wait for this provider's JSONL reader lock.
        # A frozen replacement gives either complete prior or complete new stats.
        return asdict(self._stats)

    def _publish_stats(self):
        self._stats = HistoryCacheStats(len(self.indices), sum(self.weights[k][1] for k in self.indices),
            self.max_cache_bytes, self.evictions, time.monotonic())

    def _budget(self, key, index):
        version = (index.revision, index.generation)
        if self.weights.get(key, (None,))[0] != version:
            weight = retained_bytes(index.turns, index.phases, index.refs, index.outputs,
                                    index.relations, index.agent_metadata, index.quality,
                                    getattr(index, 'ranges', {}), getattr(index, 'spawn_calls', set()),
                                    getattr(index, 'phase_catalogue', {}), getattr(index, 'selected_ids', frozenset()),
                                    getattr(index, 'seed_phase_ids', frozenset()),
                                    getattr(index, 'request_rates', None),
                                    getattr(index, 'source', None),
                                    getattr(index, 'selection', None), getattr(index, 'hint_signature', None))
            self.weights[key] = (version, weight)
        if self.weights[key][1] > self.max_cache_bytes:
            # A selection that cannot be admitted must not first flush the
            # useful working set. The caller can still read its detached view.
            self.indices.pop(key)
            self.weights.pop(key)
            self.evictions += 1
            self._publish_stats()
            return
        total = sum(self.weights[k][1] for k in self.indices)
        while self.indices:
            sources = [k for k in self.indices if k.kind in ('full', 'directory')]
            parts = [k for k in self.indices if k.kind in ('turn', 'facts', 'detail', 'timeline')]
            candidates = (sources if len(sources) > self.capacity else
                          parts if len(parts) > self.max_turn_indices else
                          ([k for k in self.indices if k.kind == 'directory'] or list(self.indices))
                          if total > self.max_cache_bytes else [])
            if not candidates:
                break
            evicted = candidates[0]
            self.indices.pop(evicted)
            total -= self.weights.pop(evicted)[1]
            self.evictions += 1
        self._publish_stats()

    def reconciliation(self, scope, thread, turns, *, items=None):
        """Small, content-free summary for the event loop; all I/O stays here."""
        with self.lock:
            selected = set(turns)
            required = None if items is None else self._item_phases(scope, thread, items)
            index = self._cached_selection(scope, thread, selected, required_ids=required)
            if index is not None:
                return self._summary(index, selected, items)
            for attempt in range(2):
                directory = self.index(scope, thread, directory_only=True)
                if directory is None:
                    return {'scope_id': scope, 'thread_id': thread, 'quality': ['history_unavailable']}
                if not selected and directory.turns:
                    selected.add(max(directory.turns.values(), key=lambda t: t.get('updated_ms') or 0)['turn_id'])
                try:
                    index = (self.turn_view(directory, selected) if items is None else
                             self._facts_view(directory, selected, required))
                    break
                except HistoryChanged:
                    if attempt: raise
            return self._summary(index, selected, items)

    @staticmethod
    def _item_phases(scope, thread, items):
        result = {}
        for turn, item in items:
            result.setdefault(turn, set()).update(phase_id(scope, thread, turn, namespace, item)
                                                  for namespace in ('model', 'input', 'canonical'))
        return result

    def _facts_view(self, directory, turns, required):
        parts = []
        for turn in turns:
            seeds = frozenset(directory.seed_ids(turn))
            wanted = required.get(turn, set()) | seeds
            key = HistoryCacheKey(directory.scope, directory.thread, 'facts', turn)
            index = self.indices.get(key)
            if (index is None or index.file_stamp != directory.file_stamp or
                    index.generation != directory.generation or not wanted <= index.selected_ids):
                try:
                    index = directory.load_facts(turn, wanted)
                except HistoryChanged:
                    self.invalidate(directory.scope, directory.thread)
                    raise
                index.seed_phase_ids = seeds
                self.indices[key] = index
            self.indices.move_to_end(key)
            self._budget(key, index)
            parts.append(index)
        return self._selection_view(directory, parts, projection='facts')

    @staticmethod
    def _summary(index, selected, items=None):
        phases = [p for p in index.phases.values() if p['turn_id'] in selected and
                  (items is None or (p['turn_id'], p['item_id']) in items or p['id'] in index.seed_phase_ids)]
        result = {'scope_id': index.scope, 'thread_id': index.thread,
                'revision': index.revision, 'generation': index.generation, 'fingerprint': index.fingerprint,
                'source_epoch': index.source_epoch,
                'read_complete': index.read_complete,
                'requested_turns': sorted(selected),
                'quality': sorted(index.quality | (set() if index.read_complete else {'history_incomplete'})),
                'turns': deepcopy([t for t in index.turns.values() if t['turn_id'] in selected]),
                'phases': deepcopy(phases)}
        if index.source and index.source.branched and index.read_complete and not index.quality:
            result['source_absent_turns'] = sorted(selected-set(index.turns))
        if items is not None:
            result['phase_selection'] = [list(pair) for pair in sorted(items) if pair[0] in selected]
        return result

    def hint(self, scope, thread, value):
        with self.lock:
            self.resolver.hint(scope, thread, value)
            self.invalidate(scope, thread)

    def configure_source(self, scope, thread, epoch, hint):
        with self.lock:
            key = scope, thread
            old = self.input_versions.get(key)
            value = epoch, tuple(hint)
            if old is not None and (old == value or old[0] > epoch):
                return
            self.input_versions[key] = value
            self.input_versions.move_to_end(key)
            while len(self.input_versions) > 1024:
                self.input_versions.popitem(last=False)
            self.hint(scope, thread, dict(hint))
            self._advance_source(key)

    def _source(self, scope, thread):
        key = scope, thread
        try:
            source = self.resolver.resolve(scope, thread)
        except (OSError, ValueError):
            self.invalidate(scope, thread)
            self.sources.pop(key, None)
            raise
        old = self.sources.get(key)
        if old is not None and (source is None or old != source.generation):
            directories = [(k, v) for k, v in self.indices.items() if
                           (k.scope, k.thread) == key and k.kind in ('full', 'directory') and
                           source is not None and v.path == source.path]
            self.invalidate(scope, thread)
            # Refresh existing directory objects in place; narrow views were invalidated.
            for cache_key, index in directories:
                self.indices[cache_key] = index
        if old is None or source is None or old != source.generation:
            self._advance_source(key)
        if source is None:
            self.sources.pop(key, None)
        else:
            self.sources[key] = source.generation
            self.sources.move_to_end(key)
            while len(self.sources) > 512:
                self.sources.popitem(last=False)
        return source

    def index(self, scope, thread, *, directory_only=False):
        if not isinstance(thread, str) or not thread or any(c in thread for c in ('/', '\\', '\x00')):
            raise ValueError('invalid_thread_identity')
        if not Path(scope).expanduser().is_absolute():
            return None
        key = HistoryCacheKey(scope, thread, 'directory' if directory_only else 'full')
        missing_key = (scope, thread)
        with self.lock:
            source = self._source(scope, thread)
            if source is None:
                self.missing[missing_key] = time.monotonic()
                self.missing.move_to_end(missing_key)
                while len(self.missing) > 512:
                    self.missing.popitem(last=False)
                return None
            self.missing.pop(missing_key, None)
            index = self.indices.get(key)
            if index is None:
                index = (HistoryDirectory if directory_only else RolloutIndex)(source.path, scope, thread)
                self.indices[key] = index
            index.source = source
            index.source_epoch = self.source_epochs[(scope, thread)]
            try:
                index.refresh()
            except (OSError, ValueError, TypeError):
                self.invalidate(scope, thread)
                raise
            self.indices.move_to_end(key)
            self._budget(key, index)
            return index
