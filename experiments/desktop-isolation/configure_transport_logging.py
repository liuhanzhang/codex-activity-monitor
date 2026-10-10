#!/usr/bin/env python3
"""Restart the recorded isolated backend with receive-layer logging only."""

import argparse
import datetime
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import tomllib
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from configure_tool_mode import connect, await_backend
from launch_isolated_desktop import CLI, ROOT


TRACE_FILTER = ('warn,codex_api::sse=trace,tungstenite::protocol=trace,'
                'tungstenite::protocol::frame=off,'
                'codex_api::responses_websocket_timing=trace')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trace', action='store_true')
    parser.add_argument('--restore-harness-baseline', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    run = Path((ROOT / 'latest-run.txt').read_text().strip()).resolve()
    if not run.is_relative_to((ROOT / 'runs').resolve()):
        raise RuntimeError('Expected an isolated experiment directory')
    report_path = run / 'report.json'
    report = json.loads(report_path.read_text())
    home = Path(report['codex_home']).resolve()
    if home != run / 'codex-home':
        raise RuntimeError('Unexpected isolated home')
    url = urlsplit(report['websocket_url'])
    if url.hostname != '127.0.0.1':
        raise RuntimeError('Expected a localhost backend')
    endpoint = (url.hostname, url.port)
    old_pid = report['backend_pid']
    if old_pid in {item['pid'] for item in report['baseline_processes']}:
        raise RuntimeError('Refusing to restart a baseline process')
    identity = subprocess.check_output(['/bin/ps', '-p', str(old_pid), '-o', 'comm='], text=True).strip()
    if identity != str(CLI):
        raise RuntimeError('Backend process identity changed')
    client = connect(endpoint, home)
    try:
        for index, thread_id in enumerate(client.request(2, 'thread/loaded/list').get('data', [])):
            status = client.request(10 + index, 'thread/read', {
                'threadId': thread_id, 'includeTurns': False})['thread'].get('status', {}).get('type')
            if status not in ('idle', 'notLoaded', 'systemError'):
                raise RuntimeError(f'An isolated chat is not idle: {status}')
    finally:
        client.close()
    stamp = datetime.datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d-%H%M%S')
    config_path = home / 'config.toml'
    before = config_path.read_text()
    if args.restore_harness_baseline:
        # This backup predates every model-catalog/feature experiment.
        baseline_path = run / 'config-before-tool-mode-20261010-170016.toml'
        baseline = baseline_path.read_text()
        remaining = tomllib.loads(before)
        remaining.pop('model_catalog_json', None)
        features = remaining.get('features', {})
        for name in ('code_mode', 'code_mode_only', 'apply_patch_streaming_events'):
            features.pop(name, None)
        if not features:
            remaining.pop('features', None)
        if remaining != tomllib.loads(baseline):
            raise RuntimeError('Other config values changed; refusing to overwrite them')
        (run / f'config-before-transport-{stamp}.toml').write_text(before)
        config_path.write_text(baseline)
    env = {key: value for key, value in os.environ.items() if not key.startswith('CODEX_')}
    env.update({'CODEX_HOME': str(home), 'CODEX_SQLITE_HOME': str(home / 'sqlite'),
                'LOG_FORMAT': 'json', 'RUST_LOG': TRACE_FILTER if args.trace else 'codex_app_server=info',
                'CODEX_INTERNAL_APP_SERVER_REMOTE_CONTROL_DISABLED': '1'})

    def launch(suffix):
        log_path = run / f'backend-transport-{stamp}{suffix}.log'
        with log_path.open('wb') as log:
            process = subprocess.Popen([str(CLI), '-c', 'features.code_mode_host=true',
                                        'app-server', '--listen', report['websocket_url']],
                                       env=env, cwd=run, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=log, start_new_session=True)
        return process, log_path

    stopped = False
    backend = None
    try:
        os.kill(old_pid, signal.SIGTERM)
        stopped = True
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                os.kill(old_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.2)
        else:
            raise RuntimeError('Old isolated backend did not exit')
        backend, log_path = launch('')
        client = await_backend(backend, endpoint, home)
        try:
            config = client.request(2, 'config/read', {'includeLayers': False})['config']
            cache = json.loads((home / 'models_cache.json').read_text())
            model = config.get('model')
            catalog_mode = next((m.get('tool_mode') for m in cache['models'] if m['slug'] == model), None)
        finally:
            client.close()
        outcome = {'backend_pid': backend.pid, 'previous_backend_pid': old_pid,
                   'backend_log': str(log_path), 'rust_log': env['RUST_LOG'],
                   'restored_harness_baseline': args.restore_harness_baseline,
                   'model': model, 'cached_model_tool_mode': catalog_mode,
                   'model_catalog_json': config.get('model_catalog_json'),
                   'features': config.get('features'), 'status': 'ready'}
        report.setdefault('previous_backend_pids', []).append(old_pid)
        report.update(backend_pid=backend.pid, active_transport_logging=outcome)
        if args.restore_harness_baseline:
            report['active_tool_mode_config']['status'] = 'restored_to_baseline'
        report_path.write_text(json.dumps(report, indent=2) + '\n')
        (run / f'transport-logging-{stamp}.json').write_text(json.dumps(outcome, indent=2) + '\n')
        print(json.dumps(outcome), flush=True)
    except BaseException:
        if backend is not None and backend.poll() is None:
            backend.terminate()
            backend.wait(timeout=10)
        config_path.write_text(before)
        if stopped:
            env['RUST_LOG'] = 'codex_app_server=info'
            restored, restored_log = launch('-rollback')
            client = await_backend(restored, endpoint, home)
            client.close()
            report.update(backend_pid=restored.pid, rollback_backend_log=str(restored_log))
            report_path.write_text(json.dumps(report, indent=2) + '\n')
        raise


if __name__ == '__main__':
    main()
