#!/usr/bin/env python3
"""Bounded observation beside the original CLI; protocol bytes stay untouched."""

import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading
import time


def main():
    actual = Path(os.environ['PREFLIGHT_REAL_CLI']).resolve()
    if actual == Path(__file__).resolve():
        raise RuntimeError('Recursive CLI override')
    args = sys.argv[1:]
    # Desktop also uses the CLI for version and helper commands.
    if 'app-server' not in args or 'daemon' in args or not os.environ.get('PREFLIGHT_CAPTURE'):
        os.execv(str(actual), [str(actual), *args])
    output = Path(os.environ['PREFLIGHT_CAPTURE'])
    output.parent.mkdir(parents=True, exist_ok=True)
    limit = int(os.environ.get('PREFLIGHT_CAPTURE_LIMIT', 64 * 1024 * 1024))
    delay = float(os.environ.get('PREFLIGHT_WRITER_DELAY', '0'))
    packets = queue.Queue(maxsize=int(os.environ.get('PREFLIGHT_QUEUE_PACKETS', '128')))
    stats = {'pid': os.getpid(), 'child_pid': None, 'dropped_packets': 0,
             'captured_rows': 0, 'captured_bytes': 0, 'capture_limit_rows': 0,
             'writer_errors': 0, 'incomplete_lines': 0, 'protocol_parse_errors': 0,
             'raw_bytes': {'stdin': 0, 'stdout': 0, 'stderr': 0}}
    env = dict(os.environ)
    env['CODEX_CLI_PATH'] = str(actual)
    child = subprocess.Popen([str(actual), *args], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env=env, start_new_session=True)
    stats['child_pid'] = child.pid

    def stop(signum, _frame):
        try:
            os.killpg(child.pid, signum)
        except ProcessLookupError:
            pass

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, stop)

    def writer():
        buffers = {'stdin': b'', 'stdout': b'', 'stderr': b''}
        try:
            handle = output.open('w', encoding='utf-8')
            os.chmod(output, 0o600)
        except OSError:
            stats['writer_errors'] += 1
            handle = None
        try:
            while True:
                packet = packets.get()
                if packet is None:
                    break
                source, observed_ns, observed_timestamp, data = packet
                if delay:
                    time.sleep(delay)
                buffers[source] += data
                while b'\n' in buffers[source]:
                    line, buffers[source] = buffers[source].split(b'\n', 1)
                    try:
                        value = json.loads(line)
                    except (ValueError, UnicodeError):
                        if source != 'stderr':
                            stats['protocol_parse_errors'] += 1
                        continue
                    timestamp = None
                    direction = 'protocol_in' if source == 'stdin' else 'protocol_out'
                    if source == 'stderr':
                        message = value.get('fields', {}).get('message', '')
                        if not message.startswith('Received message '):
                            continue
                        try:
                            value, timestamp = json.loads(message[17:]), value.get('timestamp')
                        except ValueError:
                            continue
                        if not value.get('type', '').startswith('response.'):
                            continue
                        direction = 'upstream'
                    row = json.dumps({'direction': direction, 'observed_ns': observed_ns,
                                      'observed_timestamp': observed_timestamp,
                                      'source_timestamp': timestamp, 'event': value},
                                     ensure_ascii=False) + '\n'
                    size = len(row.encode())
                    if stats['captured_bytes'] + size > limit:
                        stats['capture_limit_rows'] += 1
                        continue
                    if handle is not None:
                        try:
                            handle.write(row)
                            handle.flush()
                            stats['captured_rows'] += 1
                            stats['captured_bytes'] += size
                        except OSError:
                            stats['writer_errors'] += 1
                            handle.close()
                            handle = None
                # A dropped chunk can lose a line boundary; never retain unbounded input.
                if len(buffers[source]) > 2 * 1024 * 1024:
                    buffers[source] = b''
                    stats['incomplete_lines'] += 1
            stats['incomplete_lines'] += sum(bool(value) for value in buffers.values())
        finally:
            if handle is not None:
                handle.close()

    observer = threading.Thread(target=writer, daemon=True)
    observer.start()

    def pump(source, src, dest):
        try:
            while True:
                data = os.read(src.fileno(), 65536)
                if not data:
                    break
                observed_ns = time.monotonic_ns()
                stats['raw_bytes'][source] += len(data)
                # Observation cannot block or change the transport output.
                view = memoryview(data)
                while view:
                    written = os.write(dest.fileno(), view)
                    view = view[written:]
                try:
                    packets.put_nowait((source, observed_ns, time.time(), data))
                except queue.Full:
                    stats['dropped_packets'] += 1
        except (BrokenPipeError, OSError):
            pass
        finally:
            if source == 'stdin':
                try:
                    dest.close()
                except OSError:
                    pass

    incoming = threading.Thread(target=pump, args=('stdin', sys.stdin.buffer, child.stdin), daemon=True)
    outgoing = [threading.Thread(target=pump, args=(name, pipe, target), daemon=True)
                for name, pipe, target in [('stdout', child.stdout, sys.stdout.buffer),
                                            ('stderr', child.stderr, sys.stderr.buffer)]]
    incoming.start()
    for thread in outgoing:
        thread.start()
    code = child.wait()
    for thread in outgoing:
        thread.join(timeout=5)
    # Waiting for diagnostic drain is bounded, after the protocol has already exited.
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            packets.put(None, timeout=0.05)
            break
        except queue.Full:
            pass
    observer.join(timeout=max(0, deadline - time.monotonic()))
    stats.update(child_returncode=code, writer_drained=not observer.is_alive())
    output.with_suffix('.meta.json').write_text(json.dumps(stats, indent=2) + '\n')
    if code < 0:
        signal.signal(-code, signal.SIG_DFL)
        os.kill(os.getpid(), -code)
    return code


if __name__ == '__main__':
    sys.exit(main())
