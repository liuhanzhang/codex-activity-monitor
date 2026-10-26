#!/usr/bin/env python3
"""Compare reasoning lifecycle clocks on an owned, independently logged-in backend.

This probe bypasses the product bridge/collector. It records a monotonic clock
and read-batch ID at os.read(), the CLI tracing timestamp, public response time
fields, and numeric usage. Model text, encrypted reasoning, credentials, tool
arguments, and account data are discarded before evidence is written.

Each case uses a fresh thread, no tools, and medium effort. The default matrix
compares summary=auto/none on the current model, plus the older test-home model.
The existing isolated home's config is hashed before/after, never modified.
"""

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import time
import tomllib

from codex_activity_monitor.events import decode_log
from codex_activity_monitor.usage import response_metadata


ROOT = Path(__file__).resolve().parents[2]
CLI = Path('/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex')
TRACE = 'warn,codex_api::sse=trace,tungstenite::protocol=trace,tungstenite::protocol::frame=off'
PROMPT = ('Do not use any tools. How many binary strings of length 30 have exactly '
          'ten ones and do not contain 111? Compute the exact answer. '
          'Return only the final integer, without explanation.')


def time_fields(value, path=''):
    result = {}
    if isinstance(value, dict):
        for key, child in value.items():
            name = path + '.' + key if path else key
            if re.search(r'(^|_)(time|timestamp|duration|latency|created_at|completed_at|started_at|ended_at)($|_)', key):
                if child is None or type(child) in (int, float):
                    result[name] = child
                elif isinstance(child, str) and re.fullmatch(r'\d{4}-\d\d-\d\dT[\d:.+-]+Z?', child):
                    result[name] = child
            if isinstance(child, (dict, list)):
                result.update(time_fields(child, name))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            result.update(time_fields(child, f'{path}[{index}]'))
    return result


def safe_event(event):
    result = {key: event[key] for key in ('type', 'sequence_number', 'output_index', 'item_id', 'identity') if key in event}
    result['event_keys'] = sorted(event)
    result['time_fields'] = time_fields(event)
    metrics = event.get('timing_metrics')
    if isinstance(metrics, dict):
        result['timing_metrics'] = {key: value for key, value in metrics.items() if value is None or type(value) in (int, float, bool)}
    headers = event.get('headers')
    if isinstance(headers, dict):
        result['timing_headers'] = {key: value for key, value in headers.items()
                                   if key.lower() in ('openai-processing-ms', 'server-timing', 'x-server-timing')}
    item = event.get('item')
    if isinstance(item, dict):
        result['item'] = {key: item[key] for key in ('id', 'type', 'status') if key in item}
        result['item']['keys'] = sorted(item)
        for key in ('metadata', 'internal_chat_message_metadata_passthrough'):
            if isinstance(item.get(key), dict):
                result['item'][key + '_keys'] = sorted(item[key])
    response = event.get('response')
    if isinstance(response, dict):
        result['response'] = response_metadata(response)
        result['response']['keys'] = sorted(response)
        usage = response.get('usage')
        result['response']['usage_keys'] = sorted(usage) if isinstance(usage, dict) else []
    delta = event.get('delta')
    if isinstance(delta, str):
        result['delta_chars'] = len(delta)
    if 'method' in event:
        result['method'] = event['method']
        params = event.get('params', {})
        result['params'] = {key: params[key] for key in ('threadId', 'turnId') if key in params}
        if isinstance(params.get('item'), dict):
            result['params']['item'] = {key: params['item'][key] for key in ('id', 'type', 'status') if key in params['item']}
        if isinstance(params.get('turn'), dict):
            result['params']['turn'] = {key: params['turn'][key] for key in ('id', 'status') if key in params['turn']}
        if event['method'] == 'thread/tokenUsage/updated':
            result['params']['tokenUsage'] = params.get('tokenUsage')
        if event['method'] == 'error':
            result['params']['error'] = params.get('error')
    return result


class Probe:
    def __init__(self, run, home):
        self.run, self.home = run, home
        config = tomllib.loads((home/'config.toml').read_text())
        command = [str(CLI)]
        for name in config.get('mcp_servers', {}):
            command += ['-c', f'mcp_servers.{name}.enabled=false']
        command += ['app-server']
        env = {key: value for key, value in os.environ.items() if not key.startswith(('CODEX_', 'PREFLIGHT_'))}
        env.update(CODEX_HOME=str(home), CODEX_SQLITE_HOME=str(run/'sqlite'),
                   CODEX_INTERNAL_APP_SERVER_REMOTE_CONTROL_DISABLED='1',
                   CODEX_CLI_PATH=str(CLI), LOG_FORMAT='json', RUST_LOG=TRACE)
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, env=env, cwd=run)
        self.selector = selectors.DefaultSelector()
        for source, pipe in (('stdout', self.process.stdout), ('stderr', self.process.stderr)):
            os.set_blocking(pipe.fileno(), False)
            self.selector.register(pipe, selectors.EVENT_READ, source)
        self.buffers = {'stdout': b'', 'stderr': b''}
        self.next_id = 0
        self.batch = 0
        self.case = 'initialize'
        self.rows = []
        self.replies = {}
        self.completed = {}
        self.counts = Counter()
        self.handle = (run/'events.jsonl').open('w')

    def write(self, row):
        row['case'] = self.case
        self.rows.append(row)
        self.handle.write(json.dumps(row, ensure_ascii=False) + '\n')
        self.handle.flush()

    def send(self, method, params=None, request=True):
        event = {'method': method, 'params': params or {}}
        if request:
            self.next_id += 1
            event['id'] = self.next_id
        self.write({'direction': 'protocol_in', 'observed_ns': time.monotonic_ns(),
                    'observed_timestamp': time.time(), 'event': {'method': method, 'id': event.get('id')}})
        self.process.stdin.write(json.dumps(event).encode() + b'\n')
        self.process.stdin.flush()
        return event.get('id')

    def poll(self, timeout=0.2):
        for key, _ in self.selector.select(timeout):
            source = key.data
            data = os.read(key.fileobj.fileno(), 65536)
            observed_ns, wall = time.monotonic_ns(), time.time()
            self.batch += 1
            if not data:
                self.selector.unregister(key.fileobj)
                continue
            self.buffers[source] += data
            if len(self.buffers[source]) > 16*1024*1024:
                raise RuntimeError('Oversized experimental diagnostic line')
            lines = self.buffers[source].split(b'\n')
            self.buffers[source] = lines.pop()
            for line in lines:
                self.counts[source + '_lines'] += 1
                if source == 'stderr':
                    event, stamp = decode_log(line)
                    if event is None:
                        continue
                    direction = 'upstream'
                else:
                    event, stamp = json.loads(line), None
                    direction = 'protocol_out'
                    if 'id' in event and 'method' not in event:
                        self.replies[event['id']] = event
                    if event.get('method') == 'turn/completed':
                        p = event['params']
                        self.completed[p['turn']['id']] = p['turn']['status']
                    if 'id' in event and 'method' in event:
                        raise RuntimeError('Unexpected server request in no-tools experiment')
                row = {'direction': direction, 'observed_ns': observed_ns,
                       'observed_timestamp': wall, 'read_batch': self.batch,
                       'source_timestamp': stamp, 'event': safe_event(event)}
                if stamp:
                    row['source_epoch'] = datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp()
                self.write(row)

    def request(self, method, params=None, timeout=40):
        target = self.send(method, params)
        deadline = time.monotonic() + timeout
        while target not in self.replies and time.monotonic() < deadline:
            self.poll()
            if self.process.poll() is not None:
                raise RuntimeError('Experimental backend exited')
        value = self.replies.pop(target)
        if 'error' in value:
            raise RuntimeError(f'{method}: {value["error"]}')
        return value['result']

    def close(self):
        self.process.stdin.close()
        deadline = time.monotonic() + 8
        while self.process.poll() is None and time.monotonic() < deadline:
            self.poll()
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.handle.close()
        self.selector.close()


def analyze(rows, label):
    upstream = [row for row in rows if row['case'] == label and row['direction'] == 'upstream']
    protocol = [row for row in rows if row['case'] == label and row['direction'] == 'protocol_out']
    items = {}
    responses = []
    created = None
    metrics = None
    for row in upstream:
        event = row['event']
        if event.get('type') == 'response.created':
            created = row
            metrics = None
        if event.get('type') == 'responsesapi.websocket_timing':
            metrics = event.get('timing_metrics')
        if event.get('type') in ('response.output_item.added', 'response.output_item.done'):
            item = event['item']
            current = items.setdefault(item['id'], {'id': item['id'], 'type': item['type']})
            current['start' if event['type'].endswith('.added') else 'end'] = row
        if event.get('type') == 'response.completed':
            response = event['response']
            entry = {'response': response, 'reasoning': [], 'event_time_fields': event['time_fields']}
            if metrics is not None:
                entry['codex_timing_metrics'] = metrics
            if created:
                entry['created_to_completed_receive_seconds'] = (row['observed_ns']-created['observed_ns'])/1e9
            for item_id in response.get('item_ids', []):
                item = items.get(item_id, {})
                if item.get('type') != 'reasoning':
                    continue
                start, end = item.get('start'), item.get('end')
                segment = {'item_id': item_id}
                if start and end:
                    receive = (end['observed_ns']-start['observed_ns'])/1e9
                    source = ((datetime.fromisoformat(end['source_timestamp'].replace('Z', '+00:00'))
                               - datetime.fromisoformat(start['source_timestamp'].replace('Z', '+00:00'))).total_seconds()
                              if start.get('source_timestamp') and end.get('source_timestamp') else None)
                    segment.update(receive_seconds=receive, cli_log_seconds=source,
                                   same_read_batch=start['read_batch'] == end['read_batch'],
                                   added_time_fields=start['event']['time_fields'], done_time_fields=end['event']['time_fields'])
                    if created:
                        segment['response_created_to_item_added_receive_seconds'] = (start['observed_ns']-created['observed_ns'])/1e9
                    count = response.get('usage', {}).get('reasoning_tokens')
                    segment['naive_tokens_per_second'] = count/receive if count is not None and receive > 0 else None
                pstart = next((r for r in protocol if r['event'].get('method') == 'item/started' and r['event']['params'].get('item', {}).get('id') == item_id), None)
                pend = next((r for r in protocol if r['event'].get('method') == 'item/completed' and r['event']['params'].get('item', {}).get('id') == item_id), None)
                if pstart and pend:
                    segment['protocol_receive_seconds'] = (pend['observed_ns']-pstart['observed_ns'])/1e9
                entry['reasoning'].append(segment)
            responses.append(entry)
    return {'event_counts': dict(Counter(row['event'].get('type') for row in upstream)), 'responses': responses}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--case', action='append', help='LABEL:MODEL:SUMMARY')
    parser.add_argument('--prompt', default=PROMPT)
    parser.add_argument('--timeout', type=float, default=120)
    args = parser.parse_args()
    os.umask(0o077)
    run = args.run_dir.resolve()
    run.mkdir(parents=True, exist_ok=False)
    old = Path((ROOT/'experiments/desktop-isolation/latest-run.txt').read_text().strip()).resolve()
    assert old.is_relative_to((ROOT/'experiments/desktop-isolation/runs').resolve())
    home = old/'codex-home'
    config_before = hashlib.sha256((home/'config.toml').read_bytes()).hexdigest()
    cases = args.case or ['sol-auto:gpt-6.1-sol:auto', 'sol-none:gpt-6.1-sol:none', 'luna-auto:gpt-6-luna:auto']
    evidence = {'scope': 'owned direct stdio backend, separate sqlite, no product bridge or collector',
                'cli': str(CLI), 'cli_sha256': hashlib.sha256(CLI.read_bytes()).hexdigest(),
                'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'isolated_home': str(home), 'config_before_sha256': config_before,
                'prompt_sha256': hashlib.sha256(args.prompt.encode()).hexdigest(), 'cases': []}
    probe = Probe(run, home)
    try:
        info = probe.request('initialize', {'clientInfo': {'name': 'reasoning_timing_probe', 'version': '0.1'},
                                           'capabilities': {'experimentalApi': True}})
        assert info.get('codexHome') == str(home)
        probe.send('initialized', request=False)
        assert probe.request('account/read', {'refreshToken': False}).get('account')
        models = probe.request('model/list', {'limit': 100}).get('data', [])
        evidence['advertised_models'] = [item.get('model') for item in models]
        for spec in cases:
            label, model, summary = spec.split(':')
            probe.case = label
            case = {'label': label, 'model': model, 'summary': summary, 'effort': 'medium'}
            evidence['cases'].append(case)
            print(json.dumps({'stage': 'case_started', **case}), flush=True)
            started = probe.request('thread/start', {'model': model, 'cwd': str(run), 'ephemeral': True,
                'approvalPolicy': 'never', 'sandbox': 'read-only',
                'developerInstructions': 'This is an event-timing experiment. Do not use tools, read/write files, or access the network. Return only the requested answer.'})
            thread = started['thread']['id']
            turn = probe.request('turn/start', {'threadId': thread, 'input': [{'type': 'text', 'text': args.prompt}],
                                             'effort': 'medium', 'summary': summary})['turn']['id']
            case.update(thread_id=thread, turn_id=turn, actual_thread_model=started.get('model'))
            deadline = time.monotonic() + args.timeout
            while turn not in probe.completed and time.monotonic() < deadline:
                probe.poll()
                if probe.process.poll() is not None:
                    raise RuntimeError('Experimental backend exited during turn')
            case['status'] = probe.completed.get(turn, 'timeout')
            if case['status'] == 'timeout':
                probe.request('turn/interrupt', {'threadId': thread, 'turnId': turn})
                raise TimeoutError(label)
            for _ in range(3):
                probe.poll(.1)
            case.update(analyze(probe.rows, label))
            print(json.dumps({'stage': 'case_completed', 'label': label, 'status': case['status'], 'responses': case['responses']}), flush=True)
        evidence['status'] = 'completed'
    except Exception as exc:
        evidence.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        probe.close()
        evidence['config_after_sha256'] = hashlib.sha256((home/'config.toml').read_bytes()).hexdigest()
        evidence['config_unchanged'] = evidence['config_after_sha256'] == config_before
        evidence['counts'] = dict(probe.counts)
        evidence['child_returncode'] = probe.process.returncode
        evidence['events_sha256'] = hashlib.sha256((run/'events.jsonl').read_bytes()).hexdigest()
        (run/'result.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
