"""A reading cursor identifies one immutable section prefix across processes."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from codex_activity_monitor.history import RolloutIndex
from codex_activity_monitor.timeline import TimelineBook, item_phase
from codex_activity_monitor.timeline_query import TimelineQuery
from codex_activity_monitor.content_version import SectionVersion, paginate, page_request


class ContentPagingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='cam-page-', dir='/tmp')
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name)
        sessions = self.home/'sessions'; sessions.mkdir()
        self.path = sessions/'synthetic-t.jsonl'
        self.scope = str(self.home)
        self.body = 'raw\r\n中文🙂 e\u0301 / é\n' * 3
        self.rows = [{'type': 'session_meta', 'payload': {'id': 't', 'history_mode': 'paginated'}},
                     {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'u'}}]
        self.append_item(self.body)

    def completed(self, body):
        return {'type': 'event_msg', 'payload': {'type': 'item_completed', 'thread_id': 't', 'turn_id': 'u',
                'item': {'id': 'a', 'type': 'AgentMessage', 'text': body, 'status': 'completed'}}}

    def write(self):
        self.path.write_text(''.join(json.dumps(row, ensure_ascii=False)+'\n' for row in self.rows))

    def append_item(self, body):
        self.rows.append(self.completed(body)); self.write()

    def phase(self, query):
        return next(p for p in query.bundle(self.scope, 't', 'u')['phases'] if p['item_id'] == 'a')

    def first(self, query=None):
        query = query or TimelineQuery()
        phase = self.phase(query)
        result = query.detail(self.scope, 't', 'u', phase['id'], limit=8)
        self.assertEqual(result['version'], 2)
        self.assertIsInstance(result['sections'][0]['page_token'], str)
        return phase, result['sections'][0]

    def more(self, query, phase, section, **changes):
        return query.detail(self.scope, 't', 'u', phase['id'], section['id'], section['next_offset'],
                            page_token=section['page_token'], **changes)

    def test_stable_prefix_token_survives_new_provider_and_cli_process(self):
        phase, section = self.first()
        _, same = self.first(TimelineQuery())
        self.assertEqual(section['page_token'], same['page_token'])
        request = {'action': 'detail', 'scope_id': self.scope, 'thread_id': 't', 'turn_id': 'u',
                   'phase_id': phase['id'], 'section_id': section['id'], 'offset': section['next_offset'],
                   'page_token': section['page_token']}
        result = subprocess.run([sys.executable, '-m', 'codex_activity_monitor.cli', '--runtime',
            str(self.home/'absent-runtime'), 'timeline-query', json.dumps(request)], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        page = json.loads(result.stdout)
        self.assertEqual(page['version'], 2)
        self.assertEqual(section['text']+page['sections'][0]['text'], self.body)
        self.assertFalse((self.home/'absent-runtime').exists())

    def test_unrelated_append_does_not_change_section_version(self):
        query = TimelineQuery(); phase, section = self.first(query)
        self.rows.append({'type': 'event_msg', 'payload': {'type': 'unrelated', 'turn_id': 'other'}})
        self.write()
        page = self.more(query, phase, section)
        self.assertEqual(section['page_token'], page['sections'][0]['page_token'])
        self.assertEqual(section['text']+page['sections'][0]['text'], self.body)

    def test_current_append_can_finish_only_the_original_snapshot_length(self):
        query = TimelineQuery(); phase, section = self.first(query)
        self.append_item(self.body+'NEW TAIL')
        page = self.more(query, phase, section)['sections'][0]
        self.assertEqual(page['total_chars'], len(self.body))
        self.assertIsNone(page['next_offset'])
        self.assertEqual(section['text']+page['text'], self.body)
        _, refreshed = self.first(query)
        self.assertNotEqual(section['page_token'], refreshed['page_token'])

    def test_rewrite_in_unread_prefix_and_truncation_stop_paging(self):
        for body in (self.body[:12]+'X'+self.body[13:], 'short'):
            with self.subTest(body=body[:16]):
                self.rows = self.rows[:2]+[self.completed(self.body)]; self.write()
                query = TimelineQuery(); phase, section = self.first(query)
                self.append_item(body)
                result = self.more(query, phase, section)
                self.assertEqual(result['state'], 'content_changed')
                self.assertEqual(result['sections'], [])

    def test_equal_content_atomic_replacement_preserves_token(self):
        query = TimelineQuery(); phase, section = self.first(query)
        replacement = self.path.with_suffix('.replacement')
        replacement.write_bytes(self.path.read_bytes()); replacement.replace(self.path)
        result = self.more(query, phase, section)
        self.assertEqual(result['sections'][0]['page_token'], section['page_token'])

    def test_source_handoff_requires_reload_even_when_text_is_identical(self):
        self.path.unlink()
        book = TimelineBook()
        item = {'id': 'raw', 'type': 'custom_tool_call', 'name': 'exec', 'input': self.body}
        phase = item_phase(self.scope, 't', 'u', item)
        book._put(phase); book.live[phase['id']] = {'text': self.body}
        query = TimelineQuery(book)
        initial = query.detail(self.scope, 't', 'u', phase['id'], limit=8)
        self.assertEqual(initial['version'], 2)
        section = initial['sections'][0]
        self.rows = self.rows[:2]+[{'type': 'response_item', 'payload': item}]; self.write()
        # The prior provider cached the missing path, so use a fresh provider.
        result = self.more(TimelineQuery(book), phase, section)
        self.assertEqual(result['state'], 'content_changed')
        self.assertEqual(result['sections'], [])

    def test_missing_source_retains_an_explicit_unavailable_response(self):
        query = TimelineQuery(); phase, section = self.first(query)
        book = TimelineBook(); book._put(phase); query.book = book
        self.path.unlink()
        result = self.more(query, phase, section)
        self.assertEqual(result['state'], 'missing')
        self.assertEqual(result['sections'], [])

    def test_cursor_requires_token_and_matching_full_identity_before_io(self):
        query = TimelineQuery(); phase, section = self.first(query)
        valid = {'action': 'detail', 'scope_id': self.scope, 'thread_id': 't', 'turn_id': 'u',
                 'phase_id': phase['id'], 'section_id': section['id'], 'offset': section['next_offset'],
                 'page_token': section['page_token']}
        for change in ({'page_token': None}, {'page_token': 'x'*20_000}, {'page_token': 'malformed'},
                       {'scope_id': self.scope+'/other'}, {'thread_id': 'other'}, {'turn_id': 'other'},
                       {'phase_id': 'other'}, {'section_id': 'other'}, {'offset': 2**63-1}):
            with self.subTest(change=change), patch.object(query.history, 'detail_view', side_effect=AssertionError('unexpected read')):
                with self.assertRaises(ValueError): query.query({**valid, **change})

    def test_invalid_cursor_is_rejected_before_the_provider_lock(self):
        class NeverRead:
            def __enter__(self): raise AssertionError('invalid input waited for a source reader')
            def __exit__(self, *_): pass
        query = TimelineQuery()
        with patch.object(query.history, 'lock', NeverRead()):
            with self.assertRaises(ValueError):
                query.query({'action': 'detail', 'scope_id': self.scope, 'thread_id': 't', 'turn_id': 'u',
                             'phase_id': 'phase', 'section_id': 'input', 'offset': 3, 'page_token': 'bad'})

    def test_unicode_and_line_endings_are_not_normalized(self):
        tokens = []
        for body in ('é\r\nbody', 'e\u0301\r\nbody', 'é\nbody'):
            self.rows = self.rows[:2]+[self.completed(body)]; self.write()
            _, section = self.first()
            tokens.append(section['page_token'])
        self.assertEqual(len(set(tokens)), 3)

    def test_shared_cross_language_fixture_uses_the_same_section_prefix(self):
        fixture = json.loads((Path(__file__).parent/'fixtures/detail-protocol-v2.json').read_text())
        first = fixture['pages'][0]
        section = {**first['sections'][0], 'text': fixture['text']}
        phase = first['phase']
        for expected in fixture['pages']:
            part = expected['sections'][0]
            version = page_request('scope', 'thread', 'turn', 'phase', 'input', part['offset'], part['page_token'])
            actual = paginate({**first, 'sections': [section]}, phase, 'input', part['offset'], 3, version)
            self.assertEqual(actual, expected)

    def test_changed_digest_is_not_an_authority_to_read_a_new_version(self):
        query = TimelineQuery(); phase, section = self.first(query)
        version = SectionVersion.parse(section['page_token'])
        forged = SectionVersion(version.identity, version.snapshot_chars, version.metadata, '0'*64, version.token)
        with patch('codex_activity_monitor.content_version.SectionVersion.parse', return_value=forged):
            result = self.more(query, phase, section)
        self.assertEqual(result['state'], 'content_changed')
        self.assertFalse(result['sections'])

    def test_hot_page_reads_selected_records_not_the_whole_rollout(self):
        # Unrelated large rows stay outside the selected detail read.
        self.rows += [{'type': 'event_msg', 'payload': {'type': 'unrelated', 'body': 'x'*65536}} for _ in range(32)]
        self.write()
        query = TimelineQuery(); phase, section = self.first(query)
        original = Path.open
        reads = []
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
            stream = original(path, *args, **kwargs)
            return Measured(stream) if path == self.path and args == ('rb',) else stream
        with patch.object(Path, 'open', measured):
            page = self.more(query, phase, section)
        self.assertEqual(section['text']+page['sections'][0]['text'], self.body)
        self.assertLess(sum(reads), 4096)
        self.assertGreater(self.path.stat().st_size, 2_000_000)

    def test_same_inode_mutation_between_input_and_output_reads_never_mixes_content(self):
        self.rows = self.rows[:2]+[
            {'type': 'response_item', 'payload': {'id': 'raw', 'type': 'function_call', 'call_id': 'call',
                                                'name': 'exec', 'arguments': 'old-input'}},
            {'type': 'response_item', 'payload': {'type': 'function_call_output', 'call_id': 'call', 'output': 'old-output'}}]
        self.write()
        query = TimelineQuery(); phase = query.bundle(self.scope, 't', 'u')['phases'][0]
        original = RolloutIndex.read_ref
        mutated = False
        def mutate(index, ref, *args, **kwargs):
            nonlocal mutated
            result = original(index, ref, *args, **kwargs)
            if not mutated:
                mutated = True
                self.rows[2]['payload']['arguments'] = 'new-input'
                self.rows[3]['payload']['output'] = 'new-output'
                before = self.path.stat(); self.write()
                os.utime(self.path, ns=(before.st_atime_ns, before.st_mtime_ns+1_000_000))
            return result
        with patch.object(RolloutIndex, 'read_ref', mutate):
            result = query.detail(self.scope, 't', 'u', phase['id'])
        texts = [s['text'] for s in result['sections']]
        self.assertNotEqual(texts, ['old-input', 'new-output'])

    def test_same_call_id_in_another_turn_cannot_supply_the_output(self):
        self.rows = self.rows[:2]+[
            {'type': 'response_item', 'payload': {'id': 'raw', 'type': 'function_call', 'call_id': 'shared',
                                                'name': 'exec', 'arguments': 'request'}},
            {'type': 'response_item', 'payload': {'type': 'function_call_output', 'call_id': 'shared', 'output': 'turn-u'}},
            {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'v'}},
            {'type': 'response_item', 'payload': {'type': 'function_call_output', 'call_id': 'shared', 'output': 'turn-v'}}]
        self.write()
        query = TimelineQuery(); phase = query.bundle(self.scope, 't', 'u')['phases'][0]
        result = query.detail(self.scope, 't', 'u', phase['id'])
        self.assertEqual(result['sections'][1]['text'], 'turn-u')

    def test_rewritten_raw_turn_context_cannot_leave_a_stale_attribution(self):
        self.rows = self.rows[:2]+[
            {'type': 'response_item', 'payload': {'id': 'raw', 'type': 'function_call', 'call_id': 'call',
                                                'name': 'exec', 'arguments': 'raw text'}}]
        self.write()
        query = TimelineQuery(); phase = query.bundle(self.scope, 't', 'u')['phases'][0]
        # Rewrite earlier context and append, leaving the indexed tail unchanged.
        self.rows[1]['payload']['turn_id'] = 'v'
        self.rows.append({'type': 'event_msg', 'payload': {'type': 'unrelated'}})
        self.write()
        result = query.detail(self.scope, 't', 'u', phase['id'])
        self.assertFalse(result['sections'])
