"""Backend-scoped identity and concurrent activity state. No text is serialized."""

from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
import json

from .events import DELTA_KINDS, TOOL_TYPES, Event
from .metrics import TextStream, TokenCounter


PHASES = {'waiting':'等待响应', 'reasoning':'Reasoning', 'text':'生成正文',
          'tool_input':'生成工具参数', 'tool_ready':'工具准备 / 执行未确认', 'tool':'执行工具',
          'approval':'等待审批', 'input':'等待回答', 'unknown':'状态未知',
          'completed':'完成', 'interrupted':'已中断', 'failed':'失败', 'idle':'空闲'}
PRIORITY = {'input':0,'approval':0,'tool_input':1,'text':2,'reasoning':3,'tool':4,'tool_ready':5,'unknown':6}


@dataclass
class Activity:
    id: str
    turn_id: str
    kind: str
    label: str
    started_at: float | None
    status: str = 'inProgress'
    ended_at: float | None = None
    stream_keys: list = field(default_factory=list)
    summary_chars: int = 0
    summary_parts: set = field(default_factory=set)
    output_chars: int = 0
    exit_code: int | None = None
    turn_bound: bool = True

    @property
    def active(self):
        return self.ended_at is None and self.status not in ('completed','failed','declined','interrupted','unknown')


@dataclass
class Thread:
    backend: str
    id: str
    name: str = ''
    cwd: str = ''
    model: str = 'unknown'
    turn_id: str | None = None
    status: str = 'idle'
    wait_started_at: float = 0
    last_progress_at: float = 0
    activities: OrderedDict = field(default_factory=OrderedDict)
    quality: set = field(default_factory=set)
    ephemeral: bool | None = None
    thread_source: str | None = None


class Engine:
    def __init__(self, asynchronous_counts=True):
        self.threads = OrderedDict()
        self.backends = {}
        self.turns = {}
        self.ended_turns = {}
        self.conflicted_turns = set()
        self.items = {}
        self.requests = {}
        self.approvals = {}
        self.streams = OrderedDict()
        self.pending = defaultdict(list)
        self.pending_count = 0
        self.pending_bytes=0
        self.sequence = {}
        self.counts = defaultdict(int)
        self.counter = TokenCounter(asynchronous_counts)
        self.clock=0

    def _thread(self, backend, thread_id):
        key = (backend, thread_id)
        if key not in self.threads:
            model = self.backends.get(backend,{}).get('model','unknown')
            self.threads[key] = Thread(backend,thread_id,model=model,wait_started_at=self.clock,last_progress_at=self.clock)
        return self.threads[key]

    def _metadata(self, backend, metadata):
        if not metadata.get('id'):
            return None
        thread = self._thread(backend,metadata['id'])
        for key in ('name','cwd','model'):
            if metadata.get(key):
                setattr(thread,key,metadata[key])
        if isinstance(metadata.get('ephemeral'),bool):
            thread.ephemeral=metadata['ephemeral']
        if metadata.get('threadSource'):
            thread.thread_source=metadata['threadSource']
        return thread

    def _map_turn(self, backend, turn_id, thread_id):
        if not turn_id or not thread_id:
            return False
        key = (backend,turn_id)
        if key in self.conflicted_turns:
            self.counts['quarantined_events']+=1
            return False
        if key in self.turns and self.turns[key]!=thread_id:
            self.conflicted_turns.add(key)
            owners=(self.turns.pop(key),thread_id)
            for owner in owners:
                thread=self._thread(backend,owner)
                thread.quality.add('identity_conflict')
                if thread.turn_id==turn_id:
                    thread.status='unknown'
                    thread.last_progress_at=self.clock
                    for activity in thread.activities.values():
                        if activity.turn_id==turn_id and activity.active:
                            activity.status='unknown'
                            activity.ended_at=self.clock
                            for stream_key in activity.stream_keys:
                                stream=self.streams.get(stream_key)
                                if stream:
                                    stream.ended_at=self.clock
                                    stream.quality='identity_conflict'
                                    stream.text=''
            self.counts['identity_conflicts'] += 1
            return False
        self.turns[key] = thread_id
        return True

    def _activity(self, thread, item_id, turn_id, kind, label, at):
        if item_id not in thread.activities:
            thread.activities[item_id] = Activity(item_id,turn_id,kind,label,at)
            if at is not None:
                thread.last_progress_at = max(thread.last_progress_at,at)
        return thread.activities[item_id]

    def ingest(self, row):
        event = row if isinstance(row,Event) else Event.from_row(row)
        self.clock=max(self.clock,event.at)
        self.backends.setdefault(event.backend,{'connected':True,'model':'unknown','last_seen':event.at})
        self.backends[event.backend]['last_seen'] = event.at
        if event.direction=='lifecycle':
            self._lifecycle(event)
        elif event.direction in ('protocol_in','protocol_out'):
            self._protocol(event)
        elif event.direction=='upstream':
            self._upstream(event)
        self._resolve(event.backend)

    def _lifecycle(self,event):
        kind = event.data.get('type')
        backend = self.backends[event.backend]
        if isinstance(event.data.get('drop_reasons'),dict):
            backend.update(drop_reasons=event.data['drop_reasons'],last_send_error=event.data.get('last_send_error'),
                           send_waits=event.data.get('send_waits',0))
        if kind=='bridge/heartbeat':
            drops=event.data.get('dropped_packets',0)
            errors=event.data.get('parse_errors',0)
            if drops>backend.get('dropped_packets',0) or errors>backend.get('parse_errors',0):
                kind='bridge/gap'
            backend.update(dropped_packets=drops,parse_errors=errors)
        if kind=='bridge/started':
            backend.update(connected=True,home=event.data.get('home'),cli=event.data.get('cli'))
        elif kind in ('bridge/gap','bridge/reconnected'):
            for thread in self.threads.values():
                if thread.backend==event.backend and (thread.status=='inProgress' or any(a.active for a in thread.activities.values())):
                    thread.quality.add('observation_gap')
            for stream in self.streams.values():
                if stream.key[0]==event.backend and stream.ended_at is None:
                    stream.quality='observation_gap'
                    stream.text=''
            backend['gaps'] = event.data.get('dropped_packets',backend.get('gaps',0)+1)
        elif kind in ('bridge/disconnected','bridge/stopped'):
            backend['connected']=False
            backend['ended_at']=event.at
            for thread in self.threads.values():
                if thread.backend!=event.backend:
                    continue
                if thread.status=='inProgress' or any(a.active for a in thread.activities.values()):
                    thread.status='unknown'
                    thread.quality.add('disconnected')
                    for activity in thread.activities.values():
                        if activity.active:
                            activity.status='unknown'
                            activity.ended_at=event.at
                for stream in self.streams.values():
                    if stream.key[0]==event.backend and stream.ended_at is None:
                        stream.quality='disconnected'
                        stream.ended_at=event.at
                        stream.text=''

    def _protocol(self,event):
        value, backend, at = event.data,event.backend,event.at
        method, params = value.get('method'),value.get('params',{})
        request_key=(backend,value.get('id'))
        if event.direction=='protocol_in':
            if method and value.get('id') is not None:
                self.requests[request_key]=(method,params)
            elif request_key in self.approvals:
                thread_key,item_id = self.approvals.pop(request_key)
                thread=self.threads[thread_key]
                activity=thread.activities.get(item_id)
                decision=value.get('result',{}).get('decision')
                if activity and decision in ('decline','cancel'):
                    activity.status='declined'
                    activity.ended_at=at
                elif activity and activity.kind=='input':
                    activity.ended_at=at
                    activity.status='completed'
                    thread.wait_started_at=at
                elif activity:
                    activity.kind='tool_ready'
                    activity.started_at=at
                    activity.ready_until=at+0.1
            return
        if not method:
            request=self.requests.pop(request_key,None)
            result=value.get('result',{})
            if request and request[0]=='config/read':
                model=result.get('config',{}).get('model')
                if model:
                    self.backends[backend]['model']=model
            if isinstance(result.get('thread'),dict):
                thread=self._metadata(backend,result['thread'])
                if thread and request and request[0]=='thread/start':
                    if thread.ephemeral is None and isinstance(request[1].get('ephemeral'),bool):
                        thread.ephemeral=request[1]['ephemeral']
                    thread.thread_source=thread.thread_source or request[1].get('threadSource')
                if thread and result.get('model'):
                    thread.model=result['model']
                for turn in result['thread'].get('turns',[]):
                    if not self._map_turn(backend,turn.get('id'),thread.id):
                        continue
                    if turn.get('status')=='inProgress':
                        thread.turn_id=turn['id']
                        thread.status='inProgress'
                        thread.wait_started_at=at
                        thread.quality.add('joined_in_progress')
            return
        thread_id=params.get('threadId')
        turn_id=params.get('turnId')
        if method=='thread/started':
            self._metadata(backend,params.get('thread',{}))
            return
        if method=='thread/name/updated' and thread_id:
            self._thread(backend,thread_id).name=params.get('threadName',params.get('name',''))
            return
        if method in ('thread/settings/updated','model/rerouted') and thread_id:
            thread=self._thread(backend,thread_id)
            thread.model=params.get('model',params.get('toModel',thread.model))
            return
        if method in ('turn/started','turn/completed'):
            turn_id=params.get('turn',{}).get('id')
        if not thread_id:
            if method=='serverRequest/resolved':
                self.approvals.pop((backend,params.get('requestId')),None)
            return
        thread=self._thread(backend,thread_id)
        if turn_id and not self._map_turn(backend,turn_id,thread_id):
            return
        if method=='turn/started':
            if thread.turn_id==turn_id and thread.status=='inProgress':
                self.counts['duplicate_lifecycle_ignored']+=1
                return
            for activity in thread.activities.values():
                if activity.turn_id!=turn_id and activity.active and activity.turn_bound:
                    activity.status='unknown'
                    activity.ended_at=at
                    for key in activity.stream_keys:
                        stream=self.streams.get(key)
                        if stream and stream.ended_at is None:
                            stream.ended_at=at
                            stream.quality='missing_termination'
                            stream.text=''
            thread.turn_id,thread.status=turn_id,'inProgress'
            thread.wait_started_at=thread.last_progress_at=at
            thread.quality.clear()
        elif method=='turn/completed':
            self.ended_turns[(backend,turn_id)]=params.get('turn',{}).get('status','unknown')
            if thread.turn_id and thread.turn_id!=turn_id:
                return
            if thread.turn_id==turn_id and thread.status not in ('idle','inProgress','unknown'):
                self.counts['duplicate_lifecycle_ignored']+=1
                return
            thread.turn_id=turn_id
            thread.status=params.get('turn',{}).get('status','unknown')
            thread.last_progress_at=at
            for activity in thread.activities.values():
                if activity.turn_id==turn_id and activity.active:
                    if not activity.turn_bound and thread.status=='completed':
                        continue
                    activity.ended_at=at
                    activity.status='completed' if thread.status=='completed' else thread.status
            for key,stream in self.streams.items():
                if key[0]==backend and self.items.get((backend,key[1]),{}).get('turn_id')==turn_id and stream.ended_at is None:
                    stream.ended_at=at
        elif method in ('item/started','item/completed'):
            self._protocol_item(thread,turn_id,params.get('item',{}),method,at)
        elif method.endswith('/requestApproval') or method.endswith('/requestUserInput'):
            item_id=params.get('itemId') or 'request:'+str(value.get('id'))
            kind='approval' if method.endswith('/requestApproval') else 'input'
            activity=self._activity(thread,item_id,turn_id,kind,kind,at)
            activity.kind,activity.started_at=kind,at
            activity.status='inProgress'
            activity.ended_at=None
            self.approvals[request_key]=((backend,thread_id),item_id)
            thread.last_progress_at=at
        elif method in ('item/agentMessage/delta','item/reasoning/summaryTextDelta'):
            item_id=params.get('itemId')
            kind='summary' if 'reasoning' in method else 'text'
            if not self._delta(event,item_id,kind,params.get('delta',''),params.get('summaryIndex',0),source='protocol'):
                self._queue(event,item_id)
        elif method=='item/commandExecution/outputDelta':
            activity=thread.activities.get(params.get('itemId'))
            if activity and params.get('output_chars',len(params.get('delta',''))):
                activity.output_chars+=params.get('output_chars',len(params.get('delta','')))
                if activity.ended_at is None:
                    activity.kind='tool'
                thread.last_progress_at=at

    def _protocol_item(self,thread,turn_id,item,method,at):
        item_id=item.get('id')
        if not item_id:
            return
        key=(thread.backend,item_id)
        known=self.items.setdefault(key,{'turn_id':turn_id,'type':item.get('type'),'upstream':False})
        known.setdefault('started_at',at if method=='item/started' else None)
        known['turn_id']=turn_id or known.get('turn_id')
        kind=item.get('type')
        if kind in ('agentMessage','reasoning'):
            phase='text' if kind=='agentMessage' else 'reasoning'
            activity=self._activity(thread,item_id,turn_id,phase,PHASES[phase],known.get('started_at'))
        elif kind in TOOL_TYPES:
            label=item.get('command') or '/'.join(str(item.get(k,'')) for k in ('server','tool')).strip('/') or item.get('tool') or kind
            confirmed_process=(kind=='commandExecution' and bool(item.get('processId'))
                               and item.get('source') in ('unifiedExecStartup','unifiedExecInteraction'))
            phase='tool_ready' if kind=='commandExecution' and not confirmed_process else 'tool'
            activity=self._activity(thread,item_id,turn_id,phase,label,known.get('started_at'))
            if confirmed_process:
                activity.turn_bound=False
        else:
            return
        if method=='item/started' and activity.status=='unknown':
            activity.status='inProgress'
            activity.kind=phase
            activity.ended_at=None
        if method=='item/completed':
            if known.get('protocol_completed'):
                self.counts['duplicate_lifecycle_ignored']+=1
                return
            known['protocol_completed']=True
            activity.status=item.get('status','completed')
            activity.ended_at=at
            activity.exit_code=item.get('exitCode')
            thread.last_progress_at=max(thread.last_progress_at,at)
            thread.wait_started_at=at
            for stream_key in activity.stream_keys:
                stream=self.streams[stream_key]
                # Upstream completion remains the authority for upstream text.
                if stream.source=='protocol':
                    final=item.get('text') if stream.kind=='text' else None
                    self._finish_stream(stream,at,final)

    def _queue(self,event,item_id):
        cost=len(json.dumps(event.data,ensure_ascii=False).encode())
        if self.pending_count>=4096 or self.pending_bytes+cost>2*1024*1024:
            self.counts['unassociated_dropped']+=1
            return
        event.pending_bytes=cost
        self.pending[(event.backend,item_id)].append(event)
        self.pending_count+=1
        self.pending_bytes+=cost

    def _resolve(self,backend):
        for key in list(self.pending):
            if key[0]!=backend:
                continue
            known=self.items.get(key,{})
            if (backend,known.get('turn_id')) not in self.turns:
                continue
            rows=self.pending.pop(key)
            self.pending_count-=len(rows)
            self.pending_bytes-=sum(getattr(e,'pending_bytes',0) for e in rows)
            for event in rows:
                self.counts['delayed_mapping']+=1
                if event.direction=='upstream':
                    self._upstream(event)
                else:
                    self._protocol(event)

    def _upstream(self,event):
        value,backend,at=event.data,event.backend,event.at
        kind=value.get('type')
        if kind in ('response.completed','response.failed','response.incomplete'):
            model=value.get('response',{}).get('model')
            if model:
                self.backends[backend]['model']=model
            return
        if kind in ('response.output_item.added','response.output_item.done'):
            item=value.get('item',{})
            item_id=item.get('id')
            if not item_id:
                return
            key=(backend,item_id)
            known=self.items.setdefault(key,{'started_at':at if kind.endswith('.added') else None})
            metadata=item.get('metadata',{})
            known.update(type=item.get('type'),upstream=True)
            if metadata.get('turn_id'):
                known['turn_id']=metadata['turn_id']
            turn_id=known.get('turn_id')
            if metadata.get('thread_id') and not self._map_turn(backend,turn_id,metadata['thread_id']):
                return
            if (backend,turn_id) in self.conflicted_turns:
                self.counts['quarantined_events']+=1
                return
            thread_id=self.turns.get((backend,turn_id))
            if not thread_id:
                self._queue(event,item_id)
                return
            thread=self._thread(backend,thread_id)
            if kind.endswith('.done') and known.get('upstream_completed'):
                self.counts['duplicate_lifecycle_ignored']+=1
                return
            item_type=item.get('type')
            phases={'message':'text','reasoning':'reasoning','custom_tool_call':'tool_input',
                    'function_call':'tool_input','web_search_call':'tool','image_generation_call':'tool'}
            phase=phases.get(item_type)
            if phase is None:
                return
            terminal=self.ended_turns.get((backend,turn_id))
            if kind.endswith('.added') and terminal is not None:
                known['started_at']=None
                self.counts['late_terminal_items']+=1
                return
            label=item.get('name') or ('工具参数' if phase=='tool_input' else PHASES[phase])
            activity=self._activity(thread,item_id,turn_id,phase,label,known['started_at'])
            if kind.endswith('.added'):
                if activity.status=='unknown':
                    activity.status='inProgress'
                    activity.ended_at=None
                # A new model item means the previous foreground exec has returned.
                if phase in ('reasoning','text'):
                    for previous in thread.activities.values():
                        if previous.id!=item_id and previous.turn_id==turn_id and previous.kind=='reasoning' and previous.active:
                            previous.ended_at=at
                            previous.status='unknown'
                            thread.quality.add('missing_item_end')
                        if previous.id!=item_id and previous.turn_id==turn_id and previous.kind=='tool_ready' and previous.label=='exec':
                            previous.ended_at=at
                            previous.status='completed'
                return
            known['ended_at']=at
            known['upstream_completed']=True
            if phase=='tool_input' and terminal is None:
                activity.kind='tool_ready'
                activity.started_at=at
            else:
                activity.ended_at=at
                activity.status=terminal or item.get('status','completed')
            # If the collector rejoined after the last delta, the authoritative
            # completed item can still supply a final count, never a live rate.
            finals=[]
            if phase=='text':
                finals=[('text',n,p.get('text','')) for n,p in enumerate(item.get('content',[])) if p.get('type')=='output_text']
            elif phase=='tool_input':
                finals=[('tool_input',0,item.get('input',item.get('arguments')))]
            elif phase=='reasoning':
                finals=[('summary',n,p.get('text','')) for n,p in enumerate(item.get('summary',[]))]
            for stream_kind,part,final in finals:
                stream_key=(backend,item_id,stream_kind,part)
                if final is not None and stream_key not in self.streams:
                    stream=TextStream(stream_key,stream_kind,'upstream',thread.model,at,quality='reconciled')
                    self.streams[stream_key]=stream
                    activity.stream_keys.append(stream_key)
            for stream_key in activity.stream_keys:
                stream=self.streams[stream_key]
                part=stream_key[-1]
                final=None
                if stream.kind=='text':
                    content=item.get('content',[])
                    if part<len(content) and content[part].get('type')=='output_text':
                        final=content[part].get('text','')
                elif stream.kind=='tool_input':
                    final=item.get('input',item.get('arguments'))
                elif stream.kind=='summary':
                    summary=item.get('summary',[])
                    if part<len(summary):
                        final=summary[part].get('text','')
                self._finish_stream(stream,at,final)
            thread.last_progress_at=max(thread.last_progress_at,at)
            thread.wait_started_at=at
        elif kind in DELTA_KINDS:
            item_id=value.get('item_id')
            part=value.get('summary_index',value.get('content_index',0))
            if not self._delta(event,item_id,DELTA_KINDS[kind],value.get('delta',''),part,
                               source='upstream',sequence=value.get('sequence_number')):
                self._queue(event,item_id)
        elif kind=='response.reasoning_summary_part.added':
            known=self.items.get((backend,value.get('item_id')), {})
            thread_id=self.turns.get((backend,known.get('turn_id')))
            if thread_id:
                activity=self._thread(backend,thread_id).activities.get(value.get('item_id'))
                if activity:
                    activity.summary_parts.add(value.get('summary_index',0))

    def _delta(self,event,item_id,kind,delta,part,source,sequence=None):
        known=self.items.get((event.backend,item_id),{})
        turn_id=known.get('turn_id')
        if (event.backend,turn_id) in self.conflicted_turns:
            self.counts['quarantined_events']+=1
            return True
        thread_id=self.turns.get((event.backend,turn_id))
        if not thread_id:
            return False
        thread=self._thread(event.backend,thread_id)
        if (event.backend,turn_id) in self.ended_turns or thread.status not in ('inProgress','idle'):
            self.counts['late_terminal_deltas']+=1
            return True
        key=(event.backend,item_id,kind,part)
        if source=='protocol' and known.get('upstream') and key not in self.streams:
            return True
        stream=self.streams.get(key)
        if stream and (stream.source!=source or stream.ended_at is not None):
            self.counts['mirrors_ignored']+=1
            return True
        if sequence is not None:
            last,seen=self.sequence.setdefault(key,(-1,set()))
            if sequence in seen:
                self.counts['duplicates_ignored']+=1
                return True
            if sequence<last:
                self.counts['out_of_order']+=1
                thread.quality.add('out_of_order')
                if stream:
                    stream.quality='out_of_order'
                return True
            seen.add(sequence)
            if len(seen)>2048:
                seen.intersection_update(range(max(0,sequence-4096),sequence+1))
            self.sequence[key]=(sequence,seen)
        phase='reasoning' if kind=='summary' else kind
        activity=self._activity(thread,item_id,turn_id,phase,PHASES[phase],known.get('started_at',event.at))
        if stream is None:
            stream=TextStream(key,kind,source,thread.model,known.get('started_at') or event.at)
            self.streams[key]=stream
            activity.stream_keys.append(key)
            if thread.quality:
                stream.quality='observation_gap'
        stream.append(delta,event.at)
        if delta:
            thread.last_progress_at=max(thread.last_progress_at,event.at)
            if kind=='summary':
                activity.summary_chars+=len(delta)
                activity.summary_parts.add(part)
        return True

    def _finish_stream(self,stream,at,final=None):
        if getattr(stream,'finalized',False):
            return
        stream.ended_at=at
        if final is not None:
            stream.finalized=True
            if stream.text!=final and stream.counted_revision!=stream.revision:
                stream.quality='reconciled'
            elif stream.text!=final and stream.text:
                stream.quality='reconciled'
            if len(final.encode())<=8*1024*1024:
                stream.text=final
                stream.chars=len(final)
                stream.utf8_bytes=len(final.encode())
                stream.revision+=1
                if stream.quality not in ('complete','reconciled'):
                    stream.quality='reconciled'
            else:
                stream.quality='content_limit'
                stream.text=''

    def tick(self,now):
        for key in list(self.pending):
            kept=[e for e in self.pending[key] if now-e.at<10]
            self.counts['unassociated_expired']+=len(self.pending[key])-len(kept)
            self.pending_count-=len(self.pending[key])-len(kept)
            self.pending_bytes-=sum(getattr(e,'pending_bytes',0) for e in self.pending[key] if now-e.at>=10)
            if kept:
                self.pending[key]=kept
            else:
                del self.pending[key]
        # Retain recent finished results, release old identity and text state.
        for key,thread in list(self.threads.items()):
            if thread.status!='inProgress' and not any(a.active for a in thread.activities.values()) and now-thread.last_progress_at>600:
                self.threads.pop(key)
        alive_items={(t.backend,a.id) for t in self.threads.values() for a in t.activities.values()}
        for key in list(self.streams):
            if (key[0],key[1]) not in alive_items:
                self.streams.pop(key,None)
                self.sequence.pop(key,None)
        for key in list(self.items):
            if key not in alive_items and key not in self.pending:
                self.items.pop(key,None)
        for thread in self.threads.values():
            terminal=[a for a in thread.activities.values() if not a.active]
            for activity in terminal[:-20]:
                thread.activities.pop(activity.id,None)
        alive_turns={(t.backend,t.turn_id) for t in self.threads.values()} | {(t.backend,a.turn_id) for t in self.threads.values() for a in t.activities.values()}
        pending_turns={(b,known.get('turn_id')) for (b,i),known in self.items.items() if (b,i) in self.pending}
        self.turns={k:v for k,v in self.turns.items() if k in alive_turns or k in pending_turns}
        self.ended_turns={k:v for k,v in self.ended_turns.items() if k in alive_turns or k in pending_turns}
        self.conflicted_turns.intersection_update(alive_turns | pending_turns)
        if len(self.requests)>512:
            self.requests=dict(list(self.requests.items())[-512:])
        self.approvals={k:v for k,v in self.approvals.items() if v[0] in self.threads}
        for backend in list(self.backends):
            if not self.backends[backend].get('connected') and now-self.backends[backend].get('ended_at',now)>600:
                self.backends.pop(backend,None)
        if sum(len(s.text.encode()) for s in self.streams.values())>32*1024*1024:
            for stream in sorted(self.streams.values(),key=lambda s:len(s.text),reverse=True):
                if not stream.text:
                    continue
                stream.text=''
                stream.quality='memory_limit'
                if sum(len(s.text) for s in self.streams.values())<16*1024*1024:
                    break
        self.counter.tick(list(self.streams.values()),now)

    def snapshot(self,now,wall_time=None):
        views=[]
        for thread in self.threads.values():
            backend=self.backends.get(thread.backend,{})
            active=[a for a in thread.activities.values() if a.active]
            def phase(activity):
                return activity.kind
            active.sort(key=lambda a:(PRIORITY.get(phase(a),9),-(a.started_at or 0)))
            primary=active[0] if active else None
            main_phase=phase(primary) if primary else 'waiting' if thread.status=='inProgress' else thread.status
            if not backend.get('connected',True) and thread.status=='inProgress':
                main_phase='unknown'
            since=primary.started_at if primary else thread.wait_started_at
            details=[]
            for activity in reversed(list(thread.activities.values())):
                end=activity.ended_at
                details.append({'id':activity.id,'turn_id':activity.turn_id,'kind':phase(activity),
                                'label':str(activity.label)[:160], 'status':activity.status,
                                'active':activity.active, 'duration_seconds':max(0,(end or now)-(activity.started_at or now)),
                                'turn_bound':activity.turn_bound,
                                'start_known':activity.started_at is not None,'summary_chars':activity.summary_chars,
                                'summary_parts':len(activity.summary_parts), 'output_chars':activity.output_chars,
                                'exit_code':activity.exit_code,
                                'streams':[self.streams[k].view(now) for k in activity.stream_keys if k in self.streams]})
            rates={'text':0.0,'tool_input':0.0}
            seen_rates=set()
            unavailable_rates=set()
            quality=set(thread.quality)
            for activity in thread.activities.values():
                if activity.turn_id==thread.turn_id:
                    for key in activity.stream_keys:
                        stream=self.streams.get(key)
                        if stream and stream.quality!='complete':
                            quality.add(stream.quality)
            for activity in active:
                for key in activity.stream_keys:
                    stream=self.streams.get(key)
                    if stream and stream.kind in rates and stream.ended_at is None:
                        rate=stream.rate(now)
                        if rate is not None:
                            rates[stream.kind]+=rate
                            seen_rates.add(stream.kind)
                        else:
                            unavailable_rates.add(stream.kind)
            views.append({'backend':thread.backend,'id':thread.id,'name':thread.name or thread.id[:8],
                          'ephemeral':thread.ephemeral,'thread_source':thread.thread_source,
                          'home':backend.get('home'),'host':'local',
                          'cwd':thread.cwd,'model':thread.model,'turn_id':thread.turn_id,'status':thread.status,
                          'phase':main_phase,'phase_label':PHASES.get(main_phase,main_phase),
                          'phase_seconds':max(0,now-since) if (primary is not None or thread.status=='inProgress') and since else None,
                          'has_active_work':bool(active) or thread.status=='inProgress',
                          'active_count':len(active),'primary_activity_id':primary.id if primary else None,
                          'last_progress_seconds':max(0,now-thread.last_progress_at) if thread.last_progress_at else None,
                          'token_rates':{k:v if k in seen_rates and k not in unavailable_rates else None for k,v in rates.items()},
                          'quality':sorted(quality),'connected':backend.get('connected',True),
                          'activities':details})
        by_identity={}
        for view in views:
            identity=(view.get('home') or view['backend'],view['id'])
            previous=by_identity.get(identity)
            rank=lambda t:(t['connected'],t['status']=='inProgress',-(t['last_progress_seconds'] or 0))
            if previous is None or rank(view)>rank(previous):
                by_identity[identity]=view
            if previous and previous['connected'] and view['connected'] and previous['status']=='inProgress' and view['status']=='inProgress':
                chosen=by_identity[identity]
                chosen['quality']=sorted(set(chosen['quality']) | {'multiple_active_backends'})
        views=list(by_identity.values())
        views.sort(key=lambda t:(not t['has_active_work'],t['last_progress_seconds'] or 0))
        return {'schema_version':1,'updated_at':wall_time,'clock':now,'threads':views,
                'backends':self.backends,'unassociated_events':self.pending_count,'counters':dict(self.counts)}

    def close(self):
        self.counter.close()
