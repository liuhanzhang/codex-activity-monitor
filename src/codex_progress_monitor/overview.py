"""Aggregate independently observed live streams; never sum presentation rows."""

import math
from .agent_relations import parent_edges

LIVE_KINDS = frozenset(('text', 'plan', 'summary', 'tool_input'))
CONFLICTS = frozenset(('multiple_active_backends', 'identity_conflict', 'system_error', 'observation_gap'))
IDENTITY_CONFLICTS = CONFLICTS - {'observation_gap'}


def monitored_threads(views):
    """Match the GUI chat scope: exclude only explicitly ephemeral threads."""
    return [view for view in views if view.get('ephemeral') is not True]


def thread_observation(engine, view, now, seen=None):
    """Judge current independent streams; historical thread quality is provenance."""
    thread = engine.threads[(view['backend'], view['id'])]
    seen = set() if seen is None else seen
    quality = set()
    rates = []
    unknown = sampling = 0
    estimated = False
    working = view.get('has_active_work') or view.get('status') == 'unknown'
    conflicts = IDENTITY_CONFLICTS.intersection(view.get('quality', ()))
    if working and (not view.get('connected') or conflicts):
        return dict(rates=[], unknown=1, sampling=0, estimated=False,
                    quality=set(conflicts) | {'thread_unavailable'} |
                        ({'disconnected'} if not view.get('connected') else set()))
    if not view.get('connected'):
        return dict(rates=[], unknown=0, sampling=0, estimated=False, quality=set())
    visible = {a['id'] for a in view.get('activities', []) if a.get('display_visible')}
    current_evidence = False
    generation_evidence = False
    gap_at = engine.backends[view['backend']].get('gap_at')
    for activity in thread.activities.values():
        if activity.mirrored or not (activity.active or activity.unresolved):
            continue
        if activity.active and gap_at is not None and (
                (activity.started_at is not None and activity.started_at > gap_at) or activity.last_progress_at > gap_at):
            current_evidence = True
        generating = activity.kind in ('text', 'plan', 'tool_input')
        if generating and not activity.stream_keys and (activity.id in visible or activity.unresolved):
            unknown += 1
            sampling += not activity.unresolved
            quality.add('observation_gap' if activity.unresolved else 'stream_pending')
            current_evidence |= not activity.unresolved
            generation_evidence = True
        for key in activity.stream_keys:
            if key in seen:
                continue
            seen.add(key)
            stream = engine.streams.get(key)
            if stream is None or stream.kind not in LIVE_KINDS or stream.ended_at is not None or stream.ended_by_history:
                continue
            current_evidence = True
            generation_evidence = True
            reading = stream.rate_reading(now)
            rate = reading.value
            if rate is None or not math.isfinite(rate) or rate < 0:
                unknown += 1
                sampling += reading.status == 'sampling'
                quality.add('stream_pending' if reading.status == 'sampling' else reading.reason or 'stream_unavailable')
            else:
                rates.append(rate)
                estimated |= stream.estimated
    if working and ((not thread.turn_start_observed and not generation_evidence) or (not current_evidence and (
            'observation_gap' in thread.quality or view.get('status') == 'unknown'))):
        unknown += 1
        quality.add('observation_gap' if 'observation_gap' in view.get('quality', ()) else 'thread_unavailable')
    return dict(rates=rates, unknown=unknown, sampling=sampling, estimated=estimated, quality=quality)


def overview(engine, views, now):
    quality, rates, seen = set(), [], set()
    unknown = sampling = 0
    estimated = False
    for view in views:
        ephemeral = view.get('ephemeral') is True
        observation = thread_observation(engine, view, now, None if ephemeral else seen)
        values = observation['rates']
        missing = observation['unknown']
        reasons = observation['quality']
        view.update(generation_rate=sum(values) if values else None,
                    generation_rate_status='ready' if values else
                        'sampling' if missing and missing == observation['sampling'] else
                        'unavailable' if missing else 'inactive',
                    generation_rate_coverage=('partial' if values else 'unavailable') if missing else 'complete',
                    generation_rate_issues=sorted(reasons - {'stream_pending'}),
                    generation_estimated=observation['estimated'])
        if ephemeral:
            continue
        rates.extend(values)
        unknown += missing
        sampling += observation['sampling']
        quality.update(reasons)
        estimated |= observation['estimated']
    if engine.pending_generation_count:
        unknown += 1
        quality.add('unassociated_events')
    if engine.identity_budget.sealed:
        unknown += 1
        quality.add('identity_budget')
    connected = any(b.get('connected', False) for b in engine.backends.values())
    if not connected:
        quality.add('no_backend')
    coverage = ('partial' if rates else 'unavailable') if unknown or not connected else 'complete'
    rate_status = ('ready' if rates else 'sampling' if connected and unknown == sampling and sampling else
                   'unavailable' if unknown or not connected else 'ready')
    return {'version': 1, 'scope': 'observed_local', 'coverage': coverage,
            'rate_status': rate_status,
            'generation_rate': sum(rates) if coverage == 'complete' else None,
            'observed_subtotal': sum(rates) if rates else (0.0 if coverage == 'complete' else None),
            'estimated': estimated, 'active_thread_count': sum(bool(v.get('has_active_work')) for v in monitored_threads(views)),
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
