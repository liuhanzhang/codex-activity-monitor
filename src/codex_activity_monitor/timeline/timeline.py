"""Identity-preserving phase projection. No message or tool bodies are persisted."""

from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
import math
import threading
import time

from codex_activity_monitor.agents.codex.events import TOOL_TYPES
from codex_activity_monitor.core.tool_labels import code_tool_names, file_edit_preview, is_patch_tool, tool_name
from codex_activity_monitor.core.agent_relations import raw_coordination
from codex_activity_monitor.i18n.i18n import message
from codex_activity_monitor.agents.codex.history_contract import HistoryCoverage
from codex_activity_monitor.core.state import source_traits

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


FILE_EDIT_TOOLS = ('code_action', 'write_to_file', 'replace_file_content',
                   'multi_replace_file_content', 'edit_file', 'write_file')


def edit_input(name, code):
    if is_patch_tool(name) or name in FILE_EDIT_TOOLS:
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


def settled_model_items(turns, phases, *, read_complete, quality=(), absent_turns=(), planner_steps=()):
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
            traits = source_traits(phase.get('source'))
            if traits.synthesizes_tool_input_phase and phase['track'] == 'tool':
                settled[key].add(('function_call', phase['item_id']+'-input'))
            if traits.synthesizes_reasoning_phase and phase['item_type'] == 'agentMessage':
                settled[key].add(('reasoning', phase['item_id']+'-summary'))
    for scope, thread, turn, index in planner_steps:
        if (scope, thread, turn) in settled:
            settled[scope, thread, turn].add(('reasoning', f'{thread}-step-{index}-summary'))
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
    result = dict(new) if old is None else {**old, **{k: v for k, v in new.items() if v is not None}}
    traits = source_traits(result.get('source'))
    producer_tool = traits.producer_tool_terminal and result.get('track') == 'tool'
    if old is not None:
        result['quality'] = sorted(set(old.get('quality', [])) | set(new.get('quality', [])))
        result['mirrored'] = old.get('mirrored', False) or new.get('mirrored', False)
        # History replaces a live supplement. Never retain an alternate history
        # underneath it to resurrect after the authoritative source changes.
        result.pop('observation', None)
        ranks = traits.provenance_ranks(result.get('track'))
        for bound in ('start', 'end'):
            key, provenance = bound + '_ms', bound + '_source'
            old_rank = ranks.get(old.get(provenance), 0)
            new_rank = ranks.get(new.get(provenance), 0)
            newer_native_bound = traits.prefers_newer_producer_bound and new.get(provenance) == 'producer'
            if old.get(key) is not None and (new.get(key) is None or old_rank > new_rank or
                    (old_rank == new_rank and not newer_native_bound)):
                result[key], result[provenance] = old[key], old.get(provenance)
        if old.get('status') not in ('active', 'waiting') and new.get('status') == 'active':
            result['status'] = old['status']
        terminal = {'completed', 'failed', 'interrupted', 'declined'}
        if old.get('history_terminal') and not new.get('history_terminal'):
            result['status'] = old['status']
        if producer_tool:
            for phase in (new, old):
                if phase.get('end_source') == 'producer' and phase.get('status') in terminal:
                    result['status'] = phase['status']
                    break
        if (old.get('status') in terminal and new.get('status') in terminal and old['status'] != new['status'] and
                (not producer_tool or old.get('end_source') == new.get('end_source'))):
            result['quality'] = sorted(set(result['quality']) | {'lifecycle_conflict'})
        if old.get('kind') == 'file_edit_input':
            result['kind'] = 'file_edit_input'
        if new.get('execution_confirmed'):
            result['quality'] = [q for q in result['quality'] if q != 'execution_unconfirmed']
    result.pop('observation', None)  # Also discard legacy sidecar shadow state.
    exit_code = result.get('exit_code')
    if producer_tool and result.get('status') == 'completed' and type(exit_code) is int and exit_code != 0:
        result['status'] = 'failed'
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
    if (observed and historical and historical.get('step_type') == 'GENERIC' and
            observed.get('step_type') not in (None, 'GENERIC')):
        # A generic transcript row can settle the same step without losing
        # its more specific type and label from the state or trajectory API.
        historical = {**historical, **{key: observed[key] for key in
                      ('item_type', 'step_type', 'tool_name', 'label', 'label_message', 'exit_code') if key in observed}}
    if observed and observed.get('history_terminal'):
        basis = {k: v for k, v in observed.items()
                 if k not in ('history_terminal', 'exit_code', 'observation')}
        if (source_traits(observed.get('source')).producer_tool_terminal and observed.get('track') == 'tool' and
                observed.get('end_source') == 'producer'):
            if 'exit_code' in observed:
                basis['exit_code'] = observed['exit_code']
        else:
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

    def record_turn(self, scope, thread, turn, updated_ms, *, status=None, start_ms=None, end_ms=None, backend=None):
        if not thread or not turn:
            return
        key = identity('turn', scope, thread, turn)
        old = self.turns.get(key, {})
        self._save('turn', {**old, 'id': key, 'scope_id': scope, 'thread_id': thread, 'turn_id': turn,
                            'start_ms': old.get('start_ms') or start_ms, 'end_ms': end_ms or old.get('end_ms'),
                            'updated_ms': updated_ms, 'status': status or old.get('status', 'unknown'),
                            'observation_backend': backend})

    def record_turn_request(self, scope, thread, turn, request_id, sample):
        if sample is None:
            return
        key = identity('turn', scope, thread, turn)
        old = self.turns.get(key)
        if old is None:
            return
        requests = dict(old.get('model_requests', {}))
        requests[str(request_id)] = sample
        self._save('turn', {**old, 'model_requests': dict(list(requests.items())[-128:])})

    def record_phase(self, record, *, reopen_unknown=False):
        if not record:
            return
        current = self.phases.get(record['id'])
        merged = merge_phase(current, record)
        if reopen_unknown and current and current.get('status') == 'unknown' and record.get('status') in ('active', 'waiting'):
            merged = {**merged, 'status': record['status']}
        self._save('phase', merged)

    _put = record_phase

    def record_relation(self, scope, parent, turn, child, call, operation=None, *, source=None):
        if not child or child == parent:
            return
        record = {'id': identity('relation', scope, parent, turn, child, call), 'scope_id': scope,
                  'parent_thread_id': parent, 'parent_turn_id': turn, 'child_thread_id': child,
                  'call_id': call, 'operation': operation}
        if source:
            record['source'] = source
        self._save('relation', record)

    _relation = record_relation

    def set_live_content(self, phase_id, *, text='', input_text=None, at=None):
        entry = {'text': text, 'at': at}
        if input_text is not None:
            entry['input'] = input_text
        self.live[phase_id] = entry

    def observe(self, event, engine):
        from codex_activity_monitor.agents.codex.projection import observe_timeline_event
        observe_timeline_event(self, event, engine)

    def gap(self, scope, at, backend=None, *, thread=None):
        for turn in list(self.turns.values()):
            if (turn['scope_id'] == scope and turn['status'] == 'active' and
                    (thread is None or turn['thread_id'] == thread) and
                    (backend is None or turn.get('observation_backend') == backend)):
                marker = item_phase(scope, turn['thread_id'], turn['turn_id'],
                                    {'id': identity('gap', backend, at), 'type': 'sleep'}, at, at, 'receipt', namespace='marker')
                marker.update(kind='gap', label=LABELS['gap'], status='completed')
                self._put(marker)
        for record in list(self.phases.values()):
            if (record['scope_id'] == scope and record['status'] in ('active', 'waiting') and
                    (thread is None or record['thread_id'] == thread) and
                    (backend is None or record.get('observation_backend') == backend)):
                self._put({**record, 'status': 'unknown', 'observed_until_ms': at,
                           'quality': sorted(set(record['quality']) | {'observation_gap'})})
        for turn in list(self.turns.values()):
            if (turn['scope_id'] == scope and turn['status'] == 'active' and
                    (thread is None or turn['thread_id'] == thread) and
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
                if (not source_traits(record.get('source')).persistent_relations and
                        (record['scope_id'], record['parent_thread_id'], record['parent_turn_id']) not in keep):
                    del self.relations[key]
                    if self.store:
                        self.store.delete(key)
            # Sources whose child transcripts have no parent turn identifier
            # retain verified invocation links alongside the JSONL, within a cap.
            persistent = [key for key, record in self.relations.items()
                          if source_traits(record.get('source')).persistent_relations]
            for key in persistent[:-4096]:
                self.relations.pop(key)
                self.revision += 1
                if self.store:
                    self.store.delete(key)
            self.waiting = {key: phase for key, phase in self.waiting.items()
                            if phase in self.phases and self.phases[phase]['status'] in ('active', 'waiting')}
