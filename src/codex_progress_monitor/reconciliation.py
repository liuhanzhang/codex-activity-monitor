"""Bounded background JSONL reads. This worker never owns the live Engine lock."""

from collections import OrderedDict
from pathlib import Path
import threading
import time

from .history import HistoryProvider


class HistoryRefresh:
    def __init__(self, provider=None, capacity=128):
        self.provider = provider or HistoryProvider(capacity=64)
        self.capacity = capacity
        self.pending = OrderedDict()
        self.ready = OrderedDict()
        self.last_read = {}
        self.cursor = 0
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.stopping = False
        self.worker = threading.Thread(target=self._run, daemon=True, name='history-refresh')
        self.worker.start()

    def schedule(self, requests, now=None):
        now = time.monotonic() if now is None else now
        requests = [r for r in requests if isinstance(r[0], str) and Path(r[0]).is_absolute()]
        with self.lock:
            keys = {(r[0], r[1]) for r in requests}
            self.last_read = {k: v for k, v in self.last_read.items() if k in keys}
            self.pending = OrderedDict((k, v) for k, v in self.pending.items() if k in keys)
            start = self.cursor % len(requests) if requests else 0
            ordered = requests[start:] + requests[:start]
            for offset, (scope, thread, turns, urgent) in enumerate(ordered):
                key = (scope, thread)
                # Unresolved work is polled each second. Completed work gets
                # bounded fast retries, then a slower refresh until it expires.
                last, first = self.last_read.get(key, (0, now))
                interval = 1 if urgent or now - first < 30 else 10
                if now - last < interval:
                    continue
                if key in self.pending:
                    self.pending[key].update(turns)
                elif len(self.pending) < self.capacity:
                    self.pending[key] = set(turns)
                else:
                    self.cursor = start + offset
                    break
            else:
                self.cursor = start + 1
        self.wake.set()

    def drain(self):
        with self.lock:
            result = list(self.ready.values())
            self.ready.clear()
            return result

    def _run(self):
        while True:
            self.wake.wait(.5)
            self.wake.clear()
            while True:
                with self.lock:
                    if self.stopping:
                        return
                    if not self.pending:
                        break
                    key, turns = self.pending.popitem(last=False)
                    now = time.monotonic()
                    self.last_read[key] = (now, self.last_read.get(key, (0, now))[1])
                try:
                    result = self.provider.reconciliation(*key, turns)
                except (OSError, ValueError, TypeError, KeyError) as error:
                    result = {'scope_id': key[0], 'thread_id': key[1],
                              'quality': ['history_unavailable'], 'error': type(error).__name__}
                with self.lock:
                    self.ready[key] = result
                    self.ready.move_to_end(key)
                    while len(self.ready) > self.capacity:
                        self.ready.popitem(last=False)

    def close(self):
        with self.lock:
            self.stopping = True
        self.wake.set()
        self.worker.join(timeout=1)
