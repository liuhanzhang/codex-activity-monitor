#!/usr/bin/env python3
"""One isolated Desktop boot using the wrapper; no new source extraction."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time

from run_live import CLI, ROOT, TRACE_FILTER


def desktops():
    result = []
    for line in subprocess.check_output(['/bin/ps', '-axo', 'pid=,comm='], text=True).splitlines():
        parts = line.strip().split(None, 1)
        if len(parts)==2 and parts[1].endswith('/ChatGPT'):
            result.append(int(parts[0]))
    return result


def read_rows(path):
    if not path.exists():
        return []
    rows = []
    with path.open() as source:
        for line in source:
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    run = args.run_dir.resolve()
    run.mkdir(parents=True, exist_ok=False)
    home = Path((ROOT/'experiments/desktop-isolation/latest-run.txt').read_text().strip())/'codex-home'
    assert home.resolve().is_relative_to((ROOT/'experiments/desktop-isolation/runs').resolve())
    before_config = (home/'config.toml').read_bytes()
    (run/'config-before.toml').write_bytes(before_config)
    profile = run/'desktop-user-data'
    wrapper = run/'codex-observed'
    wrapper.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' ' +
                       shlex.quote(str(Path(__file__).with_name('forwarder.py'))) + ' "$@"\n')
    wrapper.chmod(0o700)
    capture = run/'desktop-capture.jsonl'
    before = desktops()
    values = {'CODEX_HOME': str(home), 'CODEX_SQLITE_HOME': str(home/'sqlite'),
              'CODEX_ELECTRON_USER_DATA_PATH': str(profile), 'CODEX_CLI_PATH': str(wrapper),
              'CODEX_APP_SERVER_WS_URL': '', 'CODEX_APP_SERVER_FORCE_CLI': '1',
              'CODEX_APP_SERVER_USE_LOCAL_DAEMON': '0',
              'PREFLIGHT_REAL_CLI': str(CLI), 'PREFLIGHT_CAPTURE': str(capture),
              'LOG_FORMAT': 'json', 'RUST_LOG': TRACE_FILTER}
    command = ['/usr/bin/open', '-n', '-g']
    for key, value in values.items():
        command.extend(['--env', key+'='+value])
    command.extend(['/Applications/ChatGPT.app', '--args', '--user-data-dir='+str(profile)])
    report = {'scope': 'isolated Desktop boot/initialize regression; live turns covered by owned stdio probe',
              'prior_desktop_pids': before, 'isolated_desktop_pids': [],
              'status': 'starting', 'source_reextracted': False, 'model_requests_started': False,
              'config_before_sha256': hashlib.sha256(before_config).hexdigest()}
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=20)
        deadline = time.monotonic()+45
        while time.monotonic()<deadline:
            for pid in desktops():
                if pid in before or pid in report['isolated_desktop_pids']:
                    continue
                identity = subprocess.check_output(['/bin/ps', '-p', str(pid), '-o', 'args='], text=True)
                if str(profile) in identity:
                    report['isolated_desktop_pids'].append(pid)
            rows = read_rows(capture)
            initial = [r['event'] for r in rows if r['direction']=='protocol_in' and r['event'].get('method')=='initialize']
            if initial:
                request_id = initial[0]['id']
                replies = [r['event'] for r in rows if r['direction']=='protocol_out' and r['event'].get('id')==request_id]
                if replies and replies[0].get('result',{}).get('codexHome')==str(home):
                    initialized = any(r['direction']=='protocol_in' and r['event'].get('method')=='initialized' for r in rows)
                    if initialized:
                        report.update(status='passed', initialized=True,
                                      protocol_rows=len(rows), client_name=initial[0]['params']['clientInfo']['name'])
                        break
            time.sleep(0.25)
        else:
            report.update(status='failed', error='No isolated Desktop initialization observed within 45 seconds')
    except Exception as exc:
        report.update(status='failed', error=str(exc))
    finally:
        for pid in report['isolated_desktop_pids']:
            try:
                identity = subprocess.check_output(['/bin/ps', '-p', str(pid), '-o', 'args='], text=True)
                assert str(profile) in identity
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic()+12
        while time.monotonic()<deadline:
            if not set(report['isolated_desktop_pids']) & set(desktops()):
                break
            time.sleep(0.2)
        report['isolated_desktop_stopped'] = not bool(set(report['isolated_desktop_pids']) & set(desktops()))
        report['prior_desktops_still_running'] = all(pid in desktops() for pid in before)
        # This is the idle isolated configuration owned by the experiment, not the primary home.
        after = (home/'config.toml').read_bytes()
        report['desktop_boot_updated_isolated_config'] = after != before_config
        if after != before_config:
            (run/'config-after.toml').write_bytes(after)
            (home/'config.toml').write_bytes(before_config)
        report['isolated_config_restored'] = (home/'config.toml').read_bytes()==before_config
        rows = read_rows(capture)
        report['unexpected_turn_started'] = any(r['direction']=='protocol_out' and r['event'].get('method')=='turn/started' for r in rows)
        meta = capture.with_suffix('.meta.json')
        deadline = time.monotonic()+10
        while not meta.exists() and time.monotonic()<deadline:
            time.sleep(0.1)
        if meta.exists():
            report['forwarder'] = json.loads(meta.read_text())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps(report))
    return 0 if report['status']=='passed' and report['prior_desktops_still_running'] else 1


if __name__ == '__main__':
    sys.exit(main())
