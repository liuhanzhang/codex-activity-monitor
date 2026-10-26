"""In-memory observation ownership; no filesystem or process probes on renewal."""

from dataclasses import dataclass, field
import hmac
import threading
import time


class LeaseError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _credential(value):
    return isinstance(value, str) and value.isascii() and 16 <= len(value) <= 128


@dataclass(frozen=True)
class SessionGrant:
    id: str
    owner_pid: int
    secret: str = field(repr=False)

    @classmethod
    def from_record(cls, value):
        if (not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != 2 or
                value.get('active') is not True or not _credential(value.get('id')) or
                not _credential(value.get('secret')) or type(value.get('owner_pid')) is not int or
                not 1 < value['owner_pid'] < 2**31):
            raise LeaseError('invalid_lease')
        return cls(value['id'], value['owner_pid'], value['secret'])

    def matches(self, session_id, secret):
        return (_credential(session_id) and _credential(secret) and
                hmac.compare_digest(self.id, session_id) and hmac.compare_digest(self.secret, secret))


class ObservationLease:
    """A grant authorizes one renewable connection, with a finite reconnect grace.

    Management installs grants after validating its configuration and owner.
    Readers never call acquire/renew; legacy explicit-start runtimes have no GUI
    owner. A disconnected owner retains only its existing deadline, not a new
    grace period per disconnect. Release revokes the grant immediately.
    """

    def __init__(self, *, managed, grant=None, ttl=5, now=None):
        self.managed = managed
        self.ttl = ttl
        self.lock = threading.Lock()
        self.grant = grant
        self.connection = None
        self.expires = (time.monotonic() if now is None else now)+ttl if grant else 0

    def _remaining(self, now):
        return max(0, self.expires-now) if self.grant else 0

    def remaining(self, now=None):
        with self.lock:
            return self._remaining(time.monotonic() if now is None else now) if self.managed else float('inf')

    def is_observing(self, now=None):
        return self.remaining(now) > 0

    def status(self, now=None):
        with self.lock:
            remaining = self._remaining(time.monotonic() if now is None else now)
            return {'protocol_version': 2, 'managed': self.managed, 'owner_connected': self.connection is not None and remaining > 0,
                    'session_id': self.grant.id if self.grant else None,
                    'owner_pid': self.grant.owner_pid if self.grant else None,
                    'expires_in_ms': remaining*1000 if self.managed else None}

    def authorize(self, grant, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            if not self.managed:
                raise LeaseError('unmanaged_runtime')
            if self._remaining(now) > 0 and self.grant.owner_pid != grant.owner_pid:
                raise LeaseError('owner_busy')
            if self._remaining(now) > 0 and self.connection is not None and self.grant == grant:
                return  # A retried management acknowledgement cannot revoke its live owner.
            self.grant, self.connection, self.expires = grant, None, now+self.ttl

    def acquire(self, connection, session_id, secret, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            if not self.managed:
                raise LeaseError('unmanaged_runtime')
            if self._remaining(now) <= 0:
                raise LeaseError('lease_expired')
            if not self.grant.matches(session_id, secret):
                raise LeaseError('invalid_lease')
            self.connection, self.expires = connection, now+self.ttl

    def renew(self, connection, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            if self.connection != connection or self.connection is None:
                raise LeaseError('lease_superseded')
            if self._remaining(now) <= 0:
                raise LeaseError('lease_expired')
            self.expires = now+self.ttl

    def release(self, connection):
        with self.lock:
            if self.connection != connection or self.connection is None:
                raise LeaseError('lease_superseded')
            self.grant, self.connection, self.expires = None, None, 0

    def disconnect(self, connection):
        with self.lock:
            if self.connection == connection:
                self.connection = None
