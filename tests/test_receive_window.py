"""Source receipt boundaries and finite timing metadata for the one-second rate."""

import unittest

from codex_activity_monitor.metrics import TextStream, TokenCounter


class ReceiveWindowTests(unittest.TestCase):
    def stream(self):
        return TextStream(('b', 'input', 'tool_input', 0), 'tool_input', 'upstream', 'unknown', 100)

    def test_source_boundary_between_collector_ticks(self):
        stream = self.stream()
        stream.append(' x'*25, 110.999)
        stream.append(' x'*50, 111.001)
        counter = TokenCounter(False)
        try:
            counter.tick([stream], 111.75)
            self.assertEqual(stream.rate(111.75), 75)
            counter.tick([stream], 112)
            self.assertEqual(stream.rate(112), 50)
            self.assertEqual(stream.window_baseline_chars, 50)
            counter.tick([stream], 112.001)
            self.assertEqual(stream.rate(112.001), 0)
        finally:
            counter.close()

    def test_same_batch_and_microsecond_gaps_never_extrapolate(self):
        stream = self.stream()
        for at in (110, 110, 110.000001):
            stream.append(' x', at)
        counter = TokenCounter(False)
        try:
            counter.tick([stream], 110.000002)
            self.assertEqual(stream.rate(110.000002), 3)
            self.assertEqual(len(stream.receipts), 2)
        finally:
            counter.close()

    def test_per_stream_receipt_budget_is_explicit_and_frees_metadata(self):
        stream = self.stream()
        for index in range(4097):
            stream.append('x', 110+index/10000)
        self.assertEqual(stream.quality, 'timing_limit')
        self.assertIsNone(stream.rate(110.5))
        self.assertFalse(stream.receipts)

    def test_global_budget_never_silently_drops_source_boundaries(self):
        streams = [self.stream(), self.stream()]
        for stream in streams:
            stream.append('x', 110)
            stream.append('x', 110.1)
        counter = TokenCounter(False, max_receipt_points=3)
        try:
            counter.tick(streams, 110.2)
            self.assertLessEqual(sum(len(stream.receipts) for stream in streams), 3)
            self.assertTrue(any(stream.quality == 'timing_limit' for stream in streams))
        finally:
            counter.close()
