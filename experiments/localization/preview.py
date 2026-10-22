#!/usr/bin/env python3
"""Isolated bilingual acceptance fixtures. No daily state or history is read."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'timeline'))
from panel_round3_preview import CASES, fixtures


def localized_fixtures(case, base=None):
    base = time.time() * 1000 - 90000 if base is None else base
    snapshot, timeline = fixtures(base, 'agents' if case in ('disconnected', 'partial-coverage') else case)
    if case == 'disconnected':
        snapshot['backends'] = {}
        snapshot['overview'].update(coverage='unavailable', generation_rate=None)
        for row in snapshot['threads']: row['connected'] = False
    elif case == 'partial-coverage':
        snapshot['overview'].update(coverage='partial', generation_rate=None, observed_subtotal=48, quality=['observation_gap'])
    for row in snapshot['threads']:
        if row.get('has_active_work'):
            row['display_message'] = {'key': 'activity.running', 'params': {'target': 'python'}}
    def visit(value):
        if isinstance(value, dict):
            if {'item_id', 'kind', 'label'} <= value.keys() and value.get('track') == 'tool':
                value['label_message'] = {'key': 'activity.named', 'params': {'name': 'synthetic_tool'}}
            if {'id', 'title', 'format', 'text'} <= value.keys():
                key = {'input': 'detail.input', 'output': 'detail.output', 'live': 'detail.live'}.get(value['id'])
                if key:
                    value['title_message'] = {'key': key, 'params': {}}
            for part in value.values(): visit(part)
        elif isinstance(value, list):
            for part in value: visit(part)
    visit(timeline)
    snapshot["updated_at"] = time.time()
    return snapshot, timeline


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--keep-fresh', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    base = time.time() * 1000 - 90000
    while True:
        selection = args.output/'case.txt'
        case = selection.read_text().strip() if selection.exists() else 'agents'
        if case not in CASES + ('disconnected', 'partial-coverage'): parser.error('Unknown case: ' + case)
        snapshot, timeline = localized_fixtures(case, base)
        for name, value in [('snapshot', snapshot), ('timeline', timeline)]:
            path = args.output/(name+'.json')
            temporary = path.with_suffix('.new')
            temporary.write_text(json.dumps(value, ensure_ascii=False))
            temporary.replace(path)
        if not args.keep_fresh: return
        time.sleep(.5)


if __name__ == '__main__': main()
