"""Startup authorization and process identity; connected leases are memory only."""

import json
import math
import os
from pathlib import Path
import time

from .observation_lease import LeaseError, ObservationLease, SessionGrant


def load_lease(runtime):
    """Read configuration once, before the collector accepts live connections.

    Files authorize a finite bootstrap only. Connected ownership is renewed
    exclusively through state.sock; this object performs no later file reads.
    """
    try:
        raw = (Path(runtime)/'app-managed.json').read_text()
    except FileNotFoundError:
        return ObservationLease(managed=False)
    try:
        marker = json.loads(raw)
        if (not isinstance(marker, dict) or type(marker.get('version')) is not int or marker['version'] != 1 or
                type(marker.get('lease_protocol')) is not int or marker['lease_protocol'] != 2):
            raise LeaseError('unsupported_lease_protocol')
        if not Path(marker['enabled_file']).is_file():
            raise LeaseError('observation_disabled')
        record = json.loads(Path(marker['session_file']).read_text())
        grant = SessionGrant.from_record(record)
        created = record.get('created_at')
        if type(created) not in (int, float) or not math.isfinite(created) or not -5 <= time.time()-created < 10:
            raise LeaseError('lease_expired')
        os.kill(grant.owner_pid, 0)
        return ObservationLease(managed=True, grant=grant)
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as error:
        if isinstance(error, LeaseError):
            raise
        raise LeaseError('invalid_lease') from None


def process_identity(pid):
    import subprocess
    try:
        result = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'lstart=,comm='], capture_output=True, text=True, timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None
