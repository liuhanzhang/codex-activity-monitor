"""Bounded local state subscriptions, independent of recovery-file I/O."""

from collections import deque
from dataclasses import dataclass, field
import json
import hmac
import selectors
import socket
import threading
import time
import uuid

from .publication import DetachedSnapshot, encode_frame
from .observation_lease import LeaseError, SessionGrant


PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 16_384
MAX_FRAME_BYTES = 8 * 1024 * 1024


@dataclass
class _Peer:
    socket: socket.socket
    accepted_at: float
    received: bytearray = field(default_factory=bytearray)
    controls: deque = field(default_factory=deque)
    subscribed: bool = False
    handshake_completed: bool = False
    role: str = 'reader'
    connection_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    last_sequence: int = 0
    frame: bytes | None = None
    frame_sequence: int | None = None
    sent: int = 0
    send_started: float = 0
    close_after_send: bool = False


class StateServer:
    """One event-loop owner of sockets; the collector only replaces a value.

    Lease support is optional during migration. Its state is in memory and
    renewals never call management I/O.
    """

    def __init__(self, path, *, diagnostics=None, max_clients=4,
                 max_frame_bytes=MAX_FRAME_BYTES, write_timeout=3, on_exit=None,
                 lease=None, control_token=None, instance_id=None):
        self.path = path
        self.instance = instance_id or uuid.uuid4().hex
        self.max_clients = max_clients
        self.max_frame_bytes = max_frame_bytes
        self.write_timeout = write_timeout
        self.persistence = diagnostics or (lambda: {})
        self.on_exit = on_exit
        self.lease = lease
        self.control_token = control_token
        self.lock = threading.Lock()
        self.candidate = None
        self.sequence = 0
        self.tick_at = time.monotonic()
        self.stats = {'clients': 0, 'rejected_clients': 0, 'malformed': 0,
                      'oversized_states': 0, 'max_queued_bytes': 0}
        self.stopping = threading.Event()
        self.drain_deadline = 0
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.inode = None
        try:
            self.path.unlink(missing_ok=True)
            self.listener.bind(str(path))
            self.inode = self.path.stat().st_ino
            self.path.chmod(0o600)
            self.listener.listen(max_clients)
            self.listener.setblocking(False)
            self.reader, self.waker = socket.socketpair()
            self.reader.setblocking(False)
            self.waker.setblocking(False)
        except BaseException:
            self.listener.close()
            try:
                if self.inode is not None and self.path.stat().st_ino == self.inode:
                    self.path.unlink()
            except OSError:
                pass
            raise
        self.worker = threading.Thread(target=self._run, name='state-subscriptions', daemon=True)
        self.worker.start()

    def publish(self, snapshot: DetachedSnapshot):
        with self.lock:
            self.sequence += 1
            self.candidate = (self.sequence, snapshot)
            sequence = self.sequence
        self._wake()
        return sequence

    def progress(self, now=None):
        with self.lock:
            self.tick_at = time.monotonic() if now is None else now

    def diagnostics(self):
        with self.lock:
            return dict(self.stats, instance_id=self.instance, sequence=self.sequence)

    def _wake(self):
        try:
            self.waker.send(b'x')
        except (BlockingIOError, OSError):
            pass

    def _heartbeat(self, kind, nonce, sequence):
        with self.lock:
            age = max(0, time.monotonic()-self.tick_at)*1000
        return encode_frame({'protocol_version': PROTOCOL_VERSION, 'kind': kind,
                             'nonce': nonce, 'instance_id': self.instance,
                             'last_state_sequence': sequence, 'tick_age_ms': age,
                             'persistence': self.persistence(),
                             'observation': self.lease.status() if self.lease else None})

    def _request(self, peer, value, sequence):
        if peer.close_after_send:
            raise ValueError('connection_closing')
        if not isinstance(value, dict) or type(value.get('protocol_version')) is not int or value['protocol_version'] != PROTOCOL_VERSION:
            raise ValueError('unsupported_protocol')
        nonce = value.get('nonce')
        if not isinstance(nonce, str) or not nonce or len(nonce) > 128:
            raise ValueError('invalid_nonce')
        kind = value.get('kind')
        if len(peer.controls) >= 8:
            raise ValueError('control_backpressure')
        try:
            if kind == 'subscribe' and not peer.subscribed:
                peer.role = value.get('role', 'reader')
                if peer.role == 'owner' and self.lease:
                    self.lease.acquire(peer.connection_id, value.get('session_id'), value.get('session_secret'))
                elif peer.role != 'reader':
                    raise ValueError('unsupported_role')
                peer.subscribed = True
                reply = self._heartbeat('hello', nonce, sequence)
            elif kind == 'heartbeat' and peer.subscribed:
                if peer.role == 'owner':
                    self.lease.renew(peer.connection_id)
                reply = self._heartbeat('heartbeat', nonce, sequence)
            elif kind == 'release' and peer.subscribed and peer.role == 'owner':
                self.lease.release(peer.connection_id)
                peer.subscribed = False
                peer.close_after_send = True
                reply = encode_frame({'protocol_version': 1, 'kind': 'released',
                                      'instance_id': self.instance, 'nonce': nonce})
            elif kind == 'authorize' and not peer.subscribed and self.lease:
                credential = value.get('control_token')
                if (not isinstance(credential, str) or not credential.isascii() or not self.control_token or
                        not hmac.compare_digest(credential, self.control_token)):
                    raise LeaseError('invalid_lease')
                self.lease.authorize(SessionGrant.from_record(value.get('session')))
                peer.close_after_send = True
                reply = encode_frame({'protocol_version': 1, 'kind': 'authorized',
                                      'instance_id': self.instance, 'nonce': nonce})
            else:
                raise ValueError('invalid_request')
        except LeaseError as error:
            peer.subscribed = False
            peer.close_after_send = True
            reply = encode_frame({'protocol_version': 1, 'kind': 'error', 'code': error.code,
                                  'instance_id': self.instance, 'nonce': nonce})
        peer.controls.append(reply)

    def _run(self):
        selector = selectors.DefaultSelector()
        peers = {}
        latest = None
        encoded_sequence = 0
        selector.register(self.listener, selectors.EVENT_READ)
        selector.register(self.reader, selectors.EVENT_READ)

        def close_peer(peer):
            peers.pop(peer.socket, None)
            if self.lease and peer.role == 'owner':
                self.lease.disconnect(peer.connection_id)
            try:
                selector.unregister(peer.socket)
            except (KeyError, OSError):
                pass
            peer.socket.close()
            with self.lock:
                self.stats['clients'] = len(peers)

        try:
            while True:
                with self.lock:
                    candidate = self.candidate
                if candidate is not None and candidate[0] != encoded_sequence:
                    encoded_sequence, snapshot = candidate
                    try:
                        frame = snapshot.encode(self.instance, encoded_sequence)
                        if len(frame) > self.max_frame_bytes:
                            raise ValueError('state_too_large')
                    except (ValueError, TypeError):
                        frame = encode_frame({'protocol_version': PROTOCOL_VERSION, 'kind': 'error',
                                              'instance_id': self.instance, 'sequence': encoded_sequence,
                                              'code': 'state_unavailable'})
                        with self.lock:
                            self.stats['oversized_states'] += 1
                    latest = (encoded_sequence, frame)

                now = time.monotonic()
                if self.stopping.is_set() and (now >= self.drain_deadline or all(
                        not p.frame and not p.controls and
                        (not p.subscribed or (latest and p.last_sequence >= latest[0])) for p in peers.values())):
                    break
                for peer in list(peers.values()):
                    waiting = bool(peer.frame or peer.controls or
                                   (peer.subscribed and latest and peer.last_sequence < latest[0]))
                    if waiting and not peer.send_started:
                        peer.send_started = now
                    elif not waiting:
                        peer.send_started = 0
                    if ((not peer.handshake_completed and now-peer.accepted_at > 3) or
                            (waiting and now-peer.send_started > self.write_timeout)):
                        close_peer(peer)
                        continue
                    selector.modify(peer.socket, selectors.EVENT_READ | (selectors.EVENT_WRITE if waiting else 0))
                    queued = (len(peer.frame)-peer.sent if peer.frame else 0) + sum(map(len, peer.controls))
                    if latest and peer.subscribed and peer.last_sequence < latest[0] and peer.frame_sequence != latest[0]:
                        queued += len(latest[1])
                    with self.lock:
                        self.stats['max_queued_bytes'] = max(self.stats['max_queued_bytes'], queued)
                        self.stats['clients'] = len(peers)

                for key, events in selector.select(.025):
                    sock = key.fileobj
                    if sock is self.reader:
                        try:
                            while self.reader.recv(1024):
                                pass
                        except BlockingIOError:
                            pass
                        continue
                    if sock is self.listener:
                        try:
                            connection, _ = self.listener.accept()
                        except BlockingIOError:
                            continue
                        if self.stopping.is_set() or len(peers) >= self.max_clients:
                            connection.close()
                            with self.lock:
                                self.stats['rejected_clients'] += 1
                            continue
                        connection.setblocking(False)
                        peer = _Peer(connection, time.monotonic())
                        peers[connection] = peer
                        selector.register(connection, selectors.EVENT_READ)
                        continue
                    peer = peers.get(sock)
                    if peer is None:
                        continue
                    try:
                        if events & selectors.EVENT_READ:
                            data = sock.recv(MAX_REQUEST_BYTES+1)
                            if not data:
                                close_peer(peer)
                                continue
                            peer.received.extend(data)
                            while b'\n' in peer.received:
                                line, _, rest = peer.received.partition(b'\n')
                                if len(line) > MAX_REQUEST_BYTES:
                                    raise ValueError('request_too_large')
                                peer.received = bytearray(rest)
                                self._request(peer, json.loads(line), encoded_sequence)
                            if len(peer.received) > MAX_REQUEST_BYTES:
                                raise ValueError('request_too_large')
                        if events & selectors.EVENT_WRITE:
                            if peer.frame is None:
                                if peer.controls:
                                    peer.frame = peer.controls.popleft()
                                    peer.frame_sequence = None
                                elif peer.subscribed and latest and peer.last_sequence < latest[0]:
                                    peer.frame_sequence, peer.frame = latest
                                if peer.frame is not None:
                                    if not peer.send_started:
                                        peer.send_started = time.monotonic()
                                    peer.sent = 0
                            if peer.frame is not None:
                                peer.sent += sock.send(memoryview(peer.frame)[peer.sent:])
                                if peer.sent == len(peer.frame):
                                    if peer.frame_sequence is not None:
                                        peer.last_sequence = peer.frame_sequence
                                    peer.frame = None
                                    peer.frame_sequence = None
                                    peer.send_started = 0
                                    peer.handshake_completed = True
                                    if peer.close_after_send and not peer.controls:
                                        close_peer(peer)
                    except BlockingIOError:
                        pass
                    except (OSError, ValueError, TypeError, UnicodeError):
                        with self.lock:
                            self.stats['malformed'] += 1
                        close_peer(peer)
        finally:
            for peer in list(peers.values()):
                close_peer(peer)
            selector.close()
            self.listener.close()
            self.reader.close()
            self.waker.close()
            try:
                if self.path.stat().st_ino == self.inode:
                    self.path.unlink()
            except OSError:
                pass
            if self.on_exit:
                self.on_exit()

    def close(self, timeout=1):
        # Best effort terminal delivery. A paused reader cannot extend shutdown.
        self.drain_deadline = time.monotonic()+min(.25, timeout)
        self.stopping.set()
        self._wake()
        self.worker.join(timeout)
        return not self.worker.is_alive()
