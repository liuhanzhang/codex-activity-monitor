"""Detached state values and a bounded, disposable recovery writer."""

from copy import deepcopy
from dataclasses import dataclass, field
import json
import threading
import time

from .runtime import SnapshotPublisher


@dataclass(frozen=True)
class DetachedSnapshot:
    """Own a private copy, exposing only encoded or independently copied data.

    Workers never receive aliases into Engine. No public accessor exposes the
    retained dictionary, and encoding cannot mutate it.
    """

    _value: dict = field(repr=False)

    @classmethod
    def capture(cls, value):
        return cls(deepcopy(value))

    def encode(self, instance, sequence):
        public = {k: v for k, v in self._value.items() if k != 'usage_recovery'}
        return encode_frame({'protocol_version': 1, 'kind': 'state',
                             'instance_id': instance, 'sequence': sequence,
                             'payload': public})

    def recovery_value(self):
        return deepcopy(self._value)


def encode_frame(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode() + b'\n'


@dataclass(frozen=True)
class RecoveryCheckpoint:
    snapshot: DetachedSnapshot
    instance_id: str | None = None
    state_sequence: int | None = None

    def recovery_value(self):
        value = self.snapshot.recovery_value()
        if self.instance_id is not None:
            value['checkpoint'] = {'version': 1, 'instance_id': self.instance_id,
                                   'state_sequence': self.state_sequence, 'encoded_at': time.time()}
        return value


class RecoveryWriter:
    """One write in flight and one replaceable pending checkpoint.

    A daemon worker and bounded close keep disk waits out of the collector.
    on_exit can retain the lifetime lock until this writer actually finishes.
    """

    def __init__(self, runtime, *, publisher=None, on_exit=None, retry_min=.25, retry_max=4):
        self.publisher = publisher or SnapshotPublisher(runtime)
        self.on_exit = on_exit
        self.retry_min, self.retry_max = retry_min, retry_max
        self.condition = threading.Condition()
        self.pending = None
        self.stopping = False
        self.writing_since = None
        self.stats = {'snapshot_write_failures': 0, 'last_snapshot_error': None,
                      'writes': 0, 'coalesced': 0, 'pending': 0, 'writing': False}
        self.worker = threading.Thread(target=self._run, name='recovery-writer', daemon=True)
        self.worker.start()

    def submit(self, snapshot, *, instance_id=None, state_sequence=None):
        with self.condition:
            if self.stopping:
                return False
            if self.pending is not None:
                self.stats['coalesced'] += 1
            self.pending = RecoveryCheckpoint(snapshot, instance_id, state_sequence)
            self.stats['pending'] = 1
            self.condition.notify()
            return True

    def diagnostics(self):
        with self.condition:
            return dict(deepcopy(self.stats), worker_alive=self.worker.is_alive(),
                        writing_age_ms=(time.monotonic()-self.writing_since)*1000 if self.writing_since else 0)

    def _run(self):
        next_attempt = 0
        delay = self.retry_min
        try:
            while True:
                with self.condition:
                    while self.pending is None or (not self.stopping and time.monotonic() < next_attempt):
                        if self.stopping:
                            return
                        timeout = max(0, next_attempt-time.monotonic()) if self.pending is not None else None
                        self.condition.wait(timeout)
                    value, self.pending = self.pending, None
                    self.stats.update(pending=0, writing=True)
                    self.writing_since = time.monotonic()
                try:
                    ok = self.publisher.publish(value.recovery_value())
                except (OSError, ValueError, TypeError) as error:
                    # Also support injected publishers; do not retain payloads
                    # or error text in diagnostics.
                    self.publisher.failures += 1
                    self.publisher.last_error = {'type': type(error).__name__, 'errno': getattr(error, 'errno', None)}
                    ok = False
                with self.condition:
                    self.writing_since = None
                    self.stats.update(writing=False,
                                      current_error=None if ok else self.publisher.last_error,
                                      snapshot_write_failures=self.publisher.failures,
                                      last_snapshot_error=self.publisher.last_error)
                    if ok:
                        self.stats['writes'] += 1
                        delay = self.retry_min
                        next_attempt = 0
                    elif not self.stopping:
                        # Prefer a newer checkpoint that arrived during I/O.
                        if self.pending is None:
                            self.pending = value
                            self.stats['pending'] = 1
                        next_attempt = time.monotonic()+delay
                        delay = min(self.retry_max, delay*2)
                    if self.stopping and self.pending is None:
                        return
        finally:
            if self.on_exit:
                self.on_exit()

    def close(self, timeout=2):
        with self.condition:
            self.stopping = True
            self.condition.notify()
        self.worker.join(timeout)
        return not self.worker.is_alive()
