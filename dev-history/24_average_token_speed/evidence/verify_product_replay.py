"""Compare the product parser with the frozen measurements, using private inputs.

Pass --source A=/path/to/original.jsonl for each source. Only the recorded
prefix is read; the script never writes source files or prints their contents.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]/'src'))
from codex_activity_monitor.request_rate import RequestRateBook


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', action='append', required=True, metavar='LABEL=PATH')
    args = parser.parse_args()
    sources = dict(value.split('=', 1) for value in args.source)
    evidence = json.loads(Path(__file__).with_name('measurements.json').read_text())
    expected = {(r['session'], r['source_line']): r for r in evidence['updates']}
    updates = turns = 0
    for source in evidence['sources']:
        label = source['session']
        if label not in sources:
            parser.error(f'missing source {label}')
        with Path(sources[label]).open('rb') as stream:
            raw = stream.read(source['prefix_bytes'])
        assert hashlib.sha256(raw).hexdigest() == source['prefix_sha256'], f'{label}: prefix differs'
        book = None
        for line_no, line in enumerate(raw.splitlines(), 1):
            row = json.loads(line)
            if row.get('type') == 'session_meta':
                book = RequestRateBook(row['payload']['id'])
            assert book is not None, f'{label}: missing session header'
            book.observe(row)
            if row.get('type') != 'token_usage_record':
                continue
            actual = book.projection(row['payload']['turn_id'])
            old = expected[label, line_no]
            location = f'{label}: turn {old["turn"]}, usage {old["usage"]}'
            assert actual['timed_output_tokens'] == old['cumulative_output_tokens'], location+' output differs'
            assert actual['timed_duration_ms'] == old['request_cumulative_ms'], location+' duration differs'
            assert abs(actual['tokens_per_second']-old['request_average_tps']) < 1e-10, location+' speed differs'
            updates += 1
        assert not book.pending, f'{label}: unconfirmed responses'
        assert all(not t.quality for t in book.turns.values()), f'{label}: unexpected degraded coverage'
        turns += len(book.turns)
    assert updates == len(expected), 'missing updates'
    print(f'Product replay matched {updates} usage updates in {turns} turns; all confirmations settled.')


if __name__ == '__main__':
    main()
