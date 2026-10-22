"""Scoped progress semantics and a small user-facing projection of the state."""

from pathlib import Path

from .events import BLOCKING_REQUESTS, SERVER_REQUESTS, brief
from .i18n import message, phase_message, render_message
from .tool_labels import command_name, is_code_exec, is_command_tool, is_patch_tool, tool_display


def finish(engine, activity, at, status='completed'):
    if activity is None or activity.ended_at is not None:
        return
    activity.status, activity.ended_at = status, at
    for key in activity.stream_keys:
        stream = engine.streams.get(key)
        if stream and stream.ended_at is None:
            engine._finish_stream(stream, at)


def resolve_request(engine, backend, request_id, at, decision=None):
    key = (backend, request_id)
    owner = engine.approvals.pop(key, None)
    engine.resolved_requests[key] = at
    engine.resolved_requests.move_to_end(key)
    while len(engine.resolved_requests) > 1024:
        engine.resolved_requests.popitem(last=False)
    if not owner:
        return
    thread = engine.threads.get(owner[0])
    if not thread:
        return
    activity = thread.activities.get(owner[1])
    # The request ends independently of the target operation. Even acceptance
    # cannot establish that the process has started.
    finish(engine, activity, at, 'declined' if decision in ('decline', 'cancel') else 'resolved')
    kind = activity.kind if activity else None
    if not any(a.active and a.kind == kind for a in thread.activities.values()):
        thread.active_flags.discard('waitingOnUserInput' if kind == 'input' else 'waitingOnApproval')
    thread.wait_started_at = at


def scoped_activity(engine, thread, item_id, turn_id, kind, at, ending=False, status='completed'):
    existing = thread.activities.get(item_id)
    if not ending and existing and existing.ended_at is not None:
        if kind in ('auth','retry','buffering') and at > existing.ended_at and (thread.backend,turn_id) not in engine.ended_turns:
            existing.ended_at=None
            existing.status='inProgress'
            existing.started_at=existing.last_progress_at=at
        return existing
    activity = engine._activity(thread, item_id, turn_id, kind, '', None if ending else at)
    if ending:
        finish(engine, activity, at, status)
    return activity


def handle_protocol(engine, event):
    value, backend, at = event.data, event.backend, event.at
    method, params = value.get('method'), value.get('params', {})
    thread_id, turn_id = params.get('threadId'), params.get('turnId')
    if method == 'serverRequest/resolved':
        resolve_request(engine, backend, params.get('requestId'), at)
        return True
    if method in SERVER_REQUESTS:
        key = (backend, value.get('id'))
        if key in engine.resolved_requests or not thread_id:
            return True
        thread = engine._thread(backend, thread_id)
        if turn_id and not engine._map_turn(backend, turn_id, thread_id):
            return True
        turn_id = turn_id or thread.turn_id
        if (backend, turn_id) in engine.ended_turns:
            return True
        if method in BLOCKING_REQUESTS:
            kind = 'input' if method in ('item/tool/requestUserInput', 'mcpServer/elicitation/request') else 'approval'
            item_id = 'request:' + str(value.get('id'))
            request = engine._activity(thread, item_id, turn_id, kind, '', at)
            request.target_item_id = params.get('itemId')
            request.turn_bound = bool(params.get('turnId'))
            engine.approvals[key] = ((backend, thread_id), item_id)
        elif method == 'item/tool/call':
            item_id = params.get('callId')
            if item_id:
                engine.items.setdefault((backend, item_id), {'turn_id': turn_id, 'type': 'dynamicToolCall'})
                activity = engine._activity(thread, item_id, turn_id, 'tool_ready', '', at)
                activity.item_type = 'dynamicToolCall'
                engine._tool_metadata(activity, {'type':'dynamicToolCall', **params})
        return True

    special = {'thread/status/changed','thread/archived','thread/deleted','thread/unarchived','thread/closed','thread/reverted',
               'thread/goal/updated','thread/goal/cleared','project/changed','thread/project/updated',
               'thread/environment/connected','thread/environment/disconnected','hook/started','hook/completed',
               'turn/plan/updated','item/autoApprovalReview/started','item/autoApprovalReview/completed',
               'autoApprovalReview/strictReviewRequired','error','modelProvider/authRecoveryStarted','modelProvider/authRecoveryCompleted',
               'model/safetyBuffering/updated','model/verification','warning','guardianWarning',
               'mcpServer/startupStatus/updated','mcpServer/oauthLogin/completed','account/login/completed',
               'item/mcpToolCall/progress','item/fileChange/patchUpdated','item/fileChange/outputDelta',
               'item/commandExecution/terminalInteraction','item/reasoning/summaryPartAdded',
               'thread/compacted','rawResponseItem/completed'}
    if method not in special:
        return False
    if method == 'project/changed':
        # This payload contains an ID, not a project name. No extra query.
        return True
    if method == 'rawResponseItem/completed':
        # Identity recovery through experimental raw items remains disabled.
        # A completed raw item cannot turn earlier unassociated deltas into a
        # new live generation phase, or establish a trustworthy speed baseline.
        engine.counts['raw_identity_observed'] += bool(params.get('item',{}).get('id'))
        return True
    if method == 'mcpServer/startupStatus/updated':
        states=engine.backends[backend].setdefault('mcp_status', {})
        states[brief(params.get('name'))]=params.get('status')
        while len(states)>64:
            states.pop(next(iter(states)))
        # Global startup is never assigned to every chat. Only an existing tool
        # explicitly naming this server can provide the dependency.
        for owner in engine.threads.values():
            if owner.backend==backend and (not thread_id or owner.id==thread_id):
                for activity in owner.activities.values():
                    if activity.active and activity.server==params.get('name'):
                        activity.startup_pending=params.get('status') in ('starting','pending')
    if not thread_id:
        return True
    thread = engine._thread(backend, thread_id)
    if turn_id and not engine._map_turn(backend, turn_id, thread_id):
        return True
    if turn_id and thread.turn_id and turn_id != thread.turn_id:
        # Old tool completion/progress may still concern a real background job;
        # ordinary recovery/configuration events cannot take over a newer turn.
        if method not in ('item/mcpToolCall/progress','item/commandExecution/terminalInteraction',
                          'item/fileChange/patchUpdated','item/fileChange/outputDelta','item/autoApprovalReview/completed'):
            return True

    if method == 'thread/status/changed':
        state = params.get('status', {})
        state = {'type':state} if isinstance(state, str) else state
        thread.active_flags = set(state.get('activeFlags', []))
        if state.get('type') != 'systemError':
            thread.quality.discard('system_error')
        if state.get('type') == 'active' and thread.status in ('idle', 'unknown'):
            thread.status = 'inProgress'
        elif state.get('type') == 'systemError':
            thread.quality.add('system_error')
        elif state.get('type') in ('idle','notLoaded'):
            thread.active_flags.clear()
            for activity in thread.activities.values():
                if activity.turn_bound:
                    finish(engine,activity,at,'unknown')
            if thread.status == 'inProgress':
                thread.status = 'idle' if state['type']=='idle' else 'unknown'
    elif method in ('thread/archived','thread/deleted','thread/unarchived'):
        thread.visible = method == 'thread/unarchived'
    elif method in ('thread/closed','thread/reverted'):
        if thread.turn_id:
            engine.ended_turns[(backend, thread.turn_id)] = 'unknown'
        for activity in thread.activities.values():
            if activity.turn_bound:
                finish(engine, activity, at, 'unknown')
        for key,(owner,_) in list(engine.approvals.items()):
            if owner == (backend, thread_id):
                resolve_request(engine, backend, key[1], at)
        thread.active_flags.clear()
        thread.status = 'unknown'
    elif method in ('thread/goal/updated','thread/goal/cleared'):
        thread.goal_status = params.get('goal', {}).get('status') if method.endswith('updated') else None
    elif method == 'thread/project/updated':
        thread.project_id = params.get('projectId')
    elif method.startswith('thread/environment/'):
        thread.environment_connected = method.endswith('/connected')
        thread.environment_id = params.get('environmentId')
    elif method == 'turn/plan/updated':
        thread.plan = params.get('plan', [])
    elif method.startswith('item/autoApprovalReview/'):
        review_id = params.get('reviewId')
        if not review_id:
            return True
        ending = method.endswith('/completed')
        if not ending and (backend, turn_id) in engine.ended_turns:
            return True
        review = scoped_activity(engine, thread, 'review:' + review_id, turn_id, 'review', at, ending,
                                 params.get('review', {}).get('status', 'completed'))
        review.target_item_id = params.get('targetItemId')
        review.review_started_ms = params.get('startedAtMs')
        for strict in thread.activities.values():
            if strict.id.startswith('strict:') and strict.turn_id == turn_id and (
                    strict.review_started_ms == review.review_started_ms or strict.review_started_ms is None):
                finish(engine, strict, at, 'superseded')
    elif method == 'autoApprovalReview/strictReviewRequired':
        started = params.get('startedAtMs')
        known = any(a.kind == 'review' and a.turn_id == turn_id and a.review_started_ms == started
                    for a in thread.activities.values())
        if (backend, turn_id) not in engine.ended_turns and not known:
            review = scoped_activity(engine, thread, f'strict:{turn_id}:{started}', turn_id, 'review', at)
            review.review_started_ms = started
    elif method in ('hook/started','hook/completed'):
        run = params.get('run', {})
        if turn_id == thread.turn_id and run.get('executionMode') == 'sync' and run.get('scope') == 'turn' and run.get('id'):
            if method.endswith('/completed') or (backend, turn_id) not in engine.ended_turns:
                scoped_activity(engine, thread, 'hook:' + run['id'], turn_id, 'hook', at,
                                method.endswith('/completed'), run.get('status', 'completed'))
    elif method == 'error':
        if (backend, turn_id) in engine.ended_turns:
            return True
        thread.error_status = 'retry' if params.get('willRetry') else 'failed'
        for activity in thread.activities.values():
            if activity.turn_id == turn_id and activity.kind in ('text','plan','reasoning','tool_input'):
                finish(engine, activity, at, 'unknown')
        if params.get('willRetry'):
            scoped_activity(engine, thread, 'retry:' + str(turn_id), turn_id, 'retry', at)
    elif method.startswith('modelProvider/authRecovery'):
        ending = method.endswith('Completed')
        if ending or (backend, turn_id) not in engine.ended_turns:
            scoped_activity(engine, thread, 'auth:' + str(turn_id), turn_id, 'auth', at, ending)
    elif method == 'model/safetyBuffering/updated':
        if params.get('showBufferingUi') and (backend, turn_id) not in engine.ended_turns:
            scoped_activity(engine, thread, 'buffer:' + str(turn_id), turn_id, 'buffering', at)
        else:
            finish(engine, thread.activities.get('buffer:' + str(turn_id)), at)
    elif method == 'model/verification':
        # Advisory data alone is not evidence that work is blocked.
        engine.counts['verification_observed'] += bool(params.get('verification_present'))
    elif method in ('warning','guardianWarning'):
        engine.counts['scoped_warning_observed'] += 1
    elif method in ('mcpServer/oauthLogin/completed','account/login/completed'):
        # No matching login request is in this progress protocol: never clear all
        # waiting requests because an unrelated login has finished.
        engine.counts['login_completed_observed'] += 1
    elif method in ('item/mcpToolCall/progress','item/fileChange/patchUpdated','item/fileChange/outputDelta','item/commandExecution/terminalInteraction'):
        activity = thread.activities.get(params.get('itemId'))
        if activity and activity.active:
            has_progress = params.get('has_progress') or params.get('output_chars') or params.get('changes') or params.get('processId')
            if has_progress:
                activity.last_progress_at = thread.last_progress_at = at
                activity.startup_pending=False
            if method == 'item/commandExecution/terminalInteraction' and params.get('processId'):
                activity.kind, activity.turn_bound = 'tool', False
            if 'changes' in params:
                activity.files, activity.file_count = params['changes'], params.get('file_count',len(params['changes']))
    elif method == 'item/reasoning/summaryPartAdded':
        activity = thread.activities.get(params.get('itemId'))
        if activity:
            activity.summary_parts.add(params.get('summaryIndex', 0))
    elif method == 'thread/compacted':
        for activity in thread.activities.values():
            if activity.turn_id == turn_id and activity.kind == 'compaction':
                finish(engine, activity, at)
    return True


def short_tool(name):
    return tool_display(name)['tool_display_name'][:80]


def _value(activity, key, default=None):
    return activity.get(key, default) if isinstance(activity, dict) else getattr(activity, key, default)


def activity_target(activity):
    command = _value(activity, 'command')
    if command:
        return _value(activity, 'command_name') or command_name(command) or ''
    previews = _value(activity, 'command_previews', [])
    if previews:
        preview = previews[0]
        return preview.get('command_name') or command_name(preview.get('command', ''), preview.get('complete', False)) or ''
    names = _value(activity, 'tool_names', [])
    name = names[0] if names else _value(activity, 'tool_name')
    if is_command_tool(name) or _value(activity, 'item_type') == 'commandExecution':
        return ''
    display = tool_display(name, _value(activity, 'app_context', {}))
    # Snapshots carry only the resolved names, rather than the app context.
    if not names:
        display['product_name'] = _value(activity, 'product_name') or display['product_name']
        display['tool_display_name'] = _value(activity, 'tool_display_name') or display['tool_display_name']
    return ' · '.join(v for v in (display['product_name'], display['tool_display_name']) if v)


def activity_message(activity, ended=False):
    kind, item_type = _value(activity, 'kind'), _value(activity, 'item_type')
    preview = _value(activity, 'file_edit_preview')
    patch = is_patch_tool(_value(activity, 'tool_name')) or preview is not None
    if item_type == 'fileChange' or (patch and kind in ('tool_input','tool_ready','tool')):
        files, count = _value(activity, 'files', []), _value(activity, 'file_count', 0)
        target = Path(files[0]['path']).name if files else ''
        if item_type != 'fileChange' and preview is not None:
            files, count = preview['files'], preview['file_count']
            target = preview['current_file'] if kind == 'tool_input' else (files[0]['path'] if files else '')
        if not target:
            return message('activity.file_edited')
        if ended:
            return message('activity.file_edited_more' if target and count > 1 else 'activity.file_edited',
                           target=target, **({'count': count-1} if target and count > 1 else {}))
        return message('activity.file_editing_more' if target and count > 1 else 'activity.file_editing',
                       target=target, **({'count': count-1} if target and count > 1 else {}))
    target = activity_target(activity)
    if kind in ('tool_input', 'tool_ready'):
        if ended:
            return message('activity.named', name=target) if target else message('activity.tool')
        return message('activity.preparing' if kind == 'tool_input' else 'activity.processing', target=target)
    if kind == 'tool':
        keys = {'webSearch': 'web', 'imageView': 'image_view', 'imageGeneration': 'image_generation'}
        if item_type in keys:
            return message('activity.' + keys[item_type])
        if item_type == 'collabAgentToolCall':
            keys = {'wait': 'agent_wait', 'spawnAgent': 'agent_spawn', 'sendInput': 'agent_update',
                    'resumeAgent': 'agent_resume', 'closeAgent': 'agent_close', 'sendMessage': 'agent_update',
                    'followupTask': 'agent_update', 'interruptAgent': 'agent_interrupt', 'listAgents': 'agent_list'}
            return message('activity.' + keys.get(short_tool(_value(activity, 'tool_name')), 'agent_work'))
        if ended:
            return message('activity.named', name=target) if target else message('activity.command' if item_type == 'commandExecution' else 'activity.tool')
        return message('activity.preparing' if _value(activity, 'startup_pending', False) else 'activity.running', target=target)
    return phase_message(kind)


def activity_label(activity):
    return render_message(activity_message(activity, ended=not activity.active))


def presentation(engine, thread, view, now):
    activities = list(thread.activities.values())
    # Link direct model input to canonical editing only by an explicit call ID.
    edits = {a.id for a in activities if a.item_type == 'fileChange'}
    superseded = {a.id for a in activities if a.item_type != 'fileChange'
                  and is_patch_tool(a.tool_name) and a.call_id in edits}
    editing_turns = {a.turn_id for a in activities if a.item_type == 'fileChange'}

    def display_message(activity, ended=False):
        # A completed script's preview cannot establish whether another inner
        # edit is still pending after observed editing. Keep the wrapper neutral.
        if (activity.kind == 'tool_ready' and is_code_exec(activity.tool_name)
                and activity.file_edit_preview is not None and activity.turn_id in editing_turns):
            return message('activity.tool' if ended else 'activity.processing')
        return activity_message(activity, ended)
    blockers = [a for a in activities if a.active and a.kind in ('input','approval')]
    blocked_ids = {a.target_item_id for a in blockers if a.target_item_id}
    actual = [a for a in activities if a.active and a.kind not in ('input','approval','tool_ready')
              and a.id not in superseded and (a.id not in blocked_ids or not a.turn_bound)]
    actual.sort(key=lambda a: (a.kind in ('review','auth','retry','hook','buffering','sleep'), -a.last_progress_at))
    primary = actual[0] if actual else blockers[0] if blockers else None
    previous = next((a for a in actual if a.id == thread.display_primary),None)
    waits = {'review','auth','retry','hook','buffering','sleep'}
    if previous and primary and (previous.kind in waits) == (primary.kind in waits) and now-thread.display_since < .75:
        primary = previous
    pending = [a for a in activities if a.active and a.kind == 'tool_ready' and a.id not in superseded]
    if not primary and pending and not thread.error_status:
        primary = pending[-1]
    connected = view['connected']
    if not connected or 'system_error' in thread.quality:
        label = message('activity.disconnected' if not connected else 'activity.unknown')
        primary = None
    elif view.get('has_unresolved_work') and not primary:
        label = message('activity.unknown')
    elif primary and 'observation_gap' in thread.quality and primary.last_progress_at <= engine.backends[thread.backend].get('gap_at',0):
        label = message('activity.unknown')
        primary = None
    elif primary:
        label = display_message(primary)
    elif thread.active_flags:
        label = message('activity.paused')
    elif thread.error_status:
        label = message('activity.retry' if thread.error_status == 'retry' else 'activity.failed')
    elif thread.goal_status in ('paused','blocked','usageLimited','budgetLimited'):
        label = message('activity.paused')
    elif thread.status == 'completed':
        label = message('activity.turn_completed' if thread.goal_status and thread.goal_status != 'complete' else 'activity.completed')
    elif thread.status == 'inProgress':
        recent = next((a for a in reversed(activities) if a.ended_at is not None and
                       0 <= now-a.ended_at < 1 and a.status == 'completed' and
                       a.kind not in ('input','approval','tool_ready','tool_input','hook')),None)
        label = message('activity.recent', activity=activity_message(recent, ended=True)) if recent else message('activity.processing' if activities else 'activity.waiting')
    else:
        label = phase_message(thread.status)
    # Brief service/review transitions coalesce; real work and terminal results
    # update immediately. The separate activities remain available in details.
    if primary and primary.kind in ('auth','retry','hook','buffering') and primary.started_at and now-primary.started_at < 1:
        label = message('activity.processing')
    if primary and primary.kind == 'sleep' and primary.started_at and now-primary.started_at < 3:
        label = message('activity.processing')
    primary_id = primary.id if primary else None
    if primary_id != thread.display_primary:
        thread.display_primary, thread.display_since = primary_id, now
    visible = actual if actual else blockers[:1] or pending[-1:]
    if any(a.kind != 'hook' for a in visible):
        visible = [a for a in visible if a.kind != 'hook']
    if not primary and label['key'] in ('activity.disconnected', 'activity.unknown'):
        visible=[]
    visible_ids = {a.id for a in visible}
    rates, unavailable, estimated = [], False, False
    for activity in actual:
        for key in activity.stream_keys:
            stream = engine.streams.get(key)
            if stream and stream.ended_at is None and not stream.ended_by_history and stream.last_delta_at is not None and stream.kind in ('text','plan','summary','tool_input'):
                rate = stream.rate(now)
                if rate is None:
                    unavailable = True
                else:
                    rates.append(rate)
                    estimated |= stream.estimated
    view.update(display_message=label, display_label=render_message(label), visible=thread.visible,
                display_active_count=len(visible_ids), display_primary_id=primary.id if primary else None,
                generation_rate=sum(rates) if rates and not unavailable and connected and label['key'] not in ('activity.unknown', 'activity.disconnected') else None,
                generation_estimated=estimated, plan=list(thread.plan), goal_status=thread.goal_status,
                review_mode=thread.review_mode, subagents=dict(thread.subagents),
                environment_connected=thread.environment_connected, project_id=thread.project_id,
                display_seconds=max(0,now-primary.started_at) if primary and primary.started_at and connected else None)
    for detail, activity in zip(view['activities'], reversed(activities)):
        descriptor = display_message(activity, ended=not activity.active)
        detail.update(display_message=descriptor,
                      display_label=render_message(descriptor), display_visible=activity.id not in superseded and activity.kind not in ('approval','input','hook') and not (activity.kind=='tool_ready' and is_code_exec(activity.tool_name) and actual),
                      files=list(activity.files), file_count=activity.file_count,
                      receiver_thread_ids=list(activity.receiver_thread_ids), agents_states=dict(activity.agents_states))
