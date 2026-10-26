"""Opt-in, owned macOS load: real bridge, collector, queries and Preview draw.

Uses only synthetic history in a disposable profile. Raw diagnostics stay under
.local. Run after bin/build-gui --preview; public summaries contain no paths or
source text. This is a controlled duration sample, never a natural-use soak.
"""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from codex_activity_monitor.runtime import ROOT, atomic_json, collector_alive
from codex_activity_monitor.state_client import state_once
from codex_activity_monitor.test_profile import controller
from codex_activity_monitor.timeline import phase_id
from codex_activity_monitor.timeline_query import query_runtime
from verify_state_transport import COLLECTOR, records, wait


def distribution(values):
    ordered = sorted(values)
    if not ordered:
        return {'samples': 0}
    return {'samples': len(values), 'p50_ms': statistics.median(values),
            'p95_ms': ordered[int((len(ordered)-1)*.95)],
            'p99_ms': ordered[int((len(ordered)-1)*.99)], 'max_ms': ordered[-1]}


def rss_sample(gui, collector):
    result = subprocess.run(['ps', '-axo', 'pid=,ppid=,rss='], check=True,
                            capture_output=True, text=True)
    rows = [tuple(map(int, line.split())) for line in result.stdout.splitlines()]
    owned = {collector}
    for _ in range(len(rows)):
        grown = owned | {pid for pid, parent, _ in rows if parent in owned}
        if grown == owned:
            break
        owned = grown
    memory = {pid: kib*1024 for pid, _, kib in rows}
    return {'gui_rss_bytes': memory.get(gui), 'collector_rss_bytes': memory.get(collector),
            'collector_descendants_rss_bytes': sum(memory.get(pid, 0) for pid in owned-{collector})}


def fixtures(home, streams, phases):
    sessions = home/'sessions'
    sessions.mkdir(parents=True)
    paths = []
    for number in range(streams):
        path = sessions/f'synthetic-chat-{number}.jsonl'
        with path.open('w') as stream:
            def row(kind, payload):
                stream.write(json.dumps({'type': kind, 'payload': payload})+'\n')
            row('session_meta', {'id': f'chat-{number}', 'history_mode': 'paginated'})
            row('event_msg', {'type': 'task_started', 'turn_id': f'history-{number}'})
            for item in range(phases):
                row('event_msg', {'type': 'item_completed', 'thread_id': f'chat-{number}', 'turn_id': f'history-{number}',
                    'item': {'id': f'old-{number}-{item}', 'type': 'AgentMessage', 'text': 'Synthetic history 中文🙂'*8}})
            row('event_msg', {'type': 'task_complete', 'turn_id': f'history-{number}'})
        paths.append(path)
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--duration', type=float, default=300)
    parser.add_argument('--idle', type=float, default=120)
    parser.add_argument('--streams', type=int, default=50)
    parser.add_argument('--rate', type=int, default=1000)
    parser.add_argument('--phases', type=int, default=1000)
    args = parser.parse_args()
    app, output = args.app.resolve(), args.output.resolve()
    if (sys.platform != 'darwin' or not app.name.endswith('Preview.app') or
            not output.is_relative_to(ROOT/'.local')):
        raise ValueError('Use the owned macOS Preview and private .local diagnostics')
    if min(args.duration, args.idle, args.streams, args.rate, args.phases) <= 0:
        parser.error('load dimensions must be positive')
    output.mkdir(parents=True, exist_ok=False)
    processes = []
    stop = threading.Event()
    errors = []
    query_results = {'timeline': [], 'detail': []}
    rss = []; probes = []; schedules = []; echo_times = []
    written = hashlib.sha256(); received = hashlib.sha256()
    threads = []
    slow = None
    with tempfile.TemporaryDirectory(prefix='cam-combined-', dir='/tmp') as temporary:
        profile = Path(temporary)
        atomic_json(profile/'profile.json', {'kind': 'codex-activity-monitor-disposable-test'})
        original = profile/'original-cli'
        original.write_text('#!'+sys.executable+'\nimport sys\nfor line in sys.stdin.buffer:\n sys.stdout.buffer.write(line);sys.stdout.buffer.flush()\nsys.exit(17)\n')
        original.chmod(0o700)
        home = profile/'synthetic-home'
        paths = fixtures(home, args.streams, args.phases)
        control, actual = controller(profile, app)
        control.configure(app, actual)
        runtime = Path(control.status()['runtime'])
        diagnostic = output/'gui.jsonl'
        with (output/'process.log').open('w') as log:
            try:
                with (control.root/'management.lock').open('r') as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    gui = subprocess.Popen([str(app/'Contents/MacOS/MonitorApp'), '--test-profile', str(profile),
                        '--window', '--ui-diagnostics', str(diagnostic), '--ui-diagnostics-seconds',
                        str(args.duration+args.idle+60)], stdout=log, stderr=log)
                    processes.append(gui)
                    atomic_json(control.session, {'version': 2, 'id': uuid.uuid4().hex, 'secret': uuid.uuid4().hex,
                        'owner_pid': gui.pid, 'created_at': time.time(), 'active': True})
                    collector = subprocess.Popen([sys.executable, '-B', '-c', COLLECTOR, str(runtime), str(output)],
                        stdout=log, stderr=log)
                    processes.append(collector)
                    wait(lambda:(output/'writer-entered').exists(), timeout=10)
                    fcntl.flock(lock, fcntl.LOCK_UN)

                def probe():
                    if any(process.poll() is not None for process in processes[:2]):
                        raise AssertionError('owned process exited before the load completed')
                    return state_once(runtime)

                def owner_ready():
                    try:
                        return probe().observation.get('owner_connected')
                    except (OSError, ValueError):
                        return False
                wait(owner_ready, timeout=10)
                wait(lambda:any(r.get('transport_fresh') for r in records(diagnostic)), timeout=10)
                bridge = subprocess.Popen([sys.executable, '-m', 'codex_activity_monitor.cli', '--runtime', str(runtime),
                    'bridge', '--real-cli', str(original), '--', 'app-server'], stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=log, env={**os.environ, 'CODEX_HOME': str(home)})
                processes.append(bridge)

                def drain():
                    try:
                        while line := bridge.stdout.readline():
                            received.update(line)
                            if json.loads(line).get('method') == 'fixture/latency':
                                echo_times.append((time.time()-json.loads(line)['params']['sent_at'])*1000)
                    except Exception as error:
                        errors.append(type(error).__name__)

                reader = threading.Thread(target=drain, daemon=True)
                reader.start(); threads.append(reader)

                def send(method, params):
                    raw = json.dumps({'method': method, 'params': params}).encode()+b'\n'
                    written.update(raw)
                    bridge.stdin.write(raw); bridge.stdin.flush()

                for number in range(args.streams):
                    owner = f'chat-{number}'
                    send('thread/started', {'thread': {'id': owner, 'name': f'Synthetic {number}', 'ephemeral': False}})
                    send('turn/started', {'threadId': owner, 'turn': {'id': f'live-{number}', 'status': 'inProgress'}})
                    send('item/started', {'threadId': owner, 'turnId': f'live-{number}',
                        'item': {'id': f'message-{number}', 'type': 'agentMessage'}})
                wait(lambda:probe().payload['overview']['active_thread_count'] == args.streams, timeout=10)

                def queries(kind):
                    number = 0
                    while not stop.is_set():
                        owner_number = number % args.streams
                        owner = f'chat-{owner_number}'
                        request = {'action': 'timeline', 'scope_id': str(home), 'thread_id': owner,
                            'turn_id': f'history-{owner_number}'}
                        if kind == 'detail':
                            request.update(action='detail', turn_id=f'live-{owner_number}',
                                phase_id=phase_id(str(home), owner, f'live-{owner_number}', 'model', f'message-{owner_number}'))
                        before = time.monotonic()
                        try:
                            result = query_runtime(runtime, request)
                            if result.get('error'):
                                errors.append(kind+':'+result['error'])
                            elif kind == 'timeline' and len(result['phases']) != args.phases:
                                errors.append('timeline:wrong_membership')
                            query_results[kind].append((time.monotonic()-before)*1000)
                        except Exception as error:
                            errors.append(kind+':'+type(error).__name__)
                        number += 1
                        stop.wait(.2)

                for kind in query_results:
                    thread = threading.Thread(target=queries, args=(kind,), daemon=True)
                    thread.start(); threads.append(thread)
                slow = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                slow.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8192)
                slow.settimeout(2)
                slow.connect(str(runtime/'state.sock'))
                slow.sendall(b'{"protocol_version":1,"kind":"subscribe","role":"reader","nonce":"combined-slow"}\n')
                begin = time.monotonic(); number = 0; sample_at = begin; append_at = begin+5
                append_number = 0
                total_events = int(args.duration*args.rate)
                idle_until = None
                writer_released = False
                print('Owned Preview load started.', flush=True)
                while idle_until is None or time.monotonic() < idle_until:
                    now = time.monotonic(); elapsed = now-begin
                    if elapsed >= 30 and not writer_released:
                        (output/'release-writer').touch(exist_ok=True)
                        writer_released = True
                    if elapsed >= 60 and slow is not None:
                        drained = 0
                        while chunk := slow.recv(65536):
                            drained += len(chunk)
                        slow.close(); slow = None
                    if number < total_events and now >= append_at:
                        with paths[append_number % args.streams].open('a') as stream:
                            stream.write(json.dumps({'type': 'event_msg', 'payload': {'type': 'token_count'}})+'\n')
                        append_number += 1; append_at = now+5
                    if now >= sample_at:
                        state = probe()
                        assert state.is_fresh() and state.observation['owner_connected']
                        assert not (output/'unexpected-config-read').exists()
                        rss.append({'elapsed_seconds': elapsed, **rss_sample(gui.pid, collector.pid)})
                        probes.append({'elapsed_seconds': elapsed, 'sequence': state.sequence,
                            'resources': state.payload['resource_diagnostics'],
                            'persistence': state.persistence})
                        send('fixture/latency', {'sent_at': time.time()})
                        sample_at = now+2
                    if number < total_events:
                        due = begin+number/args.rate
                        if now < due:
                            time.sleep(min(due-now, .001)); continue
                        schedules.append(max(0, now-due)*1000)
                        owner_number = number % args.streams
                        owner = f'chat-{owner_number}'
                        send('item/agentMessage/delta', {'threadId': owner, 'turnId': f'live-{owner_number}',
                            'itemId': f'message-{owner_number}', 'delta': 'hello 中文🙂\n'})
                        number += 1
                        if number == total_events:
                            end_started = time.time()
                            for owner_number in range(args.streams):
                                owner = f'chat-{owner_number}'
                                send('item/completed', {'threadId': owner, 'turnId': f'live-{owner_number}',
                                    'item': {'id': f'message-{owner_number}', 'type': 'agentMessage', 'status': 'completed'}})
                                send('turn/completed', {'threadId': owner,
                                    'turn': {'id': f'live-{owner_number}', 'status': 'completed'}})
                            end_sent = time.time()
                            end_draw = wait(lambda:next((r for r in records(diagnostic)
                                if r.get('event') == 'draw' and r.get('mode') == 'idle' and r['at'] >= end_sent), None), timeout=5)
                            assert (end_draw['at']-end_sent)*1000 < 750
                            idle = probe()
                            assert idle.payload['overview']['active_thread_count'] == 0
                            load_seconds = time.monotonic()-begin
                            idle_until = time.monotonic()+args.idle
                            stop.set()  # Let caches and background work settle during the idle window.
                            print('Load ended; observing idle resource trend.', flush=True)
                    else:
                        time.sleep(.01)
                stop.set()
                for thread in threads[1:]:
                    thread.join(3)
                    assert not thread.is_alive()
                bridge.stdin.close(); bridge.wait(timeout=5); reader.join(3)
                assert not reader.is_alive() and bridge.returncode == 17
                assert written.digest() == received.digest()
                assert not errors, errors[:10]
                final = probe()
                gui.terminate(); gui.wait(timeout=3)
                collector.wait(timeout=8)
                assert not collector_alive(runtime)
                report = {'version': 1, 'scenario': 'combined-owned-native-load', 'natural_use': False,
                    'python': platform.python_version(), 'macos': platform.mac_ver()[0],
                    'build_id': end_draw['build_id'], 'streams': args.streams,
                    'history_phases_per_source': args.phases, 'history_sources': len(paths),
                    'duration_seconds': args.duration, 'idle_seconds': args.idle,
                    'actual_load_seconds': load_seconds,
                    'actual_elapsed_seconds': time.monotonic()-begin, 'target_delta_per_second': args.rate,
                    'deltas_sent': number, 'source_appends': append_number,
                    'archive_block_seconds': 30, 'paused_reader_seconds': 60,
                    'paused_reader_drained_bytes': drained, 'scheduler_lateness': distribution(schedules),
                    'original_echo_latency': distribution(echo_times), 'original_bytes_exact': True,
                    'original_exit_code': bridge.returncode, 'queries': {kind: distribution(values)
                        for kind, values in query_results.items()}, 'query_errors': errors,
                    'end_batch_send_ms': (end_sent-end_started)*1000,
                    'end_to_idle_draw_ms': (end_draw['at']-end_sent)*1000,
                    'rss_samples': rss, 'resource_probes': probes,
                    'final_resources': final.payload['resource_diagnostics'],
                    'transport': json.loads((output/'transport.json').read_text()), 'passed': True,
                    'note': 'Controlled synthetic load and idle trend, not a hard RSS cap or natural soak. '
                        'History files are fixed then receive token-count appends; no real Codex writer. '
                        'GUI draw is the AppKit callback, not physical screen timing. '
                        'RSS descendants include the owned counter and multiprocessing support processes.'}
                atomic_json(output/'summary.json', report)
                print(json.dumps({key: report[key] for key in ('passed', 'deltas_sent', 'end_to_idle_draw_ms', 'queries')}), flush=True)
            except BaseException:
                try:
                    atomic_json(output/'failure-state.json', state_once(runtime).display_value())
                except (OSError, ValueError):
                    pass
                atomic_json(output/'failure.json', {'processes': [process.poll() for process in processes],
                    'errors': errors[:20], 'rss_samples': rss, 'resource_probes': probes,
                    'gui_record_count': len(records(diagnostic))})
                raise
            finally:
                stop.set()
                if slow is not None:
                    slow.close()
                (output/'release-writer').touch(); (output/'release-config').touch()
                for process in reversed(processes):
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill(); process.wait(timeout=2)
                for thread in threads:
                    thread.join(3)


if __name__ == '__main__':
    main()
