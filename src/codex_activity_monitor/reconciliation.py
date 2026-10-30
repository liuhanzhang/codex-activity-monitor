"""Event-loop-owned application of detached, authoritative JSONL facts."""

from .history_contract import HistoryCoverage
from .state import Activity
from .timeline import HISTORY_SEED_LIMIT, HISTORY_SEED_TYPES, merge_phase, retired_model_supplement, settled_model_items


def apply_history(engine, summary):
    """Apply persisted facts without replaying them through usage or delta intake."""
    scope, identity = summary['scope_id'], summary['thread_id']
    key = scope, identity
    if ('request_epoch' in summary and
            summary['request_epoch'] != engine.history_inputs.get(key, (0, ()))[0]):
        return
    if 'source_epoch' in summary:
        if summary['source_epoch'] < engine.history_source_epochs.get(key, 0):
            return
        engine.history_source_epochs[key] = summary['source_epoch']
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
    read_complete = summary.get('read_complete', False)
    coverage = HistoryCoverage.from_summary(summary)
    for thread in threads:
        phases = summary.get('phases', [])
        terminal_ids = {p['id'] for p in phases if p.get('history_terminal')}
        for activity in thread.activities.values():
            if (read_complete and activity.history and coverage.covers(activity.turn_id, activity.id) and
                    activity.history['id'] not in terminal_ids):
                activity.history = None
                thread.history_quality.add('history_changed')
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
        if (read_complete and thread.turn_id in summary.get('requested_turns', []) and
                thread.history_turn and thread.history_turn['turn_id'] == thread.turn_id and
                (not current or current['status'] not in ('completed', 'failed', 'interrupted'))):
            thread.status = 'unknown'
            engine.ended_turns.pop((thread.backend, thread.turn_id), None)
            thread.history_turn = None
            thread.history_progress_ms = None
            thread.history_quality.add('history_changed')
        settled = settled_model_items(source_turns.values(), phases,
                                      read_complete=summary.get('read_complete', False), quality=summary.get('quality', []),
                                      absent_turns=[(scope, identity, turn) for turn in summary.get('source_absent_turns', [])])
        if settled is not None:
            for activity in thread.activities.values():
                if not coverage.covers(activity.turn_id, activity.id):
                    continue
                if activity.active and activity.turn_id in summary.get('source_absent_turns', []):
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
                 p['item_type'] in HISTORY_SEED_TYPES][-HISTORY_SEED_LIMIT:]
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
            activity.history_retired = False
            if (activity.status in ('completed', 'failed', 'interrupted', 'declined') and
                    activity.status != fact['status']):
                thread.history_quality.add('lifecycle_conflict')
            # Each fact is already merged from the current verified source.
            # A partial result can advance the source generation before another
            # item's result arrives; never borrow that item's old-source bounds.
            activity.history = merge_phase(None, fact)
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
