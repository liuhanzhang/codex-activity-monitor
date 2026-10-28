"""Check derived evidence; never read Codex logs or exercise product runtimes."""

import csv
import json
import math
from collections import defaultdict
from pathlib import Path
import statistics


ROOT = Path(__file__).parent


def close(actual, expected):
    assert math.isclose(actual, expected, rel_tol=1e-11, abs_tol=1e-9), (actual, expected)


def percentile(values, fraction):
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def main():
    data = json.loads((ROOT / 'measurements.json').read_text())
    summary = json.loads((ROOT / 'summary.json').read_text())
    intervals = json.loads((ROOT / 'usage-intervals.json').read_text())
    rows = data['updates']
    assert len(rows) == 285
    assert len(data['turns']) == 19
    assert len(list(csv.DictReader((ROOT / 'updates.csv').open()))) == len(rows)
    groups = defaultdict(list)
    for row in rows:
        groups[(row['session'], row['turn'])].append(row)
        assert row['confirmation_matches'] is True
        assert 0 <= row['reasoning_tokens'] <= row['output_tokens']

    gaps = []
    zero_item_rows = []
    for key, entries in groups.items():
        assert [row['usage'] for row in entries] == list(range(1, len(entries) + 1))
        tokens = item_ms = request_ms = 0
        for row in entries:
            tokens += row['output_tokens']
            item_ms += row['item_duration_ms']
            request_ms += row['request_duration_ms']
            assert tokens == row['cumulative_output_tokens']
            assert item_ms == row['item_cumulative_ms']
            assert request_ms == row['request_cumulative_ms']
            assert row['usage_offset_ms'] - row['request_start_offset_ms'] == row['request_duration_ms']
            close(tokens * 1000 / item_ms, row['item_average_tps'])
            close(tokens * 1000 / request_ms, row['request_average_tps'])
            if row['new_model_item_count'] == 0:
                zero_item_rows.append(row)
        gaps.extend((b['usage_offset_ms'] - a['usage_offset_ms']) / 1000
                    for a, b in zip(entries, entries[1:]))
        turn = next(t for t in data['turns'] if (t['session'], t['turn_index']) == key)
        close(entries[-1]['request_average_tps'], turn['final_tps'])
        close(entries[-1]['item_average_tps'], turn['old_final_tps'])

    assert len(zero_item_rows) == 44
    assert sum(row['output_tokens'] for row in zero_item_rows) == 3241
    assert len(gaps) == 266
    overall = intervals['overall']
    close(statistics.mean(gaps), overall['mean_s'])
    close(statistics.median(gaps), overall['median_s'])
    for name, fraction in [('p25_s', .25), ('p75_s', .75), ('p90_s', .9), ('p95_s', .95)]:
        close(percentile(gaps, fraction), overall[name])
    close(min(gaps), overall['min_s'])
    close(max(gaps), overall['max_s'])
    for threshold in (10, 20, 30, 60):
        assert sum(gap <= threshold for gap in gaps) == overall[f'within_{threshold}s']
    assert sum(gap > 120 for gap in gaps) == overall['over_120s']

    for minimum in (2, 3, 5, 10):
        selected = [values for values in groups.values() if len(values) >= minimum]
        result = summary[f'minimum_{minimum}_usage']
        assert len(selected) == result['turns']
        for method, field in [('old', 'item_average_tps'), ('new', 'request_average_tps')]:
            all_stable = half_stable = 0
            cvs = []
            for entries in selected:
                speeds = [r[field] for r in entries]
                final = speeds[-1]
                stable = lambda values: all(abs(value / final - 1) <= .2 for value in values)
                all_stable += stable(speeds)
                half_stable += stable(speeds[len(speeds) // 2:])
                cvs.append(statistics.pstdev(speeds) / statistics.mean(speeds))
            assert all_stable == result[f'{method}_all_within_20pct_final']
            assert half_stable == result[f'{method}_latter_half_within_20pct_final']
            close(statistics.median(cvs), result[f'{method}_median_cv'])

    print('Verified: 285 updates, 19 turns, paired sums, 44 item-timing gaps, stability groups, 266 usage intervals.')


if __name__ == '__main__':
    main()
