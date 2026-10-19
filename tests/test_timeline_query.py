from pathlib import Path
import tempfile
import threading
import unittest
import json

from codex_progress_monitor.timeline import TimelineBook, item_phase
from codex_progress_monitor.timeline_query import TimelineQuery, QueryServer, query_runtime
from codex_progress_monitor.timeline_storage import TimelineStorage


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
