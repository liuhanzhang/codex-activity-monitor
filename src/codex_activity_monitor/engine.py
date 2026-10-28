"""Backend-scoped identity and activity state. Model bodies stay out of snapshots."""

from collections import OrderedDict, defaultdict
import json
import time

from .events import DELTA_KINDS, TOOL_TYPES, Event
from .progress_state import handle_protocol, resolve_request, presentation
from .metrics import TextStream, TokenCounter
from .overview import overview, annotate_parents
from .menu_presentation import menu_presentation
from .agent_relations import raw_coordination, SPAWN_OPERATIONS, thread_identity
from .usage import UsageBook
from .state import Activity, Thread
from .identity_budget import IdentityBudget, observation_identities
from .history_contract import HistorySelection
from .reconciliation import apply_history
from .i18n import PHASE_KEYS, phase_message, render_message, tr
from .tool_labels import code_tool_names, command_name, command_previews, command_summary, file_edit_preview, is_code_exec, is_command_tool, is_patch_tool, tool_display, tool_label, tool_name


PHASES = {phase: render_message(phase_message(phase)) for phase in PHASE_KEYS}
PRIORITY = {'tool_input':0,'text':1,'plan':1,'reasoning':2,'tool':3,'compaction':4,
            'review':5,'auth':6,'retry':6,'buffering':7,'hook':7,'sleep':8,
            'input':9,'approval':9,'tool_ready':10,'unknown':11}


class Engine:
    def __init__(self, asynchronous_counts=True, timeline=None):
        self.timeline = timeline
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
        self.pending_ends = {}
        self.pending_count = 0
        self.pending_bytes=0
        self.sequence = {}
        self.counts = defaultdict(int)
        self.counter = TokenCounter(asynchronous_counts)
        self.clock=0
        self.resolved_requests = OrderedDict()
        self.usage = UsageBook()
        self.history_versions = {}
        self.identity_budget = IdentityBudget()
        self.identity_gap = False

    def history_requests(self):
        requests = []
        retained = defaultdict(set)
        if self.timeline:
            for phase in self.timeline.phases.values():
                if phase.get('turn_id') and phase.get('item_id'):
                    retained[(phase['scope_id'], phase['thread_id'])].add((phase['turn_id'], phase['item_id']))
        for thread in self.threads.values():
            if thread.context_only:
                continue
            scope = self.backends.get(thread.backend, {}).get('home')
            if not isinstance(scope, str):
                continue
            turns = {a.turn_id for a in thread.activities.values() if a.turn_id}
            if thread.turn_id:
                turns.add(thread.turn_id)
            items = {(a.turn_id, a.id) for a in thread.activities.values() if a.turn_id}
            items.update(pair for pair in retained[(scope, thread.id)] if pair[0] in turns)
            requests.append(HistorySelection(scope, thread.id, frozenset(turns),
                thread.status in ('inProgress', 'unknown') or
                any(a.active or a.unresolved for a in thread.activities.values()), frozenset(items)))
        return requests

    def restore_unresolved(self, rows, updated_at):
        """Restore identities, never old live speed or a fabricated observation."""
        restored = set()
        for row in rows:
            if row.get('status') != 'unknown' or not row.get('home') or not row.get('turn_id'):
                continue
            backend = row['backend']
            self.backends.setdefault(backend, {'connected': False, 'home': row['home']})
            thread = self._thread(backend, row['id'])
            thread.name, thread.cwd = row.get('name') or '', row.get('cwd') or ''
            thread.ephemeral = row.get('ephemeral') if isinstance(row.get('ephemeral'), bool) else None
            thread.thread_source = row.get('thread_source')
            thread.visible = row.get('visible') is not False
            if row.get('parent_source') in ('session_header', 'spawn_call'):
                thread.parent_thread_id = row.get('parent_thread_id')
                thread.parent_source = row['parent_source']
            thread.parent_quality.update(row.get('parent_quality', []))
            thread.turn_id, thread.status = row['turn_id'], 'unknown'
            thread.turn_start_observed = False
            thread.quality.update(row.get('quality', []))
            thread.quality.add('observation_gap')
            age = row.get('last_progress_seconds')
            thread.history_progress_ms = (updated_at - (age or 0)) * 1000 if updated_at else None
            for item in row.get('activities', []):
                if item.get('status') != 'unknown':
                    continue
                activity = Activity(item['id'], item.get('turn_id') or thread.turn_id,
                                    item['kind'], item.get('label') or '', None,
                                    status='unknown', observation_lost=True,
                                    turn_bound=item.get('turn_bound', True), item_type=item.get('item_type'))
                thread.activities[activity.id] = activity
            restored.add((row['home'], row['id']))
        return restored

    def apply_history(self, summary):
        apply_history(self, summary)

    def _thread(self, backend, thread_id):
        key = (backend, thread_id)
        if key not in self.threads:
            model = self.backends.get(backend,{}).get('model','unknown')
            self.threads[key] = Thread(backend,thread_id,model=model,wait_started_at=self.clock,last_progress_at=self.clock)
            # A resumed bridge deliberately drops its cache. The reconnect gap
            # can therefore precede the first thread identity; retain that gap
            # until a subsequently observed turn/started establishes a baseline.
            if self.backends.get(backend,{}).get('gap_at') is not None:
                self.threads[key].quality.add('observation_gap')
                self.threads[key].turn_start_observed = False
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
        if key not in self.turns and self.identity_budget.contains('turn', backend, turn_id):
            self.counts['late_terminal_items'] += 1
            return False
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
        thread.context_only = False
        if item_id not in thread.activities:
            thread.activities[item_id] = Activity(item_id,turn_id,kind,label,at)
            thread.activities[item_id].last_progress_at = at or 0
            if at is not None:
                thread.last_progress_at = max(thread.last_progress_at,at)
        return thread.activities[item_id]

    def _tool_metadata(self, activity, item):
        activity.coordination_action = raw_coordination(item).get('call_operation', activity.coordination_action)
        # Model input completion can arrive after canonical patch execution.
        if not (activity.item_type == 'fileChange' and item.get('type') in ('custom_tool_call','function_call')):
            activity.item_type = item.get('type',activity.item_type)
        activity.call_id = item.get('call_id',activity.call_id)
        activity.server = item.get('server',activity.server)
        activity.files = item.get('changes',activity.files)
        activity.file_count = item.get('file_count',activity.file_count or len(activity.files))
        activity.receiver_thread_ids = item.get('receiverThreadIds',activity.receiver_thread_ids)
        activity.agents_states = item.get('agentsStates',activity.agents_states)
        name = tool_name(item)
        if item.get('type') == 'commandExecution':
            sources = {'unifiedExecStartup': 'exec_command', 'unifiedExecInteraction': 'write_stdin'}
            name = name or sources.get(item.get('source'))
            if item.get('command'):
                activity.command_name = item.get('command_name') or command_name(item['command'])
                activity.command = command_summary(item['command'])
        if isinstance(item.get('appContext'), dict):
            activity.app_context.update(item['appContext'])
        activity.tool_name = name or activity.tool_name or ('exec_command' if item.get('type') == 'commandExecution' else item.get('type'))
        self._tool_label(activity)

    def _tool_label(self, activity, code=None):
        if code is not None:
            if is_code_exec(activity.tool_name):
                activity.tool_names = code_tool_names(code)
            activity.command_previews = command_previews(activity.tool_name, code)
            activity.file_edit_preview = file_edit_preview(activity.tool_name, code)
        label = tool_label(activity.tool_name, activity.tool_names)
        if activity.command:
            label += ' · ' + activity.command
        elif activity.command_previews:
            label += tr('diagnostic.planned', commands=' | '.join(p['command'] for p in activity.command_previews))
        activity.label = label

    def _assign_parent(self, parent, child_id):
        if not thread_identity(child_id) or child_id == parent.id:
            return
        child = self._thread(parent.backend, child_id)
        if child.thread_source == 'user' or (child.parent_thread_id and child.parent_thread_id != parent.id):
            child.parent_quality.add('parent_identity_conflict')
            return
        child.parent_thread_id, child.parent_source = parent.id, 'spawn_call'
        child.thread_source = 'subagent'
        parent.subagents.setdefault(child_id, 'spawn_agent')

    def ingest(self, row):
        event = row if isinstance(row,Event) else Event.from_row(row)
        self.clock=max(self.clock,event.at)
        if self.identity_budget.sealed:
            self._identity_budget_gap()
            self.counts['identity_budget_dropped'] += 1
            return
        if self.identity_budget.contains('backend', event.backend, event.backend):
            self.counts['retired_backend_events'] += 1
            return
        try:
            admitted = all(self.identity_budget.reserve(kind, event.backend, identity)
                           for kind, identity in observation_identities(event))
        except ValueError:
            self.counts['invalid_observation_identity'] += 1
            return
        if not admitted:
            self._identity_budget_gap()
            self.counts['identity_budget_dropped'] += 1
            return
        self.backends.setdefault(event.backend,{'connected':True,'model':'unknown','last_seen':event.at})
        self.backends[event.backend]['last_seen'] = event.at
        if event.direction=='lifecycle':
            self._lifecycle(event)
        elif event.direction in ('protocol_in','protocol_out'):
            self._protocol(event)
        elif event.direction=='upstream':
            self._upstream(event)
        self._resolve(event.backend)
        self.usage.ingest(event,self)
        self._observe_timeline(event)

    def _identity_budget_gap(self, at=None):
        if self.identity_gap:
            return
        self.identity_gap = True
        for backend in self.backends:
            gap = Event(backend, 'lifecycle', {'type': 'bridge/gap', 'reason': 'identity_budget'},
                        self.clock if at is None else at, time.time())
            self._lifecycle(gap)
            self._observe_timeline(gap)
        for thread in self.threads.values():
            thread.quality.add('identity_budget')

    def _observe_timeline(self,event):
        if self.timeline is not None:
            try:
                self.timeline.observe(event,self)
            except (ValueError,TypeError,KeyError,AttributeError):
                self.counts['timeline_projection_error']+=1

    def _lifecycle(self,event):
        kind = event.data.get('type')
        backend = self.backends[event.backend]
        backend['last_observed_at'] = event.at
        old_drops = backend.get('dropped_packets', 0)-backend.get('unrelated_dropped_packets', 0)
        old_errors = backend.get('parse_errors', 0)
        old_reasons = backend.get('drop_reasons', {})
        if isinstance(event.data.get('drop_reasons'),dict):
            backend.update(drop_reasons=event.data['drop_reasons'],last_send_error=event.data.get('last_send_error'),
                           send_waits=event.data.get('send_waits',0))
        for key in ('dropped_packets', 'unrelated_dropped_packets', 'parse_errors'):
            if key in event.data:
                backend[key] = event.data[key]
        if kind=='bridge/heartbeat':
            drops=backend.get('dropped_packets',0)-backend.get('unrelated_dropped_packets',0)
            errors=backend.get('parse_errors',0)
            if drops>old_drops or errors>old_errors:
                kind='bridge/gap'
                event.data['type'] = kind
        if kind=='bridge/started':
            backend.update(connected=True,home=event.data.get('home'),cli=event.data.get('cli'),
                           bridge_pid=event.data.get('bridge_pid'),implementation=event.data.get('implementation'))
            for field in ('configuration_revision', 'configuration_matches'):
                if field in event.data:
                    backend[field] = event.data[field]
        elif kind in ('bridge/gap','bridge/reconnected'):
            backend['gap_at']=event.at
            reasons = [key for key, count in backend.get('drop_reasons', {}).items()
                       if count > old_reasons.get(key, 0) and key != 'oversized_unrelated_diagnostic']
            if backend.get('parse_errors', 0) > old_errors:
                reasons.append('parse_error')
            recent = backend.setdefault('recent_observation_gaps', [])
            recent.append({'at': event.at, 'timestamp': event.wall_time,
                           'reasons': reasons[:16] or [event.data.get('reason') or kind], 'scope': 'backend'})
            del recent[:-16]
            for thread in self.threads.values():
                if thread.backend==event.backend and (thread.status=='inProgress' or any(a.active for a in thread.activities.values())):
                    thread.quality.add('observation_gap')
                    for activity in thread.activities.values():
                        if activity.active:
                            activity.status = 'unknown'
                            activity.observation_lost = True
            for stream in self.streams.values():
                if stream.key[0]==event.backend and stream.ended_at is None and not stream.ended_by_history:
                    stream.interrupt_observation()
            backend['gaps'] = event.data.get('dropped_packets',backend.get('gaps',0)+1)
        elif kind in ('bridge/disconnected','bridge/stopped'):
            backend['connected']=False
            backend['ended_at']=event.at
            if kind == 'bridge/stopped':
                # A transport timeout is not evidence that this backend died.
                # Only the wrapper's explicit final stop retires the instance.
                self.identity_budget.retire('backend', event.backend, event.backend)
            for thread in self.threads.values():
                if thread.backend!=event.backend:
                    continue
                if thread.status=='inProgress' or any(a.active for a in thread.activities.values()):
                    thread.status='unknown'
                    thread.quality.add('disconnected')
                    for activity in thread.activities.values():
                        if activity.active:
                            activity.status='unknown'
                            activity.observation_lost=True
                for stream in self.streams.values():
                    if stream.key[0]==event.backend and stream.ended_at is None and not stream.ended_by_history:
                        stream.interrupt_observation('disconnected')

    def _protocol(self,event):
        value, backend, at = event.data,event.backend,event.at
        method, params = value.get('method'),value.get('params',{})
        request_key=(backend,value.get('id'))
        if event.direction=='protocol_in':
            if method and value.get('id') is not None:
                self.requests[request_key]=(method,params)
            elif value.get('id') is not None:
                resolve_request(self,backend,value.get('id'),at,value.get('result',{}).get('decision'))
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
                    identity=self.usage.identity(backend,thread.id,self)
                    self.usage.state(identity,at)['new_thread']=not bool(result['thread'].get('turns'))
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
        if handle_protocol(self,event):
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
            model=params.get('threadSettings',{}).get('model') or params.get('toModel') or params.get('model')
            if model:
                thread.model=model
                for key,stream in self.streams.items():
                    if key[0]==backend and self.turns.get((backend,self.items.get((backend,key[1]),{}).get('turn_id')))==thread_id and stream.ended_at is None and not stream.ended_by_history:
                        stream.set_model(model)
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
            if (backend,turn_id) in self.ended_turns:
                self.counts['late_terminal_items']+=1
                return
            if thread.turn_id==turn_id and thread.status=='inProgress':
                self.counts['duplicate_lifecycle_ignored']+=1
                return
            event.turn_started_accepted=True
            for activity in thread.activities.values():
                if activity.turn_id!=turn_id and (activity.active or activity.unresolved) and activity.turn_bound:
                    activity.status='unknown'
                    activity.ended_at=at
                    for key in activity.stream_keys:
                        stream=self.streams.get(key)
                        if stream and stream.ended_at is None:
                            stream.ended_at=at
                            stream.quality='missing_termination'
                            stream.text=''
            thread.turn_id,thread.status=turn_id,'inProgress'
            thread.turn_start_observed = True
            thread.history_turn = None
            thread.history_progress_ms=None
            thread.wait_started_at=thread.last_progress_at=at
            thread.quality.clear()
            thread.active_flags.clear()
            thread.error_status=None
            thread.plan=[]
        elif method=='turn/completed':
            self.identity_budget.retire('turn', backend, turn_id)
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
                if activity.turn_id==turn_id and (activity.active or activity.unresolved):
                    if not activity.turn_bound and thread.status=='completed':
                        continue
                    activity.ended_at=at
                    activity.status=('unknown' if activity.kind=='tool_ready' else 'completed') if thread.status=='completed' else thread.status
            thread.active_flags.clear()
            for request_key,(owner,item_id) in list(self.approvals.items()):
                if owner==(backend,thread_id) and thread.activities[item_id].turn_id==turn_id and thread.activities[item_id].turn_bound:
                    resolve_request(self,backend,request_key[1],at)
            for key,stream in self.streams.items():
                if key[0]==backend and self.items.get((backend,key[1]),{}).get('turn_id')==turn_id and stream.ended_at is None and not stream.ended_by_history:
                    stream.ended_at=at
        elif method in ('item/started','item/completed'):
            self._protocol_item(thread,turn_id,params.get('item',{}),method,at)
        elif method in ('item/agentMessage/delta','item/plan/delta','item/reasoning/summaryTextDelta'):
            item_id=params.get('itemId')
            kind='summary' if 'reasoning' in method else 'plan' if '/plan/' in method else 'text'
            if not self._delta(event,item_id,kind,params.get('delta',''),params.get('summaryIndex',0),source='protocol'):
                self._queue(event,item_id)
        elif method=='item/commandExecution/outputDelta':
            activity=thread.activities.get(params.get('itemId'))
            if activity and params.get('output_chars',len(params.get('delta',''))):
                activity.output_chars+=params.get('output_chars',len(params.get('delta','')))
                if activity.ended_at is None:
                    activity.kind='tool'
                    activity.last_progress_at=at
                thread.last_progress_at=at

    def _protocol_item(self,thread,turn_id,item,method,at):
        item_id=item.get('id')
        if not item_id:
            return
        key=(thread.backend,item_id)
        if key not in self.items and self.identity_budget.contains('item', thread.backend, item_id):
            self.counts['late_terminal_items'] += 1
            return
        if method == 'item/completed':
            self.identity_budget.retire('item', thread.backend, item_id)
        known=self.items.setdefault(key,{'turn_id':turn_id,'type':item.get('type'),'upstream':False})
        known.setdefault('started_at',at if method=='item/started' else None)
        known['turn_id']=turn_id or known.get('turn_id')
        kind=item.get('type')
        known['mirrored'] = known.get('mirrored',False) or item.get('delivery')=='async'
        if known['mirrored']:
            previous=thread.activities.get(item_id)
            if previous:
                previous.mirrored=True
                previous.ended_at=at
                previous.status='unknown'
                for stream_key in previous.stream_keys:
                    stream=self.streams.get(stream_key)
                    if stream:
                        stream.ended_at=at
                        stream.quality='mirrored'
                        stream.text=''
            self.counts['mirrored_items']+=1
            return
        if method=='item/started' and (thread.backend,turn_id) in self.ended_turns:
            self.counts['late_terminal_items']+=1
            return
        if kind in ('userMessage','hookPrompt','functionCallOutput'):
            known['protocol_completed']=method=='item/completed'
            return
        if kind in ('enteredReviewMode','exitedReviewMode'):
            if not thread.turn_id or turn_id==thread.turn_id:
                thread.review_mode=kind=='enteredReviewMode'
            return
        if kind=='subAgentActivity':
            child = item.get('agentThreadId')
            if child and item.get('kind') == 'started' and any(a.turn_id == turn_id and
                    a.call_id == item_id and a.coordination_action == 'spawn' for a in thread.activities.values()):
                self._assign_parent(thread, child)
            if child in thread.subagents:
                thread.subagents[child] = item.get('kind', 'unknown')
            return
        if kind in ('agentMessage','reasoning','plan'):
            phase={'agentMessage':'text','reasoning':'reasoning','plan':'plan'}[kind]
            activity=self._activity(thread,item_id,turn_id,phase,PHASES[phase],known.get('started_at'))
        elif kind in ('contextCompaction','sleep'):
            phase='compaction' if kind=='contextCompaction' else 'sleep'
            activity=self._activity(thread,item_id,turn_id,phase,PHASES[phase],known.get('started_at'))
        elif kind in TOOL_TYPES:
            if kind == 'collabAgentToolCall' and item.get('tool') in SPAWN_OPERATIONS:
                for child in item.get('receiverThreadIds', []):
                    self._assign_parent(thread, child)
            label=item.get('command') or '/'.join(str(item.get(k,'')) for k in ('server','tool')).strip('/') or item.get('tool') or kind
            confirmed_process=(kind=='commandExecution' and bool(item.get('processId'))
                               and item.get('source') in ('unifiedExecStartup','unifiedExecInteraction'))
            phase='tool_ready' if kind=='commandExecution' and not confirmed_process else 'tool'
            activity=self._activity(thread,item_id,turn_id,phase,label,known.get('started_at'))
            self._tool_metadata(activity, item)
            if kind == 'fileChange':
                if method == 'item/started' and 'file_change_started_at' not in known:
                    known['file_change_started_at'] = at
                    activity.started_at = activity.last_progress_at = at
                    thread.last_progress_at = max(thread.last_progress_at, at)
                elif method == 'item/completed' and 'file_change_started_at' not in known:
                    activity.started_at = None
                activity.kind = 'tool'
            if confirmed_process:
                activity.turn_bound=False
            if confirmed_process and activity.ended_at is None:
                activity.kind='tool'
        else:
            return
        activity.item_type=kind
        if kind=='plan' and activity.active:
            activity.kind='plan'
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
                    final=item.get('text') if stream.kind in ('text','plan') else None
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

    @property
    def pending_generation_count(self):
        """Only unfinished, unmapped output makes current generation unknown.

        Keep terminal metadata queued for late identity recovery, without letting
        its retention timeout delay the live menu or invent an activity owner.
        """
        return sum(len(events) for key, events in self.pending.items()
                   if key not in self.pending_ends and not any(
                       e.data.get('type') == 'response.output_item.done' for e in events))

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
                self._observe_timeline(event)
                if (event.data.get('type') in ('response.output_item.added','response.output_item.done')
                        or event.data.get('method') in ('item/started','item/completed')):
                    self.usage.item(event,self)
            completion = self.pending_ends.pop(key, None)
            if completion is not None:
                self._upstream(completion)

    def _upstream(self,event):
        value,backend,at=event.data,event.backend,event.at
        kind=value.get('type')
        if kind in ('response.completed','response.failed','response.incomplete'):
            model=value.get('response',{}).get('model')
            if model:
                self.backends[backend]['model']=model
            ids=set(value.get('response',{}).get('item_ids',[]))
            for item_id in ids:
                key = (backend, item_id)
                if key in self.pending:
                    self.pending_ends[key] = event
            identity=value.get('identity',{})
            for key,stream in self.streams.items():
                known=self.items.get((backend,key[1]),{})
                scoped=key[1] in ids if ids else identity.get('turn_id') and known.get('turn_id')==identity['turn_id']
                if key[0]==backend and scoped and stream.ended_at is None and not stream.ended_by_history:
                    self._finish_stream(stream,at)
                    owner=self.turns.get((backend,known.get('turn_id')))
                    activity=self.threads.get((backend,owner)).activities.get(key[1]) if (backend,owner) in self.threads else None
                    if activity and activity.kind in ('text','reasoning','plan','tool_input'):
                        activity.ended_at=at
                        activity.status='unknown' if kind!='response.completed' else 'completed'
            return
        if kind in ('response.output_item.added','response.output_item.done'):
            item=value.get('item',{})
            item_id=item.get('id')
            if not item_id:
                return
            key=(backend,item_id)
            if key not in self.items and self.identity_budget.contains('item', backend, item_id):
                self.counts['late_terminal_items'] += 1
                return
            known=self.items.setdefault(key,{'started_at':at if kind.endswith('.added') else None})
            if known.get('mirrored'):
                self.counts['mirrors_ignored']+=1
                return
            metadata={**value.get('identity',{}),**item.get('metadata',{})}
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
            label=tool_name(item) or (tr('diagnostic.tool_unknown') if phase=='tool_input' else PHASES[phase])
            activity=self._activity(thread,item_id,turn_id,phase,label,known['started_at'])
            if phase == 'tool_input':
                if tool_name(item):
                    self._tool_metadata(activity, item)
                if kind.endswith('.done'):
                    final = item.get('input', item.get('arguments'))
                    if isinstance(final, str) and activity.tool_name:
                        self._tool_label(activity, final)
            if kind.endswith('.added'):
                if activity.status=='unknown':
                    activity.status='inProgress'
                    activity.ended_at=None
                # A new model item ends the preparation context; it does not
                # prove that an outer Code Mode cell or a background tool ended.
                if phase in ('reasoning','text'):
                    for previous in thread.activities.values():
                        if previous.id!=item_id and previous.turn_id==turn_id and previous.kind=='reasoning' and previous.active:
                            previous.ended_at=at
                            previous.status='unknown'
                            thread.quality.add('missing_item_end')
                        if previous.id!=item_id and previous.turn_id==turn_id and previous.kind=='tool_ready' and is_code_exec(previous.tool_name):
                            previous.ended_at=at
                            previous.status='unknown'
                return
            known['ended_at']=at
            known['upstream_completed']=True
            self.identity_budget.retire('item', backend, item_id)
            if phase=='tool_input' and activity.item_type == 'fileChange':
                pass  # Input completion must not reopen or end canonical editing.
            elif phase=='tool_input' and terminal is None:
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
        if known.get('mirrored'):
            self.counts['mirrors_ignored']+=1
            return True
        turn_id=known.get('turn_id')
        if (event.backend,turn_id) in self.conflicted_turns:
            self.counts['quarantined_events']+=1
            return True
        thread_id=self.turns.get((event.backend,turn_id))
        if not thread_id:
            return False
        thread=self._thread(event.backend,thread_id)
        existing = thread.activities.get(item_id)
        if existing and (existing.history_retired or (existing.history and existing.history.get('history_terminal'))):
            self.counts['late_terminal_deltas'] += 1
            return True
        if (thread.status == 'unknown' and thread.turn_id == turn_id and delta and not event.replayed and
                (event.backend, turn_id) not in self.ended_turns and
                self.backends[event.backend].get('connected') and
                event.at > self.backends[event.backend].get('gap_at', event.at)):
            thread.status = 'inProgress'
        if (event.backend,turn_id) in self.ended_turns or thread.status not in ('inProgress','idle'):
            self.counts['late_terminal_deltas']+=1
            return True
        key=(event.backend,item_id,kind,part)
        if source=='protocol' and known.get('upstream') and key not in self.streams:
            return True
        stream=self.streams.get(key)
        if stream and (stream.source!=source or stream.ended_at is not None or stream.ended_by_history):
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
            if 'identity_conflict' in thread.quality or ('observation_gap' in thread.quality and
                    stream.started_at <= self.backends[event.backend].get('gap_at',event.at)):
                stream.interrupt_observation()
        if (delta and not event.replayed and self.backends[event.backend].get('connected') and
                event.at > self.backends[event.backend].get('gap_at', event.at)):
            stream.resume_observation(event.at)
        stream.append(delta,event.at)
        if delta:
            if (activity.observation_lost and not event.replayed and
                    event.at > self.backends[event.backend].get('gap_at', 0)):
                activity.status = 'inProgress'
                activity.observation_lost = False
            activity.last_progress_at=event.at
            thread.error_status=None
            for waiting in thread.activities.values():
                if waiting.turn_id==turn_id and waiting.kind in ('retry','buffering','auth') and waiting.active:
                    waiting.status='completed'
                    waiting.ended_at=event.at
            thread.last_progress_at=max(thread.last_progress_at,event.at)
            if kind=='summary':
                activity.summary_chars+=len(delta)
                activity.summary_parts.add(part)
        return True

    def _finish_stream(self,stream,at,final=None):
        if getattr(stream,'finalized',False) or stream.ended_by_history:
            return
        stream.ended_at=at
        if final is not None:
            stream.finalized=True
            if stream.text!=final and stream.counted_revision!=stream.revision:
                stream.quality='reconciled'
            elif stream.text!=final and stream.text:
                stream.quality='reconciled'
            if len(final.encode())<=8*1024*1024:
                stream.text_complete = True
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
        if self.identity_budget.sealed:
            self._identity_budget_gap(now)
        self.usage.tick(now,self)
        for key in list(self.pending):
            kept=[e for e in self.pending[key] if now-e.at<10]
            self.counts['unassociated_expired']+=len(self.pending[key])-len(kept)
            self.pending_count-=len(self.pending[key])-len(kept)
            self.pending_bytes-=sum(getattr(e,'pending_bytes',0) for e in self.pending[key] if now-e.at>=10)
            if kept:
                self.pending[key]=kept
            else:
                del self.pending[key]
                self.pending_ends.pop(key, None)
        # Retain recent finished results, release old identity and text state.
        needed = {(t.backend, t.parent_thread_id) for t in self.threads.values() if t.parent_thread_id and
                  (t.status == 'inProgress' or any(a.active for a in t.activities.values()) or now-t.last_progress_at <= 600)}
        for _ in range(len(self.threads)):
            ancestors = {(t.backend, t.parent_thread_id) for t in self.threads.values() if (t.backend, t.id) in needed and t.parent_thread_id}
            if ancestors <= needed:
                break
            needed.update(ancestors)
        for key,thread in list(self.threads.items()):
            age = max(0, time.time() - thread.history_progress_ms / 1000) if thread.history_progress_ms is not None else now-thread.last_progress_at
            if thread.status!='inProgress' and not any(a.active or a.unresolved for a in thread.activities.values()) and age>600:
                if key in needed:
                    thread.activities.clear()
                    thread.context_only = True
                else:
                    self.threads.pop(key)
        alive_items={(t.backend,a.id) for t in self.threads.values() for a in t.activities.values()}
        for key in list(self.streams):
            if (key[0],key[1]) not in alive_items:
                self.streams.pop(key,None)
                self.sequence.pop(key,None)
        for key in list(self.items):
            known=self.items[key]
            if key not in alive_items and key not in self.pending and (key[0],known.get('turn_id')) not in self.turns:
                self.items.pop(key,None)
        for thread in self.threads.values():
            for activity in thread.activities.values():
                if not (is_code_exec(activity.tool_name) or is_command_tool(activity.tool_name) or is_patch_tool(activity.tool_name)):
                    continue
                for key in activity.stream_keys:
                    stream = self.streams.get(key)
                    if (stream and stream.kind == 'tool_input' and stream.text
                            and stream.revision != activity.tool_label_revision
                            and stream.quality in ('complete', 'reconciled')):
                        self._tool_label(activity, stream.text)
                        activity.tool_label_revision = stream.revision
            terminal=[a for a in thread.activities.values() if not a.active and not a.unresolved]
            for activity in terminal[:-20]:
                thread.activities.pop(activity.id,None)
        alive_turns={(t.backend,t.turn_id) for t in self.threads.values()} | {(t.backend,a.turn_id) for t in self.threads.values() for a in t.activities.values()}
        pending_turns={(b,known.get('turn_id')) for (b,i),known in self.items.items() if (b,i) in self.pending}
        self.turns={k:v for k,v in self.turns.items() if k in alive_turns or k in pending_turns}
        self.ended_turns={k:v for k,v in self.ended_turns.items() if k in alive_turns or k in pending_turns}
        self.conflicted_turns.intersection_update(alive_turns | pending_turns)
        retained_items = {(t.backend, a.id) for t in self.threads.values() for a in t.activities.values()}
        self.items={k:v for k,v in self.items.items() if k in retained_items or k in self.pending or
                    ((k[0],v.get('turn_id')) in self.turns and not self.identity_budget.contains('item', *k))}
        if len(self.requests)>512:
            self.requests=dict(list(self.requests.items())[-512:])
        self.approvals={k:v for k,v in self.approvals.items() if v[0] in self.threads}
        # Unresolved work and recent history may outlive a disconnected backend's
        # display retention. Keep its scope and connection evidence with its rows.
        retained_backends = {thread.backend for thread in self.threads.values()}
        for backend in list(self.backends):
            if (backend not in retained_backends and not self.backends[backend].get('connected')
                    and now-self.backends[backend].get('ended_at',now)>600):
                self.backends.pop(backend,None)
        history_keys = {(self.backends.get(t.backend, {}).get('home'), t.id) for t in self.threads.values()}
        self.history_versions = {k: v for k, v in self.history_versions.items() if k in history_keys}
        text_sizes = [(len(s.text.encode('utf-8')), s) for s in self.streams.values()]
        retained = sum(size for size, _ in text_sizes)
        if retained > 32*1024*1024:
            for size, stream in sorted(text_sizes,key=lambda pair:pair[0],reverse=True):
                if not stream.text:
                    continue
                stream.text=''
                stream.quality='memory_limit'
                retained -= size
                if retained <= 16*1024*1024:
                    break
        self.counter.tick(list(self.streams.values()),now)

    def snapshot(self,now,wall_time=None):
        views=[]
        for thread in self.threads.values():
            backend=self.backends.get(thread.backend,{})
            active=[a for a in thread.activities.values() if a.active]
            unresolved = any(a.unresolved for a in thread.activities.values()) or thread.status == 'unknown'
            def phase(activity):
                return activity.kind
            active.sort(key=lambda a:(PRIORITY.get(phase(a),9),-(a.started_at or 0)))
            primary=active[0] if active else None
            main_phase=phase(primary) if primary else 'unknown' if unresolved else 'waiting' if thread.status=='inProgress' else thread.status
            if not backend.get('connected',True) and thread.status=='inProgress':
                main_phase='unknown'
            since=primary.started_at if primary else thread.wait_started_at
            details=[]
            for activity in reversed(list(thread.activities.values())):
                if activity.history_retired:
                    continue
                end=activity.ended_at
                history = activity.history or {}
                source_start, source_end = history.get('start_ms'), history.get('end_ms')
                duration = ((source_end-source_start)/1000 if source_start is not None and source_end is not None and source_end >= source_start else
                            None if activity.unresolved or (activity.effective_status == 'unknown' and end is None) or (history.get('history_terminal') and end is None) else
                            max(0,(end or now)-(activity.started_at or now)))
                details.append({'id':activity.id,'turn_id':activity.turn_id,'kind':phase(activity),
                                'label':str(activity.label)[:160], 'status':activity.effective_status,
                                'active':activity.active, 'duration_seconds':duration,
                                'source_start_ms':source_start, 'source_end_ms':source_end,
                                'turn_bound':activity.turn_bound,
                                'tool_name':activity.tool_name, 'tool_names':list(activity.tool_names),
                                'command':activity.command,
                                'command_name':activity.command_name, 'item_type':activity.item_type,
                                **tool_display(activity.tool_name, activity.app_context),
                                'command_previews':[dict(p) for p in activity.command_previews],
                                'file_edit_preview':activity.file_edit_preview, 'call_id':activity.call_id,
                                'start_known':source_start is not None or activity.started_at is not None,'summary_chars':activity.summary_chars,
                                'summary_parts':len(activity.summary_parts), 'output_chars':activity.output_chars,
                                'exit_code':history.get('exit_code', activity.exit_code),
                                'streams':[self.streams[k].view(now) for k in activity.stream_keys if k in self.streams]})
            rates={'text':0.0,'tool_input':0.0}
            seen_rates=set()
            unavailable_rates=set()
            # History availability is independent of live stream continuity.
            quality=set(thread.quality) | (thread.history_quality - {
                'history_unavailable', 'history_budget', 'history_read_error',
                'history_identity_mismatch', 'malformed_history_record'})
            if unresolved:
                quality.add('observation_gap')
            for activity in thread.activities.values():
                if activity.turn_id==thread.turn_id:
                    for key in activity.stream_keys:
                        stream=self.streams.get(key)
                        if stream and stream.quality!='complete':
                            quality.add(stream.quality)
            for activity in active:
                for key in activity.stream_keys:
                    stream=self.streams.get(key)
                    if stream and stream.kind in rates and stream.ended_at is None and not stream.ended_by_history:
                        rate=stream.rate(now)
                        if rate is not None:
                            rates[stream.kind]+=rate
                            seen_rates.add(stream.kind)
                        else:
                            unavailable_rates.add(stream.kind)
            views.append({'backend':thread.backend,'id':thread.id,'name':thread.name or thread.id[:8],
                          'ephemeral':thread.ephemeral,'thread_source':thread.thread_source,
                          'parent_thread_id':thread.parent_thread_id,'parent_source':thread.parent_source,
                          'parent_quality':sorted(thread.parent_quality),'context_only':thread.context_only,
                          'subagents':dict(thread.subagents),
                          'home':backend.get('home'),'host':'local',
                          'cwd':thread.cwd,'model':thread.model,'turn_id':thread.turn_id,'status':thread.status,
                          'phase':main_phase,'phase_label':PHASES.get(main_phase,main_phase),
                          'phase_seconds':max(0,now-since) if not unresolved and (primary is not None or thread.status=='inProgress') and since else None,
                          'has_active_work':bool(active) or unresolved or thread.status=='inProgress',
                          'has_unresolved_work':unresolved,
                          'active_count':len(active),'primary_activity_id':primary.id if primary else None,
                          'last_progress_seconds':max(0,(wall_time or time.time())-thread.history_progress_ms/1000) if thread.history_progress_ms is not None and not active and not unresolved else max(0,now-thread.last_progress_at) if thread.last_progress_at else None,
                          'token_rates':{k:v if k in seen_rates and k not in unavailable_rates else None for k,v in rates.items()},
                          'quality':sorted(quality),'connected':backend.get('connected',True),
                          'history_quality':sorted(thread.history_quality),
                          'activities':details})
            views[-1]['usage']=self.usage.view(self.usage.identity(thread.backend,thread.id,self),thread.turn_id)
            presentation(self,thread,views[-1],now)
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
        annotate_parents(views)
        total = overview(self, views, now)
        return {'schema_version':1,'updated_at':wall_time,'clock':now,'threads':views,
                'overview':total,'menu_presentation':menu_presentation(self, views, total),
                'backends':self.backends,'unassociated_events':self.pending_count,'counters':dict(self.counts),
                'unassociated_live_events':self.pending_generation_count,
                'resource_diagnostics': {'tokenizer': self.counter.diagnostics(),
                                         'identity_budget': self.identity_budget.diagnostics()},
                'usage_recovery':self.usage.dump(),'usage_diagnostics':dict(self.usage.diagnostics)}

    def close(self):
        self.counter.close()
