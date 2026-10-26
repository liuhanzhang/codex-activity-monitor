"""History size must not turn every selected-turn read into a full rebuild."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codex_activity_monitor.history import HistoryProvider, RolloutIndex
from codex_activity_monitor.history_index import HistoryDirectory, HistoryFacts, HistoryMetadata
from codex_activity_monitor.timeline import HISTORY_SEED_TYPES
from codex_activity_monitor.timeline_query import TimelineQuery


class HistoryDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.scope = self.tmp.name
        sessions = Path(self.scope)/'sessions'; sessions.mkdir()
        self.path = sessions/'synthetic-t.jsonl'
        self.rows = [{'type': 'session_meta', 'payload': {'id': 't', 'history_mode': 'paginated'}}]
        for n in range(200):
            self.rows.append({'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': f'u{n}'}})
            for item in range(8):
                self.rows.append({'type': 'event_msg', 'payload': {'type': 'item_completed', 'thread_id': 't',
                    'turn_id': f'u{n}', 'item': {'id': f'a{n}-{item}', 'type': 'AgentMessage', 'text': 'raw中文🙂'}}})
            self.rows.append({'type': 'event_msg', 'payload': {'type': 'task_complete', 'turn_id': f'u{n}'}})
        self.write()

    def write(self):
        self.path.write_text(''.join(json.dumps(r)+'\n' for r in self.rows))

    def test_scoped_facts_load_only_requested_items_and_the_existing_seed_window(self):
        for n in range(50):
            self.rows.append({'type': 'event_msg', 'payload': {'type': 'item_completed', 'thread_id': 't',
                'turn_id': 'u0', 'item': {'id': f'new-{n}', 'type': 'AgentMessage', 'text': 'PRIVATE_BODY'}}})
        self.write()
        provider = HistoryProvider()
        selected = frozenset({('u0', 'a0-0'), ('u0', 'missing')})
        with patch.object(HistoryDirectory, 'load_turn', side_effect=AssertionError('loaded full turn')):
            summary = provider.reconciliation(self.scope, 't', {'u0'}, items=selected)
        self.assertEqual(summary['phase_selection'], [['u0', 'a0-0'], ['u0', 'missing']])
        self.assertEqual({p['item_id'] for p in summary['phases']}, {'a0-0'} | {f'new-{n}' for n in range(30, 50)})
        self.assertTrue(summary['read_complete'])
        self.assertNotIn('PRIVATE_BODY', json.dumps(summary))
        facts = next(v for k, v in provider.indices.items() if k.kind == 'facts')
        self.assertIsInstance(facts, HistoryFacts)
        self.assertEqual(facts.refs, {})
        self.assertEqual(facts.outputs, {})
        summary['phases'][0]['label'] = 'mutated'
        with patch.object(HistoryFacts, '_row', side_effect=AssertionError('reparsed warm facts')):
            again = provider.reconciliation(self.scope, 't', {'u0'}, items=selected)
        self.assertNotEqual(again['phases'][0]['label'], 'mutated')

    def test_seed_catalogue_matches_full_projection_after_late_source_updates(self):
        for n in range(40):
            self.rows.append({'type': 'event_msg', 'payload': {'type': 'item_completed', 'thread_id': 't',
                'turn_id': 'u0', 'item': {'id': f'late-{n}', 'type': 'AgentMessage', 'text': 'synthetic'}}})
        # Repeated canonical/raw records keep the phase's original insertion
        # position and the full projection's terminal/type semantics.
        self.rows += [
            {'type': 'event_msg', 'payload': {'type': 'item_completed', 'thread_id': 't', 'turn_id': 'u0',
                'item': {'id': 'late-35', 'type': 'AgentMessage', 'status': 'inProgress'}}},
            {'type': 'event_msg', 'payload': {'type': 'item_completed', 'thread_id': 't', 'turn_id': 'u0',
                'item': {'id': 'a0-0', 'type': 'AgentMessage', 'status': 'completed'}}}]
        self.rows[0]['payload']['history_mode'] = 'legacy'
        self.rows.append({'type': 'response_item', 'payload': {'type': 'message', 'id': 'late-36',
            'role': 'assistant', 'metadata': {'turn_id': 'u0'}, 'content': [{'type': 'text', 'text': 'synthetic'}]}})
        self.write()
        full = RolloutIndex(self.path, self.scope, 't'); full.refresh()
        expected = [p for p in full.phases.values() if p['turn_id'] == 'u0' and
                    p.get('history_terminal') and p['item_type'] in HISTORY_SEED_TYPES][-20:]
        provider = HistoryProvider()
        directory = provider.index(self.scope, 't', directory_only=True)
        self.assertEqual(directory.seed_ids('u0'), tuple(p['id'] for p in expected))
        actual = provider.reconciliation(self.scope, 't', {'u0'}, items=frozenset())
        self.assertEqual(actual['phases'], expected)

    def test_narrow_fact_cache_survives_directory_eviction_and_invalidates_on_rewrite(self):
        provider = HistoryProvider(capacity=0)
        selected = frozenset({('u0', 'a0-0')})
        first = provider.reconciliation(self.scope, 't', {'u0'}, items=selected)
        with patch.object(HistoryFacts, '_row', side_effect=AssertionError('reparsed warm selection')):
            self.assertEqual(provider.reconciliation(self.scope, 't', {'u0'}, items=selected), first)
        self.rows[2]['payload']['item']['status'] = 'inProgress'
        self.write()
        changed = provider.reconciliation(self.scope, 't', {'u0'}, items=selected)
        self.assertNotEqual(changed['generation'], first['generation'])
        fact = next(p for p in changed['phases'] if p['item_id'] == 'a0-0')
        self.assertFalse(fact['history_terminal'])

    def test_hot_details_and_reconciliation_reuse_a_small_source_directory(self):
        provider = HistoryProvider(max_cache_bytes=500_000)
        first = provider.selected_view(self.scope, 't', 'u73')
        self.assertEqual(len(first.phases), 8)
        parsed = []
        original = RolloutIndex._row
        def count(index, row, *args):
            parsed.append(row)
            return original(index, row, *args)
        with patch.object(RolloutIndex, '_row', count):
            again = provider.selected_view(self.scope, 't', 'u73')
            summary = provider.reconciliation(self.scope, 't', {'u73'})
        self.assertEqual(set(again.phases), set(first.phases))
        self.assertEqual(len(summary['phases']), 8)
        self.assertEqual(len(parsed), 0)
        self.assertGreater(provider.diagnostics()['cache_bytes'], 0)
        self.assertLessEqual(provider.diagnostics()['cache_bytes'], 500_000)

    def test_unrelated_append_only_parses_the_tail_and_selected_turn(self):
        provider = HistoryProvider(max_cache_bytes=500_000)
        first = provider.selected_view(self.scope, 't', 'u73')
        with self.path.open('a') as stream:
            stream.write(json.dumps({'type': 'event_msg', 'payload': {'type': 'token_count'}})+'\n')
        original = RolloutIndex._row; parsed = []
        def count(index, row, *args):
            parsed.append(row); return original(index, row, *args)
        opened = Path.open; reads = []
        class Measured:
            def __init__(self, stream): self.stream = stream
            def __getattr__(self, name): return getattr(self.stream, name)
            def __enter__(self): return self
            def __exit__(self, *args): return self.stream.__exit__(*args)
            def read(self, *args):
                value = self.stream.read(*args); reads.append(len(value)); return value
            def readline(self, *args):
                value = self.stream.readline(*args); reads.append(len(value)); return value
        def measured(path, *args, **kwargs):
            stream = opened(path, *args, **kwargs)
            return Measured(stream) if path == self.path and args == ('rb',) else stream
        with patch.object(RolloutIndex, '_row', count), patch.object(Path, 'open', measured):
            again = provider.selected_view(self.scope, 't', 'u73')
        self.assertEqual(set(again.phases), set(first.phases))
        self.assertLessEqual(len(parsed), 11)
        self.assertLess(sum(reads), 8192)

    def test_selected_view_matches_the_full_parser_for_every_requested_turn(self):
        full = RolloutIndex(self.path, self.scope, 't'); full.refresh()
        provider = HistoryProvider(max_cache_bytes=500_000)
        for turn in ('u0', 'u73', 'u199'):
            selected = provider.selected_view(self.scope, 't', turn)
            expected = {key: value for key, value in full.phases.items() if value['turn_id'] == turn}
            self.assertEqual(selected.phases, expected)
            for phase in selected.phases.values(): self.assertEqual(selected.detail(phase), full.detail(phase))

    def test_complete_timeline_reuses_the_directory_and_selected_turn(self):
        provider = HistoryProvider(max_cache_bytes=500_000)
        query = TimelineQuery(history=provider)
        first = query.bundle(self.scope, 't', 'u73')
        self.assertEqual(len(first['phases']), 8)
        self.assertEqual(len(first['turns']), 7)  # Six recent turns plus the older selection.
        self.assertIn('u73', {t['turn_id'] for t in first['turns']})
        with patch.object(HistoryMetadata, '_row', side_effect=AssertionError('reparsed warm source')):
            again = query.bundle(self.scope, 't', 'u73')
        self.assertEqual(again['phases'], first['phases'])
        self.assertEqual(again['turns'], first['turns'])

    def test_selected_turn_survives_eviction_of_its_source_directory(self):
        provider = HistoryProvider(capacity=0)
        first = provider.selected_view(self.scope, 't', 'u73')
        self.assertTrue(all(key.kind == 'turn' for key in provider.indices))
        with patch.object(RolloutIndex, '_row', side_effect=AssertionError('rebuilt evicted directory')):
            again = provider.selected_view(self.scope, 't', 'u73')
            summary = provider.reconciliation(self.scope, 't', {'u73'})
        self.assertEqual(again.phases, first.phases)
        self.assertEqual(len(summary['phases']), 8)
        # Unchanged file identity is verified too; replacing the source header
        # cannot re-use detached metadata just because the directory was evicted.
        self.rows[0]['payload']['id'] = 'another-thread'
        self.write()
        self.assertIsNone(provider.selected_view(self.scope, 't', 'u73'))

    def test_an_uncacheable_turn_does_not_flush_other_reading_selections(self):
        for n in range(5000):
            self.rows.append({'type': 'event_msg', 'payload': {'type': 'item_completed', 'thread_id': 't',
                'turn_id': 'u0', 'item': {'id': f'giant-{n}', 'type': 'AgentMessage', 'text': 'synthetic'}}})
        self.write()
        provider = HistoryProvider(max_cache_bytes=2*1024*1024)
        first = provider.selected_view(self.scope, 't', 'u73')
        self.assertEqual(len(provider.selected_view(self.scope, 't', 'u0').phases), 5008)
        self.assertLessEqual(provider.diagnostics()['cache_bytes'], 2*1024*1024)
        with patch.object(RolloutIndex, '_row', side_effect=AssertionError('giant turn flushed warm selection')):
            self.assertEqual(provider.selected_view(self.scope, 't', 'u73').phases, first.phases)

    def test_old_span_rewrite_plus_append_cannot_keep_a_cached_completion(self):
        provider = HistoryProvider(max_cache_bytes=500_000)
        self.assertEqual(provider.reconciliation(self.scope, 't', {'u0'})['turns'][0]['status'], 'completed')
        # Earlier same-size content changes do not touch the 256-byte tail anchor.
        self.rows[10]['payload']['type'] = 'task_completx'
        self.rows.append({'type': 'event_msg', 'payload': {'type': 'token_count'}})
        self.write()
        summary = provider.reconciliation(self.scope, 't', {'u0'})
        self.assertNotEqual(summary['turns'][0]['status'], 'completed')

    def test_directory_does_not_reopen_a_nonactive_phase_on_a_late_active_record(self):
        self.rows.append({'type': 'event_msg', 'payload': {'type': 'item_completed', 'thread_id': 't', 'turn_id': 'u0',
            'item': {'id': 'a0-0', 'type': 'AgentMessage', 'text': 'raw中文🙂', 'status': 'inProgress'}}})
        for state in ('completed', 'unknown'):
            with self.subTest(state=state):
                self.rows[2]['payload']['item']['status'] = state
                self.write()
                provider = HistoryProvider(max_cache_bytes=500_000)
                directory = provider.index(self.scope, 't', directory_only=True)
                self.assertEqual(directory.phases, {})
                selected = provider.turn_view(directory, {'u0'})
                phase = next(p for p in selected.phases.values() if p['item_id'] == 'a0-0')
                self.assertEqual(phase['status'], state)
