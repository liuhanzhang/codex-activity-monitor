"""Apply observed Antigravity state on the collector's event-loop thread."""

from dataclasses import dataclass, field

from .antigravity_records import (OPEN, live_step, object_value, step_id, step_index, string,
                                  turn_id, workspace)
from .antigravity_source import active
from .antigravity_transport import uuid_string
from .history_index import timestamp
from .metrics import TextStream
from .timeline import identity, merge_phase
from .tool_labels import command_name, command_summary


@dataclass(slots=True)
class Conversation:
    trajectory: str = ''
    input_index: int | None = None
    summary_index: int | None = None
    root: str | None = None
    termination: str | None = None
    open_steps: dict = field(default_factory=dict)


class AntigravityProjection:
    def __init__(self, engine):
        self.engine = engine
        self.book = engine.timeline
        if self.book:
            self.book.engine = engine
        self.sessions = {}
        self.models = {}
        self.status = {'state': 'disabled'}

    @staticmethod
    def backend(scope):
        return 'antigravity-' + identity(scope)

    def _thread(self, scope, conversation):
        backend = self.backend(scope)
        self.engine.backends.setdefault(backend, {}).update(home=scope, source='antigravity')
        thread = self.engine._thread(backend, conversation)
        session = self.sessions.setdefault((scope, conversation), Conversation())
        return thread, session

    def accept(self, records):
        for kind, value, at, wall, _ in records:
            try:
                if kind == 'status':
                    self.status = value
                elif kind == 'models':
                    self.models = value
                elif kind == 'directory':
                    self.directory(*value, at=at, wall=wall)
                elif kind == 'frame':
                    self.frame(*value, at=at, wall=wall)
                elif kind == 'gap':
                    self.gap(*value, at=at, wall=wall)
                elif kind == 'disconnect':
                    self.disconnect(value, at, wall)
            except (ValueError, TypeError, KeyError, AttributeError):
                self.engine.counts['invalid_antigravity_frame'] += 1
                if kind == 'frame':
                    self.gap(*value[:2], at=at, wall=wall)

    def directory(self, scope, changed, deleted, *, at, wall):
        backend = self.backend(scope)
        self.engine.backends.setdefault(backend, {}).update(home=scope, source='antigravity', connected=True)
        for conversation in deleted:
            self.gap(scope, conversation, at=at, wall=wall)
            self.sessions.pop((scope, conversation), None)
            self.engine.threads.pop((backend, conversation), None)
        for conversation, summary in changed.items():
            thread, cursor = self._thread(scope, conversation)
            annotations = object_value(summary.get('annotations'))
            thread.name = (string(annotations.get('title')) or string(summary.get('summary')))[:256]
            thread.cwd = workspace(summary) or thread.cwd
            if not thread.activities and thread.status != 'inProgress':
                thread.history_progress_ms = timestamp(summary.get('lastModifiedTime'))
            index = summary.get('lastUserInputStepIndex')
            if step_index(index):
                cursor.summary_index = index
                if thread.turn_id is None:
                    thread.turn_id = turn_id(conversation, index)
            meta = object_value(summary.get('trajectoryMetadata'))
            root = meta.get('rootConversationId')
            cursor.root = root if uuid_string(root) and root != conversation else None
            parent = meta.get('parentConversationId')
            if uuid_string(parent) and parent != conversation:
                thread.thread_source = 'subagent'
                thread.parent_thread_id = parent
                thread.parent_source = 'trajectory_metadata'
            elif cursor.root is None:
                thread.thread_source = 'user'
            if active(summary) and thread.status != 'inProgress':
                thread.status, thread.wait_started_at = 'inProgress', at
                thread.turn_start_observed = False
            reason = string(summary.get('latestExecutorTerminationReason')).removeprefix('EXECUTOR_TERMINATION_REASON_')
            if not active(summary) and reason in ('ERROR', 'USER_CANCELED', 'HALTED_STEP'):
                cursor.termination = 'failed' if reason == 'ERROR' else 'interrupted'
                if thread.turn_id and thread.status != 'inProgress':
                    thread.status = cursor.termination
                    self._turn(scope, thread, thread.turn_id, wall, status=thread.status)

    def frame(self, scope, conversation, value, initial, *, at, wall):
        data = object_value(value.get('update'))
        if data.get('conversationId') != conversation:
            return
        thread, cursor = self._thread(scope, conversation)
        trajectory = string(data.get('trajectoryId'))
        if cursor.trajectory and trajectory and trajectory != cursor.trajectory:
            self.gap(scope, conversation, at=at, wall=wall)
            cursor.input_index = None
            cursor.open_steps.clear()
            initial = True
        cursor.trajectory = trajectory or cursor.trajectory
        self.engine.backends[thread.backend]['connected'] = True
        if initial:
            thread.quality.discard('disconnected')
        steps = object_value(object_value(data.get('mainTrajectoryUpdate')).get('stepsUpdate'))
        if initial:
            # A reconnect can replace the view of steps already in progress.
            cursor.input_index = None
        pairs = zip(steps.get('indices', ()), steps.get('steps', ()))
        entries = sorted(((index, row) for index, row in pairs if step_index(index)), key=lambda x: x[0])
        for index, row in entries:
            step = live_step(index, row)
            if not step.type:
                continue
            if step.type == 'USER_INPUT':
                self._begin(scope, thread, cursor, step, initial, at, wall)
            anchor = cursor.input_index
            if anchor is None and cursor.summary_index is not None and index >= cursor.summary_index:
                anchor = cursor.summary_index
            if anchor is None and cursor.root:
                anchor = 0  # A subagent trajectory may have no user input of its own.
            if anchor is None or index < anchor:
                continue
            turn = turn_id(conversation, anchor)
            if thread.turn_id is None:
                thread.turn_id = turn
                self._turn(scope, thread, turn, wall, status='unknown')
            if step.model and step.type == 'PLANNER_RESPONSE':
                thread.model = self.models.get(step.model, step.model)
            self._step(scope, thread, turn, step, initial, at, wall)
            self._children(scope, thread, turn, step)
            if step.state in OPEN:
                cursor.open_steps[index] = turn
            else:
                cursor.open_steps.pop(index, None)
            if step.usage and step.state == 'completed' and step.type == 'PLANNER_RESPONSE':
                self.engine.usage.record_attributed((scope, conversation), turn, thread.backend,
                                                   step_id(conversation, index), step.usage, at, live=True)
                self._request(scope, conversation, turn, step)
            elif step.type == 'PLANNER_RESPONSE' and step.state in ('failed', 'interrupted'):
                cursor.termination = step.state
        if active(data):
            thread.status = 'inProgress'
            if data.get('status') == 'CASCADE_RUN_STATUS_CANCELING':
                cursor.termination = 'interrupted'
            self._turn(scope, thread, thread.turn_id, wall, status='active')
        elif data.get('fullyIdle') or data.get('status') == 'CASCADE_RUN_STATUS_IDLE':
            self._finish(scope, thread, cursor, at, wall, initial, failed=bool(data.get('error')))
        if initial and not cursor.open_steps:
            thread.quality.discard('observation_gap')

    def _children(self, scope, thread, turn, step):
        if step.type != 'INVOKE_SUBAGENT' or not self.book:
            return
        for child in step.children:
            if not uuid_string(child) or child == thread.id:
                continue
            dependent, _ = self._thread(scope, child)
            if dependent.parent_thread_id not in (None, thread.id):
                dependent.parent_quality.add('parent_identity_conflict')
                continue
            dependent.thread_source = 'subagent'
            dependent.parent_thread_id = thread.id
            dependent.parent_source = 'spawn_call'
            self.book._relation(scope, thread.id, turn, child, step_id(thread.id, step.index), 'spawn',
                                source='antigravity')

    def _request(self, scope, conversation, turn, step):
        sample = step.model_request()
        if not self.book or sample is None:
            return
        key = identity('turn', scope, conversation, turn)
        old = self.book.turns.get(key)
        if old is None:
            return
        requests = dict(old.get('model_requests', {}))
        requests[str(step.index)] = sample
        self.book._save('turn', {**old, 'model_requests': dict(list(requests.items())[-128:])})

    def _begin(self, scope, thread, cursor, step, initial, at, wall):
        turn = turn_id(thread.id, step.index)
        if step.index == cursor.input_index and turn == thread.turn_id:
            return
        if cursor.input_index is not None and step.index < cursor.input_index and not initial:
            return
        cursor.input_index = step.index
        if thread.turn_id != turn:
            cursor.termination = None
            if not initial:
                for activity in thread.activities.values():
                    if activity.turn_id != turn and activity.active:
                        self._end_activity(activity, at, 'unknown')
        thread.turn_id = turn
        thread.history_progress_ms = step.created
        thread.wait_started_at = at
        thread.turn_start_observed = not initial
        if not initial:
            thread.last_progress_at = at
            thread.status = 'inProgress'
            thread.quality.discard('observation_gap')
        self._turn(scope, thread, turn, wall, start=step.created, status='unknown' if initial else 'active')

    def _turn(self, scope, thread, turn, wall, *, status=None, start=None, end=None):
        if not self.book or not turn:
            return
        key = identity('turn', scope, thread.id, turn)
        old = self.book.turns.get(key, {})
        self.book._save('turn', {**old, 'id': key, 'scope_id': scope, 'thread_id': thread.id, 'turn_id': turn,
                                 'start_ms': old.get('start_ms') or start, 'end_ms': end or old.get('end_ms'),
                                 'updated_ms': wall * 1000, 'status': status or old.get('status', 'unknown'),
                                 'observation_backend': thread.backend})

    def _step(self, scope, thread, turn, step, initial, at, wall):
        for part in step.parts():
            phase = step.phase(scope, thread.id, turn, part, at=None if initial else wall * 1000)
            phase['observation_backend'] = thread.backend
            if part == 'input':
                if self.book:
                    self.book._put(phase)
                    if step.arguments and (not initial or step.state in OPEN):
                        self.book.live[phase['id']] = {'text': step.arguments[:32768], 'at': wall}
                continue
            item_id, kind = phase['item_id'], phase['item_type']
            previous = thread.activities.get(item_id)
            state = step.state
            if part == 'summary' and step.text and state == 'active':
                state = 'completed'
                phase['status'] = state
                if phase['end_ms'] is None and not initial:
                    phase.update(end_ms=wall * 1000, end_source='receipt')
            if previous and previous.ended_at is not None and state in OPEN:
                continue
            current = self.book.phases.get(phase['id']) if self.book else None
            if (part == 'summary' and phase['end_source'] != 'producer' and step.text and
                    current and current.get('end_source') == 'receipt'):
                phase.update(end_ms=current['end_ms'], end_source='receipt')
            if not step.is_tool and not initial and phase['start_ms'] is None and current is None and state in OPEN:
                phase.update(start_ms=wall * 1000, start_source='receipt')
            if self.book:
                if current and current['status'] == 'unknown' and state in OPEN:
                    self.book._save('phase', {**merge_phase(current, phase), 'status': state})
                else:
                    self.book._put(phase)
            if part == 'summary' and self.book and step.thinking and (not initial or state in OPEN):
                self.book.live[phase['id']] = {'text': step.thinking[-32768:], 'at': wall}
            if kind == 'userMessage':
                continue  # The user's message is a timeline marker, not agent work.
            if initial and state not in OPEN and previous is None:
                continue
            activity_kind = 'text' if kind == 'agentMessage' else 'reasoning' if kind == 'reasoning' else (
                'tool_ready' if step.state == 'waiting' else 'tool')
            activity = self.engine._activity(thread, item_id, turn, activity_kind, phase['label'],
                                             at if not initial and state in OPEN else None)
            activity.kind, activity.item_type = activity_kind, kind
            activity.tool_name = phase['tool_name']
            if kind == 'commandExecution' and step.arguments:
                activity.command_name = command_name(step.arguments)
                activity.command = command_summary(step.arguments)
            if step.exit_code is not None:
                activity.exit_code = step.exit_code
            if part == 'summary':
                activity.summary_chars = len(step.thinking)
            if kind == 'agentMessage' and (state in OPEN or previous):
                rebased = self._text(thread, activity, step.text, at, initial)
                if rebased and self.book:
                    self.book._put({**phase, 'quality': ['observation_gap'], 'observed_until_ms': wall * 1000})
            if state in OPEN:
                activity.status, activity.observation_lost = 'inProgress', False
                if not initial:
                    thread.status = 'inProgress'
            else:
                self._end_activity(activity, at, state)
            if not initial:
                thread.last_progress_at = max(thread.last_progress_at, at)
                thread.history_progress_ms = max(thread.history_progress_ms or 0, wall * 1000)
        if self.book and (step.arguments or step.output) and (not initial or step.state in OPEN):
            # Tool bodies are transient; terminal details can be queried again.
            phase = step.phase(scope, thread.id, turn, 'main')
            self.book.live[phase['id']] = {'text': step.output[-32768:], 'input': step.arguments[:32768], 'at': wall}

    def _text(self, thread, activity, body, at, initial):
        key = thread.backend, activity.id, 'text', 0
        stream = self.engine.streams.get(key)
        if stream is None:
            self.engine.streams[key] = stream = TextStream(key, 'text', 'antigravity', thread.model, at)
            activity.stream_keys.append(key)
        if stream.ended_at is not None or stream.ended_by_history:
            return False
        stream.set_model(thread.model)
        rewritten = not body.startswith(stream.text)
        if initial or stream.quality in ('observation_gap', 'disconnected') or rewritten:
            stream.set_snapshot_baseline(body, at)
        elif body != stream.text:
            stream.append(body[len(stream.text):], at)
        return rewritten and not initial

    def _end_activity(self, activity, at, status):
        if activity.ended_at is None:
            activity.ended_at = at
        activity.status, activity.observation_lost = status, False
        for key in activity.stream_keys:
            stream = self.engine.streams.get(key)
            if stream and stream.ended_at is None:
                stream.ended_at = at

    def _finish(self, scope, thread, cursor, at, wall, initial, *, failed=False):
        pending = thread.status == 'inProgress'
        terminal = 'failed' if failed else cursor.termination or 'completed'
        for activity in thread.activities.values():
            if activity.active or activity.unresolved:
                self._end_activity(activity, at, terminal if terminal != 'completed' else 'unknown')
        cursor.open_steps.clear()
        thread.status = terminal if thread.turn_id else 'idle'
        if pending and not initial:
            self._turn(scope, thread, thread.turn_id, wall, status=terminal, end=wall * 1000)

    def gap(self, scope, conversation, *, at, wall):
        thread = self.engine.threads.get((self.backend(scope), conversation))
        if thread is None or thread.status != 'inProgress':
            return
        thread.status = 'unknown'
        thread.quality.add('observation_gap')
        for activity in thread.activities.values():
            if activity.active:
                activity.status = 'unknown'
                activity.observation_lost = True
                for key in activity.stream_keys:
                    stream = self.engine.streams.get(key)
                    if stream:
                        stream.interrupt_observation()
        if self.book:
            self.book.gap(scope, wall * 1000, thread.backend, thread=conversation)

    def disconnect(self, scope, at, wall):
        backend = self.backend(scope)
        if backend in self.engine.backends:
            self.engine.backends[backend].update(connected=False, ended_at=at)
        for owner, conversation in list(self.sessions):
            if owner == scope:
                self.gap(scope, conversation, at=at, wall=wall)

    def disable(self, at, wall):
        for scope in {scope for scope, _ in self.sessions}:
            self.disconnect(scope, at, wall)
        for scope, conversation in self.sessions:
            self.engine.threads.pop((self.backend(scope), conversation), None)
        self.sessions.clear()
        self.status = {'state': 'disabled'}

    def reconcile(self, summary, at):
        scope, conversation = summary['scope_id'], summary['thread_id']
        thread = self.engine.threads.get((self.backend(scope), conversation))
        if thread is None:
            return
        thread.history_quality = set(summary.get('quality', []))
        for turn, index, usage in summary.get('step_usage', ()):
            self.engine.usage.record_attributed((scope, conversation), turn, thread.backend,
                                               step_id(conversation, index), usage, at, live=False)
        if thread.status == 'inProgress' or not summary.get('read_complete'):
            return
        latest = max(summary.get('turns', ()), key=lambda row: row.get('start_ms') or 0, default=None)
        if latest and thread.turn_id is None:
            thread.turn_id = latest['turn_id']
        if (latest and thread.turn_id == latest['turn_id'] and latest.get('history_terminal') and
                thread.status not in ('failed', 'interrupted')):
            thread.status = latest['status']
        for phase in summary.get('phases', ()):
            activity = thread.activities.get(phase['item_id'])
            if activity and activity.unresolved and phase.get('history_terminal'):
                activity.history = phase
                self._end_activity(activity, at, phase['status'])
