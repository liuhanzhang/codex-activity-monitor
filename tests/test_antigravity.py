"""Sanitized fixtures for the desktop 2.32.1 gRPC-Web / transcript boundary."""

import json
from pathlib import Path
import queue
from tempfile import TemporaryDirectory
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from codex_activity_monitor.antigravity_history import transcript_path
from codex_activity_monitor.antigravity_projection import AntigravityProjection
from codex_activity_monitor.antigravity_records import live_step, step_id, turn_id
from codex_activity_monitor.antigravity_source import AntigravitySource
from codex_activity_monitor.antigravity_tokenizer import encoding_for_model
from codex_activity_monitor.app_control import AppController
from codex_activity_monitor.engine import Engine
from codex_activity_monitor.history import HistoryProvider
from codex_activity_monitor.identity_budget import IdentityBudget
from codex_activity_monitor.timeline import TimelineBook
from codex_activity_monitor.timeline_query import TimelineQuery


ROOT = '01234567-89ab-4def-8000-012345678900'
CHILD = '01234567-89ab-4def-8000-012345678901'
OTHER = '01234567-89ab-4def-8000-012345678902'
WHEN = 1700000000


def step(kind, state, **fields):
    return {'type': 'CORTEX_STEP_TYPE_'+kind, 'status': 'CORTEX_STEP_STATUS_'+state, **fields}


def update(owner, status, steps):
    return {'update': {'conversationId': owner, 'trajectoryId': owner, 'status': 'CASCADE_RUN_STATUS_'+status,
                       'fullyIdle': status == 'IDLE', 'mainTrajectoryUpdate': {'stepsUpdate': {
                           'indices': list(steps), 'steps': list(steps.values()),
                       }}}}


class AntigravityProjectionTests(unittest.TestCase):
    def setUp(self):
        root = TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.scope = str(Path(root.name)/'antigravity')
        (Path(self.scope)/'brain').mkdir(parents=True)
        self.book = TimelineBook()
        self.engine = Engine(asynchronous_counts=False, timeline=self.book)
        self.addCleanup(self.engine.close)
        self.monitor = AntigravityProjection(self.engine)
        self.thread_key = (self.monitor.backend(self.scope), ROOT)
        self.monitor.directory(self.scope, {ROOT: {'annotations': {'title': 'A synthetic chat'}}}, [],
                               at=1, wall=WHEN+1)

    def push(self, steps, at, status='RUNNING', initial=False, owner=ROOT):
        self.monitor.frame(self.scope, owner, update(owner, status, steps), initial, at=at, wall=WHEN+at)

    def test_cumulative_body_and_reconnect_do_not_count_baseline_or_duplicates(self):
        self.push({0: step('USER_INPUT', 'DONE')}, 1)
        self.push({1: step('PLANNER_RESPONSE', 'GENERATING', plannerResponse={'response': 'One'})}, 2)
        self.push({1: step('PLANNER_RESPONSE', 'GENERATING', plannerResponse={'response': 'One'})}, 2.25)
        self.push({1: step('PLANNER_RESPONSE', 'GENERATING', plannerResponse={'response': 'One two'})}, 2.5)
        stream, = self.engine.streams.values()
        self.engine.tick(2.5)
        self.assertEqual((stream.text, stream.deltas, stream.rate(2.5)), ('One two', 2, 2))
        self.monitor.gap(self.scope, ROOT, at=3, wall=WHEN+3)
        self.assertIsNone(stream.rate(3))
        self.push({0: step('USER_INPUT', 'DONE'), 1: step('PLANNER_RESPONSE', 'GENERATING',
                                                         plannerResponse={'response': 'One two three four'})},
                  4, initial=True)
        self.push({1: step('PLANNER_RESPONSE', 'GENERATING',
                           plannerResponse={'response': 'One two three four five'})}, 5.2)
        self.engine.tick(5.2)
        self.assertEqual(stream.rate(5.2), 1)
        self.assertFalse(stream.received_complete)
        self.assertTrue(stream.estimated)
        self.push({1: step('PLANNER_RESPONSE', 'GENERATING', plannerResponse={'response': 'Replacement'})}, 5.5)
        self.engine.tick(5.5)
        self.assertIsNone(stream.rate(5.5))
        self.assertEqual(stream.text, 'Replacement')

    def test_completed_tool_and_usage_reconcile_with_generic_transcript(self):
        self.push({0: step('USER_INPUT', 'DONE')}, 1)
        self.push({1: step('GENERIC', 'RUNNING', metadata={'toolCall': {'id': 'synthetic', 'name': 'run_command'}})}, 2)
        self.push({1: step('RUN_COMMAND', 'DONE',
                           runCommand={'commandLine': 'printf test', 'combinedOutput': {'full': 'test'}, 'exitCode': 0}),
                   2: step('PLANNER_RESPONSE', 'DONE', plannerResponse={'response': 'Finished.'},
                           metadata={'createdAt': WHEN+2, 'finishedGeneratingAt': WHEN+4,
                                     'modelUsage': {'inputTokens': '80', 'outputTokens': '31',
                                                    'responseOutputTokens': '7', 'thinkingOutputTokens': '24'}})},
                  4, status='IDLE')
        path = transcript_path(self.scope, ROOT)
        path.parent.mkdir(parents=True)
        rows = [
            {'step_index': 0, 'type': 'USER_INPUT', 'status': 'DONE', 'content': 'Synthetic command', 'created_at': WHEN+1},
            {'step_index': 1, 'type': 'GENERIC', 'status': 'DONE', 'content': 'Test', 'created_at': WHEN+2},
            {'step_index': 2, 'type': 'PLANNER_RESPONSE', 'status': 'DONE', 'content': 'Finished.',
             'output_tokens': 31, 'created_at': WHEN+3},
        ]
        path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        history = HistoryProvider()
        history_summary = history.reconciliation(self.scope, ROOT, {turn_id(ROOT, 0)})
        self.monitor.reconcile(history_summary, 5)
        self.monitor.reconcile(history_summary, 6)
        metrics = self.engine.usage.view((self.scope, ROOT), turn_id(ROOT, 0))
        self.assertEqual((metrics['output_tokens'], metrics['visible_output_tokens'], metrics['reasoning_tokens']), (31, 7, 24))
        query = TimelineQuery(self.book, history=history)
        timeline = query.bundle(self.scope, ROOT)
        command = next(phase for phase in timeline['phases'] if phase['track'] == 'tool')
        self.assertEqual((command['item_id'], command['item_type'], command['exit_code']),
                         (step_id(ROOT, 1), 'commandExecution', 0))
        self.assertEqual(timeline['average_speed']['tokens_per_second'], 15.5)
        detail = query.detail(self.scope, ROOT, turn_id(ROOT, 0), command['id'])
        self.assertEqual(detail['source'], 'antigravity_history')

    def test_tool_generation_finishes_before_execution_and_survives_history_merge(self):
        metadata = {'source': 'CORTEX_STEP_SOURCE_MODEL', 'createdAt': WHEN+2,
                    'finishedGeneratingAt': WHEN+2.25, 'toolCall': {'name': 'run_command'}}
        self.push({0: step('USER_INPUT', 'DONE')}, 1)
        self.push({1: step('GENERIC', 'PENDING', metadata=metadata,
                           generic={'args': {'CommandLine': '/usr/bin/false'}})}, 2.3)
        generated = next(p for p in self.book.phases.values() if p['kind'] == 'tool_input')
        execution = next(p for p in self.book.phases.values() if p['kind'] == 'tool_execution')
        self.assertEqual((generated['start_ms'], generated['end_ms'], generated['status']),
                         ((WHEN+2)*1000, (WHEN+2.25)*1000, 'completed'))
        self.assertIsNone(execution['start_ms'])
        self.assertEqual(self.book.live_detail(generated)['text'], '/usr/bin/false')
        self.assertFalse(self.engine.streams)  # The first available arguments are already complete.

        metadata = {**metadata, 'startedAt': WHEN+3.5, 'completedAt': WHEN+4.25}
        self.push({1: step('RUN_COMMAND', 'DONE', metadata=metadata, runCommand={'exitCode': 1}),
                   2: step('PLANNER_RESPONSE', 'DONE', plannerResponse={'response': 'Finished.'})}, 5, status='IDLE')
        path = transcript_path(self.scope, ROOT)
        path.parent.mkdir(parents=True)
        path.write_text(''.join(json.dumps(row)+'\n' for row in (
            {'step_index': 0, 'type': 'USER_INPUT', 'status': 'DONE', 'created_at': WHEN+1},
            {'step_index': 1, 'type': 'GENERIC', 'status': 'DONE', 'created_at': WHEN+2,
             'started_at': WHEN+3, 'completed_at': WHEN+4},
            {'step_index': 2, 'type': 'PLANNER_RESPONSE', 'status': 'DONE', 'content': 'Finished.',
             'created_at': WHEN+4, 'completed_at': WHEN+5},
        )))
        history = HistoryProvider()
        turn = turn_id(ROOT, 0)
        summary = history.reconciliation(self.scope, ROOT, {turn})
        self.book.apply_history(summary)
        self.book.apply_history(summary)
        bundle = TimelineQuery(self.book, history=history).bundle(self.scope, ROOT, turn)
        phases = {p['id']: p for p in bundle['phases']}
        self.assertEqual(phases[generated['id']]['related_phase_ids'], [execution['id']])
        command = phases[execution['id']]
        self.assertEqual((command['start_ms'], command['end_ms'], command['status'], command['exit_code']),
                         ((WHEN+3.5)*1000, (WHEN+4.25)*1000, 'failed', 1))
        self.assertNotIn('lifecycle_conflict', command['quality'])

    def test_older_turn_gets_precise_tool_phases_and_input_detail_from_local_trajectory(self):
        path = transcript_path(self.scope, ROOT)
        path.parent.mkdir(parents=True)
        path.write_text(''.join(json.dumps(row)+'\n' for row in (
            {'step_index': 0, 'type': 'USER_INPUT', 'status': 'DONE', 'created_at': WHEN+1},
            {'step_index': 1, 'type': 'GENERIC', 'status': 'DONE', 'created_at': WHEN+2},
            {'step_index': 2, 'type': 'PLANNER_RESPONSE', 'status': 'DONE', 'content': 'Finished.',
             'created_at': WHEN+4, 'completed_at': WHEN+5},
        )))
        source = [
            step('USER_INPUT', 'DONE', metadata={'createdAt': WHEN+1}),
            step('RUN_COMMAND', 'DONE', metadata={'source': 'CORTEX_STEP_SOURCE_MODEL',
                 'createdAt': WHEN+2, 'finishedGeneratingAt': WHEN+2.5, 'startedAt': WHEN+3,
                 'completedAt': WHEN+3.5},
                 runCommand={'commandLine': 'printf sample', 'combinedOutput': 'sample', 'exitCode': 1}),
            step('PLANNER_RESPONSE', 'DONE', plannerResponse={'response': 'Finished.'}),
        ]
        calls = []

        def read_steps(scope, thread, index):
            calls.append((scope, thread, index))
            return source[index:]

        turn = turn_id(ROOT, 0)
        history = HistoryProvider()
        query = TimelineQuery(history=history)
        history_only = query.bundle(self.scope, ROOT, turn)
        orange = next(p for p in history_only['phases'] if p['track'] == 'tool')
        self.assertIsNone(orange['start_ms'])  # created_at is the tool step's creation time.
        self.assertFalse(any(p['kind'] == 'tool_input' for p in history_only['phases']))

        query = TimelineQuery(history=history, read_antigravity_steps=read_steps)
        timeline = query.bundle(self.scope, ROOT, turn)
        query.bundle(self.scope, ROOT, turn)
        self.assertEqual(calls, [(self.scope, ROOT, 0)])
        orange = next(p for p in timeline['phases'] if p['track'] == 'tool')
        blue = next(p for p in timeline['phases'] if p['kind'] == 'tool_input')
        self.assertEqual((orange['start_ms'], orange['end_ms'], orange['status']),
                         ((WHEN+3)*1000, (WHEN+3.5)*1000, 'failed'))
        self.assertEqual((blue['start_ms'], blue['end_ms'], blue['related_phase_ids']),
                         ((WHEN+2)*1000, (WHEN+2.5)*1000, [orange['id']]))
        detail = query.query({'action': 'detail', 'scope_id': self.scope, 'thread_id': ROOT,
                              'turn_id': turn, 'phase_id': blue['id']})
        self.assertEqual(detail['source'], 'antigravity_rpc')
        arg, = detail['sections']
        self.assertEqual((arg['id'], arg['text']), ('input', 'printf sample'))

    def test_completed_turn_recovers_model_intervals_and_average_after_live_data_is_pruned(self):
        record = live_step(1, step('PLANNER_RESPONSE', 'DONE', plannerResponse={'thinkingDuration': '2.250s'},
                                  metadata={'createdAt': WHEN+1, 'finishedGeneratingAt': WHEN+4}))
        self.assertEqual(list(record.parts()), ['summary'])
        invalid = live_step(1, step('PLANNER_RESPONSE', 'DONE', plannerResponse={'thinkingDuration': '4s'},
                                   metadata={'createdAt': WHEN+1, 'finishedGeneratingAt': WHEN+4}))
        self.assertIsNone(invalid.thinking_ended)
        path = transcript_path(self.scope, ROOT)
        path.parent.mkdir(parents=True)
        path.write_text(''.join(json.dumps(row)+'\n' for row in (
            {'step_index': 0, 'type': 'USER_INPUT', 'status': 'DONE', 'created_at': WHEN},
            {'step_index': 1, 'type': 'GENERIC', 'status': 'RUNNING', 'created_at': WHEN+1},
            {'step_index': 2, 'type': 'PLANNER_RESPONSE', 'status': 'DONE', 'content': 'Earlier.',
             'created_at': WHEN+2},
            {'step_index': 3, 'type': 'USER_INPUT', 'status': 'DONE', 'created_at': WHEN+5},
            {'step_index': 4, 'type': 'PLANNER_RESPONSE', 'status': 'DONE', 'thinking': 'Tool summary.',
             'tool_calls': [{'name': 'run_command'}], 'output_tokens': 30, 'created_at': WHEN+6},
            {'step_index': 5, 'type': 'GENERIC', 'status': 'DONE', 'created_at': WHEN+8},
            {'step_index': 6, 'type': 'PLANNER_RESPONSE', 'status': 'DONE', 'thinking': 'Reply summary.',
             'content': 'Finished.', 'output_tokens': 30, 'created_at': WHEN+10},
        )))
        source = [
            step('USER_INPUT', 'DONE', metadata={'createdAt': WHEN+5.25}),
            step('PLANNER_RESPONSE', 'DONE', plannerResponse={'thinking': 'Tool summary.', 'thinkingDuration': '2s'},
                 metadata={'createdAt': WHEN+6, 'finishedGeneratingAt': WHEN+9, 'modelUsage': {'outputTokens': 30}}),
            step('RUN_COMMAND', 'DONE', metadata={'source': 'CORTEX_STEP_SOURCE_MODEL', 'createdAt': WHEN+8,
                 'finishedGeneratingAt': WHEN+9, 'startedAt': WHEN+9.125, 'completedAt': WHEN+9.5}),
            step('PLANNER_RESPONSE', 'DONE', plannerResponse={'thinking': 'Reply summary.', 'response': 'Finished.',
                 'thinkingDuration': '1.250s'}, metadata={'createdAt': WHEN+10, 'finishedGeneratingAt': WHEN+13,
                 'modelUsage': {'outputTokens': 30}}),
        ]

        def read_steps(scope, thread, index):
            self.assertGreaterEqual(index, 3)
            return source[index-3:]

        turn = turn_id(ROOT, 3)
        query = TimelineQuery(read_antigravity_steps=read_steps)
        timeline = query.bundle(self.scope, ROOT, turn)
        intervals = [(p['kind'], p['start_ms']/1000-WHEN, p['end_ms']/1000-WHEN) for p in timeline['phases']]
        self.assertEqual(intervals, [('reasoning', 6, 8), ('tool_input', 8, 9), ('tool_execution', 9.125, 9.5),
                                     ('reasoning', 10, 11.25), ('text', 11.25, 13)])
        self.assertEqual((timeline['origin_ms'], timeline['end_ms']), ((WHEN+5.25)*1000, (WHEN+13)*1000))
        self.assertEqual(timeline['earlier_active_turns'], [])
        average = timeline['average_speed']
        self.assertEqual((average['tokens_per_second'], average['sample_count'], average['coverage']),
                         (10, 2, 'complete'))

        reasoning = next(p for p in timeline['phases'] if p['step_index'] == 6 and p['kind'] == 'reasoning')
        # The previous build could cache request completion as the reasoning end.
        self.book._save('phase', {**reasoning, 'end_ms': (WHEN+13)*1000})
        upgraded = TimelineQuery(self.book, read_antigravity_steps=read_steps).bundle(self.scope, ROOT, turn)
        self.assertEqual(next(p for p in upgraded['phases'] if p['id'] == reasoning['id'])['end_ms'],
                         (WHEN+11.25)*1000)

    def test_terminal_reasoning_boundary_replaces_the_live_receipt_estimate(self):
        self.push({0: step('USER_INPUT', 'DONE')}, 1)
        self.push({1: step('PLANNER_RESPONSE', 'GENERATING', plannerResponse={'thinking': 'Summary.'})}, 2)
        self.push({1: step('PLANNER_RESPONSE', 'GENERATING', plannerResponse={'thinking': 'Summary.',
                                                                             'response': 'Text.'})}, 5)
        before = next(p for p in self.book.phases.values() if p['kind'] == 'reasoning')
        self.assertEqual(before['end_source'], 'receipt')
        self.push({1: step('PLANNER_RESPONSE', 'DONE', plannerResponse={'thinking': 'Summary.', 'response': 'Text.',
                                                                      'thinkingDuration': '2s'},
                           metadata={'createdAt': WHEN+1.5, 'finishedGeneratingAt': WHEN+6})}, 6, status='IDLE')
        after = self.book.phases[before['id']]
        self.assertEqual((after['start_ms'], after['end_ms'], after['end_source']),
                         ((WHEN+1.5)*1000, (WHEN+3.5)*1000, 'producer'))

    def test_subagent_relation_requires_the_explicit_invocation_result(self):
        self.monitor.directory(self.scope, {CHILD: {'trajectoryMetadata':
                               {'rootConversationId': ROOT, 'parentConversationId': ROOT}},
                               OTHER: {'trajectoryMetadata': {'rootConversationId': ROOT}}},
                               [], at=1, wall=WHEN+1)
        self.push({0: step('USER_INPUT', 'DONE')}, 1)
        self.push({1: step('INVOKE_SUBAGENT', 'DONE', invokeSubagent={'results': [{'conversationId': CHILD}]})}, 3)
        self.push({0: step('PLANNER_RESPONSE', 'GENERATING', plannerResponse={'response': 'Child work'})}, 4, owner=CHILD)
        parent = self.engine.threads[self.thread_key]
        self.assertEqual(parent.turn_id, turn_id(ROOT, 0))
        relation, = self.book.relations.values()
        self.assertEqual((relation['child_thread_id'], relation['parent_turn_id']), (CHILD, parent.turn_id))
        child = self.engine.threads[(parent.backend, CHILD)]
        self.assertEqual((child.parent_thread_id, child.turn_id), (ROOT, turn_id(CHILD, 0)))
        self.assertIsNone(self.engine.threads[(parent.backend, OTHER)].parent_thread_id)

    def test_failed_command_and_cancelled_turn_preserve_distinct_statuses(self):
        self.push({0: step('USER_INPUT', 'DONE')}, 1)
        self.push({1: step('GENERIC', 'PENDING', metadata={'toolCall': {'name': 'run_command'}})}, 2)
        activity = self.engine.threads[self.thread_key].activities[step_id(ROOT, 1)]
        self.assertEqual(activity.kind, 'tool_ready')
        self.push({1: step('RUN_COMMAND', 'DONE', runCommand={'exitCode': 7})}, 3)
        self.assertEqual((activity.status, activity.exit_code), ('failed', 7))
        self.push({}, 4, status='CANCELING')
        self.push({}, 5, status='IDLE')
        self.assertEqual(self.engine.threads[self.thread_key].status, 'interrupted')
        self.assertEqual(next(iter(self.book.turns.values()))['status'], 'interrupted')

    def test_view_file_details_do_not_read_or_fabricate_file_content(self):
        record = live_step(3, step('VIEW_FILE', 'DONE', viewFile={
            'absolutePathUri': 'file:///synthetic/note.txt', 'startLine': 3, 'endLine': 5,
            'lineRangeBytes': 12, 'numLines': 4,
        }))
        self.assertEqual(json.loads(record.arguments), {'absolutePathUri': 'file:///synthetic/note.txt',
                                                       'startLine': 3, 'endLine': 5})
        self.assertEqual(json.loads(record.output), {'numLines': 4, 'lineRangeBytes': 12})

    def test_bundle_trajectory_rpc_does_not_hold_history_lock(self):
        path = transcript_path(self.scope, ROOT)
        path.parent.mkdir(parents=True)
        path.write_text(''.join(json.dumps(row)+'\n' for row in (
            {'step_index': 0, 'type': 'USER_INPUT', 'status': 'DONE', 'created_at': WHEN+1},
            {'step_index': 1, 'type': 'PLANNER_RESPONSE', 'status': 'DONE', 'content': 'Done.',
             'created_at': WHEN+2, 'completed_at': WHEN+3},
        )))
        history = HistoryProvider()
        entered, release = threading.Event(), threading.Event()
        concurrent_acquired = []

        def slow_steps(scope, thread, index):
            entered.set()
            self.assertTrue(release.wait(2))
            return [
                step('USER_INPUT', 'DONE', metadata={'createdAt': WHEN+1}),
                step('PLANNER_RESPONSE', 'DONE', plannerResponse={'response': 'Done.'},
                     metadata={'createdAt': WHEN+2, 'finishedGeneratingAt': WHEN+3}),
            ]

        query = TimelineQuery(history=history, read_antigravity_steps=slow_steps)
        result_holder = []
        worker = threading.Thread(target=lambda: result_holder.append(
            query.query({'action': 'timeline', 'scope_id': self.scope, 'thread_id': ROOT})))
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            with history.lock:
                concurrent_acquired.append(True)
        finally:
            release.set()
            worker.join(2)
        self.assertEqual(concurrent_acquired, [True])
        self.assertEqual(result_holder[0]['selected_turn_id'], turn_id(ROOT, 0))

    def test_antigravity_history_is_detached_and_accounted_in_cache_budget(self):
        path = transcript_path(self.scope, ROOT)
        path.parent.mkdir(parents=True)
        path.write_text(''.join(json.dumps(row)+'\n' for row in (
            {'step_index': 0, 'type': 'USER_INPUT', 'status': 'DONE', 'created_at': WHEN+1},
            {'step_index': 1, 'type': 'PLANNER_RESPONSE', 'status': 'DONE', 'content': 'Done.',
             'output_tokens': 12, 'created_at': WHEN+2, 'completed_at': WHEN+3},
        )))
        history = HistoryProvider()
        first = history.index(self.scope, ROOT, directory_only=True)
        self.assertEqual(history.diagnostics()['indices'], 1)
        self.assertGreater(history.diagnostics()['cache_bytes'], 0)
        next(iter(first.phases.values()))['label'] = 'mutated'
        second = history.detail_view(self.scope, ROOT, turn_id(ROOT, 0), next(iter(first.phases)))
        self.assertNotEqual(next(iter(second.phases.values()))['label'], 'mutated')

        limited = HistoryProvider(max_cache_bytes=1)
        self.assertIsNotNone(limited.selected_view(self.scope, ROOT, turn_id(ROOT, 0)))
        self.assertEqual(limited.diagnostics()['indices'], 0)
        self.assertEqual(limited.diagnostics()['cache_bytes'], 0)
        self.assertGreaterEqual(limited.diagnostics()['evictions'], 1)

    def test_identity_budget_exhaustion_stops_antigravity_admission_and_marks_gap(self):
        self.engine.identity_budget = IdentityBudget(capacity=4)
        self.push({0: step('USER_INPUT', 'DONE')}, 1)
        self.push({1: step('PLANNER_RESPONSE', 'GENERATING', plannerResponse={'response': 'Unadmitted'})}, 2)
        self.assertTrue(self.engine.identity_budget.sealed)
        self.assertGreater(self.engine.counts['identity_budget_dropped'], 0)
        self.assertIn('identity_budget', self.engine.threads[self.thread_key].quality)
        self.assertIn('observation_gap', self.engine.threads[self.thread_key].quality)
        self.assertIn('identity_budget', self.engine.snapshot(2)['overview']['quality'])
        self.assertIsNone(self.engine.snapshot(2)['overview']['generation_rate'])

    def test_gemini_and_gemma_model_families_select_gemma4_when_cached(self):
        with TemporaryDirectory() as directory:
            cache_file = Path(directory)/'gemma-4-E4B-it-tokenizer.json'
            cache_file.write_text('{}')
            with patch('codex_activity_monitor.antigravity_tokenizer.cache_path', return_value=cache_file):
                self.assertEqual(encoding_for_model('Gemini 4 Argon (High)'), 'gemma4')
                self.assertEqual(encoding_for_model('Gemini 4 Hydrogen'), 'gemma4')
                self.assertEqual(encoding_for_model('gemini-2.5-pro'), 'gemma4')
                self.assertEqual(encoding_for_model('gemma-4-e4b-it'), 'gemma4')
                self.assertEqual(encoding_for_model('claude-3-7-sonnet'), 'o200k_base')


class PushStream:
    def __init__(self):
        self.messages = queue.Queue()

    def frames(self):
        while (value := self.messages.get()) is not None:
            yield value, 1

    def cancel(self):
        self.messages.put(None)


class SourceTests(unittest.TestCase):
    def test_directory_discovers_conversations_and_resubscribes_after_endpoint_change(self):
        def client(version):
            summary, chat = PushStream(), PushStream()
            alive = True

            def call(method, params):
                if not alive:
                    raise ConnectionError()
                if method == 'GetAllCascadeTrajectories':
                    return {'trajectorySummaries': {}}
                return {}

            def disconnect():
                nonlocal alive
                alive = False
                summary.cancel()
                chat.cancel()

            endpoint = SimpleNamespace(scope=Path('/synthetic/antigravity'), version=version)
            return SimpleNamespace(endpoint=endpoint, call=call, disconnect=disconnect, summary=summary, chat=chat,
                                   stream=lambda method, _: summary if method == 'JetboxSubscribeToSummaries' else chat)

        first, second = client('2.32.1'), client('2.32.2')
        selected = [first]
        source = AntigravitySource(lambda _: selected[0], apps=lambda: ['synthetic'], client_factory=lambda value: value)
        self.addCleanup(source.close)

        def wait(kind, predicate):
            deadline = time.monotonic()+6
            while time.monotonic() < deadline:
                for event, value, *_ in source.drain():
                    if event == kind and predicate(value):
                        return
                time.sleep(.02)
            self.fail('Expected source event '+kind)

        first.summary.messages.put({'updates': {ROOT: {'status': 'CASCADE_RUN_STATUS_RUNNING'}}})
        first.chat.messages.put(update(ROOT, 'RUNNING', {}))
        wait('frame', lambda value: value[1] == ROOT and value[3])
        selected[0] = second
        first.disconnect()
        wait('disconnect', lambda scope: scope == '/synthetic/antigravity')
        second.summary.messages.put({'updates': {ROOT: {'status': 'CASCADE_RUN_STATUS_RUNNING'}}})
        second.chat.messages.put(update(ROOT, 'RUNNING', {}))
        wait('frame', lambda value: value[1] == ROOT and value[3])

    def test_antigravity_can_be_enabled_without_codex_configuration(self):
        with TemporaryDirectory() as directory:
            control = AppController(Path(directory)/'Monitor')
            status = control.set_antigravity(True)
            self.assertTrue(status['antigravity_enabled'])
            self.assertFalse(status['configured'])
            self.assertFalse(control.enabled.exists())
            self.assertFalse(control.receipt_path.exists())
            self.assertFalse(control.set_antigravity(False)['antigravity_enabled'])
