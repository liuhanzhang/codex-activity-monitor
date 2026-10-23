"""Aggregate independently observed live streams; never sum presentation rows."""

import math
from .agent_relations import parent_edges

LIVE_KINDS = frozenset(('text', 'plan', 'summary', 'tool_input'))
CONFLICTS = frozenset(('multiple_active_backends', 'identity_conflict', 'system_error', 'observation_gap'))


def monitored_threads(views):
    """Match the GUI chat scope: exclude only explicitly ephemeral threads."""
    return [view for view in views if view.get('ephemeral') is not True]


def overview(engine, views, now):
    views = monitored_threads(views)
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
                if stream is None or stream.kind not in LIVE_KINDS or stream.ended_at is not None or stream.ended_by_history:
                    continue
                rate = stream.rate(now)
                if rate is None or not math.isfinite(rate) or rate < 0:
                    unknown += 1
                    quality.add(stream.quality if stream.quality != 'complete' else 'stream_pending')
                else:
                    rates.append(rate)
                    estimated |= stream.estimated
    if engine.pending_generation_count:
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
    edges = parent_edges(views)
    for view in views:
        key = (view.get('home') or view['backend'], view['id'])
        previous = view.pop('parent_thread_id', None)
        if key in edges:
            view['parent_thread_id'] = edges[key]
        elif previous:
            view['parent_quality'] = sorted(set(view.get('parent_quality', [])) | {'parent_identity_conflict'})
