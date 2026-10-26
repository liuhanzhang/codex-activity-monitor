import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from codex_activity_monitor import execution
from codex_activity_monitor.app_control import AppController
from codex_activity_monitor.runtime import default_runtime, source_identity
from test_app_control import FakeServices, MemoryEnvironment


class BundledExecutionTests(unittest.TestCase):
    def test_source_command_retains_module_entrypoint(self):
        with patch.object(sys, 'frozen', False, create=True):
            self.assertEqual(execution.backend_command('collect', executable='/test/python'),
                             ['/test/python', '-m', 'codex_activity_monitor.cli', 'collect'])

    def test_frozen_command_and_independent_child_environment(self):
        with patch.object(sys, 'frozen', True, create=True), patch.dict(os.environ, PYTHONPATH='/foreign', PYTHONHOME='/foreign'):
            self.assertEqual(execution.backend_command('--runtime', Path('/tmp/a b'), 'collect', executable='/bundle/backend'),
                             ['/bundle/backend', '--runtime', '/tmp/a b', 'collect'])
            environment = execution.backend_environment()
            self.assertNotIn('PYTHONPATH', environment)
            self.assertNotIn('PYTHONHOME', environment)
            self.assertEqual(environment['PYINSTALLER_RESET_ENVIRONMENT'], '1')
            self.assertEqual(os.environ['PYTHONPATH'], '/foreign')
            self.assertEqual(default_runtime(), Path.home()/'Library/Application Support/CodexActivityMonitor/run')

    def test_bundle_assets_and_identity_do_not_require_source_files(self):
        with tempfile.TemporaryDirectory() as directory:
            resources = Path(directory)
            modules = {'collector': 'a'*64, 'bridge': 'b'*64}
            (resources/'build-manifest.json').write_text(json.dumps({'modules': modules}))
            with patch.object(sys, 'frozen', True, create=True), patch.object(sys, '_MEIPASS', directory, create=True), patch.dict(os.environ):
                execution.initialize_bundle()
                self.assertEqual(os.environ['TIKTOKEN_CACHE_DIR'], str(resources/'tokenizer-cache'))
                identity = source_identity('collector', 'bridge')
                self.assertEqual(identity['modules'], modules)
                self.assertEqual(identity['source_sha256'], hashlib.sha256(json.dumps(modules, sort_keys=True).encode()).hexdigest())

    def test_frozen_launcher_forwards_without_source_and_falls_back(self):
        with tempfile.TemporaryDirectory(prefix='cam-bundle-', dir='/tmp') as directory:
            base = Path(directory)
            app = base/'App with spaces.app'
            entry = app/'Contents/MacOS/MonitorApp'
            backend = app/'Contents/Helpers/Backend/monitor-backend'
            actual = base/'original cli'
            for path, content in ((entry, 'exit 0'),
                                  (backend, 'printf "backend\\n"; printf "%s\\n" "$@"; cat; echo backend-stderr >&2; exit 19'),
                                  (actual, 'printf "original\\n"; printf "%s\\n" "$@"; cat; exit 17')):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('#!/bin/sh\n'+content+'\n')
                path.chmod(0o700)
            control = AppController(base/'data', environment=MemoryEnvironment(), services=FakeServices())
            with patch.object(sys, 'frozen', True, create=True):
                control.configure(app, actual, python=backend, source_root=base/'absent-source')
            launcher = control.root/'codex-observed'
            result = subprocess.run([str(launcher), 'a b', '中文', '--lang', 'en'], input='payload\n', text=True, capture_output=True)
            self.assertEqual(result.returncode, 19)
            self.assertTrue(result.stdout.startswith('backend\n--runtime\n'))
            self.assertTrue(result.stdout.endswith('a b\n中文\n--lang\nen\npayload\n'))
            self.assertEqual(result.stderr, 'backend-stderr\n')
            for removed in (control.enabled, backend, entry):
                removed.unlink()
                result = subprocess.run([str(launcher), 'a b'], input='payload\n', text=True, capture_output=True)
                self.assertEqual((result.returncode, result.stdout), (17, 'original\na b\npayload\n'))
                if removed == control.enabled:
                    removed.touch()


if __name__ == '__main__':
    unittest.main()
