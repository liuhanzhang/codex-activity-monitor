"""Bounded history I/O; only detached metadata crosses into the event loop."""

from collections import OrderedDict
from dataclasses import dataclass
import json
from pathlib import Path
import threading
import time

from .history import HistoryProvider
from .history_contract import HistorySelection
from .json_encoding import encode_limited


@dataclass(frozen=True, slots=True)
class HistoryPacket:
    """Immutable, byte-accounted result, encoded outside every domain lock."""
    payload: bytes

    @classmethod
    def capture(cls, value, limit):
        encoded = encode_limited(value, limit)
        return cls(encoded) if encoded is not None else None

    def decode(self):
        return json.loads(self.payload)


class HistoryRefresh:
    def __init__(self, provider=None, capacity=128, *, max_pending_bytes=1024*1024,
                 max_request_bytes=64*1024, max_ready_bytes=32*1024*1024,
                 max_result_bytes=8*1024*1024, max_drain_bytes=8*1024*1024):
        if (min(capacity, max_pending_bytes, max_request_bytes, max_ready_bytes,
                max_result_bytes, max_drain_bytes) <= 0 or
                max_result_bytes > min(max_ready_bytes, max_drain_bytes)):
            raise ValueError('invalid_history_refresh_budget')
        self.provider = provider or HistoryProvider(capacity=64)
        self.capacity = capacity
        self.max_pending_bytes, self.max_request_bytes = max_pending_bytes, max_request_bytes
        self.max_ready_bytes, self.max_result_bytes = max_ready_bytes, max_result_bytes
        self.max_drain_bytes = max_drain_bytes
        self.pending = OrderedDict()
        self.ready = OrderedDict()
        self.pending_bytes = self.ready_bytes = 0
        self.peak_pending_bytes = self.peak_ready_bytes = 0
        self.deferred_requests = self.oversized_results = self.discarded_results = self.processed = 0
        self.last_read = {}
        self.cursor = 0
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.stopping = False
        self.worker = threading.Thread(target=self._run, daemon=True, name='history-refresh')
        self.worker.start()

    def diagnostics(self):
        with self.lock:
            return {'pending': len(self.pending), 'pending_bytes': self.pending_bytes,
                    'ready': len(self.ready), 'ready_bytes': self.ready_bytes,
                    'peak_pending_bytes': self.peak_pending_bytes, 'peak_ready_bytes': self.peak_ready_bytes,
                    'max_pending_bytes': self.max_pending_bytes, 'max_request_bytes': self.max_request_bytes,
                    'max_ready_bytes': self.max_ready_bytes, 'max_result_bytes': self.max_result_bytes,
                    'max_drain_bytes': self.max_drain_bytes, 'deferred_requests': self.deferred_requests,
                    'oversized_results': self.oversized_results, 'discarded_results': self.discarded_results,
                    'processed': self.processed}

    def _unavailable(self, key, quality='history_budget', error=None):
        result = {'scope_id': key[0], 'thread_id': key[1], 'quality': ['history_unavailable', quality]}
        if error:
            result['error'] = error
        # No revision/read_complete: resource failure is never evidence that a
        # persisted turn or item disappeared from its source.
        return HistoryPacket.capture(result, self.max_result_bytes)

    def _publish(self, key, packet):
        """Caller holds queue lock. Dropped results are retried by the scheduler."""
        old = self.ready.pop(key, None)
        if old:
            self.ready_bytes -= len(old.payload)
        if packet is None:
            self.discarded_results += 1
            return
        while self.ready and (len(self.ready) >= self.capacity or
                              self.ready_bytes+len(packet.payload) > self.max_ready_bytes):
            _, evicted = self.ready.popitem(last=False)
            self.ready_bytes -= len(evicted.payload)
            self.discarded_results += 1
        self.ready[key] = packet
        self.ready_bytes += len(packet.payload)
        self.peak_ready_bytes = max(self.peak_ready_bytes, self.ready_bytes)

    def schedule(self, requests, now=None):
        now = time.monotonic() if now is None else now
        requests = [r for r in requests if isinstance(r.scope, str) and Path(r.scope).is_absolute()]
        with self.lock:
            if self.stopping:
                return
            keys = {r.key for r in requests}
            self.last_read = {k: v for k, v in self.last_read.items() if k in keys}
            self.pending = OrderedDict((k, v) for k, v in self.pending.items() if k in keys)
            self.pending_bytes = sum(v.utf8_bytes for v in self.pending.values())
            start = self.cursor % len(requests) if requests else 0
            for offset in range(len(requests)):
                request = requests[(start+offset) % len(requests)]
                key = request.key
                # Unresolved work is polled each second; settled work slows
                # after 30 seconds. Admission rotates across both byte/count caps.
                last, first = self.last_read.get(key, (0, now))
                if now-last < (1 if request.urgent or now-first < 30 else 10):
                    continue
                old = self.pending.get(key)
                if old:
                    request = request.merge(old)
                size = request.utf8_bytes
                if size > min(self.max_request_bytes, self.max_pending_bytes):
                    self.deferred_requests += 1
                    if old:
                        self.pending_bytes -= self.pending.pop(key).utf8_bytes
                    self.last_read[key] = (now, first)
                    self._publish(key, self._unavailable(key))
                    continue
                total = self.pending_bytes-(old.utf8_bytes if old else 0)+size
                if (old is None and len(self.pending) >= self.capacity) or total > self.max_pending_bytes:
                    self.deferred_requests += 1
                    self.cursor = start+offset
                    break
                self.pending[key] = request
                self.pending_bytes = total
                self.peak_pending_bytes = max(self.peak_pending_bytes, total)
            else:
                self.cursor = start+1
        self.wake.set()

    def drain(self):
        packets, size = [], 0
        with self.lock:
            while self.ready:
                key, packet = next(iter(self.ready.items()))
                if size+len(packet.payload) > self.max_drain_bytes:
                    break
                self.ready.pop(key)
                self.ready_bytes -= len(packet.payload)
                size += len(packet.payload)
                packets.append(packet)
        return [packet.decode() for packet in packets]

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
                    key, request = self.pending.popitem(last=False)
                    self.pending_bytes -= request.utf8_bytes
                    now = time.monotonic()
                    self.last_read[key] = (now, self.last_read.get(key, (0, now))[1])
                try:
                    result = self.provider.reconciliation(*key, request.turns, items=request.items)
                    packet = HistoryPacket.capture(result, self.max_result_bytes)
                    oversized = packet is None
                    if oversized:
                        packet = self._unavailable(key)
                except (OSError, ValueError, TypeError, KeyError) as error:
                    packet = self._unavailable(key, 'history_read_error', type(error).__name__)
                    oversized = False
                with self.lock:
                    if self.stopping:
                        return
                    self.processed += 1
                    self.oversized_results += int(oversized)
                    self._publish(key, packet)

    def close(self):
        with self.lock:
            self.stopping = True
            self.pending.clear(); self.ready.clear()
            self.pending_bytes = self.ready_bytes = 0
        self.wake.set()
        self.worker.join(timeout=1)
