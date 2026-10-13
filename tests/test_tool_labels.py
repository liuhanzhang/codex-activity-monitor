import shlex
import unittest

from codex_progress_monitor.tool_labels import MAX_CODE_PREFIX, code_tool_names, command_previews, command_summary


class ToolLabelTests(unittest.TestCase):
    def test_command_preview_is_incremental_and_only_at_command_argument_root(self):
        code = 'const unrelated={cmd:"SECRET"}; tools.exec_command({env:{cmd:"SECRET"},cmd:"rg --fi'
        self.assertEqual(command_previews('exec', code), [
            {'tool_name':'exec_command','command':'rg --fi…','complete':False}])
        self.assertEqual(command_previews('exec', code + 'les"});'), [
            {'tool_name':'exec_command','command':'rg --files','complete':True}])
        for code in ('tools.exec_command({cmd: variable});',
                     'tools.other({cmd:"SECRET"});',
                     'const s="tools.exec_command({cmd: hidden})";',
                     '// tools.exec_command({cmd:"SECRET"});',
                     'tools.exec_command(args);',
                     'other.tools.exec_command({cmd:"SECRET"});'):
            with self.subTest(code=code):
                self.assertEqual(command_previews('exec',code), [])

    def test_command_literal_escapes_and_incomplete_escape_boundaries(self):
        prefix='tools.exec_command({cmd:"rg \\u4e'
        self.assertEqual(command_previews('exec',prefix)[0]['command'],'rg …')
        self.assertEqual(command_previews('exec',prefix+'2d\\u6587"});')[0]['command'],'rg 中文')
        self.assertEqual(command_previews('exec','tools.exec_command({cmd:"rg \\ud83d')[0]['command'],'rg …')
        self.assertEqual(command_previews('exec','tools.exec_command({cmd:"rg \\ud83d\\ude42"});')[0]['command'],'rg 🙂')
        self.assertEqual(command_previews('exec',r'tools.exec_command({cmd:"python3 -c \"print(1)\"\nnext"});')[0]['command'], 'python3 -c "print(1)"')
        self.assertEqual(command_previews('exec','tools.exec_command({cmd:"rg\\')[0]['command'],'rg…')
        self.assertEqual(command_previews('exec','tools.exec_command({cmd:"\\udc00"});'), [])

    def test_direct_json_multiple_commands_and_dynamic_suffixes(self):
        self.assertEqual(command_previews('functions.exec_command','{"cmd":"git status"}')[0]['command'],'git status')
        self.assertFalse(command_previews('exec_command','{"cmd":"git status')[0]['complete'])
        code='await Promise.all([tools.exec_command({cmd:"git status"}), tools.exec_command({cmd:"rg --files"})]);'
        self.assertEqual([p['command'] for p in command_previews('exec',code)], ['git status','rg --files'])
        self.assertEqual(command_previews('exec','tools.exec_command({cmd:"git " + branch});')[0]['command'],'git …')
        self.assertEqual(command_previews('exec','tools.exec_command({cmd:`git ${branch}`});')[0]['command'],'git …')
        self.assertEqual(command_previews('exec','tools.exec_command({cmd:"git status",...options});'), [])
        self.assertEqual(command_previews('exec','tools.exec_command({cmd:"git status",cmd:variable});'), [])
        self.assertEqual(command_previews('shell_command','{"command":"git status"}')[0]['command'],'git status')

    def test_preview_summary_limits_and_shell_wrapper(self):
        code='tools.exec_command({cmd:"'+('r'*1000)+'"});'
        self.assertEqual(len(command_previews('exec',code)[0]['command']),160)
        self.assertTrue(command_previews('exec',code)[0]['command'].endswith('…'))
        code='tools.exec_command({cmd:"/bin/zsh -lc \\"rg --files\\""});'
        self.assertEqual(command_previews('exec',code)[0]['command'],'rg --files')
        self.assertEqual(len(command_previews('exec',';'.join('tools.exec_command({cmd:"rg"})' for _ in range(20)))),6)

    def test_only_visible_direct_calls_are_names(self):
        code = '''
            const text = "tools.secret_tool()";
            const unfinishedTemplate = `tools.template_tool()`;
            const regex = /tools.regex_tool\\(/;
            // tools.comment_tool()
            /* tools.block_tool() */
            await Promise.all([tools.exec_command({cmd: "PRIVATE_PARAMETERS"}),
                               tools.mcp__web__run({})]);
            tools.exec_command({}); other.tools.fake({});
            tools[dynamicName]({});
        '''
        self.assertEqual(code_tool_names(code), ['exec_command', 'mcp__web__run'])

    def test_name_waits_for_call_syntax_and_handles_split_literals(self):
        self.assertEqual(code_tool_names('await tools.exec_com'), [])
        self.assertEqual(code_tool_names('await tools.exec_command'), [])
        self.assertEqual(code_tool_names('await tools.exec_command('), ['exec_command'])
        for prefix in ('"', "'", '`', '/*', '//', '/'):
            with self.subTest(prefix=prefix):
                self.assertEqual(code_tool_names(prefix + 'tools.hidden('), [])
        self.assertEqual(code_tool_names('const s = `${`tools.hidden()`}`;'), [])

    def test_limits_work_and_retained_names(self):
        self.assertEqual(code_tool_names(' ' * MAX_CODE_PREFIX + 'tools.hidden()'), [])
        self.assertEqual(len(code_tool_names(';'.join('tools.tool%d()' % i for i in range(20)))), 6)

    def test_large_unfinished_literals_do_not_expose_their_contents(self):
        for prefix in ('"', "'", '`', '/'):
            code = 'tools.visible(); ' + prefix + ('[tools.hidden()] ' * 3000) + '\\'
            with self.subTest(prefix=prefix):
                self.assertEqual(code_tool_names(code), ['visible'])

    def test_unwrap_before_first_line_and_length_limits(self):
        for shell in ('/bin/zsh', '/bin/bash', '/bin/sh'):
            command = "rg -n 'tool name' src\nprintf 'done'"
            self.assertEqual(command_summary(shlex.join([shell, '-lc', command])), "rg -n 'tool name' src")
        self.assertEqual(command_summary('/bin/bash --login -c "git status --short"'), 'git status --short')
        self.assertEqual(command_summary('python3 -c "print(1)"'), 'python3 -c "print(1)"')
        self.assertEqual(command_summary('rg --files "unterminated'), 'rg --files "unterminated')
        self.assertEqual(command_summary('/bin/zsh -lc "rg --files src'), 'rg --files src…')
        self.assertEqual(len(command_summary('rg ' + 'x' * 500)), 160)
        self.assertTrue(command_summary('rg ' + 'x' * 500).endswith('…'))


if __name__ == '__main__':
    unittest.main()
