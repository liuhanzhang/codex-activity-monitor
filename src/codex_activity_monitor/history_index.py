"""Source scanning, compact turn ranges and verified per-turn metadata indices."""

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from enum import IntFlag
import hashlib
import json
import os
import uuid

from .agent_presentation import agent_metadata
from .history_content import ABSENT, CANONICAL_TYPES, item_content, raw_section, source_truncated
from .history_read import HistoryChanged, HistoryRead, HistoryReference, explicit_turn, file_stamp
from .timeline import HISTORY_SEED_LIMIT, HISTORY_SEED_TYPES, identity, item_namespace, item_phase, phase_id, merge_history_phase, merge_phase, status, valid_ms

def timestamp(value):
    if isinstance(value, (int, float)):
        return valid_ms(value * 1000 if value < 100000000000 else value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() * 1000
        except ValueError:
            pass
    return None


def canonical_item(item):
    result = dict(item)
    result['type'] = CANONICAL_TYPES.get(item.get('type'), item.get('type'))
    if item.get('type') == 'Extension':
        result['type'] = {'web.search': 'webSearch', 'image_gen.generation': 'imageGeneration'}.get(item.get('kind'), 'extension')
    for snake, camel in (('process_id', 'processId'), ('receiver_thread_ids', 'receiverThreadIds'),
                         ('sender_thread_id', 'senderThreadId'), ('agents_states', 'agentsStates'),
                         ('agent_thread_id', 'agentThreadId')):
        if snake in result:
            result[camel] = result[snake]
    return result


class _HistoryProjection:
    def __init__(self, path, scope, thread):
        self.path, self.scope, self.thread = path, scope, thread
        self.offset = 0
        self.fingerprint = None
        self.anchor = None
        self.turn = None
        self.turn_ref = None
        self.file_stamp = None
        self.header_digest = None
        self.turns = OrderedDict()
        self.phases = OrderedDict()
        self.refs = {}
        self.outputs = {}
        self.relations = {}
        self.mode = None
        self.quality = set()
        self.revision = 0
        self.generation = None
        self.read_complete = False
        self.agent_metadata = {}

    def refresh(self):
        self.read_complete = False
        previous_metadata, self.agent_metadata = self.agent_metadata, {}
        with self.path.open('rb') as stream:
            stat = os.fstat(stream.fileno())
            signature = (stat.st_dev, stat.st_ino)
            first = stream.readline()
            header_digest = hashlib.sha256(first).hexdigest()
            try:
                meta = json.loads(first)
            except ValueError:
                raise ValueError('history_header_invalid') from None
            if not isinstance(meta, dict) or not isinstance(meta.get('payload'), dict) or meta.get('type') != 'session_meta' or meta['payload'].get('id') != self.thread:
                raise ValueError('history_identity_mismatch')
            self.mode = meta['payload'].get('history_mode', 'legacy')
            self.agent_metadata = agent_metadata(meta['payload'])
            if previous_metadata != self.agent_metadata:
                self.revision += 1
            stream.seek(max(0, self.offset - 256))
            anchor = hashlib.sha256(stream.read(min(self.offset, 256))).digest()
            same_size_rewrite = (self.file_stamp is not None and stat.st_size == self.file_stamp[2] and
                                 file_stamp(stat) != self.file_stamp)
            if (self.fingerprint != signature or stat.st_size < self.offset or same_size_rewrite or
                    self.header_digest != header_digest or (self.anchor is not None and self.anchor != anchor)):
                self.generation = uuid.uuid4().hex
                self.offset = 0
                self.turn = None
                self.turn_ref = None
                self.turns.clear(); self.phases.clear(); self.refs.clear(); self.outputs.clear(); self.relations.clear()
                self.quality.clear()
                self._reset_projection()
            self.fingerprint = signature
            self.header_digest = header_digest
            stream.seek(self.offset)
            while True:
                start = stream.tell()
                line = stream.readline()
                if not line or not line.endswith(b'\n'):
                    break
                self.offset = stream.tell()
                try:
                    row = json.loads(line)
                    self._row(row, start, len(line), hashlib.sha256(line).hexdigest())
                except (ValueError, TypeError, KeyError, AttributeError):
                    self.quality.add('malformed_history_record')
                self.revision += 1
            self.read_complete = not line
            stream.seek(max(0, self.offset - 256))
            self.anchor = hashlib.sha256(stream.read(min(self.offset, 256))).digest()
            current = os.fstat(stream.fileno())
            if (current.st_dev, current.st_ino) != signature or file_stamp(self.path.stat()) != file_stamp(current):
                raise HistoryChanged()
            self.file_stamp = file_stamp(current)
            self.read_complete = self.read_complete and self.offset == current.st_size

    def _reset_projection(self):
        pass

    def _turn_record(self, turn, at, start=None, end=None, state=None, parent=None):
        if not turn:
            return
        old = self.turns.get(turn, {})
        self.turns[turn] = {**old, 'id': identity('turn', self.scope, self.thread, turn),
                            'scope_id': self.scope, 'thread_id': self.thread, 'turn_id': turn,
                            'start_ms': start or old.get('start_ms'), 'end_ms': end or old.get('end_ms'),
                            'updated_ms': at or old.get('updated_ms'),
                            'parent_turn_id': parent or old.get('parent_turn_id'),
                            'status': state or old.get('status', 'unknown'),
                            'start_source': 'history' if start else old.get('start_source'),
                            'end_source': 'history' if end else old.get('end_source'),
                            'history_terminal': (state or old.get('status')) in ('completed', 'failed', 'interrupted')}

    def _row(self, row, offset, length, digest):
        outer, payload = row.get('type'), row.get('payload', {})
        at = timestamp(row.get('timestamp'))
        kind = payload.get('type')
        if outer == 'turn_context':
            self.turn = payload.get('turn_id')
            self.turn_ref = HistoryReference(offset, length, digest, self.fingerprint, 'turn_context', self.turn)
            self._turn_record(self.turn, at, parent=payload.get('parent_turn_id'))
        elif outer == 'event_msg' and kind in ('task_started', 'turn_started'):
            self.turn = payload.get('turn_id') or payload.get('turn_attribution', {}).get('turn_id')
            self.turn_ref = HistoryReference(offset, length, digest, self.fingerprint, 'task_started', self.turn)
            parent = (payload.get('turn_attribution') or {}).get('parent_turn_id')
            self._turn_record(self.turn, at, start=timestamp(payload.get('started_at')), state='active', parent=parent)
        elif outer == 'event_msg' and kind in ('task_complete', 'turn_complete', 'turn_aborted'):
            turn = payload.get('turn_id') or self.turn
            self._turn_record(turn, at, end=timestamp(payload.get('completed_at')) or at,
                              state='interrupted' if kind == 'turn_aborted' else 'completed')
        elif outer == 'event_msg' and kind == 'item_completed':
            turn = payload.get('turn_id')
            if payload.get('thread_id') not in (None, self.thread):
                self.quality.add('history_identity_mismatch')
                return
            item = canonical_item(payload.get('item', {}))
            if not turn:
                self.quality.add('turn_identity_unavailable')
            if item.get('type') == 'subAgentActivity':
                child = item.get('agentThreadId')
                if child:
                    self._relation(turn, child, item.get('id'), item.get('kind'))
                return
            phase = (item_phase(self.scope, self.thread, turn, item,
                                valid_ms(payload.get('started_at_ms')), valid_ms(payload.get('completed_at_ms')), 'history')
                     if self._include_item(turn, item) else None)
            if phase:
                phase['status'] = status(item.get('status', 'completed'))
                phase['history_terminal'] = phase['status'] in ('completed', 'failed', 'interrupted', 'declined')
                code = payload.get('item', {}).get('exit_code', item.get('exitCode'))
                if isinstance(code, int) and not isinstance(code, bool):
                    phase['exit_code'] = code
                phase['observed_until_ms'] = at
                self._phase(phase, offset, length, 'canonical', digest)
                self._turn_record(turn, at)
            for child in item.get('receiverThreadIds', []):
                self._relation(turn, child, item.get('id'), item.get('tool'))
        elif outer == 'response_item':
            if kind in ('function_call_output', 'custom_tool_call_output'):
                if payload.get('call_id'):
                    turn = explicit_turn(payload) or self.turn
                    self._output(turn, payload['call_id'], HistoryReference(offset, length, digest,
                        self.fingerprint, 'output', turn, None if explicit_turn(payload) else self.turn_ref))
                return
            if kind not in ('message', 'agentMessage', 'reasoning', 'function_call', 'custom_tool_call'):
                return
            if kind == 'message' and payload.get('role') not in ('assistant', 'user'):
                return
            # Paginated canonical records provide user/model items; raw calls supply missing parameters.
            if self.mode == 'paginated' and kind not in ('function_call', 'custom_tool_call'):
                return
            item = dict(payload)
            if kind == 'message' and payload.get('role') == 'user':
                item['type'] = 'userMessage'
            if not item.get('id'):
                item['id'] = payload.get('call_id') or 'offset-' + str(offset)
            turn = explicit_turn(payload) or self.turn
            if not turn:
                self.quality.add('turn_identity_unavailable')
            phase = item_phase(self.scope, self.thread, turn, item, source='history') if self._include_item(turn, item) else None
            if phase:
                phase.update(status='completed', observed_until_ms=at)
                self._phase(phase, offset, length, 'raw', digest, None if explicit_turn(payload) else self.turn_ref)
                self._turn_record(turn, at)

    def _include_item(self, turn, item):
        return True

    def _phase(self, *args):
        raise NotImplementedError

    def _output(self, *args):
        raise NotImplementedError

    def _relation(self, turn, child, call, operation):
        if turn and child and child != self.thread:
            key = identity('relation', self.scope, self.thread, turn, child, call)
            self.relations[key] = {'id': key, 'scope_id': self.scope, 'parent_thread_id': self.thread,
                                   'parent_turn_id': turn, 'child_thread_id': child,
                                   'call_id': call, 'operation': operation}

class RolloutIndex(_HistoryProjection):
    """Metadata and content references for a materialized reading selection."""
    def _phase(self, phase, offset, length, source, digest, context=None):
        self.phases[phase['id']] = merge_phase(self.phases.get(phase['id']), phase)
        self.refs[phase['id']] = HistoryReference(offset, length, digest, self.fingerprint,
                                                source, phase['turn_id'], context)

    def _output(self, turn, call, ref):
        self.outputs[(turn, call)] = ref

    def read_ref(self, ref, reader=None):
        if reader is not None:
            return reader.read_ref(ref)
        with HistoryRead(self.path, self.thread, self.fingerprint, self.header_digest) as reader:
            return reader.read_ref(ref)

    def detail(self, phase):
        with HistoryRead(self.path, self.thread, self.fingerprint, self.header_digest) as reader:
            return self._detail(phase, reader)

    def _detail(self, phase, reader):
        ref = self.refs.get(phase['id'])
        if not ref:
            return None
        payload = self.read_ref(ref, reader)
        item = payload.get('item', {}) if ref.source == 'canonical' else payload
        if ref.source == 'canonical' and (payload.get('turn_id') != phase['turn_id'] or item.get('id') != phase['item_id']):
            raise ValueError('history_changed')
        if ref.turn_id != phase['turn_id'] or (ref.source == 'raw' and (item.get('id') or item.get('call_id') or 'offset-' + str(ref.offset)) != phase['item_id']):
            raise ValueError('history_changed')
        sections, attachments = item_content(item)
        output = ABSENT
        call = item.get('call_id')
        output_key = (phase['turn_id'], call)
        if item.get('type') in ('function_call', 'custom_tool_call') and output_key in self.outputs:
            returned = self.read_ref(self.outputs[output_key], reader)
            if returned.get('call_id') != call:
                raise ValueError('history_changed')
            output = returned.get('output', ABSENT)
            sections = [s for s in sections if s['id'] != 'output'] + [raw_section('output', '结果', output)]
        state = 'no_content' if phase.get('item_type') == 'contextCompaction' else (
            'source_truncated' if source_truncated(item, output) else 'available')
        return {'state': state, 'sections': sections,
                'attachments': attachments, 'source': 'codex_history'}



@dataclass(slots=True)
class _Span:
    start: int
    end: int
    context: HistoryReference | None
    digest: bytes = bytes(32)

    def append(self, end, line_digest):
        self.end = end
        self.digest = hashlib.sha256(self.digest+bytes.fromhex(line_digest)).digest()


@dataclass(frozen=True, slots=True)
class DetailSelection:
    phase_ids: frozenset[str]
    raw_calls: frozenset[str] = frozenset()
    output_calls: frozenset[str] = frozenset()
    relation_calls: frozenset[str] = frozenset()


class HistoryDetails(RolloutIndex):
    """Selected content references and explicitly linked call metadata only."""
    def __init__(self, path, scope, thread, turn, selection):
        super().__init__(path, scope, thread)
        self.selected_turn = turn
        self.selection = selection
        self.hint_signature = None

    @staticmethod
    def hint_key(phase):
        return tuple(phase.get(key) for key in ('namespace', 'item_id', 'item_type', 'call_id')) if phase else None

    def _include_item(self, turn, item):
        if turn != self.selected_turn:
            return False
        kind = item.get('type')
        selected = phase_id(self.scope, self.thread, turn, item_namespace(kind), item.get('id'))
        return (selected in self.selection.phase_ids or
                (kind in ('function_call', 'custom_tool_call') and item.get('call_id') in self.selection.raw_calls))

    def _output(self, turn, call, ref):
        if turn == self.selected_turn and call in self.selection.output_calls:
            super()._output(turn, call, ref)

    def _relation(self, *_):
        pass  # Supplied from the same verified directory after the call IDs are known.


class HistoryMetadata(_HistoryProjection):
    """Timeline metadata only; source bodies and output references stay lazy."""
    def _phase(self, phase, *_):
        self.phases[phase['id']] = merge_phase(self.phases.get(phase['id']), phase)

    def _output(self, *_):
        pass

    def _relation(self, *_):
        pass  # Read from the verified directory, without rebuilding all edges.


class HistoryFacts(HistoryMetadata):
    """Only explicitly selected current-fact metadata."""
    def __init__(self, path, scope, thread, selected_ids):
        super().__init__(path, scope, thread)
        self.selected_ids = frozenset(selected_ids)
        self.seed_phase_ids = frozenset()

    def _include_item(self, turn, item):
        return phase_id(self.scope, self.thread, turn, item_namespace(item.get('type')), item.get('id')) in self.selected_ids

class PhaseFlags(IntFlag):
    NONACTIVE = 1
    TERMINAL = 2
    SEED_TYPE = 4


class HistoryDirectory(_HistoryProjection):
    """Incremental turn directory; completed phase bodies/metadata stay uncached.

    Ranges retain explicit attribution and a digest of their fixed-size line
    digests. A selected turn can be verified without scanning unrelated turns.
    Active phases and spawn evidence serve cross-turn/background ownership.
    """
    def __init__(self, path, scope, thread):
        super().__init__(path, scope, thread)
        self.ranges = {}
        self.spawn_calls = set()
        self.phase_records = 0
        # Order is first source appearance, matching the full projection. A
        # compact ID/flag catalogue does not retain completed phase dictionaries.
        self.phase_catalogue: dict[str, dict[bytes, PhaseFlags]] = {}

    def _reset_projection(self):
        self.ranges.clear(); self.spawn_calls.clear(); self.phase_records = 0; self.phase_catalogue.clear()

    def _phase(self, phase, *_):
        self.phase_records += 1
        if phase.get('call_operation') == 'spawn':
            self.spawn_calls.add((phase['turn_id'], phase.get('call_id')))
        catalogue = self.phase_catalogue.setdefault(phase['turn_id'], {})
        key = bytes.fromhex(phase['id'])
        flags = catalogue.get(key, PhaseFlags(0))
        if phase['status'] not in ('active', 'waiting'):
            flags |= PhaseFlags.NONACTIVE
        terminal = phase.get('history_terminal', bool(flags & PhaseFlags.TERMINAL))
        flags = ((flags & PhaseFlags.NONACTIVE) |
                 (PhaseFlags.TERMINAL if terminal else PhaseFlags(0)) |
                 (PhaseFlags.SEED_TYPE if phase['item_type'] in HISTORY_SEED_TYPES else PhaseFlags(0)))
        catalogue[key] = flags
        if phase['status'] in ('active', 'waiting') and not (flags & PhaseFlags.NONACTIVE):
            self.phases[phase['id']] = merge_phase(self.phases.get(phase['id']), phase)
        else:
            self.phases.pop(phase['id'], None)

    def _output(self, *_):
        pass  # Output references are retained only in selected-turn indices.

    def _row(self, row, offset, length, digest):
        payload = row.get('payload', {})
        outer, kind = row.get('type'), payload.get('type')
        turn = None
        if outer == 'turn_context':
            turn = payload.get('turn_id')
        elif outer == 'event_msg':
            if kind in ('task_started', 'turn_started'):
                turn = payload.get('turn_id') or (payload.get('turn_attribution') or {}).get('turn_id')
            elif kind in ('task_complete', 'turn_complete', 'turn_aborted'):
                turn = payload.get('turn_id') or self.turn
            elif kind == 'item_completed':
                turn = payload.get('turn_id')
        elif outer == 'response_item' and kind in ('message', 'agentMessage', 'reasoning', 'function_call',
                'custom_tool_call', 'function_call_output', 'custom_tool_call_output'):
            turn = explicit_turn(payload) or self.turn
        if isinstance(turn, str) and turn:
            spans = self.ranges.setdefault(turn, [])
            if not spans or spans[-1].end != offset:
                spans.append(_Span(offset, offset, self.turn_ref))
            spans[-1].append(offset+length, digest)
        super()._row(row, offset, length, digest)

    def load_turn(self, turn):
        return self._read_turn(turn, RolloutIndex(self.path, self.scope, self.thread))

    def load_metadata(self, turn):
        index = self._read_turn(turn, HistoryMetadata(self.path, self.scope, self.thread))
        index.relations = deepcopy({key: r for key, r in self.relations.items() if r['parent_turn_id'] == turn})
        return index

    def seed_ids(self, turn):
        selected = []
        seed = PhaseFlags.TERMINAL | PhaseFlags.SEED_TYPE
        for key, flags in reversed(self.phase_catalogue.get(turn, {}).items()):
            if (flags & seed) == seed:
                selected.append(key.hex())
                if len(selected) == HISTORY_SEED_LIMIT:
                    break
        return tuple(reversed(selected))

    def load_facts(self, turn, selected_ids):
        index = self._read_turn(turn, HistoryFacts(self.path, self.scope, self.thread, selected_ids))
        # Unselected rows need no display projection, but the directory still
        # supplies their authoritative contribution to the turn's metadata.
        index.turns = {turn: deepcopy(self.turns[turn])} if turn in self.turns else {}
        return index

    def load_detail(self, turn, selected_id, observed_phase=None):
        initial = DetailSelection(frozenset({selected_id}))
        index = self._read_turn(turn, HistoryDetails(self.path, self.scope, self.thread, turn, initial))
        historical = index.phases.get(selected_id)
        basis = merge_history_phase(observed_phase, historical, read_complete=self.read_complete)
        phase_ids, raw_calls, output_calls, relation_calls = {selected_id}, set(), set(), set()
        if basis:
            call = basis.get('call_id')
            if basis.get('namespace') == 'input' and isinstance(call, str) and call:
                phase_ids.add(phase_id(self.scope, self.thread, turn, 'canonical', call))
                output_calls.add(call); relation_calls.add(call)
            if basis.get('namespace') == 'canonical' and basis.get('item_type') == 'collabAgentToolCall':
                raw_calls.add(basis['item_id']); relation_calls.add(basis['item_id'])
        selection = DetailSelection(frozenset(phase_ids), frozenset(raw_calls),
                                    frozenset(output_calls), frozenset(relation_calls))
        if selection != initial:
            # Re-project from the start, preserving source insertion order and
            # outputs/raw calls that precede the target. Body reads remain lazy.
            index = self._read_turn(turn, HistoryDetails(self.path, self.scope, self.thread, turn, selection))
        index.turns = {turn: deepcopy(self.turns[turn])} if turn in self.turns else {}
        index.relations = deepcopy({key: r for key, r in self.relations.items()
            if r['parent_turn_id'] == turn and r.get('call_id') in selection.relation_calls})
        index.hint_signature = HistoryDetails.hint_key(observed_phase)
        return index

    def _read_turn(self, turn, index):
        index.fingerprint, index.header_digest, index.mode = self.fingerprint, self.header_digest, self.mode
        index.generation, index.revision = self.generation, self.revision
        index.quality = set(self.quality)
        index.agent_metadata = deepcopy(self.agent_metadata)
        with HistoryRead(self.path, self.thread, self.fingerprint, self.header_digest) as reader:
            if reader.before != self.file_stamp:
                raise HistoryChanged()
            for span in self.ranges.get(turn, []):
                if span.context:
                    reader.read_ref(span.context)
                index.turn_ref = span.context
                index.turn = span.context.turn_id if span.context else None
                reader.stream.seek(span.start)
                actual = bytes(32)
                while reader.stream.tell() < span.end:
                    start = reader.stream.tell()
                    line = reader.stream.readline(span.end-start)
                    if not line or not line.endswith(b'\n'):
                        raise HistoryChanged()
                    digest = hashlib.sha256(line).hexdigest()
                    actual = hashlib.sha256(actual+bytes.fromhex(digest)).digest()
                    try:
                        index._row(json.loads(line), start, len(line), digest)
                    except (ValueError, TypeError, KeyError, AttributeError):
                        index.quality.add('malformed_history_record')
                if actual != span.digest:
                    raise HistoryChanged()
        index.file_stamp = self.file_stamp
        index.read_complete = self.read_complete
        return index
