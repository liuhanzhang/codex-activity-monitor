import json
import os
from pathlib import Path
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest

from codex_activity_monitor.publication import DetachedSnapshot
from codex_activity_monitor.runtime import collector_alive
from codex_activity_monitor.state_transport import StateServer, MAX_REQUEST_BYTES
from codex_activity_monitor.observation_lease import ObservationLease, SessionGrant


class JSONPeer:
    def __init__(self, path):
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(2)
        self.socket.connect(str(path))
        self.reader = self.socket.makefile('rb')

    def send(self, kind='subscribe', nonce='test', **fields):
        self.socket.sendall(json.dumps({'protocol_version': 1, 'kind': kind, 'nonce': nonce,
                                       **fields}, ensure_ascii=False).encode()+b'\n')

    def until(self, predicate):
        deadline = time.monotonic()+3
        while time.monotonic() < deadline:
            line = self.reader.readline()
            if not line:
                raise EOFError('state connection closed')
            value = json.loads(line)
            if predicate(value):
                return value
        raise AssertionError('state predicate not reached')

    def close(self):
        self.reader.close()
        self.socket.close()


class StateTransportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='cam-state-', dir='/tmp')
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)/'state.sock'
        self.server = StateServer(self.path)
        self.addCleanup(self.server.close)

    def peer(self):
        peer = JSONPeer(self.path)
        self.addCleanup(peer.close)
        return peer

    def wait(self, predicate, timeout=2):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.01)
        self.fail('transport condition not reached')

    def publish(self, **fields):
        self.server.publish(DetachedSnapshot.capture({'schema_version': 1, **fields}))

    def test_full_state_permissions_fragmented_utf8_and_nonce_health(self):
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.publish(threads=[{'id': 't'}])
        peer = self.peer()
        raw = json.dumps({'protocol_version': 1, 'kind': 'subscribe', 'nonce': '中文边界'},
                         ensure_ascii=False).encode()+b'\n'
        for byte in raw:
            peer.socket.sendall(bytes([byte]))
        hello = peer.until(lambda v:v['kind'] == 'hello')
        self.assertEqual(hello['nonce'], '中文边界')
        value = peer.until(lambda v:v['kind'] == 'state')
        self.assertEqual(value['payload']['threads'], [{'id': 't'}])
        self.assertEqual(value['sequence'], 1)
        self.assertEqual(value['instance_id'], hello['instance_id'])
        self.server.progress(time.monotonic()-5)
        peer.send('heartbeat', nonce='fresh-roundtrip')
        reply = peer.until(lambda v:v['kind'] == 'heartbeat')
        self.assertEqual(reply['nonce'], 'fresh-roundtrip')
        self.assertGreaterEqual(reply['tick_age_ms'], 5000)
        self.assertEqual(reply['last_state_sequence'], 1)
        self.server.progress()
        # Packed lines are independently bounded, not rejected by aggregate size.
        frames = [{'protocol_version': 1, 'kind': 'heartbeat', 'nonce': str(n), 'padding': 'x'*9000}
                  for n in range(2)]
        peer.socket.sendall(b''.join(json.dumps(f).encode()+b'\n' for f in frames))
        for n in range(2):
            reply = peer.until(lambda v:v.get('nonce') == str(n))
            self.assertLess(reply['tick_age_ms'], 1000)

    def test_reconnect_has_new_instance_and_sequence(self):
        self.publish(n=1)
        peer = self.peer()
        peer.send()
        old = peer.until(lambda v:v['kind'] == 'state')
        self.assertTrue(self.server.close())
        replacement = StateServer(self.path)
        self.addCleanup(replacement.close)
        replacement.publish(DetachedSnapshot.capture({'schema_version': 1, 'n': 2}))
        other = self.peer()
        other.send()
        new = other.until(lambda v:v['kind'] == 'state')
        self.assertNotEqual(old['instance_id'], new['instance_id'])
        self.assertEqual(new['sequence'], 1)

    def test_bad_requests_are_closed_without_affecting_other_peers(self):
        self.publish(n=0)
        good = self.peer()
        good.send()
        good.until(lambda v:v['kind'] == 'state')
        for raw in (b'{}\n', b'[]\n', b'\xff\n', b'x'*(MAX_REQUEST_BYTES+1),
                    b'{"protocol_version":true,"kind":"subscribe","nonce":"n"}\n',
                    b'{"protocol_version":1,"kind":"subscribe","nonce":"n","role":"owner"}\n'):
            with self.subTest(raw=raw[:40]):
                bad = self.peer()
                bad.socket.sendall(raw)
                self.assertEqual(bad.reader.read(1), b'')
        good.send('heartbeat')
        good.until(lambda v:v['kind'] == 'heartbeat')
        self.assertGreaterEqual(self.server.diagnostics()['malformed'], 6)

    def test_client_limit_and_slow_reader_are_bounded(self):
        self.server.close()
        self.server = StateServer(self.path, max_clients=2, write_timeout=.5)
        self.addCleanup(self.server.close)
        slow = self.peer()
        slow.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8192)
        slow.send()
        slow.until(lambda v:v['kind'] == 'hello')
        fast = self.peer()
        fast.send()
        fast.until(lambda v:v['kind'] == 'hello')
        self.wait(lambda:self.server.diagnostics()['clients'] == 2)
        extra = self.peer()
        self.assertEqual(extra.reader.read(1), b'')
        self.wait(lambda:self.server.diagnostics()['rejected_clients'] > 0)
        for n in range(6):
            self.publish(n=n, padding='x'*500_000)
            state = fast.until(lambda v:v['kind'] == 'state' and v['payload']['n'] == n)
            self.assertEqual(state['sequence'], n+1)
        self.wait(lambda:self.server.diagnostics()['clients'] == 1)
        self.assertLessEqual(self.server.diagnostics()['max_queued_bytes'], 2*501_000+8192)
        fast.send('heartbeat')
        fast.until(lambda v:v['kind'] == 'heartbeat')

    def test_oversized_state_is_explicit_and_next_valid_state_recovers(self):
        self.server.max_frame_bytes = 1024
        peer = self.peer()
        peer.send()
        peer.until(lambda v:v['kind'] == 'hello')
        self.publish(padding='x'*2048)
        error = peer.until(lambda v:v['kind'] == 'error')
        self.assertEqual(error['code'], 'state_unavailable')
        self.assertEqual(error['sequence'], 1)
        self.publish(n=2)
        state = peer.until(lambda v:v['kind'] == 'state')
        self.assertEqual(state['sequence'], 2)
        self.assertEqual(self.server.diagnostics()['oversized_states'], 1)


class CollectorPublicationTests(unittest.TestCase):
    def test_thirty_second_archive_stall_keeps_events_health_and_shutdown_responsive(self):
        with tempfile.TemporaryDirectory(prefix='cam-stall-', dir='/tmp') as directory:
            runtime = Path(directory)
            script = '''
import time
from pathlib import Path
import codex_activity_monitor.runtime as persistence
from codex_activity_monitor.collector import serve
original = persistence.atomic_json
def stalled(path, value):
    if path.name == 'snapshot.json':
        (path.parent/'entered').touch()
        while not (path.parent/'release').exists(): time.sleep(.01)
    return original(path, value)
persistence.atomic_json = stalled
serve(Path(RUNTIME))
'''.replace('RUNTIME', repr(directory))
            with (runtime/'probe.log').open('w') as log:
                process = subprocess.Popen([sys.executable, '-B', '-c', script], stdout=log, stderr=log)
                try:
                    deadline = time.monotonic()+5
                    while not (runtime/'entered').exists() and time.monotonic() < deadline:
                        self.assertIsNone(process.poll(), (runtime/'probe.log').read_text())
                        time.sleep(.02)
                    self.assertTrue((runtime/'entered').exists())
                    peer = JSONPeer(runtime/'state.sock')
                    self.addCleanup(peer.close)
                    peer.send()
                    peer.until(lambda v:v['kind'] == 'state')
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as events:
                        events.connect(str(runtime/'events.sock'))
                        def send(method, params):
                            events.sendall(json.dumps({'backend': 'fixture', 'direction': 'protocol_out',
                                'observed_ns': time.monotonic_ns(), 'observed_timestamp': time.time(),
                                'event': {'method': method, 'params': params}}).encode()+b'\n')
                        send('turn/started', {'threadId': 't', 'turn': {'id': 'u'}})
                        peer.until(lambda v:v['kind'] == 'state' and any(t['status'] == 'inProgress'
                                   for t in v['payload']['threads']))
                        started = time.monotonic()
                        while time.monotonic()-started < 30:
                            # Real bridges keep the ingestion connection alive.
                            send('fixture/heartbeat', {})
                            nonce = os.urandom(8).hex()
                            peer.send('heartbeat', nonce=nonce)
                            reply = peer.until(lambda v:v.get('nonce') == nonce)
                            self.assertLess(reply['tick_age_ms'], 1000)
                            self.assertTrue(reply['persistence']['writing'])
                            self.assertFalse((runtime/'snapshot.json').exists())
                            time.sleep(.5)
                        before = time.monotonic()
                        send('turn/completed', {'threadId': 't', 'turn': {'id': 'u', 'status': 'completed'}})
                        peer.until(lambda v:v['kind'] == 'state' and any(t['status'] == 'completed'
                                   for t in v['payload']['threads']))
                        self.assertLess(time.monotonic()-before, .9)
                        self.assertTrue(collector_alive(runtime))
                        process.send_signal(signal.SIGTERM)
                        # The writer still owns the runtime during finite shutdown.
                        time.sleep(.2)
                        self.assertTrue(collector_alive(runtime))
                        process.wait(timeout=4)
                        self.assertFalse(collector_alive(runtime))
                        self.assertFalse((runtime/'snapshot.json').exists())
                        self.assertFalse((runtime/'state.sock').exists())
                finally:
                    (runtime/'release').touch()
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=2)


class LeaseTransportTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='cam-owner-', dir='/tmp')
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)/'state.sock'
        self.record = {'version': 2, 'id': 'i'*32, 'owner_pid': 100, 'secret': 's'*64, 'active': True}
        self.lease = ObservationLease(managed=True, grant=SessionGrant.from_record(self.record))
        self.server = StateServer(self.path, lease=self.lease, control_token='management-secret')
        self.addCleanup(self.server.close)
        self.server.publish(DetachedSnapshot.capture({'schema_version': 1, 'threads': []}))

    def peer(self, role='reader'):
        peer = JSONPeer(self.path)
        self.addCleanup(peer.close)
        peer.send(role=role, session_id=self.record['id'], session_secret=self.record['secret'])
        return peer

    def test_only_owner_connection_renews_and_release_revokes_grant(self):
        reader = self.peer()
        hello = reader.until(lambda v:v['kind'] == 'hello')
        self.assertFalse(hello['observation']['owner_connected'])
        self.assertNotIn(self.record['secret'], json.dumps(hello))
        before = self.lease.expires
        reader.send('heartbeat')
        reader.until(lambda v:v['kind'] == 'heartbeat')
        self.assertEqual(self.lease.expires, before)
        first = self.peer('owner')
        first.until(lambda v:v['kind'] == 'hello')
        second = self.peer('owner')
        second.until(lambda v:v['kind'] == 'hello')
        # A superseded connection cannot renew or release the new owner.
        first.send('release')
        failure = first.until(lambda v:v['kind'] == 'error')
        self.assertEqual(failure['code'], 'lease_superseded')
        self.assertTrue(self.lease.status()['owner_connected'])
        before = self.lease.expires
        second.send('heartbeat', nonce='owner-roundtrip')
        second.until(lambda v:v.get('nonce') == 'owner-roundtrip')
        self.assertGreater(self.lease.expires, before)
        second.send('release', nonce='release')
        second.until(lambda v:v['kind'] == 'released')
        self.assertFalse(self.lease.is_observing())
        reader.send('heartbeat', nonce='reader-after-release')
        reader.until(lambda v:v.get('nonce') == 'reader-after-release')
        self.assertFalse(self.lease.is_observing())

    def test_authorization_requires_management_credential_and_rejects_live_other_gui(self):
        owner = self.peer('owner')
        owner.until(lambda v:v['kind'] == 'hello')
        def authorize(record, token):
            peer = JSONPeer(self.path)
            self.addCleanup(peer.close)
            peer.send('authorize', session=record, control_token=token)
            return peer.until(lambda v:v['kind'] in ('error', 'authorized'))
        self.assertEqual(authorize(self.record, 'invalid')['code'], 'invalid_lease')
        other = {**self.record, 'owner_pid': 200, 'secret': 'n'*64}
        self.assertEqual(authorize(other, 'management-secret')['code'], 'owner_busy')
        owner.send('release')
        owner.until(lambda v:v['kind'] == 'released')
        self.assertEqual(authorize(other, 'management-secret')['kind'], 'authorized')
        invalid = self.peer('owner')
        self.assertEqual(invalid.until(lambda v:v['kind'] == 'error')['code'], 'invalid_lease')
        self.assertEqual(self.lease.status()['owner_pid'], 200)
