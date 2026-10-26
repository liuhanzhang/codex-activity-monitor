import io
import time
import unittest
from rich.console import Console
from codex_activity_monitor.engine import Engine
from codex_activity_monitor.events import Event
from codex_activity_monitor.display import render


class DisplayTests(unittest.TestCase):
    def test_future_snapshot_cannot_keep_terminal_activity_live(self):
        engine = Engine(False)
        try:
            engine.ingest(Event('b', 'protocol_out', {'method': 'turn/started',
                'params': {'threadId': 't', 'turn': {'id': 'u'}}}, 100, 0))
            snapshot = engine.snapshot(101, time.time()+60)
            output = io.StringIO()
            Console(file=output, width=240).print(render(snapshot, language='en')[0])
            self.assertIn('Disconnected', output.getvalue())
        finally:
            engine.close()

    def test_partial_command_preview_is_visible_and_remains_planned(self):
        engine=Engine(False)
        try:
            engine.ingest(Event('b','protocol_out',{'method':'turn/started','params':{'threadId':'t','turn':{'id':'u'}}},100,0))
            engine.ingest(Event('b','upstream',{'type':'response.output_item.added','item':{'id':'code',
                'type':'custom_tool_call','name':'exec','metadata':{'turn_id':'u'}}},101,0))
            engine.ingest(Event('b','upstream',{'type':'response.custom_tool_call_input.delta','item_id':'code',
                'delta':'tools.exec_command({cmd:"rg --fi'},102,0))
            engine.tick(102.1)
            snapshot=engine.snapshot(103,time.time())
            output=io.StringIO()
            Console(file=output,width=240).print(render(snapshot,detail=True)[0])
            self.assertIn('准备中 · rg',output.getvalue())
            self.assertNotIn('--fi',output.getvalue())
            self.assertNotIn('exec →',output.getvalue())
            self.assertNotIn('执行工具',output.getvalue())
        finally:
            engine.close()

    def test_tool_name_and_command_are_visible_without_shell_wrapper(self):
        engine=Engine(False)
        try:
            engine.ingest(Event('b','protocol_out',{'method':'turn/started','params':{'threadId':'t','turn':{'id':'u'}}},100,0))
            engine.ingest(Event('b','protocol_out',{'method':'item/started','params':{'threadId':'t','turnId':'u',
                'item':{'id':'cmd','type':'commandExecution','source':'unifiedExecStartup','processId':'123',
                        'command':'/bin/zsh -lc "rg --files src/codex_activity_monitor/tool_labels.py"'}}},101,0))
            output=io.StringIO()
            Console(file=output,width=240).print(render(engine.snapshot(102,time.time()),detail=True)[0])
            text=output.getvalue()
            self.assertIn('运行中 · rg',text)
            self.assertNotIn('tool_labels.py',text)
            self.assertNotIn('--files',text)
            self.assertNotIn('/bin/zsh',text)
        finally:
            engine.close()

    def test_generated_tool_names_render_in_argument_phase(self):
        engine=Engine(False)
        try:
            engine.ingest(Event('b','protocol_out',{'method':'turn/started','params':{'threadId':'t','turn':{'id':'u'}}},100,0))
            engine.ingest(Event('b','upstream',{'type':'response.output_item.added','item':{'id':'code',
                'type':'custom_tool_call','name':'exec','metadata':{'turn_id':'u'}}},101,0))
            engine.ingest(Event('b','upstream',{'type':'response.custom_tool_call_input.delta','item_id':'code',
                'delta':'await tools.mcp__openaiDeveloperDocs__fetch_openai_doc({});'},102,0))
            engine.tick(102.1)
            output=io.StringIO()
            Console(file=output,width=300).print(render(engine.snapshot(103,time.time()))[0])
            self.assertIn('准备中 · openaiDeveloperDocs · fetch_openai_doc',output.getvalue())
            self.assertIn('fetch_openai_doc',output.getvalue())
            self.assertNotIn('mcp__openaiDeveloperDocs__',output.getvalue())
        finally:
            engine.close()

    def test_ephemeral_helpers_hidden_by_default_but_can_be_filtered(self):
        engine=Engine(False)
        try:
            for thread,ephemeral in (('main',False),('internal',True)):
                engine.ingest(Event('b','protocol_out',{'method':'thread/started','params':{'thread':{'id':thread,'name':thread,'ephemeral':ephemeral}}},100,0))
                engine.ingest(Event('b','protocol_out',{'method':'turn/started','params':{'threadId':thread,'turn':{'id':thread+'-turn'}}},101,0))
            snapshot=engine.snapshot(102,time.time())
            self.assertEqual(len(snapshot['threads']),2)
            visual,count=render(snapshot)
            output=io.StringIO();Console(file=output,width=200).print(visual)
            self.assertEqual(count,1)
            self.assertNotIn('internal',output.getvalue())
            self.assertEqual(render(snapshot,thread_filter='internal')[1],1)
        finally:
            engine.close()

    def test_stale_snapshot_hides_old_speed_without_mutating_source(self):
        engine=Engine(False)
        engine.ingest(Event('b','protocol_out',{'method':'turn/started','params':{'threadId':'t','turn':{'id':'u'}}},100,0))
        snapshot=engine.snapshot(101,time.time()-10)
        snapshot['threads'][0]['token_rates']['text']=999.9
        output=io.StringIO()
        Console(file=output,width=200).print(render(snapshot)[0])
        text=output.getvalue()
        self.assertIn('连接中断',text)
        self.assertNotIn('999.9',text)
        self.assertEqual(snapshot['threads'][0]['status'],'inProgress')
        self.assertEqual(snapshot['threads'][0]['token_rates']['text'],999.9)
        engine.close()
