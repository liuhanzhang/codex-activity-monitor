"""Product requirements exercised through the real filter, engine and renderer."""
import io
import json
from pathlib import Path
import time
import unittest

from rich.console import Console
from codex_progress_monitor.bridge import Observer
from codex_progress_monitor.display import render
from codex_progress_monitor.engine import Engine
from codex_progress_monitor.events import Event, ITEM_TYPES, NOTIFICATION_KINDS, filter_event
from codex_progress_monitor.metrics import count_text

ROOT = Path(__file__).resolve().parents[1]


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.engine = Engine(False)
        self.send('turn/started', {'threadId':'t','turn':{'id':'u'}}, 100)

    def tearDown(self):
        self.engine.close()

    def value(self, value, at=101, direction='protocol_out'):
        safe = filter_event(direction, value)
        if safe is not None:
            self.engine.ingest(Event('b', direction, safe, at, 0))

    def send(self, method, params=None, at=101, request=None):
        value = {'method':method,'params':params or {'threadId':'t','turnId':'u'}}
        if request is not None:
            value['id'] = request
        self.value(value, at)

    def item(self, item, at=101, ending=False, turn='u'):
        self.send('item/completed' if ending else 'item/started',
                  {'threadId':'t','turnId':turn,'item':item}, at)

    def view(self, at=104):
        self.engine.tick(at)
        return self.engine.snapshot(at, time.time())['threads'][0]

    def visual(self, at=104):
        self.view(at)
        output = io.StringIO()
        Console(file=output, width=220).print(render(self.engine.snapshot(at,time.time()),detail=True)[0])
        return output.getvalue()

    def command(self, name='cmd', at=101):
        self.item({'id':name,'type':'commandExecution','command':'sleep 10',
                   'source':'unifiedExecStartup','processId':'123'}, at)

    def text(self, item='text', at=101):
        self.item({'id':item,'type':'agentMessage'}, at)
        self.send('item/agentMessage/delta', {'threadId':'t','turnId':'u','itemId':item,'delta':'可见模型文本'}, at+.2)

    def test_all_87_intake_decisions_and_19_type_inventory_match_baseline(self):
        decisions = json.loads((ROOT/'docs/03_event_lifecycle_and_progress_display/event-display-decisions.json').read_text())
        rows = decisions['server_notifications']
        self.assertEqual(len(rows),87)
        self.assertEqual(len({r['method'] for r in rows}),87)
        for row in rows:
            with self.subTest(method=row['method']):
                kept = filter_event('protocol_out', {'method':row['method'],'params':{}})
                self.assertEqual(kept is not None,row['recommendation']['consume']!='忽略')
                self.assertIn('implementation_review',row)
        self.assertEqual(set(NOTIFICATION_KINDS), {r['method'] for r in rows if r['recommendation']['consume']!='忽略'})
        self.assertEqual(ITEM_TYPES, {r['type'] for r in decisions['thread_item_types']})
        self.assertIsNone(filter_event('protocol_out',{'method':'item/futureSecret/delta','params':{'delta':'secret'}}))
        raw=filter_event('protocol_out',{'method':'rawResponseItem/completed','params':{
            'threadId':'t','turnId':'u','item':{'id':'raw','type':'message','content':[{'text':'PRIVATE'}]}}})
        self.assertNotIn('PRIVATE',json.dumps(raw))
        self.value({'type':'response.output_text.delta','item_id':'raw','delta':'未知起点','sequence_number':1},102,'upstream')
        self.value(raw,103)
        self.assertFalse(self.engine.streams)
        self.assertNotIn(('b','raw'),self.engine.items)

    def test_all_19_item_lifecycles_preserve_identity_and_clear_real_activities(self):
        hidden={'userMessage','hookPrompt','functionCallOutput','subAgentActivity','enteredReviewMode','exitedReviewMode'}
        for kind in sorted(ITEM_TYPES):
            with self.subTest(kind=kind):
                item={'id':kind,'type':kind,'status':'inProgress','text':'PRIVATE',
                      'tool':'spawnAgent','server':'srv','receiverThreadIds':['child'],
                      'agentsStates':{'child':{'status':'running','message':'PRIVATE'}},
                      'agentThreadId':'child','kind':'started'}
                self.item(item)
                self.assertIn(('b',kind),self.engine.items)
                self.assertEqual(kind in self.engine.threads[('b','t')].activities,kind not in hidden)
                self.item({**item,'status':'completed','kind':'completed','text':''},102,True)
        view=self.view(104)
        self.assertEqual(view['display_active_count'],0)
        self.assertIsNone(view['generation_rate'])
        self.assertEqual(view['subagents']['child'],'completed')
        self.assertNotIn('PRIVATE',self.visual())

    def test_finished_dispatch_does_not_claim_child_finished_and_sleep_coalesces(self):
        self.item({'id':'dispatch','type':'collabAgentToolCall','tool':'spawnAgent',
                   'receiverThreadIds':['child'],'agentsStates':{'child':{'status':'running'}}})
        self.assertEqual(self.view(102)['display_label'],'分配子任务')
        self.item({'id':'dispatch','type':'collabAgentToolCall','tool':'spawnAgent','status':'completed',
                   'receiverThreadIds':['child'],'agentsStates':{'child':{'status':'running'}}},103,True)
        self.assertIn('子任务 1 项 · 0 项完成',self.visual(105))
        self.item({'id':'wait','type':'sleep'},106)
        self.assertEqual(self.view(107)['display_label'],'处理中')
        self.assertEqual(self.view(110)['display_label'],'等待中')
        self.item({'id':'wait','type':'sleep'},111,True)
        self.assertEqual(self.view(113)['display_active_count'],0)

    def test_11_server_requests_and_dynamic_reply_do_not_supply_execution_proof(self):
        decisions=json.loads((ROOT/'docs/03_event_lifecycle_and_progress_display/event-display-decisions.json').read_text())
        self.assertEqual(len(decisions['server_requests']),11)
        for row in decisions['server_requests']:
            with self.subTest(method=row['method']):
                safe=filter_event('protocol_out',{'method':row['method'],'id':7,
                         'params':{'threadId':'t','turnId':'u','conversationId':'t',
                                   'callId':'call','arguments':{'value':'PRIVATE'},'questions':['PRIVATE'],'accessToken':'PRIVATE'}})
                self.assertEqual(safe is None,row['recommendation']['consume']=='忽略')
                if safe:
                    self.assertNotIn('PRIVATE',json.dumps(safe))
        self.send('item/tool/call',{'threadId':'t','turnId':'u','callId':'client-tool',
                  'tool':'echo','arguments':{'value':'PRIVATE'}},102,12)
        self.assertEqual(self.view()['display_label'],'处理中 · echo')
        self.value({'id':12,'result':{'success':True,'contentItems':[{'text':'PRIVATE'}]}},105,'protocol_in')
        self.assertEqual(self.view(106)['display_label'],'处理中 · echo')
        self.item({'id':'client-tool','type':'dynamicToolCall','tool':'echo','status':'completed','success':True},107,True)
        self.assertEqual(self.view(109)['display_active_count'],0)
        self.assertNotIn('PRIVATE',self.visual(109))

    def test_request_resolution_is_scoped_independent_and_idempotent(self):
        self.command()
        for request in (7,8):
            self.send('item/commandExecution/requestApproval',{'threadId':'t','turnId':'u','itemId':'cmd'},102,request)
        self.assertEqual(self.view()['display_label'],'运行命令 · sleep 10')
        self.send('serverRequest/resolved',{'threadId':'t','requestId':7},105)
        self.assertEqual(len(self.engine.approvals),1)
        self.value({'id':8,'result':{'decision':'decline'}},106,'protocol_in')
        self.assertFalse(self.engine.approvals)
        self.assertTrue(self.engine.threads[('b','t')].activities['cmd'].active)
        self.send('item/commandExecution/requestApproval',{'threadId':'t','turnId':'u','itemId':'cmd'},107,8)
        self.assertFalse(self.engine.approvals)
        self.assertNotIn('审批',self.visual(108))

    def test_blocked_target_does_not_hide_another_actual_tool(self):
        self.item({'id':'file','type':'fileChange','changes':[{'path':'src/a.py','diff':'secret'}]})
        self.send('item/fileChange/requestApproval',{'threadId':'t','turnId':'u','itemId':'file'},102,7)
        self.assertEqual(self.view()['display_label'],'已暂停')
        self.command('other',105)
        view=self.view(106)
        self.assertEqual(view['display_active_count'],1)
        self.assertEqual(view['display_label'],'运行命令 · sleep 10')

    def test_legacy_and_empty_target_requests_never_store_prompts(self):
        self.send('execCommandApproval',{'conversationId':'t','callId':'cmd','command':['secret']},102,7)
        self.assertEqual(self.view()['display_label'],'已暂停')
        self.send('serverRequest/resolved',{'threadId':'t','requestId':7},105)
        self.send('item/permissions/requestApproval',{'threadId':'t','turnId':'u','reason':'PRIVATE'},106,8)
        self.assertEqual(self.view(107)['display_label'],'已暂停')
        self.assertNotIn('PRIVATE',json.dumps(self.engine.snapshot(107)))

    def test_global_login_or_verification_cannot_clear_or_pause_other_work(self):
        self.text()
        self.send('model/verification',{'threadId':'t','turnId':'u','verifications':[{'secret':'PRIVATE'}]},102)
        self.send('account/login/completed',{'loginId':'other','success':True},103)
        self.assertEqual(self.view()['display_label'],'生成回答')
        self.send('mcpServer/elicitation/request',{'threadId':'t','turnId':'u','message':'PRIVATE'},105,9)
        self.send('mcpServer/oauthLogin/completed',{'name':'unrelated','success':True},106)
        self.assertIn(('b',9),self.engine.approvals)
        self.assertNotIn('PRIVATE',self.visual(107))

    def test_auto_reviews_same_target_distinct_results_and_strict_dedup(self):
        for ident,ms in (('r1',1000),('r2',2000)):
            self.send('item/autoApprovalReview/started',{'threadId':'t','turnId':'u','reviewId':ident,
                      'targetItemId':'cmd','startedAtMs':ms,'review':{'status':'inProgress','reason':'PRIVATE'}},102)
        self.assertEqual(self.view()['display_label'],'自动审核中')
        self.assertEqual(self.view()['display_active_count'],2)
        self.send('item/autoApprovalReview/completed',{'threadId':'t','turnId':'u','reviewId':'r1',
                  'startedAtMs':1000,'review':{'status':'approved'}},105)
        self.send('autoApprovalReview/strictReviewRequired',{'threadId':'t','turnId':'u','startedAtMs':1000},106)
        self.assertEqual(self.view(107)['display_active_count'],1)
        self.send('item/autoApprovalReview/completed',{'threadId':'t','turnId':'u','reviewId':'r2',
                  'startedAtMs':2000,'review':{'status':'denied'}},108)
        self.assertEqual(self.view(110)['display_active_count'],0)
        self.assertNotIn('运行命令',self.visual(110))
        self.assertIn('审核未通过',self.visual(110))
        self.assertNotIn('PRIVATE',self.visual(110))

    def test_strict_placeholder_and_completion_without_start_never_invent_duration(self):
        self.send('autoApprovalReview/strictReviewRequired',{'threadId':'t','turnId':'u','startedAtMs':1000},102)
        self.send('item/autoApprovalReview/started',{'threadId':'t','turnId':'u','reviewId':'r',
                  'startedAtMs':1000,'review':{'status':'inProgress'}},103)
        self.assertEqual(self.view()['display_active_count'],1)
        self.send('item/autoApprovalReview/completed',{'threadId':'t','turnId':'u','reviewId':'other',
                  'startedAtMs':2000,'review':{'status':'timedOut'}},105)
        other=next(a for a in self.view(106)['activities'] if a['id']=='review:other')
        self.assertFalse(other['start_known'])
        self.assertFalse(other['active'])

    def test_compaction_and_old_completion_do_not_reopen(self):
        self.item({'id':'compact','type':'contextCompaction'})
        self.assertEqual(self.view()['display_label'],'压缩上下文')
        self.send('thread/compacted',{'threadId':'t','turnId':'u'},105)
        self.item({'id':'compact','type':'contextCompaction'},106,True)
        self.assertEqual(self.view(108)['display_active_count'],0)
        self.assertIsNone(self.view(108)['generation_rate'])

    def test_retry_failure_closes_generation_and_recovery_keeps_new_rate(self):
        self.text()
        self.assertGreater(self.view(102)['generation_rate'],0)
        self.send('error',{'threadId':'t','turnId':'u','willRetry':True,'error':{'message':'PRIVATE'}},103)
        self.assertIsNone(self.view(105)['generation_rate'])
        self.assertEqual(self.view(105)['display_label'],'正在重试')
        self.text('new',106)
        self.assertEqual(self.view(107)['display_label'],'生成回答')
        self.send('error',{'threadId':'t','turnId':'u','willRetry':False},108)
        self.assertEqual(self.view(110)['display_label'],'任务失败')
        self.assertNotIn('PRIVATE',self.visual(110))

    def test_response_end_uses_explicit_identity_without_backend_recency(self):
        self.text()
        self.value({'type':'response.failed','response':{'id':'r','output':[]}},103,'upstream')
        self.assertEqual(self.view()['display_label'],'生成回答')
        self.value({'type':'response.incomplete','turn_id':'u','response':{'id':'r'}},105,'upstream')
        self.assertIsNone(self.view(106)['generation_rate'])
        self.assertEqual(self.view(107)['display_active_count'],0)

    def test_plan_snapshot_and_final_text_have_no_backfilled_speed(self):
        self.item({'id':'plan','type':'plan'})
        self.send('item/plan/delta',{'threadId':'t','turnId':'u','itemId':'plan','delta':'原计划'},102)
        self.send('turn/plan/updated',{'threadId':'t','turnId':'u','plan':[{'step':'查证','status':'completed'},{'step':'实现','status':'inProgress'}]},103)
        self.assertEqual(self.view(103.1)['display_label'],'整理计划')
        self.assertIn('计划 1/2 · 实现',self.visual(103.1))
        self.item({'id':'plan','type':'plan','text':'修改后的最终计划'},104,True)
        stream=self.engine.streams[('b','plan','plan',0)]
        self.view(105)
        self.assertEqual(stream.tokens,count_text('修改后的最终计划','o200k_base')[0])
        self.assertIsNone(self.view(105)['generation_rate'])

    def test_async_parent_mirror_and_injected_items_do_not_generate(self):
        for kind in ('userMessage','hookPrompt','functionCallOutput'):
            self.item({'id':kind,'type':kind,'text':'PRIVATE','content':[{'text':'PRIVATE'}]})
        self.item({'id':'mirror','type':'agentMessage','delivery':'async','text':'PRIVATE'})
        self.send('item/agentMessage/delta',{'threadId':'t','turnId':'u','itemId':'mirror','delta':'PRIVATE'},102)
        self.assertEqual(self.view()['display_active_count'],0)
        self.assertFalse(self.engine.streams)
        self.assertNotIn('PRIVATE',json.dumps(self.engine.snapshot(104)))

    def test_visible_summary_rate_and_output_never_counts(self):
        self.item({'id':'summary','type':'reasoning'})
        self.send('item/reasoning/summaryTextDelta',{'threadId':'t','turnId':'u','itemId':'summary','delta':'摘要文本','summaryIndex':0},102)
        self.send('item/reasoning/summaryPartAdded',{'threadId':'t','turnId':'u','itemId':'summary','summaryIndex':1},102.1)
        before=self.view(103)['generation_rate']
        self.command('output',103)
        self.send('item/commandExecution/outputDelta',{'threadId':'t','turnId':'u','itemId':'output','delta':'PRIVATE'*1000},103.1)
        self.assertAlmostEqual(self.view(103.1)['generation_rate'],before*2/2.1)
        self.assertEqual(len(self.engine.streams),1)
        self.assertNotIn('PRIVATE',self.visual(103.1))

    def test_hooks_affect_waiting_without_creating_activity_rows_or_extra_work(self):
        self.send('hook/started',{'threadId':'t','turnId':'u','run':{'id':'sync','scope':'turn','executionMode':'sync'}},101)
        waiting=self.view(103)
        self.assertEqual(waiting['display_label'],'处理中')
        self.assertEqual(waiting['display_active_count'],1)
        self.assertFalse(next(a for a in waiting['activities'] if a['kind']=='hook')['display_visible'])
        self.command('cmd',104)
        working=self.view(106)
        self.assertTrue(working['display_label'].startswith('运行命令'))
        self.assertEqual(working['display_active_count'],1)
        self.assertNotIn('另 1 项进行中',self.visual(106))
        self.send('hook/completed',{'threadId':'t','turnId':'u','run':{'id':'sync','scope':'turn','executionMode':'sync','status':'completed'}},107)
        self.send('item/completed',{'threadId':'t','turnId':'u','item':{'id':'cmd','type':'commandExecution','status':'completed','exitCode':0}},108)
        self.send('hook/started',{'threadId':'t','turnId':'u','run':{'id':'post','scope':'turn','executionMode':'sync'}},109)
        self.send('hook/completed',{'threadId':'t','turnId':'u','run':{'id':'post','scope':'turn','executionMode':'sync','status':'completed'}},110)
        ended=self.view(110.1)
        self.assertTrue(all(not a['display_visible'] for a in ended['activities'] if a['kind']=='hook'))
        self.assertEqual(ended['display_label'],'处理中')
        self.assertNotIn('刚完成 · 处理中',self.visual(110.1))

    def test_conditional_hook_buffer_startup_and_environment_are_scoped(self):
        self.send('hook/started',{'threadId':'t','turnId':'u','run':{'id':'async','scope':'turn','executionMode':'async'}},101)
        self.assertEqual(self.view()['display_active_count'],0)
        self.send('hook/started',{'threadId':'t','turnId':'u','run':{'id':'sync','scope':'turn','executionMode':'sync'}},105)
        self.assertEqual(self.view(107)['display_label'],'处理中')
        self.send('hook/completed',{'threadId':'t','turnId':'u','run':{'id':'sync','scope':'turn','executionMode':'sync','status':'completed'}},108)
        self.send('model/safetyBuffering/updated',{'threadId':'t','turnId':'u','showBufferingUi':False},109)
        self.assertEqual(self.view(110)['display_active_count'],0)
        self.send('model/safetyBuffering/updated',{'threadId':'t','turnId':'u','showBufferingUi':True},111)
        self.assertEqual(self.view(113)['display_label'],'等待输出')
        self.send('thread/environment/disconnected',{'threadId':'t','environmentId':'env'},114)
        self.assertIn('工作环境连接中断',self.visual(115))
        self.send('mcpServer/startupStatus/updated',{'name':'unrelated','status':'starting'},116)
        self.assertEqual(self.view(118)['display_label'],'等待输出')
        self.item({'id':'mcp','type':'mcpToolCall','server':'srv','tool':'read_docs'},119)
        self.send('mcpServer/startupStatus/updated',{'name':'srv','status':'starting'},120)
        self.assertEqual(self.view(122)['display_label'],'准备工具')
        self.send('item/mcpToolCall/progress',{'threadId':'t','turnId':'u','itemId':'mcp','message':'PRIVATE'},123)
        self.assertEqual(self.view(124)['display_label'],'调用工具 · read_docs')
        self.assertNotIn('PRIVATE',self.visual(124))

    def test_goal_paused_archive_and_completed_turn_are_distinct(self):
        self.send('thread/goal/updated',{'threadId':'t','goal':{'status':'active','objective':'PRIVATE'}},101)
        self.send('turn/completed',{'threadId':'t','turn':{'id':'u','status':'completed'}},102)
        self.assertEqual(self.view()['display_label'],'本轮完成')
        self.send('thread/goal/updated',{'threadId':'t','goal':{'status':'paused'}},105)
        self.assertEqual(self.view(106)['display_label'],'已暂停')
        self.send('thread/archived',{'threadId':'t'},107)
        self.view(108)
        self.assertEqual(render(self.engine.snapshot(108,time.time()))[1],0)
        self.send('thread/unarchived',{'threadId':'t'},109)
        self.assertEqual(render(self.engine.snapshot(109,time.time()))[1],1)

    def test_encoding_switch_recounts_without_turning_old_text_into_live_speed(self):
        self.send('thread/settings/updated',{'threadId':'t','threadSettings':{'model':'gpt-4'}},100.5)
        self.text()
        self.view(102)
        stream=self.engine.streams[('b','text','text',0)]
        self.assertEqual(stream.encoding_name,'cl100k_base')
        self.send('model/rerouted',{'threadId':'t','turnId':'u','toModel':'gpt-4o'},103)
        self.view(103)
        self.assertEqual(stream.encoding_name,'o200k_base')
        self.assertIsNone(stream.rate(103))
        self.send('item/agentMessage/delta',{'threadId':'t','turnId':'u','itemId':'text','delta':'继续'},104)
        self.assertGreater(self.view(104.5)['generation_rate'],0)

    def test_gap_does_not_leave_unconfirmed_old_work_on_the_main_row(self):
        self.text()
        self.value({'type':'bridge/gap'},103,'lifecycle')
        view=self.view(104)
        self.assertEqual(view['display_label'],'状态暂不可用')
        self.assertEqual(view['display_active_count'],0)
        self.assertIsNone(view['generation_rate'])
        self.send('item/agentMessage/delta',{'threadId':'t','turnId':'u','itemId':'text','delta':'继续'},105)
        self.assertEqual(self.view(106)['display_label'],'生成回答')
        self.assertIsNone(self.view(106)['generation_rate'])
        self.item({'id':'text','type':'agentMessage','text':'旧流'},107,True)
        self.text('complete-new-stream',108)
        self.assertGreater(self.view(109)['generation_rate'],0)

    def test_idle_is_not_success_and_preserves_independent_background_work(self):
        self.text()
        self.command('background',102)
        self.send('thread/status/changed',{'threadId':'t','status':{'type':'idle'}},103)
        view=self.view(104)
        self.assertEqual(view['status'],'idle')
        self.assertEqual(view['display_label'],'运行命令 · sleep 10')
        self.assertEqual(view['display_active_count'],1)
        self.assertIsNone(view['generation_rate'])
        self.assertNotIn('任务完成',self.visual(104))

    def test_late_start_and_thread_boundary_cannot_reopen_finished_generation(self):
        self.text()
        self.send('turn/completed',{'threadId':'t','turn':{'id':'u','status':'completed'}},103)
        self.send('turn/started',{'threadId':'t','turn':{'id':'u'}},104)
        self.assertEqual(self.view(105)['display_label'],'任务完成')
        self.send('turn/started',{'threadId':'t','turn':{'id':'v'}},106)
        self.item({'id':'new','type':'agentMessage'},107,turn='v')
        self.send('thread/closed',{'threadId':'t'},108)
        self.send('item/agentMessage/delta',{'threadId':'t','turnId':'v','itemId':'new','delta':'迟到'},109)
        self.assertEqual(self.view(110)['display_active_count'],0)
        self.assertIsNone(self.view(110)['generation_rate'])

    def test_replay_cache_cannot_restore_old_requests_or_strict_review(self):
        observer=Observer(ROOT/'.local/unused','b','/tmp','/bin/echo')
        def remember(method,params,request=None):
            value={'method':method,'params':params}
            if request is not None: value['id']=request
            observer.remember(observer.row('protocol_out',filter_event('protocol_out',value)))
        remember('turn/started',{'threadId':'t','turn':{'id':'u'}})
        remember('item/commandExecution/requestApproval',{'threadId':'t','turnId':'u','itemId':'cmd'},7)
        remember('autoApprovalReview/strictReviewRequired',{'threadId':'t','turnId':'u','startedAtMs':1000})
        remember('item/autoApprovalReview/completed',{'threadId':'t','turnId':'u','reviewId':'r','startedAtMs':1000,'review':{'status':'approved'}})
        self.assertNotIn(('strict_review','t','u'),observer.cache)
        remember('turn/completed',{'threadId':'t','turn':{'id':'u','status':'completed'}})
        remember('turn/started',{'threadId':'t','turn':{'id':'v'}})
        self.assertNotIn(('approval',7),observer.cache)
        self.assertNotIn(('review','r'),observer.cache)
        remember('autoApprovalReview/strictReviewRequired',{'threadId':'t','turnId':'v','startedAtMs':3000})
        remember('item/autoApprovalReview/completed',{'threadId':'t','turnId':'v','reviewId':'previous',
                 'startedAtMs':2000,'review':{'status':'approved'}})
        self.assertIn(('strict_review','t','v'),observer.cache)
