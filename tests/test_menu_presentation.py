from collections import OrderedDict
import unittest

from codex_progress_monitor.engine import Activity, Engine
from codex_progress_monitor.metrics import TextStream


class MenuPresentationTests(unittest.TestCase):
    def setUp(self):
        self.engine = Engine(asynchronous_counts=False)
        self.addCleanup(self.engine.close)

    def thread(self, name='t', backend='b', home='/h'):
        self.engine.backends[backend] = {'connected': True, 'home': home}
        thread = self.engine._thread(backend, name)
        thread.status, thread.turn_id = 'inProgress', 'turn'
        return thread

    def activity(self, thread, kind, name='a', **extra):
        activity = Activity(name, 'turn', kind, '', 100, **extra)
        thread.activities[name] = activity
        return activity

    def menu(self, now=103):
        return self.engine.snapshot(now)['menu_presentation']

    def test_thinking_execution_and_generation_priority(self):
        thread = self.thread()
        self.activity(thread, 'reasoning')
        self.assertEqual(self.menu()['mode'], 'thinking')
        self.activity(thread, 'tool', 'exec')
        self.assertEqual(self.menu()['mode'], 'executing')
        self.activity(thread, 'tool_input', 'input')
        self.assertEqual(self.menu()['reason'], 'generating')

    def test_active_generation_does_not_depend_on_positive_or_rounded_speed(self):
        thread = self.thread()
        self.activity(thread, 'text')
        self.assertEqual(self.menu()['mode'], 'speed')
        self.assertEqual(self.menu(110)['reason'], 'generating')

    def test_summary_is_generation_only_while_stream_is_live(self):
        thread = self.thread()
        reasoning = self.activity(thread, 'reasoning')
        self.assertEqual(self.menu()['mode'], 'thinking')
        key = ('b', 'a', 'summary', 0)
        stream = TextStream(key, 'summary', 'upstream', 'unknown', 100)
        stream.append('sample', 102)
        stream.apply_count(3, stream.revision, 102, 0)
        self.engine.streams[key] = stream
        reasoning.stream_keys.append(key)
        self.assertEqual(self.menu()['reason'], 'generating')
        stream.ended_at = 102.5
        self.assertEqual(self.menu()['mode'], 'thinking')

    def test_finished_generation_cannot_mask_execution(self):
        thread = self.thread()
        self.activity(thread, 'text').ended_at = 102
        self.activity(thread, 'tool', 'exec')
        self.assertEqual(self.menu()['mode'], 'executing')

    def test_preparing_startup_review_and_wait_are_not_execution(self):
        for kind in ('tool_ready', 'review', 'auth', 'retry', 'sleep', 'compaction'):
            thread = self.thread()
            thread.activities.clear()
            self.activity(thread, kind)
            self.assertEqual(self.menu()['mode'], 'speed', kind)
        thread.activities.clear()
        self.activity(thread, 'tool', startup_pending=True)
        self.assertEqual(self.menu()['mode'], 'speed')

    def test_approval_blocks_target_but_not_independent_background_work(self):
        thread = self.thread()
        self.activity(thread, 'tool', 'exec')
        self.activity(thread, 'approval', 'approval', target_item_id='exec')
        self.assertEqual(self.menu()['reason'], 'paused')
        self.activity(thread, 'tool', 'background', turn_bound=False)
        self.assertEqual(self.menu()['mode'], 'executing')

    def test_multiple_roots_show_speed_even_when_all_thinking(self):
        self.activity(self.thread(), 'reasoning')
        self.activity(self.thread('other'), 'reasoning')
        self.assertEqual(self.menu()['mode'], 'speed')
        self.assertEqual(self.menu()['active_conversation_count'], 2)
        self.assertEqual(self.menu()['reason'], 'multi_conversation')

    def test_children_group_under_completed_hidden_parent(self):
        parent = self.thread('parent')
        parent.status, parent.visible = 'completed', False
        parent.subagents = {'child': 'running', 'second': 'running'}
        self.activity(self.thread('child'), 'reasoning')
        self.activity(self.thread('second'), 'tool')
        value = self.menu()
        self.assertEqual(value['active_conversation_count'], 1)
        self.assertEqual(value['mode'], 'executing')
        self.assertEqual(self.engine.snapshot(103)['overview']['active_thread_count'], 2)

    def test_missing_cyclic_ambiguous_and_cross_scope_parents_stay_independent(self):
        a, b = self.thread('a'), self.thread('b')
        a.subagents, b.subagents = {'b': 'running'}, {'a': 'running'}
        self.assertEqual(self.menu()['active_conversation_count'], 2)
        a.subagents, b.subagents = {'c': 'running'}, {'c': 'running'}
        self.thread('c')
        self.assertEqual(self.menu()['active_conversation_count'], 3)
        self.engine.threads = OrderedDict()
        a = self.thread('a'); a.subagents = {'b': 'running'}
        self.thread('b', backend='other', home='/other')
        self.assertEqual(self.menu()['active_conversation_count'], 2)

    def test_partial_gap_and_disconnect_stop_animations(self):
        thread = self.thread()
        self.activity(thread, 'reasoning')
        thread.quality.add('observation_gap')
        self.assertEqual(self.menu()['mode'], 'unavailable')
        thread.quality.clear()
        self.engine.backends['b']['connected'] = False
        self.assertEqual(self.menu()['mode'], 'unavailable')

    def test_idle_and_terminal_states_are_static(self):
        thread = self.thread()
        self.activity(thread, 'reasoning').ended_at = 102
        thread.status = 'completed'
        self.assertEqual(self.menu()['reason'], 'idle')
        self.assertEqual(self.menu()['active_conversation_count'], 0)

    def test_mirrored_work_cannot_start_animation(self):
        thread = self.thread()
        self.activity(thread, 'tool', mirrored=True)
        self.assertEqual(self.menu()['mode'], 'speed')
