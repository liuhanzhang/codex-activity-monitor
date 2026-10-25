"""Finite, read-only state probes and narrowly scoped lease management RPC."""

from dataclasses import dataclass
import json
import math
import socket
import time
import uuid

from .publication import encode_frame
from .state_transport import MAX_FRAME_BYTES, MAX_REQUEST_BYTES


class StateClientError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _sequence(value):
    return type(value) is int and 0 <= value < 2**64


def _reject_constant(_):
    raise ValueError('non_finite_json')


class StateConnection:
    """One request scope, one monotonic deadline, no recovery-file fallback."""

    def __init__(self, runtime, timeout=1):
        self.path = runtime/'state.sock'
        self.deadline = time.monotonic()+timeout
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.buffer = bytearray()
        self.searched = 0

    def _remaining(self):
        remaining = self.deadline-time.monotonic()
        if remaining <= 0:
            raise TimeoutError('state_deadline')
        self.socket.settimeout(remaining)

    def __enter__(self):
        try:
            self._remaining()
            self.socket.connect(str(self.path))
        except BaseException:
            self.socket.close()
            raise
        return self

    def __exit__(self, *_):
        self.socket.close()

    def request(self, kind, **fields):
        nonce = uuid.uuid4().hex
        value = encode_frame({**fields, 'protocol_version': 1, 'kind': kind, 'nonce': nonce})
        if len(value) > MAX_REQUEST_BYTES:
            raise StateClientError('request_too_large')
        self._remaining()
        self.socket.sendall(value)
        return nonce

    def receive(self):
        while True:
            self._remaining()
            end = self.buffer.find(b'\n', self.searched)
            if end >= 0:
                if end+1 > MAX_FRAME_BYTES:
                    raise StateClientError('frame_too_large')
                line, self.buffer = bytes(self.buffer[:end]), self.buffer[end+1:]
                self.searched = 0
                try:
                    value = json.loads(line, parse_constant=_reject_constant)
                except (ValueError, UnicodeError):
                    raise StateClientError('invalid_frame') from None
                if (not isinstance(value, dict) or type(value.get('protocol_version')) is not int or
                        value['protocol_version'] != 1):
                    raise StateClientError('unsupported_protocol')
                instance = value.get('instance_id')
                if not isinstance(instance, str) or not instance or len(instance) > 128:
                    raise StateClientError('invalid_instance')
                self._remaining()
                return value
            self.searched = len(self.buffer)
            if len(self.buffer) >= MAX_FRAME_BYTES:
                raise StateClientError('frame_too_large')
            data = self.socket.recv(min(65_536, MAX_FRAME_BYTES-len(self.buffer)))
            if not data:
                raise StateClientError('disconnected')
            self.buffer.extend(data)


@dataclass(frozen=True)
class LiveSnapshot:
    instance_id: str
    sequence: int
    payload: dict
    acknowledged_at: float
    tick_age_seconds: float
    persistence: dict
    observation: dict | None

    def is_fresh(self, now=None):
        elapsed = (time.monotonic() if now is None else now)-self.acknowledged_at
        return self.payload.get('running') is True and 0 <= elapsed < 2 and self.tick_age_seconds+elapsed < 2

    def display_value(self):
        return {**self.payload, 'connection': {'version': 1, 'instance_id': self.instance_id,
                'sequence': self.sequence, 'healthy': self.is_fresh()}, 'persistence': self.persistence}


def state_once(runtime, *, timeout=1):
    """A new nonce proves transport health; payload timestamps prove nothing."""
    with StateConnection(runtime, timeout) as connection:
        sent_at = time.monotonic()
        nonce = connection.request('subscribe', role='reader')
        hello = connection.receive()
        received_at = time.monotonic()
        age = hello.get('tick_age_ms')
        try:
            valid_age = type(age) in (int, float) and math.isfinite(age) and age >= 0
        except OverflowError:
            valid_age = False
        if (hello.get('kind') != 'hello' or hello.get('nonce') != nonce or
                not valid_age or not _sequence(hello.get('last_state_sequence')) or
                not isinstance(hello.get('persistence', {}), dict) or
                (hello.get('observation') is not None and not isinstance(hello['observation'], dict))):
            raise StateClientError('invalid_hello')
        age_seconds = age/1000+(received_at-sent_at)
        while True:
            frame = connection.receive()
            if frame['instance_id'] != hello['instance_id']:
                raise StateClientError('instance_changed')
            if frame.get('kind') == 'error':
                raise StateClientError('state_unavailable')
            sequence = frame.get('sequence')
            payload = frame.get('payload')
            if (frame.get('kind') != 'state' or not _sequence(sequence) or sequence == 0 or
                    not isinstance(payload, dict) or type(payload.get('schema_version')) is not int or
                    payload['schema_version'] != 1 or not isinstance(payload.get('threads'), list)):
                raise StateClientError('invalid_state')
            if sequence < hello['last_state_sequence']:
                continue
            return LiveSnapshot(frame['instance_id'], sequence, payload, received_at, age_seconds,
                                hello.get('persistence') or {}, hello.get('observation'))


def authorize_session(runtime, session, control, *, timeout=1):
    """Management alone supplies the private collector token and new grant."""
    with StateConnection(runtime, timeout) as connection:
        nonce = connection.request('authorize', session=session, control_token=control['token'])
        reply = connection.receive()
        if reply.get('nonce') != nonce:
            raise StateClientError('invalid_nonce')
        if control.get('instance_id') and reply['instance_id'] != control['instance_id']:
            raise StateClientError('instance_changed')
        if reply.get('kind') == 'error':
            raise StateClientError(reply.get('code') or 'authorization_failed')
        if reply.get('kind') != 'authorized':
            raise StateClientError('invalid_authorization')


def current_state(runtime, *, timeout=1):
    try:
        return state_once(runtime, timeout=timeout).display_value()
    except (OSError, ValueError):
        return {'schema_version': 1, 'running': False, 'updated_at': None, 'threads': [],
                'backends': {}, 'counters': {}, 'connection': {'version': 1, 'healthy': False}}
