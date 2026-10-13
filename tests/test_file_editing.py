"""File editing UX through bounded parsing, real event filtering and rendering."""

import io
import json
import time
import unittest

from rich.console import Console

from codex_progress_monitor.display import render
from codex_progress_monitor.engine import Engine
from codex_progress_monitor.events import Event, filter_event
from codex_progress_monitor.metrics import count_text
from codex_progress_monitor.tool_labels import MAX_CODE_PREFIX, MAX_PATCH_FILES, file_edit_preview


PATCH = '*** Begin Patch\n*** Update File: src/first.py\n@@\n-OLD_PRIVATE\n+NEW_PRIVATE\n*** Add File: tests/second.py\n+PRIVATE_BODY\n*** End Patch\n'


class PatchPreviewTests(unittest.TestCase):
    def test_complete_headers_track_current_file_and_rename_without_body(self):
        self.assertEqual(file_edit_preview('apply_patch', '*** Begin Patch\n*** Update File: src/fir')['files'], [])
        preview = file_edit_preview('functions.apply_patch', PATCH)
        self.assertEqual(preview, {'files': [{'path': 'first.py'}, {'path': 'second.py'}],
                                   'file_count': 2, 'current_file': 'second.py'})
        self.assertEqual(file_edit_preview('apply_patch', PATCH.rstrip('\n')), preview)
        rename = '*** Begin Patch\n*** Update File: src/old.py\n*** Move to: lib/new.py\n@@\n+PRIVATE\n*** Delete File: gone.py\n*** End Patch\n'
        preview = file_edit_preview('apply_patch', rename)
        self.assertEqual(preview['files'], [{'path': 'new.py'}, {'path': 'gone.py'}])
        self.assertEqual(preview['file_count'], 2)
        partial = file_edit_preview('apply_patch', PATCH.split('*** Add File:')[0]+'*** Add File: tests/sec')
        self.assertEqual(partial['file_count'], 1)
        self.assertEqual(partial['current_file'], '')
        self.assertNotIn('PRIVATE', json.dumps(preview))
        fake = '*** Begin Patch\n*** Update File: real.py\n@@\n+*** Update File: fake.py\n *** Add File: also_fake.py\n*** End Patch\n'
        self.assertEqual(file_edit_preview('apply_patch', fake)['file_count'], 1)

    def test_code_mode_literals_partial_escapes_and_dynamic_fallback(self):
        code = 'await tools.apply_patch('+json.dumps(PATCH)+');'
        self.assertEqual(file_edit_preview('exec', code)['current_file'], 'second.py')
        self.assertEqual(file_edit_preview('functions.exec', 'tools.apply_patch(`'+PATCH+'`);')['file_count'], 2)
        partial = r'tools.apply_patch("*** Begin Patch\n*** Update File: src/fir'
        self.assertEqual(file_edit_preview('exec', partial)['files'], [])
        self.assertEqual(file_edit_preview('exec', partial+r'st.py\n@@\n+NEW')['current_file'], 'first.py')
        for value in ('tools.apply_patch(variable)',
                      'tools.apply_patch("*** Begin Patch\\n*** Update File: first.py\\n" + rest)',
                      'tools.apply_patch(`*** Begin Patch\n*** Update File: ${file}\n`)'):
            with self.subTest(value=value):
                self.assertEqual(file_edit_preview('exec', value)['files'], [])
        for value in ('// '+code, 'const s='+json.dumps(code)+';', 'other.'+code.removeprefix('await '),
                      code+' tools.exec_command({cmd:"sleep 2"});', 'tools.mcp__example__apply_patch({})'):
            with self.subTest(value=value):
                self.assertIsNone(file_edit_preview('exec', value))
        self.assertIsNone(file_edit_preview('mcp__example__apply_patch', PATCH))

    def test_scan_and_metadata_limits_fall_back_without_truncated_names(self):
        huge = '*** Begin Patch\n*** Update File: first.py\n+'+'x'*MAX_CODE_PREFIX
        self.assertEqual(file_edit_preview('apply_patch', huge)['files'], [])
        self.assertEqual(file_edit_preview('exec', 'tools.apply_patch(`'+huge)['files'], [])
        many = '*** Begin Patch\n'+''.join('*** Delete File: file%d.py\n'%i for i in range(MAX_PATCH_FILES+1))
        self.assertEqual(file_edit_preview('apply_patch', many)['file_count'], 0)
        for filename in ('\x1bPRIVATE.py', 'a'*161+'.py', '..'):
            self.assertEqual(file_edit_preview('apply_patch', '*** Begin Patch\n*** Delete File: '+filename+'\n')['files'], [])


class FileEditingTests(unittest.TestCase):
    def setUp(self):
        self.engine = Engine(False)
        self.send('turn/started', {'threadId': 't', 'turn': {'id': 'u'}}, 100)

    def tearDown(self):
        self.engine.close()

    def value(self, value, at, direction='protocol_out'):
        self.engine.ingest(Event('b', direction, filter_event(direction, value), at, 0))

    def send(self, method, params, at, request=None):
        value = {'method': method, 'params': params}
        if request is not None:
            value['id'] = request
        self.value(value, at)

    def model(self, name='apply_patch', item_id='input', call_id=None, code=None, at=101):
        item = {'id': item_id, 'type': 'custom_tool_call', 'name': name, 'metadata': {'turn_id': 'u'}}
        if call_id:
            item['call_id'] = call_id
        if code is not None:
            item['input'] = code
        self.value({'type': 'response.output_item.done' if code is not None else 'response.output_item.added',
                    'item': item}, at, 'upstream')

    def delta(self, text, at=102, item_id='input'):
        self.value({'type': 'response.custom_tool_call_input.delta', 'item_id': item_id, 'delta': text}, at, 'upstream')

    def file(self, item_id='input', at=104, ending=False, status='completed'):
        self.send('item/completed' if ending else 'item/started', {'threadId': 't', 'turnId': 'u', 'item': {
            'id': item_id, 'type': 'fileChange', 'status': status if ending else 'inProgress',
            'changes': [{'path': 'src/first.py', 'diff': 'PRIVATE_DIFF'}, {'path': 'tests/second.py'}]}}, at)

    def view(self, at):
        self.engine.tick(at)
        return self.engine.snapshot(at, time.time())['threads'][0]

    def visual(self, at):
        self.view(at)
        out = io.StringIO()
        Console(file=out, width=160).print(render(self.engine.snapshot(at, time.time()), detail=True)[0])
        return out.getvalue()

    def test_direct_generation_updates_file_without_claiming_execution_or_saving_patch(self):
        self.model()
        self.assertEqual(self.view(101.1)['display_label'], '编辑文件')
        self.delta('*** Begin Patch\n*** Update File: src/fir')
        self.assertEqual(self.view(102.1)['display_label'], '编辑文件')
        self.delta('st.py\n@@\n+NEW_PRIVATE\n', 103)
        view = self.view(103.1)
        self.assertEqual(view['display_label'], '编辑中 · first.py')
        self.assertEqual(view['phase'], 'tool_input')
        self.assertEqual(view['activities'][0]['kind'], 'tool_input')
        self.delta('*** Add File: tests/second.py\n+PRIVATE_BODY\n*** End Patch\n', 104)
        self.assertEqual(self.view(104.1)['display_label'], '编辑中 · second.py（另 1 个）')
        self.assertNotIn('PRIVATE', json.dumps(self.view(104.2)))
        self.assertNotIn('apply_patch', self.visual(104.2))
        self.model(code=PATCH, at=105)
        view = self.view(106)
        self.assertEqual(view['phase'], 'tool_ready')
        self.assertEqual(view['display_label'], '编辑中 · first.py（另 1 个）')
        stream = self.engine.streams[('b', 'input', 'tool_input', 0)]
        self.assertEqual(stream.tokens, count_text(PATCH, 'o200k_base')[0])
        self.assertEqual(stream.text, '')

    def test_code_mode_editing_and_later_command_use_their_own_targets(self):
        code = 'tools.apply_patch('+json.dumps(PATCH)+');'
        self.model(name='functions.exec')
        self.delta(code)
        self.assertEqual(self.view(103)['display_label'], '编辑中 · second.py（另 1 个）')
        self.delta('tools.exec_command({cmd:"sleep 3"});', 104)
        self.assertEqual(self.view(105)['display_label'], '准备中 · sleep')
        self.assertNotIn('PRIVATE', json.dumps(self.view(105)))

    def test_same_id_execution_and_late_input_completion_preserve_canonical_state(self):
        self.model()
        self.delta(PATCH)
        self.file(at=104)
        view = self.view(105)
        self.assertEqual(view['phase'], 'tool')
        self.assertEqual(view['display_label'], '编辑中 · first.py（另 1 个）')
        self.assertEqual(view['display_active_count'], 1)
        self.assertEqual(view['display_seconds'], 1)
        self.model(code=PATCH, at=106)
        self.assertEqual(self.view(107)['phase'], 'tool')
        self.file(at=108)  # Duplicate start does not reset execution duration.
        self.assertEqual(self.view(109)['display_seconds'], 5)
        self.file(at=110, ending=True)
        self.model(code=PATCH, at=111)  # Duplicate model end cannot reopen editing.
        view = self.view(111.1)
        self.assertEqual(view['display_active_count'], 0)
        self.assertEqual(view['activities'][0]['duration_seconds'], 6)
        self.assertNotIn('编辑中', self.visual(111.1))

    def test_explicit_call_id_hides_preparation_and_retains_one_visible_edit(self):
        self.model(call_id='call')
        self.delta(PATCH)
        self.model(call_id='call', code=PATCH, at=103)
        self.file(item_id='call', at=104)
        view = self.view(105)
        self.assertEqual(view['display_active_count'], 1)
        self.assertEqual([a['id'] for a in view['activities'] if a['display_visible']], ['call'])
        self.file(item_id='call', at=106, ending=True)
        view = self.view(106.1)
        self.assertEqual(view['display_active_count'], 0)
        self.assertEqual(view['display_label'], '刚完成 · 编辑文件 · first.py（另 1 个）')
        self.assertNotIn('编辑中', self.visual(106.1))

    def test_file_names_do_not_create_an_identity_link(self):
        self.model(call_id='different-call')
        self.delta(PATCH)
        self.file(item_id='call', at=104)
        self.assertEqual(self.view(105)['display_active_count'], 2)

    def test_code_mode_wrapper_does_not_keep_claiming_editing_after_inner_completion(self):
        code = 'tools.apply_patch('+json.dumps(PATCH)+');'
        self.model(name='exec', code=code, at=101)
        self.file(item_id='inner', at=102)
        self.assertEqual(self.view(103)['display_active_count'], 1)
        self.file(item_id='inner', at=104, ending=True)
        self.assertEqual(self.view(105)['display_label'], '处理中')
        self.assertNotIn('编辑中', self.visual(105))

    def test_patch_progress_and_legacy_render_use_bounded_file_metadata(self):
        self.model()
        self.delta(PATCH)
        self.send('item/fileChange/patchUpdated', {'threadId': 't', 'turnId': 'u', 'itemId': 'input',
            'changes': [{'path': 'src/canonical.py', 'diff': 'PRIVATE_DIFF'}]}, 103)
        self.assertEqual(self.view(103.1)['phase'], 'tool_input')
        self.file(at=104)
        self.send('item/fileChange/patchUpdated', {'threadId': 't', 'turnId': 'u', 'itemId': 'input',
            'changes': [{'path': 'src/canonical.py', 'diff': 'PRIVATE_DIFF'}]}, 105)
        snapshot = self.engine.snapshot(106, time.time())
        thread = snapshot['threads'][0]
        thread.pop('display_message')
        for activity in thread['activities']:
            activity.pop('display_message')
        output = io.StringIO()
        Console(file=output, width=160).print(render(snapshot, detail=True)[0])
        self.assertIn('编辑中 · canonical.py', output.getvalue())
        self.assertNotIn('PRIVATE', json.dumps(snapshot))

    def test_approval_parallel_work_and_failed_edit_keep_real_status(self):
        self.model(call_id='call', code=PATCH, at=101)
        self.file(item_id='call', at=102)
        self.send('item/fileChange/requestApproval', {'threadId': 't', 'turnId': 'u', 'itemId': 'call'}, 103, 7)
        self.assertEqual(self.view(104)['display_label'], '已暂停')
        self.send('item/started', {'threadId': 't', 'turnId': 'u', 'item': {'id': 'cmd', 'type': 'commandExecution',
            'command': 'sleep 3', 'source': 'unifiedExecStartup', 'processId': '1'}}, 105)
        self.assertEqual(self.view(106)['display_label'], '运行中 · sleep')
        self.send('serverRequest/resolved', {'threadId': 't', 'requestId': 7}, 107)
        self.file(item_id='call', at=108, ending=True, status='failed')
        detail = next(a for a in self.view(109)['activities'] if a['id'] == 'call')
        self.assertEqual(detail['status'], 'failed')
        self.assertFalse(detail['active'])
        self.assertNotIn('编辑中', detail['display_label'])

    def test_completion_without_start_has_unknown_execution_duration(self):
        self.model(code=PATCH, at=101)
        self.file(at=104, ending=True)
        view = self.view(104.1)
        self.assertEqual(view['display_active_count'], 0)
        self.assertFalse(view['activities'][0]['start_known'])
        self.assertEqual(view['display_label'], '刚完成 · 编辑文件 · first.py（另 1 个）')


if __name__ == '__main__':
    unittest.main()
