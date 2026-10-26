"""Reconcile an owned probe's TRACE windows with its Codex-owned durable history."""

import argparse
from datetime import datetime
import json
from pathlib import Path

from codex_activity_monitor.engine import Engine
from codex_activity_monitor.events import Event, decode_log, filter_event
from codex_activity_monitor.timeline import TimelineBook
from codex_activity_monitor.timeline_storage import TimelineStorage, read_records
from codex_activity_monitor.timeline_query import TimelineQuery


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--home', type=Path, required=True)
    args = parser.parse_args()
    path = args.run / 'timeline/metadata.sqlite3'
    store = TimelineStorage(path)
    book = TimelineBook(store)
    engine = Engine(False, timeline=book)
    source_count = 0
    try:
        engine.ingest(Event('probe', 'lifecycle', {'type': 'bridge/started', 'home': str(args.home.resolve())}, 1, 1))
        # TRACE has turn_id but no thread_id on this binary. Join only explicit durable identities.
        for rollout in (args.home / 'sessions').rglob('*.jsonl'):
            owner = None
            for line in rollout.open():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                payload = row.get('payload', {})
                if row.get('type') == 'session_meta':
                    owner = payload.get('id')
                if row.get('type') == 'turn_context' and owner:
                    engine._map_turn('probe', payload.get('turn_id'), owner)
        for line in (args.run / 'stderr.log').open():
            raw, stamp = decode_log(line)
            if raw is None:
                continue
            safe = filter_event('upstream', raw)
            if safe is None:
                continue
            wall = datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp()
            engine.ingest(Event('probe', 'upstream', safe, wall, wall))
            source_count += 1
    finally:
        engine.close(); store.close()
    query = TimelineQuery(metadata=path)
    candidates = [p for p in book.phases.values() if p['namespace'] == 'input' and p['start_ms'] is not None and p['end_ms'] is not None]
    restored = []
    for phase in candidates:
        result = query.detail(phase['scope_id'], phase['thread_id'], phase['turn_id'], phase['id'])
        restored.append({'interval_ms': phase['end_ms'] - phase['start_ms'],
                         'content_available_from_codex': result.get('source') == 'codex_history' and bool(result.get('sections'))})
    evidence = {'source_events': source_count, 'mapping_source': 'explicit durable turn identities',
                'phase_count': len(book.phases), 'parameter_windows': restored,
                'metadata_rows': len(read_records(path)),
                'projection_errors': engine.counts['timeline_projection_error'],
                'passed': bool(restored) and all(p['content_available_from_codex'] for p in restored)}
    (args.run / 'reconciliation.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps(evidence))


if __name__ == '__main__':
    main()
