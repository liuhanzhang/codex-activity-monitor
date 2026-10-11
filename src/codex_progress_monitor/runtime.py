"""Private runtime paths and atomic, metrics-only snapshots."""

import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[2]
REAL_CLI = Path('/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex')


def runtime_dir(value=None):
    path = Path(value or os.environ.get('CODEX_PROGRESS_RUNTIME', ROOT/'.local/runtime')).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    if len(str(path/'events.sock').encode())>103:
        raise ValueError('Runtime path is too long for a macOS Unix socket; choose a shorter --runtime path')
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
