"""Bounded GUI observation lease; legacy terminal-only runtimes stay unchanged."""

import json
import os
from pathlib import Path
import time


def observing(runtime, now=None):
    marker = Path(runtime) / 'app-managed.json'
    try:
        raw = marker.read_text()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    try:
        managed = json.loads(raw)
        if managed.get('version') != 1 or not Path(managed['enabled_file']).is_file():
            return False
        session = json.loads(Path(managed['session_file']).read_text())
        age = (time.time() if now is None else now) - session['heartbeat_at']
        pid = session['owner_pid']
        if (session.get('version') != 1 or session.get('active') is not True or
                not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1 or
                not (-5 <= age < 5)):
            return False
        os.kill(pid, 0)
        return True
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def process_identity(pid):
    import subprocess
    result = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'lstart=,comm='], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None
