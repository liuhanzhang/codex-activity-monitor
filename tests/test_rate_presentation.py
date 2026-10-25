"""Normal speed sampling stays distinct from unavailable observations."""

from concurrent.futures import Future
import unittest
from unittest.mock import MagicMock

from codex_progress_monitor.engine import Activity, Engine
from codex_progress_monitor.events import Event
from codex_progress_monitor.metrics import TextStream, TokenCounter, count_text


class RatePresentationTests(unittest.TestCase):
    def setUp(self):
        self.engine = Engine(asynchronous_counts=False)
        self.addCleanup(lambda: self.engine.close())
        self.engine.backends['b'] = {'connected': True, 'home': '/synthetic'}

    def stream(self, name='input', thread_id='chat'):
        thread = self.engine._thread('b', thread_id)
        thread.status, thread.turn_id = 'inProgress', 'turn'
        key = ('b', name, 'tool_input', 0)
        stream = TextStream(key, 'tool_input', 'upstream', 'unknown', 100)
        stream.append(' x' * 25, 101)
        self.engine.streams[key] = stream
        thread.activities[name] = Activity(name, 'turn', 'tool_input', '', 100, stream_keys=[key])
        return stream

    def count(self, stream, at):
        stream.apply_count(count_text(stream.text, stream.encoding_name)[0], stream.revision, at, 0)

    def ready(self, stream):
        self.count(stream, 101)
        stream.append(' x' * 75, 101.25)
        self.count(stream, 101.25)

    def test_first_count_immediately_provides_a_fixed_window_number(self):
        stream = self.stream()
        for counted in (False, True):
            if counted:
                self.count(stream, 101)
            value = self.engine.snapshot(101.1)
            self.assertEqual(value['overview']['rate_status'], 'ready' if counted else 'sampling')
            self.assertEqual(value['overview']['generation_rate'], 25 if counted else None)
            self.assertEqual(value['menu_presentation']['reason'], 'generating' if counted else 'sampling')
            self.assertEqual(value['menu_presentation']['mode'], 'speed')
            self.assertEqual(value['threads'][0]['generation_rate_status'], 'ready' if counted else 'sampling')

    def test_explicit_generation_waiting_for_its_first_delta_is_sampling(self):
        stream = self.stream()
        thread = self.engine.threads[('b', 'chat')]
        thread.activities['input'].stream_keys.clear()
        del self.engine.streams[stream.key]
        value = self.engine.snapshot(101.1)
        self.assertEqual(value['threads'][0]['generation_rate_status'], 'sampling')
        self.assertEqual(value['overview']['rate_status'], 'sampling')
        self.assertEqual(value['menu_presentation']['reason'], 'sampling')

    def test_sampling_transitions_to_real_rate_and_never_resurrects_after_completion(self):
        stream = self.stream()
        self.ready(stream)
        value = self.engine.snapshot(101.25)
        self.assertEqual(value['overview']['rate_status'], 'ready')
        self.assertAlmostEqual(value['threads'][0]['generation_rate'], 100)
        stream.ended_at = 101.3
        self.engine.threads[('b', 'chat')].activities['input'].ended_at = 101.3
        self.engine.threads[('b', 'chat')].status = 'completed'
        value = self.engine.snapshot(101.4)
        self.assertEqual(value['menu_presentation']['reason'], 'idle')
        self.assertEqual(value['threads'][0]['generation_rate_status'], 'inactive')
        self.assertIsNone(value['threads'][0]['generation_rate'])

    def test_new_stream_waits_for_its_own_samples(self):
        stream = self.stream()
        self.ready(stream)
        stream.ended_at = 101.3
        self.engine.threads[('b', 'chat')].activities['input'].ended_at = 101.3
        self.stream('new-input')
        value = self.engine.snapshot(101.4)
        self.assertEqual(value['threads'][0]['generation_rate_status'], 'sampling')
        self.assertIsNone(value['threads'][0]['generation_rate'])

    def test_queued_recount_keeps_fresh_rate_but_cannot_make_stale_windows_current(self):
        stream = self.stream()
        self.ready(stream)
        stream.append(' x', 101.5)
        self.assertAlmostEqual(stream.rate(101.5), 100)
        stream.append(' x', 102.3)
        value = self.engine.snapshot(102.3)
        self.assertEqual(value['overview']['rate_status'], 'unavailable')
        self.assertEqual(value['menu_presentation']['mode'], 'unavailable')

    def test_temporary_executor_pressure_is_pending_not_a_hard_limit(self):
        stream = self.stream()
        self.ready(stream)
        stream.append(' x', 101.5)
        busy = TextStream(('b', 'busy', 'text', 0), 'text', 'upstream', 'unknown', 100)
        busy.append('busy', 101)
        counter = TokenCounter(max_jobs=1, isolated=False)
        counter.pool.shutdown(wait=True)
        future = Future()
        counter.pool = MagicMock()
        counter.pool.submit.return_value = future
        try:
            counter.tick([busy], 101.5)
            counter.tick([stream], 101.5)
            self.assertEqual(stream.count_state, 'pending')
            self.assertAlmostEqual(stream.rate(101.5), 100)
            self.assertEqual(counter.diagnostics()['jobs'], 1)
        finally:
            future.cancel()
            counter.close()

    def test_errors_and_missing_identity_never_become_sampling(self):
        for cause in ('observation_gap', 'tokenizer_error', 'budget', 'disconnected', 'unassociated'):
            with self.subTest(cause=cause):
                self.engine.close()
                self.engine = Engine(asynchronous_counts=False)
                self.engine.backends['b'] = {'connected': True, 'home': '/synthetic'}
                stream = self.stream()
                if cause in ('observation_gap', 'tokenizer_error'):
                    stream.quality = cause
                elif cause == 'budget':
                    stream.count_state = 'budget'
                elif cause == 'disconnected':
                    self.engine.backends['b']['connected'] = False
                else:
                    self.engine.ingest(Event('b', 'upstream', {
                        'type': 'response.output_text.delta', 'item_id': 'unowned', 'delta': 'x'}, 101, 0))
                value = self.engine.snapshot(101.1)
                self.assertEqual(value['overview']['rate_status'], 'unavailable')
                self.assertEqual(value['menu_presentation']['mode'], 'unavailable')

    def test_one_sampling_chat_does_not_expose_the_other_chat_subtotal_as_total(self):
        known = self.stream()
        self.ready(known)
        self.stream('second', 'other-chat')
        value = self.engine.snapshot(101.3)
        self.assertEqual(value['overview']['rate_status'], 'sampling')
        self.assertEqual(value['overview']['coverage'], 'partial')
        self.assertIsNone(value['overview']['generation_rate'])
        self.assertAlmostEqual(value['overview']['observed_subtotal'], 100)
        self.assertEqual(value['menu_presentation']['reason'], 'sampling')

    def test_error_in_one_chat_overrides_normal_sampling_in_another(self):
        self.stream()
        bad = self.stream('second', 'other-chat')
        bad.fail_count()
        value = self.engine.snapshot(101.2)
        self.assertEqual(value['overview']['rate_status'], 'unavailable')
        self.assertEqual(value['menu_presentation']['mode'], 'unavailable')
