"""Validate an app copied from its release DMG under source and network denial.

Only explicit disposable profiles are configured. No launchctl or real Codex
installation is changed. Run outside an enclosing sandbox so sandbox-exec can
apply this stricter child policy.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

from build_dmg import APP_NAME, mounted_dmg
from verify_rollout_history import verify as verify_rollout_history

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dmg', type=Path)
    parser.add_argument('--result', type=Path, required=True)
    parser.add_argument('--gui', action='store_true')
    args = parser.parse_args()
    dmg = args.dmg.resolve()
    if dmg.suffix.lower() != '.dmg':
        parser.error('Provide the release .dmg installer.')
    result = {'dmg': str(dmg), 'dmg_sha256': hashlib.sha256(dmg.read_bytes()).hexdigest(), 'checks': {}}
    with tempfile.TemporaryDirectory(prefix='cam-package-', dir='/private/tmp') as directory:
        root = Path(directory)
        moved = root/'中文 moved application'
        moved.mkdir()
        with mounted_dmg(dmg, root/'mounted') as mounted:
            subprocess.run(['/usr/bin/ditto', str(mounted/APP_NAME), str(moved/APP_NAME)], check=True)
        app = moved/APP_NAME
        result['checks']['dmg_copy_out_and_eject'] = True
        backend = app/'Contents/Helpers/MonitorBackend.app/Contents/MacOS/monitor-backend'
        config = json.loads((app/'Contents/Resources/backend.json').read_text())
        assert config['backend'] == str(backend.relative_to(app)) and config['mode'] == 'bundled'
        assert str(ROOT) not in json.dumps(config) and '/Users/' not in json.dumps(config)
        with (app/'Contents/Info.plist').open('rb') as source:
            app_info = plistlib.load(source)
        with (app/'Contents/Helpers/MonitorBackend.app/Contents/Info.plist').open('rb') as source:
            backend_info = plistlib.load(source)
        app_id = app_info['CFBundleIdentifier']
        backend_id = backend_info['CFBundleIdentifier']
        assert app_id == 'com.liuhanzhang.codexactivitymonitor', app_id
        assert backend_id == 'com.liuhanzhang.codexactivitymonitor.backend', backend_id
        result['checks']['bundle_ids'] = {'app': app_id, 'backend': backend_id}
        minimum_systems = {'app': app_info['LSMinimumSystemVersion'],
                           'backend': backend_info['LSMinimumSystemVersion']}
        assert minimum_systems == {'app': '14.0', 'backend': '14.0'}, minimum_systems
        result['checks']['minimum_system_versions'] = minimum_systems
        result['build_id'] = config['build_id']
        subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
        developer = Path(subprocess.check_output(['/usr/bin/xcode-select', '-p'], text=True).strip())
        denied = [ROOT, Path(sys.base_prefix), developer, Path('/opt/homebrew'), Path.home()/'.codex', Path.home()/'Library/Caches',
                  Path(tempfile.gettempdir())/'data-gym-cache', Path('/Library/Frameworks/Python.framework')]
        policy = ('(version 1)(allow default)'
                  '(deny network-outbound (remote ip "*:*"))'
                  '(deny network-inbound (local ip "*:*"))'
                  '(deny file-read* '+''.join('(subpath '+json.dumps(str(path))+')' for path in denied)+')')
        environment = {'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8',
                       'CODEX_HOME': str(root/'synthetic-codex-home'), 'PYTHONPATH': '/absent-source',
                       'TIKTOKEN_CACHE_DIR': str(root/'absent-cache')}

        def execute(command, *, check=True, input=None, timeout=20):
            value = subprocess.run(['/usr/bin/sandbox-exec', '-p', policy, *map(str, command)],
                                   cwd=root, env=environment, input=input, capture_output=True, timeout=timeout)
            if check and value.returncode:
                raise RuntimeError(f'{command!r}: {value.returncode}: {value.stderr.decode(errors="replace")}')
            return value

        def cli(*arguments, **kwargs):
            return execute([backend, *arguments], **kwargs)

        assert execute(['/bin/cat', ROOT/'pyproject.toml'], check=False).returncode != 0
        assert execute(['/bin/cat', '/opt/homebrew/bin/python3'], check=False).returncode != 0
        connected = threading.Event()
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            listener.settimeout(2)
            def answer():
                try:
                    with listener.accept()[0] as peer:
                        connected.set()
                        peer.recv(4096)
                        peer.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK')
                except OSError:
                    pass
            responder = threading.Thread(target=answer, daemon=True)
            responder.start()
            network = execute(['/usr/bin/curl', '--noproxy', '*', '--max-time', '1',
                               f'http://127.0.0.1:{listener.getsockname()[1]}'], check=False)
            responder.join(2.5)
        assert network.returncode == 7 and not connected.is_set(), network.stderr
        result['checks']['source_python_and_network_denial'] = True
        result['checks']['build_toolchain_denial'] = str(developer)
        self_check = json.loads(cli('--self-check').stdout)
        assert self_check['ok'] and self_check['worker']['starts'] == 1
        assert self_check['build_id'] == config['build_id']
        assert not (root/'absent-cache').exists()
        result['checks']['offline_all_encodings_and_spawn_worker'] = self_check

        def profile(name):
            path = root/name
            path.mkdir()
            (path/'profile.json').write_text(json.dumps({'kind': 'codex-activity-monitor-disposable-test'}))
            original = path/'original-cli'
            original.write_text('#!/bin/sh\ncat\necho dev-stderr >&2\nexit 17\n')
            original.chmod(0o700)
            return path

        test = profile('profile')

        def management(action, *extra):
            value = json.loads(cli('app', action, '--app', app, '--test-profile', test,
                                   '--state-protocol', '1', *extra).stdout)
            assert value['ok'], value
            return value

        configured = management('configure')
        assert configured['integration_status']['version'] == 1
        result['checks']['integration_diagnostics_schema'] = True
        runtime = Path(configured['runtime'])
        try:
            resumed = management('resume', '--owner-pid', str(os.getpid()))
            assert resumed['state'] == 'configured'
            state = json.loads(cli('--runtime', runtime, 'status').stdout)
            assert state['running'] and state['connection']['healthy'], state
            result['checks']['collector_spawn_and_live_health'] = True
            synthetic_events = [
                {'method': 'thread/started', 'params': {'thread': {'id': 'packaging-fixture', 'name': 'Synthetic development build'}}},
                {'method': 'turn/started', 'params': {'threadId': 'packaging-fixture', 'turn': {'id': 'dev-turn'}}},
                {'method': 'item/started', 'params': {'threadId': 'packaging-fixture', 'turnId': 'dev-turn',
                                                    'item': {'id': 'dev-item', 'type': 'agentMessage'}}},
                {'method': 'item/agentMessage/delta', 'params': {'threadId': 'packaging-fixture', 'turnId': 'dev-turn',
                                                              'itemId': 'dev-item', 'delta': 'Synthetic packaging 中文'}},
                {'method': 'item/completed', 'params': {'threadId': 'packaging-fixture', 'turnId': 'dev-turn',
                                                      'item': {'id': 'dev-item', 'type': 'agentMessage', 'text': 'Synthetic packaging 中文'}}},
                {'method': 'turn/completed', 'params': {'threadId': 'packaging-fixture', 'turn': {'id': 'dev-turn', 'status': 'completed'}}},
            ]
            query = {'action': 'timeline', 'scope_id': str(root/'synthetic-codex-home'), 'thread_id': 'packaging-fixture'}
            timeline = json.loads(cli('--runtime', runtime, 'timeline-query', json.dumps(query)).stdout)
            assert timeline['version'] == 1, timeline
            result['checks']['timeline_query'] = True
            payload = b''.join(json.dumps(row, ensure_ascii=False).encode()+b'\n' for row in synthetic_events)+bytes(range(256))*16
            bridged = execute([test/'data/codex-observed', 'app-server'], input=payload, check=False)
            assert (bridged.returncode, bridged.stdout, bridged.stderr) == (17, payload, b'dev-stderr\n'), bridged
            result['checks']['bridge_binary_stdio_and_exit_code'] = True
            deadline = time.monotonic()+5
            while time.monotonic() < deadline:
                timeline = json.loads(cli('--runtime', runtime, 'timeline-query', json.dumps(query)).stdout)
                if timeline.get('selected_turn_id') == 'dev-turn' and timeline.get('phases'):
                    break
                time.sleep(.1)
            assert timeline.get('selected_turn_id') == 'dev-turn' and timeline.get('phases'), timeline
            # Timeline queries and published state have independent cadences.
            # Seeing the phase does not mean the next state tick has published.
            deadline = time.monotonic()+5
            while time.monotonic() < deadline:
                state = json.loads(cli('--runtime', runtime, 'status').stdout)
                if state['threads'] == 1:
                    break
                time.sleep(.1)
            assert state['threads'] == 1, state
            result['checks']['observed_synthetic_turn_and_timeline'] = True
            sessions = Path(environment['CODEX_HOME'])/'sessions'
            sessions.mkdir(parents=True, exist_ok=True)
            usage = {'input_tokens': 200, 'output_tokens': 100,
                     'cached_input_tokens': 0, 'reasoning_output_tokens': 20}
            rows = [
                {'type': 'session_meta', 'payload': {'id': 'packaging-rate'}},
                {'type': 'turn_context', 'timestamp': '2026-01-01T00:00:00Z',
                 'payload': {'turn_id': 'rate-turn'}},
                {'type': 'token_usage_record', 'timestamp': '2026-01-01T00:00:01Z',
                 'payload': {'thread_id': 'packaging-rate', 'turn_id': 'rate-turn',
                             'response_id': 'rate-response', 'usage': usage}},
                {'type': 'event_msg', 'timestamp': '2026-01-01T00:00:01Z',
                 'payload': {'type': 'token_count', 'info': {
                     'last_token_usage': usage, 'total_token_usage': usage}}},
            ]
            (sessions/'rollout-packaging-rate.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
            rate_query = {'action': 'timeline', 'scope_id': environment['CODEX_HOME'],
                          'thread_id': 'packaging-rate', 'turn_id': 'rate-turn'}
            rate = json.loads(cli('--runtime', runtime, 'timeline-query', json.dumps(rate_query)).stdout)['average_speed']
            assert rate['tokens_per_second'] == 100 and rate['timed_output_tokens'] == 100, rate
            assert rate['timed_duration_ms'] == 1000 and rate['pending_response_count'] == 0, rate
            result['checks']['bundled_selected_turn_average_speed'] = True
            def inject(rows):
                packet = b''.join(json.dumps(row, ensure_ascii=False).encode()+b'\n' for row in rows)
                value = execute([test/'data/codex-observed', 'app-server'], input=packet, check=False)
                assert value.returncode == 17 and value.stdout == packet, value.returncode
            result['checks']['rollout_history_compatibility'] = verify_rollout_history(
                cli, inject, environment['CODEX_HOME'], runtime)
            management('stop', '--owner-pid', str(os.getpid()))
            assert not json.loads(cli('--runtime', runtime, 'status').stdout)['running']
            result['checks']['managed_stop'] = True
            missing = app.with_name('Temporarily missing.app')
            app.rename(missing)
            try:
                fallback = execute([test/'data/codex-observed', 'app-server'], input=payload, check=False)
                assert (fallback.returncode, fallback.stdout, fallback.stderr) == (17, payload, b'dev-stderr\n'), fallback
            finally:
                missing.rename(app)
            result['checks']['missing_app_external_fallback'] = True
            management('restore')
            assert not json.loads((test/'environment.json').read_text())
            assert not (test/'data/codex-observed').exists()
            result['checks']['restore_original_environment_and_remove_launcher'] = True
            management('cleanup')
        finally:
            cli('--runtime', runtime, 'stop', check=False)

        if args.gui:
            gui_profile = profile('gui-profile')
            diagnostics = root/'gui-diagnostics.jsonl'
            with (root/'gui.log').open('wb') as log:
                process = subprocess.Popen(['/usr/bin/sandbox-exec', '-p', policy, str(app/'Contents/MacOS/MonitorApp'),
                                            '--test-profile', str(gui_profile), '--window', '--ui-diagnostics', str(diagnostics)],
                                           cwd=root, env=environment, stdout=log, stderr=log, start_new_session=True)
                try:
                    deadline = time.monotonic()+20
                    observed = None
                    while time.monotonic() < deadline and process.poll() is None:
                        if diagnostics.exists():
                            for line in diagnostics.read_text().splitlines():
                                try:
                                    row = json.loads(line)
                                except ValueError:
                                    continue
                                if row.get('transport_fresh') and row.get('management_state') == 'configured':
                                    observed = row
                        if observed:
                            break
                        time.sleep(.1)
                    assert observed, (root/'gui.log').read_text()
                    assert observed['build_id'] == config['build_id']
                    result['checks']['native_gui_with_bundled_backend'] = {
                        key: observed[key] for key in ('build_id', 'management_state', 'transport_fresh', 'mode')}
                    subprocess.run(['/usr/bin/osascript', '-e', f'tell application {json.dumps(str(app))} to quit'],
                                   capture_output=True, timeout=5)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        process.wait(timeout=5)
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
                    gui_runtime = gui_profile/'data/run'
                    cli('--runtime', gui_runtime, 'stop', check=False)
        result['ok'] = True
        args.result.parent.mkdir(parents=True, exist_ok=True)
        args.result.write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
