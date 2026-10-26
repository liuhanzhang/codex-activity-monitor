import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock

from codex_activity_monitor.bridge import Observer
from codex_activity_monitor.observer_permit import PermitExchange
from codex_activity_monitor.publication import encode_frame


class ObserverPermitTests(unittest.TestCase):
    def reply(self, exchange, now=100.1, **fields):
        return encode_frame({'protocol_version': 1, 'kind': 'observe_permit', 'instance_id': 'collector',
                             'nonce': exchange.pending[0], 'observing': True, 'ttl_ms': 1500,
                             'tick_age_ms': 10, **fields})

    def test_initial_handshake_is_independent_of_event_queue_and_fragmented(self):
        permit = PermitExchange()
        request = json.loads(permit.request('source', 100))
        self.assertEqual(request['control'], 'observe')
        self.assertEqual(permit.until, 0)
        raw = self.reply(permit)
        for byte in raw:
            permit.receive(bytes([byte]), 100.1)
        self.assertAlmostEqual(permit.until, 101.5)
        self.assertEqual(permit.instance, 'collector')

    def test_old_nonce_and_late_reply_cannot_extend_permission(self):
        permit = PermitExchange()
        permit.request('source', 100)
        permit.receive(self.reply(permit, nonce='old-nonce'), 100.1)
        self.assertEqual(permit.until, 0)
        with self.assertRaises(TimeoutError):
            permit.request('source', 100.75)
        with self.assertRaises(ValueError):
            permit.receive(self.reply(permit), 100.75)

    def test_invalid_or_stalled_collector_and_changed_instance_are_rejected(self):
        for fields in ({'protocol_version': True}, {'ttl_ms': 1501}, {'tick_age_ms': 1000},
                       {'ttl_ms': float('inf')}, {'observing': 1}, {'instance_id': []}):
            permit = PermitExchange(); permit.request('source', 100)
            # encode_frame itself forbids non-finite JSON, as the wire requires.
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                permit.receive(self.reply(permit, **fields), 100.1)
        permit = PermitExchange(); permit.request('source', 100)
        permit.receive(self.reply(permit), 100.1)
        permit.request('source', 100.6)
        with self.assertRaises(ValueError):
            permit.receive(self.reply(permit, instance_id='old-collector'), 100.7)

    def test_offer_only_checks_cached_permission_and_bounded_memory(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as directory:
            observer = Observer(Path(directory), 'source', '/synthetic', '/bin/echo')
            observer.permits.until = time.monotonic()+1
            with (patch('pathlib.Path.read_text', side_effect=AssertionError('disk read')),
                  patch('codex_activity_monitor.bridge.socket.socket', side_effect=AssertionError('socket work'))):
                observer.offer('stdout', b'authorized synthetic bytes')
                observer.permits.until = 0
                observer.offer('stdout', b'private paused bytes')
            self.assertEqual(observer.packets.qsize(), 1)
            self.assertEqual(observer.queued_bytes, len(b'authorized synthetic bytes'))
            self.assertEqual(observer.cache_bytes, 0)
            self.assertEqual(observer.drop_reasons['observation_inactive'], 1)

    def test_permission_gap_cannot_join_an_old_partial_frame_to_new_input(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as directory:
            observer = Observer(Path(directory), 'source', '/synthetic', '/bin/echo')
            observer.permits.until = time.monotonic()+60
            observer.offer('stdout', b'{"method":"turn/started","params":{"threadId":"old","turn":{"id":"')
            observer.permits.until = 0
            observer.offer('stdout', b'discarded"}}}\n')
            observer.permits.until = time.monotonic()+60
            observer.offer('stdout', encode_frame({'method': 'turn/started', 'params': {
                'threadId': 'new', 'turn': {'id': 'new-turn'}}}))
            sent = []
            observer.sock = MagicMock()
            observer.refresh_permit = lambda: None
            observer.send = lambda row: sent.append(row) or True
            observer.stopping = True
            observer.run()
            self.assertEqual(observer.errors, 0)
            turns = [row['event']['params']['threadId'] for row in sent
                     if row['event'].get('method') == 'turn/started']
            self.assertEqual(turns, ['new'])
