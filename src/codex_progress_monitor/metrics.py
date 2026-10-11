"""Counts are for received text, with explicit model/encoding provenance."""

from collections import deque
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
import time

import tiktoken


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
    last_delta_at: float | None = None
    last_count_at: float | None = None
    encoding_name: str = 'o200k_base'
    estimated: bool = True
    quality: str = 'complete'
    count_cost_ms: float = 0
    corrections: int = 0
    samples: deque = field(default_factory=deque)
    pending: object = None

    def __post_init__(self):
        self.samples.append((self.started_at, 0))
        self.set_model(self.model)

    def set_model(self, model):
        self.model = model or 'unknown'
        try:
            self.encoding_name = tiktoken.encoding_name_for_model(self.model)
            self.estimated = False
        except KeyError:
            self.encoding_name, self.estimated = 'o200k_base', True

    def append(self, delta, at, max_bytes=8*1024*1024):
        if self.ended_at is not None:
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

    def apply_count(self, count, revision, at, cost_ms):
        if revision < self.counted_revision:
            return
        if self.counted_revision >= 0 and count < self.tokens:
            self.corrections += 1
        self.tokens, self.counted_revision = count, revision
        self.last_count_at, self.count_cost_ms = at, cost_ms
        self.samples.append((at, count))
        # Keep one point before the speed window, bounded by actual sampling frequency.
        while len(self.samples) > 2 and self.samples[1][0] < at-3:
            self.samples.popleft()
        if self.ended_at is not None and self.counted_revision == self.revision:
            self.text = ''

    def rate(self, now, window=2.0):
        if self.ended_at is not None or self.kind == 'summary' or self.quality != 'complete':
            return None
        if self.last_count_at is None:
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
        duration = max(0, (self.ended_at or now)-self.started_at)
        return {'kind': self.kind, 'source': self.source, 'chars': self.chars,
                'utf8_bytes': self.utf8_bytes, 'deltas': self.deltas,
                'tokens': self.tokens if self.last_count_at is not None and self.quality in ('complete','reconciled') else None,
                'token_rate': self.rate(now), 'encoding': self.encoding_name,
                'estimated': self.estimated, 'model': self.model,
                'tokenizer_version': tiktoken.__version__, 'quality': self.quality,
                'count_pending': self.counted_revision!=self.revision,
                'count_cost_ms': self.count_cost_ms, 'corrections': self.corrections,
                'mean_received_rate': self.tokens/max(.001,(self.last_delta_at or self.started_at)-self.started_at) if self.ended_at is not None and self.last_count_at is not None and self.quality=='complete' and self.last_delta_at is not None else None,
                'duration_seconds': duration, 'finished': self.ended_at is not None}


def count_text(text, encoding_name):
    begin = time.perf_counter()
    count = len(tiktoken.get_encoding(encoding_name).encode_ordinary(text))
    return count, (time.perf_counter()-begin)*1000


class TokenCounter:
    def __init__(self, asynchronous=True):
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='token-count') if asynchronous else None

    def tick(self, streams, now):
        # Respect a shared CPU budget as well as each stream's own cost.
        budget_interval=max(.25,sum(s.count_cost_ms for s in streams if s.ended_at is None)/1000*4)
        for stream in streams:
            if stream.pending is not None:
                future, revision, at = stream.pending
                if future.done():
                    try:
                        count, cost = future.result()
                        stream.apply_count(count, revision, at, cost)
                    except Exception:
                        stream.quality = 'tokenizer_error'
                    stream.pending = None
            if stream.counted_revision==stream.revision and stream.ended_at is None:
                if not stream.samples or now-stream.samples[-1][0]>=.25:
                    stream.samples.append((now,stream.tokens))
                    while len(stream.samples)>2 and stream.samples[1][0]<now-3:
                        stream.samples.popleft()
            if stream.quality not in ('complete','reconciled') or stream.counted_revision==stream.revision or stream.pending is not None:
                continue
            minimum_interval = max(budget_interval, stream.count_cost_ms/1000*4)
            if stream.ended_at is None and stream.last_count_at is not None and now-stream.last_count_at < minimum_interval:
                continue
            if self.pool is None:
                count, cost = count_text(stream.text, stream.encoding_name)
                stream.apply_count(count, stream.revision, now, cost)
            else:
                future = self.pool.submit(count_text, stream.text, stream.encoding_name)
                stream.pending = (future, stream.revision, now)

    def close(self):
        if self.pool:
            self.pool.shutdown(wait=True)
