"""Synthetic request lifecycles, with no model requests or private transcripts."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codex_activity_monitor.history import HistoryProvider
from codex_activity_monitor.history_index import HistoryDirectory
from codex_activity_monitor.request_rate import RequestRateBook
from codex_activity_monitor.resource_budget import retained_bytes
from codex_activity_monitor.timeline_query import TimelineQuery


def row(outer, ms, **payload):
    at = (datetime(2026, 1, 1, tzinfo=timezone.utc)+timedelta(milliseconds=ms)).isoformat()
    return {'type': outer, 'timestamp': at, 'payload': payload}


def usage(output=100):
    return {'input_tokens': 200, 'output_tokens': output,
            'cached_input_tokens': 0, 'reasoning_output_tokens': min(output, 20)}


def start(turn='u', at=0):
    return row('turn_context', at, turn_id=turn)


def record(response='r', output=100, at=1000, turn='u', **extra):
    return row('token_usage_record', at, thread_id='t', turn_id=turn,
               response_id=response, usage=usage(output), **extra)


def count(output=100, at=1000, total=100):
    return row('event_msg', at, type='token_count', info={
        'last_token_usage': usage(output), 'total_token_usage': usage(total)})


class RequestRateTests(unittest.TestCase):
    def setUp(self):
        self.book = RequestRateBook('t')
        self.feed(start(), row('response_item', 700, type='reasoning'))

    def feed(self, *rows):
        for item in rows:
            self.book.observe(item)

    def value(self, turn='u'):
        return self.book.projection(turn)

    def test_usage_publishes_before_count_and_clock_does_not_change_it(self):
        self.assertIsNone(self.value()['tokens_per_second'])
        self.feed(record())
        first = self.value()
        self.assertEqual(first['tokens_per_second'], 100)
        self.assertEqual(first['timed_output_tokens'], 100)  # Reasoning is already included.
        self.assertEqual(first['pending_response_count'], 1)
        self.assertEqual(self.value(), first)
        self.feed(count())
        self.assertEqual(self.value()['tokens_per_second'], 100)
        self.assertEqual(self.value()['pending_response_count'], 0)

    def test_weighted_mean_uses_paired_sums_and_preserves_zero(self):
        self.feed(record(), count(), record('zero', 0, 3000), count(0, 3000, 101))
        result = self.value()
        self.assertEqual(result['sample_count'], 2)
        self.assertEqual(result['timed_output_tokens'], 100)
        self.assertEqual(result['timed_duration_ms'], 3000)
        self.assertAlmostEqual(result['tokens_per_second'], 100/3)
        zero = RequestRateBook('t')
        zero.observe(start()); zero.observe(record(output=0))
        self.assertEqual(zero.projection('u')['tokens_per_second'], 0)

    def test_duplicate_usage_and_count_do_not_move_request_boundary(self):
        self.feed(record(), count())
        self.feed(record(at=1200), count(at=15000))
        self.feed(record('r2', 200, 3000))
        self.assertEqual(self.value()['sample_count'], 2)
        self.assertEqual(self.value()['timed_duration_ms'], 3000)
        self.assertEqual(self.value()['tokens_per_second'], 100)

    def test_conflicting_response_is_sticky_and_both_sums_are_removed(self):
        self.feed(record(), count(), record('r2', 200, 3000), count(200, 3000, 300))
        self.feed(record(output=101), record())
        result = self.value()
        self.assertEqual(result['timed_output_tokens'], 200)
        self.assertEqual(result['timed_duration_ms'], 2000)
        self.assertEqual(result['coverage'], 'partial')
        self.assertIn('response_conflict', result['quality'])

    def test_count_conflict_or_invalid_time_retracts_preview(self):
        for confirmation in (count(101), count(at=900), {**count(), 'timestamp': 'invalid'}):
            with self.subTest(confirmation=confirmation):
                book = RequestRateBook('t')
                for item in (start(), record(), confirmation): book.observe(item)
                result = book.projection('u')
                self.assertIsNone(result['tokens_per_second'])
                self.assertEqual((result['timed_output_tokens'], result['timed_duration_ms']), (0, 0))

    def test_tool_wait_and_parallel_result_do_not_shift_frozen_start(self):
        self.feed(row('response_item', 800, type='function_call'),
                  row('response_item', 900, type='function_call_output'),
                  row('response_item', 950, type='message', role='assistant'),
                  record(at=1000), row('response_item', 16000, type='function_call_output'), count(at=16000))
        self.assertEqual(self.value()['timed_duration_ms'], 1000)
        self.feed(row('response_item', 17000, type='reasoning'), record('r2', 100, 18000))
        self.assertEqual(self.value()['timed_duration_ms'], 3000)

    def test_long_valid_window_is_retained(self):
        self.feed(record(at=352203))
        self.assertEqual(self.value()['timed_duration_ms'], 352203)

    def test_missing_confirmation_never_reuses_old_start_and_can_recover(self):
        self.feed(record(), record('r2', 200, 2000))
        self.assertEqual(self.value()['timed_output_tokens'], 100)
        self.assertEqual(self.value()['untimed_response_count'], 1)
        # A matching newer count must not revoke the older legal preview.
        # A new explicit context provides a provable recovery boundary.
        self.feed(count(200, 2000, 300), start(at=2000), record('r3', 300, 3000))
        self.assertEqual(self.value()['timed_output_tokens'], 400)
        self.assertEqual(self.value()['timed_duration_ms'], 2000)

    def test_unconfirmed_usage_before_repeated_context_cannot_block_later_counts(self):
        self.feed(record('r0', 50), count(50, 1000, 50), record('unconfirmed', 100, 2000),
                  start(at=2500), count(0, 2500, 50),
                  record('r2', 200, 3500), count(200, 3500, 350), record('r3', 300, 4500))
        result = self.value()
        self.assertEqual(result['timed_output_tokens'], 650)
        self.assertEqual(result['timed_duration_ms'], 4000)
        self.assertEqual(result['sample_count'], 4)
        self.assertEqual(result['untimed_response_count'], 0)
        self.assertEqual(result['pending_response_count'], 2)
        self.feed(count(300, 4500, 650))
        self.assertEqual(self.value()['pending_response_count'], 1)
        self.assertEqual(self.value()['quality'], [])

    def test_completion_and_late_count_preserve_preview_without_advancing_new_turn(self):
        self.feed(record(), row('event_msg', 1200, type='task_complete', turn_id='u'), start('new', 2000))
        self.assertEqual(self.value()['tokens_per_second'], 100)
        self.feed(count(at=2500), record('r2', 200, 3000, 'new'))
        self.assertEqual(self.value('new')['tokens_per_second'], 200)
        self.assertEqual(self.value()['pending_response_count'], 0)

    def test_old_turn_record_and_foreign_thread_do_not_pollute_active_turn(self):
        self.feed(record(), count(), start('new', 2000), record('late', 500, 2500, 'u'))
        self.feed(row('response_item', 2900, type='function_call_output', metadata={'turn_id': 'u'}))
        foreign = record('foreign', at=2600, turn='new'); foreign['payload']['thread_id'] = 'child'
        self.feed(foreign, record('r2', 200, 3000, 'new'))
        self.assertEqual(self.value('new')['timed_output_tokens'], 200)
        self.assertEqual(self.value()['timed_output_tokens'], 100)
        self.assertEqual(self.value()['untimed_response_count'], 1)

    def test_late_count_with_identical_buckets_settles_oldest_pending_turn(self):
        self.feed(record(), start('new', 2000), record('r2', at=3000, turn='new'), count(at=3100))
        self.assertEqual(self.value()['pending_response_count'], 0)
        self.assertEqual(self.value('new')['pending_response_count'], 1)
        self.assertEqual(self.book.timing.boundary_ms, 1767225602000)

    def test_old_turn_usage_after_new_preview_cannot_break_current_continuity(self):
        self.feed(record(), count(), start('new', 2000), record('r2', 200, 3000, 'new'),
                  record('late-old', 500, 3200, 'u'),
                  row('event_msg', 3300, type='user_message', turn_id='u', message='late input'),
                  count(200, 3500, 300), record('r3', 200, 4000, 'new'))
        self.assertEqual(self.value('new')['timed_duration_ms'], 1500)
        self.assertEqual(self.value('new')['sample_count'], 2)

    def test_compaction_breaks_unknown_request_continuity(self):
        self.feed(record(), count(), row('compacted', 1500), record('r2', at=2000))
        self.assertEqual(self.value()['timed_output_tokens'], 100)
        self.assertEqual(self.value()['untimed_response_count'], 1)

    def test_system_injection_is_not_a_new_request_but_human_markup_is(self):
        self.feed(row('event_msg', 800, type='user_message', message='<environment_context>synthetic'), record())
        self.assertEqual(self.value()['timed_duration_ms'], 1000)
        self.feed(count(), row('event_msg', 1500, type='user_message', message='<hello>human input'), record('r2', at=2000))
        self.assertEqual(self.value()['timed_duration_ms'], 1500)

    def test_invalid_numeric_and_clock_evidence_produces_unknown(self):
        for invalid in (True, -1, 1.5, float('inf'), None):
            item = record(); item['payload']['usage']['output_tokens'] = invalid
            book = RequestRateBook('t')
            for r in (start(), item): book.observe(r)
            self.assertIsNone(book.projection('u')['tokens_per_second'])
        for records in ((start(at=1000), record(at=1000)),
                        (start(at=1000), row('response_item', 900, type='reasoning'), record(at=2000)),
                        (start(), row('response_item', 3000, type='reasoning'), record(at=2000))):
            book = RequestRateBook('t')
            for r in records: book.observe(r)
            self.assertIsNone(book.projection('u')['tokens_per_second'])

    def test_response_limit_returns_unknown_and_new_state_is_measured(self):
        before = retained_bytes(self.book)
        with patch('codex_activity_monitor.request_rate.MAX_RESPONSES_PER_TURN', 2):
            self.feed(record(), count(), record('r2', at=2000), count(at=2000, total=200), record('r3', at=3000))
        self.assertGreater(retained_bytes(self.book), before)
        self.assertIsNone(self.value()['tokens_per_second'])
        self.assertEqual(len(self.book.turns['u'].responses), 2)
        self.assertIn('response_limit', self.value()['quality'])

    def test_legacy_counts_are_untimed_and_missing_ids_are_not_inferred(self):
        self.feed(count())
        missing = record(); del missing['payload']['response_id']
        self.feed(missing)
        self.assertIsNone(self.value()['tokens_per_second'])
        self.assertEqual(self.value()['untimed_response_count'], 2)


class RequestRateHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.scope = self.tmp.name
        sessions = Path(self.scope)/'sessions'; sessions.mkdir()
        self.path = sessions/'synthetic-t.jsonl'
        self.header = {'type': 'session_meta', 'payload': {'id': 't'}}
        self.rows = [self.header, start(), record(), count(), start('new', 2000), record('r2', 200, 3000, 'new')]

    def write(self, rows):
        self.path.write_text(''.join(json.dumps(r)+'\n' for r in rows))

    def test_partial_tail_append_and_rebuild_match_single_pass(self):
        self.write(self.rows[:2])
        index = HistoryDirectory(self.path, self.scope, 't'); index.refresh()
        encoded = json.dumps(self.rows[2])
        with self.path.open('a') as f: f.write(encoded[:30])
        index.refresh()
        self.assertIsNone(index.request_rates.projection('u')['tokens_per_second'])
        with self.path.open('a') as f: f.write(encoded[30:]+'\n')
        index.refresh()
        self.assertEqual(index.request_rates.projection('u')['tokens_per_second'], 100)
        with self.path.open('a') as f:
            for item in self.rows[3:]: f.write(json.dumps(item)+'\n')
        index.refresh()
        whole = HistoryDirectory(self.path, self.scope, 't'); whole.refresh()
        self.assertEqual(index.request_rates, whole.request_rates)

    def test_query_selected_turn_isolated_and_rebuilds_after_eviction_and_rewrite(self):
        self.write(self.rows)
        query = TimelineQuery(history=HistoryProvider(capacity=0))
        old = query.bundle(self.scope, 't', 'u')['average_speed']
        new = query.bundle(self.scope, 't', 'new')['average_speed']
        self.assertEqual((old['tokens_per_second'], new['tokens_per_second']), (100, 200))
        self.assertEqual(query.bundle(self.scope, 't', 'u')['average_speed'], old)
        self.rows[2]['payload']['usage'] = usage(300)
        self.rows[3] = count(300)
        self.write(self.rows)
        self.assertEqual(query.bundle(self.scope, 't', 'u')['average_speed']['tokens_per_second'], 300)
        self.write([self.header, start(), record(output=0)])
        self.assertEqual(query.bundle(self.scope, 't', 'u')['average_speed']['tokens_per_second'], 0)
        replacement = self.path.with_suffix('.new')
        replacement.write_text(''.join(json.dumps(r)+'\n' for r in [self.header, start(), record(output=400)]))
        replacement.replace(self.path)
        self.assertEqual(query.bundle(self.scope, 't', 'u')['average_speed']['tokens_per_second'], 400)

    def test_earlier_usage_rewrite_plus_append_invalidates_cached_average(self):
        self.rows += [row('response_item', 4000, type='message', role='user', content=[{'text': 'x'*400}])]
        self.write(self.rows)
        query = TimelineQuery()
        self.assertEqual(query.bundle(self.scope, 't', 'u')['average_speed']['tokens_per_second'], 100)
        self.rows[2]['payload']['usage'] = usage(900)
        self.rows[3] = count(900)
        self.rows.append(row('event_msg', 5000, type='synthetic'))
        self.write(self.rows)
        self.assertEqual(query.bundle(self.scope, 't', 'u')['average_speed']['tokens_per_second'], 900)

    def test_request_boundary_rewrite_plus_append_invalidates_cached_average(self):
        cases = [
            ([row('event_msg', 500, type='user_message', message='synthetic input')],
             [row('event_msg', 800, type='user_message', message='synthetic input')], 1000, 700),
            ([row('compacted', 500)], [row('compacter', 500)], 0, 1500),
            ([row('event_msg', 500, type='context_compacted')],
             [row('event_msg', 500, type='context_compactex')], 0, 1500),
            ([row('event_msg', 500, type='thread_rolled_back')],
             [row('event_msg', 500, type='thread_rolled_bacx')], 0, 1500),
            ([row('response_item', 200, type='web_search_call_output'),
              row('response_item', 300, type='web_search_call'),
              row('response_item', 500, type='function_call_output')],
             [row('response_item', 200, type='web_search_call_output'),
              row('response_item', 300, type='web_search_calx'),
              row('response_item', 500, type='function_call_output')], 1300, 1000),
            ([row('response_item', 500, type='web_search_call_output')],
             [row('response_item', 800, type='web_search_call_output')], 1000, 700),
        ]
        for before, after, old_duration, new_duration in cases:
            with self.subTest(boundary=before[0]['type'], kind=before[0]['payload'].get('type')):
                tail = [record(at=1500), count(at=1500),
                        row('event_msg', 1600, type='synthetic', padding='x'*400)]
                self.write([self.header, start(), *before, *tail])
                query = TimelineQuery()
                old = query.bundle(self.scope, 't', 'u')['average_speed']
                self.assertEqual(old['timed_duration_ms'], old_duration)
                self.write([self.header, start(), *after, *tail, row('event_msg', 2000, type='synthetic')])
                result = query.bundle(self.scope, 't', 'u')['average_speed']
                self.assertEqual(result['timed_duration_ms'], new_duration)
                self.assertAlmostEqual(result['tokens_per_second'], 100000/new_duration)

    def test_missing_source_cannot_reuse_cached_average(self):
        self.write(self.rows)
        query = TimelineQuery()
        self.assertIsNotNone(query.bundle(self.scope, 't', 'u')['average_speed'])
        self.path.unlink()
        self.assertIsNone(query.bundle(self.scope, 't', 'u')['average_speed'])

    def test_parent_query_does_not_merge_child_output_or_same_response_id(self):
        self.write(self.rows[:4])
        child = self.path.with_name('synthetic-child.jsonl')
        child_rows = [self.header, start(), record(output=900)]
        child_rows = json.loads(json.dumps(child_rows).replace('"t"', '"child"'))
        child.write_text(''.join(json.dumps(r)+'\n' for r in child_rows))
        query = TimelineQuery()
        parent = query.bundle(self.scope, 't', 'u')['average_speed']
        descendant = query.bundle(self.scope, 'child', 'u')['average_speed']
        self.assertEqual((parent['timed_output_tokens'], descendant['timed_output_tokens']), (100, 900))

    def test_large_response_ledger_is_charged_to_cache_admission(self):
        rows = [self.header, start()]
        for n in range(1, 61):
            rows.extend((record(f'r{n}', 1, n*1000), count(1, n*1000, n)))
        self.write(rows)
        provider = HistoryProvider(max_cache_bytes=8000)
        index = provider.index(self.scope, 't', directory_only=True)
        self.assertEqual(index.request_rates.projection('u')['sample_count'], 60)
        self.assertGreater(retained_bytes(index.request_rates), 8000)
        self.assertEqual(provider.diagnostics()['indices'], 0)
        self.assertEqual(provider.diagnostics()['cache_bytes'], 0)


if __name__ == '__main__':
    unittest.main()
