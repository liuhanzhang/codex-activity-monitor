"""Real process outlives its turn; query its original ownership during a second turn."""

import argparse
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
from codex_activity_monitor.cli import start, stop
from codex_activity_monitor.runtime import runtime_dir, read_snapshot
from codex_activity_monitor.timeline_query import query_runtime


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    home, run = args.home.resolve(), args.output.resolve()
    assert home != Path.home() / '.codex'
    run.mkdir(parents=True, exist_ok=False)
    runtime = runtime_dir(run / 'rt')
    before = hashlib.sha256((home / 'config.toml').read_bytes()).hexdigest()
    start(runtime)
    env = {k: v for k, v in os.environ.items() if not k.startswith(('CODEX_', 'PREFLIGHT_'))}
    env.update(PYTHONPATH=str(ROOT / 'src'), CODEX_HOME=str(home), CODEX_SQLITE_HOME=str(home / 'sqlite'),
               CODEX_INTERNAL_APP_SERVER_REMOTE_CONTROL_DISABLED='1')
    stderr = (run / 'stderr.log').open('w')
    process = subprocess.Popen([sys.executable, '-m', 'codex_activity_monitor.cli', '--runtime', str(runtime),
                                'bridge', '--real-cli', str(CLI), '--', 'app-server'],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, env=env, cwd=run, text=True)
    rpc = RPC(process, run / 'protocol.jsonl')
    result = {'scope': 'owned isolated real command across two real turns'}
    def wait_for(test, seconds=40):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and process.poll() is None:
            if test():
                return True
            time.sleep(.05)
        return False
    try:
        rpc.request('initialize', {'clientInfo': {'name': 'timeline_cross_turn', 'version': '1'},
                                   'capabilities': {'experimentalApi': True}})
        rpc.send({'method': 'initialized'})
        thread = rpc.request('thread/start', {'cwd': str(run), 'ephemeral': False, 'sandbox': 'read-only',
                    'approvalPolicy': 'never', 'developerInstructions': 'Bounded telemetry test. No files or networking. Only print and sleep.'})['thread']['id']
        prompt = ('只调用一次 tools.exec_command 执行以下命令，明确设置 yield_time_ms:1000：'
                  'python3 -B -c \'import time; print("TIMELINE_START",flush=True); time.sleep(12); print("TIMELINE_END",flush=True)\'。'
                  '工具返回会话后立即回复“已启动”。不要调用 write_stdin，不要等待，不要再调用任何工具。')
        first = rpc.request('turn/start', {'threadId': thread, 'input': [{'type': 'text', 'text': prompt}], 'effort': 'low'})['turn']['id']
        result['first_turn_completed'] = wait_for(lambda: rpc.completed.get(thread, {}).get('id') == first)
        def bundle(turn=None):
            request = {'action': 'timeline', 'scope_id': str(home), 'thread_id': thread}
            if turn:
                request['turn_id'] = turn
            return query_runtime(runtime, request)
        one = bundle(first)
        commands = [p for p in one['phases'] if p['kind'] == 'tool_execution' and p.get('item_type') == 'commandExecution']
        result['active_after_first_turn'] = any(p['status'] == 'active' for p in commands)
        second = rpc.request('turn/start', {'threadId': thread, 'input': [{'type': 'text', 'text': '不要调用工具，只回答“收到”。'}], 'effort': 'low'})['turn']['id']
        result['second_turn_completed'] = wait_for(lambda: rpc.completed.get(thread, {}).get('id') == second)
        current = bundle(second)
        result['old_turn_navigation_present'] = first in current['earlier_active_turns']
        result['old_command_not_reassigned'] = not any(p['item_id'] in {c['item_id'] for c in commands} for p in current['phases'])
        result['old_command_completed'] = wait_for(lambda: any(e.get('method') == 'item/completed' and
            e.get('params', {}).get('turnId') == first and e.get('params', {}).get('item', {}).get('type') == 'commandExecution'
            for e in rpc.events), seconds=25)
        final = bundle(first)
        result['original_owner_preserved'] = all(p['turn_id'] == first for p in final['phases'] if p['kind'] == 'tool_execution')
        result['completed_intervals'] = [p['end_ms'] - p['start_ms'] for p in final['phases'] if p['kind'] == 'tool_execution'
                                         and p.get('start_ms') is not None and p.get('end_ms') is not None]
        result['projection_errors'] = read_snapshot(runtime).get('counters', {}).get('timeline_projection_error', 0)
        result['passed'] = all(result.get(k) is True for k in ('first_turn_completed', 'second_turn_completed', 'active_after_first_turn',
            'old_turn_navigation_present', 'old_command_not_reassigned', 'old_command_completed', 'original_owner_preserved')) and result['projection_errors'] == 0
    finally:
        if process.poll() is None:
            process.stdin.close()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.terminate(); process.wait(timeout=8)
        stderr.close(); stop(runtime)
        (run / 'protocol.jsonl').unlink(missing_ok=True)
        result['config_unchanged'] = hashlib.sha256((home / 'config.toml').read_bytes()).hexdigest() == before
        (run / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
