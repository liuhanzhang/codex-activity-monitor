#!/usr/bin/env python3
"""Change the isolated app's model catalog, then restart only its recorded backend."""

import argparse
import datetime
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time
import tomllib
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from launch_isolated_desktop import CLI, ROOT, WebSocketProbe
from stream_probe import initialize


def set_toml_values(source, root_values, feature_values):
    first_table = re.search(r'(?m)^\s*\[[A-Za-z_]', source)
    boundary = first_table.start() if first_table else len(source)
    head, tail = source[:boundary], source[boundary:]
    for key, value in root_values.items():
        line = f'{key} = {json.dumps(value)}'
        pattern = rf'(?m)^\s*{re.escape(key)}\s*=.*$'
        if re.search(pattern, head):
            head = re.sub(pattern, lambda _: line, head)
        else:
            head = head.rstrip() + '\n' + line + '\n\n'
    source = head + tail
    match = re.search(r'(?m)^\s*\[features\]\s*$', source)
    if match:
        following = re.search(r'(?m)^\s*\[', source[match.end():])
        end = match.end() + following.start() if following else len(source)
        body = source[match.end():end]
        for key, value in feature_values.items():
            line = f'{key} = {str(value).lower()}'
            pattern = rf'(?m)^\s*{re.escape(key)}\s*=.*$'
            if re.search(pattern, body):
                body = re.sub(pattern, lambda _: '\n' + line, body)
            else:
                body = body.rstrip() + '\n' + line + '\n'
        source = source[:match.end()] + '\n' + body.lstrip('\n') + '\n' + source[end:]
    else:
        source = source.rstrip() + '\n\n[features]\n' + ''.join(
            f'{key} = {str(value).lower()}\n' for key, value in feature_values.items())
    parsed = tomllib.loads(source)
    assert all(parsed.get(key) == value for key, value in root_values.items())
    assert all(parsed['features'].get(key) == value for key, value in feature_values.items())
    return source


def connect(endpoint, expected_home):
    client = WebSocketProbe(endpoint)
    try:
        info = initialize(client, 'codex_tool_mode_configurator')
        if info.get('codexHome') != str(expected_home):
            raise RuntimeError('Connected to an unexpected backend')
        return client
    except BaseException:
        client.close()
        raise


def start_backend(run, home, websocket_url, stamp):
    env = {key: value for key, value in os.environ.items() if not key.startswith('CODEX_')}
    env.update({'CODEX_HOME': str(home), 'CODEX_SQLITE_HOME': str(home / 'sqlite'),
                'LOG_FORMAT': 'json', 'RUST_LOG': 'codex_app_server=info',
                'CODEX_INTERNAL_APP_SERVER_REMOTE_CONTROL_DISABLED': '1'})
    log_path = run / f'backend-tool-mode-{stamp}.log'
    with log_path.open('wb') as log:
        backend = subprocess.Popen([str(CLI), '-c', 'features.code_mode_host=true',
                                    'app-server', '--listen', websocket_url],
                                   env=env, cwd=run, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=log, start_new_session=True)
    return backend, log_path


def await_backend(backend, endpoint, home):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if backend.poll() is not None:
            raise RuntimeError(f'Backend exited with status {backend.returncode}')
        try:
            return connect(endpoint, home)
        except (ConnectionRefusedError, OSError, RuntimeError):
            time.sleep(0.2)
    raise RuntimeError('Backend failed to become ready')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('code_mode', 'code_mode_only', 'direct'),
                        default='code_mode')
    parser.add_argument('--run-dir', type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    run = (args.run_dir or Path((ROOT / 'latest-run.txt').read_text().strip())).resolve()
    if not run.is_relative_to((ROOT / 'runs').resolve()):
        raise RuntimeError('Only an isolated Desktop experiment may be configured')
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
        raise RuntimeError('Recorded backend process identity changed')
    client = connect(endpoint, home)
    try:
        loaded = client.request(2, 'thread/loaded/list').get('data', [])
        for index, thread_id in enumerate(loaded):
            thread = client.request(10 + index, 'thread/read', {
                'threadId': thread_id, 'includeTurns': False})['thread']
            status = thread.get('status', {}).get('type')
            if status not in ('idle', 'notLoaded', 'systemError'):
                raise RuntimeError(f'An isolated chat is not idle: {status}')
    finally:
        client.close()
    cache = json.loads((home / 'models_cache.json').read_text())
    changed = []
    for model in cache['models']:
        if model.get('tool_mode') == 'code_mode_only' and model.get('visibility') == 'list':
            changed.append(model['slug'])
            model['tool_mode'] = args.mode
    if not changed:
        raise RuntimeError('No code_mode_only model entries found in the source catalog')
    catalog_path = run / f'model-catalog-{args.mode}.json'
    catalog_path.write_text(json.dumps({'models': cache['models']}, ensure_ascii=False, indent=2) + '\n')
    config_path = home / 'config.toml'
    before = config_path.read_text()
    stamp = datetime.datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d-%H%M%S')
    backup_path = run / f'config-before-tool-mode-{stamp}.toml'
    backup_path.write_text(before)
    features = {'code_mode': args.mode == 'code_mode',
                'code_mode_only': args.mode == 'code_mode_only',
                'apply_patch_streaming_events': True}
    after = set_toml_values(before, {'model_catalog_json': str(catalog_path)}, features)
    config_path.write_text(after)
    outcome = {'mode_requested': args.mode, 'catalog_path': str(catalog_path),
               'config_path': str(config_path), 'backup_path': str(backup_path),
               'changed_model_slugs': changed, 'features_requested': features,
               'previous_backend_pid': old_pid, 'status': 'configuring'}
    backend = None
    stopped = False
    try:
        env = dict(os.environ, CODEX_HOME=str(home))
        validation = subprocess.run([str(CLI), 'features', 'list'], env=env,
                                    capture_output=True, text=True, timeout=20)
        if validation.returncode:
            raise RuntimeError('Installed CLI rejected the config/catalog: ' + validation.stderr[:2000])
        outcome['feature_list'] = [line for line in validation.stdout.splitlines()
                                   if line.split() and line.split()[0] in features]
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
        backend, log_path = start_backend(run, home, report['websocket_url'], stamp)
        client = await_backend(backend, endpoint, home)
        try:
            listing = client.request(2, 'experimentalFeature/list', {'limit': 200})
            outcome['effective_features'] = [{k: item.get(k) for k in ('name', 'enabled', 'stage')}
                for item in listing.get('data', []) if item.get('name') in features]
            config = client.request(3, 'config/read', {'includeLayers': False}).get('config', {})
            outcome['server_selected_model'] = config.get('model')
            outcome['server_catalog_path'] = config.get('model_catalog_json')
            outcome['model_list_count'] = len(client.request(4, 'model/list', {'limit': 100}).get('data', []))
        finally:
            client.close()
        outcome.update(status='ready', backend_pid=backend.pid, backend_log=str(log_path))
        report.setdefault('previous_backend_pids', []).append(old_pid)
        report.update(backend_pid=backend.pid, active_tool_mode_config=outcome)
        report_path.write_text(json.dumps(report, indent=2) + '\n')
    except BaseException as exc:
        outcome.update(status='failed', error=str(exc))
        config_path.write_text(before)
        if backend is not None and backend.poll() is None:
            backend.terminate()
            backend.wait(timeout=10)
        if stopped:
            restored, restored_log = start_backend(run, home, report['websocket_url'], stamp + '-rollback')
            restored_client = await_backend(restored, endpoint, home)
            restored_client.close()
            report['backend_pid'] = restored.pid
            report_path.write_text(json.dumps(report, indent=2) + '\n')
            outcome['rollback_backend_pid'] = restored.pid
            outcome['rollback_backend_log'] = str(restored_log)
        raise
    finally:
        (run / f'tool-mode-{stamp}.json').write_text(json.dumps(outcome, indent=2) + '\n')
        print(json.dumps(outcome, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
