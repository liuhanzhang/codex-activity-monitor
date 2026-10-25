"""Current metrics recover independently of retained historical observation gaps."""

from concurrent.futures import Future
from pathlib import Path
import unittest
from unittest.mock import MagicMock

from codex_progress_monitor.bridge import Observer, unrelated_diagnostic_prefix
from codex_progress_monitor.engine import Engine
from codex_progress_monitor.events import Event, filter_event
from codex_progress_monitor.metrics import TextStream, TokenCounter


class LiveSpeedRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.engine = Engine(False)
        self.addCleanup(self.engine.close)
        self.send({'type': 'bridge/started'}, 100, 'lifecycle')
        self.send({'method': 'turn/started', 'params': {'threadId': 'chat', 'turn': {'id': 'turn'}}}, 100)
        self.item('old', 101)
        self.delta('old', ' x' * 80, 101.1)
        self.engine.tick(101.2)

    def send(self, data, at, direction='protocol_out', replayed=False):
        safe = filter_event(direction, data)
        if safe is not None:
            self.engine.ingest(Event('b', direction, safe, at, at+1000, replayed))

    def item(self, name, at):
        self.send({'type': 'response.output_item.added', 'item': {
            'id': name, 'type': 'message', 'metadata': {'turn_id': 'turn'}}}, at, 'upstream')

    def delta(self, name, text, at, replayed=False):
        self.send({'type': 'response.output_text.delta', 'item_id': name, 'delta': text}, at, 'upstream', replayed)

    def gap(self, at=102):
        self.send({'type': 'bridge/gap', 'reason': 'synthetic_loss'}, at, 'lifecycle')

    def snapshot(self, at):
        self.engine.tick(at)
        return self.engine.snapshot(at, at+1000)

    def test_same_stream_requires_a_new_full_window_and_keeps_historical_gap(self):
        self.gap()
        self.assertIsNone(self.snapshot(102.1)['overview']['observed_subtotal'])
        self.delta('old', ' y' * 20, 103)
        self.assertIsNone(self.snapshot(103.1)['overview']['observed_subtotal'])
        self.delta('old', ' z' * 30, 103.8)
        value = self.snapshot(104.1)
        self.assertEqual(value['overview']['generation_rate'], 30)
        self.assertEqual(value['overview']['coverage'], 'complete')
        chat = value['threads'][0]
        self.assertIn('observation_gap', chat['quality'])
        self.assertEqual(chat['generation_rate_issues'], [])
        self.assertEqual(chat['generation_rate'], 30)
        stream = self.engine.streams[('b', 'old', 'text', 0)]
        self.assertIsNone(stream.view(104.1)['tokens'])  # Suffix counts are not full-item counts.
        self.gap(104.2)
        self.assertIsNone(self.snapshot(110)['overview']['observed_subtotal'])

    def test_new_stream_after_gap_contributes_while_old_stream_is_unknown(self):
        self.gap()
        self.item('new', 103)
        self.delta('new', ' x' * 25, 103.1)
        value = self.snapshot(103.2)
        self.assertEqual(value['overview']['observed_subtotal'], 25)
        self.assertIsNone(value['overview']['generation_rate'])
        self.assertEqual(value['overview']['coverage'], 'partial')
        self.assertEqual(value['menu_presentation']['reason'], 'partial_observation')
        self.assertEqual(value['threads'][0]['generation_rate'], 25)
        self.assertIn('observation_gap', value['threads'][0]['generation_rate_issues'])
        self.send({'type': 'response.output_item.done', 'item': {
            'id': 'old', 'type': 'message', 'metadata': {'turn_id': 'turn'},
            'content': [{'type': 'output_text', 'text': ' x' * 80}]}}, 103.3, 'upstream')
        value = self.snapshot(103.4)
        self.assertEqual(value['overview']['coverage'], 'complete')
        self.assertEqual(value['threads'][0]['generation_rate_issues'], [])
        self.assertIn('observation_gap', value['threads'][0]['quality'])

    def test_elapsed_time_or_replayed_text_cannot_recover_an_interrupted_stream(self):
        self.gap()
        self.delta('old', 'replayed', 103, replayed=True)
        self.assertIsNone(self.snapshot(110)['overview']['observed_subtotal'])

    def test_partial_zero_does_not_become_global_idle(self):
        self.gap()
        self.item('new', 103)
        self.delta('new', ' x', 103.1)
        self.snapshot(103.2)
        value = self.snapshot(105)
        self.assertEqual(value['overview']['observed_subtotal'], 0)
        self.assertEqual(value['overview']['coverage'], 'partial')
        self.assertEqual(value['menu_presentation']['reason'], 'partial_observation')

    def test_failure_in_another_chat_does_not_hide_the_healthy_chat_or_mark_it_abnormal(self):
        self.send({'method': 'turn/started', 'params': {'threadId': 'other', 'turn': {'id': 'other-turn'}}}, 101.3)
        self.send({'type': 'response.output_item.added', 'item': {
            'id': 'bad', 'type': 'message', 'metadata': {'turn_id': 'other-turn'}}}, 101.3, 'upstream')
        self.delta('bad', ' x', 101.3)
        self.engine.streams[('b', 'bad', 'text', 0)].fail_count()
        value = self.snapshot(101.4)
        chats = {t['id']: t for t in value['threads']}
        self.assertEqual(value['overview']['observed_subtotal'], 80)
        self.assertEqual(value['menu_presentation']['mode'], 'speed')
        self.assertEqual(chats['chat']['generation_rate_issues'], [])
        self.assertEqual(chats['other']['generation_rate_issues'], ['tokenizer_error'])

    def test_authoritative_final_text_can_restore_the_full_count_but_not_receive_history(self):
        self.gap()
        self.delta('old', ' x' * 10, 103)
        self.snapshot(103.1)
        self.delta('old', ' x' * 10, 103.8)
        self.snapshot(104.1)
        self.send({'type': 'response.output_item.done', 'item': {
            'id': 'old', 'type': 'message', 'metadata': {'turn_id': 'turn'},
            'content': [{'type': 'output_text', 'text': ' x' * 100}]}}, 104.2, 'upstream')
        self.snapshot(104.5)
        value = self.engine.streams[('b', 'old', 'text', 0)].view(104.5)
        self.assertEqual(value['tokens'], 100)
        self.assertFalse(value['received_complete'])
        self.assertIsNone(value['mean_received_rate'])

    def test_disconnect_invalidates_the_prefix_and_reconnect_cannot_resurrect_old_counts(self):
        self.send({'type': 'bridge/disconnected'}, 102, 'lifecycle')
        self.assertIsNone(self.snapshot(102.1)['overview']['observed_subtotal'])
        self.send({'type': 'bridge/started'}, 103, 'lifecycle')
        self.send({'type': 'bridge/reconnected'}, 103.1, 'lifecycle')
        self.delta('old', ' y' * 20, 104)
        self.assertIsNone(self.snapshot(104.1)['overview']['observed_subtotal'])
        self.delta('old', ' z' * 10, 104.8)
        self.assertEqual(self.snapshot(105.1)['overview']['observed_subtotal'], 10)

    def test_irrelevant_drop_is_recorded_without_a_gap_and_legacy_drop_stays_conservative(self):
        self.send({'type': 'bridge/heartbeat', 'dropped_packets': 1, 'unrelated_dropped_packets': 1,
                   'drop_reasons': {'oversized_unrelated_diagnostic': 1}, 'parse_errors': 0}, 101.3, 'lifecycle')
        value = self.snapshot(101.4)
        self.assertEqual(value['overview']['observed_subtotal'], 80)
        self.assertNotIn('observation_gap', value['threads'][0]['quality'])
        self.send({'type': 'bridge/heartbeat', 'dropped_packets': 2, 'unrelated_dropped_packets': 1,
                   'drop_reasons': {'oversized_unrelated_diagnostic': 1, 'oversized_diagnostic': 1},
                   'parse_errors': 0}, 101.5, 'lifecycle')
        self.assertIsNone(self.snapshot(101.6)['overview']['observed_subtotal'])
        diagnostics = self.engine.backends['b']['recent_observation_gaps']
        self.assertEqual(diagnostics[-1]['reasons'], ['oversized_diagnostic'])
        self.send({'type': 'bridge/heartbeat', 'dropped_packets': 2, 'unrelated_dropped_packets': 1,
                   'parse_errors': 0}, 101.7, 'lifecycle')
        self.assertEqual(len(diagnostics), 1)  # The same cumulative count is not a second loss.

    def test_late_count_from_the_previous_epoch_stays_budgeted_but_cannot_apply(self):
        stream = TextStream(('b', 'input', 'text', 0), 'text', 'upstream', 'unknown', 100)
        stream.append(' x' * 80, 101)
        counter = TokenCounter(isolated=False)
        counter.pool.shutdown(wait=True)
        old, new = Future(), Future()
        counter.pool = MagicMock()
        counter.pool.submit.side_effect = [old, new]
        self.addCleanup(counter.close)
        counter.tick([stream], 101.1)
        old_bytes = counter.pending_bytes
        stream.interrupt_observation()
        stream.resume_observation(102)
        stream.append(' y' * 5, 102)
        counter.tick([stream], 103.1)
        self.assertEqual(len(counter.jobs), 2)
        self.assertGreater(counter.pending_bytes, old_bytes)
        old.set_result((80, 0, 0))
        counter._complete()
        self.assertEqual(stream.counted_revision, -1)
        self.assertIs(stream.pending[0], new)
        new.set_result((5, 0, 5))
        counter._complete()
        self.assertEqual(stream.rate(103.1), 0)
        self.assertIsNone(stream.view(103.1)['tokens'])
        self.assertEqual(counter.pending_bytes, 0)

    def test_only_identified_outbound_logs_are_exempt_from_generation_loss(self):
        prefix = b'{"target":"tungstenite::protocol","fields":{"message":"Sending message '
        self.assertTrue(unrelated_diagnostic_prefix(prefix))
        self.assertFalse(unrelated_diagnostic_prefix(prefix.replace(b'Sending', b'Received')))
        self.assertFalse(unrelated_diagnostic_prefix(b'unknown oversized diagnostic'))
        observer = Observer(Path('/tmp/unused'), 'b', '/synthetic', '/bin/echo')
        observer.lose('oversized_unrelated_diagnostic', affects_generation=False)
        self.assertEqual(observer.health('bridge/heartbeat')['unrelated_dropped_packets'], 1)
