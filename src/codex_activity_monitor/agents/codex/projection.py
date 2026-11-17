"""Live Codex JSON-RPC protocol, upstream Responses SSE and timeline phase projection."""

import json

from .events import DELTA_KINDS, SERVER_REQUESTS, TOOL_TYPES, Event
from .progress_state import handle_protocol, resolve_request
from codex_activity_monitor.core.agent_relations import SPAWN_OPERATIONS
from codex_activity_monitor.core.identity_budget import observation_identities
from codex_activity_monitor.core.metrics import TextStream
from codex_activity_monitor.core.tool_labels import is_code_exec, tool_name
from codex_activity_monitor.i18n.i18n import PHASE_KEYS, phase_message, render_message, tr
from codex_activity_monitor.timeline.timeline import LABELS, edit_input, identity, item_phase, phase_id, status, valid_ms

PHASES = {phase: render_message(phase_message(phase)) for phase in PHASE_KEYS}


def ingest_event(engine, row):
    event = row if isinstance(row, Event) else Event.from_row(row)
    engine.clock = max(engine.clock, event.at)
    if not engine.admit_identities(event.backend, *observation_identities(event)[1:], at=event.at):
        return
    engine.backends.setdefault(event.backend, {'connected': True, 'model': 'unknown', 'last_seen': event.at})
    engine.backends[event.backend]['last_seen'] = event.at
    if event.direction == 'lifecycle':
        handle_lifecycle(engine, event)
    elif event.direction in ('protocol_in', 'protocol_out'):
        handle_protocol_event(engine, event)
    elif event.direction == 'upstream':
        handle_upstream(engine, event)
    resolve_pending(engine, event.backend)
    engine.usage.ingest(event, engine)
    engine._observe_timeline(event)


def handle_lifecycle(engine, event):
    kind = event.data.get('type')
    backend = engine.backends[event.backend]
    backend['last_observed_at'] = event.at
    old_drops = backend.get('dropped_packets', 0) - backend.get('unrelated_dropped_packets', 0)
    old_errors = backend.get('parse_errors', 0)
    old_reasons = backend.get('drop_reasons', {})
    if isinstance(event.data.get('drop_reasons'), dict):
        backend.update(drop_reasons=event.data['drop_reasons'], last_send_error=event.data.get('last_send_error'),
                       send_waits=event.data.get('send_waits', 0))
    for key in ('dropped_packets', 'unrelated_dropped_packets', 'parse_errors'):
        if key in event.data:
            backend[key] = event.data[key]
    if kind == 'bridge/heartbeat':
        drops = backend.get('dropped_packets', 0) - backend.get('unrelated_dropped_packets', 0)
        errors = backend.get('parse_errors', 0)
        if drops > old_drops or errors > old_errors:
            kind = 'bridge/gap'
            event.data['type'] = kind
    if kind == 'bridge/started':
        backend.update(connected=True, home=event.data.get('home'), cli=event.data.get('cli'),
                       bridge_pid=event.data.get('bridge_pid'), implementation=event.data.get('implementation'))
        if isinstance(event.data.get('sqlite_home'), str) and len(event.data['sqlite_home']) <= 4096:
            backend['sqlite_home'] = event.data['sqlite_home']
        for field in ('configuration_revision', 'configuration_matches'):
            if field in event.data:
                backend[field] = event.data[field]
    elif kind in ('bridge/gap', 'bridge/reconnected'):
        backend['gap_at'] = event.at
        reasons = [key for key, count in backend.get('drop_reasons', {}).items()
                   if count > old_reasons.get(key, 0) and key != 'oversized_unrelated_diagnostic']
        if backend.get('parse_errors', 0) > old_errors:
            reasons.append('parse_error')
        recent = backend.setdefault('recent_observation_gaps', [])
        recent.append({'at': event.at, 'timestamp': event.wall_time,
                       'reasons': reasons[:16] or [event.data.get('reason') or kind], 'scope': 'backend'})
        del recent[:-16]
        for thread in engine.threads.values():
            if thread.backend == event.backend and (thread.status == 'inProgress' or any(a.active for a in thread.activities.values())):
                thread.quality.add('observation_gap')
                for activity in thread.activities.values():
                    if activity.active:
                        activity.status = 'unknown'
                        activity.observation_lost = True
        for stream in engine.streams.values():
            if stream.key[0] == event.backend and stream.ended_at is None and not stream.ended_by_history:
                stream.interrupt_observation()
        backend['gaps'] = event.data.get('dropped_packets', backend.get('gaps', 0) + 1)
    elif kind in ('bridge/disconnected', 'bridge/stopped'):
        backend['connected'] = False
        backend['ended_at'] = event.at
        if kind == 'bridge/stopped':
            # A transport timeout is not evidence that this backend died.
            # Only the wrapper's explicit final stop retires the instance.
            engine.identity_budget.retire('backend', event.backend, event.backend)
        for thread in engine.threads.values():
            if thread.backend != event.backend:
                continue
            if thread.status == 'inProgress' or any(a.active for a in thread.activities.values()):
                thread.status = 'unknown'
                thread.quality.add('disconnected')
                for activity in thread.activities.values():
                    if activity.active:
                        activity.status = 'unknown'
                        activity.observation_lost = True
            for stream in engine.streams.values():
                if stream.key[0] == event.backend and stream.ended_at is None and not stream.ended_by_history:
                    stream.interrupt_observation('disconnected')


def handle_protocol_event(engine, event):
    value, backend, at = event.data, event.backend, event.at
    method, params = value.get('method'), value.get('params', {})
    request_key = (backend, value.get('id'))
    if event.direction == 'protocol_in':
        if method and value.get('id') is not None:
            engine.requests[request_key] = (method, params)
        elif value.get('id') is not None:
            resolve_request(engine, backend, value.get('id'), at, value.get('result', {}).get('decision'))
        return
    if not method:
        request = engine.requests.pop(request_key, None)
        result = value.get('result', {})
        if request and request[0] == 'config/read':
            model = result.get('config', {}).get('model')
            if model:
                engine.backends[backend]['model'] = model
        if isinstance(result.get('thread'), dict):
            thread = engine._metadata(backend, result['thread'])
            if thread and request and request[0] == 'thread/start':
                identity_key = engine.usage.identity(backend, thread.id, engine)
                engine.usage.state(identity_key, at)['new_thread'] = not bool(result['thread'].get('turns'))
                if thread.ephemeral is None and isinstance(request[1].get('ephemeral'), bool):
                    thread.ephemeral = request[1]['ephemeral']
                thread.thread_source = thread.thread_source or request[1].get('threadSource')
            if thread and result.get('model'):
                thread.model = result['model']
            for turn in result['thread'].get('turns', []):
                if not engine._map_turn(backend, turn.get('id'), thread.id):
                    continue
                if turn.get('status') == 'inProgress':
                    thread.turn_id = turn['id']
                    thread.status = 'inProgress'
                    thread.wait_started_at = at
                    thread.quality.add('joined_in_progress')
        return
    if handle_protocol(engine, event):
        return
    thread_id = params.get('threadId')
    turn_id = params.get('turnId')
    if method == 'thread/started':
        engine._metadata(backend, params.get('thread', {}))
        return
    if method == 'thread/name/updated' and thread_id:
        engine._thread(backend, thread_id).name = params.get('threadName', params.get('name', ''))
        return
    if method in ('thread/settings/updated', 'model/rerouted') and thread_id:
        thread = engine._thread(backend, thread_id)
        model = params.get('threadSettings', {}).get('model') or params.get('toModel') or params.get('model')
        if model:
            thread.model = model
            for key, stream in engine.streams.items():
                if key[0] == backend and engine.turns.get((backend, engine.items.get((backend, key[1]), {}).get('turn_id'))) == thread_id and stream.ended_at is None and not stream.ended_by_history:
                    stream.set_model(model)
        return
    if method in ('turn/started', 'turn/completed'):
        turn_id = params.get('turn', {}).get('id')
    if not thread_id:
        if method == 'serverRequest/resolved':
            engine.approvals.pop((backend, params.get('requestId')), None)
        return
    thread = engine._thread(backend, thread_id)
    if turn_id and not engine._map_turn(backend, turn_id, thread_id):
        return
    if method == 'turn/started':
        if (backend, turn_id) in engine.ended_turns:
            engine.counts['late_terminal_items'] += 1
            return
        if thread.turn_id == turn_id and thread.status == 'inProgress':
            engine.counts['duplicate_lifecycle_ignored'] += 1
            return
        event.turn_started_accepted = True
        for activity in thread.activities.values():
            if activity.turn_id != turn_id and (activity.active or activity.unresolved) and activity.turn_bound:
                activity.status = 'unknown'
                activity.ended_at = at
                for key in activity.stream_keys:
                    stream = engine.streams.get(key)
                    if stream and stream.ended_at is None:
                        stream.ended_at = at
                        stream.quality = 'missing_termination'
                        stream.text = ''
        thread.turn_id, thread.status = turn_id, 'inProgress'
        thread.turn_start_observed = True
        thread.history_turn = None
        thread.history_progress_ms = None
        thread.wait_started_at = thread.last_progress_at = at
        thread.quality.clear()
        thread.active_flags.clear()
        thread.error_status = None
        thread.plan = []
    elif method == 'turn/completed':
        engine.identity_budget.retire('turn', backend, turn_id)
        engine.ended_turns[(backend, turn_id)] = params.get('turn', {}).get('status', 'unknown')
        if thread.turn_id and thread.turn_id != turn_id:
            return
        if thread.turn_id == turn_id and thread.status not in ('idle', 'inProgress', 'unknown'):
            engine.counts['duplicate_lifecycle_ignored'] += 1
            return
        thread.turn_id = turn_id
        thread.status = params.get('turn', {}).get('status', 'unknown')
        thread.last_progress_at = at
        for activity in thread.activities.values():
            if activity.turn_id == turn_id and (activity.active or activity.unresolved):
                if not activity.turn_bound and thread.status == 'completed':
                    continue
                activity.ended_at = at
                activity.status = ('unknown' if activity.kind == 'tool_ready' else 'completed') if thread.status == 'completed' else thread.status
        thread.active_flags.clear()
        for request_key, (owner, item_id) in list(engine.approvals.items()):
            if owner == (backend, thread_id) and thread.activities[item_id].turn_id == turn_id and thread.activities[item_id].turn_bound:
                resolve_request(engine, backend, request_key[1], at)
        for key, stream in engine.streams.items():
            if key[0] == backend and engine.items.get((backend, key[1]), {}).get('turn_id') == turn_id and stream.ended_at is None and not stream.ended_by_history:
                stream.ended_at = at
    elif method in ('item/started', 'item/completed'):
        handle_protocol_item(engine, thread, turn_id, params.get('item', {}), method, at)
    elif method in ('item/agentMessage/delta', 'item/plan/delta', 'item/reasoning/summaryTextDelta'):
        item_id = params.get('itemId')
        kind = 'summary' if 'reasoning' in method else 'plan' if '/plan/' in method else 'text'
        if not handle_delta(engine, event, item_id, kind, params.get('delta', ''), params.get('summaryIndex', 0), source='protocol'):
            queue_unassociated(engine, event, item_id)
    elif method == 'item/commandExecution/outputDelta':
        activity = thread.activities.get(params.get('itemId'))
        if activity and params.get('output_chars', len(params.get('delta', ''))):
            activity.output_chars += params.get('output_chars', len(params.get('delta', '')))
            if activity.ended_at is None:
                activity.kind = 'tool'
                activity.last_progress_at = at
            thread.last_progress_at = at


def handle_protocol_item(engine, thread, turn_id, item, method, at):
    item_id = item.get('id')
    if not item_id:
        return
    key = (thread.backend, item_id)
    if key not in engine.items and engine.identity_budget.contains('item', thread.backend, item_id):
        engine.counts['late_terminal_items'] += 1
        return
    if method == 'item/completed':
        engine.identity_budget.retire('item', thread.backend, item_id)
    known = engine.items.setdefault(key, {'turn_id': turn_id, 'type': item.get('type'), 'upstream': False})
    known.setdefault('started_at', at if method == 'item/started' else None)
    known['turn_id'] = turn_id or known.get('turn_id')
    kind = item.get('type')
    known['mirrored'] = known.get('mirrored', False) or item.get('delivery') == 'async'
    if known['mirrored']:
        previous = thread.activities.get(item_id)
        if previous:
            previous.mirrored = True
            previous.ended_at = at
            previous.status = 'unknown'
            for stream_key in previous.stream_keys:
                stream = engine.streams.get(stream_key)
                if stream:
                    stream.ended_at = at
                    stream.quality = 'mirrored'
                    stream.text = ''
        engine.counts['mirrored_items'] += 1
        return
    if method == 'item/started' and (thread.backend, turn_id) in engine.ended_turns:
        engine.counts['late_terminal_items'] += 1
        return
    if kind in ('userMessage', 'hookPrompt', 'functionCallOutput'):
        known['protocol_completed'] = method == 'item/completed'
        return
    if kind in ('enteredReviewMode', 'exitedReviewMode'):
        if not thread.turn_id or turn_id == thread.turn_id:
            thread.review_mode = kind == 'enteredReviewMode'
        return
    if kind == 'subAgentActivity':
        child = item.get('agentThreadId')
        if child and item.get('kind') == 'started' and any(a.turn_id == turn_id and
                a.call_id == item_id and a.coordination_action == 'spawn' for a in thread.activities.values()):
            engine._assign_parent(thread, child)
        if child in thread.subagents:
            thread.subagents[child] = item.get('kind', 'unknown')
        return
    if kind in ('agentMessage', 'reasoning', 'plan'):
        phase = {'agentMessage': 'text', 'reasoning': 'reasoning', 'plan': 'plan'}[kind]
        activity = engine._activity(thread, item_id, turn_id, phase, PHASES[phase], known.get('started_at'))
    elif kind in ('contextCompaction', 'sleep'):
        phase = 'compaction' if kind == 'contextCompaction' else 'sleep'
        activity = engine._activity(thread, item_id, turn_id, phase, PHASES[phase], known.get('started_at'))
    elif kind in TOOL_TYPES:
        if kind == 'collabAgentToolCall' and item.get('tool') in SPAWN_OPERATIONS:
            for child in item.get('receiverThreadIds', []):
                engine._assign_parent(thread, child)
        label = item.get('command') or '/'.join(str(item.get(k, '')) for k in ('server', 'tool')).strip('/') or item.get('tool') or kind
        confirmed_process = (kind == 'commandExecution' and bool(item.get('processId'))
                             and item.get('source') in ('unifiedExecStartup', 'unifiedExecInteraction'))
        phase = 'tool_ready' if kind == 'commandExecution' and not confirmed_process else 'tool'
        activity = engine._activity(thread, item_id, turn_id, phase, label, known.get('started_at'))
        engine._tool_metadata(activity, item)
        if kind == 'fileChange':
            if method == 'item/started' and 'file_change_started_at' not in known:
                known['file_change_started_at'] = at
                activity.started_at = activity.last_progress_at = at
                thread.last_progress_at = max(thread.last_progress_at, at)
            elif method == 'item/completed' and 'file_change_started_at' not in known:
                activity.started_at = None
            activity.kind = 'tool'
        if confirmed_process:
            activity.turn_bound = False
        if confirmed_process and activity.ended_at is None:
            activity.kind = 'tool'
    else:
        return
    activity.item_type = kind
    if kind == 'plan' and activity.active:
        activity.kind = 'plan'
    if method == 'item/started' and activity.status == 'unknown':
        activity.status = 'inProgress'
        activity.kind = phase
        activity.ended_at = None
    if method == 'item/completed':
        if known.get('protocol_completed'):
            engine.counts['duplicate_lifecycle_ignored'] += 1
            return
        known['protocol_completed'] = True
        activity.status = item.get('status', 'completed')
        activity.ended_at = at
        activity.exit_code = item.get('exitCode')
        thread.last_progress_at = max(thread.last_progress_at, at)
        thread.wait_started_at = at
        for stream_key in activity.stream_keys:
            stream = engine.streams[stream_key]
            # Upstream completion remains the authority for upstream text.
            if stream.source == 'protocol':
                final = item.get('text') if stream.kind in ('text', 'plan') else None
                engine._finish_stream(stream, at, final)


def queue_unassociated(engine, event, item_id):
    cost = len(json.dumps(event.data, ensure_ascii=False).encode())
    if engine.pending_count >= 4096 or engine.pending_bytes + cost > 2 * 1024 * 1024:
        engine.counts['unassociated_dropped'] += 1
        return
    event.pending_bytes = cost
    engine.pending[(event.backend, item_id)].append(event)
    engine.pending_count += 1
    engine.pending_bytes += cost


def resolve_pending(engine, backend):
    for key in list(engine.pending):
        if key[0] != backend:
            continue
        known = engine.items.get(key, {})
        if (backend, known.get('turn_id')) not in engine.turns:
            continue
        rows = engine.pending.pop(key)
        engine.pending_count -= len(rows)
        engine.pending_bytes -= sum(getattr(e, 'pending_bytes', 0) for e in rows)
        for event in rows:
            engine.counts['delayed_mapping'] += 1
            if event.direction == 'upstream':
                handle_upstream(engine, event)
            else:
                handle_protocol_event(engine, event)
            engine._observe_timeline(event)
            if (event.data.get('type') in ('response.output_item.added', 'response.output_item.done')
                    or event.data.get('method') in ('item/started', 'item/completed')):
                engine.usage.item(event, engine)
        completion = engine.pending_ends.pop(key, None)
        if completion is not None:
            handle_upstream(engine, completion)


def handle_upstream(engine, event):
    value, backend, at = event.data, event.backend, event.at
    kind = value.get('type')
    if kind in ('response.completed', 'response.failed', 'response.incomplete'):
        model = value.get('response', {}).get('model')
        if model:
            engine.backends[backend]['model'] = model
        ids = set(value.get('response', {}).get('item_ids', []))
        for item_id in ids:
            key = (backend, item_id)
            if key in engine.pending:
                engine.pending_ends[key] = event
        identity_info = value.get('identity', {})
        for key, stream in engine.streams.items():
            known = engine.items.get((backend, key[1]), {})
            scoped = key[1] in ids if ids else identity_info.get('turn_id') and known.get('turn_id') == identity_info['turn_id']
            if key[0] == backend and scoped and stream.ended_at is None and not stream.ended_by_history:
                engine._finish_stream(stream, at)
                owner = engine.turns.get((backend, known.get('turn_id')))
                activity = engine.threads.get((backend, owner)).activities.get(key[1]) if (backend, owner) in engine.threads else None
                if activity and activity.kind in ('text', 'reasoning', 'plan', 'tool_input'):
                    activity.ended_at = at
                    activity.status = 'unknown' if kind != 'response.completed' else 'completed'
        return
    if kind in ('response.output_item.added', 'response.output_item.done'):
        item = value.get('item', {})
        item_id = item.get('id')
        if not item_id:
            return
        key = (backend, item_id)
        if key not in engine.items and engine.identity_budget.contains('item', backend, item_id):
            engine.counts['late_terminal_items'] += 1
            return
        known = engine.items.setdefault(key, {'started_at': at if kind.endswith('.added') else None})
        if known.get('mirrored'):
            engine.counts['mirrors_ignored'] += 1
            return
        metadata = {**value.get('identity', {}), **item.get('metadata', {})}
        known.update(type=item.get('type'), upstream=True)
        if metadata.get('turn_id'):
            known['turn_id'] = metadata['turn_id']
        turn_id = known.get('turn_id')
        if metadata.get('thread_id') and not engine._map_turn(backend, turn_id, metadata['thread_id']):
            return
        if (backend, turn_id) in engine.conflicted_turns:
            engine.counts['quarantined_events'] += 1
            return
        thread_id = engine.turns.get((backend, turn_id))
        if not thread_id:
            queue_unassociated(engine, event, item_id)
            return
        thread = engine._thread(backend, thread_id)
        if kind.endswith('.done') and known.get('upstream_completed'):
            engine.counts['duplicate_lifecycle_ignored'] += 1
            return
        item_type = item.get('type')
        phases = {'message': 'text', 'reasoning': 'reasoning', 'custom_tool_call': 'tool_input',
                  'function_call': 'tool_input', 'web_search_call': 'tool', 'image_generation_call': 'tool'}
        phase = phases.get(item_type)
        if phase is None:
            return
        terminal = engine.ended_turns.get((backend, turn_id))
        if kind.endswith('.added') and terminal is not None:
            known['started_at'] = None
            engine.counts['late_terminal_items'] += 1
            return
        label = tool_name(item) or (tr('diagnostic.tool_unknown') if phase == 'tool_input' else PHASES[phase])
        activity = engine._activity(thread, item_id, turn_id, phase, label, known['started_at'])
        if phase == 'tool_input':
            if tool_name(item):
                engine._tool_metadata(activity, item)
            if kind.endswith('.done'):
                final = item.get('input', item.get('arguments'))
                if isinstance(final, str) and activity.tool_name:
                    engine._tool_label(activity, final)
        if kind.endswith('.added'):
            if activity.status == 'unknown':
                activity.status = 'inProgress'
                activity.ended_at = None
            # A new model item ends the preparation context; it does not
            # prove that an outer Code Mode cell or a background tool ended.
            if phase in ('reasoning', 'text'):
                for previous in thread.activities.values():
                    if previous.id != item_id and previous.turn_id == turn_id and previous.kind == 'reasoning' and previous.active:
                        previous.ended_at = at
                        previous.status = 'unknown'
                        thread.quality.add('missing_item_end')
                    if previous.id != item_id and previous.turn_id == turn_id and previous.kind == 'tool_ready' and is_code_exec(previous.tool_name):
                        previous.ended_at = at
                        previous.status = 'unknown'
            return
        known['ended_at'] = at
        known['upstream_completed'] = True
        engine.identity_budget.retire('item', backend, item_id)
        if phase == 'tool_input' and activity.item_type == 'fileChange':
            pass  # Input completion must not reopen or end canonical editing.
        elif phase == 'tool_input' and terminal is None:
            activity.kind = 'tool_ready'
            activity.started_at = at
        else:
            activity.ended_at = at
            activity.status = terminal or item.get('status', 'completed')
        # If the collector rejoined after the last delta, the authoritative
        # completed item can still supply a final count, never a live rate.
        finals = []
        if phase == 'text':
            finals = [('text', n, p.get('text', '')) for n, p in enumerate(item.get('content', [])) if p.get('type') == 'output_text']
        elif phase == 'tool_input':
            finals = [('tool_input', 0, item.get('input', item.get('arguments')))]
        elif phase == 'reasoning':
            finals = [('summary', n, p.get('text', '')) for n, p in enumerate(item.get('summary', []))]
        for stream_kind, part, final in finals:
            stream_key = (backend, item_id, stream_kind, part)
            if final is not None and stream_key not in engine.streams:
                stream = TextStream(stream_key, stream_kind, 'upstream', thread.model, at, quality='reconciled')
                engine.streams[stream_key] = stream
                activity.stream_keys.append(stream_key)
        for stream_key in activity.stream_keys:
            stream = engine.streams[stream_key]
            part = stream_key[-1]
            final = None
            if stream.kind == 'text':
                content = item.get('content', [])
                if part < len(content) and content[part].get('type') == 'output_text':
                    final = content[part].get('text', '')
            elif stream.kind == 'tool_input':
                final = item.get('input', item.get('arguments'))
            elif stream.kind == 'summary':
                summary = item.get('summary', [])
                if part < len(summary):
                    final = summary[part].get('text', '')
            engine._finish_stream(stream, at, final)
        thread.last_progress_at = max(thread.last_progress_at, at)
        thread.wait_started_at = at
    elif kind in DELTA_KINDS:
        item_id = value.get('item_id')
        part = value.get('summary_index', value.get('content_index', 0))
        if not handle_delta(engine, event, item_id, DELTA_KINDS[kind], value.get('delta', ''), part,
                            source='upstream', sequence=value.get('sequence_number')):
            queue_unassociated(engine, event, item_id)
    elif kind == 'response.reasoning_summary_part.added':
        known = engine.items.get((backend, value.get('item_id')), {})
        thread_id = engine.turns.get((backend, known.get('turn_id')))
        if thread_id:
            activity = engine._thread(backend, thread_id).activities.get(value.get('item_id'))
            if activity:
                activity.summary_parts.add(value.get('summary_index', 0))


def handle_delta(engine, event, item_id, kind, delta, part, source, sequence=None):
    known = engine.items.get((event.backend, item_id), {})
    if known.get('mirrored'):
        engine.counts['mirrors_ignored'] += 1
        return True
    turn_id = known.get('turn_id')
    if (event.backend, turn_id) in engine.conflicted_turns:
        engine.counts['quarantined_events'] += 1
        return True
    thread_id = engine.turns.get((event.backend, turn_id))
    if not thread_id:
        return False
    thread = engine._thread(event.backend, thread_id)
    existing = thread.activities.get(item_id)
    if existing and (existing.history_retired or (existing.history and existing.history.get('history_terminal'))):
        engine.counts['late_terminal_deltas'] += 1
        return True
    if (thread.status == 'unknown' and thread.turn_id == turn_id and delta and not event.replayed and
            (event.backend, turn_id) not in engine.ended_turns and
            engine.backends[event.backend].get('connected') and
            event.at > engine.backends[event.backend].get('gap_at', event.at)):
        thread.status = 'inProgress'
    if (event.backend, turn_id) in engine.ended_turns or thread.status not in ('inProgress', 'idle'):
        engine.counts['late_terminal_deltas'] += 1
        return True
    key = (event.backend, item_id, kind, part)
    if source == 'protocol' and known.get('upstream') and key not in engine.streams:
        return True
    stream = engine.streams.get(key)
    if stream and (stream.source != source or stream.ended_at is not None or stream.ended_by_history):
        engine.counts['mirrors_ignored'] += 1
        return True
    if sequence is not None:
        last, seen = engine.sequence.setdefault(key, (-1, set()))
        if sequence in seen:
            engine.counts['duplicates_ignored'] += 1
            return True
        if sequence < last:
            engine.counts['out_of_order'] += 1
            thread.quality.add('out_of_order')
            if stream:
                stream.quality = 'out_of_order'
            return True
        seen.add(sequence)
        if len(seen) > 2048:
            seen.intersection_update(range(max(0, sequence - 4096), sequence + 1))
        engine.sequence[key] = (sequence, seen)
    phase = 'reasoning' if kind == 'summary' else kind
    activity = engine._activity(thread, item_id, turn_id, phase, PHASES[phase], known.get('started_at', event.at))
    if stream is None:
        stream = TextStream(key, kind, source, thread.model, known.get('started_at') or event.at)
        engine.streams[key] = stream
        activity.stream_keys.append(key)
        if 'identity_conflict' in thread.quality or ('observation_gap' in thread.quality and
                stream.started_at <= engine.backends[event.backend].get('gap_at', event.at)):
            stream.interrupt_observation()
    if (delta and not event.replayed and engine.backends[event.backend].get('connected') and
            event.at > engine.backends[event.backend].get('gap_at', event.at)):
        stream.resume_observation(event.at)
    stream.append(delta, event.at)
    if delta:
        if (activity.observation_lost and not event.replayed and
                event.at > engine.backends[event.backend].get('gap_at', 0)):
            activity.status = 'inProgress'
            activity.observation_lost = False
        activity.last_progress_at = event.at
        thread.error_status = None
        for waiting in thread.activities.values():
            if waiting.turn_id == turn_id and waiting.kind in ('retry', 'buffering', 'auth') and waiting.active:
                waiting.status = 'completed'
                waiting.ended_at = event.at
        thread.last_progress_at = max(thread.last_progress_at, event.at)
        if kind == 'summary':
            activity.summary_chars += len(delta)
            activity.summary_parts.add(part)
    return True


def observe_timeline_event(book, event, engine):
    with book.lock:
        book.engine = engine
        book.current_backend = event.backend
        scope = engine.backends.get(event.backend, {}).get('home') or event.backend
        if event.wall_time > 0:
            anchor = book.anchors.setdefault(event.backend, (event.at, event.wall_time * 1000))
            if not event.replayed and abs(event.wall_time * 1000 - (anchor[1] + (event.at - anchor[0]) * 1000)) > 1000:
                if event.backend not in book.clock_jumps:
                    for phase in list(book.phases.values()):
                        if phase.get('observation_backend') == event.backend and phase['status'] in ('active', 'waiting'):
                            book._put({**phase, 'quality': sorted(set(phase['quality']) | {'wall_clock_changed'})})
                book.clock_jumps.add(event.backend)
            if (event.data.get('method') == 'turn/started' and getattr(event, 'turn_started_accepted', False)
                    and event.backend in book.clock_jumps):
                book.anchors[event.backend] = anchor = (event.at, event.wall_time * 1000)
                book.clock_jumps.discard(event.backend)
            book.clock_anchor = anchor
        at = book.clock_anchor[1] + (event.at - book.clock_anchor[0]) * 1000 if book.clock_anchor else event.at * 1000
        data = event.data
        if event.direction == 'lifecycle':
            if data.get('type') in ('bridge/gap', 'bridge/disconnected', 'bridge/stopped', 'bridge/reconnected'):
                book.gap(scope, at, event.backend)
            return
        method, params = data.get('method'), data.get('params', {})
        request = params.get('requestId') if method == 'serverRequest/resolved' else data.get('id')
        if method == 'serverRequest/resolved' or (event.direction == 'protocol_in' and not method):
            marker = book.waiting.pop((event.backend, request), None)
            if marker and marker in book.phases:
                phase = book.phases[marker]
                decision = data.get('result', {}).get('decision')
                book._put({**phase, 'end_ms': at, 'end_source': 'receipt',
                           'status': 'declined' if decision in ('decline', 'cancel') else 'completed'})
        if event.direction == 'protocol_in':
            return
        if event.direction == 'protocol_out' and not method:
            return  # canonical userMessage is the sole input marker source
        raw = event.direction == 'upstream'
        item = data.get('item', params.get('item', {}))
        item_id = item.get('id') or data.get('item_id') or params.get('itemId')
        known = engine.items.get((event.backend, item_id), {})
        turn = params.get('turnId') or params.get('turn', {}).get('id') or known.get('turn_id')
        thread = params.get('threadId') or engine.turns.get((event.backend, turn))
        if thread and not turn and not raw:
            owner = engine.threads.get((event.backend, thread))
            turn = owner.turn_id if owner else None
        if not turn or not thread or (event.backend, turn) in engine.conflicted_turns:
            return
        if engine.turns.get((event.backend, turn)) != thread:
            return
        if method == 'turn/started' and getattr(event, 'turn_started_accepted', False):
            for old_turn in list(book.turns.values()):
                if (old_turn['scope_id'], old_turn['thread_id']) == (scope, thread) and old_turn['turn_id'] != turn and old_turn['status'] == 'active':
                    book._save('turn', {**old_turn, 'status': 'unknown'})
            for phase in list(book.phases.values()):
                if (phase['scope_id'], phase['thread_id']) != (scope, thread) or phase['turn_id'] == turn or phase['status'] not in ('active', 'waiting'):
                    continue
                previous = engine.threads[(event.backend, thread)].activities.get(phase['item_id'])
                if not previous or not previous.active:
                    book._put({**phase, 'status': 'unknown', 'observed_until_ms': at,
                               'quality': sorted(set(phase['quality']) | {'missing_item_end'})})
            book._turn(scope, thread, turn, at, 'active', start=at)
        elif method == 'turn/completed':
            book._turn(scope, thread, turn, at, status(params.get('turn', {}).get('status')), end=at)
            for record in list(book.phases.values()):
                if (record['scope_id'], record['thread_id'], record['turn_id']) != (scope, thread, turn) or record['status'] not in ('active', 'waiting'):
                    continue
                activity = engine.threads.get((event.backend, thread)).activities.get(record['item_id'])
                if activity and activity.active:
                    continue
                # A turn boundary does not supply the missing item completion.
                book._put({**record, 'status': 'unknown', 'observed_until_ms': at,
                           'quality': sorted(set(record['quality']) | {'missing_item_end'})})
        elif method in ('item/started', 'item/completed') or data.get('type') in ('response.output_item.added', 'response.output_item.done'):
            if known.get('mirrored') or item.get('delivery') == 'async':
                for record in list(book.phases.values()):
                    if record['scope_id'] == scope and record['thread_id'] == thread and record['item_id'] == item_id:
                        book._put({**record, 'mirrored': True})
                return
            starting = method == 'item/started' or data.get('type', '').endswith('.added')
            if starting and (event.backend, turn) in engine.ended_turns:
                return
            start = valid_ms(params.get('startedAtMs')) if not raw else None
            end = valid_ms(params.get('completedAtMs')) if not raw else None
            provenance = 'producer' if (start if starting else end) is not None else 'receipt'
            if starting:
                start = start or at
            else:
                end = end or at
            record = item_phase(scope, thread, turn, item, start, end, provenance)
            if record:
                activity = engine.threads.get((event.backend, thread)).activities.get(item_id)
                if not raw and item.get('type') == 'commandExecution' and starting:
                    if activity and activity.kind == 'tool_ready':
                        record.update(status='waiting', start_ms=None, start_source=None,
                                      quality=['execution_unconfirmed'])
                    else:
                        record['execution_confirmed'] = True
                record['observation_instance'] = book.instance
                record['observation_backend'] = event.backend
                record['observed_until_ms'] = at
                if event.backend in book.clock_jumps:
                    record['quality'].append('wall_clock_changed')
                book._put(record)
                book._turn(scope, thread, turn, at)
            if not raw:
                for child in item.get('receiverThreadIds', []):
                    book._relation(scope, thread, turn, child, item_id, item.get('tool'))
                if item.get('agentThreadId'):
                    book._relation(scope, thread, turn, item['agentThreadId'], item_id, item.get('kind'))
        elif method in SERVER_REQUESTS or method in ('thread/compacted', 'modelProvider/authRecoveryStarted', 'modelProvider/authRecoveryCompleted',
                'model/safetyBuffering/updated', 'hook/started', 'hook/completed', 'error'):
            marker_kind = ('approval' if method in SERVER_REQUESTS else 'contextCompaction' if method == 'thread/compacted'
                           else 'auth' if 'authRecovery' in method else 'retry' if params.get('willRetry') else 'other')
            marker = item_phase(scope, thread, turn, {'id': str(data.get('id', item_id or identity(method, at))), 'type': 'sleep'},
                                at, at, 'receipt', namespace='marker')
            label = LABELS.get(marker_kind, {'hook/started': '准备运行', 'hook/completed': '准备完成',
                                  'model/safetyBuffering/updated': '等待响应', 'error': '发生错误'}.get(method, '状态变化'))
            marker.update(kind=marker_kind, label=label, status='active' if method in SERVER_REQUESTS else 'completed')
            if method in SERVER_REQUESTS:
                marker['end_ms'] = marker['end_source'] = None
                book.waiting[(event.backend, data.get('id'))] = marker['id']
            book._put(marker)
        elif method == 'item/commandExecution/outputDelta':
            key = phase_id(scope, thread, turn, 'canonical', item_id)
            record = book.phases.get(key)
            activity = engine.threads.get((event.backend, thread)).activities.get(item_id)
            if record and record['status'] == 'waiting' and activity and activity.kind == 'tool':
                book._put({**record, 'status': 'active', 'observed_until_ms': at, 'execution_confirmed': True})
        classify_tool_input(book, scope, thread, turn, item_id, engine, event.backend)
        capture_command_output(book, scope, thread, turn, item_id, params)


def classify_tool_input(book, scope, thread, turn, item, engine, backend):
    key = phase_id(scope, thread, turn, 'input', item)
    record = book.phases.get(key)
    if not record or record['kind'] == 'file_edit_input':
        return
    for stream_key, stream in engine.streams.items():
        if stream_key[:2] == (backend, item) and stream.kind == 'tool_input' and stream.text:
            if edit_input(record.get('tool_name'), stream.text):
                book._put({**record, 'kind': 'file_edit_input', 'label': LABELS['file_edit_input']})


def capture_command_output(book, scope, thread, turn, item, params):
    key = phase_id(scope, thread, turn, 'canonical', item)
    if key not in book.pins or not isinstance(params.get('delta'), str):
        return
    state = book.live.setdefault(key, {'text': '', 'partial': True})
    chunk = params['delta']
    state['text'] = (state['text'] + chunk)[-262144:]
    state['partial'] = True  # subscription may have started after the first output
