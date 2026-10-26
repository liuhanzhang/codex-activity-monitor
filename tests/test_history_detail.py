"""A single detail must not materialize its entire turn or guess associations."""

import json
from pathlib import Path
import tempfile
import unittest

from codex_activity_monitor.history import HistoryProvider
from codex_activity_monitor.timeline import TimelineBook, item_phase, phase_id
from codex_activity_monitor.timeline_query import TimelineQuery


class HistoryDetailTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.scope = tmp.name
        sessions = Path(self.scope)/'sessions'; sessions.mkdir()
        self.path = sessions/'synthetic-t.jsonl'
        self.rows = [self.row('session_meta', {'id': 't', 'history_mode': 'paginated'}),
                     self.row('event_msg', {'type': 'task_started', 'turn_id': 'u'})]
        self.provider = HistoryProvider()

    def row(self, kind, payload):
        return {'type': kind, 'payload': payload}

    def canonical(self, item):
        return self.row('event_msg', {'type': 'item_completed', 'thread_id': 't', 'turn_id': 'u', 'item': item})

    def write(self):
        self.path.write_text(''.join(json.dumps(row)+'\n' for row in self.rows))

    def query(self, namespace, item, *, book=None):
        return TimelineQuery(book, history=self.provider).detail(self.scope, 't', 'u',
                    phase_id(self.scope, 't', 'u', namespace, item))

    def test_single_message_in_a_wide_turn_has_a_small_metadata_working_set(self):
        self.rows += [self.canonical({'id': f'a{n}', 'type': 'AgentMessage', 'text': f'body-{n}'})
                      for n in range(1000)]
        self.write()
        result = self.query('model', 'a500')
        self.assertEqual(result['sections'][0]['text'], 'body-500')
        self.assertLess(self.provider.diagnostics()['cache_bytes'], 500_000)
        cached = [v for k, v in self.provider.indices.items() if k.kind == 'detail']
        self.assertEqual(len(cached), 1)
        self.assertEqual(len(cached[0].phases), 1)
        self.assertEqual(len(cached[0].refs), 1)
        self.assertFalse(cached[0].outputs)

    def test_timeline_metadata_does_not_retain_content_or_output_references(self):
        self.rows += [self.canonical({'id': f'a{n}', 'type': 'AgentMessage', 'text': f'body-{n}'})
                      for n in range(1000)]
        self.rows += [self.row('response_item', {'type': 'function_call', 'id': 'raw', 'call_id': 'call',
                                               'name': 'probe', 'arguments': 'raw parameters'}),
                      self.row('response_item', {'type': 'function_call_output', 'call_id': 'call', 'output': 'raw result'})]
        self.write()
        bundle = TimelineQuery(history=self.provider).bundle(self.scope, 't', 'u')
        self.assertEqual(len(bundle['phases']), 1001)
        cached = [v for k, v in self.provider.indices.items() if k.kind != 'directory']
        self.assertTrue(cached)
        self.assertTrue(all(not v.refs and not v.outputs for v in cached))

    def test_output_before_input_and_canonical_dependency_keep_explicit_ids(self):
        self.rows += [self.row('response_item', {'type': 'function_call_output', 'call_id': 'call', 'output': 'correct'}),
            self.canonical({'id': 'call', 'type': 'CommandExecution', 'command': 'probe', 'status': 'completed'}),
            self.row('response_item', {'id': 'raw', 'type': 'function_call', 'call_id': 'call',
                                     'name': 'probe', 'arguments': 'raw\r\n参数'}),
            self.row('response_item', {'type': 'function_call_output', 'call_id': 'other', 'output': 'wrong'}),
            self.row('event_msg', {'type': 'task_started', 'turn_id': 'v'}),
            self.row('response_item', {'type': 'function_call_output', 'call_id': 'call', 'output': 'wrong turn'})]
        self.write()
        result = self.query('input', 'raw')
        self.assertEqual([s['text'] for s in result['sections']], ['raw\r\n参数', 'correct'])
        self.assertEqual(result['phase']['related_phase_ids'], [phase_id(self.scope, 't', 'u', 'canonical', 'call')])
        cached = next(v for k, v in self.provider.indices.items() if k.kind == 'detail')
        self.assertEqual(set(cached.outputs), {('u', 'call')})
        self.assertEqual(len(cached.phases), 2)

    def test_prior_raw_global_coordination_evidence_is_preserved(self):
        self.rows += [self.row('response_item', {'id': 'raw', 'type': 'function_call', 'call_id': 'call',
                'namespace': 'collaboration', 'name': 'wait_agent', 'arguments': '{}'}),
            self.canonical({'id': 'call', 'type': 'CollabAgentToolCall', 'tool': 'wait', 'status': 'completed'})]
        self.write()
        phase = self.query('canonical', 'call')['phase']
        self.assertEqual(phase['coordination_action'], 'wait')
        self.assertEqual(phase['coordination_target_scope'], 'global')

    def test_changed_live_association_reloads_dependencies_without_a_source_write(self):
        self.rows.append(self.canonical({'id': 'call', 'type': 'CommandExecution', 'command': 'probe'}))
        self.write()
        book = TimelineBook()
        phase = item_phase(self.scope, 't', 'u', {'id': 'raw', 'type': 'function_call', 'name': 'probe'})
        book._put(phase); book.live[phase['id']] = {'text': 'live parameters'}
        first = self.query('input', 'raw', book=book)
        self.assertNotIn('related_phase_ids', first['phase'])
        phase['call_id'] = 'call'; book._put(phase)
        next_result = self.query('input', 'raw', book=book)
        self.assertEqual(next_result['phase']['related_phase_ids'], [phase_id(self.scope, 't', 'u', 'canonical', 'call')])


if __name__ == '__main__': unittest.main()
