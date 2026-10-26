from pathlib import Path
import tempfile
import threading
import unittest
import json
from unittest.mock import patch

from codex_activity_monitor.timeline import TimelineBook, item_phase
from codex_activity_monitor.timeline_query import TimelineQuery, QueryServer, query_runtime
from codex_activity_monitor.timeline_storage import TimelineStorage
from codex_activity_monitor.agent_presentation import agent_metadata, present_agents, scoped_status


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scope = str(self.root / 'home')
        self.book = TimelineBook()

    def test_old_child_work_is_linked_without_reassigning_its_turn(self):
        self.book._turn(self.scope, 'root', 'old', 1000, 'completed', start=1000, end=2000)
        self.book._turn(self.scope, 'root', 'new', 3000, 'active', start=3000)
        self.book._turn(self.scope, 'child', 'child-own', 1500, 'active', start=1500)
        self.book._relation(self.scope, 'root', 'old', 'child', 'dispatch', 'spawnAgent')
        self.book._put(item_phase(self.scope, 'child', 'child-own', {'id': 'reason', 'type': 'reasoning'}, 1600))
        query = TimelineQuery(self.book)
        current = query.bundle(self.scope, 'root', 'new')
        self.assertEqual(current['earlier_active_turns'], ['old'])
        self.assertEqual(current['phases'], [])
        previous = query.bundle(self.scope, 'root', 'old')
        self.assertEqual(previous['phases'][0]['turn_id'], 'child-own')
        self.assertEqual(previous['phases'][0]['thread_id'], 'child')

    def test_same_identity_in_another_scope_is_not_merged(self):
        for scope in (self.scope, str(self.root / 'other')):
            self.book._turn(scope, 'root', 'turn', 1000, 'active', start=1000)
            self.book._put(item_phase(scope, 'root', 'turn', {'id': 'item', 'type': 'reasoning'}, 1000))
        result = TimelineQuery(self.book).bundle(self.scope, 'root')
        self.assertEqual(len(result['phases']), 1)
        self.assertEqual(result['phases'][0]['scope_id'], self.scope)

    def test_query_rejects_invalid_shapes_before_reading_history(self):
        query = TimelineQuery(self.book)
        valid = {'action': 'detail', 'scope_id': self.scope, 'thread_id': 'root',
                 'turn_id': 'turn', 'phase_id': 'phase'}
        invalid = [None, [], 'timeline'] + [dict(valid, **change) for change in (
            {'thread_id': '../root'}, {'turn_id': []}, {'phase_id': []},
            {'offset': -1}, {'offset': True}, {'offset': 1.5}, {'section_id': []},
            {'action': 'timeline', 'phase_id': []})]
        for request in invalid:
            with self.subTest(request=request), self.assertRaises(ValueError):
                query.query(request)

    def test_cli_string_offset_is_normalized(self):
        from codex_activity_monitor.timeline_query import validated_request
        from codex_activity_monitor.content_version import SectionVersion
        token = SectionVersion.create((self.scope, 'root', 'turn', 'phase', 'input'),
            'codex_history', 'available', {'format': 'text', 'text': 'abcdef'}).token
        value = validated_request({'action': 'detail', 'scope_id': self.scope, 'thread_id': 'root',
                                   'turn_id': 'turn', 'phase_id': 'phase', 'section_id': 'input',
                                   'offset': '3', 'page_token': token})
        self.assertEqual(value['offset'], 3)

    def test_detail_requires_the_requested_turn_even_for_historical_phase_id(self):
        sessions = Path(self.scope) / 'sessions'
        sessions.mkdir(parents=True)
        rows = [{'type': 'session_meta', 'payload': {'id': 'root', 'history_mode': 'paginated'}},
                {'type': 'event_msg', 'payload': {'type': 'item_completed', 'thread_id': 'root',
                    'turn_id': 'real-turn', 'item': {'id': 'item', 'type': 'AgentMessage', 'text': 'PRIVATE'}}}]
        (sessions / 'rollout-root.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
        query = TimelineQuery(self.book)
        phase = next(iter(query.history.view(self.scope, 'root').phases.values()))
        wrong = query.detail(self.scope, 'root', 'wrong-turn', phase['id'])
        self.assertEqual(wrong['state'], 'content_unavailable')
        self.assertNotIn('PRIVATE', json.dumps(wrong))

    def test_names_use_verified_metadata_and_remain_unique_without_changing_identity(self):
        sessions = Path(self.scope) / 'sessions'
        sessions.mkdir(parents=True)
        self.book._turn(self.scope, 'root', 'turn', 1000, 'active', start=1000)
        children = ['01a15451-first-abc123', '01a15451-second-abc123', '01a15451-third-xyz789']
        for n, child in enumerate(children):
            self.book._relation(self.scope, 'root', 'turn', child, str(n), 'spawnAgent')
            (sessions / ('rollout-'+child+'.jsonl')).write_text(json.dumps({'type': 'session_meta', 'payload': {
                'id': child, 'agent_nickname': 'Lorentz' if n < 2 else 'Sagan'}})+'\n')
        query = TimelineQuery(self.book)
        result = query.bundle(self.scope, 'root')
        agents = result['agents'][1:]
        self.assertEqual([a['id'] for a in agents], children)
        self.assertEqual(len({a['display_name'] for a in agents}), 3)
        self.assertTrue(all(a['display_name'].startswith('Lorentz · ') for a in agents[:2]))
        self.assertEqual(agents[2]['display_name'], 'Sagan')
        self.assertTrue(all(a['name_source'] == 'history_nickname' for a in agents))
        self.assertTrue(all(a['status'] == 'unknown' for a in agents))

    def test_names_fall_back_and_explicit_thread_names_take_priority(self):
        from types import SimpleNamespace
        agents = [{'thread_id': 'root', 'quality': []}] + [
            {'thread_id': thread, 'quality': []} for thread in ('sameprefix-a-abc123', 'sameprefix-b-abc123', 'path-child')]
        indices = {'sameprefix-a-abc123': SimpleNamespace(agent_metadata=agent_metadata({'agent_nickname': 'Nickname'})),
                   'path-child': SimpleNamespace(agent_metadata=agent_metadata({'source': {'subagent': {
                       'thread_spawn': {'agent_path': '/root/native_storage'}}}}))}
        present_agents(agents, indices, {'sameprefix-a-abc123': 'Named task', 'sameprefix-b-abc123': 'samepref'})
        self.assertEqual([a['name_source'] for a in agents[1:]], ['thread_name', 'thread_id', 'history_path'])
        self.assertEqual(agents[1]['display_name'], 'Named task')
        self.assertEqual(agents[3]['display_name'], 'native_storage')
        self.assertNotEqual(agents[1]['display_name'], agents[2]['display_name'])

    def test_waiting_and_observation_gaps_are_not_reported_as_completion(self):
        turns = [{'status': 'completed', 'end_ms': 5000}]
        phase = {'status': 'waiting', 'quality': []}
        self.assertEqual(scoped_status(turns, [phase]), 'waiting')
        phase.update(status='active', quality=['observation_stopped'])
        self.assertEqual(scoped_status(turns, [phase]), 'unknown')
        self.assertEqual(scoped_status([{'status': 'active', 'end_ms': None}], []), 'unknown')

    def test_terminal_round_reports_missing_item_end_separately(self):
        self.book._turn(self.scope, 'root', 'turn', 1000, 'active', start=1000)
        self.book._turn(self.scope, 'child', 'own', 1100, 'completed', start=1100, end=5000)
        self.book._relation(self.scope, 'root', 'turn', 'child', 'spawn', 'spawnAgent')
        p = item_phase(self.scope, 'child', 'own', {'id': 'reason', 'type': 'reasoning'}, 1200)
        p.update(status='unknown', quality=['missing_item_end'])
        self.book._put(p)
        query = TimelineQuery(self.book)
        agent = query.bundle(self.scope, 'root')['agents'][1]
        self.assertEqual(agent['status'], 'completed')
        self.assertIn('missing_item_end', agent['activity_quality'])
        self.assertEqual(self.book.phases[p['id']]['status'], 'unknown')
        p.update(status='completed', end_ms=4000, end_source='producer', quality=[])
        self.book._put(p)
        agent = query.bundle(self.scope, 'root')['agents'][1]
        self.assertEqual((agent['status'], agent['status_scope']), ('completed', 'selected_parent_turn'))

    def test_coordination_only_uses_explicit_call_scope_and_turn_in_bundle_and_detail(self):
        self.book._turn(self.scope, 'root', 'turn', 1000, 'active', start=1000)
        call = item_phase(self.scope, 'root', 'turn', {'id': 'call', 'type': 'collabAgentToolCall', 'tool': 'wait'}, 1100, 1200)
        code = item_phase(self.scope, 'root', 'turn', {'id': 'code', 'type': 'dynamicToolCall', 'tool': 'wait'}, 1100, 1200)
        self.book._put(call); self.book._put(code)
        self.book._relation(self.scope, 'root', 'turn', 'child', 'call', 'wait')
        self.book._relation(self.scope, 'root', 'other-turn', 'wrong-turn', 'call', 'wait')
        self.book._relation('/other', 'root', 'turn', 'wrong-scope', 'call', 'wait')
        query = TimelineQuery(self.book)
        result = query.bundle(self.scope, 'root')
        projected = next(p for p in result['phases'] if p['id'] == call['id'])
        self.assertEqual((projected['coordination_action'], projected['related_agent_ids']), ('wait', ['child']))
        self.assertNotIn('coordination_action', next(p for p in result['phases'] if p['id'] == code['id']))
        self.assertNotIn('coordination_action', self.book.phases[call['id']])
        detail = query.detail(self.scope, 'root', 'turn', call['id'])
        self.assertEqual(detail['phase']['related_agent_ids'], ['child'])

    def test_repeated_relations_load_each_history_once_and_refresh_on_next_query(self):
        self.book._turn(self.scope, 'root', 'old', 1000, 'completed', start=1000, end=2000)
        self.book._turn(self.scope, 'root', 'new', 3000, 'active', start=3000)
        sessions = Path(self.scope) / 'sessions'
        sessions.mkdir(parents=True)
        child_path = sessions / 'rollout-child.jsonl'
        child_path.write_text(''.join(json.dumps(row) + '\n' for row in [
            {'type': 'session_meta', 'payload': {'id': 'child', 'history_mode': 'paginated'}},
            {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'child-own', 'started_at': 1.5}},
            {'type': 'event_msg', 'payload': {'type': 'item_completed', 'thread_id': 'child', 'turn_id': 'child-own',
             'started_at_ms': 1600, 'completed_at_ms': 1700, 'item': {'id': 'reason', 'type': 'Reasoning'}}},
        ]))
        for i in range(528):
            self.book._relation(self.scope, 'root', 'old', 'child', str(i), 'spawnAgent' if i == 0 else 'interacted')
            self.book._relation(self.scope, 'root', 'old', 'missing-child', str(i), 'interacted')
        query = TimelineQuery(self.book)
        with patch.object(query.history, 'index', wraps=query.history.index) as read:
            current = query.query({'action': 'timeline', 'scope_id': self.scope, 'thread_id': 'root'})
            self.assertEqual([call.args[1] for call in read.call_args_list], ['root', 'child', 'missing-child'])
        self.assertEqual(current['earlier_active_turns'], [])
        with child_path.open('a') as stream:
            stream.write(json.dumps({'type': 'event_msg', 'payload': {'type': 'item_completed',
                'thread_id': 'child', 'turn_id': 'child-own', 'started_at_ms': 1800, 'completed_at_ms': 1900,
                'item': {'id': 'text', 'type': 'AgentMessage'}}}) + '\n')
        with patch.object(query.history, 'index', wraps=query.history.index) as read:
            previous = query.query({'action': 'timeline', 'scope_id': self.scope, 'thread_id': 'root', 'turn_id': 'old'})
            self.assertEqual([call.args[1] for call in read.call_args_list], ['root', 'child', 'missing-child'])
        self.assertEqual({p['item_id'] for p in previous['phases']}, {'reason', 'text'})
        self.assertTrue(all(p['turn_id'] == 'child-own' for p in previous['phases']))

    def test_reused_child_only_keeps_its_explicit_active_parent_in_earlier_turns(self):
        for n, turn in enumerate(('old', 'reused', 'new')):
            self.book._turn(self.scope, 'root', turn, 1000+n*1000, 'active' if turn == 'new' else 'completed',
                            start=1000+n*1000)
        self.book._turn(self.scope, 'child', 'initial', 1100, 'completed', start=1100, end=1900)
        self.book._turn(self.scope, 'child', 'followup', 2100, 'active', start=2100)
        followup = next(t for t in self.book.turns.values() if t['turn_id'] == 'followup')
        followup['parent_turn_id'] = 'reused'
        self.book._relation(self.scope, 'root', 'old', 'child', 'spawn', 'spawnAgent')
        for n in range(3):
            self.book._relation(self.scope, 'root', 'reused', 'child', str(n), 'interacted')
        self.book._put(item_phase(self.scope, 'child', 'followup', {'id': 'reason', 'type': 'reasoning'}, 2200))
        current = TimelineQuery(self.book).bundle(self.scope, 'root', 'new')
        self.assertEqual(current['earlier_active_turns'], ['reused'])
        self.assertEqual(current['phases'], [])
        previous = TimelineQuery(self.book).bundle(self.scope, 'root', 'old')
        self.assertEqual(previous['agents'][1]['status'], 'completed')

    def test_old_sidecar_hides_non_input_markers_and_migrates_compaction_only_in_projection(self):
        path = self.root / 'metadata.sqlite3'
        store = TimelineStorage(path)
        self.book._turn(self.scope, 'root', 'turn', 1000, 'completed', start=1000, end=8000)
        for n, kind in enumerate(('other', 'approval', 'retry', 'auth', 'gap', 'contextCompaction')):
            p = item_phase(self.scope, 'root', 'turn', {'id': 'marker-'+str(n), 'type': 'sleep'}, 2000+n, 2000+n,
                           namespace='marker')
            p['kind'] = kind
            store.put('phase', p)
        compaction = item_phase(self.scope, 'root', 'turn', {'id': 'compact', 'type': 'contextCompaction'}, 2000, 4000)
        compaction['track'] = 'marker'  # First-release stored projection.
        original = dict(compaction)
        store.put('phase', compaction)
        store.put('turn', next(iter(self.book.turns.values())))
        for n in range(2):
            p = item_phase(self.scope, 'root', 'turn', {'id': 'user-'+str(n), 'type': 'userMessage'}, 1000+n*4000, 1000+n*4000)
            store.put('phase', p)
        store.close()
        before = path.read_bytes()
        result = TimelineQuery(metadata=path).bundle(self.scope, 'root')
        self.assertEqual([p['id'] for p in result['phases']], [original['id']])
        self.assertEqual(result['phases'][0]['track'], 'model')
        self.assertEqual([p['is_initial'] for p in result['markers']], [True, False])
        self.assertIn('observation_gap', result['quality'])
        self.assertEqual(path.read_bytes(), before)
        detail = TimelineQuery(metadata=path).detail(self.scope, 'root', 'turn', original['id'])
        self.assertEqual(detail['state'], 'no_content')
        self.assertEqual(detail['phase']['id'], original['id'])

    def test_existing_history_without_activity_content_is_not_reported_as_deleted(self):
        sessions = Path(self.scope) / 'sessions'
        sessions.mkdir(parents=True)
        (sessions / 'rollout-root.jsonl').write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'root'}})+'\n')
        phase = item_phase(self.scope, 'root', 'turn', {'id': 'cmd', 'type': 'commandExecution'}, 1000, 2000)
        self.book._put(phase)
        detail = TimelineQuery(self.book).detail(self.scope, 'root', 'turn', phase['id'])
        self.assertEqual(detail['state'], 'content_unavailable')

    def test_read_query_socket_is_separate_and_does_not_resume_observation(self):
        self.book._turn(self.scope, 'root', 'turn', 1000, 'active', start=1000)
        server = QueryServer(self.root / 'tl.sock', TimelineQuery(self.book))
        worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        worker.start()
        try:
            result = query_runtime(self.root, {'action': 'timeline', 'scope_id': self.scope, 'thread_id': 'root'})
            self.assertEqual(result['selected_turn_id'], 'turn')
            self.assertFalse((self.root / 'events.sock').exists())
            self.assertFalse((self.root / 'observation.json').exists())
            self.assertEqual((self.root / 'tl.sock').stat().st_mode & 0o777, 0o600)
        finally:
            server.shutdown(); server.server_close(); worker.join(timeout=1)

    def test_pruning_keeps_selected_old_turn_and_active_child_ancestor(self):
        scope = self.scope
        for i in range(9):
            self.book._turn(scope, 'root', str(i), 1000000+i*1000, 'completed', start=1000000+i*1000)
            self.book._put(item_phase(scope, 'root', str(i), {'id': 'item-'+str(i), 'type': 'reasoning'},
                                      1000000+i*1000, 1000001+i*1000))
        oldest = next(p for p in self.book.phases.values() if p['turn_id'] == '0')
        self.book.pin(oldest['id'], now=1010)
        self.book._relation(scope, 'root', '1', 'child', 'dispatch', 'spawnAgent')
        self.book._turn(scope, 'child', 'c', 1001000, 'active', start=1001000)
        self.book._put(item_phase(scope, 'child', 'c', {'id': 'c', 'type': 'reasoning'}, 1001000))
        self.book.prune(now=1010)
        root_turns = {t['turn_id'] for t in self.book.turns.values() if t['thread_id'] == 'root'}
        self.assertIn('0', root_turns); self.assertIn('1', root_turns)
        self.assertNotIn('2', root_turns)
        self.assertTrue(self.book.relations)


if __name__ == '__main__':
    unittest.main()
