"""Current rollout and bounded inherited history, using invented content only."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from codex_activity_monitor.history import HistoryProvider
from codex_activity_monitor.timeline import phase_id
from codex_activity_monitor.timeline import TimelineBook
from codex_activity_monitor.timeline_query import TimelineQuery
from codex_activity_monitor.engine import Engine
from codex_activity_monitor.events import Event, filter_event
from codex_activity_monitor.history_refresh import HistoryRefresh
from codex_activity_monitor.history_contract import HistorySelection
import threading
import time


class RolloutHistoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.directory = self.home/'sessions/2026/10/29'
        self.directory.mkdir(parents=True)
        self.provider = HistoryProvider()
        self.query = TimelineQuery(history=self.provider)

    def write(self, thread, rollout, turns, *, base=None, rates=None, **header):
        path = self.directory/f'rollout-2026-10-29T12-00-00-{thread}{"_"+rollout if rollout != thread else ""}.jsonl'
        start = base['end_ordinal_exclusive'] if base else 0
        rows = [{'type': 'session_meta', 'payload': {'id': thread, 'history_mode': 'paginated', **({'history_base': base} if base else {}), **header}}]
        for turn, text in turns:
            rows.extend([
                {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': turn}},
                {'type': 'event_msg', 'payload': {'type': 'item_completed', 'thread_id': thread, 'turn_id': turn,
                    'started_at_ms': 1793275200000, 'completed_at_ms': 1793275201000,
                    'item': {'type': 'AgentMessage', 'id': 'message-'+turn, 'content': text}}},
                {'type': 'event_msg', 'payload': {'type': 'task_complete', 'turn_id': turn}},
            ])
            if rates and turn in rates:
                from datetime import datetime, timedelta, timezone
                output, duration = rates[turn]
                at = (datetime(2026, 10, 29, 12, tzinfo=timezone.utc)+timedelta(milliseconds=duration)).isoformat()
                usage = {'input_tokens': 200, 'cached_input_tokens': 0, 'output_tokens': output, 'reasoning_output_tokens': 0}
                rows[-1]['timestamp'] = at
                rows[-1:-1] = [
                    {'timestamp': at, 'type': 'token_usage_record', 'payload': {'thread_id': thread, 'turn_id': turn, 'response_id': 'response-'+turn, 'usage': usage}},
                    {'timestamp': at, 'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {'last_token_usage': usage, 'total_token_usage': usage}}},
                ]
        offsets = []
        with path.open('wb') as stream:
            for ordinal, row in enumerate(rows, start):
                row.setdefault('timestamp', '2026-10-29T12:00:00Z')
                row['ordinal'] = ordinal
                stream.write((json.dumps(row)+'\n').encode())
                offsets.append(stream.tell())
        return path, offsets

    def select(self, thread, path):
        with sqlite3.connect(self.home/'state_5.sqlite') as db:
            db.execute('CREATE TABLE IF NOT EXISTS threads (id TEXT PRIMARY KEY, rollout_path TEXT, history_mode TEXT)')
            db.execute('INSERT OR REPLACE INTO threads VALUES (?,?,?)', (thread, str(path), 'paginated'))

    def detail(self, thread, turn):
        return self.query.detail(str(self.home), thread, turn,
            phase_id(str(self.home), thread, turn, 'model', 'message-'+turn))

    def test_current_pointer_selects_replacement_while_old_file_remains(self):
        old, _ = self.write('t', 't', [('old', 'old branch')])
        new, _ = self.write('t', 'replacement', [('new', 'new branch')])
        self.select('t', new)
        result = self.detail('t', 'new')
        self.assertEqual(result['state'], 'available')
        self.assertEqual(result['sections'][0]['text'], 'new branch')
        self.assertTrue(old.exists())

    def test_warm_detail_is_not_reused_after_pointer_switch(self):
        old, _ = self.write('t', 't', [('same', 'old branch')])
        self.select('t', old)
        self.assertEqual(self.detail('t', 'same')['sections'][0]['text'], 'old branch')
        new, _ = self.write('t', 'replacement', [('same', 'new branch')])
        self.select('t', new)
        result = self.detail('t', 'same')
        self.assertEqual(result['sections'][0]['text'], 'new branch')

    def test_revert_reads_only_retained_prefix_and_current_suffix(self):
        old, offsets = self.write('t', 't', [('keep', 'keep me'), ('discard', 'discard me')])
        base = {'thread_id': 't', 'end_ordinal_exclusive': 4, 'end_byte_offset': offsets[3]}
        new, _ = self.write('t', 'replacement', [('new', 'new branch')], base=base)
        self.select('t', new)
        self.assertEqual(self.detail('t', 'keep')['sections'][0]['text'], 'keep me')
        self.assertEqual(self.detail('t', 'new')['sections'][0]['text'], 'new branch')
        self.assertFalse(self.detail('t', 'discard')['sections'])
        self.assertEqual(set(self.provider.index(str(self.home), 't').turns), {'keep', 'new'})

    def test_user_fork_validates_source_owner_and_projects_inherited_turn(self):
        parent, offsets = self.write('parent', 'parent', [('keep', 'parent prefix'), ('discard', 'not inherited')])
        base = {'thread_id': 'parent', 'end_ordinal_exclusive': 4, 'end_byte_offset': offsets[3]}
        child, _ = self.write('child', 'child', [('own', 'child output')], base=base)
        self.select('child', child)
        detail = self.detail('child', 'keep')
        self.assertEqual(detail['state'], 'available')
        self.assertEqual(detail['sections'][0]['text'], 'parent prefix')
        self.assertEqual(detail['phase']['thread_id'], 'child')
        self.assertFalse(self.detail('child', 'discard')['sections'])

    def test_missing_authoritative_path_does_not_fall_back_to_old_branch(self):
        self.write('t', 't', [('old', 'old branch')])
        self.select('t', self.directory/'missing.jsonl')
        self.assertFalse(self.detail('t', 'old')['sections'])

    def test_multiple_reverts_follow_rollout_identity_and_can_jump_to_an_ancestor(self):
        root, offsets = self.write('t', 't', [('keep', 'root prefix'), ('discard', 'old suffix')])
        base = {'thread_id': 't', 'end_ordinal_exclusive': 4, 'end_byte_offset': offsets[3]}
        first, first_offsets = self.write('t', 'first', [('middle', 'middle output')], base=base)
        second_base = {'thread_id': 'first', 'end_ordinal_exclusive': 8, 'end_byte_offset': first_offsets[3]}
        second, _ = self.write('t', 'second', [('last', 'last output')], base=second_base)
        self.select('t', second)
        for turn, expected in [('keep', 'root prefix'), ('middle', 'middle output'), ('last', 'last output')]:
            self.assertEqual(self.detail('t', turn)['sections'][0]['text'], expected)
        self.assertFalse(self.detail('t', 'discard')['sections'])
        jump, _ = self.write('t', 'jump', [('alternative', 'alternative output')], base=base)
        self.select('t', jump)
        self.assertEqual(self.detail('t', 'keep')['sections'][0]['text'], 'root prefix')
        self.assertFalse(self.detail('t', 'middle')['sections'])
        self.assertFalse(self.detail('t', 'last')['sections'])

    def test_ambiguous_discovery_does_not_choose_by_mtime(self):
        self.write('t', 't', [('old', 'old branch')])
        self.write('t', 'replacement', [('new', 'new branch')])
        self.assertFalse(self.detail('t', 'old')['sections'])
        self.assertFalse(self.detail('t', 'new')['sections'])

    def test_explicit_sqlite_directory_is_read_without_modifying_sources(self):
        import hashlib
        selected, _ = self.write('t', 'replacement', [('new', 'new branch')])
        database_home = self.home/'custom-state'; database_home.mkdir()
        self.select('t', selected)
        database = self.home/'state_5.sqlite'
        database.rename(database_home/database.name)
        (self.home/'config.toml').write_text('sqlite_home = '+json.dumps(str(database_home))+'\n')
        files = [selected, database_home/database.name]
        before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in files]
        self.assertEqual(self.detail('t', 'new')['sections'][0]['text'], 'new branch')
        self.assertEqual(before, [hashlib.sha256(p.read_bytes()).hexdigest() for p in files])

    def test_unknown_or_busy_database_does_not_guess_an_old_file(self):
        old, _ = self.write('t', 't', [('old', 'old branch')])
        self.select('t', old)
        database = sqlite3.connect(self.home/'state_5.sqlite')
        self.addCleanup(database.close)
        database.execute('BEGIN EXCLUSIVE')
        self.assertFalse(self.detail('t', 'old')['sections'])
        database.rollback()
        database.execute('DROP TABLE threads'); database.commit()
        self.assertFalse(self.detail('t', 'old')['sections'])

    def test_cross_scope_selection_and_symlink_are_rejected(self):
        other = self.home/'other'; other.mkdir()
        foreign = other/'foreign.jsonl'
        root, _ = self.write('t', 't', [('old', 'private branch')])
        foreign.write_bytes(root.read_bytes())
        for candidate in (foreign, self.directory/'link.jsonl'):
            if candidate != foreign:
                candidate.symlink_to(foreign)
            self.select('t', candidate)
            self.assertFalse(self.detail('t', 'old')['sections'])

    def test_invalid_cutoffs_are_not_read_as_a_complete_prefix(self):
        _, offsets = self.write('t', 't', [('keep', 'prefix'), ('discard', 'private suffix')])
        for end, ordinal in ((offsets[3]-1, 4), (offsets[3]+1, 4), (offsets[3], 5), (99999999, 4)):
            base = {'thread_id': 't', 'end_ordinal_exclusive': ordinal, 'end_byte_offset': end}
            selected, _ = self.write('t', 'replacement', [('new', 'new output')], base=base)
            self.select('t', selected)
            self.assertFalse(self.detail('t', 'discard')['sections'])
            self.assertFalse(self.detail('t', 'keep')['sections'])

    def test_missing_ancestor_keeps_provable_suffix_and_reports_partial_coverage(self):
        base = {'thread_id': 'missing', 'end_ordinal_exclusive': 4, 'end_byte_offset': 999}
        selected, _ = self.write('t', 'replacement', [('new', 'new output')], base=base)
        self.select('t', selected)
        self.assertEqual(self.detail('t', 'new')['sections'][0]['text'], 'new output')
        index = self.provider.index(str(self.home), 't')
        self.assertFalse(index.read_complete)
        self.assertIn('history_inheritance_unavailable', index.quality)

    def test_special_subagent_prefix_is_not_added_as_child_activity(self):
        selected, _ = self.write('child', 'child', [('inherited', 'parent context'), ('own', 'child output')],
                                  subagent_history_start_ordinal=4)
        self.select('child', selected)
        self.assertEqual(self.detail('child', 'own')['sections'][0]['text'], 'child output')
        self.assertFalse(self.detail('child', 'inherited')['sections'])
        self.assertFalse(self.provider.index(str(self.home), 'child').read_complete)

    def test_paging_cannot_continue_in_discarded_branch_even_with_unchanged_bytes(self):
        body = 'discarded output ' * 8
        old, offsets = self.write('t', 't', [('keep', 'prefix'), ('discard', body)])
        self.select('t', old)
        identity = phase_id(str(self.home), 't', 'discard', 'model', 'message-discard')
        first = self.query.detail(str(self.home), 't', 'discard', identity, limit=10)
        page = first['sections'][0]
        book = TimelineBook(); book._put(first['phase'])
        book.live[identity] = {'text': body}
        self.query.book = book
        base = {'thread_id': 't', 'end_ordinal_exclusive': 4, 'end_byte_offset': offsets[3]}
        new, _ = self.write('t', 'replacement', [('new', 'new output')], base=base)
        self.select('t', new)
        second = self.query.detail(str(self.home), 't', 'discard', identity, page['id'], page['next_offset'],
                                   page_token=page['page_token'])
        self.assertEqual(second['state'], 'not_retained')
        self.assertFalse(second['sections'])
        self.assertFalse(self.query.bundle(str(self.home), 't', 'discard')['phases'])
        self.assertIn(body.encode(), old.read_bytes())

    def test_pointer_switch_during_content_rendering_returns_no_mixed_body(self):
        from codex_activity_monitor.history_content import item_content
        old, _ = self.write('t', 't', [('same', 'old branch')])
        new, _ = self.write('t', 'replacement', [('same', 'new branch')])
        self.select('t', old)
        def cutover(item):
            self.select('t', new)
            return item_content(item)
        with patch('codex_activity_monitor.history_index.item_content', side_effect=cutover):
            result = self.detail('t', 'same')
        self.assertFalse(result['sections'])
        self.assertEqual(self.detail('t', 'same')['sections'][0]['text'], 'new branch')

    def test_stable_inherited_details_do_not_rescan_the_history_tree(self):
        _, offsets = self.write('t', 't', [('keep', 'prefix')])
        base = {'thread_id': 't', 'end_ordinal_exclusive': 4, 'end_byte_offset': offsets[3]}
        selected, _ = self.write('t', 'replacement', [('new', 'new output')], base=base)
        self.select('t', selected)
        self.detail('t', 'keep')
        with patch('codex_activity_monitor.history_source.os.walk', side_effect=AssertionError('rescanned stable history')):
            for _ in range(5):
                self.assertEqual(self.detail('t', 'keep')['sections'][0]['text'], 'prefix')

    def test_average_speed_uses_effective_prefix_and_never_the_discarded_suffix(self):
        _, offsets = self.write('t', 't', [('keep', 'prefix'), ('discard', 'discard')],
                                 rates={'keep': (100, 1000), 'discard': (900, 1000)})
        base = {'thread_id': 't', 'end_ordinal_exclusive': 6, 'end_byte_offset': offsets[5]}
        selected, _ = self.write('t', 'replacement', [('new', 'new output')], base=base, rates={'new': (200, 2000)})
        self.select('t', selected)
        for turn, tokens, duration in [('keep', 100, 1000), ('new', 200, 2000)]:
            value = self.query.bundle(str(self.home), 't', turn)['average_speed']
            self.assertEqual(value['timed_output_tokens'], tokens)
            self.assertEqual(value['timed_duration_ms'], duration)
            self.assertEqual(value['tokens_per_second'], 100)
        removed = self.query.bundle(str(self.home), 't', 'discard')['average_speed']
        self.assertIsNone(removed['tokens_per_second'])
        self.assertEqual(removed['sample_count'], 0)

    def test_warm_rate_ledger_changes_with_the_source(self):
        old, _ = self.write('t', 't', [('same', 'old')], rates={'same': (100, 1000)})
        self.select('t', old)
        self.assertEqual(self.query.bundle(str(self.home), 't', 'same')['average_speed']['tokens_per_second'], 100)
        new, _ = self.write('t', 'replacement', [('same', 'new')], rates={'same': (300, 2000)})
        self.select('t', new)
        value = self.query.bundle(str(self.home), 't', 'same')['average_speed']
        self.assertEqual(value['timed_output_tokens'], 300)
        self.assertEqual(value['timed_duration_ms'], 2000)
        self.assertEqual(value['tokens_per_second'], 150)

    def test_unsupported_compressed_ancestor_preserves_current_suffix_only(self):
        old, offsets = self.write('t', 't', [('keep', 'prefix')])
        base = {'thread_id': 't', 'end_ordinal_exclusive': 4, 'end_byte_offset': offsets[3]}
        selected, _ = self.write('t', 'replacement', [('new', 'new output')], base=base)
        # No decoder is invoked: representation discovery must retain the unsupported reason.
        old.with_suffix('.jsonl.zst').write_bytes(b'synthetic compressed representation')
        old.unlink()
        self.select('t', selected)
        self.assertEqual(self.detail('t', 'new')['sections'][0]['text'], 'new output')
        index = self.provider.index(str(self.home), 't')
        self.assertFalse(index.read_complete)
        self.assertIn('history_compressed_unsupported', index.quality)

    def test_old_source_results_cannot_overwrite_new_source_facts(self):
        book = TimelineBook(); engine = Engine(False, timeline=book)
        self.addCleanup(engine.close)
        engine.ingest(Event('b', 'lifecycle', {'type': 'bridge/started', 'home': str(self.home)}, 100, 1793275200))
        engine.ingest(Event('b', 'protocol_out', {'method': 'turn/started', 'params': {'threadId': 't', 'turn': {'id': 'same'}}}, 101, 1793275201))
        old, _ = self.write('t', 't', [('same', 'old')]); self.select('t', old)
        before = self.provider.reconciliation(str(self.home), 't', {'same'})
        new, _ = self.write('t', 'replacement', [('same', 'new')]); self.select('t', new)
        after = self.provider.reconciliation(str(self.home), 't', {'same'})
        self.assertGreater(after['source_epoch'], before['source_epoch'])
        engine.apply_history(after)
        latest = engine.history_versions[(str(self.home), 't')]
        engine.apply_history(before)
        self.assertEqual(engine.history_versions[(str(self.home), 't')], latest)

    def test_revert_invalidates_optional_protocol_hint_and_old_request_epoch(self):
        book = TimelineBook(); engine = Engine(False, timeline=book)
        self.addCleanup(engine.close)
        engine.ingest(Event('b', 'lifecycle', {'type': 'bridge/started', 'home': str(self.home)}, 100, 0))
        raw = {'method': 'thread/started', 'params': {'thread': {'id': 't', 'path': str(self.directory/'old.jsonl'), 'historyMode': 'paginated'}}}
        filtered = filter_event('protocol_out', raw)
        engine.ingest(Event('b', 'protocol_out', filtered, 101, 0))
        request, = engine.history_requests()
        self.assertIn('path', dict(request.source_hint))
        engine.ingest(Event('b', 'protocol_out', {'method': 'thread/reverted', 'params': {'threadId': 't'}}, 102, 0))
        after, = engine.history_requests()
        self.assertGreater(after.source_epoch, request.source_epoch)
        self.assertNotIn('path', dict(after.source_hint))
        engine.apply_history({'scope_id': str(self.home), 'thread_id': 't', 'request_epoch': request.source_epoch,
                              'revision': 1, 'generation': 'obsolete', 'quality': []})
        self.assertNotIn((str(self.home), 't'), engine.history_versions)

    def test_refresh_discards_a_read_that_finishes_after_a_new_hint(self):
        entered, release = threading.Event(), threading.Event()
        class Provider:
            def reconciliation(inner, scope, thread, turns, *, items):
                if turns == frozenset({'old'}):
                    entered.set(); release.wait(2)
                return {'scope_id': scope, 'thread_id': thread, 'revision': 1, 'quality': []}
        reader = HistoryRefresh(provider=Provider()); self.addCleanup(reader.close)
        reader.schedule([HistorySelection(str(self.home), 't', frozenset({'old'}), True, source_epoch=1)])
        self.assertTrue(entered.wait(2))
        reader.schedule([HistorySelection(str(self.home), 't', frozenset({'new'}), True, source_epoch=2)])
        release.set()
        deadline = time.monotonic()+2
        while reader.processed < 2 and time.monotonic() < deadline:
            time.sleep(.002)
        self.assertEqual(reader.processed, 2)
        values = reader.drain()
        self.assertEqual(len(values), 1)
        self.assertEqual(values[0]['request_epoch'], 2)

    def test_unavailable_current_selection_cannot_revive_completed_live_content(self):
        old, _ = self.write('t', 't', [('old', 'old branch')]); self.select('t', old)
        detail = self.detail('t', 'old')
        book = TimelineBook(); book._put(detail['phase'])
        book.live[detail['phase']['id']] = {'text': 'old live body'}
        self.query.book = book
        self.select('t', self.directory/'missing.jsonl')
        self.assertFalse(self.detail('t', 'old')['sections'])

    def test_all_warm_projections_and_evicted_rebuild_use_the_same_new_source(self):
        old, _ = self.write('t', 't', [('same', 'old body')]); self.select('t', old)
        self.provider.view(str(self.home), 't')
        self.provider.selected_view(str(self.home), 't', 'same')
        before = self.provider.reconciliation(str(self.home), 't', {'same'}, items={('same', 'message-same')})
        self.detail('t', 'same')
        new, _ = self.write('t', 'replacement', [('same', 'new body')]); self.select('t', new)
        for index in (self.provider.view(str(self.home), 't'), self.provider.selected_view(str(self.home), 't', 'same')):
            phase = next(p for p in index.phases.values() if p['item_id'] == 'message-same')
            self.assertEqual(index.detail(phase)['sections'][0]['text'], 'new body')
        after = self.provider.reconciliation(str(self.home), 't', {'same'}, items={('same', 'message-same')})
        self.assertGreater(after['source_epoch'], before['source_epoch'])
        self.provider.invalidate(str(self.home), 't')
        self.assertEqual(self.detail('t', 'same')['sections'][0]['text'], 'new body')

    def test_cycle_and_chain_budget_do_not_return_an_arbitrary_branch(self):
        base = {'thread_id': 't', 'end_ordinal_exclusive': 4, 'end_byte_offset': 99999}
        selected, _ = self.write('t', 't', [('own', 'own body')], base=base)
        self.select('t', selected)
        self.assertFalse(self.detail('t', 'own')['sections'])
        root, offsets = self.write('t', 't', [('keep', 'prefix')])
        base = {'thread_id': 't', 'end_ordinal_exclusive': 4, 'end_byte_offset': offsets[3]}
        selected, _ = self.write('t', 'replacement', [('new', 'new body')], base=base)
        self.select('t', selected)
        self.provider.resolver.max_lineage = 1
        self.assertFalse(self.detail('t', 'new')['sections'])

    def test_attached_special_shape_does_not_expand_the_parent_prefix(self):
        root, offsets = self.write('parent', 'parent', [('keep', 'parent prefix')])
        base = {'thread_id': 'parent', 'end_ordinal_exclusive': 4, 'end_byte_offset': offsets[3]}
        selected, _ = self.write('child', 'child', [('own', 'child body')], base=base, attached_thread=True,
                                 rates={'own': (100, 1000)})
        self.select('child', selected)
        self.assertEqual(self.detail('child', 'own')['sections'][0]['text'], 'child body')
        self.assertFalse(self.detail('child', 'keep')['sections'])
        average = self.query.bundle(str(self.home), 'child', 'own')['average_speed']
        self.assertEqual(average['coverage'], 'partial')

    def test_source_switch_does_not_change_live_token_windows_or_another_chat(self):
        from codex_activity_monitor.metrics import count_window
        def counted(*args):
            count, _, baseline = count_window(*args)
            return count, 0.0, baseline
        old, offsets = self.write('t', 't', [('keep', 'prefix')]); self.select('t', old)
        engines = [Engine(False, timeline=TimelineBook()) for _ in range(2)]
        for engine in engines:
            self.addCleanup(engine.close)
        def send(value, at, direction='protocol_out'):
            for engine in engines:
                engine.ingest(Event('b', direction, value, at, 1793275200+at-100))
        send({'type': 'bridge/started', 'home': str(self.home)}, 100, 'lifecycle')
        for owner, turn, item in [('t', 'live', 'main-item'), ('other', 'other-live', 'other-item')]:
            send({'method': 'turn/started', 'params': {'threadId': owner, 'turn': {'id': turn}}}, 100.1)
            send({'type': 'response.output_item.added', 'item': {'id': item, 'type': 'message', 'metadata': {'turn_id': turn}}}, 100.2, 'upstream')
        base = {'thread_id': 't', 'end_ordinal_exclusive': 4, 'end_byte_offset': offsets[3]}
        selected, _ = self.write('t', 'replacement', [('recent', 'recent output')], base=base)
        for sequence in range(4):
            send({'type': 'response.output_text.delta', 'item_id': 'main-item', 'sequence_number': sequence,
                  'delta': 'hello world '}, 101+sequence*.2, 'upstream')
            send({'type': 'response.output_text.delta', 'item_id': 'other-item', 'sequence_number': sequence,
                  'delta': 'other chat '}, 101+sequence*.2, 'upstream')
            if sequence == 1:
                self.select('t', selected)
            summary = self.provider.reconciliation(str(self.home), 't', {'keep', 'recent', 'live'})
            engines[1].apply_history(summary)
            now = 101.05+sequence*.2
            # CPU timing is diagnostic input to throttling, not the effect under test.
            with patch('codex_activity_monitor.metrics.count_window', side_effect=counted):
                for engine in engines:
                    engine.tick(now); engine.snapshot(now)
            for item in ('main-item', 'other-item'):
                baseline = engines[0].streams[('b', item, 'text', 0)].view(now)
                actual = engines[1].streams[('b', item, 'text', 0)].view(now)
                self.assertEqual(actual, baseline)

    def test_optional_protocol_hint_can_select_without_a_database(self):
        old, _ = self.write('t', 't', [('same', 'old body')])
        new, _ = self.write('t', 'replacement', [('same', 'new body')])
        self.provider.configure_source(str(self.home), 't', 1, (('path', str(old)), ('history_mode', 'paginated')))
        self.assertEqual(self.detail('t', 'same')['sections'][0]['text'], 'old body')
        self.provider.configure_source(str(self.home), 't', 2, (('path', str(new)), ('history_mode', 'paginated')))
        self.assertEqual(self.detail('t', 'same')['sections'][0]['text'], 'new body')
        # A late worker carrying the earlier hint cannot restore the old path.
        self.provider.configure_source(str(self.home), 't', 1, (('path', str(old)),))
        self.assertEqual(self.detail('t', 'same')['sections'][0]['text'], 'new body')

    def test_readonly_sqlite_selection_observes_committed_wal_updates(self):
        old, _ = self.write('t', 't', [('same', 'old body')]); self.select('t', old)
        writer = sqlite3.connect(self.home/'state_5.sqlite'); self.addCleanup(writer.close)
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_autocheckpoint=0')
        self.assertEqual(self.detail('t', 'same')['sections'][0]['text'], 'old body')
        new, _ = self.write('t', 'replacement', [('same', 'new body')])
        writer.execute('UPDATE threads SET rollout_path=? WHERE id=?', (str(new), 't')); writer.commit()
        self.assertTrue((self.home/'state_5.sqlite-wal').is_file())
        self.assertEqual(self.detail('t', 'same')['sections'][0]['text'], 'new body')

    def test_positive_current_content_restores_membership_even_with_a_missing_ancestor(self):
        old, _ = self.write('t', 't', [('same', 'old body')]); self.select('t', old)
        first = self.detail('t', 'same')
        book = TimelineBook(); book._put(first['phase']); self.query.book = book
        empty, _ = self.write('t', 'empty', []); self.select('t', empty)
        self.assertFalse(self.query.bundle(str(self.home), 't', 'same')['phases'])
        missing = {'thread_id': 'missing', 'end_ordinal_exclusive': 4, 'end_byte_offset': 999}
        restored, _ = self.write('t', 'restored', [('same', 'restored body')], base=missing)
        self.select('t', restored)
        result = self.detail('t', 'same')
        self.assertEqual(result['state'], 'available')
        self.assertEqual(result['sections'][0]['text'], 'restored body')

    def test_lineage_resolution_deadline_is_separate_from_the_length_limit(self):
        selected, _ = self.write('t', 't', [('own', 'own body')]); self.select('t', selected)
        self.provider.resolver.max_lineage_seconds = 0
        self.assertFalse(self.detail('t', 'own')['sections'])

    def test_unresolved_profile_sqlite_configuration_is_not_guessed(self):
        old, _ = self.write('t', 't', [('same', 'old body')]); self.select('t', old)
        new, _ = self.write('t', 'replacement', [('same', 'new body')])
        (self.home/'config.toml').write_text('[profiles.other]\nsqlite_home = '+json.dumps(str(self.home/'other-state'))+'\n')
        self.assertFalse(self.detail('t', 'same')['sections'])
        self.provider.configure_source(str(self.home), 't', 1, (('path', str(new)), ('history_mode', 'paginated')))
        self.assertEqual(self.detail('t', 'same')['sections'][0]['text'], 'new body')
        self.assertFalse(self.provider.index(str(self.home), 't').read_complete)


if __name__ == '__main__': unittest.main()
