import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

from codex_progress_monitor.engine import Engine
from codex_progress_monitor.events import Event
from codex_progress_monitor.history import HistoryProvider
from codex_progress_monitor.reconciliation import HistoryRefresh
from codex_progress_monitor.timeline import TimelineBook
from codex_progress_monitor.timeline_query import TimelineQuery


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='history-reconcile-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.path = self.home / 'sessions/2026/10/22/probe-t.jsonl'
        self.path.parent.mkdir(parents=True)
        self.wall = time.time() - 2
        self.provider = HistoryProvider()
        self.book = TimelineBook()
        self.engine = Engine(asynchronous_counts=False, timeline=self.book)
        self.addCleanup(self.engine.close)
        self.write([self.row('session_meta', {'id': 't', 'history_mode': 'paginated'}),
                    self.row('event_msg', {'type': 'task_started', 'turn_id': 'u', 'started_at': self.wall})])
        self.send({'type': 'bridge/started', 'home': str(self.home)}, direction='lifecycle')
        self.start()
        self.command()

    def row(self, kind, payload):
        return {'type': kind, 'timestamp': self.wall, 'payload': payload}

    def write(self, rows, mode='w'):
        with self.path.open(mode) as stream:
            stream.write(''.join(json.dumps(row)+'\n' for row in rows))

    def send(self, event, at=100, direction='protocol_out'):
        self.engine.ingest(Event('b', direction, event, at, self.wall + at - 100))

    def start(self, turn='u', at=100):
        self.send({'method': 'turn/started', 'params': {'threadId': 't', 'turn': {'id': turn}}}, at)

    def command(self, item='cmd', turn='u', at=101):
        self.send({'method': 'item/started', 'params': {'threadId': 't', 'turnId': turn, 'item': {
            'id': item, 'type': 'commandExecution', 'source': 'unifiedExecStartup',
            'processId': 'process-1', 'status': 'inProgress'}}}, at)

    def completion(self, *, item='cmd', turn='u', outcome='completed', start=None, end=None):
        return self.row('event_msg', {'type': 'item_completed', 'thread_id': 't', 'turn_id': turn,
            'started_at_ms': start if start is not None else self.wall*1000+100,
            'completed_at_ms': end if end is not None else self.wall*1000+450,
            'item': {'id': item, 'type': 'CommandExecution', 'status': outcome, 'exit_code': 0,
                     'process_id': 'process-1', 'aggregated_output': 'PRIVATE_RESULT'}})

    def finish_turn(self, turn='u', at=103):
        self.send({'method': 'turn/completed', 'params': {'threadId': 't',
                   'turn': {'id': turn, 'status': 'completed'}}}, at)

    def gap(self):
        self.send({'type': 'bridge/gap'}, 102, 'lifecycle')

    def summary(self, turns=('u',)):
        return self.provider.reconciliation(str(self.home), 't', turns)

    def reconcile(self, rows=None, turns=('u',)):
        if rows is not None:
            self.write(rows, 'a')
        summary = self.summary(turns)
        self.engine.apply_history(summary)
        return summary

    def snapshot(self):
        return self.engine.snapshot(105, self.wall+5)

    def activity(self, item='cmd'):
        return next(a for a in self.snapshot()['threads'][0]['activities'] if a['id'] == item)

    def test_missing_completion_is_corrected_without_query_or_usage_replay(self):
        self.gap()
        self.finish_turn()
        before = self.snapshot()
        self.assertTrue(before['threads'][0]['has_unresolved_work'])
        self.assertIsNone(before['overview']['generation_rate'])
        usage = self.engine.usage.dump()
        summary = self.reconcile([self.completion()])
        for _ in range(3):
            self.engine.apply_history(summary)
        activity = self.activity()
        self.assertEqual(activity['status'], 'completed')
        self.assertEqual(activity['exit_code'], 0)
        self.assertFalse(activity['active'])
        self.assertAlmostEqual(activity['duration_seconds'], .350, places=4)
        self.assertIn('observation_gap', self.snapshot()['threads'][0]['quality'])
        self.assertEqual(self.snapshot()['overview']['generation_rate'], 0)
        self.assertEqual(self.engine.usage.dump(), usage)
        self.assertNotIn('PRIVATE_RESULT', json.dumps(summary))
        self.assertEqual(self.engine.threads[('b', 't')].activities['cmd'].started_at, 101)

    def test_realtime_completion_precedes_history_and_late_start_does_not_reopen(self):
        self.send({'method': 'item/completed', 'params': {'threadId': 't', 'turnId': 'u',
                   'item': {'id': 'cmd', 'type': 'commandExecution', 'status': 'completed'}}}, 102)
        self.reconcile()
        self.assertFalse(self.activity()['active'])
        self.reconcile([self.completion()])
        self.command(at=104)
        self.assertFalse(self.activity()['active'])
        self.assertEqual(len(self.engine.threads[('b', 't')].activities), 1)

    def test_only_canonical_completion_closes_process(self):
        self.gap()
        self.finish_turn()
        self.reconcile([self.row('response_item', {'type': 'custom_tool_call', 'id': 'outer',
            'name': 'exec', 'call_id': 'cmd', 'input': 'PRIVATE_PARAMETERS'}),
            self.row('response_item', {'type': 'custom_tool_call_output', 'call_id': 'cmd',
                                     'output': 'Process running with session ID 1'})])
        self.assertEqual(self.activity()['status'], 'unknown')
        self.assertTrue(self.snapshot()['threads'][0]['has_unresolved_work'])
        self.assertIsNone(self.snapshot()['overview']['generation_rate'])

    def test_old_completion_leaves_new_turn_and_generation_intact(self):
        self.gap()
        self.finish_turn()
        self.start('new', 103)
        self.send({'type': 'response.output_item.added', 'item': {'id': 'text', 'type': 'message',
                   'metadata': {'thread_id': 't', 'turn_id': 'new'}}}, 103.1, 'upstream')
        self.send({'type': 'response.output_text.delta', 'item_id': 'text', 'delta': 'fresh generation'}, 104, 'upstream')
        self.engine.tick(104)
        stream = self.engine.streams[('b', 'text', 'text', 0)]
        tokens = stream.tokens
        self.reconcile([self.completion(), self.row('event_msg', {'type': 'task_complete', 'turn_id': 'u'})], ('u', 'new'))
        snapshot = self.snapshot()
        self.assertEqual(snapshot['threads'][0]['turn_id'], 'new')
        self.assertEqual(snapshot['threads'][0]['status'], 'inProgress')
        self.assertEqual(snapshot['overview']['known_stream_count'], 1)
        self.assertEqual(stream.tokens, tokens)
        self.assertIsNotNone(snapshot['overview']['generation_rate'])

    def test_history_closed_stream_keeps_counts_across_later_gap_and_model_change(self):
        self.send({'type': 'response.output_item.added', 'item': {'id': 'text', 'type': 'message',
                   'metadata': {'thread_id': 't', 'turn_id': 'u'}}}, 101, 'upstream')
        self.send({'type': 'response.output_text.delta', 'item_id': 'text', 'delta': 'completed text'}, 102, 'upstream')
        self.engine.tick(102)
        stream = self.engine.streams[('b', 'text', 'text', 0)]
        tokens, model = stream.tokens, stream.model
        record = self.completion(item='text')
        record['payload']['item']['type'] = 'AgentMessage'
        self.reconcile([record])
        self.assertTrue(stream.ended_by_history)
        self.assertEqual(stream.text, '')
        self.send({'type': 'bridge/gap'}, 103, 'lifecycle')
        self.send({'method': 'model/rerouted', 'params': {'threadId': 't', 'toModel': 'gpt-4o'}}, 104)
        self.assertEqual(stream.quality, 'complete')
        self.assertEqual(stream.tokens, tokens)
        self.assertEqual(stream.model, model)
        self.assertIsNone(stream.rate(105))
        self.send({'method': 'item/completed', 'params': {'threadId': 't', 'turnId': 'u',
            'item': {'id': 'text', 'type': 'agentMessage', 'status': 'completed',
                     'text': 'late final cannot rewrite observed counts'}}}, 105)
        self.engine.tick(106)
        self.assertEqual(stream.tokens, tokens)

    def test_identity_type_and_scope_are_not_guessed(self):
        summary = self.reconcile([self.completion(item='other'), self.completion(turn='other-turn')])
        self.assertTrue(self.activity()['active'])
        summary['scope_id'] = '/different-home'
        summary['phases'] = [dict(p, item_id='cmd', turn_id='u') for p in summary['phases']]
        self.engine.apply_history(summary)
        self.assertTrue(self.activity()['active'])
        summary['scope_id'] = str(self.home)
        summary['phases'] = [dict(p, item_type='reasoning') for p in summary['phases']]
        self.engine.apply_history(summary)
        self.assertTrue(self.activity()['active'])

    def test_background_command_survives_turn_end_until_own_completion(self):
        self.finish_turn()
        self.reconcile([self.row('event_msg', {'type': 'task_complete', 'turn_id': 'u'})])
        self.assertTrue(self.activity()['active'])
        self.reconcile([self.completion()])
        self.assertFalse(self.activity()['active'])

    def test_history_turn_end_closes_context_without_inventing_item_end(self):
        self.send({'method': 'item/started', 'params': {'threadId': 't', 'turnId': 'u',
                   'item': {'id': 'thinking', 'type': 'reasoning'}}}, 101)
        self.reconcile([self.row('event_msg', {'type': 'task_complete', 'turn_id': 'u'})])
        activity = self.activity('thinking')
        self.assertEqual(activity['status'], 'unknown')
        self.assertFalse(activity['active'])
        self.assertIsNone(activity['duration_seconds'])
        self.assertTrue(self.activity()['active'])

    def test_known_history_activity_can_exist_without_live_start(self):
        self.reconcile([self.completion(item='missed-command')])
        activity = self.activity('missed-command')
        self.assertEqual(activity['status'], 'completed')
        self.assertFalse(activity['active'])
        self.assertEqual(activity['exit_code'], 0)

    def test_partial_tail_and_missing_file_do_not_complete(self):
        encoded = json.dumps(self.completion())+'\n'
        with self.path.open('a') as stream:
            stream.write(encoded[:50])
        self.reconcile()
        self.assertTrue(self.activity()['active'])
        with self.path.open('a') as stream:
            stream.write(encoded[50:])
        self.reconcile()
        self.assertFalse(self.activity()['active'])
        self.path.unlink()
        self.reconcile()
        self.assertIn('history_unavailable', self.snapshot()['threads'][0]['history_quality'])

    def test_malformed_uncached_header_degrades_without_stopping_reader(self):
        self.path.write_text('[]\n')
        self.provider.indices.clear()
        self.reconcile()
        self.assertTrue(self.activity()['active'])
        self.assertIn('history_unavailable', self.snapshot()['threads'][0]['history_quality'])

    def test_replacement_invalidates_removed_history_fact(self):
        self.reconcile([self.completion()])
        replacement = self.path.with_suffix('.new')
        replacement.write_text(json.dumps(self.row('session_meta', {'id': 't'}))+'\n')
        replacement.replace(self.path)
        self.reconcile()
        self.assertEqual(self.activity()['status'], 'unknown')
        self.assertTrue(self.snapshot()['threads'][0]['has_unresolved_work'])
        self.assertIn('history_changed', self.snapshot()['threads'][0]['quality'])

    def test_conflicting_terminal_sources_are_reported(self):
        self.send({'method': 'item/completed', 'params': {'threadId': 't', 'turnId': 'u',
            'item': {'id': 'cmd', 'type': 'commandExecution', 'status': 'failed', 'exitCode': 9}}}, 102)
        self.reconcile([self.completion()])
        self.assertEqual(self.activity()['status'], 'completed')
        self.assertIn('lifecycle_conflict', self.snapshot()['threads'][0]['quality'])

    def test_missing_history_fields_preserve_valid_realtime_fields(self):
        self.send({'method': 'item/completed', 'params': {'threadId': 't', 'turnId': 'u',
            'item': {'id': 'cmd', 'type': 'commandExecution', 'status': 'completed', 'exitCode': 7}}}, 102)
        record = self.completion()
        record['payload']['item'].pop('exit_code')
        record['payload'].pop('started_at_ms')
        record['payload'].pop('completed_at_ms')
        self.reconcile([record])
        self.assertEqual(self.activity()['exit_code'], 7)
        self.assertEqual(self.activity()['duration_seconds'], 1)
        self.assertIsNone(self.activity()['source_end_ms'])

    def test_same_inode_truncation_rebuilds_history_basis(self):
        self.reconcile([self.completion()])
        generation = self.summary()['generation']
        self.write([self.row('session_meta', {'id': 't'}),
                    self.row('event_msg', {'type': 'task_started', 'turn_id': 'u'})])
        self.reconcile()
        self.assertNotEqual(self.summary()['generation'], generation)
        self.assertEqual(self.activity()['status'], 'unknown')
        self.assertTrue(self.snapshot()['threads'][0]['has_unresolved_work'])

    def test_detached_view_is_stable_during_background_index_refresh(self):
        old = self.provider.view(str(self.home), 't')
        self.reconcile([self.completion()])
        self.assertEqual(len(old.phases), 0)
        self.assertEqual(len(self.provider.view(str(self.home), 't').phases), 1)

    def test_historical_clock_does_not_refresh_old_retention(self):
        self.finish_turn()
        old = (self.wall-36000)*1000
        self.reconcile([self.completion(start=old, end=old+350),
                        {'type': 'event_msg', 'timestamp': (old+500)/1000,
                         'payload': {'type': 'task_complete', 'turn_id': 'u'}}])
        self.assertGreater(self.snapshot()['threads'][0]['last_progress_seconds'], 35000)
        self.engine.tick(105)
        self.assertNotIn(('b', 't'), self.engine.threads)

    def test_snapshot_timeline_and_original_detail_agree(self):
        self.gap()
        self.finish_turn()
        self.reconcile([self.completion()])
        query = TimelineQuery(self.book, history=self.provider)
        bundle = query.bundle(str(self.home), 't', 'u')
        phase = next(p for p in bundle['phases'] if p['item_id'] == 'cmd')
        self.assertEqual(phase['status'], self.activity()['status'])
        self.assertIn('observation_gap', phase['quality'])
        detail = query.detail(str(self.home), 't', 'u', phase['id'])
        self.assertIn('PRIVATE_RESULT', json.dumps(detail))

    def test_unrelated_unknown_work_still_blocks_total(self):
        self.command('still-unknown')
        self.gap()
        self.finish_turn()
        self.reconcile([self.completion()])
        self.assertFalse(self.activity()['active'])
        self.assertEqual(self.activity('still-unknown')['status'], 'unknown')
        self.assertIsNone(self.snapshot()['overview']['generation_rate'])

    def test_restored_unknown_identity_uses_history_without_new_turn(self):
        self.engine.close()
        self.engine = Engine(asynchronous_counts=False)
        self.addCleanup(self.engine.close)
        restored = self.engine.restore_unresolved([{'backend': 'b', 'home': str(self.home), 'id': 't',
            'turn_id': 'u', 'status': 'unknown', 'activities': [{'id': 'cmd', 'turn_id': 'u',
            'status': 'unknown', 'kind': 'tool', 'item_type': 'commandExecution', 'turn_bound': False}]}], self.wall)
        self.assertEqual(restored, {(str(self.home), 't')})
        self.reconcile([self.completion(), self.row('event_msg', {'type': 'task_complete', 'turn_id': 'u'})])
        self.assertEqual(self.activity()['status'], 'completed')
        self.assertFalse(self.snapshot()['threads'][0]['has_active_work'])

    def test_background_reader_is_bounded_and_does_not_own_live_lock(self):
        entered, release = threading.Event(), threading.Event()
        def slow(*_):
            entered.set()
            release.wait(2)
            return {'scope_id': str(self.home), 'thread_id': 't', 'quality': []}
        provider = Mock()
        provider.reconciliation.side_effect = slow
        reader = HistoryRefresh(provider=provider)
        try:
            reader.schedule([(str(self.home), 't', {'u'}, True)]*128)
            self.assertTrue(entered.wait(1))
            self.assertEqual(reader.drain(), [])
            self.assertTrue(self.snapshot()['threads'][0]['has_active_work'])
            self.assertFalse(release.is_set())
            self.assertEqual(provider.reconciliation.call_count, 1)
        finally:
            release.set()
            reader.close()

    def test_bounded_queue_does_not_starve_later_threads(self):
        entered, release = threading.Event(), threading.Event()
        def slow(*_):
            entered.set()
            release.wait(2)
            return {'quality': []}
        provider = Mock()
        provider.reconciliation.side_effect = slow
        reader = HistoryRefresh(provider=provider, capacity=2)
        requests = [(str(self.home), t, {'u'}, True) for t in ('a', 'b', 'c')]
        try:
            now = time.monotonic()
            reader.schedule(requests, now)
            self.assertTrue(entered.wait(1))
            reader.schedule(requests, now+2)
            with reader.lock:
                self.assertIn((str(self.home), 'c'), reader.pending)
                self.assertLessEqual(len(reader.pending), 2)
        finally:
            release.set()
            reader.close()


if __name__ == '__main__':
    unittest.main()
