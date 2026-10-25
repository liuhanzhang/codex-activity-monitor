"""Synthetic multi-source cache pressure; no real Codex directory is opened."""

import argparse
import json
from pathlib import Path
import platform
import resource
import statistics
import tempfile
import time
from unittest.mock import patch

from codex_progress_monitor.history import HistoryProvider
from codex_progress_monitor.history_index import _HistoryProjection


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', type=int, default=50)
    parser.add_argument('--turns', type=int, default=1000)
    parser.add_argument('--selected-turns', type=int, default=20)
    parser.add_argument('--cycles', type=int, default=2)
    parser.add_argument('--rows-per-source', type=int, default=0)
    parser.add_argument('--largest-source-rows', type=int, default=0)
    parser.add_argument('--scoped', action='store_true', help='Use production item-scoped fact selection.')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if min(args.sources, args.turns, args.selected_turns, args.cycles) < 1:
        parser.error('all counts must be positive')
    if min(args.rows_per_source, args.largest_source_rows) < 0:
        parser.error('row targets must be nonnegative')
    result = {'version': 1, 'python': platform.python_version(), 'platform': platform.system(),
              'selection_kind': 'scoped_facts' if args.scoped else 'full_turn_metadata',
              'sources': args.sources, 'turns_per_source': args.turns, 'items_per_turn': 4,
              'selected_turns_per_source': min(args.turns, args.selected_turns), 'cycles': [],
              'note': 'Provider cache pressure only; sequential calls, not parallel ingestion or natural soak.'}
    with tempfile.TemporaryDirectory(prefix='cpm-history-working-set-') as tmp:
        home = Path(tmp); (home/'sessions').mkdir()
        size = rows = largest = 0
        for owner in range(args.sources):
            path = home/'sessions'/f'fixture-t{owner}.jsonl'
            with path.open('w') as stream:
                def row(kind, payload):
                    stream.write(json.dumps({'type': kind, 'payload': payload})+'\n')
                row('session_meta', {'id': f't{owner}', 'history_mode': 'paginated'})
                for turn in range(args.turns):
                    row('event_msg', {'type': 'task_started', 'turn_id': f'u{turn}'})
                    for item in range(4):
                        row('event_msg', {'type': 'item_completed', 'thread_id': f't{owner}', 'turn_id': f'u{turn}',
                            'item': {'id': f'a{turn}-{item}', 'type': 'AgentMessage', 'text': 'synthetic'}})
                    row('event_msg', {'type': 'task_complete', 'turn_id': f'u{turn}'})
                source_rows = max(1+args.turns*6, args.rows_per_source,
                                  args.largest_source_rows if owner == 0 else 0)
                for _ in range(source_rows-(1+args.turns*6)):
                    row('event_msg', {'type': 'token_count', 'info': {'synthetic': True}})
            size += path.stat().st_size
            rows += source_rows; largest = max(largest, source_rows)
        result['source_bytes'] = size
        result['source_rows'], result['largest_source_rows'] = rows, largest
        provider = HistoryProvider(capacity=64)
        selected = {f'u{n}' for n in range(max(0, args.turns-args.selected_turns), args.turns)}
        items = frozenset((turn, f'a{turn[1:]}-{n}') for turn in selected for n in range(4)) if args.scoped else None
        original = _HistoryProjection._row
        parsed = 0
        def measured(index, *args):
            nonlocal parsed
            parsed += 1
            return original(index, *args)
        with patch.object(_HistoryProjection, '_row', measured):
            for cycle in range(args.cycles):
                times = []; parsed = 0
                for owner in range(args.sources):
                    begin = time.perf_counter()
                    summary = provider.reconciliation(str(home), f't{owner}', selected, items=items)
                    times.append((time.perf_counter()-begin)*1000)
                    assert len(summary['phases']) == len(selected)*4
                result['cycles'].append({'cycle': cycle, 'p50_ms': statistics.median(times),
                    'p95_ms': sorted(times)[int((len(times)-1)*.95)], 'sum_ms': sum(times),
                    'parsed_rows': parsed, 'cache': provider.diagnostics()})
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        result['benchmark_peak_rss_bytes'] = rss if platform.system() == 'Darwin' else rss*1024
    args.output.write_text(json.dumps(result, indent=2)+'\n')


if __name__ == '__main__':
    main()
