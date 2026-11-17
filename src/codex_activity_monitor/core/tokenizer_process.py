"""One owned tokenizer child, with finite request time and shutdown.

Only the counter's executor thread uses the pipe. The deadline/close path kills
the owned child to interrupt blocked reads or writes; it never closes another
thread's descriptor. No history, IDs, or Engine state enter the child.
"""

import json
from dataclasses import asdict, dataclass
import math
import multiprocessing
import threading


MAX_INPUT_BYTES = 8*1024*1024
MAX_RESPONSE_BYTES = 4096


class TokenizerUnavailable(Exception):
    pass


@dataclass(frozen=True, slots=True)
class TokenizerStats:
    starts: int
    timeouts: int
    deadline_seconds: float
    running: bool
    closed: bool


def count_worker(connection):
    from .metrics import count_text, TokenizationError
    try:
        while True:
            request = connection.recv_bytes(MAX_INPUT_BYTES+1024)
            header, _, text = request.partition(b'\n')
            encoding = json.loads(header)['encoding']
            try:
                tokens, cost = count_text(text.decode('utf-8'), encoding)
                result = {'tokens': tokens, 'cost_ms': cost}
            except TokenizationError:
                result = {'error': 'tokenizer_error'}
            connection.send_bytes(json.dumps(result, separators=(',', ':')).encode())
    except (EOFError, OSError):
        return
    finally:
        connection.close()


class TokenizerProcess:
    def __init__(self, timeout=3, *, target=count_worker):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('invalid_tokenizer_deadline')
        self.timeout, self.target = timeout, target
        self.context = multiprocessing.get_context('spawn')
        self.lock = threading.Lock()
        self.process = self.connection = self.active = None
        self.closed = self.broken = False
        self.starts = self.timeouts = 0
        self.stats = TokenizerStats(0, 0, timeout, False, False)

    def diagnostics(self):
        # Snapshot collection must not wait for process startup/reaping.
        return asdict(self.stats)

    def _publish_stats(self):
        self.stats = TokenizerStats(self.starts, self.timeouts, self.timeout,
                                    self.active is not None, self.closed)

    def _discard(self):
        """Caller holds lifecycle lock and has finished pipe I/O."""
        if self.connection:
            self.connection.close()
            self.connection = None
        if self.process:
            if self.process.is_alive():
                self.process.kill()
            self.process.join(.25)
            if not self.process.is_alive():
                self.process.close()
                self.process = None
        self.broken = self.process is not None

    def _ensure(self):
        if self.closed:
            raise TokenizerUnavailable('closed')
        if self.process and (self.broken or not self.process.is_alive()):
            self._discard()
        if self.broken:
            # Never accumulate replacements while a previous owned child has
            # not actually exited, including an uninterruptible OS operation.
            raise TokenizerUnavailable('previous_worker_not_exited')
        if self.process is None:
            parent, child = self.context.Pipe(duplex=True)
            process = self.context.Process(target=self.target, args=(child,), daemon=True,
                                           name='monitor-tokenizer')
            try:
                process.start()
            except Exception as error:
                parent.close(); child.close()
                raise TokenizerUnavailable('start_failed') from error
            child.close()
            self.process, self.connection = process, parent
            self.starts += 1

    def count(self, text: str, encoding: str) -> tuple[int, float]:
        with self.lock:
            if self.active is not None:
                raise TokenizerUnavailable('concurrent_count')
            self._ensure()
            token = object()
            self.active = token
            self._publish_stats()
            process, connection = self.process, self.connection
        expired = threading.Event()
        def expire():
            with self.lock:
                if self.active is token:
                    expired.set()
                    self.timeouts += 1
                    self.broken = True
                    self._publish_stats()
                    if process.is_alive():
                        process.kill()
        timer = threading.Timer(self.timeout, expire)
        timer.daemon = True
        timer.start()
        try:
            content = text.encode('utf-8')
            if len(content) > MAX_INPUT_BYTES or not isinstance(encoding, str) or len(encoding) > 128:
                raise TokenizerUnavailable('input_limit')
            header = json.dumps({'encoding': encoding}, separators=(',', ':')).encode()
            connection.send_bytes(header+b'\n'+content)
            result = json.loads(connection.recv_bytes(MAX_RESPONSE_BYTES))
            if not isinstance(result, dict):
                raise ValueError('invalid_count_response')
            if result.get('error') == 'tokenizer_error':
                raise TokenizerUnavailable('tokenizer_error')
            tokens, cost = result.get('tokens'), result.get('cost_ms')
            if (not isinstance(tokens, int) or isinstance(tokens, bool) or tokens < 0 or
                    not isinstance(cost, (int, float)) or isinstance(cost, bool) or not math.isfinite(cost) or cost < 0):
                raise ValueError('invalid_count_response')
            with self.lock:
                if expired.is_set() or self.closed:
                    raise TokenizerUnavailable('deadline_or_closed')
            return tokens, cost
        except TokenizerUnavailable:
            raise
        except (OSError, EOFError, ValueError, TypeError) as error:
            with self.lock:
                self.broken = True
            raise TokenizerUnavailable('worker_failed') from error
        finally:
            timer.cancel()
            with self.lock:
                self.active = None
                self._publish_stats()
                if self.broken or self.closed:
                    self._discard()

    def close(self):
        with self.lock:
            self.closed = True
            self._publish_stats()
            if self.process and self.process.is_alive():
                self.process.kill()
            # Active I/O belongs to the executor thread. Killing the child
            # closes its pipe end and lets that thread release its own handle.
            if self.active is None:
                self._discard()
