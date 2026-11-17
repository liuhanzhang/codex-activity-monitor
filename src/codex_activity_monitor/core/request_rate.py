"""Usage-driven, paired request-window estimates from a read-only JSONL prefix.

Boundary rules follow the chapter 24 experiment (Tokscale f52a392c). This
implementation owns its ledger and confirmation lifecycle; it does not infer
server decoding time or use query-time wall clocks. Output includes reasoning.
"""

# Timing boundary rules adapted from Tokscale's codex_timing.rs at f52a392c.
# Copyright (c) 2025 Junho Yeo
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from dataclasses import dataclass, field
from datetime import datetime, timezone


MAX_RESPONSES_PER_TURN = 4096
_MAX_NUMBER = 2**53 - 1
_SYSTEM_PREFIXES = ('<environment_context>', '<system-reminder>', '<user_instructions>')


def source_ms(value):
    if not isinstance(value, str):
        return None
    try:
        delta = datetime.fromisoformat(value.replace('Z', '+00:00')) - datetime(1970, 1, 1, tzinfo=timezone.utc)
        result = delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000
        return result if 0 <= result <= _MAX_NUMBER else None
    except (ValueError, TypeError, OverflowError):
        return None


def _count(value):
    return value if type(value) is int and 0 <= value <= _MAX_NUMBER else None


def _totals(value):
    if not isinstance(value, dict):
        return None
    values = (value.get('input_tokens'), value.get('output_tokens'),
              value.get('cached_input_tokens', value.get('cache_read_input_tokens', 0)),
              value.get('reasoning_output_tokens', 0))
    if not all(_count(v) is not None for v in values) or values[2] > values[0] or values[3] > values[1]:
        return None
    return tuple(values)


def _latest(a, b):
    return b if a is None else a if b is None else max(a, b)


def _identity(value):
    return value if isinstance(value, str) and 0 < len(value) <= 256 else None


@dataclass(slots=True)
class RequestTiming:
    boundary_ms: int | None = None
    request_start_ms: int | None = None
    model_end_ms: int | None = None
    last_count_ms: int | None = None
    invalid_clock: bool = False

    def reset(self, at):
        self.boundary_ms = at
        self.request_start_ms = self.model_end_ms = None
        self.invalid_clock = False

    def model(self, at):
        if ((self.boundary_ms is not None and at < self.boundary_ms) or
                (self.model_end_ms is not None and at < self.model_end_ms)):
            self.invalid_clock = True
        # A flush immediately after token_count can still belong to the last request.
        if self.request_start_ms is None and self.last_count_ms is not None and 0 <= at - self.last_count_ms <= 100:
            return
        if self.request_start_ms is None:
            self.request_start_ms = self.boundary_ms
        self.model_end_ms = at

    def duration(self, at):
        start = self.request_start_ms if self.request_start_ms is not None else self.boundary_ms
        if (self.invalid_clock or at is None or start is None or at <= start or
                (self.model_end_ms is not None and at < self.model_end_ms)):
            return None
        return at - start


@dataclass(slots=True)
class ResponseSample:
    totals: tuple | None
    at: int | None
    duration_ms: int | None
    pending: bool = True
    conflict: bool = False


@dataclass(slots=True)
class TurnRates:
    responses: dict[str, ResponseSample] = field(default_factory=dict)
    quality: set[str] = field(default_factory=set)
    untracked: int = 0
    limited: bool = False

    def wire(self, thread, turn):
        valid = [r for r in self.responses.values() if r.duration_ms is not None and not r.conflict]
        output = sum(r.totals[1] for r in valid)
        duration = sum(r.duration_ms for r in valid)
        untimed = len(self.responses)-len(valid)+self.untracked
        usable = not self.limited and duration > 0
        return {'version': 1, 'thread_id': thread, 'turn_id': turn,
                'tokens_per_second': output*1000/duration if usable else None,
                'timed_output_tokens': output, 'timed_duration_ms': duration,
                'sample_count': len(valid), 'untimed_response_count': untimed,
                'pending_response_count': sum(r.pending for r in self.responses.values()),
                'coverage': 'unavailable' if not usable else 'partial' if untimed or self.quality else 'complete',
                'estimated': True, 'quality': sorted(self.quality)}


@dataclass(slots=True)
class RequestRateBook:
    thread: str
    turns: dict[str, TurnRates] = field(default_factory=dict)
    timing: RequestTiming = field(default_factory=RequestTiming)
    current_turn: str | None = None
    epoch: int = 0
    pending: list[tuple[str, str, int]] = field(default_factory=list)
    previous_total: tuple | None = None

    def projection(self, turn):
        return self.turns.get(turn, TurnRates()).wire(self.thread, turn) if turn else None

    def _reset(self, turn, at):
        self.current_turn = turn
        self.epoch += 1
        self.timing.reset(at)

    def observe(self, row):
        outer, p = row.get('type'), row.get('payload')
        if not isinstance(p, dict):
            return
        kind, at = p.get('type', ''), source_ms(row.get('timestamp'))
        if not isinstance(kind, str):
            return
        if p.get('thread_id') not in (None, self.thread):
            if self.current_turn:
                self.turns.setdefault(self.current_turn, TurnRates()).quality.add('identity_mismatch')
            return
        metadata = p.get('metadata')
        turn = _identity(p.get('turn_id')) or (_identity(metadata.get('turn_id')) if isinstance(metadata, dict) else None)
        if outer == 'turn_context' or outer == 'event_msg' and kind in ('task_started', 'turn_started'):
            attribution = p.get('turn_attribution')
            turn = turn or (_identity(attribution.get('turn_id')) if isinstance(attribution, dict) else None)
            self._reset(turn, at)
        elif outer == 'token_usage_record':
            self._usage(p, turn, at)
        elif outer == 'event_msg' and kind == 'token_count':
            self._confirm(p, turn, at)
        elif outer == 'event_msg' and kind in ('task_complete', 'turn_complete', 'turn_aborted'):
            if turn in (None, self.current_turn):
                # Terminal records and ordinary EOF do not disprove a usage preview.
                self._reset(self.current_turn, at if kind == 'turn_aborted' else None)
        elif outer == 'event_msg' and kind == 'user_message' and turn in (None, self.current_turn):
            message = p.get('message')
            if isinstance(message, str) and not message.lstrip().startswith(_SYSTEM_PREFIXES):
                self._reset(self.current_turn, at)
        elif outer == 'compacted' or outer == 'event_msg' and kind in ('context_compacted', 'thread_rolled_back'):
            if turn in (None, self.current_turn):
                self._reset(self.current_turn, None)
        elif outer == 'response_item' and turn in (None, self.current_turn):
            if at is None:
                self.timing.reset(None)
            elif kind.endswith('_output') or kind == 'message' and p.get('role') != 'assistant':
                self.timing.boundary_ms = _latest(self.timing.boundary_ms, at)
            elif kind == 'reasoning' or kind.endswith('_call') or kind == 'message' and p.get('role') == 'assistant':
                self.timing.model(at)

    def _usage(self, p, turn, at):
        if not turn:
            if self.current_turn:
                rates = self.turns.setdefault(self.current_turn, TurnRates())
                rates.untracked += 1
                rates.quality.add('turn_identity_unavailable')
            self.timing.reset(None)
            return
        rates = self.turns.setdefault(turn, TurnRates())
        response = _identity(p.get('response_id'))
        usage = _totals(p.get('usage'))
        if response in rates.responses:
            previous = rates.responses[response]
            if usage != previous.totals:
                previous.duration_ms = None
                previous.conflict = True
                rates.quality.add('response_conflict')
            return
        if rates.limited:
            return
        if len(rates.responses) >= MAX_RESPONSES_PER_TURN or len(self.pending) >= MAX_RESPONSES_PER_TURN:
            rates.limited = True
            rates.quality.add('response_limit')
            if turn == self.current_turn:
                self.timing.reset(None)
            return
        if not response:
            rates.untracked += 1
            rates.quality.add('response_identity_unavailable')
            if turn == self.current_turn:
                self.timing.reset(None)
            return
        if turn == self.current_turn and self.pending and self.pending[-1][2] == self.epoch:
            # A second usage without settlement cannot borrow the old fixed start.
            self._reset(self.current_turn, None)
            rates.quality.add('missing_confirmation')
        duration = self.timing.duration(at) if turn == self.current_turn and usage is not None else None
        if duration is None:
            rates.quality.add('request_timing_unavailable')
        sample = ResponseSample(usage, at, duration)
        rates.responses[response] = sample
        if turn == self.current_turn:
            self.pending.append((turn, response, self.epoch))
        else:
            sample.pending = False
            rates.quality.add('turn_identity_mismatch')

    def _confirm(self, p, turn, at):
        info = p.get('info')
        if not isinstance(info, dict):
            return
        total, last = _totals(info.get('total_token_usage')), _totals(info.get('last_token_usage'))
        if total is not None and total == self.previous_total:
            return  # Duplicate count does not move the next request boundary.
        if self.pending:
            eligible = [i for i, entry in enumerate(self.pending) if turn in (None, entry[0])]
            if not eligible:
                return
            # A context reset can leave an older usage without a count. Match
            # buckets before consuming it, so it cannot block all later work.
            # When buckets coincide, the oldest match wins: a late old count
            # must not move the new request's boundary merely by coincidence.
            matching = [i for i in eligible if self.turns[self.pending[i][0]].responses[self.pending[i][1]].totals == last]
            current = [i for i in eligible if self.pending[i][2] == self.epoch]
            position = (matching or current or eligible)[0]
            owner, response, epoch = self.pending.pop(position)
            sample = self.turns[owner].responses[response]
            valid = (last is not None and last == sample.totals and at is not None and
                     sample.at is not None and at >= sample.at and
                     (epoch != self.epoch or not self.timing.invalid_clock and
                      (self.timing.model_end_ms is None or self.timing.model_end_ms <= sample.at)))
            sample.pending = False
            if not valid:
                sample.duration_ms = None
                self.turns[owner].quality.add('confirmation_mismatch')
            if epoch != self.epoch:
                self.previous_total = total
                return  # A late count cannot move a new turn's boundary.
        else:
            if turn not in (None, self.current_turn):
                return
            if self.current_turn and last is not None:
                rates = self.turns.setdefault(self.current_turn, TurnRates())
                rates.untracked += 1
                rates.quality.add('usage_record_unavailable')
        self.previous_total = total
        # Each accepted settlement closes its request generation. Older pending
        # previews may remain, but cannot lend their start to the next request.
        self._reset(self.current_turn, _latest(self.timing.boundary_ms, at) if at is not None else None)
        self.timing.last_count_ms = _latest(self.timing.last_count_ms, at)
