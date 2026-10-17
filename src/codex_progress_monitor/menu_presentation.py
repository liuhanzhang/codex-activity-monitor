"""Evidence-based menu state. Formatting and animation belong to the GUI."""

from .overview import CONFLICTS, LIVE_KINDS


def menu_presentation(engine, views, total):
    by_id = {(v.get('home') or v['backend'], v['id']): v for v in views}

    def root(view):
        key = (view.get('home') or view['backend'], view['id'])
        seen = set()
        while key not in seen:
            seen.add(key)
            parent = by_id[key].get('parent_thread_id')
            candidate = (key[0], parent)
            if candidate not in by_id:
                return key
            key = candidate
        # Invalid relationships never merge independent conversations.
        return (view.get('home') or view['backend'], view['id'])

    active = [v for v in views if v.get('has_active_work')]
    count = len({root(v) for v in active})

    def result(mode, reason):
        return dict(version=1, mode=mode, reason=reason, active_conversation_count=count)

    if total['coverage'] != 'complete':
        return result('unavailable', 'coverage_incomplete')
    if any(not v.get('connected') or CONFLICTS.intersection(v.get('quality', ())) or
           v.get('display_message', {}).get('key') in ('activity.unknown', 'activity.disconnected')
           for v in active):
        return result('unavailable', 'activity_unavailable')
    if count > 1:
        return result('speed', 'multi_conversation')
    if not active:
        return result('speed', 'idle')

    generating = executing = thinking = paused = False
    for view in active:
        thread = engine.threads[(view['backend'], view['id'])]
        blockers = [a for a in thread.activities.values() if a.active and a.kind in ('input', 'approval')]
        blocked = {a.target_item_id for a in blockers if a.target_item_id}
        visible = {a['id'] for a in view['activities'] if a.get('display_visible')}
        paused |= bool(blockers or thread.active_flags or thread.goal_status in
                       ('paused', 'blocked', 'usageLimited', 'budgetLimited'))
        for activity in thread.activities.values():
            if not activity.active or activity.mirrored or activity.id not in visible:
                continue
            if activity.id in blocked and activity.turn_bound:
                continue
            # A live stream remains generation during short pauses. Completed
            # streams and their trailing rate window cannot hold this state.
            generating |= activity.kind in ('text', 'plan', 'tool_input')
            generating |= any(
                stream is not None and stream.kind in LIVE_KINDS and
                stream.ended_at is None and stream.last_delta_at is not None
                for stream in (engine.streams.get(key) for key in activity.stream_keys))
            executing |= activity.kind == 'tool' and not activity.startup_pending and activity.started_at is not None
            thinking |= activity.kind == 'reasoning' and activity.started_at is not None

    if generating:
        return result('speed', 'generating')
    if executing:
        return result('executing', 'executing')
    if thinking:
        return result('thinking', 'reasoning')
    return result('speed', 'paused' if paused else 'waiting')
