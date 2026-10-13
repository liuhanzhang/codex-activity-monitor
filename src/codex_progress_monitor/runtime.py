"""Private runtime paths and atomic, metrics-only snapshots."""

import json
import hashlib
import os
from pathlib import Path
import tempfile

from .i18n import DEFAULT_LANGUAGE, available_languages, tr, validate_language

ROOT = Path(__file__).resolve().parents[2]
REAL_CLI = Path('/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex')


def source_identity(*modules):
    """Fingerprint code at process startup, for rollout diagnostics only."""
    directory=Path(__file__).resolve().parent
    hashes={name:hashlib.sha256((directory/(name+'.py')).read_bytes()).hexdigest() for name in modules}
    return {'source_sha256':hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest(),
            'modules':hashes}


def runtime_dir(value=None, language=DEFAULT_LANGUAGE):
    path = Path(value or os.environ.get('CODEX_PROGRESS_RUNTIME', ROOT/'.local/runtime')).expanduser().resolve()
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
        return json.loads((path/'snapshot.json').read_text())
    except (OSError,ValueError):
        return {'threads':[],'backends':{},'counters':{},'updated_at':None}


def ui_language(runtime, explicit=None):
    if explicit is not None:
        return validate_language(explicit)
    try:
        settings = json.loads((runtime/'ui-settings.json').read_text())
        value = settings.get('language') if isinstance(settings, dict) and settings.get('version') == 1 else None
        return value if value in available_languages() else DEFAULT_LANGUAGE
    except (OSError, ValueError):
        return DEFAULT_LANGUAGE


def save_ui_language(runtime, language):
    atomic_json(runtime/'ui-settings.json', {'version': 1, 'language': validate_language(language)})
