"""Single-phase detail cost in a wide turn, including its actual page output."""

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

from codex_progress_monitor.history import HistoryProvider
from codex_progress_monitor.timeline import phase_id
from codex_progress_monitor.timeline_query import TimelineQuery


class FullTurnAdapter(HistoryProvider):
    """Reproduce the old detail reader boundary, without changing current code."""
    def detail_view(self, scope, thread, turn, selected_id, observed_phase=None):
        return self.selected_view(scope, thread, turn)


def timed(call):
    begin = time.perf_counter(); result = call()
    return result, (time.perf_counter()-begin)*1000


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('full', 'detail'), default='detail')
    parser.add_argument('--phases', type=int, default=48000)
    parser.add_argument('--body-repeats', type=int, default=64)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.phases < 1 or args.body_repeats < 1: parser.error('phase count and body repeats must be positive')
    with tempfile.TemporaryDirectory(prefix='cpm-detail-projection-') as tmp:
        home = Path(tmp); (home/'sessions').mkdir(); path = home/'sessions'/'fixture-thread.jsonl'
        with path.open('w') as stream:
            def row(payload): stream.write(json.dumps({'type': 'event_msg', 'payload': payload})+'\n')
            stream.write(json.dumps({'type': 'session_meta', 'payload': {'id': 'thread', 'history_mode': 'paginated'}})+'\n')
            row({'type': 'task_started', 'turn_id': 'u0'})
            for n in range(args.phases):
                row({'type': 'item_completed', 'thread_id': 'thread', 'turn_id': 'u0',
                     'item': {'id': f'a0-{n}', 'type': 'AgentMessage', 'text': 'raw中文🙂'*args.body_repeats}})
            row({'type': 'task_complete', 'turn_id': 'u0'})
        provider = FullTurnAdapter() if args.mode == 'full' else HistoryProvider()
        directory, directory_ms = timed(lambda: provider.index(tmp, 'thread', directory_only=True))
        assert directory is not None
        initial_cache = provider.diagnostics()
        del directory  # Do not retain an oversized, deliberately uncached catalogue in the harness.
        query = TimelineQuery(history=provider)
        selected = phase_id(tmp, 'thread', 'u0', 'model', 'a0-0')
        def read(): return query.detail(tmp, 'thread', 'u0', selected, limit=8)
        gc.collect(); tracemalloc.start(); tracemalloc.reset_peak()
        value, first_ms = timed(read)
        _, peak = tracemalloc.get_traced_memory(); tracemalloc.stop()
        assert value['sections'][0]['text'] == ('raw中文🙂'*args.body_repeats)[:8]
        hot = []
        for _ in range(3):
            _, ms = timed(read); hot.append(ms)
        with path.open('a') as stream:
            stream.write(json.dumps({'type': 'event_msg', 'payload': {'type': 'token_count'}})+'\n')
        _, append_ms = timed(read)
        result = {'version': 1, 'python': platform.python_version(), 'platform': platform.system(),
            'mode': args.mode, 'source_rows': args.phases+4, 'source_bytes': path.stat().st_size,
            'turns': 1, 'phases': args.phases, 'directory_ms': directory_ms,
            'body_repeats': args.body_repeats, 'cache_after_initial_directory': initial_cache,
            'first_detail_instrumented_ms': first_ms, 'first_detail_peak_allocated_bytes': peak,
            'hot_detail_ms': statistics.median(hot), 'append_detail_ms': append_ms,
            'response_bytes': len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()),
            'cache': provider.diagnostics(),
            'note': 'Synthetic single-message detail; first read uses tracemalloc, hot/append do not. Source directory built separately; full mode adapts the former full-turn reader.'}
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        result['benchmark_peak_rss_bytes'] = rss if platform.system() == 'Darwin' else rss*1024
        args.output.write_text(json.dumps(result, indent=2)+'\n')


if __name__ == '__main__': main()
