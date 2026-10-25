"""Exercise observation recovery through real isolated collector and tokenizer IPC."""

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

from codex_progress_monitor.state_client import state_once


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    samples = {}
    with tempfile.TemporaryDirectory(prefix='cpm-gap-', dir='/tmp') as directory:
        runtime = Path(directory)
        with (runtime/'collector.log').open('w') as log:
            collector = subprocess.Popen([sys.executable, '-m', 'codex_progress_monitor.cli',
                '--runtime', str(runtime), 'collect'], cwd=root,
                env={**os.environ, 'PYTHONPATH': str(root/'src')}, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT)
            peer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                deadline = time.monotonic()+5
                while not (runtime/'events.sock').exists():
                    if collector.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError('isolated_collector_start_failed')
                    time.sleep(.02)
                peer.connect(str(runtime/'events.sock'))

                def send(direction, event):
                    at = time.monotonic()
                    peer.sendall(json.dumps({'backend': 'synthetic', 'direction': direction,
                        'observed_ns': int(at*1e9), 'observed_timestamp': time.time(), 'event': event}).encode()+b'\n')
                    return at

                def item(name):
                    send('upstream', {'type': 'response.output_item.added', 'item': {
                        'id': name, 'type': 'message', 'metadata': {'turn_id': 'turn'}}})

                def delta(name, count):
                    return send('upstream', {'type': 'response.output_text.delta',
                        'item_id': name, 'delta': ' x'*count})

                def read_until(name, predicate):
                    deadline = time.monotonic()+4
                    while True:
                        try:
                            state = state_once(runtime, timeout=1)
                        except (OSError, ValueError):
                            if collector.poll() is not None or time.monotonic() >= deadline:
                                raise
                            time.sleep(.025)
                            continue
                        assert state.is_fresh()
                        value = state.payload
                        if value['threads'] and predicate(value):
                            samples[name] = {'overview': value['overview'],
                                'chat_issues': value['threads'][0]['generation_rate_issues'],
                                'historical_gap': 'observation_gap' in value['threads'][0]['quality']}
                            return value
                        if time.monotonic() >= deadline:
                            raise AssertionError((name, value.get('overview')))
                        time.sleep(.025)

                send('lifecycle', {'type': 'bridge/started', 'home': str(runtime/'synthetic-home')})
                send('protocol_out', {'method': 'turn/started', 'params': {
                    'threadId': 'chat', 'turn': {'id': 'turn', 'status': 'inProgress'}}})
                item('old'); delta('old', 80)
                read_until('initial', lambda v: v['overview']['observed_subtotal'] == 80)
                send('lifecycle', {'type': 'bridge/gap', 'reason': 'synthetic_loss'})
                read_until('gap', lambda v: v['overview']['observed_subtotal'] is None)
                item('new'); delta('new', 25)
                read_until('partial', lambda v: v['overview']['coverage'] == 'partial' and
                    v['overview']['observed_subtotal'] == 25)
                send('upstream', {'type': 'response.output_item.done', 'item': {
                    'id': 'old', 'type': 'message', 'metadata': {'turn_id': 'turn'},
                    'content': [{'type': 'output_text', 'text': ' x'*80}]}})
                read_until('historical_only', lambda v: v['overview']['coverage'] == 'complete' and
                    v['overview']['observed_subtotal'] == 25 and not v['threads'][0]['generation_rate_issues'])
                send('lifecycle', {'type': 'bridge/gap', 'reason': 'synthetic_second_loss'})
                read_until('second_gap', lambda v: v['overview']['observed_subtotal'] is None)
                delta('new', 10)
                read_until('warming', lambda v: v['overview']['observed_subtotal'] is None and
                    v['threads'][0]['generation_rate_issues'] == ['observation_gap'])
                for _ in range(6):
                    time.sleep(.25)
                    delta('new', 10)
                read_until('same_stream_recovered', lambda v: v['overview']['coverage'] == 'complete' and
                    (v['overview']['observed_subtotal'] or 0) > 0 and not v['threads'][0]['generation_rate_issues'])
                assert samples['historical_only']['historical_gap']
                assert samples['same_stream_recovered']['historical_gap']
            finally:
                peer.close()
                collector.terminate()
                collector.wait(timeout=5)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {'passed': True, 'scope': 'synthetic events; real isolated collector, state socket and tokenizer IPC',
              'production_runtime_changed': False, 'samples': samples,
              'limits': ['Not a natural model stream or a production cutover.']}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'passed': True, 'phases': list(samples)}))


if __name__ == '__main__':
    main()
