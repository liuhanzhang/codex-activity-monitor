"""Opt-in rollout cutover acceptance using an isolated collector and real CLI queries."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'packaging'))
from verify_rollout_history import verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--backend', type=Path)
    parser.add_argument('--native-client', action='store_true')
    args = parser.parse_args()
    command = [str(args.backend.resolve())] if args.backend else [sys.executable, '-m', 'codex_activity_monitor.cli']
    with tempfile.TemporaryDirectory(prefix='cam-rollout-acceptance-', dir='/private/tmp') as directory:
        root = Path(directory)
        runtime = root/'run'
        home = root/'codex-home'; home.mkdir()
        original = root/'synthetic-cli'
        original.write_text('#!/bin/sh\ncat\nexit 17\n'); original.chmod(0o700)
        environment = {**os.environ, 'PYTHONPATH': str(ROOT/'src'), 'CODEX_HOME': str(home)}
        environment.pop('CODEX_SQLITE_HOME', None)
        def cli(*arguments):
            value = subprocess.run([*command, *map(str, arguments)], env=environment, capture_output=True, timeout=20)
            assert value.returncode == 0, value.stderr.decode(errors='replace')[:1000]
            return value
        def inject(rows):
            packet = b''.join(json.dumps(row, ensure_ascii=False).encode()+b'\n' for row in rows)
            value = subprocess.run([*command, '--runtime', str(runtime), 'bridge', '--real-cli', str(original), '--', 'app-server'],
                                   env=environment, input=packet, capture_output=True, timeout=20)
            assert value.returncode == 17 and value.stdout == packet, value.returncode
        with (root/'collector.log').open('wb') as log:
            collector = subprocess.Popen([*command, '--runtime', str(runtime), 'collect'], env=environment,
                                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
            try:
                deadline = time.monotonic()+10
                while not (runtime/'tl.sock').is_socket():
                    if collector.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError('isolated collector did not start: '+(root/'collector.log').read_text()[-1000:])
                    time.sleep(.02)
                checks = verify(cli, inject, home, runtime)
                live = json.loads(cli('--runtime', runtime, 'status').stdout)
                assert live['running'] and live['connection']['healthy']
                checks['collector_remained_healthy'] = True
                if args.native_client:
                    fixture = root/'native-client.json'
                    fixture.write_text(json.dumps({'scope': str(home), 'configuration': {
                        'python': str(args.backend.resolve()) if args.backend else sys.executable,
                        'source_root': str(ROOT), 'runtime': str(runtime), 'data_root': str(root),
                        'build_id': 'isolated-rollout-acceptance', 'mode': 'bundled' if args.backend else None}}))
                    native_env = {**environment, 'CAM_ROLLOUT_CLIENT_CONFIG': str(fixture),
                                  'SDKROOT': '/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk',
                                  'CLANG_MODULE_CACHE_PATH': str(ROOT/'.local/clang-module-cache')}
                    native = subprocess.run(['swift', 'test', '--disable-sandbox', '--cache-path', str(ROOT/'.local/swift-cache'),
                        '--package-path', str(ROOT/'macos'), '-Xswiftc', '-load-plugin-library', '-Xswiftc',
                        '/Library/Developer/CommandLineTools/usr/lib/swift/host/plugins/testing/libTestingMacros.dylib',
                        '--filter', 'isolatedRolloutHistoryBackendClient'], env=native_env,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=180)
                    args.output.with_suffix('.native.log').write_text(native.stdout)
                    assert native.returncode == 0, native.stdout[-3000:]
                    assert Path(str(fixture)+'.ok').is_file(), 'native client test did not execute'
                    checks['native_system_client_timeline_and_activity_viewer'] = True
            finally:
                subprocess.run([*command, '--runtime', str(runtime), 'stop'], env=environment,
                               capture_output=True, timeout=10)
                try:
                    collector.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(collector.pid, signal.SIGTERM)
                    collector.wait(timeout=5)
        hashes = {p.stem: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT/'src/codex_activity_monitor').glob('*.py'))}
        result = {'version': 1, 'ok': True, 'transport': 'isolated collector, bridge stdio, CLI and tl.sock',
                  'backend': 'bundled' if args.backend else 'source', 'checks': checks, 'source_modules': hashes}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps({'ok': True, 'checks': checks}, indent=2))


if __name__ == '__main__': main()
