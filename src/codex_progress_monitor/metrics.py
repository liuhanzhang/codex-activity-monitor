"""Counts are for received text, with explicit model/encoding provenance."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from concurrent.futures import Future, ThreadPoolExecutor
import time
import weakref

import tiktoken

from .tokenizer_process import TokenizerProcess


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
    samples: deque = field(default_factory=deque)
    pending: object = None
    reset_rate_baseline: bool = False
    count_state: str = 'pending'

    def __post_init__(self):
        self.samples.append((self.started_at, 0))
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
            self.reset_rate_baseline = True
            self.samples.clear()
            self.samples.append((self.last_delta_at or self.started_at, 0))

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

    def close_from_history(self):
        self.ended_by_history = True
        if self.counted_revision == self.revision:
            self.text = ''

    def fail_count(self):
        self.quality, self.count_state = 'tokenizer_error', 'failed'
        self.text = ''  # Failed prefixes cannot participate in a later exact count.

    def apply_count(self, count, revision, at, cost_ms):
        if revision < self.counted_revision:
            return
        if self.counted_revision >= 0 and count < self.tokens:
            self.corrections += 1
        self.tokens, self.counted_revision = count, revision
        self.last_count_at, self.count_cost_ms = at, cost_ms
        self.count_state = 'ready'
        if self.reset_rate_baseline:
            self.samples.clear()
            self.reset_rate_baseline = False
        self.samples.append((at, count))
        # Keep one point before the speed window, bounded by actual sampling frequency.
        while len(self.samples) > 2 and self.samples[1][0] < at-3:
            self.samples.popleft()
        if (self.ended_at is not None or self.ended_by_history) and self.counted_revision == self.revision:
            self.text = ''

    def rate(self, now, window=2.0):
        if self.ended_at is not None or self.ended_by_history or self.quality != 'complete':
            return None
        if self.last_count_at is None:
            return None
        if self.count_state == 'budget' or (self.counted_revision != self.revision and now-self.last_count_at >= window):
            return None
        if self.last_delta_at is not None and now-self.last_delta_at >= window:
            return 0.0
        target = now-window
        before = self.samples[0]
        for point in self.samples:
            if point[0] <= target:
                before = point
            else:
                break
        span = max(0, now-before[0])
        change = self.tokens-before[1]
        if change < 0 or span <= 0:
            return None
        return change/span

    def view(self, now):
        duration = None if self.ended_by_history and self.ended_at is None else max(0, (self.ended_at or now)-self.started_at)
        final_count = self.count_state == 'ready' and self.counted_revision == self.revision
        return {'kind': self.kind, 'source': self.source, 'chars': self.chars,
                'utf8_bytes': self.utf8_bytes, 'deltas': self.deltas,
                'tokens': self.tokens if self.last_count_at is not None and self.quality in ('complete','reconciled') else None,
                'token_rate': self.rate(now), 'encoding': self.encoding_name,
                'estimated': self.estimated, 'model': self.model,
                'tokenizer_version': tiktoken.__version__, 'quality': self.quality,
                'count_pending': self.counted_revision!=self.revision,
                'count_state': self.count_state,
                'count_cost_ms': self.count_cost_ms, 'corrections': self.corrections,
                'mean_received_rate': self.tokens/max(.001,(self.last_delta_at or self.started_at)-self.started_at) if final_count and self.ended_at is not None and self.last_count_at is not None and self.quality=='complete' and self.last_delta_at is not None else None,
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


@dataclass(frozen=True, slots=True)
class CountWork:
    owner: weakref.ReferenceType[TextStream]
    revision: int
    received_at: float  # Observation monotonic seconds, before executor delay.
    encoding: str
    utf8_bytes: int


class TokenCounter:
    def __init__(self, asynchronous=True, max_jobs=2, max_pending_bytes=2*1024*1024, *,
                 max_input_bytes=1024*1024, isolated=True, count_deadline=3):
        if max_jobs < 1 or max_pending_bytes < 0 or max_input_bytes < 1:
            raise ValueError('invalid_count_budget')
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='token-count') if asynchronous else None
        self.worker = TokenizerProcess(count_deadline) if asynchronous and isolated else None
        self.max_jobs, self.max_pending_bytes = max_jobs, max_pending_bytes
        self.max_input_bytes = max_input_bytes
        self.jobs: dict[Future[tuple[int, float]], CountWork] = {}
        self.pending_bytes = 0
        self.peak_jobs = self.peak_bytes = self.budget_deferrals = 0
        self.cursor = 0

    def diagnostics(self):
        return {'jobs': len(self.jobs), 'pending_bytes': self.pending_bytes,
                'max_jobs': self.max_jobs, 'max_pending_bytes': self.max_pending_bytes,
                'max_input_bytes': self.max_input_bytes,
                'peak_jobs': self.peak_jobs, 'peak_pending_bytes': self.peak_bytes,
                'budget_deferrals': self.budget_deferrals,
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
            if stream.encoding_name != work.encoding or stream.quality not in ('complete', 'reconciled'):
                continue
            try:
                count, cost = future.result()
                stream.apply_count(count, work.revision, work.received_at, cost)
            except Exception:
                stream.fail_count()

    def tick(self, streams, now):
        self._complete()
        # Respect a shared CPU budget as well as each stream's own cost.
        budget_interval=max(.25,sum(s.count_cost_ms for s in streams if s.ended_at is None and not s.ended_by_history)/1000*4)
        start = self.cursor % len(streams) if streams else 0
        for position in range(len(streams)):
            index = (start+position) % len(streams)
            stream = streams[index]
            if stream.counted_revision==stream.revision and stream.ended_at is None and not stream.ended_by_history:
                if not stream.samples or now-stream.samples[-1][0]>=.25:
                    stream.samples.append((now,stream.tokens))
                    while len(stream.samples)>2 and stream.samples[1][0]<now-3:
                        stream.samples.popleft()
            if stream.quality not in ('complete','reconciled') or stream.counted_revision==stream.revision or stream.pending is not None:
                continue
            size = len(stream.text.encode('utf-8'))
            if size > self.max_input_bytes:
                stream.count_state = 'budget'
                self.budget_deferrals += 1
                continue
            minimum_interval = max(budget_interval, stream.count_cost_ms/1000*4)
            if stream.ended_at is None and not stream.ended_by_history and stream.last_count_at is not None and now-stream.last_count_at < minimum_interval:
                continue
            if self.pool is None:
                try:
                    count, cost = count_text(stream.text, stream.encoding_name)
                    stream.apply_count(count, stream.revision, now, cost)
                except TokenizationError:
                    stream.fail_count()
            else:
                if len(self.jobs) >= self.max_jobs:
                    stream.count_state = 'budget'
                    self.budget_deferrals += 1
                    continue
                if self.pending_bytes+size > self.max_pending_bytes:
                    stream.count_state = 'budget'
                    self.budget_deferrals += 1
                    continue
                count = self.worker.count if self.worker else count_text
                future = self.pool.submit(count, stream.text, stream.encoding_name)
                stream.pending = (future, stream.revision, now)
                stream.count_state = 'pending'
                self.jobs[future] = CountWork(weakref.ref(stream), stream.revision, now, stream.encoding_name, size)
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
