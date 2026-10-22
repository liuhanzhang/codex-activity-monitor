import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock

from codex_progress_monitor.cli import start,stop
from codex_progress_monitor.runtime import runtime_dir,read_snapshot
from codex_progress_monitor.bridge import observes_stdio,Observer
from codex_progress_monitor.timeline_query import query_runtime


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='cpm-',dir='/tmp')
        self.runtime=runtime_dir(self.temp.name)

    def tearDown(self):
        stop(self.runtime)
        self.temp.cleanup()

    def fake(self,source):
        path=self.runtime/'fake-cli'
        path.write_text('#!'+sys.executable+'\n'+source)
        path.chmod(0o700)
        return path

    def history_fixture(self):
        home = self.runtime/'fixture-home'
        path = home/'sessions/probe-t.jsonl'
        path.parent.mkdir(parents=True)
        wall = time.time()-1
        def row(kind, payload):
            return {'type':kind, 'timestamp':wall, 'payload':payload}
        path.write_text('\n'.join(json.dumps(r) for r in [
            row('session_meta', {'id':'t','history_mode':'paginated'}),
            row('event_msg', {'type':'task_started','turn_id':'u','started_at':wall})])+'\n')
        def complete():
            with path.open('a') as stream:
                for record in [row('event_msg', {'type':'item_completed','thread_id':'t','turn_id':'u',
                    'started_at_ms':wall*1000+100,'completed_at_ms':wall*1000+450,
                    'item':{'id':'cmd','type':'CommandExecution','status':'completed','exit_code':0,
                            'aggregated_output':'PRIVATE_HISTORY_RESULT'}}),
                    row('event_msg', {'type':'task_complete','turn_id':'u','completed_at':wall+.5})]:
                    stream.write(json.dumps(record)+'\n')
        return home, complete

    def send_history_work(self, sock, home):
        def send(direction, event):
            sock.sendall(json.dumps({'backend':'history-test','direction':direction,
                'observed_ns':time.monotonic_ns(),'observed_timestamp':time.time(),'event':event}).encode()+b'\n')
        send('lifecycle', {'type':'bridge/started','home':str(home)})
        send('protocol_out', {'method':'turn/started','params':{'threadId':'t','turn':{'id':'u'}}})
        send('protocol_out', {'method':'item/started','params':{'threadId':'t','turnId':'u',
            'item':{'id':'cmd','type':'commandExecution','source':'unifiedExecStartup',
                    'processId':'fixture-only','status':'inProgress'}}})
        send('lifecycle', {'type':'bridge/gap'})
        send('protocol_out', {'method':'turn/completed','params':{'threadId':'t',
            'turn':{'id':'u','status':'completed'}}})
        return send

    def test_history_corrects_without_timeline_request(self):
        home, complete = self.history_fixture()
        start(self.runtime)
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
            sock.connect(str(self.runtime/'events.sock'))
            self.send_history_work(sock,home)
            before = self.wait_snapshot(lambda s:any(t.get('has_unresolved_work') for t in s['threads']))
            self.assertIsNone(before['overview']['generation_rate'])
            complete()
            after = self.wait_snapshot(lambda s:any(any(a['id']=='cmd' and a['status']=='completed'
                for a in t['activities']) for t in s['threads']),timeout=6)
            thread = next(t for t in after['threads'] if t['id']=='t')
            self.assertFalse(thread['has_active_work'])
            self.assertIn('observation_gap',thread['quality'])
            self.assertEqual(after['overview']['generation_rate'],0)
            self.assertNotIn('PRIVATE_HISTORY_RESULT',(self.runtime/'snapshot.json').read_text())
            bundle = query_runtime(self.runtime,{'action':'timeline','scope_id':str(home),'thread_id':'t','turn_id':'u'})
            phase = next(p for p in bundle['phases'] if p['item_id']=='cmd')
            self.assertEqual(phase['status'],'completed')

    def test_history_corrects_restored_unknown_without_starting_new_turn(self):
        home, complete = self.history_fixture()
        start(self.runtime)
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
            sock.connect(str(self.runtime/'events.sock'))
            self.send_history_work(sock,home)
            self.wait_snapshot(lambda s:any(t.get('has_unresolved_work') for t in s['threads']))
            stop(self.runtime)
        complete()
        start(self.runtime)
        after = self.wait_snapshot(lambda s:any(any(a['id']=='cmd' and a['status']=='completed'
            for a in t['activities']) for t in s['threads']),timeout=6)
        self.assertFalse(next(t for t in after['threads'] if t['id']=='t')['has_active_work'])

    def test_history_retires_unrecorded_model_without_timeline_request(self):
        home, _ = self.history_fixture()
        path = home/'sessions/probe-t.jsonl'
        start(self.runtime)
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
            sock.connect(str(self.runtime/'events.sock'))
            def send(direction, event):
                sock.sendall(json.dumps({'backend':'history-test','direction':direction,
                    'observed_ns':time.monotonic_ns(),'observed_timestamp':time.time(),'event':event}).encode()+b'\n')
            send('lifecycle', {'type':'bridge/started','home':str(home)})
            send('protocol_out', {'method':'turn/started','params':{'threadId':'t','turn':{'id':'u'}}})
            send('protocol_out', {'method':'item/started','params':{'threadId':'t','turnId':'u',
                'item':{'id':'thinking','type':'reasoning'}}})
            send('protocol_out', {'method':'turn/completed','params':{'threadId':'t','turn':{'id':'u','status':'completed'}}})
            self.wait_snapshot(lambda s:any(any(a['id']=='thinking' for a in t['activities']) for t in s['threads']))
            with path.open('a') as stream:
                stream.write(json.dumps({'type':'event_msg','timestamp':time.time(),'payload':{
                    'type':'task_complete','turn_id':'u','completed_at':time.time()}})+'\n')
            self.wait_snapshot(lambda s:any(t['id']=='t' and t['status']=='completed' and
                not any(a['id']=='thinking' for a in t['activities']) for t in s['threads']),timeout=6)
        bundle = query_runtime(self.runtime,{'action':'timeline','scope_id':str(home),'thread_id':'t','turn_id':'u'})
        self.assertNotIn('thinking',[p['item_id'] for p in bundle['phases']])
        stop(self.runtime)
        start(self.runtime)
        bundle = query_runtime(self.runtime,{'action':'timeline','scope_id':str(home),'thread_id':'t','turn_id':'u'})
        self.assertNotIn('thinking',[p['item_id'] for p in bundle['phases']])

    def test_blocked_history_read_does_not_block_collector_events(self):
        home, complete = self.history_fixture()
        entered, release = self.runtime/'reader-entered', self.runtime/'reader-release'
        script = '''import time
from pathlib import Path
from codex_progress_monitor.history import HistoryProvider
from codex_progress_monitor.collector import serve
original=HistoryProvider.reconciliation
def blocked(self,*args):
    Path(ENTERED).touch()
    while not Path(RELEASE).exists(): time.sleep(.01)
    return original(self,*args)
HistoryProvider.reconciliation=blocked
serve(Path(RUNTIME))
'''.replace('ENTERED',repr(str(entered))).replace('RELEASE',repr(str(release))).replace('RUNTIME',repr(str(self.runtime)))
        with (self.runtime/'probe.log').open('w') as log:
            process = subprocess.Popen([sys.executable,'-B','-c',script],stdout=log,stderr=log)
            try:
                self.wait_snapshot(lambda s:s.get('running'),timeout=4)
                with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
                    sock.connect(str(self.runtime/'events.sock'))
                    send = self.send_history_work(sock,home)
                    self.wait_snapshot(lambda s:entered.exists(),timeout=4)
                    blocked_at = read_snapshot(self.runtime)['updated_at']
                    send('protocol_out',{'method':'turn/started','params':{'threadId':'t','turn':{'id':'new'}}})
                    send('protocol_out',{'method':'turn/started','params':{'threadId':'other','turn':{'id':'independent'}}})
                    during = self.wait_snapshot(lambda s:s.get('updated_at',0)>blocked_at and
                        any(t['turn_id']=='new' for t in s['threads']) and
                        any(t['id']=='other' and t['status']=='inProgress' for t in s['threads']),timeout=4)
                    self.assertFalse(release.exists())
                    complete()
                    release.touch()
                    after = self.wait_snapshot(lambda s:any(any(a['id']=='cmd' and a['status']=='completed'
                        for a in t['activities']) for t in s['threads']),timeout=6)
                    self.assertEqual(next(t for t in after['threads'] if t['id']=='t')['turn_id'],'new')
            finally:
                release.touch()
                stop(self.runtime)
                process.wait(timeout=5)

    def command(self,actual,*args):
        return [sys.executable,'-m','codex_progress_monitor.cli','--runtime',str(self.runtime),'bridge','--real-cli',str(actual),'--',*args]

    def wait_snapshot(self, predicate, timeout=3):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            snapshot=read_snapshot(self.runtime)
            if predicate(snapshot):
                return snapshot
            time.sleep(.05)
        self.fail('Expected state was not published to the snapshot')

    def test_transport_exit_stderr_and_absent_collector(self):
        actual=self.fake('import sys,json\nraw=sys.stdin.buffer.read()\nsys.stdout.buffer.write(raw)\nsys.stdout.flush()\nprint(json.dumps({"target":"tungstenite::protocol", "level":"TRACE", "fields":{"message":"Received message {}"}}),file=sys.stderr)\nprint("normal diagnostic",file=sys.stderr)\nsys.exit(7)\n')
        payload=b'{"id":1,"method":"test"}\n'+bytes(range(256))*2048
        process=subprocess.run(self.command(actual,'app-server'),input=payload,capture_output=True,timeout=10)
        self.assertEqual(process.stdout,payload)
        self.assertEqual(process.returncode,7)
        self.assertEqual(process.stderr,b'normal diagnostic\n')
        self.assertFalse((self.runtime/'snapshot.json').exists())

    def test_helpers_pass_original_arguments(self):
        actual=self.fake('import sys,json\nprint(json.dumps(sys.argv[1:]))\n')
        result=subprocess.run(self.command(actual,'--version'),capture_output=True,text=True,timeout=5)
        self.assertEqual(json.loads(result.stdout),['--version'])
        self.assertFalse(observes_stdio(['exec','app-server']))
        self.assertFalse(observes_stdio(['app-server','--listen=ws://127.0.0.1:1234']))
        self.assertTrue(observes_stdio(['-c','features.code_mode_host=true','app-server']))

    def test_terminal_cache_has_no_reconciliation_text(self):
        observer=Observer(self.runtime,'b','/tmp','/bin/echo')
        row=observer.row('protocol_out',{'method':'item/completed','params':{'threadId':'t','turnId':'u','item':{'id':'i','type':'agentMessage','text':'PRIVATE_RECONCILIATION'}}})
        observer.remember(row)
        observer.remember(observer.row('protocol_out',{'method':'turn/completed','params':{'threadId':'t','turn':{'id':'u','status':'completed'}}}))
        self.assertNotIn('PRIVATE_RECONCILIATION',json.dumps(list(observer.cache.values())))
        self.assertIn('PRIVATE_RECONCILIATION',json.dumps(row))
        self.assertIn(('turn','t'),observer.cache)

    def test_superseded_connection_cannot_disconnect_new_owner(self):
        start(self.runtime)
        first=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        second=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        try:
            def send(sock,value):
                sock.sendall(json.dumps({'backend':'b','direction':'lifecycle','observed_ns':time.monotonic_ns(),'event':value}).encode()+b'\n')
            first.connect(str(self.runtime/'events.sock'))
            send(first,{'type':'bridge/started'})
            time.sleep(.3)
            second.connect(str(self.runtime/'events.sock'))
            send(second,{'type':'bridge/started'})
            time.sleep(.3)
            closed_at=time.time()
            first.close()
            snapshot=self.wait_snapshot(lambda s: s.get('updated_at',0)>closed_at and 'b' in s.get('backends',{}))
            self.assertTrue(snapshot['backends']['b']['connected'])
            second.close()
            snapshot=self.wait_snapshot(lambda s: s.get('backends',{}).get('b',{}).get('connected') is False)
            self.assertFalse(snapshot['backends']['b']['connected'])
        finally:
            first.close();second.close()

    def test_observer_replays_terminal_state_and_completed_counts(self):
        start(self.runtime)
        observer=Observer(self.runtime,'b','/tmp/owned','/bin/echo')
        observer.start()
        def offer(method,params):
            observer.offer('stdout',json.dumps({'method':method,'params':params}).encode()+b'\n')
        try:
            offer('thread/started',{'thread':{'id':'t','name':'test'}})
            offer('turn/started',{'threadId':'t','turn':{'id':'u'}})
            offer('item/started',{'threadId':'t','turnId':'u','item':{'id':'i','type':'agentMessage'}})
            offer('item/agentMessage/delta',{'threadId':'t','turnId':'u','itemId':'i','delta':'private final'})
            offer('item/completed',{'threadId':'t','turnId':'u','item':{'id':'i','type':'agentMessage','text':'private final'}})
            offer('turn/completed',{'threadId':'t','turn':{'id':'u','status':'completed'}})
            deadline=time.monotonic()+4
            while time.monotonic()<deadline:
                threads=read_snapshot(self.runtime)['threads']
                if threads and threads[0]['activities'][0]['streams'][0]['tokens'] is not None:
                    break
                time.sleep(.1)
            before=read_snapshot(self.runtime)['threads'][0]
            stop(self.runtime)
            start(self.runtime)
            deadline=time.monotonic()+4
            while time.monotonic()<deadline:
                snapshot=read_snapshot(self.runtime)
                if snapshot.get('backends',{}).get('b',{}).get('connected'):
                    break
                time.sleep(.1)
            after=read_snapshot(self.runtime)['threads'][0]
            self.assertEqual(after['phase'],'completed')
            self.assertEqual(after['activities'][0]['streams'][0]['tokens'],before['activities'][0]['streams'][0]['tokens'])
            self.assertNotIn('private final',(self.runtime/'snapshot.json').read_text())
        finally:
            observer.close()

    def test_observation_queue_has_byte_and_packet_limits(self):
        observer=Observer(self.runtime,'b','/tmp','/bin/echo',queue_size=2)
        for _ in range(20):observer.offer('stdout',b'x'*65536)
        self.assertEqual(observer.packets.qsize(),2)
        self.assertEqual(observer.dropped,18)
        observer.offer('stdout',b'x'*(9*1024*1024))
        self.assertLessEqual(observer.queued_bytes,8*1024*1024)

    def test_usage_cache_byte_limit_and_explicit_original_time_replay(self):
        observer=Observer(self.runtime,'b','/tmp','/bin/echo')
        for n in range(90):
            observer.remember(observer.row('upstream',{'type':'response.completed','response':{
                'id':str(n),'item_ids':[str(i)+'x'*128 for i in range(512)],'usage':{'output_tokens':100,'reasoning_tokens':40}}}))
        self.assertLessEqual(observer.cache_bytes,4*1024*1024)
        self.assertLess(len(observer.cache),90)
        self.assertEqual(observer.cache_bytes,sum(observer.cache_sizes.values()))
        fresh=Observer(self.runtime,'b','/tmp','/bin/echo')
        original=fresh.row('protocol_out',{'method':'turn/started','params':{'threadId':'t','turn':{'id':'u'}}},at=123456789,wall=111)
        fresh.remember(original)
        sent=[]
        fresh.send=lambda row:sent.append(row) or True
        with patch('codex_progress_monitor.bridge.socket.socket',return_value=MagicMock()):
            fresh.connect()
        replay=next(row for row in sent if row['direction']=='protocol_out')
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['observed_ns'],123456789)
        self.assertEqual(replay['observed_timestamp'],111)
        self.assertNotIn('replayed',original)

    def test_brief_collector_pause_preserves_stream(self):
        start(self.runtime)
        observer=Observer(self.runtime,'paused','/tmp/owned','/bin/echo')
        observer.start()
        def offer(events):
            observer.offer('stdout',b''.join(json.dumps(e).encode()+b'\n' for e in events))
        try:
            offer([{'method':'turn/started','params':{'threadId':'t','turn':{'id':'u'}}},
                   {'method':'item/started','params':{'threadId':'t','turnId':'u','item':{'id':'i','type':'agentMessage'}}}])
            deadline=time.monotonic()+3
            while not read_snapshot(self.runtime)['threads'] and time.monotonic()<deadline:time.sleep(.05)
            pid=json.loads((self.runtime/'collector.json').read_text())['pid']
            os.kill(pid,signal.SIGSTOP)
            try:
                offer([{'method':'item/agentMessage/delta','params':{'threadId':'t','turnId':'u','itemId':'i','delta':'hello '*20}} for _ in range(600)])
                time.sleep(.35)
            finally:
                os.kill(pid,signal.SIGCONT)
            deadline=time.monotonic()+4
            while time.monotonic()<deadline:
                snapshot=read_snapshot(self.runtime)
                deltas=max((s['deltas'] for t in snapshot['threads'] for a in t['activities'] for s in a['streams']),default=0)
                if deltas==600:break
                time.sleep(.05)
            self.assertEqual(observer.dropped,0,observer.health('test'))
            self.assertGreater(observer.send_waits,0)
            self.assertEqual(deltas,600)
            self.assertEqual(snapshot['threads'][0]['quality'],[])
        finally:
            observer.close()

    def test_stalled_consumer_cannot_block_original_protocol_or_exit(self):
        listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        listener.bind(str(self.runtime/'events.sock'))
        listener.listen(1)
        actual=self.fake('''import json,time
time.sleep(.2)
for _ in range(2000):
    print(json.dumps({'method':'item/agentMessage/delta','params':{'threadId':'t','turnId':'u','itemId':'i','delta':'hello '*20}}))
''')
        try:
            result=subprocess.run(self.command(actual,'app-server'),input=b'',capture_output=True,timeout=5)
            self.assertEqual(result.returncode,0)
            self.assertEqual(len(result.stdout.splitlines()),2000)
            self.assertTrue(all(json.loads(line)['params']['delta']=='hello '*20 for line in result.stdout.splitlines()))
        finally:
            listener.close()

    def test_websocket_diagnostic_burst_preserves_visible_deltas(self):
        start(self.runtime)
        actual=self.fake('''import json,sys,time
def protocol(method,params):
    print(json.dumps({'method':method,'params':params}),flush=True)
def trace(message):
    print(json.dumps({'target':'tungstenite::protocol','level':'TRACE','fields':{'message':message}}),file=sys.stderr,flush=True)
protocol('thread/started',{'thread':{'id':'t','name':'burst','ephemeral':False}})
protocol('turn/started',{'threadId':'t','turn':{'id':'u'}})
time.sleep(.15)
trace('Received message '+json.dumps({'type':'response.output_item.added','item':{'id':'i','type':'message','metadata':{'turn_id':'u'}}}))
trace('Writing frame '+('x'*(4*1024*1024)))
for index in range(600):
    trace('Received message '+json.dumps({'type':'response.output_text.delta','item_id':'i','delta':'hello ','sequence_number':index+1}))
sys.stdin.readline()
trace('Received message '+json.dumps({'type':'response.output_item.done','item':{'id':'i','type':'message','metadata':{'turn_id':'u'},'content':[{'type':'output_text','text':'hello '*600}]}}))
time.sleep(.2)
protocol('turn/completed',{'threadId':'t','turn':{'id':'u','status':'completed'}})
''')
        process=subprocess.Popen(self.command(actual,'app-server'),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            deadline=time.monotonic()+5
            deltas=0
            while time.monotonic()<deadline:
                snapshot=read_snapshot(self.runtime)
                deltas=max((s['deltas'] for t in snapshot['threads'] for a in t['activities'] for s in a['streams']),default=0)
                if deltas==600 and any(t['token_rates'].get('text',0) for t in snapshot['threads']):break
                time.sleep(.05)
            self.assertEqual(deltas,600)
            self.assertTrue(any(t['token_rates'].get('text',0) for t in snapshot['threads']))
            self.assertTrue(all(b.get('dropped_packets',0)==0 and b.get('parse_errors',0)==0 for b in snapshot['backends'].values()))
            process.communicate(input=b'finish\n',timeout=8)
            self.assertEqual(process.returncode,0)
        finally:
            if process.poll() is None:
                process.terminate();process.communicate(timeout=8)

    def test_signal_reaches_owned_process_group(self):
        actual=self.fake('import sys,time\nprint("ready",flush=True)\ntime.sleep(30)\n')
        process=subprocess.Popen(self.command(actual,'app-server'),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.assertEqual(process.stdout.readline(),b'ready\n')
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=5)
        self.assertEqual(process.returncode,-signal.SIGTERM)

    def test_closed_stderr_consumer_does_not_block_protocol(self):
        actual=self.fake('import sys\nsys.stderr.write("diagnostic\\n"*100000);sys.stderr.flush()\nprint("protocol done",flush=True)\n')
        process=subprocess.Popen(self.command(actual,'app-server'),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        process.stderr.close()
        try:
            self.assertEqual(process.communicate(timeout=8)[0],b'protocol done\n')
            self.assertEqual(process.returncode,0)
        finally:
            if process.poll() is None:process.kill();process.wait()

    def test_oversized_diagnostic_does_not_forward_partial_tail(self):
        actual=self.fake('import sys\nsys.stderr.write("x"*(11*1024*1024)+"PRIVATE_TAIL\\nnormal diagnostic\\n");sys.stderr.flush()\nprint("done",flush=True)\n')
        process=subprocess.run(self.command(actual,'app-server'),input=b'',capture_output=True,timeout=10)
        self.assertEqual(process.stdout,b'done\n')
        self.assertEqual(process.stderr,b'normal diagnostic\n')
        self.assertEqual(process.returncode,0)

    def test_collector_restart_and_private_snapshot(self):
        start(self.runtime)
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
            sock.connect(str(self.runtime/'events.sock'))
            now=time.monotonic_ns()
            rows=[{'backend':'test','direction':'protocol_out','observed_ns':now,'event':{'method':'thread/started','params':{'thread':{'id':'t','name':'Known chat','cwd':'/tmp/owned-project'}}}},
                  {'backend':'test','direction':'protocol_out','observed_ns':now,'event':{'method':'turn/started','params':{'threadId':'t','turn':{'id':'u'}}}},
                  {'backend':'test','direction':'upstream','observed_ns':now,'event':{'type':'response.output_item.added','item':{'id':'i','type':'message','metadata':{'turn_id':'u'}}}},
                  {'backend':'test','direction':'upstream','observed_ns':now,'event':{'type':'response.output_text.delta','item_id':'i','delta':'PRIVATE_BODY','sequence_number':1}}]
            sock.sendall(b''.join(json.dumps(r).encode()+b'\n' for r in rows))
            snapshot=self.wait_snapshot(lambda s: any(t['phase']=='text' for t in s['threads']))
            self.assertEqual(snapshot['threads'][0]['phase'],'text')
            self.assertNotIn('PRIVATE_BODY',(self.runtime/'snapshot.json').read_text())
            self.assertEqual((self.runtime/'events.sock').stat().st_mode&0o777,0o600)
        snapshot=self.wait_snapshot(lambda s: any(t['phase']=='unknown' for t in s['threads']))
        self.assertEqual(snapshot['threads'][0]['phase'],'unknown')
        stop(self.runtime)
        start(self.runtime)
        restored=read_snapshot(self.runtime)['threads'][0]
        self.assertEqual(restored['phase'],'unknown')
        self.assertIsNone(restored['token_rates']['text'])
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
            sock.connect(str(self.runtime/'events.sock'))
            def send(value):
                sock.sendall(json.dumps({'backend':'test','direction':'protocol_out','observed_ns':time.monotonic_ns(),'event':value}).encode()+b'\n')
            send({'method':'turn/started','params':{'threadId':'t','turn':{'id':'v'}}})
            fresh=self.wait_snapshot(lambda s: any(t.get('turn_id')=='v' for t in s['threads']))['threads'][0]
            self.assertEqual(fresh['name'],'Known chat')
            self.assertEqual(fresh['cwd'],'/tmp/owned-project')
            self.assertEqual(fresh['turn_id'],'v')
            self.assertIsNone(fresh['generation_rate'])
            send({'method':'thread/started','params':{'thread':{'id':'t','name':'Updated chat','cwd':'/tmp/new-project'}}})
            renamed=self.wait_snapshot(lambda s: any(t['name']=='Updated chat' for t in s['threads']))['threads'][0]
            self.assertEqual(renamed['name'],'Updated chat')
            self.assertEqual(renamed['cwd'],'/tmp/new-project')

    def test_restart_keeps_completed_metrics_when_metadata_replays(self):
        start(self.runtime)
        def send(sock,direction,value):
            sock.sendall(json.dumps({'backend':'b','direction':direction,'observed_ns':time.monotonic_ns(),'event':value}).encode()+b'\n')
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
            sock.connect(str(self.runtime/'events.sock'))
            send(sock,'lifecycle',{'type':'bridge/started','home':'/tmp/owned'})
            send(sock,'protocol_out',{'method':'turn/started','params':{'threadId':'t','turn':{'id':'u'}}})
            send(sock,'upstream',{'type':'response.output_item.done','item':{'id':'i','type':'message','metadata':{'turn_id':'u'},'content':[{'type':'output_text','text':'completed metrics'}]}})
            send(sock,'protocol_out',{'method':'turn/completed','params':{'threadId':'t','turn':{'id':'u','status':'completed'}}})
            before=self.wait_snapshot(lambda s: any(t['phase']=='completed' and any(
                stream['tokens'] is not None for a in t['activities'] for stream in a['streams'])
                for t in s['threads']))['threads'][0]
        stop(self.runtime)
        start(self.runtime)
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
            sock.connect(str(self.runtime/'events.sock'))
            send(sock,'lifecycle',{'type':'bridge/started','home':'/tmp/owned'})
            send(sock,'protocol_out',{'method':'thread/started','params':{'thread':{'id':'t','name':'replayed','ephemeral':True}}})
            after=self.wait_snapshot(lambda s: any(t['name']=='replayed' for t in s['threads']))['threads'][0]
            self.assertEqual(after['phase'],'completed')
            self.assertTrue(after['ephemeral'])
            self.assertEqual(after['name'],'replayed')
            self.assertEqual(after['activities'][0]['streams'][0]['tokens'],before['activities'][0]['streams'][0]['tokens'])
            self.assertIsNone(after['token_rates']['text'])


if __name__=='__main__':
    unittest.main()
