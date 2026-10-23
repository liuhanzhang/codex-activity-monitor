import unittest

from codex_progress_monitor.engine import Engine
from codex_progress_monitor.events import Event, filter_event
from codex_progress_monitor.timeline import TimelineBook, phase_id, merge_phase


class TimelineTests(unittest.TestCase):
    def setUp(self):
        self.book = TimelineBook()
        self.engine = Engine(False, timeline=self.book)
        self.addCleanup(self.engine.close)
        self.send({'type': 'bridge/started', 'home': '/probe'}, 99, 'lifecycle')
        self.turn()

    def send(self, data, at=102, direction='protocol_out'):
        self.engine.ingest(Event('b', direction, data, at, 1700000000 + at))

    def turn(self, turn='u', thread='t', at=100):
        self.send({'method': 'turn/started', 'params': {'threadId': thread, 'turn': {'id': turn, 'status': 'inProgress'}}}, at)

    def raw(self, item='call', kind='function_call', event='added', at=101, **kwargs):
        self.send({'type': 'response.output_item.' + event, 'item': {'id': item, 'type': kind,
                   'metadata': {'turn_id': 'u', 'thread_id': 't'}, **kwargs}}, at, 'upstream')

    def item(self, item='call', kind='commandExecution', event='started', at=103, turn='u', **kwargs):
        self.send({'method': 'item/' + event, 'params': {'threadId': 't', 'turnId': turn,
                   'item': {'id': item, 'type': kind, **kwargs}}}, at)

    def phases(self):
        return list(self.book.phases.values())

    def test_generation_survives_tool_ready_and_execution_is_separate(self):
        self.raw(name='exec_command', call_id='cmd')
        self.raw(event='done', at=102, name='exec_command', call_id='cmd', arguments='{"command":"echo probe"}')
        self.item(item='cmd', source='unifiedExecStartup', processId='123')
        generation = next(p for p in self.phases() if p['track'] == 'model')
        self.assertEqual(generation['end_ms'] - generation['start_ms'], 1000)
        self.assertEqual(self.engine.threads[('b', 't')].activities['call'].kind, 'tool_ready')
        self.assertEqual(len(self.phases()), 2)

    def test_canonical_times_do_not_overwrite_input_window(self):
        self.raw(name='apply_patch', call_id='call')
        self.raw(event='done', at=102, name='apply_patch')
        self.send({'method': 'item/started', 'params': {'threadId': 't', 'turnId': 'u',
                   'startedAtMs': 1700000200000, 'item': {'id': 'call', 'type': 'fileChange'}}}, 103)
        self.assertEqual({p['kind'] for p in self.phases()}, {'file_edit_input', 'tool_execution'})
        edit = next(p for p in self.phases() if p['track'] == 'tool')
        self.assertEqual(edit['start_ms'], 1700000200000)
        model = next(p for p in self.phases() if p['track'] == 'model')
        self.assertEqual(model['end_ms'] - model['start_ms'], 1000)

    def test_repeated_lifecycle_and_late_delta_do_not_reopen(self):
        self.raw(kind='message')
        self.raw(kind='message', event='done', at=102, status='completed')
        self.raw(kind='message', event='done', at=103, status='completed')
        self.send({'type': 'response.output_text.delta', 'item_id': 'call', 'delta': 'late'}, 104, 'upstream')
        self.assertEqual(len(self.phases()), 1)
        self.assertEqual(self.phases()[0]['status'], 'completed')
        self.assertEqual(self.phases()[0]['end_ms'], 1700000102000)

    def test_missing_start_is_unknown_not_zero(self):
        self.item(event='completed', status='completed')
        p = self.phases()[0]
        self.assertIsNone(p['start_ms'])
        self.assertIn('start_unknown', p['quality'])

    def test_waiting_lookup_expires_with_its_phase_after_a_missing_reply(self):
        self.send({'id': 'request', 'method': 'item/commandExecution/requestApproval',
                   'params': {'threadId': 't', 'turnId': 'u', 'itemId': 'cmd'}}, 101)
        self.assertTrue(self.book.waiting)
        self.send({'method': 'turn/completed', 'params': {'threadId': 't',
                   'turn': {'id': 'u', 'status': 'completed'}}}, 102)
        self.book.prune(now=1700000800)
        self.assertFalse(self.book.phases)
        self.assertFalse(self.book.waiting)

    def test_cross_turn_process_keeps_owner_and_survives_parent_end(self):
        self.item(source='unifiedExecStartup', processId='123')
        self.send({'method': 'turn/completed', 'params': {'threadId': 't', 'turn': {'id': 'u', 'status': 'completed'}}}, 104)
        self.turn('v', at=105)
        self.assertEqual((self.phases()[0]['turn_id'], self.phases()[0]['status']), ('u', 'active'))
        self.item(event='completed', status='completed', at=110)
        self.assertEqual(self.phases()[0]['status'], 'completed')

    def test_gap_freezes_observation_without_inventing_end(self):
        self.raw(kind='message')
        self.send({'type': 'bridge/gap'}, 104, 'lifecycle')
        p = self.phases()[0]
        self.assertEqual(p['status'], 'unknown')
        self.assertIsNone(p['end_ms'])
        self.assertEqual(p['observed_until_ms'], 1700000104000)

    def test_mirror_not_drawn_as_independent_work(self):
        self.item(kind='agentMessage', delivery='async')
        self.assertEqual(self.phases(), [])

    def test_history_without_delivery_cannot_revive_a_known_mirror(self):
        self.raw(kind='message')
        self.item(kind='agentMessage', item='call', delivery='async')
        p = self.phases()[0]
        self.assertTrue(p['mirrored'])
        restored = merge_phase(p, {**p, 'mirrored': False, 'status': 'completed', 'end_ms': 1700000105000, 'end_source': 'history'})
        self.assertTrue(restored['mirrored'])

    def test_pending_identity_retains_original_receipt_time(self):
        self.send({'type': 'response.output_item.added', 'item': {'id': 'pending', 'type': 'message',
                   'metadata': {'turn_id': 'later'}}}, 101, 'upstream')
        self.turn('later', at=104)
        p = next(p for p in self.phases() if p['item_id'] == 'pending')
        self.assertEqual(p['start_ms'], 1700000101000)

    def test_steering_request_is_not_an_extra_input_block(self):
        request = filter_event('protocol_in', {'id': 42, 'method': 'turn/steer',
                 'params': {'threadId': 't', 'expectedTurnId': 'u', 'input': [{'text': 'PRIVATE'}]}})
        self.assertNotIn('PRIVATE', str(request))
        self.send(request, direction='protocol_in')
        self.send(filter_event('protocol_out', {'id': 42, 'result': {'turnId': 'u'}}))
        self.item(kind='userMessage', item='input', event='completed', status='completed')
        self.assertEqual(len(self.book.turns), 1)
        self.assertEqual(len([p for p in self.phases() if p['kind'] == 'input']), 1)

    def test_failed_steering_and_resolved_question_do_not_create_input_markers(self):
        from codex_progress_monitor.timeline_query import TimelineQuery
        self.item(kind='userMessage', item='first', event='completed', at=101, status='completed')
        self.send({'id': 52, 'method': 'turn/steer', 'params': {'threadId': 't', 'expectedTurnId': 'u'}},
                  102, 'protocol_in')
        self.send({'id': 52, 'error': {'code': -32600, 'message': 'turn mismatch'}}, 103)
        self.send({'id': 53, 'method': 'item/tool/requestUserInput',
                   'params': {'threadId': 't', 'turnId': 'u', 'itemId': 'question'}}, 104)
        self.send({'method': 'serverRequest/resolved', 'params': {'requestId': 53}}, 105)
        self.item(kind='userMessage', item='steer', event='completed', at=106, status='completed')
        self.item(kind='userMessage', item='steer', event='completed', at=107, status='completed')
        result = TimelineQuery(self.book).bundle('/probe', 't', 'u')
        self.assertEqual([p['item_id'] for p in result['markers']], ['first', 'steer'])
        self.assertEqual([p['is_initial'] for p in result['markers']], [True, False])
        self.assertEqual(len(self.book.turns), 1)

    def test_compaction_lifecycle_and_old_completion_notification_are_one_visible_process(self):
        from codex_progress_monitor.timeline_query import TimelineQuery
        self.item(kind='contextCompaction', item='compact', at=101)
        original_id = self.phases()[0]['id']
        self.item(kind='contextCompaction', item='compact', event='completed', at=104, status='completed')
        self.send({'method': 'thread/compacted', 'params': {'threadId': 't', 'turnId': 'u'}}, 105)
        self.item(kind='contextCompaction', item='end-only', event='completed', at=106, status='completed')
        result = TimelineQuery(self.book).bundle('/probe', 't', 'u')
        self.assertEqual(len(result['phases']), 2)
        self.assertEqual(result['markers'], [])
        complete = next(p for p in result['phases'] if p['item_id'] == 'compact')
        unknown = next(p for p in result['phases'] if p['item_id'] == 'end-only')
        self.assertEqual((complete['id'], complete['track'], complete['namespace']),
                         (original_id, 'model', 'canonical'))
        self.assertEqual(complete['end_ms'] - complete['start_ms'], 3000)
        self.assertIsNone(unknown['start_ms'])
        self.assertIn('start_unknown', unknown['quality'])

    def test_edit_recognition_is_static_and_conservative(self):
        self.raw(name='functions.exec')
        self.raw(event='done', at=102, name='functions.exec', arguments='await tools.apply_patch("*** Begin Patch\\n*** Update File: test.py\\n@@\\n-a\\n+b\\n*** End Patch")')
        self.assertEqual(self.phases()[0]['kind'], 'file_edit_input')
        self.raw(item='dynamic', name='functions.exec', at=103)
        self.raw(item='dynamic', event='done', at=104, name='functions.exec', arguments='await tools.apply_patch(variable)')
        self.assertEqual(self.phases()[1]['kind'], 'tool_input')

    def test_more_than_twenty_activities_remain_for_whole_turn(self):
        for i in range(35):
            self.item(item=str(i), kind='fileChange', at=101+i)
            self.item(item=str(i), kind='fileChange', event='completed', at=101.1+i, status='completed')
        self.engine.tick(140)
        self.assertEqual(len(self.engine.threads[('b', 't')].activities), 20)
        self.assertEqual(len(self.phases()), 35)

    def test_dispatch_completion_does_not_end_child(self):
        self.turn('child-turn', 'child', 101)
        self.item(kind='collabAgentToolCall', receiverThreadIds=['child'], tool='spawnAgent')
        self.item(kind='collabAgentToolCall', event='completed', status='completed', receiverThreadIds=['child'], tool='spawnAgent')
        self.assertEqual(next(t for t in self.book.turns.values() if t['thread_id'] == 'child')['status'], 'active')
        self.assertEqual(next(iter(self.book.relations.values()))['parent_turn_id'], 'u')

    def test_waiting_resolves_without_ending_the_target_operation(self):
        self.item(item='cmd', source='unifiedExecStartup', processId='123')
        self.send({'id': 42, 'method': 'item/commandExecution/requestApproval',
                   'params': {'threadId': 't', 'turnId': 'u', 'itemId': 'cmd'}}, 104)
        self.send({'method': 'serverRequest/resolved', 'params': {'requestId': 42}}, 109)
        marker = next(p for p in self.phases() if p['kind'] == 'approval')
        self.assertEqual(marker['end_ms'] - marker['start_ms'], 5000)
        command = next(p for p in self.phases() if p['kind'] == 'tool_execution')
        self.assertEqual(command['status'], 'active')

    def test_command_preparation_does_not_claim_execution(self):
        self.item(item='pending', command='echo probe')
        p = next(p for p in self.phases() if p['item_id'] == 'pending')
        self.assertEqual(p['status'], 'waiting')
        self.assertIsNone(p['start_ms'])
        self.send({'method': 'item/commandExecution/outputDelta', 'params': {'threadId': 't', 'turnId': 'u',
                   'itemId': 'pending', 'delta': 'observed output'}}, 105)
        p = next(p for p in self.phases() if p['item_id'] == 'pending')
        self.assertEqual(p['status'], 'active')
        self.assertIsNone(p['start_ms'])
        self.assertNotIn('execution_unconfirmed', p['quality'])

    def test_disconnect_does_not_freeze_another_backend_in_same_home(self):
        self.raw(kind='message')
        for event in [Event('c', 'lifecycle', {'type': 'bridge/started', 'home': '/probe'}, 100, 1700000100),
                      Event('c', 'protocol_out', {'method': 'turn/started', 'params': {'threadId': 'other', 'turn': {'id': 'v', 'status': 'inProgress'}}}, 101, 1700000101),
                      Event('c', 'upstream', {'type': 'response.output_item.added', 'item': {'id': 'other-item', 'type': 'reasoning', 'metadata': {'thread_id': 'other', 'turn_id': 'v'}}}, 102, 1700000102)]:
            self.engine.ingest(event)
        self.send({'type': 'bridge/disconnected'}, 104, 'lifecycle')
        self.assertEqual(next(p for p in self.phases() if p['thread_id'] == 'other')['status'], 'active')

    def test_wall_clock_jump_keeps_work_identity_but_refuses_false_alignment(self):
        from codex_progress_monitor.timeline_query import TimelineQuery
        self.raw(kind='message')
        self.engine.ingest(Event('b', 'upstream', {'type': 'response.output_text.delta', 'item_id': 'call',
                                                  'delta': 'observed'}, 102, 1700003702))
        old = next(p for p in self.phases() if p['item_id'] == 'call')
        self.assertEqual(old['status'], 'active')
        self.assertIn('wall_clock_changed', old['quality'])
        bundle = TimelineQuery(self.book).bundle('/probe', 't', 'u')
        self.assertIsNone(bundle['origin_ms'])
        self.assertIn('clock_unreliable', bundle['quality'])
        self.engine.ingest(Event('b', 'protocol_out', {'method': 'turn/started', 'params': {'threadId': 't',
                      'turn': {'id': 'next', 'status': 'inProgress'}}}, 103, 1700003703))
        self.assertNotIn('b', self.book.clock_jumps)

    def test_new_turn_freezes_missing_model_end_without_ending_independent_process(self):
        self.raw(kind='message')
        self.item(item='running', source='unifiedExecStartup', processId='123')
        self.turn('next', at=105)
        model = next(p for p in self.phases() if p['namespace'] == 'model')
        command = next(p for p in self.phases() if p['namespace'] == 'canonical')
        self.assertEqual(model['status'], 'unknown')
        self.assertIsNone(model['end_ms'])
        self.assertEqual(command['status'], 'active')
        old_turn = next(t for t in self.book.turns.values() if t['turn_id'] == 'u')
        self.assertEqual(old_turn['status'], 'unknown')


if __name__ == '__main__':
    unittest.main()
