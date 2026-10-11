#!/usr/bin/env python3
"""Transport fault injection and identity replay; no model requests."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from telemetry import Telemetry, read_capture


FAKE_SOURCE = '''import json, os, sys, time
if '--version' in sys.argv:
    print('fake-cli 1.0')
    raise SystemExit(0)
mode=os.environ.get('PREFLIGHT_FAKE_MODE','echo')
if mode=='signal':
    print(json.dumps({'method':'ready','params':{}}),flush=True)
    while True: time.sleep(1)
for line in sys.stdin:
    value=json.loads(line)
    if mode=='burst':
        for n in range(128):
            print(json.dumps({'method':'burst','params':{'n':n,'text':'x'*16384}}),flush=True)
    print(json.dumps({'id':value['id'],'result':value['params']}),flush=True)
raise SystemExit(7 if mode=='exit7' else 0)
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--capture', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    run.mkdir(parents=True, exist_ok=False)
    fake = run/'fake-cli'
    fake.write_text('#!' + sys.executable + '\n' + FAKE_SOURCE)
    fake.chmod(0o700)
    wrapper = Path(__file__).with_name('forwarder.py')
    results = []
    for mode, options in [('echo', {}), ('exit7', {}),
                          ('burst', {'PREFLIGHT_QUEUE_PACKETS': '2', 'PREFLIGHT_WRITER_DELAY': '0.1'}),
                          ('writer_failure', {}),
                          ('capture_limit', {'PREFLIGHT_CAPTURE_LIMIT': '1'})]:
        capture = run/(mode+'.jsonl')
        if mode == 'writer_failure':
            capture.mkdir(exist_ok=True)
        env = dict(os.environ, PREFLIGHT_REAL_CLI=str(fake), PREFLIGHT_CAPTURE=str(capture),
                   PREFLIGHT_FAKE_MODE='burst' if mode=='burst' else 'exit7' if mode=='exit7' else 'echo')
        env.update(options)
        begin = time.monotonic()
        process = subprocess.Popen([sys.executable, str(wrapper), 'app-server'],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, env=env)
        stdout, stderr = process.communicate(json.dumps({'id': 42, 'params': {'ok': True}})+'\n', timeout=15)
        rows = [json.loads(line) for line in stdout.splitlines()]
        assert rows[-1] == {'id': 42, 'result': {'ok': True}}
        assert not stderr
        assert process.returncode == (7 if mode=='exit7' else 0)
        meta = json.loads(capture.with_suffix('.meta.json').read_text())
        if mode == 'burst':
            assert len(rows) == 129 and [r['params']['n'] for r in rows[:-1]] == list(range(128))
            assert meta['dropped_packets'] > 0
        if mode == 'writer_failure':
            assert meta['writer_errors'] > 0
        if mode == 'capture_limit':
            assert meta['capture_limit_rows'] > 0
        results.append({'case': mode, 'returncode': process.returncode, 'stdout_rows': len(rows),
                        'stdout_sha256': hashlib.sha256(stdout.encode()).hexdigest(),
                        'elapsed_ms_including_diagnostic_drain': (time.monotonic()-begin)*1000,
                        'forwarder': meta, 'passed': True})
    env = dict(os.environ, PREFLIGHT_REAL_CLI=str(fake), PREFLIGHT_CAPTURE=str(run/'signal.jsonl'),
               PREFLIGHT_FAKE_MODE='signal')
    process = subprocess.Popen([sys.executable, str(wrapper), 'app-server'], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    assert json.loads(process.stdout.readline())['method'] == 'ready'
    process.terminate()
    process.communicate(timeout=10)
    assert process.returncode == -signal.SIGTERM
    results.append({'case': 'SIGTERM', 'returncode': process.returncode, 'passed': True})
    passthrough = subprocess.run([sys.executable, str(wrapper), '--version'], env=env,
                                capture_output=True, text=True, timeout=10)
    assert passthrough.stdout.strip() == 'fake-cli 1.0' and passthrough.returncode == 0
    results.append({'case': 'non_app_server_passthrough', 'passed': True})

    rows = list(read_capture(args.capture))
    baseline = Telemetry()
    for row in rows:
        baseline.ingest(row)
    reference = baseline.summarize()
    duplicate = Telemetry()
    for row in rows:
        duplicate.ingest(row)
        if row['direction'] == 'upstream' and row['event'].get('type','').endswith('.delta'):
            duplicate.ingest(row)
    dup = duplicate.summarize()
    assert [(s['sha256'],s['chars']) for s in dup['streams']] == [(s['sha256'],s['chars']) for s in reference['streams']]
    unknown = Telemetry()
    one = next(row for row in rows if row['direction']=='upstream' and row['event'].get('type','').endswith('.delta'))
    unknown.ingest(one)
    assert not unknown.streams and unknown.summarize()['unassociated_delta_count'] == 1
    missing = Telemetry()
    for row in rows:
        if row['direction']=='upstream' and row['event'].get('type')=='response.output_item.added':
            continue
        missing.ingest(row)
    assert not missing.streams and missing.summarize()['unassociated_delta_count'] > 0
    report = {'transport_faults': results,
              'replay': {'duplicate_deltas_ignored': dup['event_counts'].get('duplicates_ignored'),
                         'duplicate_replay_matches_baseline': True, 'unknown_item_quarantined': True,
                         'missing_item_start_not_guessed': True},
              'scope': 'synthetic transport faults plus real capture replay; no Desktop crash test'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({'transport_cases': len(results), 'passed': True,
                      'replay': report['replay'], 'report': str(args.output)}))


if __name__ == '__main__':
    main()
