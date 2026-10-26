"""Usability requirements exercised through filtering, snapshots and a real PTY."""

import copy
import io
import json
import os
from pathlib import Path
import pty
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from rich.console import Console

from codex_activity_monitor.cli import main
from codex_activity_monitor.display import input_keys, render, seconds
from codex_activity_monitor.engine import Engine
from codex_activity_monitor.events import Event, filter_event
from codex_activity_monitor.i18n import CATALOGS, message, register_catalog, render_message, tr
from codex_activity_monitor.messages_zh_cn import MESSAGES
from codex_activity_monitor.runtime import atomic_json, save_ui_language, ui_language

ROOT = Path(__file__).resolve().parents[1]


def test_catalog():
    catalog = dict(MESSAGES)
    catalog.update({'activity.preparing': 'Preparing{target}', 'activity.running': 'Running{target}',
                    'activity.unknown': 'Unavailable', 'activity.disconnected': 'Disconnected',
                    'table.chat': 'Chat / project', 'table.activity': 'Activity',
                    'view.local': 'Local progress', 'view.header': {
                        'one': 'Codex Activity Monitor · {state} · {count} chat',
                        'other': 'Codex Activity Monitor · {state} · {count} chats'}})
    return catalog


class UsabilityTests(unittest.TestCase):
    def setUp(self):
        self.engine = Engine(False)
        self.send('turn/started', {'threadId': 't', 'turn': {'id': 'u'}}, 100)

    def tearDown(self):
        self.engine.close()
        CATALOGS.pop('test-en', None)

    def send(self, method, params, at):
        self.engine.ingest(Event('b', 'protocol_out', filter_event('protocol_out', {'method': method, 'params': params}), at, 0))

    def item(self, item, at, ending=False):
        self.send('item/completed' if ending else 'item/started', {'threadId': 't', 'turnId': 'u', 'item': item}, at)

    def snapshot(self, at):
        self.engine.tick(at)
        return self.engine.snapshot(at, time.time())

    def visual(self, snapshot, language='zh-CN'):
        output = io.StringIO()
        Console(file=output, width=160).print(render(snapshot, detail=True, language=language)[0])
        return output.getvalue()

    def test_time_boundaries_and_completion_do_not_show_fractional_seconds(self):
        cases = [(None, '—'), (0, ''), (.999, ''), (1, '1s'), (59.99, '59s'),
                 (60, '1m00s'), (3599.99, '59m59s'), (3600, '1h00m00s'), (3723.9, '1h02m03s')]
        for value, expected in cases:
            self.assertEqual(seconds(value), expected)
        command = {'id': 'cmd', 'type': 'commandExecution', 'command': '.local/venv/bin/python PRIVATE.py --secret',
                   'source': 'unifiedExecStartup', 'processId': '1'}
        self.item(command, 101)
        self.item({**command, 'status': 'completed', 'exitCode': 0}, 101.5, ending=True)
        early, later = self.snapshot(101.6), self.snapshot(110)
        self.assertEqual(early['threads'][0]['display_label'], '刚完成 · python')
        activity = later['threads'][0]['activities'][0]
        self.assertEqual(activity['duration_seconds'], .5)
        self.assertEqual(activity['display_label'], 'python')
        for snapshot in (early, later):
            visual = self.visual(snapshot)
            for hidden in ('PRIVATE.py', '--secret', '.local/venv', '运行中', '0.5s'):
                self.assertNotIn(hidden, visual)

    def test_mcp_display_metadata_arrives_without_restarting_the_activity(self):
        raw = {'id': 'mcp', 'type': 'mcpToolCall', 'server': 'technical_server', 'tool': 'search',
               'arguments': {'token': 'PRIVATE_ARGUMENTS'}}
        self.item(raw, 101)
        before = self.snapshot(103)['threads'][0]['activities'][0]
        self.assertEqual(before['display_label'], '运行中 · technical_server · search')
        self.item({**raw, 'appContext': {'appName': 'Example Workspace', 'actionName': 'Find documents', 'secret': 'PRIVATE_CONTEXT'}}, 104)
        snapshot = self.snapshot(105)
        after = snapshot['threads'][0]['activities'][0]
        self.assertEqual(after['id'], before['id'])
        self.assertEqual(after['duration_seconds'], 4)
        self.assertEqual(after['name_source'], {'product': 'display', 'tool': 'display'})
        self.assertEqual(after['display_label'], '运行中 · Example Workspace · Find documents')
        self.assertNotIn('PRIVATE', json.dumps(snapshot))

    def test_preparation_ready_and_execution_keep_distinct_evidence(self):
        self.engine.ingest(Event('b', 'upstream', {'type': 'response.output_item.added', 'item': {
            'id': 'call', 'type': 'function_call', 'name': 'exec_command', 'metadata': {'turn_id': 'u'}}}, 101, 0))
        self.engine.ingest(Event('b', 'upstream', {'type': 'response.function_call_arguments.delta', 'item_id': 'call',
            'delta': '{"cmd":".local/venv/bin/py'}, 102, 0))
        self.assertEqual(self.snapshot(102.1)['threads'][0]['display_label'], '准备中')
        self.engine.ingest(Event('b', 'upstream', {'type': 'response.function_call_arguments.delta', 'item_id': 'call',
            'delta': 'thon PRIVATE.py"}'}, 103, 0))
        self.assertEqual(self.snapshot(103.1)['threads'][0]['display_label'], '准备中 · python')
        self.engine.ingest(Event('b', 'upstream', {'type': 'response.output_item.done', 'item': {
            'id': 'call', 'type': 'function_call', 'name': 'exec_command', 'metadata': {'turn_id': 'u'},
            'arguments': '{"cmd":".local/venv/bin/python PRIVATE.py"}'}}, 104, 0))
        self.assertEqual(self.snapshot(104.1)['threads'][0]['display_label'], '处理中 · python')
        self.item({'id': 'cmd', 'type': 'commandExecution', 'source': 'unifiedExecStartup', 'processId': '1',
                   'command': '.local/venv/bin/python PRIVATE.py'}, 105)
        snapshot = self.snapshot(107)
        self.assertEqual(snapshot['threads'][0]['display_label'], '运行中 · python')
        self.assertNotIn('PRIVATE', self.visual(snapshot))

    def test_language_and_rewording_do_not_change_state_or_rates(self):
        register_catalog('test-en', test_catalog())
        self.item({'id': 'cmd', 'type': 'commandExecution', 'source': 'unifiedExecStartup', 'processId': '1', 'command': 'sleep 3'}, 101)
        snapshot = self.snapshot(103)
        saved = copy.deepcopy(snapshot)
        self.assertIn('运行中 · sleep', self.visual(snapshot))
        self.assertIn('Running · sleep', self.visual(snapshot, 'test-en'))
        self.assertIn('1 chat', self.visual(snapshot, 'test-en'))
        self.assertEqual(snapshot, saved)
        with patch.dict(MESSAGES, {'activity.unknown': 'changed unknown', 'activity.disconnected': 'changed disconnected'}):
            self.engine.backends['b']['connected'] = False
            view = self.snapshot(104)['threads'][0]
            self.assertEqual(view['display_active_count'], 0)
            self.assertIsNone(view['generation_rate'])
            self.assertEqual(view['display_message']['key'], 'activity.disconnected')
        stale = copy.deepcopy(saved)
        stale['updated_at'] = time.time()-10
        self.assertIn('Disconnected', self.visual(stale, 'test-en'))
        self.assertNotIn('Running', self.visual(stale, 'test-en'))

    def test_legacy_command_snapshots_hide_parameters_too(self):
        self.item({'id': 'cmd', 'type': 'commandExecution', 'source': 'unifiedExecStartup', 'processId': '1',
                   'command': '/opt/python PRIVATE.py --secret'}, 101)
        snapshot = self.snapshot(103)
        thread = snapshot['threads'][0]
        thread.pop('display_message')
        for activity in thread['activities']:
            for field in ('display_message', 'command_name', 'item_type'):
                activity.pop(field, None)
            activity['display_label'] = 'OLD LABEL PRIVATE'
        self.assertIn('运行中 · python', self.visual(snapshot))
        self.assertNotIn('PRIVATE', self.visual(snapshot))
        thread.update(status='completed', phase='completed', display_label='OLD COMPLETION PRIVATE')
        thread['activities'][0].update(active=False, status='completed')
        self.assertIn('任务完成', self.visual(snapshot))
        self.assertNotIn('PRIVATE', self.visual(snapshot))

    def test_settings_priority_errors_and_catalog_completeness(self):
        register_catalog('test-en', test_catalog())
        self.assertEqual(tr('view.header', 'test-en', state='Local', count=2), 'Codex Activity Monitor · Local · 2 chats')
        self.assertEqual(render_message(message('not.a.real.key'), 'test-en'), '处理中')
        with self.assertRaises(ValueError):
            register_catalog('bad', {'activity.running': 'Running'})
        wrong = test_catalog()
        wrong['activity.running'] = 'Running {wrong_parameter}'
        with self.assertRaises(ValueError):
            register_catalog('bad', wrong)
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            self.assertEqual(ui_language(runtime), 'zh-CN')
            save_ui_language(runtime, 'test-en')
            self.assertEqual(ui_language(runtime), 'test-en')
            self.assertEqual(ui_language(runtime, 'zh-CN'), 'zh-CN')
            self.assertEqual(ui_language(runtime), 'test-en')
            self.assertEqual((runtime/'ui-settings.json').stat().st_mode & 0o777, 0o600)
            for content in ('broken', '[]', '{"version":1,"language":"missing"}', '{"version":1,"language":{}}'):
                (runtime/'ui-settings.json').write_text(content)
                self.assertEqual(ui_language(runtime), 'zh-CN')
            with self.assertRaises(ValueError):
                ui_language(runtime, 'missing')
        self.assertIn('en', CATALOGS)

    def test_cli_once_and_published_english(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as tmp:
            runtime = Path(tmp)
            from codex_activity_monitor.state_transport import StateServer
            from codex_activity_monitor.publication import DetachedSnapshot
            server=StateServer(runtime/'state.sock')
            self.addCleanup(server.close)
            server.publish(DetachedSnapshot.capture({**self.snapshot(103), 'running': True}))
            output = io.StringIO()
            with patch('sys.stdout', output):
                self.assertEqual(main(['--runtime', tmp, 'watch', '--once', '--lang', 'zh-CN']), 0)
            self.assertIn('当前活动', output.getvalue())
            self.assertIn('l 切换语言', output.getvalue())
            error = io.StringIO()
            with patch('sys.stderr', error):
                self.assertEqual(main(['--runtime', tmp, 'watch', '--once', '--lang', 'missing']), 1)
            self.assertIn('不支持的界面语言', error.getvalue())


class WatchPTYTests(unittest.TestCase):
    def test_real_cli_json_continuous_output_is_one_hz(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            atomic_json(runtime/'snapshot.json', {'updated_at': time.time(), 'threads': []})
            process = subprocess.Popen([sys.executable, '-m', 'codex_activity_monitor.cli', '--runtime', tmp,
                                        'watch', '--json'], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                stamps, buffer = [], b''
                deadline = time.monotonic()+4
                while len(stamps) < 3 and time.monotonic() < deadline:
                    if select.select([process.stdout], [], [], .1)[0]:
                        data = os.read(process.stdout.fileno(), 65536)
                        if not data:
                            break
                        buffer += data
                        while b'\n' in buffer:
                            line, buffer = buffer.split(b'\n', 1)
                            self.assertEqual(json.loads(line)['threads'], [])
                            stamps.append(time.monotonic())
                self.assertEqual(len(stamps), 3)
                for a, b in zip(stamps, stamps[1:]):
                    self.assertGreaterEqual(b-a, .9)
                    self.assertLess(b-a, 1.5)
                process.send_signal(signal.SIGINT)
                process.wait(timeout=1)
                self.assertEqual(process.returncode, 0)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=3)
                process.stdout.close()
                process.stderr.close()

    def wait_for(self, path, count, master, process, timeout=3):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            if select.select([master], [], [], .03)[0]:
                os.read(master, 65536)
            if path.exists() and len(path.read_text().splitlines()) >= count:
                return
            if process.poll() is not None:
                self.fail('watch exited before the expected refresh')
        self.fail('watch did not refresh in time')

    def test_real_terminal_schedule_keys_language_persistence_and_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            atomic_json(runtime/'snapshot.json', {'updated_at': time.time(), 'threads': []})
            reads = runtime/'reads.log'
            master, slave = pty.openpty()
            script = '''
import sys,time
from pathlib import Path
import codex_activity_monitor.display as display
from codex_activity_monitor.i18n import register_catalog
from codex_activity_monitor.messages_zh_cn import MESSAGES
register_catalog('test-en', dict(MESSAGES))
runtime=Path(sys.argv[1])
read=display.current_state
def tracked(path):
    with (runtime/'reads.log').open('a') as out:
        out.write(str(time.monotonic())+'\\n')
    return read(path)
display.current_state=tracked
display.watch(runtime)
'''
            process = subprocess.Popen([sys.executable, '-c', script, tmp], cwd=ROOT,
                                       stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
            os.close(slave)
            try:
                self.wait_for(reads, 1, master, process)
                self.wait_for(reads, 3, master, process)
                stamps = [float(v) for v in reads.read_text().splitlines()]
                self.assertEqual(len(stamps), 3)
                for a, b in zip(stamps, stamps[1:]):
                    self.assertGreaterEqual(b-a, .9)
                    self.assertLess(b-a, 1.5)
                sent = time.monotonic()
                os.write(master, b'\n')
                self.wait_for(reads, 4, master, process, timeout=.4)
                self.assertLess(float(reads.read_text().splitlines()[3])-sent, .3)
                os.write(master, b'l')
                self.wait_for(reads, 5, master, process, timeout=.4)
                self.assertEqual(json.loads((runtime/'ui-settings.json').read_text())['language'], 'en')
                sent = time.monotonic()
                os.write(master, b'q')
                process.wait(timeout=.5)
                self.assertEqual(process.returncode, 0)
                self.assertLess(time.monotonic()-sent, .5)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=3)
                os.close(master)

    def test_split_arrow_sequences(self):
        keys, remaining = input_keys('\x1b[')
        self.assertEqual(keys, [])
        keys, remaining = input_keys(remaining+'Aj\x1b[B')
        self.assertEqual(keys, ['k', 'j', 'j'])
        self.assertEqual(remaining, '')


if __name__ == '__main__':
    unittest.main()
