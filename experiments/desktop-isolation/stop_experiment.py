#!/usr/bin/env python3
"""Stop only the processes recorded for one of this experiment's runs."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

root = Path(__file__).resolve().parent
run = Path(sys.argv[1]) if len(sys.argv) > 1 else Path((root / 'latest-run.txt').read_text().strip())
run = run.resolve()
if not run.is_relative_to(root / 'runs'):
    raise SystemExit('Expected a run within this experiment directory')
path = run / 'report.json'
report = json.loads(path.read_text())
protected = {item['pid'] for item in report['baseline_processes']}
desktops = report.get('isolated_desktop_processes', [])
targets = [(item['pid'], '/ChatGPT') for item in desktops]
targets.append((report['backend_pid'], '/codex'))
stopped = []
for pid, suffix in targets:
    if pid in protected:
        raise SystemExit(f'Refusing to stop a baseline process: {pid}')
    result = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'comm='], capture_output=True, text=True)
    if result.returncode:
        continue
    if not result.stdout.strip().endswith(suffix):
        raise SystemExit(f'Process identity changed for PID {pid}')
    os.kill(pid, signal.SIGTERM)
    stopped.append(pid)
deadline = time.monotonic() + 10
alive = stopped[:]
while alive and time.monotonic() < deadline:
    alive = [pid for pid in alive if subprocess.run(
        ['/bin/ps', '-p', str(pid), '-o', 'pid='], capture_output=True
    ).returncode == 0]
    if alive:
        time.sleep(0.25)
report['experiment_processes_stopped'] = stopped
report['experiment_processes_still_alive'] = alive
path.write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({'stopped': stopped, 'still_alive': alive}))
