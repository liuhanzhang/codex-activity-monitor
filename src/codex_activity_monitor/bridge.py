"""Original CLI transport plus a bounded, disposable local observation channel."""

from collections import Counter, OrderedDict
import json
import os
from pathlib import Path
import queue
import re
import select
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid

from .events import decode_log, filter_event, BLOCKING_REQUESTS
from .runtime import REAL_CLI
from .observer_permit import PermitExchange

TRACE_FILTER='warn,codex_api::sse=trace,tungstenite::protocol=trace,tungstenite::protocol::frame=off'


def unrelated_diagnostic_prefix(prefix):
    """Only positively identified outbound frame logs cannot contain received deltas."""
    prefix = prefix[:4096]
    if b'tungstenite::protocol' not in prefix:
        return False
    match = re.search(rb'"fields"\s*:\s*\{\s*"message"\s*:\s*"([^"\\]*)', prefix)
    return bool(match and match[1].startswith((b'Sending message ', b'Sending frame ')))


def observes_stdio(args):
    # Desktop's global -c overrides precede the subcommand. A literal
    # "app-server" inside an exec prompt must remain a helper invocation.
    index=0
    command=None
    while index<len(args):
        arg=args[index]
        if arg in ('-c','--config'):
            index+=2
            continue
        if not arg.startswith('-'):
            command=arg
            break
        index+=1
    if command!='app-server' or 'daemon' in args:
        return False
    if '--listen' in args:
        index=args.index('--listen')
        if index+1>=len(args) or args[index+1]!='stdio://':
            return False
    return not any(arg.startswith('--listen=') and arg!='--listen=stdio://' for arg in args)


class Observer:
    def __init__(self,runtime,backend,home,cli,queue_size=128):
        self.runtime,self.backend=runtime,backend
        from .runtime import source_identity
        self.info={'type':'bridge/started','home':home,'cli':str(cli),'bridge_pid':os.getpid(),
                   'implementation':source_identity('bridge','observer_permit','events','usage','tool_labels','runtime','i18n','messages_zh_cn')}
        self.packets=queue.Queue(maxsize=queue_size)
        self.cache=OrderedDict()
        self.cache_sizes={}
        self.cache_bytes=0
        self.dropped=0
        self.unrelated_dropped=0
        self.framing_drops=0
        self.drop_reasons=Counter()
        self.last_send_error=None
        self.send_waits=0
        self.drain_deadline=None
        self.errors=0
        self.sock=None
        self.thread=threading.Thread(target=self.run,daemon=True,name='progress-observer')
        self.stopping=False
        self.queued_bytes=0
        self.queue_lock=threading.Lock()
        self.permits=PermitExchange()
        self.initial_attempt=threading.Event()

    def observing(self):
        return time.monotonic()<self.permits.until

    def offer(self,source,data,at=None):
        if not self.observing():
            self.lose('observation_inactive', framing=True)
            return
        with self.queue_lock:
            if self.queued_bytes+len(data)>8*1024*1024:
                self.dropped+=1
                self.framing_drops+=1
                self.drop_reasons['queue_bytes']+=1
                return
            try:
                self.packets.put_nowait((source,at or time.monotonic_ns(),time.time(),data,self.framing_drops))
                self.queued_bytes+=len(data)
            except queue.Full:
                self.dropped+=1
                self.framing_drops+=1
                self.drop_reasons['queue_full']+=1

    def lose(self,reason,*,framing=False,affects_generation=True):
        with self.queue_lock:
            self.dropped+=1
            self.unrelated_dropped+=not affects_generation
            if framing:
                self.framing_drops+=1
            self.drop_reasons[reason]+=1

    def health(self,kind):
        with self.queue_lock:
            return {'type':kind,'dropped_packets':self.dropped,'parse_errors':self.errors,
                    'unrelated_dropped_packets':self.unrelated_dropped,
                    'drop_reasons':dict(self.drop_reasons),'last_send_error':self.last_send_error,
                    'send_waits':self.send_waits}

    def row(self,direction,event,at=None,wall=None):
        return {'backend':self.backend,'direction':direction,'observed_ns':at or time.monotonic_ns(),
                'observed_timestamp':wall or time.time(),'event':event}

    def remember(self,row):
        event=row['event']
        method=event.get('method')
        params=event.get('params',{})
        item=params.get('item',event.get('item',{}))
        key=None
        if method=='thread/started':
            key=('thread',params.get('thread',{}).get('id'))
        elif method=='thread/tokenUsage/updated':
            key=('usage',params.get('threadId'))
        elif event.get('type') in ('response.completed','response.failed','response.incomplete'):
            key=('response_usage',event.get('response',{}).get('id'))
        elif method in ('turn/started','turn/completed'):
            key=('turn',params.get('threadId'))
            if method=='turn/completed':
                turn_id=params.get('turn',{}).get('id')
                for cached_key,cached in list(self.cache.items()):
                    old=cached['event']
                    old_params=old.get('params',{})
                    old_item=old_params.get('item',old.get('item',{}))
                    old_turn=old_params.get('turnId') or old_item.get('metadata',{}).get('turn_id') or old.get('identity',{}).get('turn_id')
                    if old_turn!=turn_id:
                        continue
                    if cached_key[0] in ('approval','strict_review','review','hook','auth','turn/plan/updated','model/safetyBuffering/updated'):
                        self.forget(cached_key)
                    elif cached_key[0]=='item' and (old.get('method')=='item/started' or old.get('type')=='response.output_item.added'):
                        background=old_item.get('type')=='commandExecution' and old_item.get('processId') and old_item.get('source') in ('unifiedExecStartup','unifiedExecInteraction')
                        if not background or params.get('turn',{}).get('status')!='completed':
                            self.forget(cached_key)
        elif method=='item/started' or event.get('type')=='response.output_item.added':
            key=('item',item.get('id'))
        elif method=='item/completed' or event.get('type')=='response.output_item.done':
            key=('item',item.get('id'))
            item={k:v for k,v in item.items() if k not in ('text','input','arguments','summary','content')}
            event={**event}
            if 'item' in event:
                event['item']=item
            else:
                event['params']={**params,'item':item}
            row={**row,'event':event}
        elif method=='thread/name/updated':
            key=('name',params.get('threadId'))
        elif method in ('thread/settings/updated','model/rerouted'):
            key=('model',params.get('threadId'))
        elif method in BLOCKING_REQUESTS:
            key=('approval',event.get('id'))
        elif method in ('item/autoApprovalReview/started','item/autoApprovalReview/completed'):
            key=('review',params.get('reviewId'))
            strict_key=('strict_review',params.get('threadId'),params.get('turnId'))
            strict=self.cache.get(strict_key,{}).get('event',{}).get('params',{})
            if not strict or strict.get('startedAtMs') in (None,params.get('startedAtMs')):
                self.forget(strict_key)
        elif method=='autoApprovalReview/strictReviewRequired':
            key=('strict_review',params.get('threadId'),params.get('turnId'))
        elif method in ('hook/started','hook/completed'):
            key=('hook',params.get('run',{}).get('id'))
        elif method in ('modelProvider/authRecoveryStarted','modelProvider/authRecoveryCompleted'):
            key=('auth',params.get('threadId'),params.get('turnId'))
        elif method in ('thread/status/changed','thread/goal/updated','thread/goal/cleared',
                         'thread/project/updated','thread/environment/connected','thread/environment/disconnected',
                         'thread/archived','thread/deleted','thread/unarchived','thread/closed','thread/reverted'):
            category='goal' if '/goal/' in method else 'environment' if '/environment/' in method else 'visibility' if method in ('thread/archived','thread/deleted','thread/unarchived') else method
            key=(category,params.get('threadId'))
        elif method in ('turn/plan/updated','model/safetyBuffering/updated'):
            key=(method,params.get('threadId'),params.get('turnId'))
        elif method=='serverRequest/resolved':
            self.forget(('approval',params.get('requestId')))
        elif row['direction']=='protocol_in' and not method:
            self.forget(('approval',event.get('id')))
        if key:
            # Keep terminal metadata too, so a missed end cannot become perpetual work.
            # Reconciliation text is sent once to the live consumer, never cached.
            self.forget(key)
            self.cache[key]=row
            size=len(json.dumps(row,ensure_ascii=False,separators=(',',':')).encode())
            self.cache_sizes[key]=size
            self.cache_bytes+=size
            self.cache.move_to_end(key)
            while len(self.cache)>512 or self.cache_bytes>4*1024*1024:
                self.forget(next(iter(self.cache)))

    def forget(self,key):
        self.cache.pop(key,None)
        self.cache_bytes-=self.cache_sizes.pop(key,0)

    def send(self,row):
        raw=json.dumps(row,ensure_ascii=False,separators=(',',':')).encode()+b'\n'
        return self.send_bytes(raw, requires_permit=True)

    def disconnect(self):
        if self.sock:
            self.sock.close()
        self.sock=None
        self.permits.reset()

    def send_bytes(self,raw,*,requires_permit=False):
        if self.sock is None:
            return False
        deadline=time.monotonic()+1
        try:
            view=memoryview(raw)
            while view:
                # A slow observer is disposable, but a brief scheduler/encoding
                # pause is not a broken connection. Preserve the partial frame;
                # only this worker waits, while producers retain finite limits.
                now=time.monotonic()
                reason=('shutdown_pending' if self.drain_deadline is not None and now>=self.drain_deadline else
                        'send_timeout' if now>=deadline else 'permit_expired' if requires_permit and not self.observing() else None)
                if reason:
                    self.lose(reason)
                    self.disconnect()
                    return False
                try:
                    written=self.sock.send(view)
                    if not written:
                        raise ConnectionError('Observation socket closed')
                    view=view[written:]
                except BlockingIOError:
                    self.send_waits+=1
                    select.select([], [self.sock], [], .05)
            return True
        except (OSError,ValueError) as error:
            self.last_send_error={'type':type(error).__name__,'errno':getattr(error,'errno',None)}
            self.disconnect()
            self.lose('send_error')
            return False

    def connect(self):
        try:
            sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
            sock.settimeout(0.1)
            sock.connect(str(self.runtime/'events.sock'))
            sock.setblocking(False)
            self.sock=sock
            self.permits.reset()
            self.send_bytes(self.permits.request(self.backend))
        except OSError:
            sock.close()
            self.sock=None

    def replay(self):
        self.send(self.row('lifecycle',self.info))
        for row in self.cache.values():
            if not self.send({**row,'replayed':True}):
                return
        self.send(self.row('lifecycle',self.health('bridge/reconnected')))

    def refresh_permit(self):
        try:
            for _ in range(4):
                try:
                    data=self.sock.recv(4096)
                except BlockingIOError:
                    break
                if not data:
                    raise ConnectionError('Observation control closed')
                self.permits.receive(data)
            request=self.permits.request(self.backend)
            if request:
                self.send_bytes(request)
        except (OSError, ValueError, TypeError, UnicodeError):
            self.disconnect()

    def run(self):
        buffers={'stdin':b'','stdout':b'','stderr':b''}
        observed_drops=self.framing_drops
        next_connect=next_heartbeat=0
        active=False
        ever_active=False
        while not self.stopping or not self.packets.empty():
            now=time.monotonic()
            if self.drain_deadline is not None and now>=self.drain_deadline:
                break
            if self.sock is None and now>=next_connect:
                self.connect()
                if self.sock is None:
                    self.initial_attempt.set()
                next_connect=now+1
            if self.sock is not None:
                self.refresh_permit()
                if self.permits.instance is not None:
                    self.initial_attempt.set()
            if not self.observing():
                active=False
                buffers={k:b'' for k in buffers}
                self.cache.clear()
                self.cache_sizes.clear()
                self.cache_bytes=0
                try:
                    _,_,_,data,_=self.packets.get(timeout=0.025)
                    with self.queue_lock:
                        self.queued_bytes-=len(data)
                except queue.Empty:
                    pass
                continue
            if not active:
                if ever_active:
                    self.lose('observation_resumed')
                active=ever_active=True
                self.replay()
                next_heartbeat=0
            if self.sock is not None and now>=next_heartbeat:
                self.send(self.row('lifecycle',self.health('bridge/heartbeat')))
                next_heartbeat=now+2
            try:
                source,at,wall,data,drops=self.packets.get(timeout=0.025)
                with self.queue_lock:
                    self.queued_bytes-=len(data)
            except queue.Empty:
                continue
            if drops!=observed_drops:
                buffers={k:b'' for k in buffers}
                observed_drops=drops
                self.send(self.row('lifecycle',self.health('bridge/gap')))
            buffers[source]+=data
            if len(buffers[source])>10*1024*1024:
                buffers[source]=b''
                self.lose('oversized_observation')
                continue
            lines=buffers[source].split(b'\n')
            buffers[source]=lines.pop()
            for line in lines:
                try:
                    if source=='stderr':
                        event,_=decode_log(line)
                        direction='upstream'
                        if event is None:
                            continue
                    else:
                        event=json.loads(line)
                        direction='protocol_in' if source=='stdin' else 'protocol_out'
                    event=filter_event(direction,event)
                    if event is None:
                        continue
                    row=self.row(direction,event,at,wall)
                    self.remember(row)
                    if self.sock is None:
                        self.lose('offline_event')
                    else:
                        self.send(row)
                except (ValueError,UnicodeError,TypeError,AttributeError):
                    self.errors+=1
                    self.send(self.row('lifecycle',{'type':'bridge/gap','parse_errors':self.errors,'dropped_packets':self.dropped}))
        self.send(self.row('lifecycle',self.health('bridge/stopped')))
        if self.sock:
            self.sock.close()

    def start(self):
        self.thread.start()
        # Before launching the original backend, give an available collector a
        # finite chance to authorize its first events. No transport pump waits.
        self.initial_attempt.wait(.15)

    def close(self):
        self.drain_deadline=time.monotonic()+.75
        self.stopping=True
        self.thread.join(timeout=1)


def forward(args,runtime,actual=None):
    actual=Path(actual or os.environ.get('CODEX_ACTIVITY_MONITOR_REAL_CLI',REAL_CLI)).resolve()
    if actual==Path(sys.argv[0]).resolve():
        raise ValueError('Recursive CLI override')
    if not observes_stdio(args):
        os.execv(str(actual),[str(actual),*args])
    env=dict(os.environ)
    env.update(CODEX_CLI_PATH=str(actual),LOG_FORMAT='json',RUST_LOG=TRACE_FILTER)
    observer=Observer(runtime,uuid.uuid4().hex,env.get('CODEX_HOME',str(Path.home()/'.codex')),actual)
    observer.start()
    try:
        child=subprocess.Popen([str(actual),*args],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                               env=env,start_new_session=True)
    except BaseException:
        observer.close()
        raise
    registration=None
    if (runtime/'app-managed.json').exists():
        from .app_session import process_identity
        from .runtime import atomic_json
        try:
            directory=runtime/'bridges'
            directory.mkdir(mode=0o700,exist_ok=True)
            registration=directory/f'{os.getpid()}.json'
            atomic_json(registration,{'pid':os.getpid(),'identity':process_identity(os.getpid()),
                                      'cli_pid':child.pid,'started_at':time.time(),'observation_protocol':1})
        except OSError:
            registration=None
    def stop(signum,_):
        try:
            os.killpg(child.pid,signum)
        except ProcessLookupError:
            pass
    for sig in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):
        signal.signal(sig,stop)

    def write(dest,data):
        view=memoryview(data)
        while view:
            view=view[os.write(dest.fileno(),view):]

    def pump(source,src,dest):
        buffer=b''
        discarding_line=False
        diagnostic_open=True
        def diagnostic(data):
            nonlocal diagnostic_open
            if diagnostic_open:
                try:
                    write(dest,data)
                except (BrokenPipeError,OSError):
                    diagnostic_open=False
        def is_observation(line):
            target_trace=(b'"target":"tungstenite::protocol' in line or b'"target":"codex_api::sse' in line or b'"target": "tungstenite::protocol' in line or b'"target": "codex_api::sse' in line)
            verbose=(b'"level":"TRACE"' in line or b'"level": "TRACE"' in line or b'"level":"DEBUG"' in line or b'"level": "DEBUG"' in line)
            return target_trace and verbose
        try:
            while True:
                data=os.read(src.fileno(),65536)
                if not data:
                    break
                observed_at=time.monotonic_ns()
                if source!='stderr':
                    write(dest,data)
                    observer.offer(source,data,observed_at)
                    continue
                # Suppress only diagnostics enabled for observation, so Desktop never
                # persists full high-frequency traces. Other stderr remains available.
                if discarding_line:
                    if b'\n' not in data:
                        continue
                    data=data.split(b'\n',1)[1]
                    discarding_line=False
                buffer+=data
                observed_lines=bytearray()
                while b'\n' in buffer:
                    line,buffer=buffer.split(b'\n',1)
                    if is_observation(line):
                        # Frame dumps can be large and have no progress event. Do not
                        # let them compete with received deltas for the finite queue.
                        if any(marker in line for marker in (
                            b'"message":"Received message ',b'"message": "Received message ',
                            b'"message":"SSE event: ',b'"message": "SSE event: ')):
                            observed_lines.extend(line+b'\n')
                    else:
                        diagnostic(line+b'\n')
                if observed_lines:
                    # One transport read may contain hundreds of small messages.
                    # Preserve every line but offer the batch as a single packet.
                    observer.offer('stderr',bytes(observed_lines),observed_at)
                if len(buffer)>10*1024*1024:
                    # Never retain an unbounded diagnostic line.
                    unrelated = unrelated_diagnostic_prefix(buffer)
                    buffer=b''
                    discarding_line=True
                    observer.lose('oversized_unrelated_diagnostic' if unrelated else 'oversized_diagnostic',
                                  affects_generation=not unrelated)
            if buffer and source=='stderr':
                if is_observation(buffer):
                    observer.offer('stderr',buffer+b'\n')
                else:
                    diagnostic(buffer)
        except (BrokenPipeError,OSError):
            # Loss of the protocol consumer should not leave an orphan backend.
            if source=='stdout':
                stop(signal.SIGTERM,None)
        finally:
            if source=='stdin':
                try:
                    dest.close()
                except OSError:
                    pass
    incoming=threading.Thread(target=pump,args=('stdin',sys.stdin.buffer,child.stdin),daemon=True)
    outgoing=[threading.Thread(target=pump,args=(name,src,dest),daemon=True) for name,src,dest in
              [('stdout',child.stdout,sys.stdout.buffer),('stderr',child.stderr,sys.stderr.buffer)]]
    incoming.start()
    for thread in outgoing:
        thread.start()
    code=child.wait()
    for thread in outgoing:
        thread.join(timeout=1)
    observer.close()
    if registration:
        registration.unlink(missing_ok=True)
    if code<0:
        signal.signal(-code,signal.SIG_DFL)
        os.kill(os.getpid(),-code)
    return code
