"""One verified file handle for all records used to render an activity detail."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import sys
from typing import Literal


class HistoryChanged(ValueError):
    def __init__(self):
        super().__init__('history_changed')


def file_stamp(stat):
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def explicit_turn(payload):
    metadata = payload.get('metadata')
    return payload.get('turn_id') or (metadata.get('turn_id') if isinstance(metadata, dict) else None)


class PlainStream:
    def __init__(self, path, thread):
        self.path, self.thread = path, thread

    def __enter__(self):
        self.stream = self.path.open('rb')
        return self

    def __exit__(self, *_):
        self.stream.close()

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def stamp(self):
        return file_stamp(os.fstat(self.stream.fileno()))

    def verify(self):
        if self.stamp() != file_stamp(self.path.stat()):
            raise HistoryChanged()

    def owner_at(self, _):
        return self.thread

    def project(self, row, _):
        return row


def open_history(path, thread, source=None):
    return source.open() if source is not None else PlainStream(path, thread)


@dataclass(frozen=True, slots=True)
class HistoryReference:
    offset: int
    length: int
    digest: str
    fingerprint: tuple
    source: Literal['raw', 'canonical', 'output', 'turn_context', 'task_started']
    turn_id: str | None
    context: HistoryReference | None = None

    def __deepcopy__(self, memo):
        # Every field, including a context reference, is immutable.
        return self


class HistoryRead:
    def __init__(self, path, thread, fingerprint, header_digest, source=None):
        self.path, self.thread, self.fingerprint = path, thread, fingerprint
        self.stream = None
        self.header_digest = header_digest
        self.verified = {}
        self.source = source

    def __enter__(self):
        self.context = open_history(self.path, self.thread, self.source)
        self.stream = self.context.__enter__()
        try:
            self.before = self.stream.stamp()
            if self.before[:2] != self.fingerprint:
                raise HistoryChanged()
            first = self.stream.readline()
            if hashlib.sha256(first).hexdigest() != self.header_digest:
                raise HistoryChanged()
            header = json.loads(first)
            if (not isinstance(header, dict) or header.get('type') != 'session_meta' or
                    not isinstance(header.get('payload'), dict) or header['payload'].get('id') != self.thread):
                raise HistoryChanged()
            return self
        except BaseException:
            self.context.__exit__(*sys.exc_info())
            raise

    def __exit__(self, kind, *_):
        try:
            if kind is None:
                self.stream.verify()
                if self.stream.stamp() != self.before:
                    raise HistoryChanged()
        finally:
            self.context.__exit__(kind, None, None)

    def read_ref(self, ref: HistoryReference):
        if ref in self.verified:
            return self.verified[ref]
        if (ref.fingerprint != self.fingerprint or ref.offset < 0 or ref.length <= 0 or
                ref.offset+ref.length > self.before[2]):
            raise HistoryChanged()
        self.stream.seek(ref.offset)
        raw = self.stream.read(ref.length)
        if hashlib.sha256(raw).hexdigest() != ref.digest:
            raise HistoryChanged()
        row = json.loads(raw)
        if not isinstance(row, dict) or not isinstance(row.get('payload'), dict):
            raise HistoryChanged()
        payload, outer = row['payload'], row.get('type')
        if ref.source == 'canonical':
            if (outer != 'event_msg' or payload.get('type') != 'item_completed' or
                    payload.get('turn_id') != ref.turn_id or
                    payload.get('thread_id') not in (None, self.stream.owner_at(ref.offset))):
                raise HistoryChanged()
        elif ref.source in ('turn_context', 'task_started'):
            attribution = payload.get('turn_attribution')
            turn = payload.get('turn_id') or (attribution.get('turn_id') if isinstance(attribution, dict) else None)
            if (turn != ref.turn_id or
                    (ref.source == 'turn_context' and outer != 'turn_context') or
                    (ref.source == 'task_started' and (outer != 'event_msg' or payload.get('type') not in ('task_started', 'turn_started')))):
                raise HistoryChanged()
        elif ref.source in ('raw', 'output'):
            valid_types = ('function_call_output', 'custom_tool_call_output') if ref.source == 'output' else (
                'message', 'agentMessage', 'reasoning', 'function_call', 'custom_tool_call')
            if outer != 'response_item' or payload.get('type') not in valid_types:
                raise HistoryChanged()
            turn = explicit_turn(payload)
            if turn is not None:
                if turn != ref.turn_id:
                    raise HistoryChanged()
            elif ref.context is not None and ref.context.turn_id == ref.turn_id:
                self.read_ref(ref.context)
            else:
                raise HistoryChanged()
        else:
            raise HistoryChanged()
        self.verified[ref] = payload
        return payload
