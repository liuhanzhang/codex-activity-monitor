"""Regressions from the real three-agent run (dev-history/12)."""

import json
from pathlib import Path
import tempfile
import unittest
import threading
from unittest.mock import patch

from codex_activity_monitor.agent_presentation import scoped_status
from codex_activity_monitor.history import HistoryProvider
from codex_activity_monitor.overview import annotate_parents
from codex_activity_monitor.timeline import TimelineBook, item_phase
from codex_activity_monitor.timeline_query import TimelineQuery
from codex_activity_monitor.agent_identity import AgentIdentityCache, read_header
from codex_activity_monitor.agent_relations import raw_coordination
from codex_activity_monitor.engine import Activity, Engine


class AgentFixTests(unittest.TestCase):
    def test_reply_does_not_cancel_the_explicit_parent(self):
        views = [dict(id='root', backend='b', thread_source='user', subagents={'child': 'completed'}),
                 dict(id='child', backend='b', thread_source='subagent', parent_thread_id='root',
                      parent_source='session_header', subagents={'root': 'interacted'})]
        annotate_parents(views)
        self.assertNotIn('parent_thread_id', views[0])
        self.assertEqual(views[1].get('parent_thread_id'), 'root')
        views[0]['subagents'] = {'child': 'spawn_agent'}
        views[1].pop('parent_thread_id')
        views[1]['parent_quality'] = ['parent_identity_conflict']
        annotate_parents(views)
        self.assertNotIn('parent_thread_id', views[1])

    def test_finished_round_keeps_missing_phase_unknown(self):
        phase = dict(status='unknown', kind='tool_input', quality=['missing_item_end'], end_ms=None)
        self.assertEqual(scoped_status([dict(status='completed', end_ms=2000)], [phase]), 'completed')
        self.assertEqual((phase['status'], phase['end_ms']), ('unknown', None))
        self.assertEqual(scoped_status([dict(status='active', end_ms=None)], [phase]), 'unknown')

    def test_terminal_round_does_not_hide_real_work_or_observation_conflicts(self):
        done = [dict(status='completed', end_ms=2000)]
        for state in ('active', 'waiting'):
            self.assertEqual(scoped_status(done, [dict(status=state, quality=[])]), state)
        for quality in ('disconnected', 'collector_restarted', 'observation_stopped', 'observation_gap', 'identity_conflict'):
            self.assertEqual(scoped_status(done, [dict(status='unknown', quality=['missing_item_end', quality])]), 'unknown')
        self.assertEqual(scoped_status(done, [dict(status='failed', quality=[])]), 'completed')
        self.assertEqual(scoped_status(done + [dict(status='active', end_ms=None)], []), 'unknown')

    def test_formal_scope_is_required_and_empty_receivers_are_not_global_proof(self):
        for namespace in (None, 'collaboration'):
            self.assertEqual(raw_coordination(dict(type='function_call', namespace=namespace,
                name='collaboration.wait_agent', arguments='{}')),
                {'call_operation':'wait', 'call_target_scope':'global'})
        self.assertEqual(raw_coordination(dict(type='function_call', namespace='ordinary',
            name='collaboration.wait_agent', arguments='{}')), {})
        self.assertEqual(raw_coordination(dict(type='function_call', namespace='collaboration', name={})), {})
        self.assertEqual(raw_coordination(dict(type='function_call', name='wait_agent', arguments='{}')), {})
        self.assertEqual(raw_coordination(dict(type='custom_tool_call', namespace='collaboration', name='wait_agent', input='{}')), {})
        self.assertEqual(raw_coordination(dict(type='function_call', namespace='collaboration', name='wait_agent', arguments='{')),
                         {'call_operation': 'wait'})
        book = TimelineBook(); book._turn('/s', 'root', 'turn', 1000, 'active', start=1000)
        phase = item_phase('/s', 'root', 'turn', dict(id='wait', type='collabAgentToolCall', tool='wait'), 1000, 1100)
        book._put(phase)
        result = TimelineQuery(book).bundle('/s', 'root', 'turn')['phases'][0]
        self.assertEqual(result['coordination_target_scope'], 'unknown')

    def test_header_parent_is_verified_and_invalidated(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions = Path(tmp)/'sessions'; sessions.mkdir()
            path = sessions/'rollout-child.jsonl'
            payload = dict(id='child', source={'subagent': {'thread_spawn': {'parent_thread_id':'root'}}})
            path.write_text(json.dumps(dict(type='session_meta', payload=payload))+'\n')
            found, meta = read_header(tmp, 'child')
            self.assertEqual((found, meta['parent_thread_id'], meta['thread_source']), (path, 'root', 'subagent'))
            payload['source']['subagent']['thread_spawn']['parent_thread_id'] = 'child'
            path.write_text(json.dumps(dict(type='session_meta', payload=payload))+'\n')
            _, meta = read_header(tmp, 'child', path)
            self.assertNotIn('parent_thread_id', meta)
            self.assertIn('agent_parent_invalid', meta['quality'])
            payload['id'] = 'wrong'
            path.write_text(json.dumps(dict(type='session_meta', payload=payload))+'\n')
            self.assertEqual(read_header(tmp, 'child', path)[1], {'quality':['identity_unavailable']})

    def test_header_lookup_never_blocks_snapshot_and_failed_refresh_drops_old_parent(self):
        entered, release = threading.Event(), threading.Event()
        def read(*args):
            entered.set(); release.wait(2)
            return None, {'quality':['identity_unavailable']}
        with patch('codex_activity_monitor.agent_identity.read_header', side_effect=read):
            cache = AgentIdentityCache()
            try:
                with cache.lock:
                    cache.ready[('/scope','child')] = (0, dict(thread_source='subagent', parent_thread_id='root', parent_source='session_header'))
                self.assertEqual(cache.get('/scope', 'child')['parent_thread_id'], 'root')
                self.assertTrue(entered.wait(1))
                # The worker remains inside read(); enrich only consumes its cache.
                views = [dict(id='child', backend='b', home='/scope', thread_source='subagent')]
                cache.enrich(views)
                self.assertEqual(views[0]['parent_thread_id'], 'root')
                self.assertTrue(any(v['id']=='root' and v['context_only'] for v in views))
                release.set(); cache.queue.join()
                self.assertNotIn('parent_thread_id', cache.get('/scope','child'))
                restored = [dict(id='child', backend='b', home='/scope', thread_source='subagent',
                                 parent_thread_id='root', parent_source='session_header')]
                cache.enrich(restored)
                self.assertNotIn('parent_thread_id', restored[0])
            finally:
                release.set(); cache.close()

    def test_expired_ancestor_keeps_only_context_while_child_is_recent(self):
        engine = Engine(asynchronous_counts=False)
        self.addCleanup(engine.close)
        engine.backends['b'] = dict(home='/scope', connected=True)
        parent = engine._thread('b','root'); parent.status='completed'; parent.last_progress_at=1
        parent.activities['old'] = Activity('old','old','text','',1,status='completed',ended_at=2)
        child = engine._thread('b','child'); child.status='completed'; child.last_progress_at=700
        child.parent_thread_id='root'; child.parent_source='spawn_call'; child.thread_source='subagent'
        engine.tick(800)
        self.assertIn(('b','root'), engine.threads)
        self.assertFalse(parent.activities)
        self.assertTrue(parent.context_only)
        engine.tick(1400)
        self.assertFalse(engine.threads)

    def test_reconnect_keeps_scoped_user_context_but_not_stale_child_names(self):
        cache = AgentIdentityCache()
        try:
            for scope in ('/scope-a','/scope-b'):
                cache.enrich([dict(id='user', home=scope, backend='b', thread_source='user', name=scope+' title', cwd=scope)])
            views = [dict(id='user', home='/scope-a', backend='b', thread_source='user', name='user', cwd='')]
            cache.enrich(views)
            self.assertEqual((views[0]['name'], views[0]['cwd']), ('/scope-a title','/scope-a'))
            cache.enrich([dict(id='child', home='/scope-a', backend='b', thread_source='subagent', name='old nickname')])
            child = [dict(id='child', home='/scope-a', backend='b', thread_source='subagent', name='child')]
            cache.enrich(child)
            self.assertEqual(child[0]['name'], 'child')
        finally:cache.close()

    def test_child_reply_is_not_a_descendant_and_formal_calls_get_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = str(Path(tmp)); sessions = Path(tmp) / 'sessions'; sessions.mkdir()
            def write(thread, parent, turn, parent_turn, calls):
                source = {'subagent': {'thread_spawn': {'parent_thread_id': parent}}} if parent else 'vscode'
                rows = [dict(type='session_meta', payload=dict(id=thread, source=source, history_mode='paginated')),
                        dict(type='turn_context', payload=dict(turn_id=turn, parent_turn_id=parent_turn))]
                for item in calls:
                    rows.append(dict(type='response_item' if item['type']=='function_call' else 'event_msg', payload=
                        item if item['type']=='function_call' else dict(type='item_completed', thread_id=thread,
                        turn_id=turn, item=item, started_at_ms=1100, completed_at_ms=1200)))
                (sessions / ('rollout-'+thread+'.jsonl')).write_text(''.join(json.dumps(r)+'\n' for r in rows))
            write('root', None, 'parent-turn', None, [
                dict(type='function_call', id='raw-spawn', call_id='spawn', namespace='collaboration', name='spawn_agent', arguments='{}'),
                dict(type='SubAgentActivity', id='spawn', kind='started', agent_thread_id='child'),
                dict(type='function_call', id='raw-wait', call_id='wait', namespace='collaboration', name='wait_agent', arguments='{"timeout_ms":10}'),
                dict(type='CollabAgentToolCall', id='wait', tool='wait', status='completed', receiver_thread_ids=[])])
            write('child', 'root', 'own', 'parent-turn', [
                dict(type='function_call', id='raw-reply', call_id='reply', namespace='collaboration', name='send_message', arguments='{}'),
                dict(type='SubAgentActivity', id='reply', kind='interacted', agent_thread_id='root')])
            query = TimelineQuery()
            child = query.bundle(scope, 'child', 'own')
            self.assertEqual([a['id'] for a in child['agents']], ['child'])
            reply = next(p for p in child['phases'] if p['item_id']=='raw-reply')
            self.assertEqual((reply['coordination_action'], reply['related_agent_ids']), ('send', ['root']))
            root = query.bundle(scope, 'root', 'parent-turn')
            self.assertEqual([a['id'] for a in root['agents']], ['root', 'child'])
            spawn = next(p for p in root['phases'] if p['item_id']=='raw-spawn')
            self.assertEqual((spawn['coordination_action'], spawn['track']), ('spawn', 'model'))
            wait = next(p for p in root['phases'] if p['item_id']=='wait')
            self.assertEqual(wait['coordination_target_scope'], 'global')
            detail = query.detail(scope, 'root', 'parent-turn', spawn['id'])
            self.assertEqual(detail['phase']['related_agent_ids'], ['child'])
            self.assertEqual(detail['state'], 'available')


if __name__ == '__main__':
    unittest.main()
