#!/usr/bin/env python3
"""Launch a separate Desktop profile and a matching localhost app-server.

Creates a fresh Codex home without copying the primary profile or credentials.
The backend and Desktop are left running for the experiment.
"""

import base64
import datetime
import hashlib
import json
import os
from pathlib import Path
import plistlib
import socket
import struct
import subprocess
import time
import uuid
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent
APP = Path('/Applications/ChatGPT.app')
CLI = APP / 'Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex'


class WebSocketProbe:
    def __init__(self, path):
        self.sock = socket.socket(socket.AF_INET if isinstance(path, tuple) else socket.AF_UNIX,
                                  socket.SOCK_STREAM)
        self.sock.settimeout(8)
        self.sock.connect(path if isinstance(path, tuple) else str(path))
        self.buffer = bytearray()
        key = base64.b64encode(os.urandom(16)).decode()
        headers = (
            'GET /rpc HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n'
            'Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n'
            f'Sec-WebSocket-Key: {key}\r\n\r\n'
        )
        self.sock.sendall(headers.encode())
        while b'\r\n\r\n' not in self.buffer:
            part = self.sock.recv(65536)
            if not part:
                raise RuntimeError('Socket closed during WebSocket handshake')
            self.buffer.extend(part)
        raw_headers, remaining = bytes(self.buffer).split(b'\r\n\r\n', 1)
        self.buffer = bytearray(remaining)
        accept = base64.b64encode(hashlib.sha1(
            (key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()
        ).digest()).decode()
        if not raw_headers.startswith(b'HTTP/1.1 101 ') or accept.encode() not in raw_headers:
            raise RuntimeError('Invalid WebSocket handshake response')

    def read_exact(self, size):
        while len(self.buffer) < size:
            part = self.sock.recv(max(65536, size - len(self.buffer)))
            if not part:
                raise RuntimeError('WebSocket closed')
            self.buffer.extend(part)
        data = bytes(self.buffer[:size])
        del self.buffer[:size]
        return data

    def send_frame(self, data, opcode=1):
        size = len(data)
        header = bytes([0x80 | opcode])
        if size < 126:
            header += bytes([0x80 | size])
        elif size < 65536:
            header += bytes([0x80 | 126]) + struct.pack('!H', size)
        else:
            header += bytes([0x80 | 127]) + struct.pack('!Q', size)
        mask = os.urandom(4)
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(data))
        self.sock.sendall(header + mask + masked)

    def send_json(self, value):
        self.send_frame(json.dumps(value).encode())

    def receive_json(self):
        message = bytearray()
        while True:
            first, second = self.read_exact(2)
            size = second & 0x7f
            if size == 126:
                size = struct.unpack('!H', self.read_exact(2))[0]
            elif size == 127:
                size = struct.unpack('!Q', self.read_exact(8))[0]
            if size > 64 * 1024 * 1024:
                raise RuntimeError('Unexpectedly large WebSocket frame')
            mask = self.read_exact(4) if second & 0x80 else None
            data = self.read_exact(size)
            if mask:
                data = bytes(value ^ mask[index % 4] for index, value in enumerate(data))
            opcode = first & 0x0f
            if opcode == 8:
                raise RuntimeError('Server closed WebSocket')
            if opcode == 9:
                self.send_frame(data, opcode=10)
                continue
            if opcode == 10:
                continue
            if opcode not in (0, 1):
                raise RuntimeError(f'Unexpected WebSocket opcode: {opcode}')
            message.extend(data)
            if first & 0x80:
                self.last_message_bytes = len(message)
                return json.loads(message)

    def request(self, request_id, method, params=None):
        value = {'id': request_id, 'method': method, 'params': {} if params is None else params}
        self.send_json(value)
        while True:
            reply = self.receive_json()
            if reply.get('id') == request_id:
                if 'error' in reply:
                    raise RuntimeError(f'{method}: {reply["error"]}')
                return reply['result']

    def initialize(self):
        result = self.request(1, 'initialize', {
            'clientInfo': {'name': 'codex_activity_monitor_probe', 'version': '0.1.0'},
        })
        self.send_json({'method': 'initialized'})
        return result

    def close(self):
        try:
            self.send_frame(b'', opcode=8)
        except OSError:
            pass
        finally:
            self.sock.close()


def process_snapshot():
    lines = subprocess.check_output(
        ['/bin/ps', '-axo', 'pid=,ppid=,comm='], text=True
    ).splitlines()
    rows = []
    for line in lines:
        parts = line.strip().split(None, 2)
        if len(parts) == 3 and (parts[2].endswith('/ChatGPT') or parts[2].endswith('/codex')):
            rows.append({'pid': int(parts[0]), 'ppid': int(parts[1]), 'exe': parts[2]})
    return rows


def main():
    os.umask(0o077)
    timestamp = datetime.datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d-%H%M%S')
    run = ROOT / 'runs' / f'{timestamp}-{uuid.uuid4().hex[:6]}'
    home = run / 'codex-home'
    data = run / 'user-data'
    sqlite = home / 'sqlite'
    for directory in (run, home, data, sqlite):
        directory.mkdir(parents=True, mode=0o700, exist_ok=False)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    endpoint = ('127.0.0.1', port)
    websocket_url = f'ws://127.0.0.1:{port}'
    version = plistlib.loads((APP / 'Contents/Info.plist').read_bytes())
    before = process_snapshot()
    env = dict(os.environ)
    for key in list(env):
        if key.startswith('CODEX_'):
            env.pop(key)
    env.update({
        'CODEX_HOME': str(home),
        'CODEX_SQLITE_HOME': str(sqlite),
        'LOG_FORMAT': 'json',
        'RUST_LOG': 'codex_app_server=info',
        'CODEX_INTERNAL_APP_SERVER_REMOTE_CONTROL_DISABLED': '1',
    })
    with (run / 'backend.log').open('wb') as log:
        backend = subprocess.Popen(
            [str(CLI), '-c', 'features.code_mode_host=true',
             'app-server', '--listen', websocket_url],
            env=env, cwd=run, stdin=subprocess.DEVNULL,
            stdout=log, stderr=log, start_new_session=True,
        )
    report = {
        'run_directory': str(run),
        'desktop_version': version['CFBundleShortVersionString'],
        'desktop_build': version['CFBundleVersion'],
        'codex_home': str(home),
        'user_data': str(data),
        'listen_url': websocket_url,
        'transport': 'localhost-websocket',
        'websocket_url': websocket_url,
        'backend_pid': backend.pid,
        'credentials_copied': False,
        'baseline_processes': before,
    }
    report_path = run / 'report.json'
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    (ROOT / 'latest-run.txt').write_text(str(run) + '\n')
    clients = []
    try:
        deadline = time.monotonic() + 20
        while True:
            if backend.poll() is not None:
                raise RuntimeError(f'Isolated backend exited with code {backend.returncode}')
            if time.monotonic() > deadline:
                raise RuntimeError('Isolated backend did not start its listener')
            try:
                client = WebSocketProbe(endpoint)
                clients.append(client)
                break
            except (ConnectionRefusedError, socket.timeout):
                pass
            time.sleep(0.2)
        clients.append(WebSocketProbe(endpoint))
        initializations = [client.initialize() for client in clients]
        report['concurrent_probe_clients'] = len(clients)
        report['probe_codex_homes'] = [item.get('codexHome') for item in initializations]
        report['probe_loaded_thread_counts'] = [
            len(client.request(2, 'thread/loaded/list').get('data', []))
            for client in clients
        ]
        if any(item.get('codexHome') != str(home) for item in initializations):
            raise RuntimeError('Probe connected to an unexpected Codex home')
        assignments = {
            'CODEX_HOME': str(home),
            'CODEX_SQLITE_HOME': str(sqlite),
            'CODEX_ELECTRON_USER_DATA_PATH': str(data),
            'CODEX_APP_SERVER_WS_URL': websocket_url,
            'CODEX_APP_SERVER_FORCE_CLI': '0',
        }
        command = ['/usr/bin/open', '-n']
        for key, value in assignments.items():
            command.extend(['--env', f'{key}={value}'])
        command.extend([str(APP), '--args', f'--user-data-dir={data}'])
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError(f'Isolated Desktop launch failed: {result.stderr.strip()}')
        report['launch_requested'] = True
        # A bounded wait lets the new instance register before recording its PID.
        deadline = time.monotonic() + 12
        before_pids = {item['pid'] for item in before}
        while time.monotonic() < deadline:
            after = process_snapshot()
            new_desktops = [
                item for item in after
                if item['exe'].endswith('/ChatGPT') and item['pid'] not in before_pids
            ]
            if new_desktops:
                break
            time.sleep(0.5)
        report['isolated_desktop_processes'] = new_desktops
        report['primary_desktop_pids_still_running'] = [
            item['pid'] for item in before if item['exe'].endswith('/ChatGPT')
            and any(current['pid'] == item['pid'] for current in after)
        ]
        if not new_desktops:
            raise RuntimeError('No separate Desktop process appeared')
        log_root = Path.home() / 'Library/Logs/com.openai.codex'
        desktop_pid = new_desktops[0]['pid']
        deadline = time.monotonic() + 25
        report['desktop_connection_ready'] = False
        while time.monotonic() < deadline:
            candidates = list(log_root.rglob(f'*-{desktop_pid}-t0-*.log'))
            for candidate in candidates:
                for line in candidate.read_text(errors='replace').splitlines():
                    if ('app_server_connection.state_changed' in line
                            and 'next=connected' in line and 'transport=websocket' in line):
                        report['desktop_connection_ready'] = True
                        report['desktop_connection_log'] = str(candidate)
                        report['desktop_connection_evidence'] = line
                        break
            if report['desktop_connection_ready']:
                break
            time.sleep(0.5)
        report['probe_loaded_thread_counts_after_desktop_started'] = [
            len(client.request(3, 'thread/loaded/list').get('data', []))
            for client in clients
        ]
        report['status'] = 'launched'
    except Exception as error:
        report['status'] = 'failed'
        report['error'] = str(error)
        if not report.get('launch_requested'):
            backend.terminate()
        raise
    finally:
        for client in clients:
            client.close()
        report_path.write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
