"""Paced synthetic intake with real asynchronous counting and snapshot work.

No Codex writer, filesystem history, GUI, sockets or disk stall is involved.
This complements the independent transport/native and source-cache workloads.
"""

import argparse
import json
from pathlib import Path
import platform
import resource
import statistics
import time

from codex_progress_monitor.engine import Engine
from codex_progress_monitor.events import Event, filter_event
from codex_progress_monitor.publication import DetachedSnapshot
from codex_progress_monitor.timeline import TimelineBook


def summary(values):
    ordered = sorted(values)
    return {'samples': len(values), 'p50_ms': statistics.median(values),
            'p95_ms': ordered[int((len(ordered)-1)*.95)], 'max_ms': ordered[-1]}


def case(streams, rate, duration):
    engine = Engine(timeline=TimelineBook())
    def send(method, params):
        value = filter_event('protocol_out', {'method': method, 'params': params})
        engine.ingest(Event('synthetic', 'protocol_out', value, time.monotonic(), time.time()))
    try:
        begin = time.perf_counter()
        engine.counter.worker.count('warm', 'o200k_base')
        startup_ms = (time.perf_counter()-begin)*1000
        for n in range(streams):
            send('turn/started', {'threadId': f't{n}', 'turn': {'id': f'u{n}', 'status': 'inProgress'}})
            send('item/started', {'threadId': f't{n}', 'turnId': f'u{n}',
                                 'item': {'id': f'a{n}', 'type': 'agentMessage'}})
        begin = time.monotonic(); number = 0; tick = begin
        ingestion = []; lateness = []; snapshots = []; copies = []; encodes = []; sizes = []
        while number < int(rate*duration):
            due = begin+number/rate
            now = time.monotonic()
            if now >= tick:
                started = time.perf_counter()
                engine.tick(now); value = engine.snapshot(now, time.time())
                snapshots.append((time.perf_counter()-started)*1000)
                started = time.perf_counter(); detached = DetachedSnapshot.capture(value)
                copies.append((time.perf_counter()-started)*1000)
                started = time.perf_counter(); encoded = detached.encode('synthetic', len(snapshots))
                encodes.append((time.perf_counter()-started)*1000); sizes.append(len(encoded))
                tick = now+.1
            if now < due:
                time.sleep(min(due-now, .001)); continue
            lateness.append(max(0, time.monotonic()-due)*1000)
            owner = number % streams
            started = time.perf_counter()
            send('item/agentMessage/delta', {'threadId': f't{owner}', 'turnId': f'u{owner}',
                 'itemId': f'a{owner}', 'delta': 'hello 中文🙂\n'})
            ingestion.append((time.perf_counter()-started)*1000)
            number += 1
        before = engine.counter.diagnostics()
        started = time.perf_counter()
        for n in range(streams):
            send('item/completed', {'threadId': f't{n}', 'turnId': f'u{n}',
                                   'item': {'id': f'a{n}', 'type': 'agentMessage', 'status': 'completed'}})
            send('turn/completed', {'threadId': f't{n}', 'turn': {'id': f'u{n}', 'status': 'completed'}})
        engine.tick(time.monotonic()); final = engine.snapshot(time.monotonic(), time.time())
        assert final['overview']['active_thread_count'] == 0
        assert not any(v['has_unresolved_work'] for v in final['threads'])
        ended_ms = (time.perf_counter()-started)*1000
        started = time.perf_counter(); engine.close()
        return {'streams': streams, 'target_events_per_second': rate, 'duration_seconds': duration,
                'events': number, 'tokenizer_startup_ms': startup_ms, 'ingestion': summary(ingestion),
                'scheduler_lateness': summary(lateness), 'tick_and_snapshot': summary(snapshots),
                'detach': summary(copies), 'encode': summary(encodes), 'max_frame_bytes': max(sizes),
                'counter': before, 'end_all_and_snapshot_ms': ended_ms,
                'close_ms': (time.perf_counter()-started)*1000}
    finally:
        engine.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--duration', type=float, default=3)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.duration < 1: parser.error('duration must be at least one second')
    result = {'version': 1, 'python': platform.python_version(), 'platform': platform.system(),
              'note': 'Paced synthetic engine and encoding only, counting child warmed before each case; not natural soak, transport or native draw.',
              'cases': [case(streams, rate, args.duration) for streams in (1, 10, 50) for rate in (10, 100, 1000)]}
    for who, name in ((resource.RUSAGE_SELF, 'parent'), (resource.RUSAGE_CHILDREN, 'children')):
        rss = resource.getrusage(who).ru_maxrss
        result[f'{name}_peak_rss_bytes'] = rss if platform.system() == 'Darwin' else rss*1024
    args.output.write_text(json.dumps(result, indent=2)+'\n')


if __name__ == '__main__': main()
