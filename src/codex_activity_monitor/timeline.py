"""Identity-preserving phase projection. No message or tool bodies are persisted."""

from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
import math
import threading
import time

from .events import TOOL_TYPES, SERVER_REQUESTS
from .tool_labels import code_tool_names, file_edit_preview, is_patch_tool, tool_name
from .agent_relations import raw_coordination
from .i18n import message
from .history_contract import HistoryCoverage

MODEL_TYPES = {'reasoning': 'reasoning', 'agentMessage': 'text', 'message': 'text', 'plan': 'text'}
HISTORY_SEED_TYPES = TOOL_TYPES | {'agentMessage', 'reasoning', 'plan', 'contextCompaction'}
HISTORY_SEED_LIMIT = 20
LABELS = {'reasoning': '思考', 'text': '正文输出', 'tool_input': '工具参数生成',
          'file_edit_input': '编辑内容生成', 'tool_execution': '工具执行', 'input': '用户输入',
          'contextCompaction': '整理上下文', 'sleep': '等待', 'approval': '等待确认',
          'retry': '重试', 'auth': '恢复连接', 'gap': '观察中断'}


def identity(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()[:32]


def valid_ms(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0 else None


def phase_id(scope, thread, turn, namespace, item):
    return identity('phase', scope, thread, turn, namespace, item)


def item_namespace(kind):
    return 'input' if kind in ('function_call', 'custom_tool_call') else 'model' if kind in MODEL_TYPES else 'canonical'


def status(value):
    return {'inProgress': 'active', 'in_progress': 'active', 'success': 'completed',
            'done': 'completed', 'cancelled': 'interrupted'}.get(value, value or 'unknown')


def edit_input(name, code):
    if is_patch_tool(name):
        return True
    if not isinstance(code, str):
        return False
    preview = file_edit_preview(name, code)
    names = code_tool_names(code)
    return bool(preview and preview.get('file_count') and names and all(is_patch_tool(n) for n in names))


def item_phase(scope, thread, turn, item, start=None, end=None, source='producer', namespace=None):
    """Project an already identified item; input generation and execution stay separate."""
    kind = item.get('type', '')
    raw_input = kind in ('function_call', 'custom_tool_call')
    subtype = MODEL_TYPES.get(kind)
    if raw_input:
        name = tool_name(item)
        code = item.get('input', item.get('arguments', ''))
        edit = edit_input(name, code)
        subtype = 'file_edit_input' if edit else 'tool_input'
    elif subtype is not None:
        pass
    elif kind in TOOL_TYPES:
        subtype = 'tool_execution'
    elif kind == 'userMessage':
        subtype = 'input'
    elif kind in ('contextCompaction', 'sleep', 'enteredReviewMode', 'exitedReviewMode'):
        subtype = kind
    elif kind not in ('hookPrompt', 'functionCallOutput', 'subAgentActivity'):
        subtype = 'other'
    else:
        return None
    namespace = namespace or item_namespace(kind)
    item_id = item.get('id')
    if not item_id or not thread or not turn:
        return None
    # Keep compaction in its original canonical identity namespace; only its
    # presentation moves from a point to a model process.
    track = 'model' if kind in MODEL_TYPES or raw_input or kind == 'contextCompaction' else 'tool' if kind in TOOL_TYPES else 'marker'
    name = tool_name(item) or (str(item.get('tool', '')) if kind in TOOL_TYPES else '')
    return {'id': phase_id(scope, thread, turn, namespace, item_id), 'scope_id': scope,
            'thread_id': thread, 'turn_id': turn, 'item_id': item_id, 'namespace': namespace,
            'item_type': kind, 'track': track, 'kind': subtype,
            'label': (name or {'fileChange': '应用文件改动', 'commandExecution': '运行命令',
                              'collabAgentToolCall': '子智能体调用'}.get(kind, LABELS.get(subtype, '其他活动')))[:160],
            'label_message': message('activity.named', name=name) if name else
                message('activity.command' if kind == 'commandExecution' else 'activity.file_change')
                if kind in ('commandExecution', 'fileChange') else None,
            'tool_name': name or None, 'call_id': item.get('call_id', item.get('callId')),
            'client_id': item.get('clientId', item.get('client_id')),
            'process_id': item.get('processId', item.get('process_id')),
            'start_ms': valid_ms(start), 'end_ms': valid_ms(end),
            'start_source': source if valid_ms(start) else None,
            'end_source': source if valid_ms(end) else None,
            'status': status(item.get('status', 'completed' if end is not None else 'inProgress')),
            'quality': [], 'mirrored': item.get('delivery') == 'async', **raw_coordination(item)}


def settled_model_items(turns, phases, *, read_complete, quality=(), absent_turns=()):
    """Membership for reliable, ended JSONL turns; None means no new decision."""
    if not read_complete or quality:
        return None
    settled = {(t['scope_id'], t['thread_id'], t['turn_id']): set() for t in turns
               if t['status'] in ('completed', 'failed', 'interrupted') and t.get('end_ms') is not None}
    settled.update((tuple(key), set()) for key in absent_turns)
    for phase in phases:
        key = (phase['scope_id'], phase['thread_id'], phase['turn_id'])
        if key in settled:
            settled[key].add(({'message': 'agentMessage'}.get(phase['item_type'], phase['item_type']), phase['item_id']))
    return settled


def retired_model_supplement(scope, thread, turn, item, item_type, settled):
    """Only model generation retires at its turn boundary, never tool execution."""
    key = (scope, thread, turn)
    item_type = {'message': 'agentMessage'}.get(item_type, item_type)
    return (item_type in MODEL_TYPES.keys() | {'function_call', 'custom_tool_call'} and
            key in settled and (item_type, item) not in settled[key])


def apply_model_membership(phase, settled):
    if settled is not None:
        phase = {**phase, 'history_retired': retired_model_supplement(
            phase['scope_id'], phase['thread_id'], phase['turn_id'], phase['item_id'], phase.get('item_type'), settled)}
    return phase


def display_phase(phase):
    """Project old sidecars and fresh events with the same visibility policy."""
    phase = dict(phase)
    phase.pop('observation', None)  # Internal basis, not a second visible activity.
    if phase.get('tool_name'):
        phase['label_message'] = message('activity.named', name=phase['tool_name'])
    elif phase.get('item_type') in ('commandExecution', 'fileChange'):
        phase['label_message'] = message('activity.command' if phase['item_type'] == 'commandExecution' else 'activity.file_change')
    if phase.get('item_type') == 'contextCompaction':
        phase.update(track='model', kind='contextCompaction', label=LABELS['contextCompaction'])
    if phase.get('history_retired') or (phase.get('track') == 'marker' and phase.get('kind') != 'input'):
        return None
    return phase


def merge_phase(old, new):
    """Merge the same semantic phase, never reopen terminal work on a late start."""
    if old is None:
        result = dict(new)
    else:
        result = {**old, **{k: v for k, v in new.items() if v is not None}}
        result['quality'] = sorted(set(old.get('quality', [])) | set(new.get('quality', [])))
        result['mirrored'] = old.get('mirrored', False) or new.get('mirrored', False)
        # History replaces a live supplement. Never retain an alternate history
        # underneath it to resurrect after the authoritative source changes.
        result.pop('observation', None)
        rank = lambda value: 3 if value == 'history' else 2 if value == 'producer' else 1 if value == 'receipt' else 0
        for bound in ('start', 'end'):
            key, provenance = bound + '_ms', bound + '_source'
            if old.get(key) is not None and (new.get(key) is None or
                    rank(old.get(provenance)) >= rank(new.get(provenance))):
                result[key], result[provenance] = old[key], old.get(provenance)
        if old.get('status') not in ('active', 'waiting') and new.get('status') == 'active':
            result['status'] = old['status']
        terminal = {'completed', 'failed', 'interrupted', 'declined'}
        if old.get('status') in terminal and new.get('status') in terminal and old['status'] != new['status']:
            result['quality'] = sorted(set(result['quality']) | {'lifecycle_conflict'})
        if old.get('history_terminal') and not new.get('history_terminal'):
            result['status'] = old['status']
        if old.get('kind') == 'file_edit_input':
            result['kind'] = 'file_edit_input'
        if new.get('execution_confirmed'):
            result['quality'] = [q for q in result['quality'] if q != 'execution_unconfirmed']
    result.pop('observation', None)  # Also discard legacy sidecar shadow state.
    if result.get('start_ms') is None:
        result['quality'] = sorted(set(result.get('quality', [])) | {'start_unknown'})
    else:
        result['quality'] = [q for q in result.get('quality', []) if q != 'start_unknown']
    if result.get('start_ms') is not None and result.get('end_ms') is not None and result.get('end_source') in ('producer', 'history'):
        result['quality'] = [q for q in result.get('quality', []) if q not in ('missing_item_end', 'collector_restarted', 'observation_stopped')]
    if result.get('start_ms') is not None and result.get('end_ms') is not None and result['end_ms'] < result['start_ms']:
        result['quality'] = sorted(set(result.get('quality', [])) | {'clock_conflict'})
    return result


def merge_history_phase(observed, historical, *, read_complete=True):
    """Apply current JSONL, invalidating facts removed from a verified source."""
    basis = observed
    if historical is None and not read_complete:
        return observed
    if observed and observed.get('history_terminal'):
        basis = {k: v for k, v in observed.items()
                 if k not in ('history_terminal', 'exit_code', 'observation')}
        basis['status'] = 'unknown'
        for bound in ('start', 'end'):
            if basis.get(bound + '_source') == 'history':
                basis[bound + '_ms'] = basis[bound + '_source'] = None
        if historical is None or historical.get('status') in ('active', 'waiting'):
            basis['quality'] = sorted(set(basis.get('quality', [])) | {'history_changed'})
    result = merge_phase(basis, historical) if historical is not None else basis
    if historical is not None and result.get('history_retired'):
        # Positive identity evidence survives a partial ancestor chain. Only
        # absence decisions require complete history coverage.
        result['history_retired'] = False
    return result


class TimelineBook:
    def __init__(self, store=None):
        self.lock = threading.RLock()
        self.store = store
        self.phases = OrderedDict()
        self.turns = OrderedDict()
        self.relations = OrderedDict()
        self.revision = 0
        self.engine = None
        self.waiting = {}
        self.pins = {}
        self.live = {}
        self.instance = identity(time.time_ns())
        self.clock_anchor = None
        self.anchors = {}
        self.clock_jumps = set()
        self.last_prune = 0
        self.current_backend = None
        if store:
            for record_type, record in store.load():
                if record_type == 'phase' and record.get('status') in ('active', 'waiting'):
                    record.update(status='unknown', quality=sorted(set(record.get('quality', [])) | {'collector_restarted'}))
                    store.put(record_type, record)
                target = {'phase': self.phases, 'turn': self.turns, 'relation': self.relations}.get(record_type)
                if target is not None:
                    target[record['id']] = record

    def _save(self, record_type, record):
        table = {'phase': self.phases, 'turn': self.turns, 'relation': self.relations}[record_type]
        if table.get(record['id']) != record:
            table[record['id']] = record
            self.revision += 1
            if self.store:
                self.store.put(record_type, record)

    def apply_history(self, summary):
        """Correct already-observed phases; JSONL remains the content store."""
        with self.lock:
            coverage = HistoryCoverage.from_summary(summary)
            settled = settled_model_items(summary.get('turns', []), summary.get('phases', []),
                                          read_complete=summary.get('read_complete', False), quality=summary.get('quality', []),
                                          absent_turns=[(summary['scope_id'], summary['thread_id'], turn)
                                                        for turn in summary.get('source_absent_turns', [])])
            if settled is not None:
                for phase in list(self.phases.values()):
                    if ((phase['scope_id'], phase['thread_id']) == (summary['scope_id'], summary['thread_id']) and
                            coverage.covers(phase['turn_id'], phase['item_id']) and
                            not (phase['turn_id'] in summary.get('source_absent_turns', []) and phase['status'] in ('active', 'waiting'))):
                        updated = apply_model_membership(phase, settled)
                        if bool(phase.get('history_retired')) != updated['history_retired']:
                            self._put(updated)
            facts = {p['id']: p for p in summary.get('phases', []) if p.get('history_terminal')}
            for phase in list(self.phases.values()):
                if ((phase['scope_id'], phase['thread_id']) == (summary['scope_id'], summary['thread_id']) and
                        coverage.covers(phase['turn_id'], phase['item_id'])):
                    self._save('phase', merge_history_phase(phase, facts.get(phase['id']),
                                                          read_complete=summary.get('read_complete', False)))
            source_turns = {t['id']: t for t in summary.get('turns', [])}
            for turn in list(self.turns.values()):
                if ((turn['scope_id'], turn['thread_id']) == (summary['scope_id'], summary['thread_id']) and
                        turn['turn_id'] in summary.get('requested_turns', [])):
                    self._save('turn', merge_history_phase(turn, source_turns.get(turn['id']),
                                                         read_complete=summary.get('read_complete', False)))
            for turn in summary.get('turns', []):
                old = self.turns.get(turn['id'])
                if old and turn['status'] in ('completed', 'failed', 'interrupted'):
                    for phase in list(self.phases.values()):
                        if (phase['scope_id'], phase['thread_id'], phase['turn_id']) != (turn['scope_id'], turn['thread_id'], turn['turn_id']):
                            continue
                        if phase['status'] not in ('active', 'waiting'):
                            continue
                        activity = next((t.activities.get(phase['item_id']) for t in self.engine.threads.values()
                                         if t.id == turn['thread_id'] and
                                         self.engine.backends.get(t.backend, {}).get('home') == turn['scope_id']), None) if self.engine else None
                        if not activity or not activity.active:
                            self._put({**phase, 'status': 'unknown',
                                       'quality': sorted(set(phase['quality']) | {'missing_item_end'})})

    def _turn(self, scope, thread, turn, at, state=None, start=None, end=None):
        if not thread or not turn:
            return
        key = identity('turn', scope, thread, turn)
        old = self.turns.get(key, {})
        basis = old
        record = {**basis, 'id': key, 'scope_id': scope, 'thread_id': thread, 'turn_id': turn,
                  'status': state or basis.get('status', 'unknown'), 'updated_ms': at,
                  'observation_backend': self.current_backend,
                  'start_ms': basis.get('start_ms'), 'end_ms': basis.get('end_ms')}
        record.pop('history_terminal', None)
        record.pop('observation', None)
        if start is not None and record['start_ms'] is None:
            record['start_ms'] = start
            record['start_source'] = 'receipt'
        if end is not None:
            record['end_ms'] = end
            record['end_source'] = 'receipt'
        self._save('turn', merge_phase(old or None, record))

    def _put(self, record):
        if not record:
            return
        self._save('phase', merge_phase(self.phases.get(record['id']), record))

    def _relation(self, scope, parent, turn, child, call, operation=None):
        if not child or child == parent:
            return
        record = {'id': identity('relation', scope, parent, turn, child, call), 'scope_id': scope,
                  'parent_thread_id': parent, 'parent_turn_id': turn, 'child_thread_id': child,
                  'call_id': call, 'operation': operation}
        self._save('relation', record)

    def observe(self, event, engine):
        with self.lock:
            self.engine = engine
            self.current_backend = event.backend
            scope = engine.backends.get(event.backend, {}).get('home') or event.backend
            if event.wall_time > 0:
                anchor = self.anchors.setdefault(event.backend, (event.at, event.wall_time * 1000))
                if not event.replayed and abs(event.wall_time * 1000 - (anchor[1] + (event.at - anchor[0]) * 1000)) > 1000:
                    if event.backend not in self.clock_jumps:
                        for phase in list(self.phases.values()):
                            if phase.get('observation_backend') == event.backend and phase['status'] in ('active', 'waiting'):
                                self._put({**phase, 'quality': sorted(set(phase['quality']) | {'wall_clock_changed'})})
                    self.clock_jumps.add(event.backend)
                if (event.data.get('method') == 'turn/started' and getattr(event, 'turn_started_accepted', False)
                        and event.backend in self.clock_jumps):
                    self.anchors[event.backend] = anchor = (event.at, event.wall_time * 1000)
                    self.clock_jumps.discard(event.backend)
                self.clock_anchor = anchor
            at = self.clock_anchor[1] + (event.at - self.clock_anchor[0]) * 1000 if self.clock_anchor else event.at * 1000
            data = event.data
            if event.direction == 'lifecycle':
                if data.get('type') in ('bridge/gap', 'bridge/disconnected', 'bridge/stopped', 'bridge/reconnected'):
                    self.gap(scope, at, event.backend)
                return
            method, params = data.get('method'), data.get('params', {})
            request = params.get('requestId') if method == 'serverRequest/resolved' else data.get('id')
            if method == 'serverRequest/resolved' or (event.direction == 'protocol_in' and not method):
                marker = self.waiting.pop((event.backend, request), None)
                if marker and marker in self.phases:
                    phase = self.phases[marker]
                    decision = data.get('result', {}).get('decision')
                    self._put({**phase, 'end_ms': at, 'end_source': 'receipt',
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
                for old_turn in list(self.turns.values()):
                    if (old_turn['scope_id'], old_turn['thread_id']) == (scope, thread) and old_turn['turn_id'] != turn and old_turn['status'] == 'active':
                        self._save('turn', {**old_turn, 'status': 'unknown'})
                for phase in list(self.phases.values()):
                    if (phase['scope_id'], phase['thread_id']) != (scope, thread) or phase['turn_id'] == turn or phase['status'] not in ('active', 'waiting'):
                        continue
                    previous = engine.threads[(event.backend, thread)].activities.get(phase['item_id'])
                    if not previous or not previous.active:
                        self._put({**phase, 'status': 'unknown', 'observed_until_ms': at,
                                   'quality': sorted(set(phase['quality']) | {'missing_item_end'})})
                self._turn(scope, thread, turn, at, 'active', start=at)
            elif method == 'turn/completed':
                self._turn(scope, thread, turn, at, status(params.get('turn', {}).get('status')), end=at)
                for record in list(self.phases.values()):
                    if (record['scope_id'], record['thread_id'], record['turn_id']) != (scope, thread, turn) or record['status'] not in ('active', 'waiting'):
                        continue
                    activity = engine.threads.get((event.backend, thread)).activities.get(record['item_id'])
                    if activity and activity.active:
                        continue
                    # A turn boundary does not supply the missing item completion.
                    self._put({**record, 'status': 'unknown', 'observed_until_ms': at,
                               'quality': sorted(set(record['quality']) | {'missing_item_end'})})
            elif method in ('item/started', 'item/completed') or data.get('type') in ('response.output_item.added', 'response.output_item.done'):
                if known.get('mirrored') or item.get('delivery') == 'async':
                    for record in list(self.phases.values()):
                        if record['scope_id'] == scope and record['thread_id'] == thread and record['item_id'] == item_id:
                            self._put({**record, 'mirrored': True})
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
                    record['observation_instance'] = self.instance
                    record['observation_backend'] = event.backend
                    record['observed_until_ms'] = at
                    if event.backend in self.clock_jumps:
                        record['quality'].append('wall_clock_changed')
                    self._put(record)
                    self._turn(scope, thread, turn, at)
                if not raw:
                    for child in item.get('receiverThreadIds', []):
                        self._relation(scope, thread, turn, child, item_id, item.get('tool'))
                    if item.get('agentThreadId'):
                        self._relation(scope, thread, turn, item['agentThreadId'], item_id, item.get('kind'))
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
                    self.waiting[(event.backend, data.get('id'))] = marker['id']
                self._put(marker)
            elif method == 'item/commandExecution/outputDelta':
                key = phase_id(scope, thread, turn, 'canonical', item_id)
                record = self.phases.get(key)
                activity = engine.threads.get((event.backend, thread)).activities.get(item_id)
                if record and record['status'] == 'waiting' and activity and activity.kind == 'tool':
                    self._put({**record, 'status': 'active', 'observed_until_ms': at, 'execution_confirmed': True})
            self._classify_input(scope, thread, turn, item_id, engine, event.backend)
            self._capture_output(scope, thread, turn, item_id, params)

    def _classify_input(self, scope, thread, turn, item, engine, backend):
        key = phase_id(scope, thread, turn, 'input', item)
        record = self.phases.get(key)
        if not record or record['kind'] == 'file_edit_input':
            return
        for stream_key, stream in engine.streams.items():
            if stream_key[:2] == (backend, item) and stream.kind == 'tool_input' and stream.text:
                if edit_input(record.get('tool_name'), stream.text):
                    self._put({**record, 'kind': 'file_edit_input', 'label': LABELS['file_edit_input']})

    def _capture_output(self, scope, thread, turn, item, params):
        key = phase_id(scope, thread, turn, 'canonical', item)
        if key not in self.pins or not isinstance(params.get('delta'), str):
            return
        state = self.live.setdefault(key, {'text': '', 'partial': True})
        chunk = params['delta']
        state['text'] = (state['text'] + chunk)[-262144:]
        state['partial'] = True  # subscription may have started after the first output

    def gap(self, scope, at, backend=None):
        for turn in list(self.turns.values()):
            if (turn['scope_id'] == scope and turn['status'] == 'active' and
                    (backend is None or turn.get('observation_backend') == backend)):
                marker = item_phase(scope, turn['thread_id'], turn['turn_id'],
                                    {'id': identity('gap', backend, at), 'type': 'sleep'}, at, at, 'receipt', namespace='marker')
                marker.update(kind='gap', label=LABELS['gap'], status='completed')
                self._put(marker)
        for record in list(self.phases.values()):
            if (record['scope_id'] == scope and record['status'] in ('active', 'waiting') and
                    (backend is None or record.get('observation_backend') == backend)):
                self._put({**record, 'status': 'unknown', 'observed_until_ms': at,
                           'quality': sorted(set(record['quality']) | {'observation_gap'})})
        for turn in list(self.turns.values()):
            if (turn['scope_id'] == scope and turn['status'] == 'active' and
                    (backend is None or turn.get('observation_backend') == backend)):
                self._save('turn', {**turn, 'status': 'unknown'})

    def snapshot(self, scope, thread):
        with self.lock:
            return {'revision': self.revision, 'phases': deepcopy([p for p in self.phases.values() if p['scope_id'] == scope]),
                    'turns': deepcopy([p for p in self.turns.values() if p['scope_id'] == scope]),
                    'relations': deepcopy([p for p in self.relations.values() if p['scope_id'] == scope])}

    def pin(self, phase, now=None):
        with self.lock:
            self.pins[phase] = (now or time.time()) + 10

    def live_detail(self, phase):
        with self.lock:
            self.pin(phase['id'])
            if self.engine:
                for thread in self.engine.threads.values():
                    scope = self.engine.backends.get(thread.backend, {}).get('home') or thread.backend
                    if scope != phase['scope_id'] or thread.id != phase['thread_id']:
                        continue
                    streams = [s for k, s in self.engine.streams.items() if k[:2] == (thread.backend, phase['item_id']) and s.text]
                    if streams:
                        return {'text': '\n'.join(s.text for s in streams),
                                'state': 'available' if all(s.quality in ('complete', 'reconciled') for s in streams) else 'partial'}
            if phase['id'] in self.live:
                return {'text': self.live[phase['id']]['text'], 'state': 'partial'}
            return None

    def prune(self, now=None):
        with self.lock:
            now = now or time.time()
            if now - self.last_prune < 5:
                return
            self.last_prune = now
            self.pins = {k: v for k, v in self.pins.items() if v > now}
            self.live = {k: v for k, v in self.live.items() if k in self.pins}
            keep = set()
            groups = {}
            for turn in self.turns.values():
                groups.setdefault((turn['scope_id'], turn['thread_id']), []).append(turn)
            for turns in groups.values():
                recent = sorted(turns, key=lambda t: t.get('start_ms') or t.get('updated_ms') or 0, reverse=True)[:6]
                keep.update((t['scope_id'], t['thread_id'], t['turn_id']) for t in recent
                            if t['status'] == 'active' or now * 1000 - t.get('updated_ms', 0) < 600000)
            for phase in self.phases.values():
                if phase['status'] == 'active' or phase['id'] in self.pins:
                    keep.add((phase['scope_id'], phase['thread_id'], phase['turn_id']))
            changed = True
            while changed:
                changed = False
                for relation in self.relations.values():
                    parent = (relation['scope_id'], relation['parent_thread_id'], relation['parent_turn_id'])
                    if parent not in keep and any(s == relation['scope_id'] and t == relation['child_thread_id'] for s, t, _ in keep):
                        keep.add(parent)
                        changed = True
            for kind, table in (('phase', self.phases), ('turn', self.turns)):
                for key, record in list(table.items()):
                    if (record['scope_id'], record['thread_id'], record['turn_id']) not in keep:
                        del table[key]
                        self.revision += 1
                        if self.store:
                            self.store.delete(key)
            for key, record in list(self.relations.items()):
                if (record['scope_id'], record['parent_thread_id'], record['parent_turn_id']) not in keep:
                    del self.relations[key]
                    if self.store:
                        self.store.delete(key)
            self.waiting = {key: phase for key, phase in self.waiting.items()
                            if phase in self.phases and self.phases[phase]['status'] in ('active', 'waiting')}
