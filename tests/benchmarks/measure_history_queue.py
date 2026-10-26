"""Exercise production history queue limits with synthetic metadata only."""

import argparse
import json
from pathlib import Path
import threading
import time

from codex_activity_monitor.history_refresh import HistoryRefresh
from codex_activity_monitor.history_contract import HistorySelection


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    entered, release = threading.Event(), threading.Event()
    class Provider:
        def reconciliation(self, scope, thread, turns, **_):
            entered.set(); release.wait(5)
            return {'scope_id': scope, 'thread_id': thread, 'revision': 1, 'read_complete': True,
                    'requested_turns': sorted(turns), 'quality': [],
                    'padding': 'synthetic'*(1048576 if thread == 'oversized' else 65536)}
    reader = HistoryRefresh(provider=Provider())
    try:
        turns = {f'u{n:05}-'+'x'*8 for n in range(1000)}
        reader.schedule([HistorySelection('/synthetic', str(n), frozenset(turns), True) for n in range(200)])
        assert entered.wait(1)
        with reader.lock:
            admitted = len(reader.pending)+1
        pending = reader.diagnostics()
        release.set()
        deadline = time.monotonic()+15
        while reader.diagnostics()['processed'] < admitted and time.monotonic() < deadline:
            time.sleep(.005)
        assert reader.diagnostics()['processed'] == admitted
        full = reader.diagnostics()
        drains = []
        while reader.diagnostics()['ready']:
            begin = time.perf_counter(); values = reader.drain()
            elapsed = (time.perf_counter()-begin)*1000
            size = sum(len(json.dumps(v, ensure_ascii=False, separators=(',', ':')).encode()) for v in values)
            drains.append({'results': len(values), 'encoded_bytes': size, 'decode_ms': elapsed})
        reader.schedule([HistorySelection('/synthetic', 'oversized', frozenset({'u'}), True)])
        deadline = time.monotonic()+5
        while reader.diagnostics()['processed'] == admitted and time.monotonic() < deadline:
            time.sleep(.005)
        error, = reader.drain()
        assert 'revision' not in error
        result = {'version': 1, 'note': 'Default queue limits, synthetic metadata; not ingestion or native UI latency.',
                  'offered_requests': 200, 'admitted_requests': admitted,
                  'pending_pressure': pending, 'ready_pressure': full, 'drains': drains,
                  'oversized_outcome': error['quality'], 'final': reader.diagnostics()}
        args.output.write_text(json.dumps(result, indent=2)+'\n')
    finally:
        release.set(); reader.close()


if __name__ == '__main__':
    main()
