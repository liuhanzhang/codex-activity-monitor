"""Bounded admission evidence, never a second history or completion display.

Terminal IDs survive removal of display/stream state for this collector's
lifetime. Exhaustion closes observation admission; it never forgets an ID and
then treats an old item as new. A new collector establishes a fresh baseline.
"""

import hashlib
import json


class IdentityBudget:
    def __init__(self, capacity=65_536, max_identity_bytes=8*1024*1024):
        if capacity < 0 or max_identity_bytes < 0:
            raise ValueError('invalid_identity_budget')
        self.capacity = capacity
        self.max_identity_bytes = max_identity_bytes
        self.identity_bytes = 0
        self.closed = set()
        self.reserved = set()
        self.sealed = False

    @staticmethod
    def _key(kind, backend, identity):
        return hashlib.sha256(json.dumps((kind, backend, identity), ensure_ascii=False,
                                        separators=(',', ':')).encode()).digest()

    def contains(self, kind, backend, identity):
        return self._key(kind, backend, identity) in self.closed

    def reserve(self, kind, backend, identity):
        if not ((isinstance(identity, str) and 0 < len(identity) <= 4096 and '\0' not in identity) or
                (kind == 'request' and type(identity) is int)):
            raise ValueError('invalid_observation_identity')
        key = self._key(kind, backend, identity)
        if key in self.closed or key in self.reserved:
            return True
        size = len(str(identity).encode('utf-8'))
        if (len(self.closed)+len(self.reserved) >= self.capacity or
                self.identity_bytes+size > self.max_identity_bytes):
            self.sealed = True
            return False
        self.reserved.add(key)
        self.identity_bytes += size
        return True

    def retire(self, kind, backend, identity):
        if identity is None:
            return
        key = self._key(kind, backend, identity)
        if key in self.closed:
            return
        if key not in self.reserved and not self.reserve(kind, backend, identity):
            return
        self.reserved.discard(key)
        self.closed.add(key)

    def diagnostics(self):
        return {'entries': len(self.closed)+len(self.reserved), 'terminal_entries': len(self.closed),
                'identity_bytes': self.identity_bytes, 'max_identity_bytes': self.max_identity_bytes,
                'max_entries': self.capacity, 'admission_closed': self.sealed}


def observation_identities(event):
    """Only explicit protocol identities; no attribution by timing or names."""
    value = event.data
    def record(value): return value if isinstance(value, dict) else {}
    params = record(value.get('params'))
    item = record(value.get('item')) or record(params.get('item'))
    metadata = {**record(value.get('identity')), **record(item.get('metadata'))}
    thread = record(params.get('thread')) or record(record(value.get('result')).get('thread'))
    turn = record(params.get('turn'))
    candidates = [('backend', event.backend), ('thread', params.get('threadId') or thread.get('id') or metadata.get('thread_id')),
                  ('turn', params.get('turnId') or turn.get('id') or metadata.get('turn_id')),
                  ('item', item.get('id') or value.get('item_id') or params.get('itemId')),
                  ('request', value.get('id')), ('request', params.get('requestId'))]
    return [(kind, identity) for kind, identity in candidates if identity is not None]
