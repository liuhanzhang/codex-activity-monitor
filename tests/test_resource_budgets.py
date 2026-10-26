import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from codex_activity_monitor.engine import Engine
from codex_activity_monitor.events import Event, filter_event
from codex_activity_monitor.history import HistoryProvider
from codex_activity_monitor.metrics import TextStream, TokenCounter, TokenizationError, count_text
from codex_activity_monitor.state import Activity
from codex_activity_monitor.identity_budget import IdentityBudget
from codex_activity_monitor.timeline import TimelineBook


class ResourceBudgetTests(unittest.TestCase):
    def test_oversized_count_prefix_never_starts_a_native_worker(self):
        stream = TextStream(('b', 'large', 'text', 0), 'text', 'upstream', 'unknown', 1)
        stream.append('word', 2); stream.apply_count(1, stream.revision, 2, 0)
        stream.append('x'*1048576, 3)
        counter = TokenCounter()
        try:
            counter.tick([stream], 3)
            self.assertEqual(stream.count_state, 'budget')
            self.assertIsNone(stream.rate(3))
            self.assertEqual(stream.tokens, 1)
            stream.ended_at = 4
            self.assertIsNone(stream.view(4)['mean_received_rate'])
            self.assertEqual(counter.diagnostics()['jobs'], 0)
            self.assertEqual(counter.diagnostics()['worker']['starts'], 0)
        finally:
            counter.close()

    def test_native_tokenizer_panic_is_a_count_failure_and_other_work_continues(self):
        class NativePanic(BaseException): pass
        broken = Mock()
        broken.encode_ordinary.side_effect = NativePanic('synthetic native failure')
        for asynchronous in (False, True):
            with self.subTest(asynchronous=asynchronous):
                stream = TextStream(('b', 'bad', 'text', 0), 'text', 'upstream', 'unknown', 1)
                stream.append('input', 2)
                counter = TokenCounter(asynchronous, isolated=False)
                try:
                    with patch('codex_activity_monitor.metrics.tiktoken.get_encoding', return_value=broken):
                        counter.tick([stream], 2)
                        if asynchronous:
                            deadline = time.monotonic()+1
                            while stream.pending is not None and time.monotonic() < deadline:
                                counter.tick([stream], 2); time.sleep(.001)
                    self.assertEqual(stream.count_state, 'failed')
                    self.assertIsNone(stream.rate(2))
                    self.assertEqual(stream.text, '')
                    good = TextStream(('b', 'good', 'text', 0), 'text', 'upstream', 'unknown', 1)
                    good.append('word', 3)
                    counter.tick([stream, good], 3)
                    deadline = time.monotonic()+1
                    while good.pending is not None and time.monotonic() < deadline:
                        counter.tick([stream, good], 3); time.sleep(.001)
                    self.assertEqual(good.tokens, 1)
                finally:
                    counter.close()

    def test_count_failure_adapter_preserves_interpreter_control_signals(self):
        for signal in (KeyboardInterrupt, SystemExit, GeneratorExit):
            encoding = Mock(); encoding.encode_ordinary.side_effect = signal
            with patch('codex_activity_monitor.metrics.tiktoken.get_encoding', return_value=encoding):
                with self.assertRaises(signal): count_text('input', 'o200k_base')
        encoding = Mock(); encoding.encode_ordinary.side_effect = ValueError('synthetic')
        with patch('codex_activity_monitor.metrics.tiktoken.get_encoding', return_value=encoding):
            with self.assertRaises(TokenizationError): count_text('input', 'o200k_base')

    def test_history_diagnostics_never_wait_for_a_blocked_source_reader(self):
        provider = HistoryProvider()
        entered, release = threading.Event(), threading.Event()
        def reader():
            with provider.lock:
                entered.set(); release.wait(2)
        worker = threading.Thread(target=reader); worker.start()
        try:
            self.assertTrue(entered.wait(1))
            begin = time.monotonic(); stats = provider.diagnostics()
            self.assertLess(time.monotonic()-begin, .1)
            self.assertEqual(stats['indices'], 0)
        finally:
            release.set(); worker.join(1)
    def test_counter_bounds_work_across_model_changes_and_services_other_streams(self):
        streams = [TextStream(('b', str(i), 'text', 0), 'text', 'upstream', 'unknown', 1) for i in range(3)]
        for stream in streams: stream.append('word', 2)
        gate = threading.Event()
        counter = TokenCounter(max_jobs=2, max_pending_bytes=8, isolated=False)
        self.addCleanup(counter.close)
        self.addCleanup(gate.set)
        def blocked(text, encoding):
            gate.wait(2)
            return len(text), .01
        with patch('codex_activity_monitor.metrics.count_text', blocked):
            counter.tick(streams, 2)
            self.assertEqual(counter.diagnostics()['jobs'], 2)
            self.assertEqual(counter.diagnostics()['pending_bytes'], 8)
            for stream in streams: stream.set_model('gpt-4')
            counter.tick(streams, 2.1)
            self.assertEqual(counter.diagnostics()['jobs'], 2)  # Old work is still charged.
            self.assertTrue(all(s.last_count_at is None for s in streams))
            gate.set()
            deadline = time.monotonic()+2
            while any(s.counted_revision != s.revision for s in streams) and time.monotonic() < deadline:
                counter.tick(streams, 3)
                time.sleep(.001)
            self.assertTrue(all(s.counted_revision == s.revision and s.tokens == 4 for s in streams))
            self.assertLessEqual(counter.diagnostics()['peak_jobs'], 2)
            self.assertLessEqual(counter.diagnostics()['peak_pending_bytes'], 8)

    def test_count_byte_budget_does_not_reuse_an_old_rate_or_block_small_work(self):
        large = TextStream(('b', 'large', 'text', 0), 'text', 'upstream', 'unknown', 1)
        large.append('four', 2); large.apply_count(1, large.revision, 2, 0)
        large.append('more', 3)
        small = TextStream(('b', 'small', 'text', 0), 'text', 'upstream', 'unknown', 1)
        small.append('x', 3)
        counter = TokenCounter(max_pending_bytes=4)
        try:
            counter.tick([large, small], 3)
            self.assertEqual(large.count_state, 'budget')
            self.assertIsNone(large.rate(3))
            deadline = time.monotonic()+2
            while small.last_count_at is None and time.monotonic() < deadline:
                counter.tick([large, small], 3); time.sleep(.001)
            self.assertEqual(small.tokens, 1)
            self.assertEqual(large.tokens, 1)  # Previously counted cumulative value stays identifiable.
        finally: counter.close()

    def test_selected_history_is_detached_and_an_oversize_index_remains_queryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp); (home/'sessions').mkdir()
            rows = [{'type': 'session_meta', 'payload': {'id': 't'}}]
            for turn in ('u', 'v'):
                rows += [{'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': turn}},
                         {'type': 'event_msg', 'payload': {'type': 'item_completed', 'turn_id': turn,
                          'item': {'id': turn, 'type': 'AgentMessage', 'text': 'raw'}}}]
            (home/'sessions'/'fixture-t.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
            provider = HistoryProvider()
            selected = provider.selected_view(tmp, 't', 'u')
            self.assertEqual({p['turn_id'] for p in selected.phases.values()}, {'u'})
            next(iter(selected.phases.values()))['label'] = 'changed'
            self.assertNotEqual(next(iter(provider.selected_view(tmp, 't', 'u').phases.values()))['label'], 'changed')
            limited = HistoryProvider(max_cache_bytes=1)
            for _ in range(2):
                self.assertEqual(len(limited.selected_view(tmp, 't', 'v').phases), 1)
                self.assertEqual(limited.diagnostics()['cache_bytes'], 0)
                self.assertEqual(limited.diagnostics()['indices'], 0)
            self.assertGreaterEqual(limited.diagnostics()['evictions'], 2)

    def test_weight_budget_evicts_the_least_recent_index_and_rebuilds_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp); (home/'sessions').mkdir()
            for thread in ('a', 'b', 'c'):
                rows = [{'type': 'session_meta', 'payload': {'id': thread}},
                        {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'u'}}]
                (home/'sessions'/f'fixture-{thread}.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
            provider = HistoryProvider()
            provider.index(tmp, 'a')
            weight = provider.diagnostics()['cache_bytes']
            provider.max_cache_bytes = weight*2+32
            provider.index(tmp, 'b'); provider.index(tmp, 'a'); provider.index(tmp, 'c')
            self.assertIsNotNone(provider.cached_path(tmp, 'a'))
            self.assertIsNone(provider.cached_path(tmp, 'b'))
            self.assertLessEqual(provider.diagnostics()['cache_bytes'], provider.max_cache_bytes)
            self.assertIsNotNone(provider.selected_view(tmp, 'b', 'u'))
            self.assertLessEqual(provider.diagnostics()['cache_bytes'], provider.max_cache_bytes)


class TerminalBudgetTests(unittest.TestCase):
    def setUp(self):
        self.book = TimelineBook()
        self.engine = Engine(False, self.book)
        self.addCleanup(self.engine.close)

    def send(self, value, at=100, direction='protocol_out', backend='b'):
        event = filter_event(direction, value)
        if event is not None: self.engine.ingest(Event(backend, direction, event, at, 0))

    def turn(self, turn='u', at=100):
        self.send({'method': 'turn/started', 'params': {'threadId': 't', 'turn': {'id': turn, 'status': 'inProgress'}}}, at)

    def item(self, identity, kind, at=101):
        self.send({'type': 'response.output_item.'+kind, 'item': {'id': identity, 'type': 'message',
                   'metadata': {'turn_id': 'u'}, 'content': [{'type': 'output_text', 'text': 'raw'}]}}, at, 'upstream')

    def test_evicted_terminal_item_cannot_reopen_inside_the_same_long_turn(self):
        self.turn()
        for number in range(25):
            self.item(str(number), 'added', 101+number)
            self.item(str(number), 'done', 101.1+number)
        self.engine.tick(130)
        self.assertNotIn(('b', '0'), self.engine.items)
        for kind in ('added', 'done'): self.item('0', kind, 131)
        self.engine.tick(132)
        self.assertNotIn('0', self.engine.threads[('b', 't')].activities)
        self.assertGreater(self.engine.counts['late_terminal_items'], 0)

    def test_pruned_turn_and_backend_do_not_accept_late_events(self):
        self.turn()
        self.send({'method': 'turn/completed', 'params': {'threadId': 't', 'turn': {'id': 'u', 'status': 'completed'}}}, 101)
        self.engine.tick(1000)
        self.assertNotIn(('b', 'u'), self.engine.turns)
        self.turn(at=1001)
        self.assertNotIn(('b', 'u'), self.engine.turns)
        self.engine.ingest(Event('b', 'lifecycle', {'type': 'bridge/stopped'}, 1002, 0))
        self.engine.tick(2000)
        self.assertNotIn('b', self.engine.backends)
        self.turn('new-old-backend', 2001)
        self.assertNotIn('b', self.engine.backends)

    def test_disconnected_backend_can_reconnect_after_display_retention_expires(self):
        self.turn()
        self.send({'method': 'turn/completed', 'params': {'threadId': 't', 'turn': {'id': 'u', 'status': 'completed'}}}, 101)
        self.engine.ingest(Event('b', 'lifecycle', {'type': 'bridge/disconnected'}, 102, 0))
        self.engine.tick(1000)
        self.assertNotIn('b', self.engine.backends)
        self.assertFalse(self.engine.identity_budget.contains('backend', 'b', 'b'))
        self.engine.ingest(Event('b', 'lifecycle', {'type': 'bridge/started', 'home': '/synthetic'}, 1001, 0))
        self.engine.ingest(Event('b', 'lifecycle', {'type': 'bridge/reconnected'}, 1001.1, 0))
        self.turn('fresh', 1002)
        self.assertEqual(self.engine.threads[('b', 't')].turn_id, 'fresh')
        self.assertEqual(self.engine.backends['b']['home'], '/synthetic')
        self.turn('u', 1003)  # The old terminal turn still cannot reopen.
        self.assertEqual(self.engine.threads[('b', 't')].turn_id, 'fresh')

    def test_exhaustion_closes_observation_without_forgetting_terminal_ids(self):
        self.engine.identity_budget = IdentityBudget(capacity=4)
        self.turn(); self.item('i', 'added'); self.item('i', 'done')
        self.send({'method': 'turn/completed', 'params': {'threadId': 't', 'turn': {'id': 'u', 'status': 'completed'}}}, 102)
        self.engine.tick(103)
        self.turn('unadmitted', 104); self.item('new', 'added', 105)
        diagnostics = self.engine.snapshot(105)['resource_diagnostics']['identity_budget']
        self.assertEqual(diagnostics['entries'], 4)
        self.assertEqual(diagnostics['terminal_entries'], 2)
        self.assertTrue(diagnostics['admission_closed'])
        self.assertIn('identity_budget', self.engine.snapshot(105)['overview']['quality'])
        self.assertIsNone(self.engine.snapshot(105)['overview']['generation_rate'])
        self.assertEqual(self.engine.snapshot(105)['menu_presentation']['mode'], 'unavailable')
        self.assertNotIn(('b', 'unadmitted'), self.engine.turns)
        self.assertNotIn(('b', 'new'), self.engine.items)
        self.assertIn('identity_budget', self.engine.threads[('b', 't')].quality)
        self.assertFalse(any(p['status'] == 'active' for p in self.book.phases.values()))

    def test_identity_byte_exhaustion_and_malformed_identity_are_explicit(self):
        self.engine.identity_budget = IdentityBudget(max_identity_bytes=4)
        self.turn()  # Three one-byte identities.
        self.item('i', 'added')
        self.item('long', 'added', 102)
        self.assertTrue(self.engine.identity_budget.sealed)
        self.assertEqual(self.engine.identity_budget.identity_bytes, 4)
        self.assertNotIn(('b', 'long'), self.engine.items)
        other = Engine(False)
        try:
            other.ingest(Event('b', 'protocol_out', {'method': 'thread/started', 'params': {'thread': {'id': ['bad']}}}, 1, 0))
            self.assertFalse(other.threads)
            self.assertEqual(other.counts['invalid_observation_identity'], 1)
        finally: other.close()

    def test_global_text_reclamation_uses_utf8_bytes_for_its_low_water_mark(self):
        self.turn()
        thread = self.engine.threads[('b', 't')]
        for number in range(9):
            identity = str(number); key = ('b', identity, 'text', 0)
            activity = Activity(identity, 'u', 'text', 'text', 101)
            activity.stream_keys.append(key); thread.activities[identity] = activity
            stream = TextStream(key, 'text', 'upstream', 'unknown', 101)
            stream.append('中'*(4*1024*1024//3), 102); stream.quality = 'partial'
            self.engine.streams[key] = stream
        self.engine.tick(103)
        self.assertLessEqual(sum(len(s.text.encode()) for s in self.engine.streams.values()), 16*1024*1024)
        self.assertTrue(any(s.quality == 'memory_limit' for s in self.engine.streams.values()))
