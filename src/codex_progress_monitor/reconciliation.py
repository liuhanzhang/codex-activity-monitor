"""JSONL reconciliation: background reads and event-loop-owned fact application."""

from collections import OrderedDict
from pathlib import Path
import threading
import time

from .events import TOOL_TYPES
from .history import HistoryProvider
from .state import Activity
from .timeline import merge_phase, retired_model_supplement, settled_model_items


class HistoryRefresh:
    def __init__(self, provider=None, capacity=128):
        self.provider = provider or HistoryProvider(capacity=64)
        self.capacity = capacity
        self.pending = OrderedDict()
        self.ready = OrderedDict()
        self.last_read = {}
        self.cursor = 0
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.stopping = False
        self.worker = threading.Thread(target=self._run, daemon=True, name='history-refresh')
        self.worker.start()

    def schedule(self, requests, now=None):
        now = time.monotonic() if now is None else now
        requests = [r for r in requests if isinstance(r[0], str) and Path(r[0]).is_absolute()]
        with self.lock:
            keys = {(r[0], r[1]) for r in requests}
            self.last_read = {k: v for k, v in self.last_read.items() if k in keys}
            self.pending = OrderedDict((k, v) for k, v in self.pending.items() if k in keys)
            start = self.cursor % len(requests) if requests else 0
            ordered = requests[start:] + requests[:start]
            for offset, (scope, thread, turns, urgent) in enumerate(ordered):
                key = (scope, thread)
                # Unresolved work is polled each second. Completed work gets
                # bounded fast retries, then a slower refresh until it expires.
                last, first = self.last_read.get(key, (0, now))
                interval = 1 if urgent or now - first < 30 else 10
                if now - last < interval:
                    continue
                if key in self.pending:
                    self.pending[key].update(turns)
                elif len(self.pending) < self.capacity:
                    self.pending[key] = set(turns)
                else:
                    self.cursor = start + offset
                    break
            else:
                self.cursor = start + 1
        self.wake.set()

    def drain(self):
        with self.lock:
            result = list(self.ready.values())
            self.ready.clear()
            return result

    def _run(self):
        while True:
            self.wake.wait(.5)
            self.wake.clear()
            while True:
                with self.lock:
                    if self.stopping:
                        return
                    if not self.pending:
                        break
                    key, turns = self.pending.popitem(last=False)
                    now = time.monotonic()
                    self.last_read[key] = (now, self.last_read.get(key, (0, now))[1])
                try:
                    result = self.provider.reconciliation(*key, turns)
                except (OSError, ValueError, TypeError, KeyError) as error:
                    result = {'scope_id': key[0], 'thread_id': key[1],
                              'quality': ['history_unavailable'], 'error': type(error).__name__}
                with self.lock:
                    self.ready[key] = result
                    self.ready.move_to_end(key)
                    while len(self.ready) > self.capacity:
                        self.ready.popitem(last=False)

    def close(self):
        with self.lock:
            self.stopping = True
        self.wake.set()
        self.worker.join(timeout=1)


def apply_history(engine, summary):
    """Apply persisted facts without replaying them through usage or delta intake."""
    scope, identity = summary['scope_id'], summary['thread_id']
    threads = [t for t in engine.threads.values() if t.id == identity and
               engine.backends.get(t.backend, {}).get('home') == scope]
    for thread in threads:
        thread.history_quality = set(summary.get('quality', []))
    if not threads or 'revision' not in summary:
        return
    version_key = (scope, identity)
    previous = engine.history_versions.get(version_key)
    version = (summary.get('generation'), summary['revision'])
    if previous and previous[0] == version[0] and previous[1] > version[1]:
        return
    # One source generation applies to every live backend for this persistent
    # identity. Advancing it inside the loop hides replacements from later owners.
    engine.history_versions[version_key] = version
    replaced = previous is not None and previous[0] != version[0]
    for thread in threads:
        phases = summary.get('phases', [])
        terminal_ids = {p['id'] for p in phases if p.get('history_terminal')}
        for activity in thread.activities.values():
            if (activity.history and activity.turn_id in summary.get('requested_turns', []) and
                    activity.history['id'] not in terminal_ids):
                activity.history = None
                thread.history_quality.add('history_changed')
                if activity.status not in ('completed', 'failed', 'interrupted', 'declined'):
                    activity.status = 'unknown'
                    activity.observation_lost = True
                    activity.ended_at = None
                    thread.history_progress_ms = None
                    for key in activity.stream_keys:
                        if key in engine.streams:
                            engine.streams[key].ended_by_history = False
                            engine.streams[key].quality = 'observation_gap'
        source_turns = {t['turn_id']: t for t in summary.get('turns', [])}
        current = source_turns.get(thread.turn_id)
        if (replaced and thread.history_turn and thread.history_turn['turn_id'] == thread.turn_id and
                (not current or current['status'] not in ('completed', 'failed', 'interrupted'))):
            observed = thread.observed_turn
            thread.status = (observed[1] if observed and observed[0] == thread.turn_id and
                             observed[1] in ('completed', 'failed', 'interrupted') else 'unknown')
            if thread.status == 'unknown':
                engine.ended_turns.pop((thread.backend, thread.turn_id), None)
            thread.history_turn = None
            thread.history_progress_ms = None
            thread.history_quality.add('history_changed')
        settled = settled_model_items(source_turns.values(), phases,
                                      read_complete=summary.get('read_complete', False), quality=summary.get('quality', []))
        if settled is not None:
            for activity in thread.activities.values():
                if activity.turn_id not in summary.get('requested_turns', []):
                    continue
                item_type = activity.item_type or engine.items.get((thread.backend, activity.id), {}).get('type')
                activity.history_retired = retired_model_supplement(scope, identity, activity.turn_id,
                                                                   activity.id, item_type, settled)
                if activity.history_retired:
                    for key in activity.stream_keys:
                        if key in engine.streams:
                            engine.streams[key].close_from_history()
        if thread.turn_id is None and source_turns:
            thread.turn_id = max(source_turns.values(), key=lambda t: t.get('updated_ms') or 0)['turn_id']
        # Seed only recent facts for the selected/current turn. The full
        # transcript remains in JSONL and is read lazily by timeline queries.
        seeds = [p for p in phases if p.get('history_terminal') and p['turn_id'] == thread.turn_id and
                 p['item_type'] in TOOL_TYPES | {'agentMessage', 'reasoning', 'plan', 'contextCompaction'}][-20:]
        for fact in seeds:
            if fact['item_id'] in thread.activities:
                continue
            kind = {'agentMessage': 'text', 'reasoning': 'reasoning', 'plan': 'plan'}.get(fact['item_type'], 'tool')
            activity = Activity(fact['item_id'], fact['turn_id'], kind, fact['label'], None,
                                status='unknown', item_type=fact['item_type'])
            activity.tool_name = fact.get('tool_name')
            thread.activities[activity.id] = activity
        for fact in phases:
            activity = thread.activities.get(fact['item_id'])
            if not activity or activity.turn_id != fact['turn_id'] or not fact.get('history_terminal'):
                continue
            live_type = activity.item_type or engine.items.get((thread.backend, activity.id), {}).get('type')
            live_type = {'message': 'agentMessage'}.get(live_type, live_type)
            if live_type != fact['item_type']:
                continue
            if (activity.status in ('completed', 'failed', 'interrupted', 'declined') and
                    activity.status != fact['status']):
                thread.history_quality.add('lifecycle_conflict')
            activity.history = merge_phase(None if replaced else activity.history, fact)
            thread.history_quality.update(activity.history.get('quality', []))
            for key in activity.stream_keys:
                stream = engine.streams.get(key)
                if stream:
                    stream.close_from_history()
        current = source_turns.get(thread.turn_id)
        if current and current['status'] in ('completed', 'failed', 'interrupted'):
            if thread.status in ('completed', 'failed', 'interrupted') and thread.status != current['status']:
                thread.history_quality.add('lifecycle_conflict')
            thread.status = current['status']
            thread.history_turn = current
            engine.ended_turns[(thread.backend, thread.turn_id)] = current['status']
            engine.turns[(thread.backend, thread.turn_id)] = thread.id
            for activity in thread.activities.values():
                if activity.turn_id == thread.turn_id and activity.turn_bound and (activity.active or activity.unresolved):
                    # A turn end closes turn-bound context but cannot supply
                    # the missing item's own terminal state or time.
                    activity.status = 'unknown'
                    activity.observation_lost = False
                    thread.history_quality.add('missing_item_end')
                    for key in activity.stream_keys:
                        if key in engine.streams:
                            engine.streams[key].close_from_history()
            if not any(a.active or a.unresolved for a in thread.activities.values()):
                ends = [p['end_ms'] for p in phases if p.get('history_terminal') and p.get('end_ms') is not None]
                if current.get('end_ms') is not None:
                    ends.append(current['end_ms'])
                if ends:
                    thread.history_progress_ms = max(ends)
        elif current and current['status'] == 'active' and thread.status == 'idle':
            thread.status = 'unknown'
            thread.quality.add('observation_gap')
    if engine.timeline:
        engine.timeline.apply_history(summary)
