"""Opt-in macOS acceptance with an owned GUI profile and synthetic echo CLI.

Build with bin/build-gui --preview, then run this file using the project venv:
  PYTHONPATH=src python tests/acceptance/verify_state_transport.py --app APP --output .local/diagnostics/eh01
No Codex writer, real launch environment, or real session content is used.
"""

import argparse
import fcntl
import json
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid

from codex_activity_monitor.runtime import ROOT, atomic_json, collector_alive
from codex_activity_monitor.state_client import state_once
from codex_activity_monitor.test_profile import controller


COLLECTOR = '''
import builtins, io, json, sys, time
from pathlib import Path
from codex_activity_monitor import app_session, runtime as persistence, state_transport
from codex_activity_monitor.collector import serve
runtime, output = map(Path, sys.argv[1:3])
targets = {runtime/'app-managed.json', runtime.parent/'observation.json', runtime.parent/'integration.enabled'}
original_load = app_session.load_lease
armed = False
# collector imported the function already: patch its own boundary explicitly.
import codex_activity_monitor.collector as collector
def loaded(path):
    lease = original_load(path)
    original_io, original_open, original_stat = io.open, builtins.open, Path.stat
    def block(file):
        try: targeted = Path(file) in targets
        except TypeError: targeted = False
        if armed and targeted:
            (output/'unexpected-config-read').touch()
            while not (output/'release-config').exists(): time.sleep(.01)
    def checked_io(file, *args, **kwargs):
        block(file); return original_io(file, *args, **kwargs)
    def checked_open(file, *args, **kwargs):
        block(file); return original_open(file, *args, **kwargs)
    def checked_stat(path, *args, **kwargs):
        block(path); return original_stat(path, *args, **kwargs)
    io.open, builtins.open, Path.stat = checked_io, checked_open, checked_stat
    return lease
collector.load_lease = loaded
original_write = persistence.atomic_json
def stalled(path, value):
    global armed
    if path.name == 'snapshot.json':
        armed = True
        (output/'writer-entered').touch()
        while not (output/'release-writer').exists(): time.sleep(.01)
    return original_write(path, value)
persistence.atomic_json = stalled
original_close = state_transport.StateServer.close
def closed(self, *args, **kwargs):
    result = original_close(self, *args, **kwargs)
    (output/'transport.json').write_text(json.dumps(self.diagnostics()))
    return result
state_transport.StateServer.close = closed
serve(runtime)
'''


def wait(predicate, *, timeout=5):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.02)
    raise AssertionError('acceptance condition timed out')


def records(path):
    try:
        # Ignore only an in-flight final line, never malformed complete records.
        return [json.loads(line) for line in path.read_bytes().split(b'\n')[:-1] if line]
    except FileNotFoundError:
        return []


def verify_normal_quit(app, output):
    """Launch the production management path; quit through the Preview UI."""
    with tempfile.TemporaryDirectory(prefix='cam-quit-', dir='/tmp') as directory:
        profile = Path(directory)
        atomic_json(profile/'profile.json', {'kind': 'codex-activity-monitor-disposable-test'})
        original = profile/'original-cli'
        original.write_text('#!/bin/sh\ncat\n')
        original.chmod(0o700)
        runtime = profile/'data/run'
        diagnostic = output/'gui.jsonl'
        with (output/'process.log').open('w') as log:
            gui = subprocess.Popen([str(app/'Contents/MacOS/MonitorApp'), '--test-profile', str(profile),
                '--window', '--ui-diagnostics', str(diagnostic)], stdout=log, stderr=log)
            try:
                def ready():
                    if gui.poll() is not None:
                        raise AssertionError(f'GUI exited during startup: {gui.returncode}')
                    try:
                        return state_once(runtime).observation.get('owner_connected')
                    except (OSError, ValueError):
                        return False
                wait(ready, timeout=10)
                wait(lambda:any(r.get('transport_fresh') for r in records(diagnostic)))
                print('Ready: quit Codex Activity Monitor Preview through its isolated UI.', flush=True)
                wait(lambda:gui.poll() is not None, timeout=60)
                assert gui.returncode == 0
                wait(lambda:not collector_alive(runtime))
                assert (profile/'data/integration.enabled').exists()
                assert not (profile/'data/observation.json').exists()
                report = {'scenario': 'native-normal-quit', 'passed': True, 'gui_exit_code': gui.returncode,
                          'collector_stopped': True, 'integration_preserved': True, 'observation_released': True,
                          'build_id': records(diagnostic)[-1]['build_id']}
                atomic_json(output/'summary.json', report)
                print(json.dumps(report), flush=True)
            finally:
                if gui.poll() is None:
                    gui.terminate(); gui.wait(timeout=3)
                if collector_alive(runtime):
                    from codex_activity_monitor.cli import stop
                    stop(runtime)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--normal-quit', action='store_true', help='Wait for normal Quit from the isolated Preview UI')
    args = parser.parse_args()
    app, output = args.app.resolve(), args.output.resolve()
    if sys.platform != 'darwin' or not app.name.endswith('Preview.app'):
        raise ValueError('Use the owned preview app on macOS')
    if not output.is_relative_to(ROOT/'.local'):
        raise ValueError('Private diagnostics must stay under .local')
    output.mkdir(parents=True, exist_ok=False)
    if args.normal_quit:
        verify_normal_quit(app, output)
        return
    processes = []
    report = {'scenario': 'managed-state-and-original-stdio', 'archive_block_seconds': 30,
              'paused_reader_seconds': 60, 'natural_use': False}
    with tempfile.TemporaryDirectory(prefix='cam-native-', dir='/tmp') as directory:
        profile = Path(directory)
        atomic_json(profile/'profile.json', {'kind': 'codex-activity-monitor-disposable-test'})
        original = profile/'original-cli'
        original.write_text('#!'+sys.executable+'\nimport sys\nfor line in sys.stdin.buffer:\n sys.stdout.buffer.write(line);sys.stdout.buffer.flush()\nsys.exit(17)\n')
        original.chmod(0o700)
        control, actual = controller(profile, app)
        control.configure(app, actual)
        runtime = Path(control.status()['runtime'])
        diagnostic = output/'gui.jsonl'
        log = (output/'process.log').open('w')
        try:
            with (control.root/'management.lock').open('r') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                gui = subprocess.Popen([str(app/'Contents/MacOS/MonitorApp'), '--test-profile', str(profile),
                    '--window', '--ui-diagnostics', str(diagnostic), '--ui-diagnostics-seconds', '120'],
                    stdout=log, stderr=log)
                processes.append(gui)
                atomic_json(control.session, {'version': 2, 'id': uuid.uuid4().hex, 'secret': uuid.uuid4().hex,
                    'owner_pid': gui.pid, 'created_at': time.time(), 'active': True})
                collector = subprocess.Popen([sys.executable, '-B', '-c', COLLECTOR, str(runtime), str(output)],
                    stdout=log, stderr=log)
                processes.append(collector)
                def writer_entered():
                    if collector.poll() is not None or gui.poll() is not None:
                        raise AssertionError(f'bootstrap process exited: GUI={gui.poll()}, collector={collector.poll()}')
                    return (output/'writer-entered').exists()
                wait(writer_entered)
                fcntl.flock(lock, fcntl.LOCK_UN)
            def probe():
                return state_once(runtime)
            def owner_ready():
                if collector.poll() is not None or gui.poll() is not None:
                    raise AssertionError(f'owner startup exited: GUI={gui.poll()}, collector={collector.poll()}')
                try:
                    return probe().observation.get('owner_connected')
                except (OSError, ValueError):
                    return False
            wait(owner_ready, timeout=8)
            wait(lambda:any(r.get('transport_fresh') and r.get('state_sequence') for r in records(diagnostic)))
            print('Native owner connected; archive and config reads are blocked.', flush=True)
            env = {**os.environ, 'CODEX_HOME': str(profile/'synthetic-home')}
            bridge = subprocess.Popen([sys.executable, '-m', 'codex_activity_monitor.cli', '--runtime', str(runtime),
                'bridge', '--real-cli', str(original), '--', 'app-server'], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=log, env=env)
            processes.append(bridge)
            def send(method, params):
                raw = json.dumps({'method': method, 'params': params}).encode()+b'\n'
                before = time.time()
                bridge.stdin.write(raw); bridge.stdin.flush()
                if not select.select([bridge.stdout], [], [], 1)[0] or bridge.stdout.readline() != raw:
                    raise AssertionError('original byte stream changed or blocked')
                return before
            send('thread/started', {'thread': {'id': 'synthetic', 'name': 'Synthetic acceptance', 'ephemeral': False}})
            send('turn/started', {'threadId': 'synthetic', 'turn': {'id': 'turn', 'status': 'inProgress'}})
            send('item/started', {'threadId': 'synthetic', 'turnId': 'turn', 'item': {'id': 'reasoning', 'type': 'reasoning'}})
            wait(lambda:any(r.get('event') == 'draw' and r.get('mode') == 'thinking' for r in records(diagnostic)))
            slow = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            slow.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8192)
            slow.settimeout(2)
            slow.connect(str(runtime/'state.sock'))
            slow.sendall(b'{"protocol_version":1,"kind":"subscribe","role":"reader","nonce":"paused-reader"}\n')
            started = time.monotonic()
            checkpoints = []
            while time.monotonic()-started < 60:
                elapsed = time.monotonic()-started
                state = probe()
                assert state.is_fresh() and state.observation['owner_connected']
                assert not (output/'unexpected-config-read').exists()
                if elapsed < 30:
                    assert state.persistence['writing'] and not (runtime/'snapshot.json').exists()
                elif not (output/'release-writer').exists():
                    (output/'release-writer').touch()
                send('fixture/heartbeat', {})
                checkpoints.append({'elapsed': round(elapsed, 3), 'sequence': state.sequence,
                                    'fresh': state.is_fresh(), 'pending': state.persistence.get('pending')})
                time.sleep(.5)
            slow_bytes = 0
            while True:
                raw = slow.recv(65_536)
                if not raw:
                    break
                slow_bytes += len(raw)
            slow.close()
            wait(lambda:(runtime/'snapshot.json').exists())
            send('item/completed', {'threadId': 'synthetic', 'turnId': 'turn',
                'item': {'id': 'reasoning', 'type': 'reasoning', 'summary': []}})
            send('item/started', {'threadId': 'synthetic', 'turnId': 'turn',
                'item': {'id': 'message', 'type': 'agentMessage'}})
            text = 'Synthetic visible text 中文🙂 é\n'
            for _ in range(8):
                last_delta = send('item/agentMessage/delta', {'threadId': 'synthetic', 'turnId': 'turn',
                    'itemId': 'message', 'delta': text})
                time.sleep(.1)
            wait(lambda:any(r.get('event') == 'draw' and r.get('mode') == 'speed' and r['at'] >= last_delta-1
                            for r in records(diagnostic)))
            send('item/completed', {'threadId': 'synthetic', 'turnId': 'turn',
                'item': {'id': 'message', 'type': 'agentMessage', 'text': text*8}})
            before = send('turn/completed', {'threadId': 'synthetic', 'turn': {'id': 'turn', 'status': 'completed'}})
            end = wait(lambda:next((r for r in records(diagnostic)
                if r.get('event') == 'draw' and r.get('mode') == 'idle' and r['at'] >= before), None))
            report['end_to_draw_ms'] = round((end['at']-before)*1000, 3)
            report['last_delta_to_draw_ms'] = round((end['at']-last_delta)*1000, 3)
            report['visible_speed_draw_observed'] = True
            assert report['end_to_draw_ms'] < 750, report['end_to_draw_ms']
            bridge.communicate(timeout=4)
            assert bridge.returncode == 17
            assert not (output/'unexpected-config-read').exists()
            report.update(checkpoints=checkpoints, paused_reader_drained_bytes=slow_bytes,
                          build_id=end['build_id'], original_exit_code=17, config_read_attempts=0)
            # This acceptance exercises GUI crash expiry. Explicit normal release
            # and application quit are covered separately, never inferred here.
            gui.terminate(); gui.wait(timeout=3)
            collector.wait(timeout=8)
            assert not collector_alive(runtime)
            report['collector_stopped_after_gui_exit'] = True
            report['transport'] = json.loads((output/'transport.json').read_text())
            report['passed'] = True
            atomic_json(output/'summary.json', report)
            print(json.dumps({k:v for k,v in report.items() if k not in ('checkpoints','transport')}), flush=True)
        except BaseException:
            atomic_json(output/'failure.json', {'processes': [p.poll() for p in processes],
                'runtime_entries': sorted(p.name for p in runtime.iterdir()),
                'gui_record_count': len(records(diagnostic)),
                'config_read_attempted': (output/'unexpected-config-read').exists()})
            raise
        finally:
            (output/'release-writer').touch(); (output/'release-config').touch()
            for process in reversed(processes):
                if process.poll() is None:
                    process.terminate()
                    try: process.wait(timeout=5)
                    except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=2)
            log.close()


if __name__ == '__main__':
    main()
