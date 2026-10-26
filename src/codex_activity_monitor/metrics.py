"""Counts are for received text, with explicit model/encoding provenance."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from concurrent.futures import Future, ThreadPoolExecutor
import time
import weakref
from typing import Literal

import tiktoken

from .tokenizer_process import TokenizerProcess

RATE_WINDOW_SECONDS = 1.0
MAX_RECEIPT_POINTS = 4096


@dataclass(frozen=True, slots=True)
class RateReading:
    value: float | None
    status: Literal['ready', 'sampling', 'unavailable', 'inactive']
    reason: str | None = None


@dataclass
class TextStream:
    key: tuple
    kind: str
    source: str
    model: str
    started_at: float
    text: str = ''
    chars: int = 0
    utf8_bytes: int = 0
    deltas: int = 0
    revision: int = 0
    counted_revision: int = -1
    tokens: int = 0
    ended_at: float | None = None
    ended_by_history: bool = False
    last_delta_at: float | None = None
    last_count_at: float | None = None
    encoding_name: str = 'o200k_base'
    estimated: bool = True
    quality: str = 'complete'
    count_cost_ms: float = 0
    corrections: int = 0
    receipts: deque = field(default_factory=deque)  # (source receive time, cumulative characters)
    window_at: float | None = None
    window_tokens: int | None = None
    window_baseline_chars: int | None = None
    pending: object = None
    count_state: str = 'pending'
    observation_epoch: int = 0
    recovery_started_at: float | None = None
    received_complete: bool = True
    text_complete: bool = True

    def __post_init__(self):
        self.set_model(self.model)

    def set_model(self, model):
        previous = self.encoding_name
        self.model = model or 'unknown'
        try:
            self.encoding_name = tiktoken.encoding_name_for_model(self.model)
            self.estimated = False
        except KeyError:
            self.encoding_name, self.estimated = 'o200k_base', True
        if previous != self.encoding_name and self.text and self.ended_at is None and not self.ended_by_history:
            self.pending = None
            self.revision += 1
            self.counted_revision = -1
            self.tokens = 0
            self.last_count_at = None
            self.window_at = self.window_tokens = self.window_baseline_chars = None

    def append(self, delta, at, max_bytes=8*1024*1024):
        if self.ended_at is not None or self.ended_by_history:
            return
        self.chars += len(delta)
        self.utf8_bytes += len(delta.encode('utf-8'))
        self.deltas += 1
        self.last_delta_at = at
        if self.utf8_bytes > max_bytes:
            self.quality = 'content_limit'
            self.text = ''
            return
        if self.quality == 'complete':
            self.text += delta
            self.revision += 1
            if delta:
                if self.receipts and at < self.receipts[-1][0]:
                    self.quality = 'out_of_order'
                    return
                self.prune_receipts(at)
                if self.receipts and at == self.receipts[-1][0]:
                    self.receipts[-1] = (at, len(self.text))
                elif len(self.receipts) >= MAX_RECEIPT_POINTS:
                    self.discard_timing()
                else:
                    self.receipts.append((at, len(self.text)))

    def interrupt_observation(self, reason='observation_gap'):
        """Invalidate this live baseline; detached jobs remain budgeted by the counter."""
        self.observation_epoch += 1
        self.quality = reason
        self.received_complete = self.text_complete = False
        self.recovery_started_at = None
        self.pending = None
        self.revision += 1
        self.counted_revision = -1
        self.tokens = 0
        self.last_delta_at = self.last_count_at = None
        self.window_at = self.window_tokens = self.window_baseline_chars = None
        self.count_state = 'pending'
        self.text = ''
        self.receipts.clear()

    def resume_observation(self, at):
        """An attributed, fresh delta starts a new continuous receive interval."""
        if self.quality in ('observation_gap', 'disconnected'):
            self.quality = 'complete'
            self.recovery_started_at = at

    def prune_receipts(self, now):
        cutoff = now-RATE_WINDOW_SECONDS
        while len(self.receipts) > 1 and self.receipts[1][0] <= cutoff:
            self.receipts.popleft()

    def baseline_chars(self, now):
        self.prune_receipts(now)
        return self.receipts[0][1] if self.receipts and self.receipts[0][0] <= now-RATE_WINDOW_SECONDS else 0

    def discard_timing(self):
        self.quality = 'timing_limit'
        self.text = ''
        self.receipts.clear()

    def close_from_history(self):
        self.ended_by_history = True
        self.receipts.clear()
        if self.counted_revision == self.revision:
            self.text = ''

    def fail_count(self):
        self.quality, self.count_state = 'tokenizer_error', 'failed'
        self.text = ''  # Failed prefixes cannot participate in a later exact count.
        self.receipts.clear()

    def apply_count(self, count, revision, at, cost_ms, *, baseline_tokens=None, baseline_chars=None):
        if revision < self.counted_revision:
            return
        if self.counted_revision >= 0 and count < self.tokens:
            self.corrections += 1
        self.tokens, self.counted_revision = count, revision
        self.last_count_at, self.count_cost_ms = at, cost_ms
        self.count_state = 'ready'
        if baseline_chars is None:
            baseline_chars = self.baseline_chars(at)
        if baseline_tokens is None:
            # A zero-length or complete baseline needs no second tokenization.
            baseline_tokens = 0 if baseline_chars == 0 else count if baseline_chars == len(self.text) else None
        self.window_at = at
        self.window_baseline_chars = baseline_chars
        self.window_tokens = count-baseline_tokens if baseline_tokens is not None else None
        if (self.ended_at is not None or self.ended_by_history) and self.counted_revision == self.revision:
            self.text = ''
            self.receipts.clear()

    def rate(self, now):
        return self.rate_reading(now).value

    def rate_reading(self, now):
        if self.ended_at is not None or self.ended_by_history:
            return RateReading(None, 'inactive')
        if self.quality != 'complete':
            return RateReading(None, 'unavailable', self.quality)
        if self.count_state in ('budget', 'failed'):
            return RateReading(None, 'unavailable', 'count_budget' if self.count_state == 'budget' else 'tokenizer_error')
        if self.recovery_started_at is not None and (
                self.window_at is None or self.window_at-self.recovery_started_at < RATE_WINDOW_SECONDS):
            return RateReading(None, 'unavailable', 'observation_gap')
        if self.last_delta_at is not None and now-self.last_delta_at >= RATE_WINDOW_SECONDS:
            return RateReading(0.0, 'ready')
        if self.window_at is None:
            return RateReading(None, 'sampling', 'initial_count')
        if now-self.window_at >= RATE_WINDOW_SECONDS:
            return RateReading(None, 'unavailable', 'count_pending')
        if self.window_tokens is None:
            return RateReading(None, 'sampling', 'initial_count')
        if self.window_tokens < 0:
            return RateReading(None, 'sampling', 'count_correction')
        return RateReading(float(self.window_tokens)/RATE_WINDOW_SECONDS, 'ready')

    def view(self, now):
        duration = None if self.ended_by_history and self.ended_at is None else max(0, (self.ended_at or now)-self.started_at)
        final_count = self.count_state == 'ready' and self.counted_revision == self.revision
        reading = self.rate_reading(now)
        window_at = (now if reading.status == 'ready' and self.last_delta_at is not None and
                     now-self.last_delta_at >= RATE_WINDOW_SECONDS else self.window_at)
        return {'kind': self.kind, 'source': self.source, 'chars': self.chars,
                'utf8_bytes': self.utf8_bytes, 'deltas': self.deltas,
                'tokens': self.tokens if self.last_count_at is not None and self.text_complete and
                    (self.received_complete or final_count) and self.quality in ('complete','reconciled') else None,
                'token_rate': reading.value, 'rate_status': reading.status, 'rate_reason': reading.reason,
                'rate_window_seconds': RATE_WINDOW_SECONDS, 'rate_window_at': window_at,
                'encoding': self.encoding_name,
                'estimated': self.estimated, 'model': self.model,
                'tokenizer_version': tiktoken.__version__, 'quality': self.quality,
                'received_complete': self.received_complete, 'observation_epoch': self.observation_epoch,
                'count_pending': self.counted_revision!=self.revision,
                'count_state': self.count_state,
                'count_cost_ms': self.count_cost_ms, 'corrections': self.corrections,
                'mean_received_rate': self.tokens/max(.001,(self.last_delta_at or self.started_at)-self.started_at) if self.received_complete and final_count and self.ended_at is not None and self.last_count_at is not None and self.quality=='complete' and self.last_delta_at is not None else None,
                'duration_seconds': duration, 'finished': self.ended_at is not None or self.ended_by_history}


class TokenizationError(Exception):
    """A tokenizer failure, including a native panic, without source text."""


def count_text(text, encoding_name):
    begin = time.perf_counter()
    try:
        count = len(tiktoken.get_encoding(encoding_name).encode_ordinary(text))
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise
    except BaseException as error:
        # PyO3 PanicException derives directly from BaseException. Normalize
        # native library failures at this boundary, preserving process signals.
        raise TokenizationError(type(error).__name__) from error
    return count, (time.perf_counter()-begin)*1000


def count_window(text, encoding, baseline_chars, tokenizer=None):
    """Count both cumulative prefixes on one capture clock, off the event loop."""
    tokenizer = tokenizer or count_text
    if not 0 <= baseline_chars <= len(text):
        raise TokenizationError('invalid_window_boundary')
    total, cost = tokenizer(text, encoding)
    if baseline_chars == 0:
        baseline = 0
    elif baseline_chars == len(text):
        baseline = total
    else:
        baseline, baseline_cost = tokenizer(text[:baseline_chars], encoding)
        cost += baseline_cost
    return total, cost, baseline


@dataclass(frozen=True, slots=True)
class CountWork:
    owner: weakref.ReferenceType[TextStream]
    revision: int
    received_at: float  # Collector prefix-capture clock, before executor delay.
    encoding: str
    utf8_bytes: int
    baseline_chars: int
    observation_epoch: int = 0


class TokenCounter:
    def __init__(self, asynchronous=True, max_jobs=2, max_pending_bytes=2*1024*1024, *,
                 max_input_bytes=1024*1024, isolated=True, count_deadline=3,
                 max_receipt_points=65536):
        if max_jobs < 1 or max_pending_bytes < 0 or max_input_bytes < 1 or max_receipt_points < 1:
            raise ValueError('invalid_count_budget')
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='token-count') if asynchronous else None
        self.worker = TokenizerProcess(count_deadline) if asynchronous and isolated else None
        self.max_jobs, self.max_pending_bytes = max_jobs, max_pending_bytes
        self.max_input_bytes = max_input_bytes
        self.jobs: dict[Future[tuple[int, float, int]], CountWork] = {}
        self.pending_bytes = 0
        self.peak_jobs = self.peak_bytes = self.budget_deferrals = 0
        self.cursor = 0
        self.max_receipt_points = max_receipt_points

    def diagnostics(self):
        return {'jobs': len(self.jobs), 'pending_bytes': self.pending_bytes,
                'max_jobs': self.max_jobs, 'max_pending_bytes': self.max_pending_bytes,
                'max_input_bytes': self.max_input_bytes,
                'peak_jobs': self.peak_jobs, 'peak_pending_bytes': self.peak_bytes,
                'budget_deferrals': self.budget_deferrals,
                'max_receipt_points': self.max_receipt_points,
                'worker': self.worker.diagnostics() if self.worker else None}

    def _complete(self):
        # Track work independently of streams: model changes and eviction may
        # clear stream.pending while the executor still owns the old text.
        for future, work in list(self.jobs.items()):
            if not future.done():
                continue
            del self.jobs[future]
            self.pending_bytes -= work.utf8_bytes
            stream = work.owner()
            if stream is None or stream.pending is None or stream.pending[0] is not future:
                continue
            stream.pending = None
            if (stream.observation_epoch != work.observation_epoch or stream.encoding_name != work.encoding or
                    stream.quality not in ('complete', 'reconciled')):
                continue
            try:
                count, cost, baseline = future.result()
                stream.apply_count(count, work.revision, work.received_at, cost,
                                   baseline_tokens=baseline, baseline_chars=work.baseline_chars)
            except Exception:
                stream.fail_count()

    def tick(self, streams, now):
        self._complete()
        for stream in streams:
            if stream.ended_at is not None or stream.ended_by_history:
                stream.receipts.clear()
            else:
                stream.prune_receipts(now)
        retained = sum(len(stream.receipts) for stream in streams)
        if retained > self.max_receipt_points:
            for stream in sorted(streams, key=lambda stream: len(stream.receipts), reverse=True):
                retained -= len(stream.receipts)
                stream.discard_timing()
                if retained <= self.max_receipt_points:
                    break
        # Respect a shared CPU budget as well as each stream's own cost.
        budget_interval=max(.25,sum(s.count_cost_ms for s in streams if s.ended_at is None and not s.ended_by_history)/1000*4)
        start = self.cursor % len(streams) if streams else 0
        for position in range(len(streams)):
            index = (start+position) % len(streams)
            stream = streams[index]
            baseline_chars = stream.baseline_chars(now)
            current = stream.counted_revision == stream.revision
            if current and stream.window_baseline_chars == baseline_chars:
                stream.window_at = now
                continue
            if stream.quality not in ('complete','reconciled') or stream.pending is not None:
                continue
            if current and (stream.ended_at is not None or stream.ended_by_history):
                continue
            size = len(stream.text.encode('utf-8'))
            if size > self.max_input_bytes or (self.pool is not None and size > self.max_pending_bytes):
                stream.count_state = 'budget'
                self.budget_deferrals += 1
                continue
            minimum_interval = max(budget_interval, stream.count_cost_ms/1000*4)
            if stream.ended_at is None and not stream.ended_by_history and stream.last_count_at is not None and now-stream.last_count_at < minimum_interval:
                continue
            if self.pool is None:
                try:
                    count, cost, baseline = count_window(stream.text, stream.encoding_name, baseline_chars)
                    stream.apply_count(count, stream.revision, now, cost,
                                       baseline_tokens=baseline, baseline_chars=baseline_chars)
                except TokenizationError:
                    stream.fail_count()
            else:
                if len(self.jobs) >= self.max_jobs or self.pending_bytes+size > self.max_pending_bytes:
                    stream.count_state = 'pending'
                    self.budget_deferrals += 1
                    continue
                tokenizer = self.worker.count if self.worker else None
                future = self.pool.submit(count_window, stream.text, stream.encoding_name, baseline_chars, tokenizer)
                stream.pending = (future, stream.revision, now)
                stream.count_state = 'pending'
                self.jobs[future] = CountWork(weakref.ref(stream), stream.revision, now, stream.encoding_name,
                                              size, baseline_chars, stream.observation_epoch)
                self.pending_bytes += size
                self.peak_jobs = max(self.peak_jobs, len(self.jobs))
                self.peak_bytes = max(self.peak_bytes, self.pending_bytes)
                self.cursor = index+1

    def close(self):
        if self.worker:
            self.worker.close()
        if self.pool:
            self.pool.shutdown(wait=True, cancel_futures=True)
            self._complete()
