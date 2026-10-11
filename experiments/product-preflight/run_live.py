#!/usr/bin/env python3
"""Two concurrent real turns on an owned stdio backend using the isolated home."""

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

from telemetry import Telemetry, read_capture


ROOT = Path(__file__).resolve().parents[2]
CLI = Path('/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex')
TRACE_FILTER = 'warn,codex_api::sse=trace,tungstenite::protocol=trace,tungstenite::protocol::frame=off'


class RPC:
    def __init__(self, process, evidence):
        self.process, self.next_id = process, 0
        self.inbox = queue.Queue()
        self.completed, self.methods = {}, Counter()
        self.events = []
        self.server_requests = []
        self.thread = threading.Thread(target=self.read, args=(evidence,), daemon=True)
        self.thread.start()

    def read(self, evidence):
        with evidence.open('w') as out:
            for line in self.process.stdout:
                value = json.loads(line)
                out.write(json.dumps({'observed_ns': time.monotonic_ns(), 'event': value}) + '\n')
                out.flush()
                self.events.append(value)
                method = value.get('method')
                if method:
                    self.methods[method] += 1
                if method == 'turn/completed':
                    p = value['params']
                    self.completed[p['threadId']] = p['turn']
                if 'id' in value and method:
                    self.server_requests.append({'method': method, 'params': value.get('params', {})})
                    if method.endswith('/requestApproval'):
                        answer = {'id': value['id'], 'result': {'decision': 'decline'}}
                    else:
                        # Tests have no client-executed dynamic tools.
                        answer = {'id': value['id'], 'error': {'code': -32601, 'message': 'Unsupported preflight client request'}}
                    self.send(answer)
                if 'id' in value and not method:
                    self.inbox.put(value)

    def send(self, value):
        self.process.stdin.write(json.dumps(value) + '\n')
        self.process.stdin.flush()

    def request(self, method, params=None, timeout=30):
        self.next_id += 1
        target = self.next_id
        self.send({'id': target, 'method': method, 'params': params or {}})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = self.inbox.get(timeout=max(0.01, deadline-time.monotonic()))
            if value.get('id') != target:
                continue
            if 'error' in value:
                raise RuntimeError(f'{method}: {value["error"]}')
            return value['result']
        raise TimeoutError(method)


def launch(run, home):
    env = {k: v for k, v in os.environ.items() if not k.startswith(('CODEX_', 'PREFLIGHT_'))}
    env.update(CODEX_HOME=str(home), CODEX_SQLITE_HOME=str(home/'sqlite'),
               CODEX_INTERNAL_APP_SERVER_REMOTE_CONTROL_DISABLED='1',
               CODEX_CLI_PATH=str(CLI), LOG_FORMAT='json', RUST_LOG=TRACE_FILTER,
               PREFLIGHT_REAL_CLI=str(CLI), PREFLIGHT_CAPTURE=str(run/'capture.jsonl'))
    return subprocess.Popen([sys.executable, str(Path(__file__).with_name('forwarder.py')),
                             '-c', 'features.code_mode_host=true', 'app-server'],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, bufsize=1, env=env, cwd=run)


def new_thread(rpc, run, model, name):
    result = rpc.request('thread/start', {
        'model': model, 'cwd': str(run), 'ephemeral': False,
        'approvalPolicy': 'never', 'sandbox': 'read-only',
        'developerInstructions': ('This is a bounded telemetry test. Use only harmless Python '
                                  'that prints to stdout. Do not read or write files or access the network.')})
    thread_id = result['thread']['id']
    rpc.request('thread/name/set', {'threadId': thread_id, 'name': name})
    return thread_id


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--timeout', type=float, default=150)
    parser.add_argument('--mode', choices=('parallel', 'tools'), default='parallel')
    args = parser.parse_args()
    run = args.run_dir.resolve()
    run.mkdir(parents=True, exist_ok=False)
    os.umask(0o077)
    old = Path((ROOT/'experiments/desktop-isolation/latest-run.txt').read_text().strip()).resolve()
    expected = (ROOT/'experiments/desktop-isolation/runs').resolve()
    assert old.is_relative_to(expected)
    home = old/'codex-home'
    baseline_hash = hashlib.sha256((home/'config.toml').read_bytes()).hexdigest()
    process = launch(run, home)
    rpc = RPC(process, run/'protocol.jsonl')
    result = {'run_dir': str(run), 'isolated_home': str(home), 'status': 'running',
              'baseline_config_sha256': baseline_hash, 'backend_transport': 'owned-stdio',
              'desktop_submitted_turns': False, 'threads': [], 'mode': args.mode}
    try:
        info = rpc.request('initialize', {'clientInfo': {'name': 'product_preflight', 'version': '0.1.0'},
                                          'capabilities': {'experimentalApi': True}})
        assert info.get('codexHome') == str(home)
        rpc.send({'method': 'initialized'})
        assert rpc.request('account/read', {'refreshToken': False}).get('account')
        config = rpc.request('config/read', {'includeLayers': False})['config']
        model = config['model']
        result['model'] = model
        print(json.dumps({'stage': 'initialized', 'model': model}), flush=True)
        for name in ('A', 'B'):
            thread_id = new_thread(rpc, run, model, '产品验证并行 ' + name)
            prompt = (
                f'这是并行遥测验证{name}。先输出一段80至120字的中文说明，然后调用一次 shell 工具执行 '
                "python3 -B - <<'PY'\n完整Python程序\nPY\n。"
                '完整程序必须直接写进这个 heredoc，至少70行且至少2500字符；不能压缩源码或用exec。'
                '实现8个不同的带类型标注和文档字符串的纯函数，例如字符串处理、去重、分组、排序、'
                '词频、统计均值、极值、过滤；用明确输入执行自检。只用标准库，不读写文件，不联网。'
                f'程序开始打印 PREFLIGHT_{name}_BEGIN，结束前 sleep(2)，然后打印 PREFLIGHT_{name}_OK。'
                '最后输出一段150至200字的中文结果说明。只进行这一个工具调用。')
            if args.mode == 'tools':
                command = (f'python3 -B -c \'import time; print("PREFLIGHT_{name}_BEGIN", flush=True); '
                           f'time.sleep(3); print("PREFLIGHT_{name}_END", flush=True); '
                           f'raise SystemExit({0 if name == "A" else 7})\'')
                prompt = ('先简短说明将执行命令，然后只调用一次shell执行以下只打印和等待的命令，不重试，'
                          '完成后一句话说明退出结果：\n' + command)
            (run/f'prompt-{name}.txt').write_text(prompt)
            started = rpc.request('turn/start', {'threadId': thread_id,
                                 'input': [{'type': 'text', 'text': prompt}],
                                 'effort': 'low' if args.mode == 'tools' else 'medium', 'summary': 'auto'})
            result['threads'].append({'label': name, 'thread_id': thread_id,
                                      'turn_id': started['turn']['id']})
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            if all(t['thread_id'] in rpc.completed for t in result['threads']):
                break
            if process.poll() is not None:
                raise RuntimeError('Owned backend exited before turns completed')
            time.sleep(0.2)
        else:
            raise TimeoutError('Parallel turns did not finish within the bounded run')
        result['completed'] = {t['thread_id']: rpc.completed[t['thread_id']]['status'] for t in result['threads']}
        print(json.dumps({'stage': 'parallel_completed', 'statuses': result['completed']}), flush=True)
        if args.mode == 'parallel':
            # Cheap real interruption regression, without starting another long output.
            cancel_id = new_thread(rpc, run, model, '产品验证取消')
            cancel = rpc.request('turn/start', {'threadId': cancel_id,
                                'input': [{'type': 'text', 'text': '不调用工具，逐条写出100条不同的Python优化建议，每条约50字。'}],
                                'effort': 'medium', 'summary': 'auto'})
            time.sleep(0.5)
            rpc.request('turn/interrupt', {'threadId': cancel_id, 'turnId': cancel['turn']['id']})
            deadline = time.monotonic() + 20
            while cancel_id not in rpc.completed and time.monotonic() < deadline:
                time.sleep(0.1)
            result['interruption_status'] = rpc.completed.get(cancel_id, {}).get('status')
        result['status'] = 'completed'
    except Exception as exc:
        result.update(status='failed', error=str(exc))
        # Only interrupt turns created by this probe.
        for entry in result['threads']:
            if entry['thread_id'] not in rpc.completed:
                try:
                    rpc.request('turn/interrupt', {'threadId': entry['thread_id'], 'turnId': entry['turn_id']}, timeout=5)
                except Exception:
                    pass
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        rpc.thread.join(timeout=2)
        result['wrapper_returncode'] = process.returncode
        result['config_unchanged'] = hashlib.sha256((home/'config.toml').read_bytes()).hexdigest() == baseline_hash
        if (run/'capture.jsonl').exists():
            telemetry = Telemetry()
            for row in read_capture(run/'capture.jsonl'):
                telemetry.ingest(row)
            result['telemetry'] = telemetry.summarize()
        if (run/'capture.meta.json').exists():
            result['forwarder'] = json.loads((run/'capture.meta.json').read_text())
        result['protocol_methods'] = dict(rpc.methods)
        (run/'live-report.json').write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
        print(json.dumps({'status': result['status'], 'error': result.get('error'),
                          'streams': len(result.get('telemetry', {}).get('streams', [])),
                          'report': str(run/'live-report.json')}, ensure_ascii=False), flush=True)
    return 0 if result['status'] == 'completed' else 1


if __name__ == '__main__':
    sys.exit(main())
