"""Measure the owned tokenizer's cold/warm cost, actual deadline and RSS."""

import argparse
import json
from pathlib import Path
import platform
import resource
import time

from codex_activity_monitor.tokenizer_process import TokenizerProcess, TokenizerUnavailable


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    worker = TokenizerProcess()
    result = {'version': 1, 'python': platform.python_version(), 'platform': platform.system(),
              'note': 'One persistent owned child, synthetic text; wall includes IPC, child startup for first count only.',
              'counts': []}
    try:
        for size, kind in ((65536, 'mixed'), (1048576, 'mixed'), (1048576, 'ascii')):
            raw = (('hello 中文🙂\n' if kind == 'mixed' else 'x')*(size//17+1 if kind == 'mixed' else size)).encode()[:size]
            text = raw.decode(errors='ignore')
            begin = time.perf_counter(); tokens, cost = worker.count(text, 'o200k_base')
            result['counts'].append({'utf8_bytes': len(text.encode()), 'kind': kind, 'tokens': tokens,
                                    'native_ms': cost, 'wall_ms': (time.perf_counter()-begin)*1000})
        worker.timeout = .1
        begin = time.perf_counter()
        try:
            worker.count('x'*1048576, 'o200k_base')
            raise AssertionError('large native count unexpectedly beat 100ms deadline')
        except TokenizerUnavailable:
            result['forced_deadline_wall_ms'] = (time.perf_counter()-begin)*1000
        worker.timeout = 3
        begin = time.perf_counter()
        assert worker.count('word', 'o200k_base')[0] == 1
        result['recovery_wall_ms'] = (time.perf_counter()-begin)*1000
        result['worker'] = worker.diagnostics()
    finally:
        begin = time.perf_counter(); worker.close()
        result['idle_close_ms'] = (time.perf_counter()-begin)*1000
    rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    result['terminated_child_peak_rss_bytes'] = rss if platform.system() == 'Darwin' else rss*1024
    args.output.write_text(json.dumps(result, indent=2)+'\n')


if __name__ == '__main__': main()
