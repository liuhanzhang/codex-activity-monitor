"""Synthetic cutover checks through the bundled CLI and its real query server."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3


def verify(cli, inject, home, runtime):
    home = Path(home)
    sessions = home/'sessions/2026/10/29'
    sessions.mkdir(parents=True, exist_ok=True)
    thread = 'packaging-rollout'

    def records(owner, turns, base=None):
        rows = [{'type': 'session_meta', 'payload': {'id': owner, 'history_mode': 'paginated',
                 **({'history_base': base} if base else {})}}]
        for turn, body, output in turns:
            usage = {'input_tokens': 200, 'cached_input_tokens': 0, 'output_tokens': output, 'reasoning_output_tokens': 0}
            rows.extend([
                {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': turn}},
                {'type': 'event_msg', 'payload': {'type': 'item_completed', 'thread_id': owner, 'turn_id': turn,
                    'started_at_ms': 1793275200000, 'completed_at_ms': 1793275201000,
                    'item': {'type': 'AgentMessage', 'id': turn+'-item', 'content': body}}},
                {'type': 'token_usage_record', 'payload': {'thread_id': owner, 'turn_id': turn,
                    'response_id': turn+'-response', 'usage': usage}},
                {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
                    'last_token_usage': usage, 'total_token_usage': usage}}},
                {'type': 'event_msg', 'payload': {'type': 'task_complete', 'turn_id': turn}},
            ])
        ordinal = base['end_ordinal_exclusive'] if base else 0
        for index, row in enumerate(rows):
            row['ordinal'] = ordinal+index
            # Every invented turn has one known one-second request window.
            ms = 1000 if row['type'] == 'token_usage_record' or row['payload'].get('type') in ('token_count', 'task_complete') else 0
            row['timestamp'] = (datetime(2026, 10, 29, 12, tzinfo=timezone.utc)+timedelta(milliseconds=ms)).isoformat()
        return rows

    def write(path, rows):
        offsets = []
        with path.open('wb') as stream:
            for row in rows:
                stream.write((json.dumps(row)+'\n').encode())
                offsets.append(stream.tell())
        return offsets

    def select(owner, path):
        with sqlite3.connect(home/'state_5.sqlite') as db:
            db.execute('CREATE TABLE IF NOT EXISTS threads (id TEXT PRIMARY KEY, rollout_path TEXT, history_mode TEXT)')
            db.execute('INSERT OR REPLACE INTO threads VALUES (?,?,?)', (owner, str(path), 'paginated'))

    def query(owner, turn, **extra):
        request = {'action': 'timeline', 'scope_id': str(home), 'thread_id': owner, 'turn_id': turn, **extra}
        return json.loads(cli('--runtime', runtime, 'timeline-query', json.dumps(request)).stdout)

    def detail(owner, turn):
        bundle = query(owner, turn)
        phase = next(p for p in bundle['phases'] if p['item_id'] == turn+'-item')
        return query(owner, turn, action='detail', phase_id=phase['id'])

    old = sessions/f'rollout-2026-10-29T12-00-00-{thread}.jsonl'
    discarded = 'Synthetic discarded output 中文 ' * 20000
    offsets = write(old, records(thread, [('kept', 'Synthetic retained prefix', 100), ('discarded', discarded, 900)]))
    select(thread, old)
    inject([
        {'method': 'thread/started', 'params': {'thread': {'id': thread, 'path': str(old), 'historyMode': 'paginated'}}},
        {'method': 'turn/started', 'params': {'threadId': thread, 'turn': {'id': 'discarded'}}},
        {'method': 'item/completed', 'params': {'threadId': thread, 'turnId': 'discarded',
            'item': {'id': 'discarded-item', 'type': 'agentMessage', 'text': discarded}}},
        {'method': 'turn/completed', 'params': {'threadId': thread, 'turn': {'id': 'discarded', 'status': 'completed'}}},
    ])
    first = detail(thread, 'discarded')
    page = first['sections'][0]
    assert page['next_offset'], 'fixture must exercise a real paged section'
    assert query(thread, 'kept')['average_speed']['tokens_per_second'] == 100
    before = hashlib.sha256(old.read_bytes()).hexdigest()

    base = {'thread_id': thread, 'end_ordinal_exclusive': 6, 'end_byte_offset': offsets[5]}
    current = sessions/f'rollout-2026-10-29T13-00-00-{thread}_replacement.jsonl'
    rows = records(thread, [('current', 'Synthetic current suffix', 200)], base)
    write(current, rows); select(thread, current)
    inject([{'method': 'thread/reverted', 'params': {'threadId': thread}},
            {'method': 'thread/started', 'params': {'thread': {'id': thread, 'path': str(current), 'historyMode': 'paginated'}}}])
    assert detail(thread, 'current')['sections'][0]['text'] == 'Synthetic current suffix'
    assert detail(thread, 'kept')['sections'][0]['text'] == 'Synthetic retained prefix'
    assert query(thread, 'current')['average_speed']['tokens_per_second'] == 200
    assert query(thread, 'kept')['average_speed']['timed_output_tokens'] == 100
    removed = query(thread, 'discarded', action='detail', phase_id=first['phase']['id'],
                    section_id=page['id'], offset=page['next_offset'], page_token=page['page_token'])
    assert removed['state'] == 'not_retained' and not removed['sections'], removed['state']
    assert query(thread, 'discarded')['average_speed']['sample_count'] == 0
    assert hashlib.sha256(old.read_bytes()).hexdigest() == before

    appended = records(thread, [('appended', 'Synthetic next turn', 300)], base)
    with current.open('ab') as stream:
        for index, row in enumerate(appended[1:], rows[-1]['ordinal']+1):
            row['ordinal'] = index
            stream.write((json.dumps(row)+'\n').encode())
    assert detail(thread, 'appended')['sections'][0]['text'] == 'Synthetic next turn'
    assert query(thread, 'appended')['average_speed']['tokens_per_second'] == 300

    child = 'packaging-user-fork'
    fork = sessions/f'rollout-2026-10-29T14-00-00-{child}.jsonl'
    write(fork, records(child, [('fork-own', 'Synthetic fork output', 400)], base))
    select(child, fork)
    assert detail(child, 'kept')['sections'][0]['text'] == 'Synthetic retained prefix'
    assert detail(child, 'fork-own')['sections'][0]['text'] == 'Synthetic fork output'
    assert query(child, 'kept')['average_speed']['tokens_per_second'] == 100

    sources = [old, current, fork, home/'state_5.sqlite']
    before_read = [hashlib.sha256(p.read_bytes()).hexdigest() for p in sources]
    detail(thread, 'current'); detail(child, 'kept')
    assert before_read == [hashlib.sha256(p.read_bytes()).hexdigest() for p in sources]
    return {'current_selection_and_warm_cache': True, 'retained_prefix': True,
            'discarded_paging_blocked': True, 'effective_turn_averages': True,
            'incremental_next_turn': True, 'user_fork_source_ownership': True,
            'source_files_and_sqlite_unchanged_by_reads': True}
