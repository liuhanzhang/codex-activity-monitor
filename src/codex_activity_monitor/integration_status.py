"""Read-only integration evidence, independent of configuration transactions."""

import hashlib
import json
import os
from pathlib import Path
import time

from .integration import KEYS
from .runtime import bridge_identity


REVISION_ENV = 'CODEX_ACTIVITY_MONITOR_CONFIG_REVISION'
EXPECTED_ENV = 'CODEX_ACTIVITY_MONITOR_CONFIG_FINGERPRINT'


def fingerprint(values):
    return hashlib.sha256(json.dumps({key: values.get(key) for key in KEYS}, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def configuration_revision(installed, app, actual):
    value = {'environment': fingerprint(installed), 'app': str(app), 'cli': str(actual),
             'bridge': bridge_identity()['source_sha256']}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def startup_evidence(environment=None):
    environment = os.environ if environment is None else environment
    revision, expected = environment.get(REVISION_ENV), environment.get(EXPECTED_ENV)
    if not revision or not expected:
        return {}
    return {'configuration_revision': revision,
            'configuration_matches': fingerprint(environment) == expected}


def restart_candidates(control, receipt):
    from .app_session import process_identity
    probe = getattr(control.services, 'inspect_dependents', control.services.dependents)
    candidates = []
    deadline = time.monotonic() + 1
    for value in probe(receipt)[:32]:
        pid = value.get('pid')
        if time.monotonic() >= deadline:
            break
        if type(pid) is int and pid > 1:
            identity = process_identity(pid)
            if identity:
                candidates.append({'pid': pid, 'identity': identity})
    return candidates


def inspect_integration(control, receipt, state):
    result = {'version': 1, 'configuration': 'unknown', 'runtime': 'unknown', 'restart': 'unknown',
              'operation': 'idle', 'checked_at': time.time(), 'matching_bridges': 0,
              'old_bridges': 0, 'unknown_bridges': 0, 'reasons': []}
    if state in ('new', 'restored', 'purged'):
        result.update(configuration='disabled', runtime='disabled', restart='not_required')
        return result
    result['operation'] = {'configuring': 'configuring', 'restoring': 'restoring', 'error': 'failed'}.get(state, 'idle')
    try:
        current = {key: control.environment.get(key) for key in KEYS}
        restored = set(receipt.get('restored_keys', []))
        if state == 'pending_cleanup' and restored == set(KEYS) and not control.enabled.exists():
            result['configuration'] = 'disabled'
        elif state == 'configured' and current == receipt.get('installed') and control.enabled.exists():
            result['configuration'] = 'enabled'
        elif state == 'error' and current == receipt.get('previous') and not control.enabled.exists():
            result['configuration'] = 'disabled'
        else:
            result['configuration'] = 'partial'
            result['reasons'].append('configuration_changed')
    except (OSError, RuntimeError, ValueError):
        result['reasons'].append('configuration_check_failed')
    runtime = Path(receipt.get('runtime', control.root / 'run'))
    revision = receipt.get('configuration_revision')
    result['configuration_revision'] = revision
    deadline = time.monotonic() + 1.5
    from .app_session import process_identity
    try:
        records = runtime / 'bridges'
        for index, path in enumerate(records.glob('*.json')):
            if index >= 32 or time.monotonic() > deadline:
                result['reasons'].append('process_check_incomplete')
                result['unknown_bridges'] += 1
                break
            if path.is_symlink() or path.stat().st_size > 16384:
                result['unknown_bridges'] += 1
                continue
            info = json.loads(path.read_text())
            pid = info.get('pid')
            if type(pid) is not int or pid <= 1:
                continue
            identity = process_identity(pid)
            if identity is None:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    continue
                except PermissionError:
                    pass
                result['unknown_bridges'] += 1
            elif info.get('identity') != identity:
                continue
            elif (revision and info.get('configuration_revision') == revision and
                  info.get('configuration_matches') is True and info.get('observation_protocol') == 1):
                result['matching_bridges'] += 1
            else:
                # A verified resident bridge without startup revision evidence
                # predates that evidence. A restart is needed to establish the
                # current integration, even if its observation protocol works.
                result['old_bridges'] += 1
        probe = getattr(control.services, 'inspect_dependents', control.services.dependents)
        dependencies = probe(receipt)
        visible = not any(p.get('reason') == 'process_visibility_unknown' for p in dependencies)
        running = any(p.get('reason') in ('codex_running', 'bridge_running') for p in dependencies) or bool(result['matching_bridges'] or result['old_bridges'])
        result['processes_running'] = running if visible else None
        old_hosts = False
        for process in receipt.get('restart_processes', [])[:32]:
            if time.monotonic() > deadline:
                result['unknown_bridges'] += 1
                break
            if type(process.get('pid')) is int and process_identity(process['pid']) == process.get('identity'):
                old_hosts = True
        if not visible:
            result['reasons'].append('process_check_failed')
        if result['configuration'] == 'disabled':
            result.update(runtime='disabled', restart='required' if running else 'not_required' if visible and not result['unknown_bridges'] else 'unknown')
        elif result['old_bridges'] or old_hosts:
            result.update(runtime='partial' if result['matching_bridges'] else 'waiting_for_codex', restart='required')
        elif result['matching_bridges']:
            # Registration proves startup configuration, not current IPC health.
            result.update(runtime='awaiting_handshake', restart='unknown' if result['unknown_bridges'] else 'not_required')
        elif visible and not running and not result['unknown_bridges']:
            result.update(runtime='waiting_for_codex', restart='not_required')
        else:
            result['runtime'] = 'waiting_for_connection'
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        result['reasons'].append('process_check_failed')
    return result
