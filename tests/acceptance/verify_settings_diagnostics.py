#!/usr/bin/env python3
"""Disposable cross-process acceptance. Never reads/writes launchctl or real chats."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tests'))
from test_state_transport import JSONPeer
from codex_activity_monitor.runtime import collector_alive


def main():
    environment = dict(os.environ, PYTHONPATH=str(ROOT / 'src'))
    with tempfile.TemporaryDirectory(prefix='cam-settings-', dir='/tmp') as temporary:
        profile = Path(temporary)
        (profile / 'codex/sessions').mkdir(parents=True)
        environment['CODEX_HOME'] = str(profile / 'codex')
        (profile / 'profile.json').write_text(json.dumps({'kind': 'codex-activity-monitor-disposable-test'}))
        app = profile / 'Fixture.app'
        entry = app / 'Contents/MacOS/MonitorApp'
        entry.parent.mkdir(parents=True)
        entry.write_text('#!/bin/sh\nexit 0\n'); entry.chmod(0o700)
        original = profile / 'original-cli'
        original.write_text('#!' + sys.executable + '\n' + '''import json,sys
print(json.dumps({'method':'thread/started','params':{'thread':{'id':'fixture-thread','cwd':'/synthetic/project','name':'Synthetic acceptance'}}}),flush=True)
for line in sys.stdin:
    print(line,end='',flush=True)
''')
        original.chmod(0o700)
        runtime = profile / 'data/run'
        history = profile / 'codex/sessions/rollout-fixture-thread.jsonl'
        usage = {'input_tokens': 200, 'output_tokens': 100,
                 'cached_input_tokens': 0, 'reasoning_output_tokens': 20}

        def append_history(kind, second, **payload):
            with history.open('a') as stream:
                stream.write(json.dumps({'type': kind, 'timestamp': f'2026-01-01T00:00:0{second}Z',
                                         'payload': payload}) + '\n')

        append_history('session_meta', 0, id='fixture-thread')
        append_history('turn_context', 0, turn_id='fixture-turn')
        append_history('token_usage_record', 1, thread_id='fixture-thread', turn_id='fixture-turn',
                       response_id='response-one', usage=usage)

        def average_speed():
            request = {'action': 'timeline', 'scope_id': str(profile / 'codex'),
                       'thread_id': 'fixture-thread', 'turn_id': 'fixture-turn'}
            result = subprocess.run([sys.executable, '-B', '-m', 'codex_activity_monitor.cli',
                '--runtime', str(runtime), 'timeline-query', json.dumps(request)],
                capture_output=True, env=environment, timeout=10, check=True)
            return json.loads(result.stdout)['average_speed']

        def command(action, *extra):
            result = subprocess.run([sys.executable, '-B', '-m', 'codex_activity_monitor.cli', 'app', action,
                '--app', str(app), '--test-profile', str(profile), '--state-protocol', '1', *extra],
                capture_output=True, env=environment, timeout=10)
            if result.returncode:
                raise AssertionError(f'{action} failed: {result.stdout.decode()}')
            return json.loads(result.stdout)

        initial = command('configure')
        assert initial['integration_status']['configuration'] == 'enabled'
        revision = initial['integration_status']['configuration_revision']
        assert command('configure')['integration_status']['configuration_revision'] == revision
        session = command('resume', '--owner-pid', str(os.getpid()))['session']
        peer = JSONPeer(runtime / 'state.sock')
        bridge = None
        try:
            peer.send(role='owner', session_id=session['id'], session_secret=session['secret'])
            peer.until(lambda frame: frame['kind'] == 'hello')
            desktop_env = environment | json.loads((profile / 'environment.json').read_text())
            bridge = subprocess.Popen([str(profile / 'data/codex-observed'), 'app-server'], env=desktop_env,
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            state = peer.until(lambda frame: frame['kind'] == 'state' and any(
                backend.get('configuration_revision') == revision and backend.get('configuration_matches') is True
                for backend in frame['payload'].get('backends', {}).values()))
            assert state['payload']['diagnostics']['version'] == 1
            assert any(c['component'] == 'collector' and c['state'] == 'healthy'
                       for c in state['payload']['diagnostics']['components'])
            first = average_speed()
            assert first['tokens_per_second'] == 100 and first['pending_response_count'] == 1, first
            append_history('event_msg', 1, type='token_count', info={
                'last_token_usage': usage, 'total_token_usage': usage})
            second_usage = dict(usage, output_tokens=300)
            append_history('token_usage_record', 3, thread_id='fixture-thread', turn_id='fixture-turn',
                           response_id='response-two', usage=second_usage)
            append_history('event_msg', 3, type='token_count', info={
                'last_token_usage': second_usage, 'total_token_usage': dict(usage, output_tokens=400)})
            second = average_speed()
            assert second['sample_count'] == 2 and second['timed_output_tokens'] == 400, second
            assert second['timed_duration_ms'] == 3000 and second['pending_response_count'] == 0, second
            assert abs(second['tokens_per_second'] - 400 / 3) < 1e-10, second
            assert average_speed() == second, 'query-time clock changed the average'
            command('status')
            assert average_speed() == second, 'diagnostic status check changed the average'
            (profile / 'dependencies.json').write_text(json.dumps({'pending': [{'pid': bridge.pid, 'reason': 'bridge_running'}]}))
            restored = command('restore')
            assert restored['state'] == 'pending_cleanup'
            assert restored['integration_status']['configuration'] == 'disabled'
            assert restored['integration_status']['restart'] == 'required'
            output, errors = bridge.communicate(b'literal-probe\n', timeout=5)
            assert bridge.returncode == 0 and output.endswith(b'literal-probe\n'), (bridge.returncode, errors)
            assert not collector_alive(runtime)
            (profile / 'dependencies.json').write_text(json.dumps({'pending': []}))
            assert command('cleanup')['state'] == 'restored'
            assert command('status')['integration_status']['configuration'] == 'disabled'
            logs = profile / 'data/diagnostics'
            assert any(logs.glob('*.jsonl'))
            assert command('configure')['integration_status']['configuration'] == 'enabled'
            assert command('restore', '--purge')['state'] == 'purged'
            assert not (profile / 'data').exists()
            print(json.dumps({'ok': True, 'scope': 'disposable profile, synthetic CLI',
                'checks': ['stable revision', 'real bridge startup evidence', 'live health summary',
                           'usage preview through collector query socket and CLI',
                           'incremental average and confirmations', 'average stable between queries',
                           'diagnostic status and average speed coexist',
                           'restore with pending bridge', 'stdio forwarding after restore',
                           'collector shutdown', 'cleanup', 'disabled status persists',
                           'diagnostics retained', 'reconfigure', 'purge']}, indent=2))
        finally:
            peer.close()
            if bridge is not None and bridge.poll() is None:
                bridge.kill(); bridge.communicate(timeout=3)
            if (profile / 'data').exists():
                (profile / 'dependencies.json').write_text(json.dumps({'pending': []}))
                try:
                    command('restore', '--purge')
                except Exception:
                    pass


if __name__ == '__main__':
    main()
