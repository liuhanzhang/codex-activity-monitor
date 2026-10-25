"""Opt-in constant-rate tool-input fixture through an isolated real collector.

No Codex process, GUI, production runtime or model request is created.
"""

import argparse
import json
import os
from pathlib import Path
import socket
import statistics
import subprocess
import sys
import tempfile
import time

from codex_progress_monitor.metrics import count_text
from codex_progress_monitor.state_client import state_once


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    root = Path(__file__).resolve().parents[2]
    text = ' x' * 25
    assert count_text(text, 'o200k_base')[0] == 25
    samples, sent = [], []
    with tempfile.TemporaryDirectory(prefix='cpm-rate-', dir='/tmp') as directory:
        runtime = Path(directory)
        home = runtime / 'synthetic-home'
        home.mkdir()
        with (runtime / 'collector.log').open('w') as log:
            collector = subprocess.Popen([sys.executable, '-m', 'codex_progress_monitor.cli',
                '--runtime', str(runtime), 'collect'], cwd=root,
                env={**os.environ, 'PYTHONPATH': str(root / 'src')},
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            peer = None
            try:
                deadline = time.monotonic() + 8
                while not (runtime / 'events.sock').exists():
                    if collector.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError('isolated_collector_start_failed')
                    time.sleep(.02)
                peer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                peer.settimeout(1)
                peer.connect(str(runtime / 'events.sock'))

                def send(direction, event, at=None):
                    at = time.monotonic() if at is None else at
                    peer.sendall(json.dumps({'backend': 'synthetic-backend', 'direction': direction,
                        'observed_ns': int(at * 1e9), 'observed_timestamp': time.time(),
                        'event': event}, separators=(',', ':')).encode() + b'\n')
                    return at

                send('lifecycle', {'type': 'bridge/started', 'home': str(home), 'cli': 'synthetic'})
                # The explicitly synthetic item has an 8-second pre-text wait.
                start = time.monotonic() - 8
                send('protocol_out', {'method': 'turn/started', 'params': {'threadId': 'chat',
                    'turn': {'id': 'turn', 'status': 'inProgress'}}}, start)
                send('upstream', {'type': 'response.output_item.added', 'item': {'id': 'input',
                    'type': 'custom_tool_call', 'name': 'synthetic',
                    'metadata': {'turn_id': 'turn'}}}, start)
                deadline = time.monotonic() + 2
                while True:
                    try:
                        state_once(runtime)
                        break
                    except (OSError, ValueError):
                        if time.monotonic() > deadline:
                            raise
                        time.sleep(.02)
                epoch = time.monotonic()
                seen = set()
                for batch in range(1, 65):
                    time.sleep(max(0, epoch + batch / 4 - time.monotonic()))
                    at = send('upstream', {'type': 'response.custom_tool_call_input.delta',
                        'item_id': 'input', 'delta': text, 'sequence_number': batch})
                    sent.append(at)
                    live = state_once(runtime)
                    assert live.is_fresh()
                    if live.sequence in seen:
                        continue
                    seen.add(live.sequence)
                    chat = next(t for t in live.payload['threads'] if t['id'] == 'chat')
                    stream = next((s for a in chat['activities'] for s in a['streams'] if s['kind'] == 'tool_input'), {})
                    end = stream.get('rate_window_at')
                    expected = 25*sum(end-1 < receipt <= end for receipt in sent) if end is not None else None
                    samples.append({'elapsed': time.monotonic() - epoch,
                        'rate': chat['generation_rate'],
                        'expected_window_tokens': expected,
                        'rate_window_seconds': stream.get('rate_window_seconds'),
                        'coverage': live.payload['overview']['coverage']})
                send('protocol_out', {'method': 'turn/completed', 'params': {'threadId': 'chat',
                    'turn': {'id': 'turn', 'status': 'completed'}}})
                deadline = time.monotonic() + 3
                while True:
                    live = state_once(runtime)
                    chat = next(t for t in live.payload['threads'] if t['id'] == 'chat')
                    if chat['status'] == 'completed':
                        assert chat['generation_rate'] is None
                        break
                    if time.monotonic() > deadline:
                        raise RuntimeError('completion_not_observed')
                    time.sleep(.05)
            finally:
                if peer is not None:
                    peer.close()
                collector.terminate()
                collector.wait(timeout=5)
    reference = (len(sent) - 1) * 25 / (sent[-1] - sent[0])
    known = [s for s in samples if s['rate'] is not None and s['rate'] > 0]
    steady = [s['rate'] for s in known if s['elapsed'] >= 3]
    report = {'scope': 'synthetic tool-input stream; real isolated collector and tokenizer IPC',
        'feed_tokens_per_batch': 25, 'feed_interval_seconds': .25, 'batches': len(sent),
        'pre_text_wait_seconds': 8, 'reference_tokens_per_second': reference,
        'first_positive_rate': known[0] if known else None,
        'steady_samples': len(steady), 'steady_mean': statistics.mean(steady) if steady else None,
        'steady_min': min(steady, default=None), 'steady_max': max(steady, default=None),
        'completion_rate_unknown': True, 'samples': samples,
        'limits': ['Synthetic content and emission schedule, not natural model performance.',
                   'Collector prefix sampling introduces bounded fixture quantization; no GUI draw is measured.']}
    report['passed'] = (bool(known) and len(steady) >= 5
        and abs(statistics.mean(steady) / reference - 1) < .12
        and all(sample['rate_window_seconds'] == 1 and sample['rate'] == sample['expected_window_tokens']
                for sample in known))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'samples'}, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
