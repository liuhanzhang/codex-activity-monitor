import json
import time
import unittest

from codex_progress_monitor.engine import Engine
from codex_progress_monitor.events import Event,filter_event
from codex_progress_monitor.metrics import TextStream,TokenCounter,count_text


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.engine=Engine(False)

    def tearDown(self):
        self.engine.close()

    def send(self,value,at=100,backend='b',direction='protocol_out'):
        event=filter_event(direction,value)
        if event is not None:
            self.engine.ingest(Event(backend,direction,event,at,0))

    def turn(self,thread='t',turn='u',at=100,backend='b'):
        self.send({'method':'turn/started','params':{'threadId':thread,'turn':{'id':turn,'status':'inProgress'}}},at,backend)

    def item(self,item='i',kind='message',turn='u',at=101):
        self.send({'type':'response.output_item.added','item':{'id':item,'type':kind,'metadata':{'turn_id':turn}}},at,direction='upstream')

    def delta(self,item='i',text='hello',sequence=1,at=102,kind='response.output_text.delta'):
        self.send({'type':kind,'item_id':item,'delta':text,'sequence_number':sequence},at,direction='upstream')

    def view(self,at=103):
        self.engine.tick(at)
        return self.engine.snapshot(at)

    def test_concurrent_mapping_mirrors_and_final_counts(self):
        self.turn()
        self.turn('other','v')
        self.item()
        self.item('j',turn='v')
        for index,text in enumerate(['中文','print','🙂','hello']):
            self.delta('i',text,index,102+index/4)
            self.delta('j',text.upper(),index,102+index/4)
            self.send({'method':'item/agentMessage/delta','params':{'threadId':'t','turnId':'u','itemId':'i','delta':text}},102+index/4)
            self.delta('i',text,index,102+index/4)
        view=self.view(104)
        self.assertEqual(len(view['threads']),2)
        self.assertEqual(self.engine.counts['duplicates_ignored'],4)
        self.assertEqual(self.engine.streams[('b','i','text',0)].tokens,count_text('中文print🙂hello','o200k_base')[0])
        self.assertEqual(self.engine.streams[('b','j','text',0)].text,'中文PRINT🙂HELLO')
        self.assertEqual(view['threads'][0]['phase'],'text')

    def test_unknown_never_assigned_by_recency(self):
        self.turn()
        self.delta('unknown')
        self.assertEqual(self.view()['unassociated_events'],1)
        self.assertEqual(len(self.engine.streams),0)
        self.view(120)
        self.assertEqual(self.engine.counts['unassociated_expired'],1)

    def test_unmapped_completed_output_no_longer_blocks_live_coverage(self):
        self.turn()
        self.item()
        self.delta()
        self.item('unmapped', turn='unobserved-turn', at=102.1)
        self.delta('unmapped', at=102.2)
        self.assertEqual(self.view(103)['overview']['coverage'], 'partial')
        self.send({'type': 'response.output_item.done', 'item': {
            'id': 'unmapped', 'type': 'message', 'metadata': {'turn_id': 'unobserved-turn'},
            'content': [{'type': 'output_text', 'text': 'finished'}]}}, 103.1, direction='upstream')
        result = self.view(103.2)
        self.assertEqual(result['overview']['coverage'], 'complete')
        self.assertGreater(result['unassociated_events'], 0)
        self.assertEqual(result['unassociated_live_events'], 0)
        self.assertEqual(len(result['threads']), 1)  # No guessed owner.
        # The bounded queue can still recover identity and final counts later.
        self.turn('other', 'unobserved-turn', 104)
        recovered = next(t for t in self.view(104.1)['threads'] if t['id'] == 'other')
        self.assertFalse(recovered['activities'][0]['active'])

    def test_response_end_retires_only_its_unmapped_generation(self):
        self.turn()
        self.item()
        self.delta()
        for item in ('closed', 'still-open'):
            self.item(item, turn='unobserved-turn', at=102.1)
            self.delta(item, at=102.2)
        self.send({'type': 'response.completed', 'response': {'id': 'response',
            'output': [{'id': 'closed', 'type': 'message'}]}}, 103, direction='upstream')
        result = self.view(103.1)
        self.assertEqual(result['overview']['coverage'], 'partial')
        self.assertEqual(result['unassociated_live_events'], 2)
        self.send({'type': 'response.completed', 'response': {'id': 'response2',
            'output': [{'id': 'still-open', 'type': 'message'}]}}, 103.2, direction='upstream')
        self.assertEqual(self.view(103.3)['overview']['coverage'], 'complete')
        self.turn('other', 'unobserved-turn', 104)
        recovered = next(t for t in self.view(104.1)['threads'] if t['id'] == 'other')
        self.assertTrue(all(not a['active'] for a in recovered['activities']))
        self.assertEqual(recovered['generation_rate'], None)
        self.assertEqual(self.engine.pending_ends, {})

    def test_unmapped_completion_is_scoped_to_its_backend(self):
        self.turn()
        self.item()
        self.delta()
        for backend in ('b', 'other'):
            self.send({'type': 'response.output_text.delta', 'item_id': 'unmapped', 'delta': 'x'},
                      102.1, backend, 'upstream')
        self.send({'type': 'response.completed', 'response': {'id': 'response',
            'output': [{'id': 'unmapped', 'type': 'message'}]}}, 103, direction='upstream')
        self.assertEqual(self.view(103.1)['unassociated_live_events'], 1)

    def test_reconnect_before_thread_identity_keeps_gap_until_new_turn(self):
        self.engine.ingest(Event('b','lifecycle',{'type':'bridge/started'},100,0))
        self.engine.ingest(Event('b','lifecycle',{'type':'bridge/reconnected'},101,0))
        self.send({'method':'thread/status/changed','params':{'threadId':'t','status':{'type':'active','activeFlags':[]}}},102)
        # Also receive an item while the start of its turn was not observed.
        self.send({'method':'item/started','params':{'threadId':'t','turnId':'old','item':{'id':'i','type':'reasoning'}}},103)
        view=self.view(104)
        self.assertIn('observation_gap',view['threads'][0]['quality'])
        self.assertIsNone(view['overview']['generation_rate'])
        self.turn(turn='new',at=105)
        view=self.view(106)
        self.assertNotIn('observation_gap',view['threads'][0]['quality'])
        self.assertEqual(view['overview']['generation_rate'],0)

    def test_delayed_mapping_uses_original_time(self):
        self.delta(at=102)
        self.item(at=103)
        self.turn(at=104)
        self.assertEqual(self.view(105)['threads'][0]['activities'][0]['streams'][0]['duration_seconds'],2)

    def test_approval_overrides_in_progress_and_duration(self):
        self.turn()
        self.send({'method':'item/started','params':{'threadId':'t','turnId':'u','item':{'id':'cmd','type':'commandExecution','status':'inProgress','command':'sleep 10'}}},101)
        self.send({'id':99,'method':'item/commandExecution/requestApproval','params':{'threadId':'t','turnId':'u','itemId':'cmd'}},101.01)
        self.assertEqual(self.view(110)['threads'][0]['phase'],'approval')
        self.send({'id':99,'result':{'decision':'decline'}},111,direction='protocol_in')
        self.assertEqual(self.view(112)['threads'][0]['phase'],'tool_ready')
        self.assertEqual(self.view(112)['threads'][0]['display_label'],'处理中 · sleep')

    def test_reasoning_counts_and_terminal_freezes_time(self):
        self.turn()
        self.item(kind='reasoning')
        self.delta(text='摘要🙂',kind='response.reasoning_summary_text.delta')
        a=self.view()['threads'][0]['activities'][0]
        self.assertEqual((a['summary_chars'],a['summary_parts']),(3,1))
        self.assertGreater(a['streams'][0]['token_rate'],0)
        self.send({'method':'turn/completed','params':{'threadId':'t','turn':{'id':'u','status':'interrupted'}}},104)
        self.assertEqual(self.view(120)['threads'][0]['activities'][0]['duration_seconds'],3)
        self.assertEqual(self.view(120)['threads'][0]['phase'],'interrupted')

    def test_started_unified_process_confirms_silent_execution(self):
        self.turn()
        self.send({'method':'item/started','params':{'threadId':'t','turnId':'u','item':{'id':'cmd','type':'commandExecution','status':'inProgress','source':'unifiedExecStartup','processId':'123'}}},101)
        thread=self.view(116)['threads'][0]
        self.assertEqual(thread['phase'],'tool')
        self.assertEqual(thread['phase_seconds'],15)
        self.assertEqual(thread['activities'][0]['output_chars'],0)
        self.send({'id':99,'method':'item/commandExecution/requestApproval','params':{'threadId':'t','turnId':'u','itemId':'cmd'}},116)
        self.assertEqual(self.view(117)['threads'][0]['phase'],'tool')
        self.assertEqual(self.view(117)['threads'][0]['display_label'],'运行中')

    def test_gap_disables_speed_and_next_turn_recovers(self):
        self.turn()
        self.item()
        self.delta()
        self.view()
        self.send({'type':'bridge/gap'},104,direction='lifecycle')
        s=self.view(105)['threads'][0]['activities'][0]['streams'][0]
        self.assertIsNone(s['tokens'])
        self.assertIsNone(s['token_rate'])
        self.turn(turn='new',at=110)
        self.item('newitem',turn='new',at=111)
        self.delta('newitem',at=112)
        self.assertEqual(self.view(113)['threads'][0]['activities'][0]['streams'][0]['quality'],'complete')

    def test_disconnected_cannot_stay_running(self):
        self.turn()
        self.item()
        self.send({'type':'bridge/disconnected'},104,direction='lifecycle')
        thread=self.view(110)['threads'][0]
        self.assertEqual(thread['phase'],'unknown')
        self.assertEqual(thread['active_count'],0)

    def test_background_command_outlives_turn_and_new_generation(self):
        self.turn()
        self.send({'method':'item/started','params':{'threadId':'t','turnId':'u','item':{'id':'cmd','type':'commandExecution','status':'inProgress','source':'unifiedExecStartup','processId':'123'}}},101)
        self.send({'method':'item/commandExecution/outputDelta','params':{'threadId':'t','turnId':'u','itemId':'cmd','delta':'begin'}},102)
        self.send({'method':'turn/completed','params':{'threadId':'t','turn':{'id':'u','status':'completed'}}},103)
        thread=self.view(105)['threads'][0]
        self.assertEqual((thread['status'],thread['phase'],thread['active_count']),('completed','tool',1))
        self.assertTrue(thread['has_active_work'])
        self.assertEqual(thread['phase_seconds'],4)
        self.turn(turn='new',at=106)
        self.item('newtext',turn='new',at=107)
        self.delta('newtext',at=108)
        self.assertEqual(self.view(109)['threads'][0]['active_count'],2)
        self.send({'method':'item/completed','params':{'threadId':'t','turnId':'u','item':{'id':'cmd','type':'commandExecution','status':'completed','exitCode':0}}},110)
        self.assertEqual(self.view(111)['threads'][0]['active_count'],1)

    def test_background_command_disconnect_stops_live_state(self):
        self.turn()
        self.send({'method':'item/started','params':{'threadId':'t','turnId':'u','item':{'id':'cmd','type':'commandExecution','status':'inProgress','source':'unifiedExecStartup','processId':'123'}}},101)
        self.send({'method':'turn/completed','params':{'threadId':'t','turn':{'id':'u','status':'completed'}}},103)
        self.send({'type':'bridge/disconnected'},104,direction='lifecycle')
        thread=self.view(105)['threads'][0]
        self.assertEqual(thread['phase'],'unknown')
        self.assertEqual(thread['active_count'],0)
        self.assertTrue(thread['has_unresolved_work'])
        self.assertIsNone(self.view(105)['overview']['generation_rate'])

    def test_late_generation_cannot_reopen_completed_turn(self):
        self.turn()
        self.send({'method':'turn/completed','params':{'threadId':'t','turn':{'id':'u','status':'completed'}}},103)
        self.item(kind='custom_tool_call',at=104)
        self.delta(kind='response.custom_tool_call_input.delta',at=105)
        self.send({'type':'response.output_item.done','item':{'id':'i','type':'custom_tool_call','metadata':{'turn_id':'u'},'input':'late final'}},106,direction='upstream')
        thread=self.view(107)['threads'][0]
        self.assertEqual((thread['phase'],thread['active_count']),('completed',0))
        self.assertIsNone(thread['token_rates']['tool_input'])
        self.assertEqual(thread['activities'][0]['streams'][0]['tokens'],count_text('late final','o200k_base')[0])

    def test_bad_stream_disables_aggregate_rate_and_marks_quality(self):
        self.turn();self.item();self.delta();self.view()
        stream=self.engine.streams[('b','i','text',0)]
        stream.append('over limit',104,max_bytes=1)
        self.send({'type':'response.output_text.delta','item_id':'i','delta':'valid second part','content_index':1,'sequence_number':2},104,direction='upstream')
        thread=self.view(105)['threads'][0]
        self.assertIn('content_limit',thread['quality'])
        self.assertIsNone(thread['token_rates']['text'])

    def test_duplicate_lifecycle_does_not_refresh_progress(self):
        self.turn();self.item();self.delta()
        before=self.view(104)['threads'][0]['last_progress_seconds']
        self.turn(at=120)
        thread=self.view(121)['threads'][0]
        self.assertEqual(before,2)
        self.assertEqual(thread['last_progress_seconds'],19)
        self.send({'method':'turn/completed','params':{'threadId':'t','turn':{'id':'u','status':'completed'}}},122)
        self.send({'method':'turn/completed','params':{'threadId':'t','turn':{'id':'u','status':'completed'}}},130)
        self.assertEqual(self.view(131)['threads'][0]['last_progress_seconds'],9)

    def test_conflicting_turn_identity_is_quarantined(self):
        self.turn();self.item();self.delta();self.view()
        self.turn(thread='other',at=104)
        self.delta(text='must not assign',sequence=2,at=105)
        self.view(106)
        self.assertEqual(self.engine.streams[('b','i','text',0)].chars,5)
        self.assertEqual(self.engine.streams[('b','i','text',0)].quality,'identity_conflict')
        self.assertNotIn(('b','u'),self.engine.turns)
        self.assertEqual(self.engine.counts['identity_conflicts'],1)

    def test_silent_command_does_not_invent_execution_from_timer(self):
        self.turn()
        self.send({'method':'item/started','params':{'threadId':'t','turnId':'u','item':{'id':'cmd','type':'commandExecution','status':'inProgress'}}},101)
        self.assertEqual(self.view(120)['threads'][0]['phase'],'tool_ready')
        self.send({'method':'item/commandExecution/outputDelta','params':{'threadId':'t','turnId':'u','itemId':'cmd','delta':'running'}},121)
        self.assertEqual(self.view(122)['threads'][0]['phase'],'tool')

    def test_code_mode_names_update_during_arguments_without_confirming_execution(self):
        self.turn()
        self.send({'type':'response.output_item.added','item':{'id':'code','type':'custom_tool_call','name':'exec',
                   'metadata':{'turn_id':'u'}}},101,direction='upstream')
        self.assertEqual(self.view(101.1)['threads'][0]['activities'][0]['label'],'exec（Code Mode）')
        self.delta('code','await tools.exec_com',at=102,kind='response.custom_tool_call_input.delta')
        self.assertEqual(self.view(102.1)['threads'][0]['activities'][0]['tool_names'],[])
        self.delta('code','mand({cmd: "rg --files",extra: "PRIVATE_TOOL_INPUT"});',sequence=2,at=103,kind='response.custom_tool_call_input.delta')
        thread=self.view(103.1)['threads'][0]
        self.assertEqual(thread['phase'],'tool_input')
        self.assertEqual(thread['activities'][0]['label'],'exec → exec_command · 拟执行: rg --files')
        self.assertNotIn('PRIVATE_TOOL_INPUT',json.dumps(thread))
        self.send({'type':'response.output_item.done','item':{'id':'code','type':'custom_tool_call','name':'exec',
                   'input':'await tools.exec_command({cmd: "rg --files",extra: "PRIVATE_TOOL_INPUT"});','metadata':{'turn_id':'u'}}},104,direction='upstream')
        self.assertEqual(self.view(105)['threads'][0]['phase'],'tool_ready')
        self.assertEqual(self.engine.streams[('b','code','tool_input',0)].text,'')
        self.item('next',kind='reasoning',at=106)
        previous=next(a for a in self.view(107)['threads'][0]['activities'] if a['id']=='code')
        self.assertFalse(previous['active'])
        self.assertEqual(previous['tool_name'],'exec')

    def test_command_preview_grows_before_parameters_complete_and_does_not_confirm_execution(self):
        self.turn()
        self.send({'type':'response.output_item.added','item':{'id':'code','type':'custom_tool_call','name':'exec',
                   'metadata':{'turn_id':'u'}}},101,direction='upstream')
        self.delta('code','tools.exec_command({cmd:"git sta',at=102,kind='response.custom_tool_call_input.delta')
        thread=self.view(102.1)['threads'][0];activity=thread['activities'][0]
        self.assertEqual(thread['phase'],'tool_input')
        self.assertEqual(activity['command_previews'][0]['command'],'git sta…')
        self.assertIsNone(activity['command'])
        self.delta('code','tus",env:{key:"PRIVATE_ENV"}});',sequence=2,at=103,kind='response.custom_tool_call_input.delta')
        activity=self.view(103.1)['threads'][0]['activities'][0]
        self.assertEqual(activity['command_previews'][0]['command'],'git status')
        self.assertNotIn('PRIVATE_ENV',json.dumps(activity))
        self.send({'type':'response.output_item.done','item':{'id':'code','type':'custom_tool_call','name':'exec',
                   'input':'tools.exec_command({cmd:"git status",env:{key:"PRIVATE_ENV"}});','metadata':{'turn_id':'u'}}},104,direction='upstream')
        thread=self.view(105)['threads'][0]
        self.assertEqual(thread['phase'],'tool_ready')
        self.assertEqual(thread['activities'][0]['command_previews'][0]['command'],'git status')
        self.assertIsNone(thread['activities'][0]['command'])
        self.assertEqual(self.engine.streams[('b','code','tool_input',0)].text,'')

    def test_direct_command_function_has_preview_during_json_argument_generation(self):
        self.turn()
        self.send({'type':'response.output_item.added','item':{'id':'cmd','type':'function_call','name':'exec_command',
                   'namespace':'functions','metadata':{'turn_id':'u'}}},101,direction='upstream')
        self.delta('cmd','{"cmd":"rg --fi',at=102,kind='response.function_call_arguments.delta')
        thread=self.view()['threads'][0]
        self.assertEqual(thread['phase'],'tool_input')
        self.assertEqual(thread['activities'][0]['label'],'functions.exec_command · 拟执行: rg --fi…')

    def test_final_metadata_supplies_missing_tool_name_and_namespace(self):
        self.turn();self.item(kind='function_call')
        self.delta(text='{"private":true}',kind='response.function_call_arguments.delta')
        self.assertEqual(self.view()['threads'][0]['activities'][0]['label'],'工具名未知')
        self.send({'type':'response.output_item.done','item':{'id':'i','type':'function_call','name':'lookup',
                   'namespace':'catalog','arguments':'{"private":true}','metadata':{'turn_id':'u'}}},104,direction='upstream')
        activity=self.view(105)['threads'][0]['activities'][0]
        self.assertEqual(activity['label'],'catalog.lookup')
        self.assertNotIn('private',json.dumps(activity))

    def test_completed_code_reconciles_names_and_namespaced_exec_closes(self):
        self.turn()
        self.send({'type':'response.output_item.added','item':{'id':'code','type':'custom_tool_call','name':'exec',
                   'namespace':'functions','metadata':{'turn_id':'u'}}},101,direction='upstream')
        self.delta('code','tools.partial()',at=102,kind='response.custom_tool_call_input.delta')
        self.view()
        self.send({'type':'response.output_item.done','item':{'id':'code','type':'custom_tool_call',
                   'input':'await Promise.all([tools.exec_command({}), tools.mcp__web__run({})]);',
                   'metadata':{'turn_id':'u'}}},104,direction='upstream')
        self.assertEqual(self.view(105)['threads'][0]['activities'][0]['tool_names'],['exec_command','mcp__web__run'])
        self.item('next',at=106)
        previous=next(a for a in self.view(107)['threads'][0]['activities'] if a['id']=='code')
        self.assertFalse(previous['active'])

    def test_command_summary_updates_when_lifecycle_metadata_arrives_late(self):
        self.turn()
        self.send({'method':'item/started','params':{'threadId':'t','turnId':'u','item':{'id':'cmd',
                   'type':'commandExecution','source':'unifiedExecStartup','processId':'123'}}},101)
        self.send({'method':'item/completed','params':{'threadId':'t','turnId':'u','item':{'id':'cmd',
                   'type':'commandExecution','status':'completed','command':'/bin/zsh -lc "rg --files src"'}}},104)
        activity=self.view(105)['threads'][0]['activities'][0]
        self.assertEqual(activity['label'],'exec_command · rg --files src')
        self.assertEqual(activity['command'],'rg --files src')
        self.assertFalse(activity['active'])

    def test_final_after_rejoin_has_count_but_no_speed(self):
        self.turn()
        self.send({'type':'response.output_item.done','item':{'id':'late','type':'message','metadata':{'turn_id':'u'},'content':[{'type':'output_text','text':'final count'}]}},104,direction='upstream')
        a=self.view(105)['threads'][0]['activities'][0]
        self.assertFalse(a['start_known'])
        self.assertEqual(a['streams'][0]['tokens'],count_text('final count','o200k_base')[0])
        self.assertEqual(a['streams'][0]['quality'],'reconciled')
        self.assertIsNone(a['streams'][0]['token_rate'])

    def test_out_of_order_and_final_reconciliation(self):
        self.turn()
        self.item()
        self.delta(sequence=5,text='A')
        self.delta(sequence=4,text='B')
        self.send({'type':'response.output_item.done','item':{'id':'i','type':'message','metadata':{'turn_id':'u'},'content':[{'type':'output_text','text':'AB'}]}},104,direction='upstream')
        stream=self.view(105)['threads'][0]['activities'][0]['streams'][0]
        self.assertEqual(stream['quality'],'reconciled')
        self.assertEqual(stream['tokens'],count_text('AB','o200k_base')[0])
        self.assertEqual(self.engine.streams[('b','i','text',0)].text,'')

    def test_metadata_retained_and_snapshot_has_no_generated_text(self):
        self.send({'method':'thread/started','params':{'thread':{'id':'t','name':'标题','cwd':'/tmp'}}},10000)
        self.view(10001)
        self.turn(at=10002)
        self.item(at=10003)
        self.delta(text='SENSITIVE_GENERATED_PAYLOAD',at=10004)
        text=json.dumps(self.view(10005),ensure_ascii=False)
        self.assertIn('标题',text)
        self.assertNotIn('SENSITIVE_GENERATED_PAYLOAD',text)
        safe=filter_event('protocol_in',{'method':'turn/start','params':{'threadId':'t','input':[{'text':'SECRET_USER_INPUT'}]}})
        self.assertNotIn('SECRET_USER_INPUT',json.dumps(safe))
        for method in ('item/started','item/completed'):
            safe=filter_event('protocol_out',{'method':method,'params':{'threadId':'t','item':{'id':'input','type':'userMessage','content':[{'type':'text','text':'SECRET_USER_INPUT'}]}}})
            self.assertNotIn('SECRET_USER_INPUT',json.dumps(safe))

    def test_ephemeral_classification_is_authoritative_and_survives_filtering(self):
        self.send({'id':1,'method':'thread/start','params':{'ephemeral':True,'threadSource':'thread_name'}},100,direction='protocol_in')
        self.send({'id':1,'result':{'thread':{'id':'helper'}}},101)
        helper=self.view(102)['threads'][0]
        self.assertTrue(helper['ephemeral'])
        self.assertEqual(helper['thread_source'],'thread_name')
        self.send({'method':'thread/started','params':{'thread':{'id':'main','ephemeral':False}}},103)
        main=next(t for t in self.view(104)['threads'] if t['id']=='main')
        self.assertFalse(main['ephemeral'])


class CounterTests(unittest.TestCase):
    def test_large_parallel_streams_share_recount_budget(self):
        streams=[TextStream(('b',str(i),'text',0),'text','upstream','unknown',100) for i in range(8)]
        for stream in streams:
            stream.apply_count(0,0,100,30)
            stream.append('hello',100.1)
        counter=TokenCounter(False)
        counter.tick(streams,100.5)
        self.assertTrue(all(s.counted_revision!=s.revision for s in streams))
        counter.tick(streams,101)
        self.assertTrue(all(s.counted_revision==s.revision for s in streams))

    def test_arbitrary_chunk_boundaries(self):
        value='中文和emoji🙂 '+('def sample(a): return a + 1\n'*100)
        for width in (1,7,123,len(value)):
            stream=TextStream(('b','i','text',0),'text','upstream','gpt-6-luna',100)
            counter=TokenCounter(False)
            for index in range(0,len(value),width):
                stream.append(value[index:index+width],100+index/100)
            stream.ended_at=200
            counter.tick([stream],200)
            self.assertEqual(stream.tokens,count_text(value,'o200k_base')[0])
            self.assertEqual(stream.text,'')
            self.assertTrue(stream.estimated)

    def test_negative_correction_keeps_count(self):
        stream=TextStream(('b','i','text',0),'text','upstream','unknown',100)
        stream.apply_count(5,1,101,0)
        stream.apply_count(3,2,101.2,0)
        self.assertEqual(stream.tokens,3)
        self.assertEqual(stream.corrections,1)
        self.assertIsNone(stream.rate(103.1))

    def test_async_counts_eventually_release_final_buffer(self):
        stream=TextStream(('b','i','text',0),'text','upstream','unknown',100)
        stream.append('hello world',101)
        counter=TokenCounter(True)
        counter.tick([stream],101)
        stream.append(' 中文',102)
        stream.ended_at=103
        deadline=time.monotonic()+3
        while stream.text and time.monotonic()<deadline:
            counter.tick([stream],103)
            time.sleep(.01)
        counter.close()
        self.assertEqual(stream.text,'')
        self.assertEqual(stream.tokens,count_text('hello world 中文','o200k_base')[0])


if __name__=='__main__':
    unittest.main()
