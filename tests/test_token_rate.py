"""Fixed one-second receive counts, including startup and BPE boundaries."""

from concurrent.futures import Future
import unittest

from codex_activity_monitor.metrics import TextStream, TokenCounter, count_text
from codex_activity_monitor.engine import Engine
from codex_activity_monitor.events import Event


class ManualCounter:
    def __init__(self):
        self.jobs = []

    def submit(self, function, *args):
        future = Future()
        self.jobs.append((future, function, args))
        return future

    def finish_next(self):
        future, function, args = self.jobs.pop(0)
        count, _, baseline = function(*args)
        future.set_result((count, 0, baseline))

    def shutdown(self, **_):
        for future, _, _ in self.jobs:
            future.cancel()


class TokenRateTests(unittest.TestCase):
    def stream(self, kind='tool_input', started=100):
        return TextStream(('b', 'item', kind, 0), kind, 'upstream', 'unknown', started)

    def measured_append(self, stream, text, at):
        stream.append(text, at)
        counter = TokenCounter(False)
        try:
            counter.tick([stream], at)
        finally:
            counter.close()

    def test_fixed_denominator_ignores_pre_text_wait_and_does_not_extrapolate_startup(self):
        # One x plus its leading space is one token in this explicit encoding.
        self.assertEqual(count_text(' x' * 25, 'o200k_base')[0], 25)
        for delay in (0, 8, 60):
            with self.subTest(first_text_delay=delay):
                stream = self.stream()
                for batch in range(1, 25):
                    at = 100 + delay + batch / 4
                    self.measured_append(stream, ' x' * 25, at)
                    self.assertEqual(stream.rate(at), min(batch, 4)*25)

    def test_first_large_batch_is_counted_without_dividing_by_a_short_interval(self):
        stream = self.stream()
        self.measured_append(stream, ' x' * 800, 110)
        self.assertEqual(stream.rate(110.1), 800)
        self.measured_append(stream, ' x' * 25, 110.25)
        self.assertEqual(stream.rate(110.25), 825)

    def test_rates_above_and_below_100_are_not_scaled_or_clamped(self):
        for tokens_per_batch in (10, 40):
            with self.subTest(tokens_per_batch=tokens_per_batch):
                stream = self.stream()
                for batch in range(1, 17):
                    at = 110 + batch / 4
                    self.measured_append(stream, ' x' * tokens_per_batch, at)
                    self.assertEqual(stream.rate(at), min(batch, 4)*tokens_per_batch)

    def test_prefix_merges_are_normal_resampling_not_a_counting_failure(self):
        stream = self.stream()
        self.measured_append(stream, 'abcde', 110)
        self.assertEqual(stream.tokens, 2)
        self.measured_append(stream, 'f', 110.25)
        self.assertEqual(stream.tokens, 1)
        self.assertEqual(stream.rate(110.25), 1)
        counter = TokenCounter(False)
        try:
            counter.tick([stream], 111)
        finally:
            counter.close()
        self.assertEqual(stream.window_tokens, -1)
        self.assertIsNone(stream.rate(111))
        self.assertEqual(stream.rate_reading(111).reason, 'count_correction')

    def test_delayed_count_uses_matching_count_endpoints(self):
        stream = self.stream()
        self.measured_append(stream, ' x' * 25, 110)
        self.measured_append(stream, ' x' * 50, 110.5)
        stream.append(' x' * 25, 110.75)  # Received, not counted yet.
        # Latest completed full-second window contains 75 tokens. Waiting for
        # the next count never changes its fixed one-second denominator.
        self.assertEqual(stream.rate(111), 75)
        stream.append(' x', 111.6)
        self.assertIsNone(stream.rate(111.6))
        self.assertEqual(stream.rate(112.6), 0)

    def test_async_count_results_keep_their_prefix_capture_time(self):
        stream = self.stream()
        counter = TokenCounter(isolated=False)
        counter.pool.shutdown(wait=True)
        counter.pool = pool = ManualCounter()
        self.addCleanup(counter.close)
        stream.append(' x' * 25, 110)
        counter.tick([stream], 110)
        for at in (110.25, 110.5, 110.75, 111):
            stream.append(' x' * 25, at)
        pool.finish_next()
        counter.tick([stream], 111)
        stream.append(' x' * 25, 111.25)
        pool.finish_next()
        counter.tick([stream], 111.25)
        self.assertEqual(stream.tokens, 125)
        self.assertAlmostEqual(stream.rate(111.25), 100)

    def test_model_recount_does_not_treat_the_old_prefix_as_new_tokens(self):
        stream = self.stream()
        self.measured_append(stream, ' x' * 1000, 101)
        self.measured_append(stream, ' x' * 25, 101.25)
        stream.set_model('gpt-4')
        count, _ = count_text(stream.text, stream.encoding_name)
        stream.apply_count(count, stream.revision, 101.5, 0)
        self.assertEqual(stream.rate(101.5), 1025)
        self.measured_append(stream, ' x' * 25, 102.5)
        self.assertEqual(stream.rate(102.5), 25)

    def test_idle_ticks_supply_zero_growth_without_recounting(self):
        stream = self.stream()
        counter = TokenCounter(False)
        self.addCleanup(counter.close)
        self.measured_append(stream, ' x' * 25, 110)
        counter.tick([stream], 110.25)
        self.assertEqual(stream.rate(110.25), 25)
        self.assertEqual(stream.rate(112), 0)

    def test_budget_gap_and_completion_keep_rate_unknown(self):
        stream = self.stream()
        self.measured_append(stream, ' x' * 25, 110)
        self.measured_append(stream, ' x' * 25, 110.25)
        self.assertEqual(stream.rate(110.25), 50)
        stream.count_state = 'budget'
        self.assertIsNone(stream.rate(110.25))
        stream.count_state = 'ready'
        stream.quality = 'observation_gap'
        self.assertIsNone(stream.rate(110.25))
        stream.quality = 'complete'
        stream.ended_at = 110.5
        self.assertIsNone(stream.rate(110.5))

    def test_single_chat_tool_arguments_do_not_multiply_the_input_rate(self):
        engine = Engine(asynchronous_counts=False)
        self.addCleanup(engine.close)
        engine.ingest(Event('b', 'protocol_out', {'method': 'turn/started', 'params': {
            'threadId': 't', 'turn': {'id': 'u', 'status': 'inProgress'}}}, 100, 0))
        engine.ingest(Event('b', 'upstream', {'type': 'response.output_item.added', 'item': {
            'id': 'input', 'type': 'custom_tool_call', 'name': 'exec',
            'metadata': {'turn_id': 'u'}}}, 101, 0))
        count_text('warmup', 'o200k_base')
        for batch in range(1, 25):
            at = 110 + batch / 4
            engine.ingest(Event('b', 'upstream', {'type': 'response.custom_tool_call_input.delta',
                'item_id': 'input', 'delta': ' x' * 25, 'sequence_number': batch}, at, 0))
            engine.tick(at)
            snapshot = engine.snapshot(at)
            chat = snapshot['threads'][0]
            self.assertEqual(chat['phase'], 'tool_input')
            self.assertEqual(chat['generation_rate'], min(batch, 4)*25)
            self.assertEqual(snapshot['overview']['generation_rate'], min(batch, 4)*25)


if __name__ == '__main__':
    unittest.main()
