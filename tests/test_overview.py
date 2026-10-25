from collections import OrderedDict
import unittest

from codex_progress_monitor.engine import Activity, Engine
from codex_progress_monitor.metrics import TextStream
from codex_progress_monitor.overview import annotate_parents


class OverviewTests(unittest.TestCase):
    def setUp(self):
        self.engine = Engine(asynchronous_counts=False)
        self.addCleanup(self.engine.close)

    def thread(self, name='t', backend='b', home='/home'):
        self.engine.backends[backend] = {'connected': True, 'home': home}
        thread = self.engine._thread(backend, name)
        thread.status, thread.turn_id = 'inProgress', 'turn'
        return thread

    def stream(self, thread, item='i', pending=False):
        key = (thread.backend, item, 'text', 0)
        stream = TextStream(key, 'text', 'upstream', 'unknown', 100)
        stream.apply_count(0, 0, 100, 0)
        stream.append(' x'*4, 102.5)
        if not pending:
            stream.apply_count(4, stream.revision, 103, 0)
        self.engine.streams[key] = stream
        activity = Activity(item, 'turn', 'text', '', 100, stream_keys=[key])
        thread.activities[item] = activity
        return stream

    def result(self):
        return self.engine.snapshot(103)['overview']

    def test_no_backend_is_unknown_but_known_idle_is_zero(self):
        self.assertIsNone(self.result()['generation_rate'])
        self.engine.backends['b'] = {'connected': True}
        self.assertEqual(self.result()['generation_rate'], 0)

    def test_distinct_streams_summed_once_despite_shared_activity_reference(self):
        t = self.thread()
        self.stream(t)
        self.stream(self.thread('child'), 'j')
        t.activities['alias'] = Activity('alias', 'turn', 'text', '', 100,
                                        stream_keys=[('b', 'i', 'text', 0)])
        self.assertEqual(self.result()['generation_rate'], 8)
        self.assertEqual(self.result()['known_stream_count'], 2)

    def test_invisible_thread_still_contributes(self):
        t = self.thread(); t.visible = False
        self.stream(t)
        self.assertEqual(self.result()['generation_rate'], 4)

    def test_ephemeral_streams_and_gaps_do_not_change_chat_totals(self):
        self.stream(self.thread())
        helper = self.thread('helper')
        helper.ephemeral = True
        self.stream(helper, 'helper-output', pending=True)
        helper.quality.add('observation_gap')
        value = self.result()
        self.assertEqual(value['coverage'], 'complete')
        self.assertEqual(value['generation_rate'], 4)
        self.assertEqual(value['active_thread_count'], 1)
        self.assertEqual(value['unknown_stream_count'], 0)
        # Raw observations remain available; scope only affects aggregation.
        self.assertEqual(len(self.engine.snapshot(103)['threads']), 2)
        helper.ephemeral = None
        self.assertEqual(self.result()['coverage'], 'partial')

    def test_partial_never_claims_known_subtotal_is_total(self):
        self.stream(self.thread())
        self.stream(self.thread('unknown'), 'j', pending=True)
        value = self.result()
        self.assertEqual(value['coverage'], 'partial')
        self.assertIsNone(value['generation_rate'])
        self.assertEqual(value['observed_subtotal'], 4)

    def test_mirrors_and_finished_streams_are_excluded(self):
        t = self.thread(); s = self.stream(t)
        t.activities['i'].mirrored = True
        self.assertEqual(self.result()['known_stream_count'], 0)
        t.activities['i'].mirrored = False
        s.ended_at = 102
        self.assertEqual(self.result()['known_stream_count'], 0)

    def test_duplicate_backend_is_not_added_and_conflict_is_unknown(self):
        self.stream(self.thread())
        self.stream(self.thread(backend='second'))
        value = self.result()
        self.assertIsNone(value['generation_rate'])
        self.assertIn('multiple_active_backends', value['quality'])

    def test_independent_homes_are_not_deduplicated(self):
        self.stream(self.thread())
        self.stream(self.thread(backend='other', home='/other'))
        self.assertEqual(self.result()['generation_rate'], 8)

    def test_gap_without_known_streams_is_unknown_until_new_turn(self):
        t = self.thread()
        t.quality.add('observation_gap')
        value = self.result()
        self.assertIsNone(value['generation_rate'])
        self.assertIn('observation_gap', value['quality'])
        t.quality.clear()
        self.assertEqual(self.result()['generation_rate'], 0)

    def test_parent_edges_reject_ambiguity_cycles_and_cross_home(self):
        views = [{'id': 'a', 'backend': 'b', 'home': '/h', 'subagents': {'b': 'spawnAgent'}},
                 {'id': 'b', 'backend': 'b', 'home': '/h', 'subagents': {'a': 'spawnAgent'}},
                 {'id': 'b', 'backend': 'x', 'home': '/other'}]
        annotate_parents(views)
        self.assertTrue(all('parent_thread_id' not in v for v in views))
        views[1]['subagents'] = {}
        annotate_parents(views)
        self.assertEqual(views[1]['parent_thread_id'], 'a')
        self.assertNotIn('parent_thread_id', views[2])


if __name__ == '__main__':
    unittest.main()
