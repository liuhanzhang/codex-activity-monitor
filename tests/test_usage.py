import io
import json
import time
import unittest
from unittest.mock import patch

from rich.console import Console
from codex_progress_monitor.display import render
from codex_progress_monitor.engine import Engine
from codex_progress_monitor.events import Event, filter_event
from codex_progress_monitor.usage import MAX_PENDING, MAX_RESPONSES, usage_fields


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.engine = Engine(False)

    def tearDown(self):
        self.engine.close()

    def send(self, value, at=100, backend='b', direction='protocol_out', replayed=False):
        filtered = filter_event(direction, value)
        if filtered is not None:
            self.engine.ingest(Event(backend, direction, filtered, at, 0, replayed))

    def turn(self, turn='u', thread='t', at=100, backend='b', replayed=False):
        self.send({'method':'turn/started','params':{'threadId':thread,'turn':{'id':turn}}},at,backend,replayed=replayed)

    def end_turn(self, turn='u', thread='t', at=120, backend='b'):
        self.send({'method':'turn/completed','params':{'threadId':thread,'turn':{'id':turn,'status':'completed'}}},at,backend)

    def reason(self, item='r', start=101, end=103, turn='u', thread='t', backend='b', replayed=False, source='upstream', status='completed'):
        for is_start, at in ((True,start),(False,end)):
            if at is None:
                continue
            if source=='upstream':
                value={'type':'response.output_item.'+('added' if is_start else 'done'),
                       'item':{'id':item,'type':'reasoning','status':status,'metadata':{'turn_id':turn,'thread_id':thread}}}
            else:
                value={'method':'item/'+('started' if is_start else 'completed'),
                       'params':{'threadId':thread,'turnId':turn,'item':{'id':item,'type':'reasoning','status':status}}}
            self.send(value,at,backend,source,replayed)

    def response(self, response='resp', output=100, reasoning=40, items=('r',), at=110, turn=None, thread=None, backend='b', kind='completed'):
        usage={'output_tokens':output,'output_tokens_details':{'reasoning_tokens':reasoning},'secret':'SENSITIVE'}
        value={'type':'response.'+kind,'response':{'id':response,'model':'test-model','usage':usage,
                  'output':[{'id':i,'type':'reasoning','encrypted_content':'SENSITIVE'} for i in items]}}
        if turn and thread:
            value['identity']={'turn_id':turn,'thread_id':thread}
        self.send(value,at,backend,'upstream')

    def cumulative(self, output, reasoning, last=(100,40), at=111, turn='u', thread='t', backend='b'):
        def camel(values):
            return dict(outputTokens=values[0],reasoningOutputTokens=values[1],inputTokens=999999)
        self.send({'method':'thread/tokenUsage/updated','params':{'threadId':thread,'turnId':turn,
                   'tokenUsage':{'total':camel((output,reasoning)),'last':camel(last),'modelContextWindow':999}}},at,backend)

    def usage(self, thread='t', backend='b'):
        return next(t['usage'] for t in self.engine.snapshot(self.engine.clock)['threads'] if t['id']==thread and t['backend']==backend)

    def test_official_subset_counts_and_delayed_usage(self):
        self.turn()
        self.assertEqual((self.usage()['output_tokens'],self.usage()['reasoning_tokens']),(0,0))
        self.reason(end=103.333333)
        self.response(output=1267,reasoning=114,at=119)
        self.assertEqual((self.usage()['output_tokens'],self.usage()['reasoning_tokens']),(1267,114))
        self.reason('r2',start=120,end=124)
        self.response('resp2',157,42,('r2',),at=150)
        self.assertEqual((self.usage()['output_tokens'],self.usage()['reasoning_tokens']),(1424,156))
        self.assertNotIn('latest_reasoning',self.usage())
        self.assertNotIn('SENSITIVE',json.dumps(self.engine.snapshot(151)))

    def test_request_goal_pause_tools_duplicates_and_new_turn(self):
        self.turn();self.reason();self.response()
        for value in ({'id':8,'method':'turn/start','params':{'threadId':'t'}},
                      {'method':'thread/goal/updated','params':{'threadId':'t','goal':{'status':'paused'}}},
                      {'method':'thread/goal/updated','params':{'threadId':'t','goal':{'status':'active'}}}):
            self.send(value,111,direction='protocol_in' if value.get('id') else 'protocol_out')
        self.turn(at=112,replayed=True)
        self.assertEqual(self.usage()['output_tokens'],100)
        self.reason('r2',start=113,end=None)
        self.turn('next',at=114)
        self.assertEqual(self.usage()['output_tokens'],0)

    def test_late_old_usage_does_not_pollute_or_replace_newer(self):
        self.turn();self.reason(end=103);self.end_turn(at=104)
        self.turn('next',at=105)
        self.reason('new',106,108,'next')
        self.response('newresp',200,60,('new',),109)
        self.response(at=150)
        self.assertEqual(self.usage()['output_tokens'],200)
        self.assertEqual(self.engine.usage.view(('b','t'),'u')['output_tokens'],100)

    def test_rejected_old_start_cannot_move_usage_current_pointer(self):
        self.turn();self.end_turn(at=101)
        self.turn('next',at=102)
        self.engine.usage.threads[('b','t')]['turns'].pop('u')
        self.turn(at=103)
        self.assertEqual(self.engine.usage.threads[('b','t')]['current'],'next')
        self.assertEqual(self.usage()['output_tokens'],0)

    def test_cumulative_and_response_sources_are_not_added(self):
        self.turn();self.reason();self.response()
        self.cumulative(1000,400)
        self.cumulative(1000,400,at=112)
        self.response(at=113)
        self.assertEqual(self.usage()['output_tokens'],100)
        self.reason('r2',114,115)
        self.response('resp2',100,40,('r2',),116)
        self.cumulative(1100,440,at=117)
        self.assertEqual(self.usage()['output_tokens'],200)

    def test_protocol_before_response_can_reconcile_baseline(self):
        self.send({'id':1,'method':'thread/start'},99,direction='protocol_in')
        self.send({'id':1,'result':{'thread':{'id':'t','turns':[]}}},99.1)
        self.turn();self.reason()
        self.cumulative(100,40,at=104)
        self.response(at=105)
        self.end_turn(at=106);self.turn('next',at=107)
        self.send({'type':'bridge/gap'},108,direction='lifecycle')
        self.assertIsNone(self.usage()['output_tokens'])
        self.cumulative(150,60,last=(50,20),turn='next',at=109)
        self.assertEqual((self.usage()['output_tokens'],self.usage()['reasoning_tokens']),(50,20))

    def test_identical_last_and_late_prefix_cannot_bootstrap_unknown_history(self):
        self.turn();self.reason();self.response()
        self.reason('r2',111,112)
        self.response('resp2',100,40,('r2',),113)
        # Historical thread output was 900 / 360. First prefix arrives after
        # both response events; matching `last` cannot identify that prefix.
        self.cumulative(1000,400,at=114)
        self.cumulative(1100,440,at=115)
        self.assertEqual(self.usage()['output_tokens'],200)
        self.assertIsNone(self.engine.usage.threads[('b','t')]['turns']['u']['baseline'])
        self.send({'type':'bridge/gap'},116,direction='lifecycle')
        self.cumulative(1200,480,at=117)
        self.assertIsNone(self.usage()['output_tokens'])

    def test_cumulative_and_response_prefix_conflict_is_unknown(self):
        self.send({'id':1,'method':'thread/start'},99,direction='protocol_in')
        self.send({'id':1,'result':{'thread':{'id':'t','turns':[]}}},99.1)
        self.turn();self.reason();self.response(output=200,reasoning=70)
        self.cumulative(190,80,at=111)
        self.assertIsNone(self.usage()['output_tokens'])
        self.assertIn('cumulative_response_conflict',self.usage()['diagnostics'])

    def test_old_cumulative_prefix_cannot_erase_current_response_counts(self):
        self.turn();self.reason();self.response();self.cumulative(1000,400);self.end_turn(at=112)
        self.turn('next',at=113);self.reason('new',114,115,'next')
        self.response('newresp',200,60,('new',),116)
        self.cumulative(1200,460,last=(200,60),turn='next',at=117)
        self.cumulative(1000,400,turn='u',at=118)
        self.assertEqual(self.usage()['output_tokens'],200)
        self.assertEqual(self.usage()['reasoning_tokens'],60)
        self.assertNotIn('cumulative_regression',self.usage()['diagnostics'])

    def test_midjoin_total_never_becomes_turn_total(self):
        self.send({'method':'item/completed','params':{'threadId':'t','turnId':'u','item':{'id':'r','type':'reasoning'}}})
        self.response(turn='u',thread='t')
        self.cumulative(10000,5000)
        self.end_turn()
        self.assertIsNone(self.usage()['output_tokens'])

    def test_new_thread_protocol_only_and_regression(self):
        self.send({'id':1,'method':'thread/start'},99,direction='protocol_in')
        self.send({'id':1,'result':{'thread':{'id':'t','turns':[]}}},99.1)
        self.turn();self.cumulative(100,40)
        self.assertEqual(self.usage()['output_tokens'],100)
        self.cumulative(90,40,at=112)
        self.assertIsNone(self.usage()['output_tokens'])
        self.assertIn('cumulative_regression',self.usage()['diagnostics'])

    def test_multiple_reasonings_preserve_official_aggregate_without_speed(self):
        self.turn();self.reason();self.response()
        self.reason('a',111,112);self.reason('z',113,115)
        self.response('multi',300,120,('a','z'),119)
        self.assertEqual(self.usage()['output_tokens'],400)
        self.assertEqual(self.usage()['reasoning_tokens'],160)
        self.assertNotIn('latest_reasoning',json.dumps(self.engine.snapshot(120)))

    def test_missing_item_end_does_not_discard_official_usage(self):
        self.turn();self.reason(end=None)
        self.response()
        self.assertEqual((self.usage()['output_tokens'],self.usage()['reasoning_tokens']),(100,40))

    def test_usage_before_item_completion_and_mapping(self):
        self.turn();self.response(at=102)
        self.assertEqual(len(self.engine.usage.pending),1)
        self.reason(start=101,end=None)
        self.assertEqual((self.usage()['output_tokens'],self.usage()['reasoning_tokens']),(100,40))
        self.reason(start=None,end=104)
        self.assertEqual((self.usage()['output_tokens'],self.usage()['reasoning_tokens']),(100,40))

    def test_invalid_usage_fields_and_item_timing_does_not_change_counts(self):
        for output,reasoning in ((True,3),(-1,0),(1.5,0),(1,2),(10,None),(None,5)):
            with self.subTest(output=output,reasoning=reasoning):
                self.assertEqual(usage_fields({'output_tokens':output,'output_tokens_details':{'reasoning_tokens':reasoning}}).get('reasoning_tokens'),
                                 reasoning if output is None or output==10 else None)
        self.turn();self.reason(end=101)
        self.response()
        self.assertEqual((self.usage()['output_tokens'],self.usage()['reasoning_tokens']),(100,40))
        self.reason('failed',111,113,status='failed')
        self.response('failedresp',100,40,('failed',),115)
        self.assertEqual((self.usage()['output_tokens'],self.usage()['reasoning_tokens']),(200,80))
        self.reason('partial',116,117)
        self.response('partialresp',None,40,('partial',),118)
        self.assertIsNone(self.usage()['output_tokens'])

    def test_gap_keeps_usage_unknown_until_a_new_complete_turn(self):
        self.turn();self.reason();self.response()
        self.reason('gap',111,None)
        self.send({'type':'bridge/gap'},112,direction='lifecycle')
        self.reason('gap',None,113)
        self.response('gapresp',100,40,('gap',),114)
        self.assertIsNone(self.usage()['output_tokens'])
        self.turn('next',at=121)
        self.reason('valid',122,124,'next')
        self.response('validresp',100,40,('valid',),125)
        self.assertEqual((self.usage()['output_tokens'],self.usage()['reasoning_tokens']),(100,40))

    def test_mixed_boundary_sources_do_not_affect_usage(self):
        self.turn();self.reason(end=None)
        self.reason(start=None,end=104,source='protocol_out')
        self.response()
        self.assertEqual((self.usage()['output_tokens'],self.usage()['reasoning_tokens']),(100,40))

    def test_independent_threads_homes_and_duplicate_backends(self):
        for backend,home in (('b','home'),('mirror','home'),('other','otherhome')):
            self.send({'type':'bridge/started','home':home},99,backend,'lifecycle')
            self.turn(backend=backend)
            self.reason(backend=backend)
            self.response(backend=backend)
        views=self.engine.snapshot(111)['threads']
        self.assertEqual(len(views),2)
        self.assertTrue(all(t['usage']['output_tokens']==100 for t in views))
        self.turn('childturn','child',112)
        self.reason('childr',113,114,'childturn','child')
        self.response('childresp',80,20,('childr',),115)
        self.assertEqual(self.usage('child')['output_tokens'],80)
        self.assertEqual(self.usage()['output_tokens'],100)

    def test_conflicting_identity_and_response_counts_are_quarantined(self):
        self.turn();self.reason()
        self.turn('v','other',at=104)
        self.reason('other_r',105,106,'v','other')
        self.response(items=('r','other_r'),at=107)
        self.assertIsNone(self.usage()['output_tokens'])
        self.assertIsNone(self.usage('other')['output_tokens'])

    def test_duplicate_response_conflict_invalidates_counts(self):
        self.turn();self.reason();self.response()
        self.response(output=101,at=112)
        self.assertIsNone(self.usage()['output_tokens'])

    def test_zero_usage_is_zero_and_missing_reasoning_is_unknown(self):
        self.turn();self.reason();self.response(output=0,reasoning=0)
        self.assertEqual(self.usage()['output_tokens'],0)
        self.assertEqual(self.usage()['reasoning_tokens'],0)
        self.reason('next',111,112)
        self.response('nextresp',output=50,reasoning=None,items=('next',),at=113)
        self.assertEqual(self.usage()['output_tokens'],50)
        self.assertIsNone(self.usage()['reasoning_tokens'])

    def test_delayed_reasoning_mapping_preserves_official_usage(self):
        for suffix,at in (('added',101),('done',103)):
            self.send({'type':'response.output_item.'+suffix,'item':{'id':'r','type':'reasoning','metadata':{'turn_id':'u'}}},at,direction='upstream')
        self.turn(at=100)
        self.response(at=110)
        self.assertEqual((self.usage()['output_tokens'],self.usage()['reasoning_tokens']),(100,40))

    def test_legacy_rate_is_discarded_while_official_counts_are_restored(self):
        self.engine.usage.restore({'version':0,'threads':[{}]})
        self.engine.usage.restore({'version':2,'threads':[{}]})
        self.assertFalse(self.engine.usage.threads)
        self.turn();self.reason();self.response()
        data=self.engine.usage.dump()
        data['threads'][0]['state']['latest_reasoning']={
            'tokens_per_second':20,'duration_seconds':2,'reasoning_tokens':40,
            'turn_id':'u','response_id':'resp','item_id':'r','source':'upstream','order':101}
        data['threads'][0]['state']['private']='SENSITIVE'
        from codex_progress_monitor.usage import UsageBook
        restored=UsageBook();restored.restore(data)
        self.assertEqual((restored.view(('b','t'),'u')['output_tokens'],restored.view(('b','t'),'u')['reasoning_tokens']),(100,40))
        self.assertNotIn('latest_reasoning',json.dumps(restored.dump()))
        self.assertNotIn('SENSITIVE',json.dumps(restored.dump()))

    def test_restoration_preserves_static_values_and_dedupe(self):
        self.send({'type':'bridge/started','home':'home'},99,direction='lifecycle')
        self.turn();self.reason();self.response();self.end_turn()
        data=json.loads(json.dumps(self.engine.snapshot(120)))
        restored=Engine(False)
        try:
            restored.usage.restore(data['usage_recovery'])
            restored.ingest(Event('new','lifecycle',{'type':'bridge/started','home':'home'},121,0))
            restored.ingest(Event('new','protocol_out',{'method':'turn/completed','params':{'threadId':'t','turn':{'id':'u','status':'completed'}}},120,0,True))
            usage=restored.snapshot(122)['threads'][0]['usage']
            self.assertEqual(usage['output_tokens'],100)
            self.assertEqual(usage['reasoning_tokens'],40)
            restored.ingest(Event('new','upstream',filter_event('upstream',{'type':'response.completed',
                'identity':{'thread_id':'t','turn_id':'u'},'response':{'id':'resp','model':'test-model','output':[{'id':'r','type':'reasoning'}],
                'usage':{'output_tokens':100,'output_tokens_details':{'reasoning_tokens':40}}}}),110,0,True))
            self.assertEqual(restored.snapshot(122)['threads'][0]['usage']['output_tokens'],100)
        finally:
            restored.close()

    def test_attribution_accepts_only_verified_generated_ids(self):
        self.turn();self.reason()
        raw={'type':'response.completed','response':{'id':'attr','model':'test-model','output':[],
             'usage':{'output_tokens':40,'output_tokens_details':{'reasoning_tokens':40},
                      'attribution':{'items':{'history':{'output_tokens':0,'input_tokens':10000,'content':'SENSITIVE'},'r':{'output_tokens':40}}}}}}
        filtered=filter_event('upstream',raw)
        self.assertEqual(filtered['response']['item_ids'],['r'])
        self.assertNotIn('history',json.dumps(filtered))
        self.send(raw,110,direction='upstream')
        self.assertEqual((self.usage()['output_tokens'],self.usage()['reasoning_tokens']),(40,40))
        raw['response']['usage']['attribution']['items']['r']['output_tokens']=39
        self.assertFalse(filter_event('upstream',raw)['response']['items_complete'])

    def test_truncated_membership_requires_independent_response_identity(self):
        self.turn();self.reason()
        with patch('codex_progress_monitor.usage.MAX_ITEMS',1):
            self.response(items=('r','unmapped'))
            self.assertIsNone(self.usage()['output_tokens'])
        self.turn('next',at=111)
        self.response('identified',100,40,(),112,'next','t')
        self.assertEqual(self.usage()['output_tokens'],100)

    def test_bounded_response_and_pending_caches_fail_conservatively(self):
        self.turn()
        with patch('codex_progress_monitor.usage.MAX_RESPONSES',2):
            for n in range(3):
                self.response(str(n),100,0,(),110+n,'u','t')
            self.assertIsNone(self.usage()['output_tokens'])
            self.assertIn('response_limit',self.usage()['diagnostics'])
        with patch('codex_progress_monitor.usage.MAX_PENDING',2):
            for n in range(5):
                self.response('unknown'+str(n),items=('unmapped'+str(n),),at=120+n)
            self.assertEqual(len(self.engine.usage.pending),2)
            self.assertEqual(self.engine.usage.diagnostics['pending_limit'],3)

    def test_expired_old_unassociated_usage_cannot_invalidate_new_turn(self):
        self.turn()
        self.response('unknown',items=('unmapped',),at=110)
        self.end_turn(at=120)
        self.turn('next',at=700)
        self.reason('new',701,703,'next')
        self.response('newresp',200,60,('new',),712)
        self.assertEqual(self.engine.usage.diagnostics['unassociated_expired'],1)
        self.assertEqual((self.usage()['output_tokens'],self.usage()['reasoning_tokens']),(200,60))
        self.assertNotIn('unassociated_usage_expired',self.usage()['diagnostics'])

    def test_expiry_invalidates_overlapping_turn_counts(self):
        self.turn()
        self.response('unknown',items=('unmapped',),at=110)
        self.reason('new',711,713)
        self.response('newresp',200,60,('new',),714)
        self.assertIsNone(self.usage()['output_tokens'])
        self.assertIn('unassociated_usage_expired',self.usage()['diagnostics'])

    def test_counts_and_visible_speed_render_but_legacy_reasoning_rate_is_hidden(self):
        self.turn();self.reason();self.response(output=123456,reasoning=9000)
        snapshot=self.engine.snapshot(111,time.time())
        snapshot['threads'][0]['generation_rate']=23.7
        snapshot['threads'][0]['usage']['latest_reasoning']={'tokens_per_second':4500,'turn_id':'u'}
        for width in (60,80,120,240):
            with self.subTest(width=width):
                output=io.StringIO()
                Console(file=output,width=width).print(render(snapshot,detail=True)[0])
                rendered=output.getvalue()
                compact=''.join(rendered.split())
                self.assertIn('123,456',compact)
                self.assertIn('9,000',compact)
                self.assertIn('23.7',compact)
                self.assertNotIn('4500',compact)
                self.assertNotIn('推理均速',compact)
                self.assertNotIn('统计中',rendered)
                self.assertNotIn('SENSITIVE',rendered)
        snapshot['updated_at']=time.time()-10
        output=io.StringIO();Console(file=output,width=160).print(render(snapshot)[0])
        self.assertNotIn('4500',output.getvalue())


if __name__=='__main__':
    unittest.main()
