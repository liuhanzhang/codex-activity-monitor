#!/usr/bin/env python3
"""Summarize the two recorded probes without copying their payload contents."""

from collections import Counter
from datetime import datetime
import gzip
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / 'experiments/desktop-isolation/runs/20261010-150337-2b232b'
LOG = RUN / 'backend-transport-20261010-171554.log'
if not LOG.exists():
    LOG = LOG.with_suffix(LOG.suffix + '.gz')
CASES = {
    'tool': 'stream-20261010-171611-long_inline-auto',
    'reasoning': 'stream-20261010-171729-reasoning-auto',
}


def seconds(end, start):
    return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()


def main():
    cases = {key: json.loads((RUN / folder / 'summary.json').read_text())
             for key, folder in CASES.items()}
    records = []
    open_log = gzip.open if LOG.suffix == '.gz' else open
    with open_log(LOG, 'rt', encoding='utf-8') as source:
        for line in source:
            try:
                row = json.loads(line)
                message = row.get('fields', {}).get('message', '')
                if not message.startswith('Received message '):
                    continue
                event = json.loads(message[len('Received message '):])
                if event.get('type', '').startswith(('response.', 'responsesapi.', 'codex.')):
                    records.append((row['timestamp'], event))
            except (ValueError, TypeError, KeyError):
                continue
    items = {}
    completed = []
    for at, event in records:
        kind = event['type']
        if kind == 'response.output_item.added':
            item = event['item']
            items[item['id']] = {
                'type': item['type'], 'name': item.get('name'),
                'call_id': item.get('call_id'), 'added_at': at,
                'metadata': item.get('metadata', {}),
                'added_encrypted_chars': len(item.get('encrypted_content', '')),
            }
        elif kind == 'response.output_item.done':
            item = event['item']
            items.setdefault(item['id'], {}).update(done_at=at, done=item)
        elif kind == 'response.completed':
            completed.append((at, event['response']))
    rollout = next((RUN / 'codex-home/sessions').rglob('*' + cases['tool']['thread_id'] + '.jsonl'))
    calls = []
    for line in rollout.read_text().splitlines():
        row = json.loads(line)
        if row.get('type') == 'response_item' and row.get('payload', {}).get('type') == 'custom_tool_call':
            calls.append(row['payload'])
    # The commandExecution item is the nested shell call, not the outer exec call.
    assert len(calls) == 1 and calls[0]['name'] == 'exec'
    call = calls[0]
    tool_id = call['id']
    assert items[tool_id]['call_id'] == call['call_id']
    deltas = [(at, event) for at, event in records
              if event.get('type') == 'response.custom_tool_call_input.delta'
              and event.get('item_id') == tool_id]
    joined = ''.join(event['delta'] for _, event in deltas)
    assert joined == call['input'] == items[tool_id]['done']['input']
    sequence = [event['sequence_number'] for _, event in deltas]
    assert sequence == sorted(set(sequence))
    span = seconds(deltas[-1][0], deltas[0][0])
    telemetry = [{'timestamp': at, 'seconds_from_first_delta': seconds(at, deltas[0][0]),
                  'sequence_number': event['sequence_number'],
                  'delta_chars': len(event['delta']),
                  'delta_utf8_bytes': len(event['delta'].encode())}
                 for at, event in deltas]
    (Path(__file__).parent / 'tool-input-deltas.jsonl').write_text(
        ''.join(json.dumps(row) + '\n' for row in telemetry))
    reasoning_id = next(key for key, value in items.items()
                        if value.get('type') == 'reasoning'
                        and value.get('metadata', {}).get('turn_id') == cases['reasoning']['turn_id'])
    reasoning = items[reasoning_id]
    reasoning_events = [(at, event) for at, event in records
                        if event.get('item_id') == reasoning_id and 'reasoning' in event['type']]
    reasoning_response = next(response for _, response in completed
                              if any(item.get('id') == reasoning_id for item in response.get('output', [])))
    in_window = [(at, event['type']) for at, event in records
                 if reasoning['added_at'] <= at <= reasoning['done_at']]
    longest_gap = max(seconds(b[0], a[0]) for a, b in zip(in_window, in_window[1:]))
    report = json.loads((RUN / 'report.json').read_text())
    completed_command = next(row['item'] for row in cases['tool']['tool_item_events']
                             if row['event'] == 'item/completed')
    summary = {
        'desktop_version': report['desktop_version'], 'desktop_build': report['desktop_build'],
        'cli_version': '0.162.0-alpha.17.2', 'transport': 'Responses WebSocket',
        'method': 'Built-in RUST_LOG receive logging; no proxy or binary modification',
        'trace_log': str(LOG),
        'case_summaries': {label: str(RUN / folder / 'summary.json') for label, folder in CASES.items()},
        'tool_case': {
            'thread_id': cases['tool']['thread_id'], 'turn_id': cases['tool']['turn_id'],
            'status': cases['tool']['status'], 'tool_name': call['name'], 'item_id': tool_id,
            'delta_count': len(deltas), 'delta_chars': len(joined), 'delta_utf8_bytes': len(joined.encode()),
            'first_delta_at': deltas[0][0], 'last_delta_at': deltas[-1][0],
            'stream_span_seconds': span, 'receipt_chars_per_second': len(joined) / span,
            'delta_char_length_min': min(len(event['delta']) for _, event in deltas),
            'delta_char_length_max': max(len(event['delta']) for _, event in deltas),
            'delta_token_count_fields_present': any(any('token' in key for key in event) for _, event in deltas),
            'joined_deltas_equal_upstream_done_and_saved_rollout': True,
            'sequence_numbers_unique_and_increasing': True,
            'app_server_notification_counts': cases['tool']['notification_counts']['observer'],
            'execution_exit_code': completed_command['exitCode'],
            'command_stdout': completed_command['aggregatedOutput'],
        },
        'reasoning_case': {
            'thread_id': cases['reasoning']['thread_id'], 'turn_id': cases['reasoning']['turn_id'],
            'status': cases['reasoning']['status'], 'effort': cases['reasoning']['requested_effort'],
            'reasoning_item_id': reasoning_id,
            'summary_delta_count': sum(event['type'] == 'response.reasoning_summary_text.delta'
                                       for _, event in reasoning_events),
            'summary_delta_chars': sum(len(event.get('delta', '')) for _, event in reasoning_events),
            'reasoning_event_types': dict(Counter(event['type'] for _, event in reasoning_events)),
            'encrypted_content_chars_at_start': reasoning['added_encrypted_chars'],
            'encrypted_content_chars_at_done': len(reasoning['done'].get('encrypted_content', '')),
            'longest_upstream_event_gap_during_reasoning_seconds': longest_gap,
            'usage_reported_at_response_completed': {
                key: reasoning_response.get('usage', {}).get(key)
                for key in ['input_tokens', 'output_tokens', 'output_tokens_details', 'total_tokens']},
            'continuous_reasoning_token_updates_observed': False,
        },
        'restored_state': {
            'backend_pid': report['backend_pid'],
            'rust_log': report['active_transport_logging']['rust_log'],
            'model_catalog_json': report['active_transport_logging']['model_catalog_json'],
            'cached_model_tool_mode': report['active_transport_logging']['cached_model_tool_mode'],
            'isolated_config_matches_original_backup': (RUN / 'codex-home/config.toml').read_bytes()
                == (RUN / 'config-before-tool-mode-20261010-170016.toml').read_bytes(),
            'primary_app_modified': False,
        },
        'limits': ['Delta count is not token count', 'Receipt rate is not guaranteed backend generation rate',
                   'No events does not prove a hang',
                   'RUST_LOG is documented; raw log text is not a stable monitoring API',
                   'Trace recording adds I/O overhead; no overhead benchmark performed'],
    }
    assert summary['restored_state']['isolated_config_matches_original_backup']
    assert summary['tool_case']['status'] == summary['reasoning_case']['status'] == 'completed'
    assert summary['tool_case']['execution_exit_code'] == 0
    (Path(__file__).parent / 'verification.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'tool': summary['tool_case'], 'reasoning': summary['reasoning_case'],
                      'restored_state': summary['restored_state']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
