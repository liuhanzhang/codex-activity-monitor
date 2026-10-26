"""A native tokenizer cannot own collector shutdown or block its event loop."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
import threading
import time
import unittest

from codex_activity_monitor.tokenizer_process import TokenizerProcess, TokenizerUnavailable
from codex_activity_monitor.metrics import TextStream, TokenCounter


def synthetic_worker(connection):
    try:
        while True:
            request = connection.recv_bytes()
            encoding = json.loads(request.partition(b'\n')[0])['encoding']
            if encoding == 'blocked': time.sleep(60)
            if encoding == 'crash': os._exit(17)
            if encoding == 'oversized': connection.send_bytes(b'x'*5000)
            else: connection.send_bytes(b'{"tokens":3,"cost_ms":0.1}')
    except (EOFError, OSError):
        return


def unread_worker(connection):
    time.sleep(60)


class TokenizerProcessTests(unittest.TestCase):
    def worker(self, **kwargs):
        worker = TokenizerProcess(**kwargs)
        self.addCleanup(worker.close)
        return worker

    def test_diagnostics_never_waits_for_process_lifecycle_operations(self):
        worker = self.worker()
        entered, release = threading.Event(), threading.Event()
        def hold():
            with worker.lock:
                entered.set(); release.wait(2)
        thread = threading.Thread(target=hold); thread.start()
        try:
            self.assertTrue(entered.wait(1))
            begin = time.monotonic(); stats = worker.diagnostics()
            self.assertLess(time.monotonic()-begin, .1)
            self.assertEqual(stats['starts'], 0)
        finally:
            release.set(); thread.join(1)

    def test_real_counts_reuse_one_child_and_native_panic_is_contained(self):
        worker = self.worker(timeout=3)
        self.assertEqual(worker.count('word', 'o200k_base')[0], 1)
        first = worker.diagnostics()['starts']
        self.assertEqual(worker.count('hello world', 'o200k_base')[0], 2)
        self.assertEqual(worker.diagnostics()['starts'], first)
        with self.assertRaises(TokenizerUnavailable): worker.count(' '*1048576, 'o200k_base')
        self.assertEqual(worker.count('word', 'o200k_base')[0], 1)
        self.assertEqual(worker.diagnostics()['starts'], first)

    def test_deadline_kills_only_the_owned_child_and_next_count_restarts_it(self):
        worker = self.worker(timeout=.3, target=synthetic_worker)
        begin = time.monotonic()
        with self.assertRaises(TokenizerUnavailable): worker.count('input', 'blocked')
        self.assertLess(time.monotonic()-begin, 1.5)
        self.assertEqual(worker.diagnostics()['timeouts'], 1)
        self.assertEqual(worker.count('input', 'good')[0], 3)
        self.assertEqual(worker.diagnostics()['starts'], 2)

    def test_deadline_also_bounds_a_blocked_request_pipe(self):
        worker = self.worker(timeout=.3, target=unread_worker)
        begin = time.monotonic()
        with self.assertRaises(TokenizerUnavailable): worker.count('x'*(8*1024*1024), 'good')
        self.assertLess(time.monotonic()-begin, 1.5)
        self.assertFalse(worker.diagnostics()['running'])

    def test_crash_and_oversized_response_do_not_poison_the_next_count(self):
        worker = self.worker(timeout=1, target=synthetic_worker)
        for encoding in ('crash', 'oversized'):
            with self.assertRaises(TokenizerUnavailable): worker.count('input', encoding)
            self.assertEqual(worker.count('input', 'good')[0], 3)

    def test_close_interrupts_current_work_and_rejects_new_counts(self):
        worker = self.worker(timeout=30, target=synthetic_worker)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(worker.count, 'input', 'blocked')
            deadline = time.monotonic()+2
            while not worker.diagnostics()['running'] and time.monotonic() < deadline:
                time.sleep(.002)
            self.assertTrue(worker.diagnostics()['running'])
            begin = time.monotonic(); worker.close()
            with self.assertRaises(TokenizerUnavailable): future.result(timeout=1)
            self.assertLess(time.monotonic()-begin, 1.5)
        with self.assertRaises(TokenizerUnavailable): worker.count('input', 'good')

    def test_counter_close_cancels_queued_work_and_interrupts_the_active_child(self):
        counter = TokenCounter(count_deadline=30)
        counter.worker = self.worker(timeout=30, target=unread_worker)
        self.addCleanup(counter.close)
        streams = [TextStream(('b', str(n), 'text', 0), 'text', 'upstream', 'unknown', 1) for n in range(2)]
        for stream in streams: stream.append('x'*1048576, 2)
        counter.tick(streams, 2)
        deadline = time.monotonic()+2
        while not counter.worker.diagnostics()['running'] and time.monotonic() < deadline:
            time.sleep(.002)
        self.assertTrue(counter.worker.diagnostics()['running'])
        begin = time.monotonic(); counter.close()
        self.assertLess(time.monotonic()-begin, 1.5)
        self.assertEqual(counter.diagnostics()['jobs'], 0)
        self.assertEqual(counter.diagnostics()['pending_bytes'], 0)
        self.assertTrue(all(s.count_state == 'failed' and s.rate(2) is None for s in streams))


if __name__ == '__main__': unittest.main()
