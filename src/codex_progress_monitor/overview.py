"""Aggregate independently observed live streams; never sum presentation rows."""

import math

LIVE_KINDS = frozenset(('text', 'plan', 'summary', 'tool_input'))
CONFLICTS = frozenset(('multiple_active_backends', 'identity_conflict', 'system_error', 'observation_gap'))


def overview(engine, views, now):
    quality = set()
    rates = []
    unknown = 0
    estimated = False
    seen = set()
    for view in views:
        thread = engine.threads[(view['backend'], view['id'])]
        uncertain = bool(CONFLICTS.intersection(view.get('quality', ())))
        if (not view.get('connected', False) or uncertain) and (
                view.get('has_active_work') or view.get('status') == 'unknown'):
            unknown += 1
            quality.add('thread_unavailable')
            quality.update(CONFLICTS.intersection(view.get('quality', ())))
            continue
        if not view.get('connected', False):
            continue
        for activity in thread.activities.values():
            if not activity.active or activity.mirrored:
                continue
            for key in activity.stream_keys:
                if key in seen:
                    continue
                seen.add(key)
                stream = engine.streams.get(key)
                if stream is None or stream.kind not in LIVE_KINDS or stream.ended_at is not None:
                    continue
                rate = stream.rate(now)
                if rate is None or not math.isfinite(rate) or rate < 0:
                    unknown += 1
                    quality.add(stream.quality if stream.quality != 'complete' else 'stream_pending')
                else:
                    rates.append(rate)
                    estimated |= stream.estimated
    if engine.pending_count:
        unknown += 1
        quality.add('unassociated_events')
    connected = any(b.get('connected', False) for b in engine.backends.values())
    if not connected:
        quality.add('no_backend')
    coverage = ('partial' if rates else 'unavailable') if unknown or not connected else 'complete'
    return {'version': 1, 'scope': 'observed_local', 'coverage': coverage,
            'generation_rate': sum(rates) if coverage == 'complete' else None,
            'observed_subtotal': sum(rates) if rates else (0.0 if coverage == 'complete' else None),
            'estimated': estimated, 'active_thread_count': sum(bool(v.get('has_active_work')) for v in views),
            'known_stream_count': len(rates), 'unknown_stream_count': unknown, 'quality': sorted(quality)}


def annotate_parents(views):
    """Use explicit child identities only; ambiguous and cyclic edges stay ungrouped."""
    by_id = {(v.get('home') or v['backend'], v['id']): v for v in views}
    candidates = {}
    for parent in views:
        scope = parent.get('home') or parent['backend']
        for child in parent.get('subagents', {}):
            if (scope, child) in by_id and child != parent['id']:
                candidates.setdefault((scope, child), set()).add(parent['id'])
    edges = {key: next(iter(parents)) for key, parents in candidates.items() if len(parents) == 1}
    for key, parent in edges.items():
        visited = {key[1]}
        cursor = parent
        while cursor not in visited and (key[0], cursor) in edges:
            visited.add(cursor)
            cursor = edges[(key[0], cursor)]
        if cursor not in visited:
            by_id[key]['parent_thread_id'] = parent
