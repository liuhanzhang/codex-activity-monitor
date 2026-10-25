import json
from pathlib import Path
import tempfile
import unittest

from codex_progress_monitor.history import HistoryProvider, item_content, source_truncated
from codex_progress_monitor.timeline_query import TimelineQuery


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.directory = self.home / 'sessions/2026/10/17'
        self.directory.mkdir(parents=True)
        self.path = self.directory / 'rollout-probe-t.jsonl'
        self.provider = HistoryProvider()
        self.rows = [self.row('session_meta', {'id': 't', 'history_mode': 'paginated'}),
                     self.row('event_msg', {'type': 'task_started', 'turn_id': 'u', 'started_at': 1700000000})]

    def row(self, kind, payload):
        return {'timestamp': '2023-11-14T22:13:22Z', 'type': kind, 'payload': payload}

    def completed(self, item, start=1700000001000, end=1700000002000):
        return self.row('event_msg', {'type': 'item_completed', 'thread_id': 't', 'turn_id': 'u',
                        'started_at_ms': start, 'completed_at_ms': end, 'item': item})

    def write(self):
        self.path.write_text(''.join(json.dumps(r) + '\n' for r in self.rows))

    def index(self):
        return self.provider.index(str(self.home), 't')

    def test_missing_history_cache_is_bounded_and_success_clears_the_miss(self):
        for number in range(520):
            self.assertIsNone(self.provider.index(str(self.home), f'missing-{number}'))
        self.assertLessEqual(len(self.provider.missing), 512)
        self.assertNotIn((str(self.home), 'missing-0'), self.provider.missing)
        self.write()
        key = (str(self.home), 't')
        self.provider.missing[key] = 0
        self.assertIsNotNone(self.index())
        self.assertNotIn(key, self.provider.missing)

    def test_agent_identity_is_verified_bounded_and_invalidated_with_history(self):
        self.rows[0]['payload'].update(agent_nickname='Lorentz', source={'subagent': {'thread_spawn': {
            'agent_nickname': 'Lorentz', 'agent_path': '/root/desktop_control', 'agent_role': None}}})
        self.write()
        index = self.index()
        self.assertEqual(index.agent_metadata, {'nickname': 'Lorentz', 'agent_path': '/root/desktop_control',
                                               'thread_source': 'subagent', 'quality': []})
        revision = index.revision
        self.index()
        self.assertEqual(index.revision, revision)
        self.rows[0]['payload']['agent_nickname'] = 'Parfit'
        self.write()
        self.index()
        self.assertIsNone(index.agent_metadata['nickname'])
        self.assertIn('agent_name_conflict', index.agent_metadata['quality'])
        self.assertGreater(index.revision, revision)
        self.rows[0]['payload'].update(id='other')
        self.write()
        with self.assertRaises(ValueError):
            index.refresh()
        self.assertEqual(index.agent_metadata, {})
        self.rows[0]['payload'] = {'id': 't', 'history_mode': 'paginated', 'agent_nickname': ['bad'],
                                   'source': {'subagent': {'thread_spawn': {'agent_path': 'x'*513}}}}
        self.write()
        index.refresh()
        self.assertIsNone(index.agent_metadata['nickname'])
        self.assertIsNone(index.agent_metadata['agent_path'])
        self.assertIn('agent_metadata_invalid', index.agent_metadata['quality'])

    def test_replaced_and_legacy_history_do_not_retain_an_old_agent_name(self):
        self.rows[0]['payload']['agent_nickname'] = 'Lorentz'
        self.write()
        index = self.index()
        replacement = self.path.with_suffix('.new')
        replacement.write_text(json.dumps(self.row('session_meta', {'id': 't'}))+'\n')
        replacement.replace(self.path)
        self.index()
        self.assertIsNone(index.agent_metadata['nickname'])
        self.assertEqual(index.phases, {})

    def test_canonical_interval_and_raw_parameters_use_distinct_timings(self):
        self.rows += [self.row('response_item', {'type': 'function_call', 'id': 'raw', 'call_id': 'cmd',
                       'name': 'exec_command', 'arguments': '{"command":"echo probe"}'}),
                      self.completed({'type': 'CommandExecution', 'id': 'cmd', 'command': 'echo probe', 'status': 'completed'})]
        self.write(); index = self.index()
        raw = next(p for p in index.phases.values() if p['namespace'] == 'input')
        self.assertIsNone(raw['start_ms']); self.assertIsNone(raw['end_ms'])
        command = next(p for p in index.phases.values() if p['namespace'] == 'canonical')
        self.assertEqual(command['end_ms'] - command['start_ms'], 1000)
        self.assertIn('echo probe', index.detail(raw)['sections'][0]['text'])

    def test_partial_tail_is_consumed_only_after_newline(self):
        self.write()
        line = json.dumps(self.completed({'id': 'r', 'type': 'Reasoning', 'summary_text': ['visible']}))
        with self.path.open('a') as stream: stream.write(line[:20])
        self.assertEqual(len(self.index().phases), 0)
        with self.path.open('a') as stream: stream.write(line[20:] + '\n')
        self.assertEqual(len(self.index().phases), 1)

    def test_atomic_replacement_invalidates_offsets(self):
        self.rows.append(self.completed({'id': 'old', 'type': 'AgentMessage', 'content': 'old'}))
        self.write(); self.index()
        self.rows[-1] = self.completed({'id': 'new', 'type': 'AgentMessage', 'content': 'new'})
        replacement = self.path.with_suffix('.tmp')
        replacement.write_text(''.join(json.dumps(r) + '\n' for r in self.rows)); replacement.replace(self.path)
        self.assertEqual([p['item_id'] for p in self.index().phases.values()], ['new'])

    def test_wrong_session_identity_is_not_trusted_from_filename(self):
        self.rows[0]['payload']['id'] = 'other'
        self.write()
        self.assertIsNone(self.index())

    def test_legacy_raw_content_retained_with_unknown_duration(self):
        self.rows[0]['payload']['history_mode'] = 'legacy'
        self.rows.append(self.row('response_item', {'type': 'message', 'id': 'a', 'role': 'assistant',
                       'content': [{'type': 'output_text', 'text': 'legacy content'}]}))
        self.write(); index = self.index(); phase = next(iter(index.phases.values()))
        self.assertIsNone(phase['start_ms'])
        self.assertEqual(index.detail(phase)['sections'][0]['text'], 'legacy content')

    def test_source_truncation_remains_explicit(self):
        self.rows.append(self.completed({'id': 'cmd', 'type': 'CommandExecution', 'status': 'completed',
                         'command': 'probe', 'aggregated_output': 'before\n... command output truncated for persistence ...\nafter'}))
        self.write(); index = self.index()
        self.assertEqual(index.detail(next(iter(index.phases.values())))['state'], 'source_truncated')

    def test_raw_arguments_and_structured_result_are_not_rewritten(self):
        arguments = '{ "z": 0, "a": false, "text": "truncated for persistence" }'
        output = [{'type': 'text', 'text': 'first'}, {'type': 'text', 'text': 'second', 'extra': None}]
        self.rows += [self.row('response_item', {'type': 'function_call', 'id': 'raw', 'call_id': 'call',
                       'name': 'probe', 'arguments': arguments}),
                      self.row('response_item', {'type': 'function_call_output', 'call_id': 'other', 'output': 'WRONG'}),
                      self.row('response_item', {'type': 'function_call_output', 'call_id': 'call', 'output': output})]
        self.write(); index = self.index(); detail = index.detail(next(iter(index.phases.values())))
        self.assertEqual([s['title'] for s in detail['sections']], ['参数', '结果'])
        self.assertEqual(detail['sections'][0]['text'], arguments)
        self.assertEqual(json.loads(detail['sections'][1]['text']), output)
        self.assertEqual(detail['state'], 'available')

    def test_custom_input_and_empty_scalar_results_keep_their_values(self):
        for output in ('', None, False, 0, [], {}):
            with self.subTest(output=output):
                self.rows = self.rows[:2] + [
                    self.row('response_item', {'type': 'custom_tool_call', 'id': 'raw', 'call_id': 'call',
                             'name': 'exec', 'input': 'text(42);', 'arguments': 'NOT INPUT'}),
                    self.row('response_item', {'type': 'custom_tool_call_output', 'call_id': 'call', 'output': output})]
                self.write(); provider = HistoryProvider(); index = provider.index(str(self.home), 't')
                sections = index.detail(next(iter(index.phases.values())))['sections']
                self.assertEqual(sections[0]['text'], 'text(42);')
                self.assertNotIn('notice', sections[1])
                self.assertEqual(sections[1]['text'] if isinstance(output, str) else json.loads(sections[1]['text']), output)

    def test_internal_tools_return_original_fields_without_diff_assembly(self):
        cases = [
            ({'type': 'CommandExecution', 'command': ['/bin/sh', '-c', 'echo 42'], 'cwd': '/probe',
              'aggregated_output': '', 'exit_code': 0, 'duration': {'secs': 1, 'nanos': 0}},
             {'command': ['/bin/sh', '-c', 'echo 42'], 'cwd': '/probe'},
             {'aggregated_output': '', 'exit_code': 0, 'duration': {'secs': 1, 'nanos': 0}}),
            ({'type': 'McpToolCall', 'arguments': None, 'result': {'content': []}, 'error': None},
             None, {'result': {'content': []}, 'error': None}),
            ({'type': 'DynamicToolCall', 'arguments': {}, 'content_items': [], 'success': False},
             {}, {'content_items': [], 'success': False}),
        ]
        for item, args, result in cases:
            with self.subTest(kind=item['type']):
                sections, _ = item_content(item)
                self.assertEqual([s['title'] for s in sections], ['参数', '结果'])
                self.assertEqual(json.loads(sections[0]['text']), args)
                self.assertEqual(json.loads(sections[1]['text']), result)
        change = {'type': 'FileChange', 'changes': {'a.swift': {'unified_diff': '@@\n-a\n+b'}}, 'status': 'failed', 'stdout': ''}
        sections, _ = item_content(change)
        self.assertIn('notice', sections[0])
        self.assertEqual(json.loads(sections[1]['text']), {k: change[k] for k in ('changes', 'status', 'stdout')})
        self.assertNotIn('--- a.swift', sections[1]['text'])

    def test_truncation_is_detected_only_in_source_result_conventions(self):
        marker = '…42 chars truncated…'
        self.assertFalse(source_truncated({'type': 'custom_tool_call', 'input': marker}))
        self.assertFalse(source_truncated({'type': 'CommandExecution', 'command': marker,
                                         'aggregated_output': 'a normal sentence containing truncated by'}))
        self.assertFalse(source_truncated({'type': 'McpToolCall', 'result': {'content': [
            {'type': 'text', 'text': json.dumps({'code': marker})}]}}))
        self.assertTrue(source_truncated({'type': 'McpToolCall', 'result': {'content': [
            {'type': 'text', 'text': '{"content": [' + marker + ']}'}]}}))
        self.assertTrue(source_truncated({'type': 'custom_tool_call'}, [
            {'type': 'text', 'text': 'Warning: truncated output (original token count: 500)\nTotal output lines: 20\n\nfirst' + marker + 'last'}]))

    def test_compaction_is_one_model_process_with_no_body(self):
        self.rows.append(self.completed({'type': 'ContextCompaction', 'id': 'compact'}))
        self.write(); query = TimelineQuery(); bundle = query.bundle(str(self.home), 't')
        self.assertEqual(len(bundle['phases']), 1)
        phase = bundle['phases'][0]
        self.assertEqual((phase['track'], phase['namespace']), ('model', 'canonical'))
        self.assertEqual(phase['end_ms'] - phase['start_ms'], 1000)
        detail = query.detail(str(self.home), 't', 'u', phase['id'])
        self.assertEqual(detail['state'], 'no_content')
        self.assertEqual(detail['sections'], [])

    def test_raw_result_pagination_and_source_deletion_are_separate(self):
        output = [{'type': 'text', 'text': '原始🙂' * 110000}, {'type': 'text', 'text': ''}]
        self.rows += [self.row('response_item', {'type': 'custom_tool_call', 'id': 'raw', 'call_id': 'call',
                       'name': 'exec', 'input': 'text(result);'}),
                      self.row('response_item', {'type': 'custom_tool_call_output', 'call_id': 'call', 'output': output})]
        self.write(); query = TimelineQuery(); bundle = query.bundle(str(self.home), 't')
        phase = next(p for p in bundle['phases'] if p['namespace'] == 'input')
        detail = query.detail(str(self.home), 't', 'u', phase['id'])
        first = detail['sections'][1]
        self.assertEqual(detail['state'], 'available')
        self.assertIsNotNone(first['next_offset'])
        second = query.detail(str(self.home), 't', 'u', phase['id'], 'output', first['next_offset'],
                              page_token=first['page_token'])['sections'][0]
        self.assertEqual(json.loads(first['text'] + second['text']), output)
        # A sidecar retains identity after the source disappears, never its body.
        from codex_progress_monitor.timeline import TimelineBook
        query.book = TimelineBook()
        query.book._put(phase)
        self.path.unlink()
        missing = query.detail(str(self.home), 't', 'u', phase['id'])
        self.assertEqual(missing['state'], 'missing')
        self.assertEqual(missing['sections'], [])

    def test_detail_pagination_does_not_silently_discard_content(self):
        body = '中文🙂' * 120000
        self.rows.append(self.completed({'id': 'a', 'type': 'AgentMessage', 'content': body}))
        self.write(); index = self.index(); phase = next(iter(index.phases.values()))
        query = TimelineQuery()
        one = query.detail(str(self.home), 't', 'u', phase['id'])
        s = one['sections'][0]
        two = query.detail(str(self.home), 't', 'u', phase['id'], s['id'], s['next_offset'], page_token=s['page_token'])
        self.assertEqual(s['text'] + two['sections'][0]['text'], body)

    def test_history_queries_do_not_create_observation_or_sidecar(self):
        self.rows.append(self.completed({'id': 'a', 'type': 'AgentMessage', 'content': 'body'}))
        self.write()
        bundle = TimelineQuery().bundle(str(self.home), 't')
        self.assertEqual(bundle['selected_turn_id'], 'u')
        self.assertFalse((self.home / 'timeline').exists())
        self.assertFalse((self.home / 'observation.json').exists())

    def test_initial_and_midturn_inputs_are_markers_without_extra_turn(self):
        self.rows += [self.completed({'id': 'first', 'type': 'UserMessage', 'content': 'first'}),
                      self.completed({'id': 'steer', 'type': 'UserMessage', 'content': 'steer'}, end=1700000003000)]
        self.write(); result = TimelineQuery().bundle(str(self.home), 't')
        self.assertEqual(len(result['turns']), 1)
        self.assertEqual([p['is_initial'] for p in result['markers']], [True, False])
        self.assertEqual(result['phases'], [])

    def test_missing_old_time_fields_are_not_epoch_zero(self):
        self.rows.append(self.completed({'id': 'a', 'type': 'AgentMessage', 'content': 'body'}, start=None, end=0))
        self.write(); p = next(iter(self.index().phases.values()))
        self.assertIsNone(p['start_ms']); self.assertIsNone(p['end_ms'])

    def test_image_input_keeps_attachment_out_of_plaintext_body(self):
        self.rows.append(self.completed({'id': 'image-input', 'type': 'UserMessage', 'content': [
            {'type': 'text', 'text': 'Please inspect the image'}, {'type': 'local_image', 'path': '/example/preview.png'}]}))
        self.write(); index = self.index(); phase = next(iter(index.phases.values()))
        detail = index.detail(phase)
        self.assertEqual(detail['sections'][0]['text'], 'Please inspect the image')
        self.assertEqual(detail['attachments'], ['/example/preview.png'])

    def test_legacy_null_raw_id_uses_explicit_call_identity(self):
        self.rows[0]['payload']['history_mode'] = 'legacy'
        self.rows.append(self.row('response_item', {'id': None, 'type': 'function_call', 'call_id': 'known-call',
                         'name': 'probe', 'arguments': '{"value":42}'}))
        self.write(); index = self.index(); phase = next(iter(index.phases.values()))
        self.assertEqual(phase['item_id'], 'known-call')
        self.assertIn('42', index.detail(phase)['sections'][0]['text'])

    def test_dynamic_tool_retained_content_items_are_in_full_detail(self):
        self.rows.append(self.completed({'id': 'dynamic', 'type': 'DynamicToolCall', 'tool': 'example',
                         'arguments': {'input': 1}, 'status': 'completed',
                         'content_items': [{'type': 'text', 'text': 'retained result'}]}))
        self.write(); index = self.index(); phase = next(iter(index.phases.values()))
        detail = index.detail(phase)
        self.assertIn('retained result', next(s for s in detail['sections'] if s['id'] == 'output')['text'])


if __name__ == '__main__':
    unittest.main()
