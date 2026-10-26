"""Bounded read-only-tool turn on an isolated home; export structure, never bodies."""

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'experiments/product-preflight'))
from run_live import RPC, CLI


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--runtime', type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    run = args.output.resolve()
    run.mkdir(parents=True, exist_ok=False)
    home = args.home.resolve()
    assert home != Path.home() / '.codex', 'Use an isolated Codex home'
    config_before = hashlib.sha256((home / 'config.toml').read_bytes()).hexdigest()
    env = {k: v for k, v in os.environ.items() if not k.startswith(('CODEX_', 'PREFLIGHT_'))}
    env.update(CODEX_HOME=str(home), CODEX_SQLITE_HOME=str(home / 'sqlite'),
               CODEX_INTERNAL_APP_SERVER_REMOTE_CONTROL_DISABLED='1', LOG_FORMAT='json')
    env['PYTHONPATH'] = str(ROOT / 'src')
    command = [str(CLI), 'app-server']
    if args.runtime:
        from codex_activity_monitor.cli import start
        from codex_activity_monitor.runtime import runtime_dir
        args.runtime = runtime_dir(args.runtime)
        start(args.runtime)
        command = [sys.executable, '-m', 'codex_activity_monitor.cli', '--runtime', str(args.runtime),
                   'bridge', '--real-cli', str(CLI), '--', 'app-server']
    stderr = (run / 'stderr.log').open('w')
    process = subprocess.Popen(command, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=stderr, text=True, env=env, cwd=run)
    rpc = RPC(process, run / 'protocol.jsonl')
    result = {'scope': 'owned isolated stdio backend', 'status': 'running'}
    try:
        rpc.request('initialize', {'clientInfo': {'name': 'timeline_probe', 'version': '1'},
                                   'capabilities': {'experimentalApi': True}})
        rpc.send({'method': 'initialized'})
        model = rpc.request('config/read', {'includeLayers': False})['config']['model']
        thread = rpc.request('thread/start', {'model': model, 'cwd': str(run), 'ephemeral': False,
                             'approvalPolicy': 'never', 'sandbox': 'read-only',
                             'developerInstructions': 'Telemetry probe. Only print and sleep. Never read/write files or access the network.'})['thread']['id']
        turn = rpc.request('turn/start', {'threadId': thread, 'input': [{'type': 'text', 'text':
                 '只调用一次 exec_command 执行 python3 -B -c \'import time; print("TIMELINE_BEGIN",flush=True); time.sleep(4); print("TIMELINE_END",flush=True)\'。yield_time_ms=1000。如果有会话，用 write_stdin 等待结束。不要做其他操作，最后回答完成。'}],
                 'effort': 'low', 'summary': 'auto'})['turn']['id']
        accepted = rpc.request('turn/steer', {'threadId': thread, 'expectedTurnId': turn,
                              'input': [{'type': 'text', 'text': '最后回答保持一句话。'}]})
        result['steer_same_turn'] = accepted.get('turnId') == turn
        try:
            rpc.request('turn/steer', {'threadId': thread, 'expectedTurnId': 'invalid-probe-turn',
                                      'input': [{'type': 'text', 'text': 'This input must be rejected.'}]})
            result['wrong_turn_rejected'] = False
        except RuntimeError:
            result['wrong_turn_rejected'] = True
        deadline = time.monotonic() + 120
        while thread not in rpc.completed and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.1)
        result['turn_completed'] = thread in rpc.completed
        for method, params in [('thread/read', {'threadId': thread, 'includeTurns': False}),
                               ('thread/turns/list', {'threadId': thread, 'limit': 6}),
                               ('thread/items/list', {'threadId': thread, 'turnId': turn, 'limit': 100})]:
            try:
                response = rpc.request(method, params)
                result[method] = {'ok': True, 'keys': sorted(response),
                                  'count': len(response.get('data', []))}
            except RuntimeError as exc:
                result[method] = {'ok': False, 'error_type': type(exc).__name__}
        methods = Counter()
        items = Counter()
        shapes = {}
        inputs = []
        command_results = []
        for event in rpc.events:
            method = event.get('method')
            if method:
                methods[method] += 1
            if method in ('item/started', 'item/completed'):
                p = event['params']; item = p['item']; kind = item['type']
                items[kind] += 1
                shapes.setdefault(method + ':' + kind, {'params': sorted(p), 'item': sorted(item)})
                if method == 'item/completed' and kind == 'userMessage':
                    inputs.append({'has_client_id': bool(item.get('clientId')), 'same_turn': p.get('turnId') == turn})
                if method == 'item/completed' and kind == 'commandExecution':
                    command_results.append({'status': item.get('status'), 'exit_code': item.get('exitCode'),
                                            'duration_ms': item.get('durationMs'), 'source': item.get('source'),
                                            'has_process_id': bool(item.get('processId'))})
        result.update(methods=dict(methods), items=dict(items), shapes=shapes, user_inputs=inputs,
                      command_results=command_results, new_turn_count=methods['turn/started'], status='complete')
        if args.runtime:
            from codex_activity_monitor.timeline_query import query_runtime
            from codex_activity_monitor.runtime import read_snapshot
            bundle = query_runtime(args.runtime, {'action': 'timeline', 'scope_id': str(home), 'thread_id': thread})
            parameters = [p for p in bundle['phases'] if p['namespace'] == 'input']
            details = [query_runtime(args.runtime, {'action': 'detail', 'scope_id': str(home), 'thread_id': thread,
                       'turn_id': p['turn_id'], 'phase_id': p['id']}) for p in parameters]
            result['online_timeline'] = {'phase_count': len(bundle['phases']), 'parameter_count': len(parameters),
                'timed_parameter_count': sum(p.get('start_ms') is not None and p.get('end_ms') is not None for p in parameters),
                'parameter_details_from_codex': all(d.get('source') == 'codex_history' and d.get('sections') for d in details),
                'retained_parameter_details_from_codex': all(d.get('source') == 'codex_history' and d.get('sections')
                    for p, d in zip(parameters, details) if p.get('start_ms') is not None and p.get('end_ms') is not None),
                'unfinished_parameter_count': sum(p.get('end_ms') is None for p in parameters),
                'projection_errors': read_snapshot(args.runtime).get('counters', {}).get('timeline_projection_error', 0),
                'quality': bundle['quality']}
    except Exception as exc:
        result.update(status='failed', error_type=type(exc).__name__)
        raise
    finally:
        if process.poll() is None:
            process.stdin.close()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.terminate(); process.wait(timeout=8)
        stderr.close()
        if args.runtime:
            from codex_activity_monitor.cli import stop
            stop(args.runtime)
        result['config_unchanged'] = hashlib.sha256((home / 'config.toml').read_bytes()).hexdigest() == config_before
        (run / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        # The disposable source remains in Codex's isolated home. Retain no second transcript.
        (run / 'protocol.jsonl').unlink(missing_ok=True)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
