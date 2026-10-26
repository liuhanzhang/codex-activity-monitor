"""Query errors must not become partial JSON or a second offline full scan."""

import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from codex_activity_monitor.query_response import MAX_RESPONSE_BYTES, encode_response, read_response
from codex_activity_monitor.timeline_query import QueryServer, query_runtime


class QueryResponseTests(unittest.TestCase):
    def test_encoding_counts_utf8_and_returns_one_complete_bounded_frame(self):
        frame = encode_response({'text': '中文🙂'*100}, max_bytes=128)
        self.assertLessEqual(len(frame), 128)
        self.assertTrue(frame.endswith(b'\n'))
        self.assertEqual(json.loads(frame), {'version': 1, 'error': 'query_too_large'})
        value = {'text': 'raw\r\n中文🙂'*10}
        encoded = encode_response(value)
        self.assertEqual(encode_response(value, max_bytes=len(encoded)), encoded)
        self.assertEqual(json.loads(encode_response(value, max_bytes=len(encoded)-1))['error'], 'query_too_large')

    def test_invalid_or_truncated_responses_are_explicit(self):
        for frame in (b'{"version":1}', b'not json\n', b'[]\n', b'{"text":"\xff"}\n'):
            with self.subTest(frame=frame):
                self.assertEqual(read_response(io.BytesIO(frame))['error'], 'query_response_invalid')
        self.assertEqual(read_response(io.BytesIO(b''))['error'], 'query_unavailable')
        self.assertEqual(read_response(io.BytesIO(b'x'*(MAX_RESPONSE_BYTES+1)))['error'], 'query_too_large')

    def test_nonfinite_and_unsupported_values_do_not_escape_the_boundary(self):
        for value in ({'number': float('nan')}, {'value': object()}):
            with self.subTest(value=value):
                self.assertEqual(json.loads(encode_response(value))['error'], 'query_unavailable')


class QueryFallbackTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='cam-query-', dir='/tmp'); self.addCleanup(tmp.cleanup)
        self.runtime = Path(tmp.name)
        self.request = {'action': 'timeline', 'scope_id': str(self.runtime/'source'), 'thread_id': 't'}

    def peer(self, payload):
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(self.runtime/'tl.sock')); listener.listen(1)
        self.addCleanup(listener.close)
        def reply():
            with listener.accept()[0] as connection:
                connection.settimeout(2)
                connection.recv(32768)
                try: connection.sendall(payload)
                except (OSError, TimeoutError): pass
        thread = threading.Thread(target=reply); thread.start()
        self.addCleanup(lambda: thread.join(2))

    def test_bad_connected_peer_never_starts_an_offline_reader(self):
        for frame in (b'{"version":1}\nBROKEN', b'{"version":1', b'bad\n'):
            with self.subTest(frame=frame):
                (self.runtime/'tl.sock').unlink(missing_ok=True)
                self.peer(frame)
                with patch('codex_activity_monitor.timeline_query.TimelineQuery', side_effect=AssertionError('duplicate scan')):
                    result = query_runtime(self.runtime, self.request)
                # A complete first line is accepted; a later line is not part
                # of this single-response protocol. Other inputs are failures.
                self.assertEqual(result.get('error'), None if frame.endswith(b'BROKEN') else 'query_response_invalid')

    def test_oversized_peer_returns_a_small_error_without_offline_retry(self):
        self.peer(b'x'*(MAX_RESPONSE_BYTES+1))
        with patch('codex_activity_monitor.timeline_query.TimelineQuery', side_effect=AssertionError('duplicate scan')):
            self.assertEqual(query_runtime(self.runtime, self.request)['error'], 'query_too_large')

    def test_offline_fallback_requires_absent_collector_ownership(self):
        provider = Mock(); provider.query.return_value = {'version': 1, 'offline': True}
        with patch('codex_activity_monitor.timeline_query.collector_alive', return_value=True), \
                patch('codex_activity_monitor.timeline_query.TimelineQuery', side_effect=AssertionError('live runtime')):
            self.assertEqual(query_runtime(self.runtime, self.request)['error'], 'query_unavailable')
        with patch('codex_activity_monitor.timeline_query.collector_alive', return_value=False), \
                patch('codex_activity_monitor.timeline_query.TimelineQuery', return_value=provider):
            self.assertEqual(query_runtime(self.runtime, self.request), {'version': 1, 'offline': True})
        provider.query.assert_called_once()

    def test_server_and_cli_share_the_budget_and_force_utf8_protocol_output(self):
        query = Mock()
        query.query.return_value = {'version': 1, 'payload': 'x'*MAX_RESPONSE_BYTES}
        server = QueryServer(self.runtime/'tl.sock', query)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        thread.start()
        try:
            command = [sys.executable, '-m', 'codex_activity_monitor.cli', '--runtime', str(self.runtime),
                       'timeline-query', json.dumps(self.request)]
            env = dict(os.environ, PYTHONIOENCODING='ascii')
            oversized = subprocess.run(command, capture_output=True, env=env, timeout=5)
            self.assertEqual(oversized.returncode, 0, oversized.stderr)
            self.assertEqual(json.loads(oversized.stdout)['error'], 'query_too_large')
            self.assertLess(len(oversized.stdout), 100)
            query.query.return_value = {'version': 1, 'text': 'raw\r\n中文🙂'}
            valid = subprocess.run(command, capture_output=True, env=env, timeout=5)
            self.assertEqual(valid.returncode, 0, valid.stderr)
            self.assertEqual(json.loads(valid.stdout.decode('utf-8')), query.query.return_value)
            self.assertEqual(query.query.call_count, 2)
        finally:
            server.shutdown(); server.server_close(); thread.join(1)
