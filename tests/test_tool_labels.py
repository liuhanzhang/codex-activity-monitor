import shlex
import unittest

from codex_activity_monitor.tool_labels import MAX_CODE_PREFIX, code_tool_names, command_name, command_previews, command_summary, tool_display


class ToolLabelTests(unittest.TestCase):
    def test_command_preview_is_incremental_and_only_at_command_argument_root(self):
        code = 'const unrelated={cmd:"SECRET"}; tools.exec_command({env:{cmd:"SECRET"},cmd:"rg --fi'
        self.assertEqual(command_previews('exec', code), [
            {'tool_name':'exec_command','command':'rg --fi…','complete':False,'command_name':'rg'}])
        self.assertEqual(command_previews('exec', code + 'les"});'), [
            {'tool_name':'exec_command','command':'rg --files','complete':True,'command_name':'rg'}])
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

    def test_command_name_strips_paths_arguments_and_known_wrappers(self):
        cases = {
            '.local/venv/bin/python SECRET.py --verbose': 'python',
            '/usr/bin/python3 -c "SECRET"': 'python3',
            '/bin/zsh -lc \'rg --files SECRET\'': 'rg',
            'DEBUG=1 .local/venv/bin/python SECRET.py': 'python',
            'env -i --unset DEBUG DEBUG=1 python SECRET.py': 'python',
            'sudo -n -u root env DEBUG=1 /opt/bin/python SECRET.py': 'python',
            'cd "directory with spaces" && python SECRET.py': 'python',
            'cd -- project; python SECRET.py': 'python',
            'git status && pytest -q': 'git',
            'git status | head -n 1': 'git',
            '"/path with spaces/python" SECRET': 'python',
            '/bin/bash --login -c \'env DEBUG=1 python SECRET\'': 'python',
            'python SECRET > output': 'python',
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(command_name(source), expected)
        self.assertEqual(command_name(['/bin/zsh', '-lc', 'rg --files']), 'rg')

    def test_command_name_partial_dynamic_and_unknown_wrappers(self):
        for source in ('$PROGRAM SECRET', '`program` SECRET', 'python${VERSION} SECRET',
                       '.local/venv/bin/py…', 'env --unknown SECRET python', 'sudo -x SECRET python'):
            with self.subTest(source=source):
                self.assertIsNone(command_name(source))
        self.assertIsNone(command_name('.local/venv/bin/py', complete=False))
        self.assertEqual(command_name('.local/venv/bin/python ', complete=False), 'python')
        self.assertEqual(command_name('python -c "unfinished', complete=False), 'python')
        self.assertEqual(command_name('/bin/zsh -lc "rg --fi', complete=False), 'rg')
        self.assertIsNone(command_name('/bin/zsh -lc "rg', complete=False))
        self.assertIsNone(command_previews('exec_command', '{"cmd":"py')[0]['command_name'])
        self.assertEqual(command_previews('exec_command', '{"cmd":"python -')[0]['command_name'], 'python')
        self.assertIsNone(command_previews('exec_command', '{"cmd":"python" + name}')[0]['command_name'])

    def test_provider_names_and_protocol_fallback_use_one_rule(self):
        for raw in ('mcp__example__search', 'mcp__example.search', 'example/search'):
            with self.subTest(raw=raw):
                display = tool_display(raw)
                self.assertEqual((display['product_name'], display['tool_display_name']), ('example', 'search'))
                named = tool_display(raw, {'appName': 'Friendly Product', 'actionName': 'Find documents'})
                self.assertEqual((named['product_name'], named['tool_display_name']), ('Friendly Product', 'Find documents'))
                self.assertEqual(named['name_source'], {'product': 'display', 'tool': 'display'})
        nested = tool_display('mcp__host__app__search')
        self.assertEqual(nested['product_name'], 'host · app')
        self.assertEqual(tool_display('mcp__cua_repl__js')['product_name'], 'cua_repl')
        self.assertEqual(tool_display('exec')['tool_display_name'], '')
        self.assertEqual(tool_display('mcp__example__search', {'appName': 'Product\x1b\nName'})['product_name'], 'ProductName')


if __name__ == '__main__':
    unittest.main()
