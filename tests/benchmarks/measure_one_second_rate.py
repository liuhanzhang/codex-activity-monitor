"""Fixed one-second receive counts under regular and bursty source arrivals."""

import argparse
import json
from pathlib import Path
from unittest.mock import patch

from codex_activity_monitor.metrics import TextStream, TokenCounter, count_text
from codex_activity_monitor.runtime import source_identity


def measure(interval, tokens):
    stream = TextStream(('synthetic',), 'tool_input', 'upstream', 'unknown', 100)
    counter = TokenCounter(False)
    next_receipt = 108.125
    receipts, samples = [], []
    try:
        with patch('codex_activity_monitor.metrics.count_text', side_effect=lambda text, encoding: (count_text(text, encoding)[0], 0)):
            for tick in range(1, 81):
                now = 108+tick/4
                while next_receipt <= now+1e-8:
                    stream.append(' x'*tokens, next_receipt)
                    receipts.append(next_receipt)
                    next_receipt += interval
                counter.tick([stream], now)
                expected = tokens*sum(now-1 < at <= now for at in receipts)
                actual = stream.rate(now)
                assert actual == expected, (now, actual, expected)
                samples.append({'elapsed': round(now-108, 3), 'rate': actual})
    finally:
        counter.close()
    steady = [sample['rate'] for sample in samples if sample['elapsed'] >= 5]
    return {'receipt_interval_seconds': interval, 'tokens_per_batch': tokens,
            'first_two_seconds': samples[:8], 'steady_min': min(steady), 'steady_max': max(steady),
            'all_samples_equal_source_window_count': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    cases = {name: measure(interval, tokens) for name, interval, tokens in (
        ('quarter_second', .25, 25), ('half_second', .5, 50),
        ('batches_1_2_seconds', 1.2, 120), ('batches_1_5_seconds', 1.5, 150))}
    assert cases['quarter_second']['steady_min'] == cases['quarter_second']['steady_max'] == 100
    assert cases['batches_1_2_seconds']['steady_min'] == 0
    assert cases['batches_1_2_seconds']['steady_max'] == 120
    report = {'version': 1, 'rate_window_seconds': 1, 'source': source_identity('metrics'), 'cases': cases,
              'limits': ['Real local BPE, virtual clock, normalized zero tokenizer CPU cost; no native GUI or natural model.',
                         'Window counts deliberately reflect burst arrivals; they do not promise a constant or smooth display.']}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({name: {key: case[key] for key in ('steady_min', 'steady_max')} for name, case in cases.items()}))


if __name__ == '__main__':
    main()
