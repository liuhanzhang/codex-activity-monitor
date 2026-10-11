import io
import time
import unittest
from rich.console import Console
from codex_progress_monitor.engine import Engine
from codex_progress_monitor.events import Event
from codex_progress_monitor.display import render


class DisplayTests(unittest.TestCase):
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
        self.assertIn('观测中断',text)
        self.assertNotIn('999.9',text)
        self.assertEqual(snapshot['threads'][0]['status'],'inProgress')
        self.assertEqual(snapshot['threads'][0]['token_rates']['text'],999.9)
        engine.close()
