"""Owned synthetic peer for Swift protocol and cancellation acceptance."""

import json
from pathlib import Path
import socket
import sys
import time

from codex_progress_monitor.publication import DetachedSnapshot, encode_frame
from codex_progress_monitor.state_transport import StateServer
from codex_progress_monitor.observation_lease import ObservationLease, SessionGrant


runtime, mode = Path(sys.argv[1]), sys.argv[2]
frames = [json.loads(line) for line in Path(__file__).with_name('state-protocol-v1.jsonl').read_text().splitlines()]
path = runtime/'state.sock'
if mode in ('normal', 'stale', 'owner'):
    lease = ObservationLease(managed=True, grant=SessionGrant('i'*32, 100, 's'*64)) if mode == 'owner' else None
    server = StateServer(path, lease=lease)
    server.publish(DetachedSnapshot.capture(frames[1]['payload']))
    (runtime/'ready').touch()
    try:
        while not (runtime/'stop').exists():
            server.progress(time.monotonic()-(5 if mode == 'stale' else 0))
            time.sleep(.02)
    finally:
        server.close()
else:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(path))
        listener.listen(1)
        listener.settimeout(.2)
        (runtime/'ready').touch()
        connection = None
        while connection is None and not (runtime/'stop').exists():
            try:
                connection, _ = listener.accept()
            except TimeoutError:
                pass
        if connection:
            with connection, connection.makefile('rb') as source:
                def send(value):
                    raw = encode_frame(value)
                    if mode == 'fragmented':
                        for offset in range(0, len(raw), 7):
                            connection.sendall(raw[offset:offset+7])
                            time.sleep(.0005)
                    else:
                        connection.sendall(raw)
                try:
                    for line in source:
                        request = json.loads(line)
                        with (runtime/'requests.jsonl').open('a') as recording:
                            recording.write(json.dumps(request)+'\n')
                        reply = dict(frames[0 if request['kind'] == 'subscribe' else 2], nonce=request['nonce'])
                        if mode == 'unsupported':
                            reply['protocol_version'] = 2
                        if mode == 'wrong_nonce' and request['kind'] == 'heartbeat':
                            reply['nonce'] = 'expired-foreign-nonce'
                        send(reply)
                        if request['kind'] == 'subscribe':
                            if mode == 'oversized':
                                connection.sendall(b'x'*8192+b'\n')
                            else:
                                state = dict(frames[1])
                                if mode == 'foreign_instance':
                                    state['instance_id'] = 'old-instance'
                                send(state)
                                if mode == 'reordered':
                                    send(dict(state, sequence=3))
                                    send(dict(state, sequence=2))
                except (BrokenPipeError, ConnectionResetError):
                    pass
