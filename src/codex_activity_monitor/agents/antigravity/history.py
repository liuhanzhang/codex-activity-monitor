"""Read the desktop transcripts without copying their bodies into Monitor state."""

from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

from .records import OPEN, history_step, live_step, step_id, step_index, turn_id
from .transport import uuid_string
from ..codex.history_content import ABSENT, raw_section
from ..codex.history_read import HistoryChanged
from ...request_rate import RequestRateBook, ResponseSample, TurnRates
from ...timeline import identity, phase_id


MAX_LINE = 8 * 1024 * 1024
MAX_PHASES = 8192


def antigravity_scope(scope):
    path = Path(scope)
    return path.is_absolute() and path.name == 'antigravity' and (path/'brain').is_dir()


def transcript_path(scope, thread):
    if not antigravity_scope(scope) or not uuid_string(thread):
        raise ValueError('invalid_history_identity')
    root = Path(scope).resolve()
    path = root/'brain'/thread/'.system_generated/logs/transcript.jsonl'
    if path.resolve() != path:
        raise ValueError('history_path_outside_scope')
    return path


def _stamp(stat):
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def detail_result(step, part, *, source, calls=None):
    if part == 'input':
        sections = [raw_section('input', '参数', step.arguments)] if step.arguments else []
    elif step.type in ('USER_INPUT', 'PLANNER_RESPONSE'):
        text = step.thinking if part == 'summary' else step.text
        key = 'input' if step.type == 'USER_INPUT' else 'output'
        sections = [raw_section(key, '内容', text)] if text else []
        if calls and part == 'main':
            sections.append(raw_section('input', '工具调用', calls))
    else:
        sections = [raw_section('input', '参数', step.arguments or ABSENT),
                    raw_section('output', '结果', step.output or step.text or ABSENT)]
    state = 'available' if part == 'input' or step.state not in OPEN else 'partial'
    return {'state': state if sections else 'content_unavailable',
            'sections': sections, 'attachments': [], 'source': source}


def antigravity_turn_average_speed(thread, turn, observed_requests=None, *, index=None):
    if not turn:
        return None
    observed_requests = dict(observed_requests or {})
    rates = TurnRates()
    for sample_id, sample in observed_requests.items():
        rates.responses[sample_id] = ResponseSample((0, sample['output_tokens'], 0, 0), None,
                                                    sample.get('duration_ms'), pending=False)
    if index is not None:
        rates.untracked = sum(1 for (usage_turn, step) in index.usage
                              if usage_turn == turn and str(step) not in observed_requests)
    return rates.wire(thread, turn)


def fetch_remote_detail(scope, thread, turn, selected, observed, history, read_steps):
    if not read_steps or not antigravity_scope(scope):
        return None
    phase = next((p for p in observed['phases'] if (p['id'], p['thread_id'], p['turn_id']) ==
                  (selected, thread, turn)), None)
    if not phase:
        with history.lock:
            index = history.index(scope, thread, directory_only=True)
            phase = index.detail_phase(turn, selected) if index else None
    if phase and phase['turn_id'] == turn and step_index(phase.get('step_index')):
        index = phase['step_index']
        rows = read_steps(scope, thread, index)
        if rows:
            step = live_step(index, rows[0])
            if len(rows) > 1:
                step.pair_next(live_step(index + 1, rows[1]))
            if step.type == 'PLANNER_RESPONSE' and step.text and step.thinking_ended is None:
                main_id = step.phase(scope, thread, turn, 'main')['id']
                main_phase = next((p for p in observed['phases'] if p['id'] == main_id), None)
                step.pair_text_receipt(main_phase)
            return step
    return None


class TranscriptIndex:
    source = None
    source_epoch = 0

    def __init__(self, path, scope, thread):
        self.path, self.scope, self.thread = path, scope, thread
        self._reset()

    def _reset(self):
        self.turns, self.phases = OrderedDict(), OrderedDict()
        self.refs, self.usage, self.relations, self.outputs = {}, {}, {}, {}
        self.spawn_calls, self.quality, self.seed_phase_ids = set(), set(), set()
        self.agent_metadata = {}
        self.request_rates = RequestRateBook(self.thread)
        self.revision = self.offset = 0
        self.fingerprint = self.generation = self.stamp = self.anchor = None
        self.trajectory_snapshot = (None, None)
        self.read_complete = False
        self.current_turn = None

    def refresh(self):
        with self.path.open('rb') as handle:
            before = _stamp(os.fstat(handle.fileno()))
            if before == self.stamp:
                return
            self.trajectory_snapshot = (None, None)
            handle.seek(max(0, self.offset - 256))
            anchor = handle.read(min(self.offset, 256))
            if (self.stamp is None or before[:2] != self.stamp[:2] or before[2] <= self.stamp[2] or
                    anchor != self.anchor):
                offset = self.revision + 1
                self._reset()
                self.revision = offset
                self.fingerprint = before[:2]
                self.generation = identity(self.path.name, before)
            handle.seek(self.offset)
            self.read_complete = False
            self.quality.discard('history_incomplete')
            while self.offset < before[2]:
                start = handle.tell()
                line = handle.readline(min(MAX_LINE + 1, before[2] - start))
                if not line.endswith(b'\n'):
                    self.quality.add('history_budget' if len(line) > MAX_LINE else 'history_incomplete')
                    break
                self.offset = handle.tell()
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict) or not step_index(row.get('step_index')):
                        raise ValueError('invalid_antigravity_transcript')
                    self._row(row, start, line)
                except (ValueError, TypeError, KeyError):
                    self.quality.add('malformed_history_record')
                self.revision += 1
            if _stamp(os.fstat(handle.fileno())) != before or _stamp(self.path.stat()) != before:
                raise HistoryChanged()
            self.stamp = before
            self.read_complete = self.offset == before[2] and not self.quality
            handle.seek(max(0, self.offset - 256))
            self.anchor = handle.read(min(self.offset, 256))

    def _row(self, row, start, line):
        step = history_step(row)
        if step.type == 'USER_INPUT' or (self.current_turn is None and step.type == 'SYSTEM_MESSAGE'):
            # Child trajectories can begin with a system step rather than an
            # input from the user. Its step zero anchors the independent lane.
            first = step.index if step.type == 'USER_INPUT' else 0
            self.current_turn = turn_id(self.thread, first)
            self.turns[self.current_turn] = {
                'id': identity('turn', self.scope, self.thread, self.current_turn), 'scope_id': self.scope,
                'thread_id': self.thread, 'turn_id': self.current_turn, 'start_ms': step.created,
                'start_source': 'history', 'end_ms': None, 'updated_ms': step.created or 0, 'status': 'unknown',
                'step_index': first,
            }
        key = self.current_turn
        if not key:
            return
        turn = self.turns[key]
        turn['last_step_index'] = max(turn.get('last_step_index', step.index), step.index)
        turn['updated_ms'] = max(turn['updated_ms'], step.ended or step.created or 0)
        if step.type == 'PLANNER_RESPONSE':
            if step.usage:
                self.usage[(key, step.index)] = step.usage
            if step.text and step.state == 'completed' and not row.get('tool_calls'):
                turn.update(status='completed', history_terminal=True, end_ms=step.ended,
                            final_response_step=step.index)
            elif step.state in ('failed', 'interrupted'):
                turn.update(status=step.state, history_terminal=True, end_ms=step.ended)
        digest = hashlib.sha256(line).digest()
        for part in step.parts():
            phase = step.phase(self.scope, self.thread, key, part, history=True)
            self.phases[phase['id']] = phase
            self.refs[phase['id']] = (start, len(line), digest, part)
        while len(self.phases) > MAX_PHASES or len(self.turns) > 1024 or len(self.usage) > MAX_PHASES:
            oldest, _ = self.turns.popitem(last=False)
            self.phases = OrderedDict((index, phase) for index, phase in self.phases.items() if phase['turn_id'] != oldest)
            self.refs = {index: ref for index, ref in self.refs.items() if index in self.phases}
            self.usage = {index: fields for index, fields in self.usage.items() if index[0] != oldest}
            self.quality.add('history_budget')
            if self.current_turn == oldest:
                self.current_turn = None
                return

    def detail(self, phase):
        ref = self.refs.get(phase['id'])
        if ref is None:
            return None
        offset, length, digest, part = ref
        with self.path.open('rb') as handle:
            before = _stamp(os.fstat(handle.fileno()))
            handle.seek(offset)
            line = handle.read(length)
            if before != self.stamp or _stamp(self.path.stat()) != before or hashlib.sha256(line).digest() != digest:
                raise HistoryChanged()
        row = json.loads(line)
        return detail_result(history_step(row), part, source='antigravity_history', calls=row.get('tool_calls'))

    def detail_phase(self, turn, selected):
        phase = self.phases.get(selected)
        if phase is not None:
            return phase
        cached_turn, cached = self.trajectory_snapshot
        if cached_turn == turn and cached:
            phase = next((p for p in cached['phases'] if p['id'] == selected), None)
            if phase is not None:
                return phase
        for p in self.phases.values():
            if p['turn_id'] == turn and p['track'] == 'tool':
                if phase_id(self.scope, self.thread, turn, 'input', p['item_id']+'-input') == selected:
                    return p
        for turn_key, index in self.usage:
            if turn_key == turn:
                item = step_id(self.thread, index) + '-summary'
                if phase_id(self.scope, self.thread, turn, 'model', item) == selected:
                    return {'id': selected, 'scope_id': self.scope, 'thread_id': self.thread,
                            'turn_id': turn, 'step_index': index}
        return None

    def trajectory_request(self, turn):
        cached_turn, _ = self.trajectory_snapshot
        if cached_turn == turn:
            return None
        record = self.turns.get(turn)
        return record['step_index'] if record is not None else None

    def turn_average_speed(self, turn, observed_requests=None):
        return antigravity_turn_average_speed(self.thread, turn, observed_requests, index=self)

    def trajectory_turn(self, turn, read_steps=None, *, prefetched=None, observed_phases=None):
        """Recover the selected turn's timings without retaining trajectory bodies."""
        cached_turn, cached = self.trajectory_snapshot
        if cached_turn == turn:
            return deepcopy(cached)
        record = self.turns.get(turn)
        if record is None:
            return None
        first, last = record['step_index'], record['last_step_index']
        rows = prefetched if prefetched is not None else (
            read_steps(self.scope, self.thread, first) if read_steps else [])
        if not rows:
            return None
        steps = [live_step(index, row) for index, row in enumerate(rows[:last-first+1], first)]
        phases, requests, bounds = [], {}, {}
        for pos, step in enumerate(steps):
            index = step.index
            if index == first:
                if step.type not in ('USER_INPUT', 'SYSTEM_MESSAGE'):
                    return None
                if step.created is not None:
                    bounds.update(start_ms=step.created, start_source='producer')
            elif step.type == 'USER_INPUT':
                return None  # The current trajectory has a different turn boundary.
            if pos + 1 < len(steps):
                step.pair_next(steps[pos + 1])
            if observed_phases and step.type == 'PLANNER_RESPONSE' and step.text and step.thinking_ended is None:
                main_id = step.phase(self.scope, self.thread, turn, 'main')['id']
                step.pair_text_receipt(observed_phases.get(main_id))
            sample = step.model_request()
            if sample is not None:
                requests[str(index)] = sample
            final_response = index == record.get('final_response_step')
            if final_response and step.type == 'PLANNER_RESPONSE' and step.state == 'completed':
                end = step.generation_ended or step.ended
                if end is not None:
                    bounds.update(end_ms=end, end_source='producer')
            for part in step.parts():
                # A textual preamble can share its request with tool arguments.
                if step.type == 'PLANNER_RESPONSE' and part == 'main' and not final_response:
                    continue
                phases.append(step.phase(self.scope, self.thread, turn, part, final_response=final_response))
        projection = {'phases': phases, 'model_requests': requests, 'bounds': bounds}
        if last-first < len(rows):
            self.trajectory_snapshot = (turn, projection)
        return deepcopy(projection)

    def reconciliation(self, turns):
        selected = set(turns)
        if not selected and self.turns:
            selected.add(next(reversed(self.turns)))
        return {'source': 'antigravity', 'scope_id': self.scope, 'thread_id': self.thread,
                'revision': self.revision, 'requested_turns': sorted(selected), 'read_complete': self.read_complete,
                'quality': sorted(self.quality), 'turns': [dict(t) for k, t in self.turns.items() if k in selected],
                'phases': [deepcopy(p) for p in self.phases.values() if p['turn_id'] in selected],
                'step_usage': [(turn, index, dict(value)) for (turn, index), value in self.usage.items()
                               if turn in selected]}
