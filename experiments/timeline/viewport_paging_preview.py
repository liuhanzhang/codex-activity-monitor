#!/usr/bin/env python3
"""Controlled synthetic paging preview. Edit control.json; never reads daily history."""
import argparse
import json
from pathlib import Path
import time

from panel_round3_preview import fixtures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    base = time.time() * 1000 - 59000
    while True:
        control_path = args.output / 'control.json'
        control = json.loads(control_path.read_text()) if control_path.exists() else {}
        elapsed = float(control.get('seconds', 59))
        if control.get('advance_from'):
            elapsed += time.time() - control['advance_from']
        snapshot, timeline = fixtures(base, control.get('case', 'single'))
        current = timeline['bundles']['current']
        current.update(now_ms=base + elapsed * 1000, end_ms=base + elapsed * 1000)
        current['phases'] = [p for p in current['phases'] if (p.get('start_ms') or base) <= current['now_ms']]
        current['markers'] = [p for p in current['markers'] if (p.get('start_ms') or base) <= current['now_ms']]
        for phase in current['phases']:
            if phase.get('end_ms') and phase['end_ms'] > current['now_ms']:
                phase.update(status='active', end_ms=None)
            phase['observed_until_ms'] = current['now_ms']
        if control.get('completed'):
            current['turns'][0].update(status='completed', end_ms=current['now_ms'])
            for phase in current['phases']:
                if phase['status'] == 'active':
                    phase.update(status='completed', end_ms=current['now_ms'])
        snapshot['updated_at'] = time.time()
        for name, value in [('snapshot', snapshot), ('timeline', timeline)]:
            path = args.output / (name + '.json')
            temporary = path.with_suffix('.new')
            temporary.write_text(json.dumps(value, ensure_ascii=False))
            temporary.replace(path)
        time.sleep(.2)


if __name__ == '__main__':
    main()
