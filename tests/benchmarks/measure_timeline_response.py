"""Whole-turn metadata and reply cost, including the existing 8 MiB boundary."""

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
from codex_activity_monitor.query_response import MAX_RESPONSE_BYTES, encode_response
from codex_activity_monitor.timeline_query import TimelineQuery


class FullReferenceAdapter(HistoryProvider):
    """Reproduce the former metadata reader, retaining unused body references."""
    def timeline_view(self, directory, turns):
        return self.turn_view(directory, turns)


def timed(call):
    begin = time.perf_counter()
    value = call()
    return value, (time.perf_counter()-begin)*1000


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('full', 'metadata'), default='metadata')
    parser.add_argument('--phases', type=int, default=48000)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.phases < 1:
        parser.error('phase count must be positive')
    with tempfile.TemporaryDirectory(prefix='cam-timeline-response-') as temporary:
        home = Path(temporary)
        (home/'sessions').mkdir()
        source = home/'sessions'/'fixture-thread.jsonl'
        with source.open('w') as stream:
            def row(kind, payload):
                stream.write(json.dumps({'type': kind, 'payload': payload})+'\n')
            row('session_meta', {'id': 'thread', 'history_mode': 'paginated'})
            row('event_msg', {'type': 'task_started', 'turn_id': 'u0'})
            for number in range(args.phases):
                row('event_msg', {'type': 'item_completed', 'thread_id': 'thread', 'turn_id': 'u0',
                    'item': {'id': f'a0-{number}', 'type': 'AgentMessage', 'text': 'raw中文🙂'*64}})
            row('event_msg', {'type': 'task_complete', 'turn_id': 'u0'})
        provider = FullReferenceAdapter() if args.mode == 'full' else HistoryProvider()
        _, directory_ms = timed(lambda: provider.index(temporary, 'thread', directory_only=True))
        query = TimelineQuery(history=provider)

        def read():
            bundle = query.bundle(temporary, 'thread', 'u0')
            assert len(bundle['phases']) == args.phases
            if args.mode == 'full':
                frame = json.dumps(bundle, ensure_ascii=False).encode('utf-8')+b'\n'
            else:
                frame = encode_response(bundle)
            # Decode only the small error envelope. A full loopback decode here
            # would charge a second client's object graph to the server's peak.
            error = json.loads(frame).get('error') if len(frame) < 100 else None
            return len(frame), error

        gc.collect()
        tracemalloc.start()
        value, first_ms = timed(read)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        hot = [timed(read)[1] for _ in range(3)]
        if args.mode == 'metadata' and value[1] is not None:
            assert value[1] == 'query_too_large' and value[0] < 100
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        result = {
            'version': 1, 'python': platform.python_version(), 'platform': platform.system(),
            'mode': args.mode, 'source_rows': args.phases+3, 'source_bytes': source.stat().st_size,
            'turns': 1, 'phases': args.phases, 'directory_ms': directory_ms,
            'first_bundle_and_encode_instrumented_ms': first_ms,
            'first_bundle_and_encode_peak_allocated_bytes': peak,
            'hot_bundle_and_encode_ms': statistics.median(hot),
            'response_bytes': value[0], 'response_error': value[1],
            'response_within_gui_budget': value[0] <= MAX_RESPONSE_BYTES,
            'query_response_budget_bytes': MAX_RESPONSE_BYTES, 'cache': provider.diagnostics(),
            'benchmark_peak_rss_bytes': rss if platform.system() == 'Darwin' else rss*1024,
            'note': 'Synthetic whole-turn query, not a process RSS cap. Directory built separately. '
                'Full mode adapts the old full-reference projection and whole-buffer JSON encoding; '
                'metadata mode retains only timeline fields and emits a bounded complete reply. '
                'Both project every phase; first read includes tracemalloc, hot reads do not. '
                'The old oversized reply could not be consumed by the existing GUI output limit.'}
        args.output.write_text(json.dumps(result, indent=2)+'\n')


if __name__ == '__main__':
    main()
