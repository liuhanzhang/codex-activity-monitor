"""Bound background history traffic without turning overload into source facts."""

import json
import threading
import time
import unittest
from unittest.mock import Mock

from codex_progress_monitor.history_refresh import HistoryPacket, HistoryRefresh
from codex_progress_monitor.history_contract import HistorySelection


class HistoryRefreshBudgetTests(unittest.TestCase):
    def test_selection_detaches_mutable_input_sets(self):
        turns, items = {'u'}, {('u', 'item')}
        request = HistorySelection('/synthetic', 't', turns, items=items)
        turns.add('later'); items.add(('later', 'new'))
        self.assertEqual(request.turns, frozenset({'u'}))
        self.assertEqual(request.items, frozenset({('u', 'item')}))

    def wait(self, predicate):
        deadline = time.monotonic()+2
        while not predicate() and time.monotonic() < deadline:
            time.sleep(.002)
        self.assertTrue(predicate())

    def test_packet_detaches_mutable_metadata_and_caps_encoding(self):
        value = {'scope_id': '/synthetic', 'thread_id': 't', 'revision': 1,
                 'phases': [{'id': 'a'}], 'quality': []}
        packet = HistoryPacket.capture(value, 1024)
        value['phases'][0]['id'] = 'mutated'
        self.assertEqual(packet.decode()['phases'][0]['id'], 'a')
        self.assertIsNone(HistoryPacket.capture({'huge': '中文'*1024}, 1024))

    def reader(self, provider, **kwargs):
        reader = HistoryRefresh(provider=provider, **kwargs)
        self.addCleanup(reader.close)
        return reader

    def test_pending_requests_merge_item_coverage_for_the_same_persistent_source(self):
        entered, release = threading.Event(), threading.Event()
        provider = Mock()
        def slow(scope, thread, turns, *, items):
            entered.set(); release.wait(2)
            return {'scope_id': scope, 'thread_id': thread, 'quality': []}
        provider.reconciliation.side_effect = slow
        reader = self.reader(provider)
        try:
            reader.schedule([HistorySelection('/synthetic', 'busy', frozenset({'u'}), True, frozenset())])
            self.assertTrue(entered.wait(1))
            a = HistorySelection('/synthetic', 'shared', frozenset({'u'}), True, frozenset({('u', 'a')}))
            b = HistorySelection('/synthetic', 'shared', frozenset({'v'}), False, frozenset({('v', 'b')}))
            reader.schedule([a, b])
            with reader.lock:
                pending = reader.pending[a.key]
                self.assertEqual(pending.items, frozenset({('u', 'a'), ('v', 'b')}))
                self.assertEqual(pending.turns, frozenset({'u', 'v'}))
                self.assertTrue(pending.urgent)
                self.assertEqual(reader.pending_bytes, pending.utf8_bytes)
            release.set()
            self.wait(lambda: reader.diagnostics()['processed'] == 2)
            provider.reconciliation.assert_called_with('/synthetic', 'shared', frozenset({'u', 'v'}), items=pending.items)
        finally:
            release.set()

    def test_item_selection_bytes_participate_in_request_admission(self):
        provider = Mock(); reader = self.reader(provider, max_request_bytes=128)
        request = HistorySelection('/synthetic', 't', frozenset({'u'}), True,
                                   frozenset({('u', 'x'*512)}))
        reader.schedule([request])
        result, = reader.drain()
        self.assertNotIn('phase_selection', result)
        self.assertNotIn('revision', result)
        provider.reconciliation.assert_not_called()

    def test_oversized_summary_becomes_unavailable_without_a_revision(self):
        provider = Mock()
        provider.reconciliation.return_value = {
            'scope_id': '/synthetic', 'thread_id': 't', 'revision': 7,
            'generation': 'source', 'read_complete': True,
            'requested_turns': ['u'], 'phases': [{'id': 'x'*2048}]}
        reader = self.reader(provider, max_result_bytes=512, max_ready_bytes=1024, max_drain_bytes=512)
        reader.schedule([HistorySelection('/synthetic', 't', frozenset({'u'}), True)])
        self.wait(lambda: reader.diagnostics()['processed'] == 1)
        result, = reader.drain()
        self.assertEqual(result['scope_id'], '/synthetic')
        self.assertEqual(result['thread_id'], 't')
        self.assertEqual(result['quality'], ['history_unavailable', 'history_budget'])
        self.assertNotIn('revision', result)  # Cannot withdraw persisted terminal facts.
        self.assertNotIn('read_complete', result)
        self.assertEqual(reader.diagnostics()['oversized_results'], 1)

    def test_ready_bytes_and_each_tick_drain_are_bounded(self):
        provider = Mock()
        def summary(scope, thread, turns, **_):
            return {'scope_id': scope, 'thread_id': thread, 'revision': 1,
                    'quality': [], 'padding': 'z'*180}
        provider.reconciliation.side_effect = summary
        reader = self.reader(provider, max_result_bytes=512, max_ready_bytes=1024, max_drain_bytes=512)
        reader.schedule([HistorySelection('/synthetic', str(n), frozenset({'u'}), True) for n in range(8)])
        self.wait(lambda: reader.diagnostics()['processed'] == 8)
        before = reader.diagnostics()
        self.assertLessEqual(before['peak_ready_bytes'], 1024)
        self.assertGreater(before['discarded_results'], 0)
        drained = []
        while reader.diagnostics()['ready']:
            batch = reader.drain()
            self.assertTrue(batch)
            size = sum(len(json.dumps(v, ensure_ascii=False, separators=(',', ':')).encode()) for v in batch)
            self.assertLessEqual(size, 512)
            drained.extend(batch)
        self.assertEqual(drained[-1]['thread_id'], '7')  # Latest results survive pressure.
        self.assertEqual(reader.diagnostics()['ready_bytes'], 0)

    def test_pending_bytes_are_global_and_deferred_threads_are_retried(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        provider = Mock()
        def slow(scope, thread, turns, **_):
            entered.set(); release.wait(2)
            return {'scope_id': scope, 'thread_id': thread, 'quality': []}
        provider.reconciliation.side_effect = slow
        reader = self.reader(provider, capacity=128, max_pending_bytes=160)
        requests = [HistorySelection('/synthetic', str(n), frozenset({'turn-'+'x'*40}), True) for n in range(10)]
        reader.schedule(requests)
        self.assertTrue(entered.wait(1))
        reader.schedule(requests, time.monotonic()+2)
        stats = reader.diagnostics()
        self.assertLessEqual(stats['peak_pending_bytes'], 160)
        self.assertGreater(stats['deferred_requests'], 0)
        with reader.lock:
            self.assertIn(('/synthetic', '2'), reader.pending)
        release.set()

    def test_an_oversized_selection_is_not_silently_narrowed(self):
        provider = Mock()
        reader = self.reader(provider, max_pending_bytes=128, max_request_bytes=128)
        reader.schedule([HistorySelection('/synthetic', 't', frozenset({'x'*1024}), True)])
        result, = reader.drain()
        self.assertEqual(result['quality'], ['history_unavailable', 'history_budget'])
        self.assertNotIn('requested_turns', result)
        provider.reconciliation.assert_not_called()

    def test_close_does_not_publish_a_late_read(self):
        entered, release = threading.Event(), threading.Event()
        provider = Mock()
        def slow(*_, **__):
            entered.set(); release.wait(2)
            return {'scope_id': '/synthetic', 'thread_id': 't', 'quality': []}
        provider.reconciliation.side_effect = slow
        reader = self.reader(provider)
        try:
            reader.schedule([HistorySelection('/synthetic', 't', frozenset({'u'}), True)])
            self.assertTrue(entered.wait(1))
            reader.close()
            release.set(); reader.worker.join(1)
            self.assertFalse(reader.worker.is_alive())
            self.assertEqual(reader.drain(), [])
        finally:
            release.set()
