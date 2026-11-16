"""Seekable view over verified rollout ranges; never creates a merged transcript."""
import hashlib
import json
import os

from .history_read import HistoryChanged, file_stamp
from .history_source import MAX_HEADER


class SourceStream:
    def __init__(self, source):
        self.source = source
        self.files = {}
        self.ranges = []
        self.position = 0
        self.last_record = None
        self.native = len(source.parts) == 1 and source.parts[0].start == 0 and source.parts[0].end is None

    def __enter__(self):
        try:
            offset = 0
            for part in self.source.parts:
                head = part.header
                if head.path not in self.files:
                    if head.path.resolve() != head.path or not head.path.is_file():
                        raise HistoryChanged()
                    stream = head.path.open('rb')
                    self.files[head.path] = stream
                    stat = os.fstat(stream.fileno())
                    first = stream.readline(MAX_HEADER+1)
                    if len(first) > MAX_HEADER or not first.endswith(b'\n'):
                        raise HistoryChanged()
                    if self.native and hashlib.sha256(first).hexdigest() != head.digest:
                        try:
                            payload = json.loads(first)['payload']
                            if any(payload.get(k) != head.payload.get(k) for k in
                                   ('history_base', 'subagent_history_start_ordinal', 'attached_thread')):
                                raise HistoryChanged()
                        except (ValueError, KeyError, TypeError):
                            raise HistoryChanged() from None
                    if (not self.native and
                            ((stat.st_dev, stat.st_ino) != head.fingerprint or hashlib.sha256(first).hexdigest() != head.digest)):
                        raise HistoryChanged()
                stream = self.files[head.path]
                end = part.end if part.end is not None else os.fstat(stream.fileno()).st_size
                if not part.start <= end <= os.fstat(stream.fileno()).st_size:
                    raise HistoryChanged()
                self.ranges.append((offset, offset+end-part.start, part))
                offset += end-part.start
            self.size = offset
            self.before = self.stamp()
            self.verify()
            return self
        except BaseException:
            self.close()
            raise

    def __exit__(self, kind, *_):
        try:
            if kind is None:
                self.verify()
        finally:
            self.close()

    def close(self):
        for stream in self.files.values():
            stream.close()

    def stamp(self):
        stamps = tuple(file_stamp(os.fstat(stream.fileno())) for stream in self.files.values())
        if self.native:
            return stamps[0]
        generation = self.source.generation
        return (generation, generation, self.size, stamps, 0)

    def verify(self):
        if self.source.guard is not None and not self.source.guard():
            raise HistoryChanged()
        for path, stream in self.files.items():
            if file_stamp(path.stat()) != file_stamp(os.fstat(stream.fileno())):
                raise HistoryChanged()
        if self.stamp() != self.before:
            raise HistoryChanged()

    def seek(self, offset, whence=0):
        if whence == 1:
            offset += self.position
        elif whence == 2:
            offset += self.size
        if offset < 0:
            raise ValueError('invalid_offset')
        self.position = offset
        return offset

    def tell(self):
        return self.position

    def _range(self, offset):
        return next(((start, stop, part) for start, stop, part in self.ranges if start <= offset < stop), None)

    def owner_at(self, offset):
        found = self._range(offset)
        if found is None:
            raise HistoryChanged()
        return found[2].header.owner

    def project(self, row, offset):
        """Validate actual source ownership before projecting a fork's reading view."""
        start, _, part = self._range(offset)
        if offset == 0 and row.get('type') == 'session_meta':
            return row
        payload = row.get('payload')
        if not isinstance(payload, dict):
            return row
        if len(self.source.parts) > 1:
            ordinal = row.get('ordinal')
            if (type(ordinal) is not int or ordinal <= part.header.ordinal or
                    part.end_ordinal is not None and ordinal >= part.end_ordinal):
                raise HistoryChanged()
            if offset == start and part.start == part.header.length and ordinal != part.header.ordinal+1:
                raise HistoryChanged()
            if self.last_record is not None:
                previous_end, previous_ordinal, previous_part = self.last_record
                if previous_end == offset and previous_part == part and ordinal != previous_ordinal+1:
                    raise HistoryChanged()
            self.last_record = self.position, ordinal, part
            if part.minimum_ordinal is not None and ordinal < part.minimum_ordinal:
                return None
        if payload.get('thread_id') not in (None, part.header.owner):
            raise HistoryChanged()
        if payload.get('thread_id') is not None and part.header.owner != self.source.thread:
            return {**row, 'payload': {**payload, 'thread_id': self.source.thread}}
        return row

    def read(self, size=-1):
        remaining = self.size-self.position if size < 0 else min(size, self.size-self.position)
        chunks = []
        while remaining > 0:
            start, stop, part = self._range(self.position)
            stream = self.files[part.header.path]
            stream.seek(part.start+self.position-start)
            count = min(remaining, stop-self.position)
            data = stream.read(count)
            if len(data) != count:
                raise HistoryChanged()
            chunks.append(data)
            self.position += count
            remaining -= count
        return b''.join(chunks)

    def readline(self, size=-1):
        found = self._range(self.position)
        if found is None or size == 0:
            return b''
        start, stop, part = found
        stream = self.files[part.header.path]
        stream.seek(part.start+self.position-start)
        count = stop-self.position if size < 0 else min(size, stop-self.position)
        data = stream.readline(count)
        self.position += len(data)
        return data
