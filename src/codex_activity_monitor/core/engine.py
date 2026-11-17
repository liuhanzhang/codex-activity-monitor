"""Backend-scoped identity and activity state. Model bodies stay out of snapshots."""

from collections import OrderedDict, defaultdict
import time

from codex_activity_monitor.agents.codex.events import Event
from codex_activity_monitor.agents.codex.history_contract import HistorySelection
from codex_activity_monitor.agents.codex.progress_state import presentation
from codex_activity_monitor.agents.codex.projection import handle_lifecycle, ingest_event
from codex_activity_monitor.agents.codex.reconciliation import apply_history
from codex_activity_monitor.i18n.i18n import PHASE_KEYS, phase_message, render_message, tr
from .agent_relations import VERIFIED_PARENTS, raw_coordination, thread_identity
from .identity_budget import IdentityBudget
from .menu_presentation import menu_presentation
from .metrics import TokenCounter
from .overview import annotate_parents, overview
from .state import Activity, Thread, source_traits
from .tool_labels import (code_tool_names, command_name, command_previews, command_summary,
                          file_edit_preview, is_code_exec, is_command_tool, is_patch_tool,
                          tool_display, tool_label, tool_name)
from .usage import UsageBook


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
        self.history_source_epochs = {}
        self.history_inputs = OrderedDict()
        self.history_input_sequence = 0
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
            source_epoch, hint = self.history_inputs.get((scope, thread.id), (0, ()))
            hint = dict(hint)
            if self.backends.get(thread.backend, {}).get('sqlite_home'):
                hint['sqlite_home'] = self.backends[thread.backend]['sqlite_home']
            requests.append(HistorySelection(scope, thread.id, frozenset(turns),
                thread.status in ('inProgress', 'unknown') or
                any(a.active or a.unresolved for a in thread.activities.values()), frozenset(items),
                source_epoch, tuple(sorted(hint.items()))))
        return requests

    def history_hint(self, backend, thread, metadata=None):
        scope = self.backends.get(backend, {}).get('home')
        if not isinstance(scope, str):
            return
        key = scope, thread
        _, previous = self.history_inputs.get(key, (0, ()))
        value = dict(previous)
        if metadata is None:
            value.pop('path', None)
        else:
            for field, name in (('path', 'path'), ('historyMode', 'history_mode')):
                candidate = metadata.get(field)
                if isinstance(candidate, str) and 0 < len(candidate) <= 4096:
                    value[name] = candidate
            if value == dict(previous):
                return
        self.history_input_sequence += 1
        self.history_inputs[key] = self.history_input_sequence, tuple(sorted(value.items()))
        self.history_inputs.move_to_end(key)
        while len(self.history_inputs) > 1024:
            self.history_inputs.popitem(last=False)

    def restore_unresolved(self, rows, updated_at):
        """Restore identities, never old live speed or a fabricated observation."""
        restored = set()
        for row in rows:
            if row.get('status') != 'unknown' or not row.get('home') or not row.get('turn_id'):
                continue
            backend = row['backend']
            self.backends.setdefault(backend, {'connected': False, 'home': row['home'], 'source': row.get('source')})
            thread = self._thread(backend, row['id'])
            thread.name, thread.cwd = row.get('name') or '', row.get('cwd') or ''
            thread.ephemeral = row.get('ephemeral') if isinstance(row.get('ephemeral'), bool) else None
            thread.thread_source = row.get('thread_source')
            thread.visible = row.get('visible') is not False
            if row.get('parent_source') in VERIFIED_PARENTS:
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

    def register_backend(self, backend, **fields):
        entry = self.backends.setdefault(backend, {})
        entry.update(fields)
        return entry

    def thread(self, backend, thread_id):
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

    _thread = thread

    def remove_thread(self, backend, thread_id):
        return self.threads.pop((backend, thread_id), None)

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
        self.history_hint(backend, thread.id, metadata)
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

    def activity(self, thread, item_id, turn_id, kind, label, at):
        thread.context_only = False
        if item_id not in thread.activities:
            thread.activities[item_id] = Activity(item_id,turn_id,kind,label,at)
            thread.activities[item_id].last_progress_at = at or 0
            if at is not None:
                thread.last_progress_at = max(thread.last_progress_at,at)
        return thread.activities[item_id]

    _activity = activity

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

    def admit_identities(self, backend, *pairs, at=None):
        if at is not None:
            self.clock = max(self.clock, at)
        if self.identity_budget.sealed:
            self._identity_budget_gap(at)
            self.counts['identity_budget_dropped'] += 1
            return False
        if self.identity_budget.contains('backend', backend, backend):
            self.counts['retired_backend_events'] += 1
            return False
        try:
            admitted = all(self.identity_budget.reserve(kind, backend, identity)
                           for kind, identity in [('backend', backend), *pairs] if identity is not None)
        except ValueError:
            self.counts['invalid_observation_identity'] += 1
            return False
        if not admitted:
            self._identity_budget_gap(at)
            self.counts['identity_budget_dropped'] += 1
            return False
        return True

    def ingest(self, row):
        ingest_event(self, row)

    def _identity_budget_gap(self, at=None):
        if self.identity_gap:
            return
        self.identity_gap = True
        for backend in self.backends:
            gap = Event(backend, 'lifecycle', {'type': 'bridge/gap', 'reason': 'identity_budget'},
                        self.clock if at is None else at, time.time())
            handle_lifecycle(self, gap)
            self._observe_timeline(gap)
        for thread in self.threads.values():
            thread.quality.add('identity_budget')

    def _observe_timeline(self,event):
        if self.timeline is not None:
            try:
                self.timeline.observe(event,self)
            except (ValueError,TypeError,KeyError,AttributeError):
                self.counts['timeline_projection_error']+=1

    @property
    def pending_generation_count(self):
        """Only unfinished, unmapped output makes current generation unknown.

        Keep terminal metadata queued for late identity recovery, without letting
        its retention timeout delay the live menu or invent an activity owner.
        """
        return sum(len(events) for key, events in self.pending.items()
                   if key not in self.pending_ends and not any(
                       e.data.get('type') == 'response.output_item.done' for e in events))

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
            retained_directory = source_traits(self.backends.get(thread.backend, {}).get('source')).retains_idle_directory
            if not retained_directory and thread.status!='inProgress' and not any(a.active or a.unresolved for a in thread.activities.values()) and age>600:
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
        self.history_source_epochs = {k: v for k, v in self.history_source_epochs.items() if k in history_keys}
        self.history_inputs = OrderedDict((k, v) for k, v in self.history_inputs.items() if k in history_keys)
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
            if backend.get('source'):
                views[-1]['source'] = backend['source']
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
