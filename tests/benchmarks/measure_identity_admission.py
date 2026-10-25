"""Fill both default identity limits without any session or native tokenizer."""

import argparse
import gc
import json
from pathlib import Path
import platform
import resource
import time
import tracemalloc

from codex_progress_monitor.identity_budget import IdentityBudget
from codex_progress_monitor.resource_budget import retained_bytes


def case(kind):
    budget = IdentityBudget()
    if kind == 'entries':
        count = budget.capacity
        def identity(number):
            return f'{number:08d}'
    else:
        # 8 ASCII bytes + 1,362 three-byte code points + 2 ASCII bytes.
        # Exactly 4,096 UTF-8 bytes; well within the 4,096-code-point ID limit.
        count = budget.max_identity_bytes//4096
        def identity(number):
            return f'{number:08d}'+'中'*1362+'xx'
        assert len(identity(0).encode('utf-8')) == 4096
    gc.collect()
    tracemalloc.start()
    started = time.perf_counter()
    for number in range(count):
        value = identity(number)
        assert budget.reserve('item', 'synthetic', value)
        budget.retire('item', 'synthetic', value)
    fill_ms = (time.perf_counter()-started)*1000
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    before = budget.diagnostics()
    assert not before['admission_closed']
    assert not budget.reserve('item', 'synthetic', 'not-admitted')
    assert budget.diagnostics()['admission_closed']
    assert all(budget.contains('item', 'synthetic', identity(number))
               for number in (0, count//2, count-1))
    assert budget.diagnostics()['entries'] == before['entries']
    if kind == 'entries':
        assert before['entries'] == before['max_entries']
        assert before['identity_bytes'] < before['max_identity_bytes']
    else:
        assert before['identity_bytes'] == before['max_identity_bytes']
        assert before['entries'] < before['max_entries']
    return {'limit': kind, 'admitted_terminal_identities': count,
            'fill_instrumented_ms': fill_ms, 'fill_peak_allocated_bytes': peak,
            'ledger_sets_and_digests_heap_bytes': retained_bytes(budget.closed, budget.reserved),
            'before_rejection': before, 'after_rejection': budget.diagnostics(),
            'terminal_probes_retained': 3}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    results = [case(kind) for kind in ('entries', 'utf8_bytes')]
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    args.output.write_text(json.dumps({'version': 1, 'python': platform.python_version(),
        'platform': platform.system(), 'cases': results,
        'benchmark_peak_rss_bytes': rss if platform.system() == 'Darwin' else rss*1024,
        'note': 'Direct admission-ledger measurement with unchanged production defaults. '
            'Fill timings include tracemalloc. Heap counts sets/digests, not allocator or RSS. '
            'Engine gap/presentation/replay behavior is covered by the separate resource tests; '
            'this is neither an Engine event-load nor a natural-use soak.'}, indent=2)+'\n')


if __name__ == '__main__':
    main()
