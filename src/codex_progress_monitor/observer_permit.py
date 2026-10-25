"""Nonce-bound observation permits owned by the bridge's disposable worker."""

import json
import math
import time
import uuid

from .publication import encode_frame


class PermitExchange:
    def __init__(self):
        self.reset()

    def reset(self):
        self.until = 0
        self.instance = None
        self.pending = None
        self.next_request = 0
        self.buffer = bytearray()

    def request(self, backend, now=None):
        now = time.monotonic() if now is None else now
        if self.pending and now-self.pending[1] >= .75:
            raise TimeoutError('observation_permit_timeout')
        if self.pending or now < self.next_request:
            return None
        nonce = uuid.uuid4().hex
        self.pending = (nonce, now)
        self.next_request = now+.5
        return encode_frame({'control': 'observe', 'protocol_version': 1, 'backend': backend, 'nonce': nonce})

    def receive(self, data, now=None):
        now = time.monotonic() if now is None else now
        self.buffer.extend(data)
        while b'\n' in self.buffer:
            line, _, rest = self.buffer.partition(b'\n')
            if len(line) > 4096:
                raise ValueError('permit_too_large')
            self.buffer = bytearray(rest)
            reply = json.loads(line)
            if not isinstance(reply, dict):
                raise ValueError('invalid_permit')
            if not self.pending or reply.get('nonce') != self.pending[0]:
                continue  # Buffered replies cannot renew permission.
            round_trip = now-self.pending[1]
            ttl, age, instance = reply.get('ttl_ms'), reply.get('tick_age_ms'), reply.get('instance_id')
            if (type(reply.get('protocol_version')) is not int or reply['protocol_version'] != 1 or
                    reply.get('kind') != 'observe_permit' or not isinstance(instance, str) or not 1 <= len(instance) <= 128 or
                    type(ttl) not in (int, float) or not 0 <= ttl <= 1500 or
                    type(age) not in (int, float) or not 0 <= age < 1000 or not math.isfinite(age) or
                    type(reply.get('observing')) is not bool or not 0 <= round_trip < .75 or
                    (self.instance is not None and self.instance != instance)):
                raise ValueError('invalid_permit')
            self.instance = instance
            self.pending = None
            self.until = now+max(0, ttl/1000-round_trip) if reply['observing'] else 0
        if len(self.buffer) > 4096:
            raise ValueError('permit_too_large')
