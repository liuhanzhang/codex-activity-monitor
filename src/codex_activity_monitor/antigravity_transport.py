"""The desktop Language Server's local, read-only Antigravity interfaces."""

from dataclasses import dataclass, field
from functools import lru_cache
import ctypes
import hashlib
import http.client
import json
import mmap
import os
from pathlib import Path
import re
import socket
import ssl
import struct
import subprocess
import sys
from uuid import UUID


SERVICE = '/exa.language_server_pb.LanguageServerService/'
VERBOSITY = 'CLIENT_TRAJECTORY_VERBOSITY_PROD_UI'
MAX_MESSAGE = 8 * 1024 * 1024
# Public loopback certificate extracted from the 2.32.1 Language Server.
CERTIFICATE = 'f8bbabd57a2dff32992f205ede841f9ba8d95cdf45982e1d0e73aa5834e9cf94'
UNARY = {'GetAllCascadeTrajectories', 'GetCascadeTrajectorySteps', 'GetUserStatus'}
STREAMS = {'JetboxSubscribeToSummaries', 'StreamAgentStateUpdates'}


def uuid_string(value):
    if not isinstance(value, str) or len(value) != 36:
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


class LocalRPCError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DesktopEndpoint:
    pid: int
    port: int
    scope: Path
    executable: Path
    version: str
    csrf: str = field(repr=False)


def _pid_path(lib, pid):
    buffer = ctypes.create_string_buffer(4096)
    if lib.proc_pidpath(pid, buffer, len(buffer)) > 0:
        return Path(os.fsdecode(buffer.value))
    return None


def _arguments(lib, pid):
    key = (ctypes.c_int * 3)(1, 49, pid)  # CTL_KERN, KERN_PROCARGS2
    buffer = ctypes.create_string_buffer(262144)
    size = ctypes.c_size_t(len(buffer))
    if lib.sysctl(key, len(key), buffer, ctypes.byref(size), None, 0):
        return []
    raw = buffer.raw[:size.value]
    count, = struct.unpack_from('=i', raw)
    executable_end = raw.index(b'\0', 4)
    return raw[executable_end:].lstrip(b'\0').split(b'\0', count)[:count]


def _arg(args, name):
    encoded = name.encode('ascii')
    for index, arg in enumerate(args):
        if arg == encoded and index + 1 < len(args):
            return os.fsdecode(args[index + 1])
        if arg.startswith(encoded + b'='):
            return os.fsdecode(arg[len(encoded) + 1:])
    return ''


def installed_apps():
    return [p for p in (Path('/Applications/Antigravity.app'), Path.home()/'Applications/Antigravity.app')
            if (p/'Contents/Resources/bin/language_server').is_file()]


def discover(apps):
    """Return only a listener owned by a same-user child of the desktop app."""
    if sys.platform != 'darwin' or not apps:
        return None
    lib = ctypes.CDLL(None, use_errno=True)
    lib.proc_pidpath.argtypes = (ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32)
    lib.proc_pidinfo.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int)
    lib.sysctl.argtypes = (ctypes.POINTER(ctypes.c_int), ctypes.c_uint, ctypes.c_void_p,
                           ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t)
    app_paths = {(app/'Contents/Resources/bin/language_server').resolve():
                 (app/'Contents/MacOS/Antigravity').resolve() for app in apps}
    listeners = subprocess.run(
        ['/usr/sbin/lsof', '-nP', '-a', '-u', str(os.getuid()), '-c', 'language_server',
         '-iTCP@127.0.0.1', '-sTCP:LISTEN', '-Fpn'],
        capture_output=True, text=True, timeout=3,
    ).stdout
    endpoints = []
    for match in re.finditer(r'^p(\d+)\n(?:[^p\n][^\n]*\n)*?n127\.0\.0\.1:(\d+)$', listeners, re.M):
        pid, port = map(int, match.groups())
        executable = _pid_path(lib, pid)
        if executable not in app_paths:
            continue
        info = ctypes.create_string_buffer(256)
        if lib.proc_pidinfo(pid, 3, 0, info, len(info)) < 24:  # PROC_PIDTBSDINFO
            continue
        actual_pid, parent, owner = struct.unpack_from('=III', info, 12)
        if actual_pid != pid or owner != os.getuid() or _pid_path(lib, parent) != app_paths[executable]:
            continue
        args = _arguments(lib, pid)
        if b'--standalone' not in args or _arg(args, '--subclient_type') != 'hub':
            continue
        csrf = _arg(args, '--csrf_token')
        gemini_dir = Path(_arg(args, '--gemini_dir') or Path.home()/'.gemini')
        data_dir = _arg(args, '--app_data_dir')
        configured_port = _arg(args, '--https_server_port')
        if (not re.fullmatch(r'[A-Za-z0-9_./+=-]{16,256}', csrf) or data_dir != 'antigravity' or
                not gemini_dir.is_absolute() or configured_port not in ('0', str(port))):
            continue
        endpoints.append(DesktopEndpoint(pid, port, (gemini_dir/data_dir).resolve(), executable,
                                         _arg(args, '--override_ide_version'), csrf))
    if len(endpoints) > 1:
        raise LocalRPCError('multiple_desktop_backends')
    return endpoints[0] if endpoints else None


@lru_cache(maxsize=4)
def _tls_context(executable, _stamp):
    begin, end = b'-----BEGIN CERTIFICATE-----', b'-----END CERTIFICATE-----'
    with executable.open('rb') as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as image:
        cursor = 0
        while (start := image.find(begin, cursor)) != -1:
            cursor = start + len(begin)
            finish = image.find(end, cursor, cursor + 8192)
            if finish == -1:
                continue
            try:
                pem = image[start:finish + len(end)].decode('ascii')
                der = ssl.PEM_cert_to_DER_cert(pem)
            except (UnicodeError, ValueError):
                continue
            if hashlib.sha256(der).hexdigest() == CERTIFICATE:
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                context.load_verify_locations(cadata=pem)
                return context
    raise LocalRPCError('unrecognized_desktop_certificate')


class LocalRPC:
    def __init__(self, endpoint):
        self.endpoint = endpoint
        stat = endpoint.executable.stat()
        self.context = _tls_context(endpoint.executable, (stat.st_ino, stat.st_mtime_ns, stat.st_size))

    def _connection(self):
        return http.client.HTTPSConnection('127.0.0.1', self.endpoint.port, context=self.context, timeout=4)

    def call(self, method, params):
        if method not in UNARY:
            raise LocalRPCError('unsupported_method')
        connection = self._connection()
        try:
            connection.request('POST', SERVICE + method, json.dumps(params).encode(),
                               {'Content-Type': 'application/json', 'Connect-Protocol-Version': '1',
                                'x-codeium-csrf-token': self.endpoint.csrf})
            response = connection.getresponse()
            if response.status != 200 or not (response.getheader('Content-Type') or '').startswith('application/json'):
                raise LocalRPCError('request_rejected')
            data = response.read(MAX_MESSAGE + 1)
            if len(data) > MAX_MESSAGE:
                raise LocalRPCError('oversized_response')
            return _object(data)
        finally:
            connection.close()

    def stream(self, method, params):
        if method not in STREAMS:
            raise LocalRPCError('unsupported_method')
        return LocalStream(self._connection(), method, params, self.endpoint.csrf)


def _object(data):
    value = json.loads(data)
    if not isinstance(value, dict):
        raise LocalRPCError('invalid_response')
    return value


class LocalStream:
    """gRPC-Web JSON uses a one-byte flag and a big-endian length."""

    def __init__(self, connection, method, params, csrf):
        self.connection, self.method, self.params = connection, method, params
        self.csrf = csrf
        self.sock = None
        self.closed = False

    def frames(self):
        try:
            self.connection.connect()
            self.sock = self.connection.sock
            if self.closed:
                return
            data = json.dumps(self.params).encode()
            self.connection.request('POST', SERVICE + self.method, struct.pack('>BI', 0, len(data)) + data,
                                    {'Content-Type': 'application/grpc-web+json', 'X-Grpc-Web': '1',
                                     'x-codeium-csrf-token': self.csrf})
            response = self.connection.getresponse()
            if response.status != 200 or not (response.getheader('Content-Type') or '').startswith('application/grpc-web+json'):
                raise LocalRPCError('stream_rejected')
            self.sock.settimeout(None)
            while not self.closed:
                header = response.read(5)
                if not header:
                    break
                flag, size = struct.unpack('>BI', header)
                if size > MAX_MESSAGE:
                    raise LocalRPCError('oversized_frame')
                frame = response.read(size)
                if len(frame) != size:
                    raise LocalRPCError('truncated_frame')
                if flag == 0:
                    yield _object(frame), size
                elif flag == 0x80:
                    if re.search(rb'(?im)^grpc-status:\s*0\s*$', frame):
                        break
                    raise LocalRPCError('stream_failed')
                else:
                    raise LocalRPCError('unsupported_frame')
        finally:
            self.connection.close()

    def cancel(self):
        self.closed = True
        if self.sock is not None:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
