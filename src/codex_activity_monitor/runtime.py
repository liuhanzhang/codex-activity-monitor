"""Private runtime files, snapshot publication and collector ownership."""

import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time

from .i18n import DEFAULT_LANGUAGE, available_languages, tr, validate_language
from .execution import is_frozen, bundle_resources

ROOT = Path(__file__).resolve().parents[2]
REAL_CLI = Path('/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex')


def source_identity(*modules):
    """Fingerprint code at process startup, for rollout diagnostics only."""
    if is_frozen():
        manifest=json.loads((bundle_resources()/'build-manifest.json').read_text())
        hashes={name:manifest['modules'][name] for name in modules}
    else:
        directory=Path(__file__).resolve().parent
        hashes={name:hashlib.sha256((directory/(name+'.py')).read_bytes()).hexdigest() for name in modules}
    return {'source_sha256':hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest(),
            'modules':hashes}


def default_runtime():
    # A fresh GUI installation and its terminal reader share one runtime.
    return Path.home()/'Library/Application Support/CodexActivityMonitor/run'


def runtime_dir(value=None, language=DEFAULT_LANGUAGE):
    path = Path(value or os.environ.get('CODEX_ACTIVITY_MONITOR_RUNTIME', default_runtime())).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    if len(str(path/'events.sock').encode())>103:
        raise ValueError(tr('cli.runtime_long', language))
    return path


def atomic_json(path, value):
    fd, name = tempfile.mkstemp(prefix='.'+path.name, dir=path.parent)
    try:
        with os.fdopen(fd,'w') as out:
            json.dump(value,out,ensure_ascii=False,separators=(',',':'))
            out.write('\n')
        os.replace(name,path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_snapshot(path):
    try:
        value = json.loads((path/'snapshot.json').read_text())
        if isinstance(value, dict):
            return value
    except (OSError,ValueError):
        pass
    return {'threads':[],'backends':{},'counters':{},'updated_at':None}


def snapshot_age(snapshot, now=None):
    updated = snapshot.get('updated_at')
    if type(updated) not in (int, float) or not math.isfinite(updated):
        return None
    return (time.time() if now is None else now) - updated


def snapshot_fresh(snapshot, now=None):
    """Match the Swift reader's clock tolerance, including legacy replay views."""
    age = snapshot_age(snapshot, now)
    return snapshot.get('running', True) is True and age is not None and -5 <= age < 3


def collector_alive(runtime):
    """The lifetime lock proves ownership even when snapshot publication fails.

    Never create the lock while probing. A stale file or a recycled PID is not
    evidence that a collector still owns this runtime.
    """
    try:
        with (runtime / 'collector.lock').open('r') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
    except FileNotFoundError:
        pass
    return False


class SnapshotPublisher:
    """Retry disposable snapshot writes without ending live observation."""

    def __init__(self, runtime):
        self.path = runtime / 'snapshot.json'
        self.failures = 0
        self.last_error = None

    def publish(self, snapshot):
        snapshot['persistence'] = {'snapshot_write_failures': self.failures,
                                   'last_snapshot_error': self.last_error}
        try:
            atomic_json(self.path, snapshot)
        except OSError as error:
            self.failures += 1
            self.last_error = {'type': type(error).__name__, 'errno': error.errno}
            return False
        return True


class SnapshotCadence:
    """Publish lifecycle changes on the next tick; throttle numeric updates."""

    def __init__(self, interval=1):
        self.interval = interval
        self.next_update = 0
        self.previous_state = None

    def should_publish(self, snapshot, now):
        menu = snapshot['menu_presentation']
        state = (snapshot['overview']['coverage'], menu['mode'], menu['reason'],
                 menu['active_conversation_count'], tuple(sorted(
                     (t.get('backend') or '', t['id'], t.get('turn_id') or '', t.get('status') or '',
                      t.get('display_message', {}).get('key', ''), bool(t.get('has_active_work')))
                     for t in snapshot['threads'])))
        if state == self.previous_state and now < self.next_update:
            return False
        self.previous_state = state
        self.next_update = now + self.interval
        return True


def ui_language(runtime, explicit=None):
    if explicit is not None:
        return validate_language(explicit)
    try:
        settings = json.loads((runtime/'ui-settings.json').read_text())
        value = settings.get('language') if (isinstance(settings, dict) and
            type(settings.get('version')) is int and settings.get('version') == 1) else None
        return value if value in available_languages() else DEFAULT_LANGUAGE
    except (OSError, ValueError):
        return DEFAULT_LANGUAGE


def save_ui_language(runtime, language):
    atomic_json(runtime/'ui-settings.json', {'version': 1, 'language': validate_language(language)})
