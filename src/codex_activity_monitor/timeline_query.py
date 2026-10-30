"""Read-only timeline queries, separate from both Codex and the producer socket."""

from copy import deepcopy
import json
from pathlib import Path
import socket
import socketserver
import threading
import time

from .history import HistoryProvider, raw_section
from .timeline import (
    apply_model_membership, display_phase, identity, merge_history_phase, settled_model_items,
)
from .timeline_storage import metadata_path, read_records
from .agent_presentation import present_agents, present_coordination, scoped_status
from .agent_relations import SPAWN_OPERATIONS
from .content_version import DETAIL_VERSION, page_request, paginate
from .history_read import HistoryChanged
from .query_request import TimelineRequest
from .query_response import encode_response, read_response, failure
from .runtime import collector_alive


def validated_request(value):
    """Compatibility adapter for CLI/socket serialization; domain uses the DTO."""
    return TimelineRequest.parse(value).wire()


class TimelineQuery:
    def __init__(self, book=None, metadata=None, history=None):
        self.book = book
        self.metadata = metadata
        self.history = history or HistoryProvider()

    def _configure_source(self, scope, thread):
        engine = getattr(self.book, 'engine', None)
        if engine is not None:
            epoch, hint = engine.history_inputs.get((scope, thread), (0, ()))
            hint = dict(hint)
            for backend in list(engine.backends.values()):
                if backend.get('home') == scope and backend.get('sqlite_home'):
                    hint['sqlite_home'] = backend['sqlite_home']
            self.history.configure_source(scope, thread, epoch, tuple(sorted(hint.items())))

    def _observed(self, scope, thread):
        if self.book:
            return self.book.snapshot(scope, thread)
        result = {'phases': [], 'turns': [], 'relations': [], 'revision': 0}
        for kind, record in read_records(self.metadata) if self.metadata else []:
            if record.get('scope_id') == scope:
                result[{'phase': 'phases', 'turn': 'turns', 'relation': 'relations'}[kind]].append(record)
        # Reading history while observation is stopped must not revive open phases.
        for record in result['phases']:
            if record['status'] in ('active', 'waiting'):
                record.update(status='unknown', quality=sorted(set(record.get('quality', [])) | {'observation_stopped'}))
        return result

    def bundle(self, scope, thread, selected=None):
        with self.history.lock:
            for attempt in range(2):
                try:
                    return self._bundle(scope, thread, selected)
                except HistoryChanged:
                    if attempt:
                        raise

    def _bundle(self, scope, thread, selected=None):
        observed = self._observed(scope, thread)
        phases = {p['id']: p for p in observed['phases']}
        turns = {(t['thread_id'], t['turn_id']): t for t in observed['turns']}
        relations = {r['id']: r for r in observed['relations']}
        quality = set()
        if self.book:
            if self.book.store and self.book.store.error:
                quality.add(self.book.store.error)
            if self.book.engine and self.book.engine.counts.get('timeline_projection_error', 0):
                quality.add('projection_partial')
        revisions = [observed['revision']]
        loaded = {}
        spawn_calls = {(p['thread_id'], p['turn_id'], p.get('call_id')) for p in phases.values()
                       if p.get('call_operation') == 'spawn'}

        hydrated = set()

        def load(owner):
            # Load one light catalogue per owner. Completed phase metadata is
            # fetched only after explicit turn ownership has been resolved.
            if owner in loaded:
                return loaded[owner]
            loaded[owner] = None
            try:
                self._configure_source(scope, owner)
                index = self.history.index(scope, owner, directory_only=True)
                if not index:
                    return None
                revisions.append(index.revision)
                quality.update(index.quality)
                if not index.read_complete:
                    quality.add('history_incomplete')
                for key, old in list(turns.items()):
                    if key[0] == owner:
                        turns[key] = merge_history_phase(old, index.turns.get(key[1]), read_complete=index.read_complete)
                for t in index.turns.values():
                    turns.setdefault((owner, t['turn_id']), dict(t))
                for p in index.phases.values():
                    phases[p['id']] = merge_history_phase(phases.get(p['id']), p, read_complete=index.read_complete)
                spawn_calls.update((owner, turn, call) for turn, call in index.spawn_calls)
                relations.update({key: dict(value) for key, value in index.relations.items()})
                loaded[owner] = index
                return index
            except HistoryChanged:
                raise
            except (OSError, ValueError, TypeError) as error:
                quality.add('history_unavailable')
                if getattr(error, 'reason', None):
                    quality.add(error.reason)
                return None

        def hydrate(owner, wanted=()):
            directory = loaded.get(owner)
            if directory is None:
                return
            wanted = set(wanted) | {p['turn_id'] for p in phases.values() if p['thread_id'] == owner}
            pending = {turn for turn in wanted if turn is not None and (owner, turn) not in hydrated}
            if not pending:
                return
            index = self.history.timeline_view(directory, pending)
            hydrated.update((owner, turn) for turn in pending)
            quality.update(index.quality)
            for key, p in list(phases.items()):
                if p['thread_id'] == owner and p['turn_id'] in pending:
                    phases[key] = merge_history_phase(p, index.phases.get(key), read_complete=index.read_complete)
            for p in index.phases.values():
                phases.setdefault(p['id'], p)
                if p.get('call_operation') == 'spawn':
                    spawn_calls.add((owner, p['turn_id'], p.get('call_id')))
            settled = settled_model_items(index.turns.values(), index.phases.values(),
                                          read_complete=index.read_complete, quality=index.quality,
                                          absent_turns={(scope, owner, p['turn_id']) for p in phases.values()
                                              if index.source and index.source.branched and p['thread_id'] == owner and
                                              p['turn_id'] in pending and p['turn_id'] not in index.turns and
                                              p['status'] not in ('active', 'waiting')})
            for key, phase in list(phases.items()):
                if phase['thread_id'] == owner and phase['turn_id'] in pending:
                    phases[key] = apply_model_membership(phase, settled)

        root_index = load(thread)

        def structural_parent(owner):
            metadata = getattr(loaded.get(owner), 'agent_metadata', {})
            if 'agent_parent_invalid' in metadata.get('quality', []):
                return None
            parents = {r['parent_thread_id'] for r in relations.values() if r['child_thread_id'] == owner and
                       (r.get('operation') in SPAWN_OPERATIONS or
                        (r['parent_thread_id'], r['parent_turn_id'], r.get('call_id')) in spawn_calls)}
            if metadata.get('parent_thread_id'):
                if parents - {metadata['parent_thread_id']}:
                    quality.add('parent_identity_conflict')
                    return None
                return metadata['parent_thread_id']
            if metadata.get('thread_source') == 'user':
                if parents:
                    quality.add('parent_identity_conflict')
                return None
            return next(iter(parents)) if len(parents) == 1 else None

        def is_child(parent, child):
            if structural_parent(child) != parent:
                return False
            seen = {child}
            cursor = parent
            while cursor and cursor not in seen:
                seen.add(cursor); cursor = structural_parent(cursor)
            return cursor is None
        root_turns = sorted((t for t in turns.values() if t['thread_id'] == thread),
                            key=lambda t: t.get('start_ms') or t.get('updated_ms') or 0, reverse=True)
        current = next((t['turn_id'] for t in root_turns if t['status'] == 'active'), None)
        selected = selected or current or (root_turns[0]['turn_id'] if root_turns else None)
        hydrate(thread, (selected,))
        agents = [{'id': thread, 'thread_id': thread, 'name': '主智能体', 'parent_thread_id': None,
                   'quality': []}]
        owners = {(thread, selected)}
        agent_turns = {thread: [t for t in root_turns if t['turn_id'] == selected]}
        visited = {thread}
        pending = [(thread, selected)]
        while pending:
            parent, parent_turn = pending.pop(0)
            candidates = [r for r in relations.values() if r['parent_thread_id'] == parent and r['parent_turn_id'] == parent_turn]
            for child in dict.fromkeys(r['child_thread_id'] for r in candidates):
                if child in visited:
                    continue
                index = load(child)
                if not is_child(parent, child):
                    continue
                visited.add(child)
                child_turns = [t for t in turns.values() if t['thread_id'] == child]
                linked = [t for t in child_turns if t.get('parent_turn_id') == parent_turn]
                # A newly spawned thread's initial turn is the spawn's work. Reuse needs explicit attribution.
                spawned = any(r['child_thread_id'] == child and (r.get('operation') in SPAWN_OPERATIONS or
                    (parent, parent_turn, r.get('call_id')) in spawn_calls) for r in candidates)
                if not linked and spawned and child_turns:
                    linked = [min(child_turns, key=lambda t: t.get('start_ms') or t.get('updated_ms') or 0)]
                hydrate(child, (t['turn_id'] for t in linked))
                agent_quality = [] if linked else ['turn_relation_unknown']
                agent_turns[child] = linked
                agents.append({'id': child, 'thread_id': child, 'name': '子智能体 · ' + child[:8],
                               'parent_thread_id': parent, 'quality': agent_quality})
                for child_turn in linked:
                    owners.add((child, child_turn['turn_id']))
                    pending.append((child, child_turn['turn_id']))
                if index is None:
                    agent_quality.append('history_unavailable')
        engine_names = {}
        if self.book and self.book.engine:
            with self.book.lock:
                for agent in agents[1:]:
                    matches = [t for t in self.book.engine.threads.values() if t.id == agent['thread_id'] and
                               (self.book.engine.backends.get(t.backend, {}).get('home') or t.backend) == scope]
                    if matches:
                        engine_names[agent['thread_id']] = matches[0].name
        present_agents(agents, loaded, engine_names)
        owned = [p for p in phases.values() if (p['thread_id'], p['turn_id']) in owners
                 and not p.get('mirrored') and not p.get('history_retired')]
        by_agent = {}
        for phase in owned:
            by_agent.setdefault(phase['thread_id'], []).append(phase)
        for agent in agents:
            activity = by_agent.get(agent['thread_id'], [])
            agent['status'] = scoped_status(agent_turns.get(agent['thread_id'], []), activity)
            agent['activity_quality'] = sorted({q for p in activity for q in p.get('quality', [])})
            agent['status_scope'] = 'selected_parent_turn'
        if any(p.get('kind') == 'gap' for p in owned):
            quality.add('observation_gap')
        if any('wall_clock_changed' in p.get('quality', []) for p in owned):
            quality.add('clock_unreliable')
        visible = [projected for p in owned if (projected := display_phase(p)) is not None]
        visible.sort(key=lambda p: (p.get('start_ms') or p.get('end_ms') or p.get('observed_until_ms') or 0, p['id']))
        present_coordination(visible, relations.values(), scope)
        input_phases = [p for p in visible if p['thread_id'] == thread and p['kind'] == 'input']
        for i, p in enumerate(input_phases):
            p['is_initial'] = i == 0
        # Explicit raw call IDs can connect phases without collapsing generation into execution.
        by_item = {(p['thread_id'], p['turn_id'], p['item_id']): p['id'] for p in visible if p['namespace'] == 'canonical'}
        for p in visible:
            linked = by_item.get((p['thread_id'], p['turn_id'], p.get('call_id')))
            if linked and p['namespace'] == 'input':
                p['related_phase_ids'] = [linked]
        selected_turn = turns.get((thread, selected), {})
        times = [v for p in visible for v in (p.get('start_ms'), p.get('end_ms'), p.get('observed_until_ms')) if v is not None]
        start = selected_turn.get('start_ms') or (min(times) if times else None)
        end = max(times + [selected_turn.get('end_ms') or start or time.time() * 1000]) if times or start else None
        if any(p['status'] == 'active' for p in visible):
            end = max(end or 0, time.time() * 1000)
        if 'clock_unreliable' in quality:
            start = end = None  # Preserve activities/content, but do not invent a common time basis.
        if root_index is None:
            quality.add('history_pending' if any(p['status'] == 'active' for p in visible) else 'history_unavailable')
        earlier = sorted({p['turn_id'] for p in phases.values() if p['thread_id'] == thread and
                          p['turn_id'] != selected and p['status'] == 'active' and not p.get('history_retired')})
        earlier_relations = {}
        for relation in list(relations.values()):
            if relation['parent_thread_id'] != thread or relation['parent_turn_id'] == selected:
                continue
            earlier_relations.setdefault(relation['child_thread_id'], []).append(relation)
        for child, candidates in earlier_relations.items():
            load(child)
            hydrate(child)
            if not is_child(thread, child):
                continue
            child_turns = [t for t in turns.values() if t['thread_id'] == child]
            first = min(child_turns, key=lambda t: t.get('start_ms') or t.get('updated_ms') or 0) if child_turns else None
            active = [p for p in phases.values() if p['thread_id'] == child and p['status'] == 'active' and not p.get('history_retired')]
            for p in active:
                owned = turns.get((child, p['turn_id']), {})
                for relation in candidates:
                    linked = owned.get('parent_turn_id') == relation['parent_turn_id'] or (
                        (relation.get('operation') in SPAWN_OPERATIONS or
                         (thread, relation['parent_turn_id'], relation.get('call_id')) in spawn_calls) and
                        first and p['turn_id'] == first['turn_id'])
                    if linked:
                        earlier.append(relation['parent_turn_id'])
        earlier = sorted(set(earlier))
        recent = root_turns[:6]
        for t in root_turns[6:]:
            if t['turn_id'] in earlier or t['turn_id'] == selected:
                recent.append(t)
        average = root_index.request_rates.projection(selected) if root_index else None
        if average and root_index.quality:
            average = {**average, 'quality': sorted(set(average['quality']) | root_index.quality),
                       'coverage': 'partial' if average['coverage'] == 'complete' else average['coverage']}
        return {'version': 1, 'scope_id': scope, 'thread_id': thread, 'selected_turn_id': selected,
                'average_speed': average,
                'current_turn_id': current, 'turns': recent, 'agents': agents,
                'phases': [p for p in visible if p['track'] != 'marker'],
                'markers': [p for p in visible if p['track'] == 'marker'],
                'origin_ms': start, 'end_ms': end, 'earlier_active_turns': earlier,
                'quality': sorted(quality), 'revision': identity(revisions, selected), 'now_ms': time.time() * 1000}

    def detail(self, scope, thread, turn, phase_id, section_id=None, offset=0, limit=262144, *, page_token=None):
        version = page_request(scope, thread, turn, phase_id, section_id, offset, page_token)
        observed = self._observed(scope, thread)
        phase = next((p for p in observed['phases'] if p['id'] == phase_id and p['thread_id'] == thread and p['turn_id'] == turn), None)
        index = None
        history_error = None
        source_blocked = False
        try:
            self._configure_source(scope, thread)
            previous_path = self.history.cached_path(scope, thread)
            index = self.history.detail_view(scope, thread, turn, phase_id, phase)
            if index is None and previous_path and not previous_path.exists():
                history_error = 'missing'
        except (OSError, ValueError, TypeError) as error:
            history_error = 'unavailable'
            source_blocked = isinstance(error, HistoryChanged) or getattr(error, 'reason', None) is not None
        historical = index.phases.get(phase_id) if index else None
        if historical and (historical['scope_id'], historical['thread_id'], historical['turn_id']) != (scope, thread, turn):
            historical = None
        if index:
            phase = merge_history_phase(phase, historical, read_complete=index.read_complete)
        if phase and index:
            phase = apply_model_membership(phase, settled_model_items(
                index.turns.values(), index.phases.values(), read_complete=index.read_complete, quality=index.quality))
        branched = bool(index and index.source and index.source.branched)
        if (phase and phase.get('history_retired') or
                index and index.read_complete and branched and historical is None and
                (phase is None or phase['status'] not in ('active', 'waiting'))):
            return {'version': DETAIL_VERSION, 'state': 'not_retained', 'phase': phase,
                    'source': 'observed_metadata', 'sections': [], 'attachments': []}
        phase = display_phase(phase) if phase else None
        if not phase:
            return {'version': DETAIL_VERSION, 'state': history_error or
                    ('history_unavailable' if index and not index.read_complete else 'content_unavailable'),
                    'sections': [], 'attachments': []}
        relations = {r['id']: r for r in observed['relations']}
        if index:
            relations.update(index.relations)
        evidence = list(observed['phases']) + (list(index.phases.values()) if index else [])
        present_coordination([phase], relations.values(), scope, evidence)
        if phase.get('call_id') and phase.get('namespace') == 'input':
            related = next((p['id'] for p in observed['phases'] if p['thread_id'] == thread and
                            p['turn_id'] == turn and p['namespace'] == 'canonical' and p['item_id'] == phase['call_id']), None)
            if related is None and index:
                related = next((p['id'] for p in index.phases.values() if p['turn_id'] == turn and
                                p['namespace'] == 'canonical' and p['item_id'] == phase['call_id']), None)
            if related:
                phase['related_phase_ids'] = [related]
        result = None
        if index:
            try:
                result = index.detail(phase)
            except HistoryChanged:
                self.history.invalidate(scope, thread)
                result = {'state': 'unavailable', 'sections': [], 'attachments': []}
                source_blocked = True
            except FileNotFoundError:
                result = {'state': 'missing', 'sections': [], 'attachments': []}
            except (OSError, ValueError, TypeError) as error:
                result = {'state': 'unavailable', 'sections': [], 'attachments': []}
                source_blocked = source_blocked or getattr(error, 'reason', None) is not None
        if phase.get('item_type') == 'contextCompaction' and not history_error:
            result = {'state': 'no_content', 'sections': [], 'attachments': [],
                      'source': 'codex_history' if historical else 'observed_metadata'}
        if self.book:
            self.book.pin(phase_id)
            if (not result or not result.get('sections')) and (not source_blocked or phase['status'] in ('active', 'waiting')):
                live = self.book.live_detail(phase)
                if live and live['text']:
                    sections = ([raw_section('input', '参数', live['text']), raw_section('output', '结果')]
                                if phase['namespace'] == 'input' else
                                [raw_section('input', '参数'), raw_section('output', '结果', live['text'])]
                                if phase['track'] == 'tool' else
                                [{'id': 'live', 'title': '实时内容', 'title_message': {'key': 'detail.live', 'params': {}}, 'format': 'code', 'text': live['text']}])
                    result = {'state': 'partial', 'sections': sections,
                              'attachments': [], 'source': 'observed_stream'}
        fallback = ('pending' if phase['status'] in ('active', 'waiting') else
                    'not_retained' if index and phase['namespace'] == 'input' and phase.get('end_ms') is None else
                    'content_unavailable' if index else 'history_unavailable')
        result = result or {'state': history_error or fallback, 'sections': [], 'attachments': []}
        return paginate(result, phase, section_id, offset, limit, version)

    def query(self, request):
        request = TimelineRequest.parse(request)
        scope, thread = request.scope_id, request.thread_id
        # Serialise each provider's offset/index updates, not the collector event loop.
        with self.history.lock:
            if request.action == 'detail':
                return self.detail(scope, thread, request.turn_id, request.phase_id,
                                   request.section_id, request.offset, page_token=request.page_token)
            if request.action == 'timeline':
                return self.bundle(scope, thread, request.turn_id)
        raise ValueError('unsupported_query')


class QueryServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, path, query):
        self.query = query
        self.capacity = threading.BoundedSemaphore(3)
        Path(path).unlink(missing_ok=True)
        super().__init__(str(path), QueryHandler)
        Path(path).chmod(0o600)

    def process_request(self, request, client_address):
        if not self.capacity.acquire(blocking=False):
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.capacity.release()


class QueryHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(10)
        try:
            data = self.rfile.readline(32769)
            if len(data) > 32768 or not data.endswith(b'\n'):
                return
            result = self.server.query.query(json.loads(data))
        except (OSError, ValueError, TypeError, KeyError):
            result = {'version': 1, 'error': 'query_unavailable'}
        try:
            self.wfile.write(encode_response(result))
        except OSError:
            pass


def query_runtime(runtime, request):
    """Does not create runtime, start observation, resume a chat, or change config."""
    request = validated_request(request)
    runtime = Path(runtime)
    connected = False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(10)
            connection.connect(str(runtime / 'tl.sock'))
            connected = True
            connection.sendall(json.dumps(request).encode() + b'\n')
            with connection.makefile('rb') as response:
                return read_response(response)
    except OSError:
        if connected:
            return failure('query_unavailable')
        try:
            if collector_alive(runtime):
                return failure('query_unavailable')
        except OSError:
            return failure('query_unavailable')
        return TimelineQuery(metadata=metadata_path(runtime)).query(request)
