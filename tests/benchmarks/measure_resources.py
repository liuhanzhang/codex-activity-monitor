#!/usr/bin/env python3
"""Opt-in, synthetic resource measurements. Never opens the user's Codex home.

Run with PYTHONPATH=src .local/venv/bin/python tests/benchmarks/measure_resources.py.
RSS is this benchmark process's high-water mark, not a product memory guarantee.
"""

import argparse
import gc
import json
from pathlib import Path
import platform
import random
import resource
import statistics
import tempfile
import threading
import time
import tracemalloc
from unittest.mock import patch

from codex_activity_monitor.history import HistoryProvider
from codex_activity_monitor.metrics import TextStream, TokenCounter, count_text
from codex_activity_monitor.publication import DetachedSnapshot
from codex_activity_monitor.engine import Engine
from codex_activity_monitor.events import Event, filter_event
from codex_activity_monitor.resource_budget import retained_bytes
from codex_activity_monitor.timeline_storage import TimelineStorage


def elapsed(call):
    begin = time.perf_counter()
    value = call()
    return value, (time.perf_counter()-begin)*1000


def percentile(values, fraction):
    return sorted(values)[min(len(values)-1, int((len(values)-1)*fraction))]


def index_case(turns, seed, items_per_turn=4, repetitions=10, padding_rows=0):
    rng = random.Random(seed)
    with tempfile.TemporaryDirectory(prefix='cam-resource-') as directory:
        home = Path(directory); (home/'sessions').mkdir()
        path = home/'sessions'/'fixture-thread.jsonl'
        with path.open('w') as stream:
            def row(kind, payload): stream.write(json.dumps({'type': kind, 'payload': payload})+'\n')
            row('session_meta', {'id': 'thread', 'history_mode': 'paginated'})
            for turn in range(turns):
                row('event_msg', {'type': 'task_started', 'turn_id': f'u{turn}'})
                for item in range(items_per_turn):
                    row('event_msg', {'type': 'item_completed', 'turn_id': f'u{turn}', 'thread_id': 'thread',
                        'item': {'id': f'a{turn}-{item}', 'type': 'AgentMessage', 'text': 'raw中文🙂'*64}})
                row('event_msg', {'type': 'task_complete', 'turn_id': f'u{turn}'})
            for _ in range(padding_rows):
                row('event_msg', {'type': 'token_count', 'info': {'synthetic': True}})
        provider = HistoryProvider()
        index, cold_ms = elapsed(lambda:provider.index(str(home), 'thread', directory_only=True))
        phase_count = getattr(index, 'phase_records', len(index.phases))
        del index  # Do not keep an evicted full index alive merely for a counter.
        selected = f'u{rng.randrange(turns)}'
        def view():
            # The baseline only exposes whole-rollout views. Keep that behavior
            # measurable when comparing a checkout from before selected views.
            if hasattr(provider, 'selected_view'): return provider.selected_view(str(home), 'thread', selected)
            return provider.view(str(home), 'thread')
        times = []; sizes = []
        gc.collect(); tracemalloc.start(); tracemalloc.reset_peak()
        for _ in range(repetitions):
            value, ms = elapsed(view); times.append(ms); sizes.append(len(value.phases)); del value
        current, peak = tracemalloc.get_traced_memory(); tracemalloc.stop()
        with path.open('a') as stream:
            stream.write(json.dumps({'type': 'event_msg', 'payload': {'type': 'task_complete', 'turn_id': selected}})+'\n')
        _, append_ms = elapsed(view)
        return {'turns': turns, 'phases': phase_count, 'source_rows': 1+turns*(items_per_turn+2)+padding_rows+1,
            'items_per_turn': items_per_turn, 'repetitions': repetitions, 'source_bytes': path.stat().st_size,
            'cold_index_ms': cold_ms, 'hot_view_ms': {'p50': statistics.median(times), 'p95': percentile(times, .95)},
            'hot_view_returned_phases': max(sizes), 'hot_view_peak_allocated_bytes': peak,
            'hot_view_remaining_allocated_bytes': current, 'append_and_view_ms': append_ms,
            'cache': provider.diagnostics() if hasattr(provider, 'diagnostics') else {'indices': len(provider.indices)}}


def counter_case():
    streams = [TextStream(('b', str(i), 'text', 0), 'text', 'upstream', 'unknown', 1) for i in range(256)]
    for stream in streams: stream.append('x'*65536, 2)
    gate = threading.Event()
    def blocked(text, encoding):
        gate.wait(3)
        return 1, 0.01
    counter = TokenCounter(isolated=False)  # Inject a deterministic blocked counting function.
    try:
        with patch('codex_activity_monitor.metrics.count_text', blocked):
            _, submit_ms = elapsed(lambda:counter.tick(streams, 2))
            first = counter.pool._work_queue.qsize()+1
            for model in ('gpt-4', 'unknown'):
                for stream in streams: stream.set_model(model)
                counter.tick(streams, 2.5)
            pending = counter.pool._work_queue.qsize()+1
            diagnostics = counter.diagnostics() if hasattr(counter, 'diagnostics') else {}
            gate.set(); counter.close()
        return {'streams': 256, 'text_bytes_per_stream': 65536, 'submit_ms': submit_ms,
            'blocked_tasks_after_tick': first, 'blocked_tasks_after_model_changes': pending,
            'blocked_input_bytes_after_model_changes': pending*65536, 'diagnostics': diagnostics,
            'note': 'Blocked synthetic counter; input bytes inferred from equal-size work, not Python RSS.'}
    finally:
        gate.set()


def snapshot_case():
    snapshot = {'threads': [{'id': str(i), 'activities': [{'id': str(j), 'label': 'tool', 'status': 'active'}
        for j in range(8)]} for i in range(100)]}
    detached, copy_ms = elapsed(lambda:DetachedSnapshot.capture(snapshot))
    # This measures metadata serialization only; live/raw transcripts are absent.
    encoded, encode_ms = elapsed(lambda:json.dumps(snapshot).encode())
    return {'chats': 100, 'activities': 800, 'copy_ms': copy_ms, 'json_encode_ms': encode_ms, 'json_bytes': len(encoded)}


def event_case():
    engine = Engine(False)
    count_text('warm', 'o200k_base')
    times = []; max_streams = max_items = 0
    begin = time.perf_counter()
    try:
        for number in range(1000):
            turn, item = f'u{number}', f'i{number}'
            def send(method, params):
                value = filter_event('protocol_out', {'method': method, 'params': {'threadId': 't', **params}})
                engine.ingest(Event('b', 'protocol_out', value, number*3+100, 0))
            started = time.perf_counter()
            send('turn/started', {'turn': {'id': turn, 'status': 'inProgress'}})
            send('item/started', {'turnId': turn, 'item': {'id': item, 'type': 'agentMessage'}})
            send('item/agentMessage/delta', {'turnId': turn, 'itemId': item, 'delta': 'hello 中文🙂'})
            send('item/completed', {'turnId': turn, 'item': {'id': item, 'type': 'agentMessage', 'status': 'completed'}})
            send('turn/completed', {'turn': {'id': turn, 'status': 'completed'}})
            engine.tick(number*3+101)
            times.append((time.perf_counter()-started)*1000)
            max_streams = max(max_streams, len(engine.streams)); max_items = max(max_items, len(engine.items))
        return {'turns': 1000, 'events': 5000, 'wall_seconds': time.perf_counter()-begin,
            'batch_ms': {'p50': statistics.median(times), 'p95': percentile(times, .95), 'p99': percentile(times, .99)},
            'max_streams': max_streams, 'max_items': max_items,
            'identity_budget': engine.identity_budget.diagnostics(),
            'identity_ledger_heap_bytes': retained_bytes(engine.identity_budget.closed, engine.identity_budget.reserved),
            'note': 'Synthetic accelerated turns, synchronous small counts; not a natural soak or GUI draw measurement.'}
    finally: engine.close()


def metadata_case():
    with tempfile.TemporaryDirectory(prefix='cam-metadata-budget-') as tmp:
        entered, release = threading.Event(), threading.Event()
        original = TimelineStorage._write_batch
        def blocked(store, batch, reset):
            entered.set(); release.wait(5)
            return original(store, batch, reset)
        with patch.object(TimelineStorage, '_write_batch', blocked):
            store = TimelineStorage(Path(tmp)/'metadata.sqlite3')
            try:
                store.put('phase', {'id': 'inflight', 'status': 'active'})
                if not entered.wait(1): raise RuntimeError('metadata barrier did not start')
                started = time.perf_counter()
                for n in range(20_000): store.put('phase', {'id': str(n), 'status': 'completed', 'label': 'tool'})
                for n in range(2000): store.put('phase', {'id': f'wide-{n}', 'scope_id': '/synthetic/'+'a'*3000, 'status': 'completed'})
                producer_ms = (time.perf_counter()-started)*1000
                stats = store.diagnostics()
                release.set(); started = time.perf_counter(); store.close()
                return {'narrow_records': 20000, 'wide_records': 2000, 'producer_ms': producer_ms,
                    'recovery_drain_ms': (time.perf_counter()-started)*1000, 'blocked_diagnostics': stats,
                    'derived_database_bytes': store.path.stat().st_size,
                    'note': 'Synthetic blocked metadata write; overflow invalidates only the derived sidecar.'}
            finally:
                release.set(); store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--label', default='current-worktree')
    parser.add_argument('--seed', type=int, default=20261023)
    parser.add_argument('--index-turns', type=int, nargs='+', default=[100, 1000])
    parser.add_argument('--items-per-turn', type=int, default=4)
    parser.add_argument('--repetitions', type=int, default=10)
    parser.add_argument('--padding-rows', type=int, default=0)
    args = parser.parse_args()
    result = {'version': 1, 'label': args.label, 'seed': args.seed, 'python': platform.python_version(),
        'platform': platform.system(), 'machine': platform.machine(),
        'index': [index_case(n, args.seed, args.items_per_turn, args.repetitions, args.padding_rows) for n in args.index_turns],
        'counter': counter_case(), 'tokenizer': {}, 'snapshot': snapshot_case(), 'events': event_case(), 'metadata': metadata_case()}
    # Warm BPE separately from a blocked scheduler's queue-admission measurement.
    count_text('warm', 'o200k_base')
    for size in (65536, 1048576):
        text = ('hello 中文🙂\n'*((size//17)+1)).encode()[:size].decode(errors='ignore')
        _, ms = elapsed(lambda:count_text(text, 'o200k_base'))
        result['tokenizer'][str(size)] = {'actual_utf8_bytes': len(text.encode()), 'count_ms': ms}
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    result['benchmark_peak_rss_bytes'] = rss if platform.system() == 'Darwin' else rss*1024
    encoded = json.dumps(result, ensure_ascii=False, indent=2)+'\n'
    if args.output: args.output.write_text(encoded)
    else: print(encoded, end='')


if __name__ == '__main__': main()
