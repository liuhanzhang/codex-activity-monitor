"""English publication, source boundaries, preference isolation and generated resources."""
import copy
import io
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from codex_progress_monitor.app_control import AppController, ControlError
from codex_progress_monitor.cli import main
from codex_progress_monitor.history import item_content, raw_section
from codex_progress_monitor.i18n import CATALOGS, message, render_message, tr
from codex_progress_monitor.runtime import save_ui_language, ui_language
from codex_progress_monitor.timeline import display_phase, item_phase

ROOT = Path(__file__).resolve().parents[1]


class LocalizationTests(unittest.TestCase):
    def test_english_is_complete_and_formatting_is_language_safe(self):
        self.assertEqual(set(CATALOGS), {'zh-CN', 'en'})
        self.assertEqual(set(CATALOGS['en']), set(CATALOGS['zh-CN']))
        for count, noun in [(0, 'chats'), (1, 'chat'), (2, 'chats')]:
            self.assertEqual(tr('view.header', 'en', state='Local', count=count),
                             f'Codex Progress · Local · {count} {noun}')
        value = message('activity.recent', activity=message('activity.file_editing_more', target='中文.py', count=1))
        before = copy.deepcopy(value)
        self.assertEqual(render_message(value, 'en'), 'Just completed · Editing · 中文.py (+1 more)')
        self.assertEqual(value, before)
        self.assertEqual(tr('time.hours', 'en', hours=1, minutes=2, seconds=3), '1h02m03s')
        for invalid in [None, {}, {'key': []}, message('missing.key'), {'key': 'activity.named', 'params': []}, {'key': 'activity.named', 'params': {'locale': 'bad', '_depth': 0, 1: 'bad'}}]:
            self.assertEqual(render_message(invalid, 'en'), 'Processing')
        self.assertEqual(tr('activity.named', 'en'), 'Processing')
        for invalid in [[], {}, True, None]:
            self.assertEqual(tr('activity.running', 'en', target=invalid), 'Processing')
        self.assertEqual(tr('activity.named', 'en', name={'internal_key': 'must not leak'}), 'Processing')
        cyclic = message('activity.recent')
        cyclic['params']['activity'] = cyclic
        self.assertLess(len(render_message(cyclic, 'en')), 200)

    def test_generated_resources_are_complete_and_current(self):
        subprocess.run([str(ROOT/'bin/export-localizations'), '--check'], check=True, capture_output=True)

    def test_preferences_merge_serialize_and_do_not_create_integration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'gui'
            control = AppController(root)
            control.set_preferences(False, 'zh-CN')
            prefs = json.loads(control.preferences.read_text())
            prefs['custom'] = 'preserve'
            control.preferences.write_text(json.dumps(prefs))
            errors = []
            def save(**kwargs):
                try:
                    AppController(root).set_preferences(**kwargs)
                except Exception as exc:
                    errors.append(exc)
            a = threading.Thread(target=save, kwargs={'language': 'en'})
            b = threading.Thread(target=save, kwargs={'launch_at_login': True})
            a.start(); b.start(); a.join(); b.join()
            self.assertEqual(errors, [])
            self.assertEqual(json.loads(control.preferences.read_text()), {
                'version': 1, 'language': 'en', 'launch_at_login': True, 'custom': 'preserve'})
            self.assertEqual(control.preferences.stat().st_mode & 0o777, 0o600)
            for name in ['integration.json', 'observation.json', 'integration.enabled']:
                self.assertFalse((root/name).exists())
            runtime = Path(tmp)/'terminal'; runtime.mkdir()
            save_ui_language(runtime, 'zh-CN')
            self.assertEqual(json.loads(control.preferences.read_text())['language'], 'en')
            (runtime/'ui-settings.json').write_text('{"version":true,"language":"en"}')
            self.assertEqual(ui_language(runtime), 'zh-CN')
            control.preferences.write_text('broken')
            self.assertTrue(control.status()['ok'])
            control.set_preferences(language='en')
            self.assertEqual(json.loads(control.preferences.read_text())['language'], 'en')

    def test_management_error_retains_code_and_structured_context(self):
        unknown = ControlError('future_error', 'Original diagnostic')
        self.assertEqual(str(unknown), 'Original diagnostic')
        self.assertIsNone(unknown.display_message)
        error = ControlError('environment_read_failed', 'control.environment_read_failed', value='TEST_KEY')
        self.assertEqual(error.code, 'environment_read_failed')
        self.assertEqual(str(error), '无法读取启动配置：TEST_KEY')
        self.assertEqual(render_message(error.display_message, 'en'), 'Cannot read launch setting: TEST_KEY')
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as root, patch('sys.stdout', output):
            self.assertEqual(main(['app', 'configure', '--state-protocol', '1', '--data-root', root]), 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result['code'], 'app_missing')
        self.assertEqual(render_message(result['display_message'], 'en'), 'Configuration requires the Monitor App path.')

    def test_cli_english_and_machine_output_preserve_source_content(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as tmp:
            snapshot = {'schema_version': 1, 'running': True, 'updated_at': 0, 'threads': [], 'source': '原始内容'}
            from codex_progress_monitor.state_transport import StateServer
            from codex_progress_monitor.publication import DetachedSnapshot
            server = StateServer(Path(tmp)/'state.sock')
            self.addCleanup(server.close)
            server.publish(DetachedSnapshot.capture(snapshot))
            for language in ['zh-CN', 'en']:
                output = io.StringIO()
                with patch('sys.stdout', output):
                    self.assertEqual(main(['--runtime', tmp, '--lang', language, 'watch', '--once', '--json']), 0)
                result = json.loads(output.getvalue())
                self.assertEqual({key: result[key] for key in snapshot}, snapshot)
                self.assertTrue(result['connection']['healthy'])
            output = io.StringIO()
            with patch('sys.stdout', output):
                self.assertEqual(main(['--runtime', tmp, 'watch', '--once', '--lang', 'en']), 0)
            self.assertIn('Waiting for a Codex task.', output.getvalue())
            self.assertNotIn('当前活动', output.getvalue())

    def test_source_sections_and_tool_labels_keep_content_with_message_metadata(self):
        for kind, expected in [('userMessage', 'User input'), ('reasoning', 'Visible reasoning summary')]:
            item = {'type': kind, 'text': '原始输入', 'summary_text': '原始输入'}
            sections, _ = item_content(item)
            self.assertEqual(render_message(sections[0]['title_message'], 'en'), expected)
            self.assertEqual(sections[0]['text'], '原始输入')
        absent = raw_section('input', '参数')
        self.assertEqual(render_message(absent['notice_message'], 'en'), 'The source record provides no separate input.')
        phase = item_phase('s', 't', 'u', {'id': 'p', 'type': 'mcpToolCall', 'name': '中文工具'}, start=1000)
        restored = display_phase({k: v for k, v in phase.items() if k != 'label_message'})
        self.assertEqual(render_message(restored['label_message'], 'en'), '中文工具')
        self.assertEqual(restored['id'], phase['id'])
