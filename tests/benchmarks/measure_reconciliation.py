"""Compare full-turn metadata with explicitly scoped current-fact projection."""

import argparse
import gc
import json
from pathlib import Path
import platform
import resource
import statistics
import tempfile
import time
import tracemalloc

from codex_activity_monitor.history import HistoryProvider
from codex_activity_monitor.history_refresh import HistoryPacket


def timed(call):
    begin = time.perf_counter(); result = call()
    return result, (time.perf_counter()-begin)*1000


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('full', 'scoped'), default='scoped')
    parser.add_argument('--phases', type=int, default=48000)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.phases < 22: parser.error('need at least 22 phases to separate requests from the seed window')
    with tempfile.TemporaryDirectory(prefix='cam-fact-projection-') as tmp:
        home = Path(tmp); (home/'sessions').mkdir(); path = home/'sessions'/'fixture-thread.jsonl'
        with path.open('w') as stream:
            def row(payload): stream.write(json.dumps({'type': 'event_msg', 'payload': payload})+'\n')
            stream.write(json.dumps({'type': 'session_meta', 'payload': {'id': 'thread', 'history_mode': 'paginated'}})+'\n')
            row({'type': 'task_started', 'turn_id': 'u0'})
            for n in range(args.phases):
                row({'type': 'item_completed', 'thread_id': 'thread', 'turn_id': 'u0',
                     'item': {'id': f'a0-{n}', 'type': 'AgentMessage', 'text': 'raw中文🙂'*64}})
            row({'type': 'task_complete', 'turn_id': 'u0'})
        provider = HistoryProvider()
        _, directory_ms = timed(lambda: provider.index(tmp, 'thread', directory_only=True))
        items = frozenset({('u0', 'a0-0'), ('u0', 'a0-1')}) if args.mode == 'scoped' else None
        def read(): return provider.reconciliation(tmp, 'thread', {'u0'}, items=items)
        gc.collect(); tracemalloc.start(); tracemalloc.reset_peak()
        value, first_ms = timed(read)
        _, peak = tracemalloc.get_traced_memory(); tracemalloc.stop()
        count = len(value['phases'])
        packet = HistoryPacket.capture(value, 8*1024*1024)
        packet_bytes = len(packet.payload) if packet else None
        del value, packet
        hot = []
        for _ in range(3):
            value, ms = timed(read); hot.append(ms); del value
        with path.open('a') as stream:
            stream.write(json.dumps({'type': 'event_msg', 'payload': {'type': 'token_count'}})+'\n')
        value, append_ms = timed(read)
        result = {'version': 1, 'python': platform.python_version(), 'platform': platform.system(),
            'mode': args.mode, 'source_rows': args.phases+4, 'source_bytes': path.stat().st_size,
            'turns': 1, 'phases': args.phases, 'requested_items': 2 if items else None,
            'directory_ms': directory_ms, 'first_summary_instrumented_ms': first_ms,
            'first_summary_peak_allocated_bytes': peak, 'returned_phases': count,
            'bounded_packet_bytes': packet_bytes, 'hot_summary_ms': statistics.median(hot),
            'append_summary_ms': append_ms, 'cache': provider.diagnostics(),
            'note': 'Synthetic single giant turn; first summary uses tracemalloc, hot/append do not. Directory built separately; no real collector or GUI.'}
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        result['benchmark_peak_rss_bytes'] = rss if platform.system() == 'Darwin' else rss*1024
        args.output.write_text(json.dumps(result, indent=2)+'\n')


if __name__ == '__main__': main()
