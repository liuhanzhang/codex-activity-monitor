"""Preflight identity association. Only upstream events count model text."""

from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json


STREAM_KINDS = {
    'response.output_text.delta': 'text',
    'response.custom_tool_call_input.delta': 'tool_input',
    'response.function_call_arguments.delta': 'tool_arguments',
    'response.reasoning_summary_text.delta': 'summary',
}


class Telemetry:
    def __init__(self):
        self.turn_threads = {}
        self.items = {}
        self.streams = {}
        self.pending = defaultdict(list)
        self.counts = Counter()
        self.timeline = []
        self.seen = set()
        self.tool_activity = []
        self.turn_statuses = {}

    def ingest(self, row):
        event = row['event']
        at = row.get('observed_timestamp', row['observed_ns'] / 1e9)
        if row.get('source_timestamp'):
            at = datetime.fromisoformat(row['source_timestamp'].replace('Z', '+00:00')).timestamp()
        direction = row['direction']
        if direction != 'upstream':
            if direction != 'protocol_out':
                return
            method, params = event.get('method'), event.get('params', {})
            thread, turn = params.get('threadId'), params.get('turnId')
            if method in ('turn/started', 'turn/completed'):
                turn = params.get('turn', {}).get('id')
                if turn and thread:
                    self.turn_threads[turn] = thread
                    self.turn_statuses[turn] = params.get('turn', {}).get('status')
                    self._resolve()
            if turn and thread:
                previous = self.turn_threads.setdefault(turn, thread)
                if previous != thread:
                    raise AssertionError('Conflicting turn identity')
                self._resolve()
            if method in ('item/started', 'item/completed'):
                item = params.get('item', {})
                self.counts[method + ':' + item.get('type', '?')] += 1
                if item.get('type') in ('commandExecution', 'dynamicToolCall', 'mcpToolCall'):
                    self.tool_activity.append({'at': at, 'thread_id': thread, 'turn_id': turn,
                                               'item_id': item.get('id'), 'event': method,
                                               'type': item.get('type'), 'status': item.get('status'),
                                               'exit_code': item.get('exitCode')})
            return
        kind = event.get('type')
        self.counts[kind] += 1
        if kind == 'response.output_item.added':
            item = event['item']
            metadata = item.get('metadata', {})
            self.items[item['id']] = {'turn_id': metadata.get('turn_id'),
                                      'type': item['type'], 'call_id': item.get('call_id'),
                                      'started_at': at, 'ended_at': None}
            self._resolve()
        elif kind == 'response.output_item.done':
            item = event['item']
            if item['id'] in self.items:
                self.items[item['id']].update(ended_at=at, final=item)
        elif kind in STREAM_KINDS:
            item_id = event.get('item_id')
            key = (item_id, kind, event.get('sequence_number'))
            if key[2] is not None and key in self.seen:
                self.counts['duplicates_ignored'] += 1
                return
            if key[2] is not None:
                self.seen.add(key)
            if not self._delta(event, at, delayed=False):
                self.pending[item_id].append((event, at))

    def _resolve(self):
        for item_id in list(self.pending):
            keep = []
            for event, at in self.pending[item_id]:
                if not self._delta(event, at, delayed=True):
                    keep.append((event, at))
            if keep:
                self.pending[item_id] = keep
            else:
                del self.pending[item_id]

    def _delta(self, event, at, delayed):
        item = self.items.get(event.get('item_id'))
        if not item or item.get('turn_id') not in self.turn_threads:
            return False
        kind = STREAM_KINDS[event['type']]
        part = event.get('content_index', event.get('summary_index', 0))
        key = (event['item_id'], kind, part)
        if key not in self.streams:
            self.streams[key] = {'item_id': key[0], 'kind': kind, 'part': part,
                                 'turn_id': item['turn_id'],
                                 'thread_id': self.turn_threads[item['turn_id']],
                                 'text': '', 'delta_count': 0, 'delayed_mapping_count': 0,
                                 'first_at': at, 'last_at': at}
        stream = self.streams[key]
        stream['text'] += event['delta']
        stream['delta_count'] += 1
        stream['delayed_mapping_count'] += int(delayed)
        stream['last_at'] = at
        self.timeline.append((at, stream['thread_id'], kind, key[0]))
        return True

    def summarize(self):
        streams = []
        for stream in self.streams.values():
            item = self.items[stream['item_id']]
            final = item.get('final', {})
            if stream['kind'] == 'text':
                expected = ''.join(p.get('text', '') for p in final.get('content', [])
                                   if p.get('type') == 'output_text')
            elif stream['kind'] == 'tool_input':
                expected = final.get('input')
            elif stream['kind'] == 'tool_arguments':
                expected = final.get('arguments')
            else:
                parts = final.get('summary', [])
                expected = parts[stream['part']].get('text') if stream['part'] < len(parts) else None
            text = stream['text']
            streams.append({k: v for k, v in stream.items() if k != 'text'} | {
                'chars': len(text), 'utf8_bytes': len(text.encode()),
                'sha256': hashlib.sha256(text.encode()).hexdigest(),
                'matches_completed_item': expected == text,
                'item_finished_at': item.get('ended_at'),
            })
        timeline = sorted(self.timeline)
        switches = sum(a[1] != b[1] for a, b in zip(timeline, timeline[1:]))
        return {'event_counts': dict(self.counts), 'streams': streams,
                'unassociated_delta_count': sum(len(v) for v in self.pending.values()),
                'inter_thread_delta_switches': switches, 'tool_activity': self.tool_activity,
                'turn_statuses': self.turn_statuses,
                'reasoning_items': [{'item_id': key, 'turn_id': value.get('turn_id'),
                                     'started_at': value['started_at'], 'ended_at': value['ended_at']}
                                    for key, value in self.items.items() if value['type'] == 'reasoning']}


def read_capture(path):
    with open(path) as source:
        for line in source:
            yield json.loads(line)
