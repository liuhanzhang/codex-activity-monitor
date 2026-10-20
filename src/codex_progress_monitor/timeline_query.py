"""Read-only timeline queries, separate from both Codex and the producer socket."""

from copy import deepcopy
import json
from pathlib import Path
import socket
import socketserver
import threading
import time

from .history import HistoryProvider, raw_section
from .timeline import display_phase, identity, merge_phase
from .timeline_storage import metadata_path, read_records
from .agent_presentation import present_agents, present_coordination, scoped_status


class TimelineQuery:
    def __init__(self, book=None, metadata=None):
        self.book = book
        self.metadata = metadata
        self.history = HistoryProvider()

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

        def load(owner):
            # Many relation records can refer to the same child. Refresh and
            # merge each history once per query, including unavailable history.
            if owner in loaded:
                return loaded[owner]
            loaded[owner] = None
            try:
                index = self.history.index(scope, owner)
                if not index:
                    return None
                revisions.append(index.revision)
                quality.update(index.quality)
                for p in index.phases.values():
                    phases[p['id']] = merge_phase(phases.get(p['id']), p)
                for t in index.turns.values():
                    key = (owner, t['turn_id']); old = turns.get(key, {})
                    merged = {**old, **{k: v for k, v in t.items() if v is not None}}
                    if old.get('status') == 'unknown' and t.get('status') == 'active':
                        merged['status'] = 'unknown'
                    turns[key] = merged
                relations.update(index.relations)
                loaded[owner] = index
                return index
            except (OSError, ValueError, TypeError):
                quality.add('history_unavailable')
                return None

        root_index = load(thread)
        root_turns = sorted((t for t in turns.values() if t['thread_id'] == thread),
                            key=lambda t: t.get('start_ms') or t.get('updated_ms') or 0, reverse=True)
        current = next((t['turn_id'] for t in root_turns if t['status'] == 'active'), None)
        selected = selected or current or (root_turns[0]['turn_id'] if root_turns else None)
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
                visited.add(child)
                index = load(child)
                child_turns = [t for t in turns.values() if t['thread_id'] == child]
                linked = [t for t in child_turns if t.get('parent_turn_id') == parent_turn]
                # A newly spawned thread's initial turn is the spawn's work. Reuse needs explicit attribution.
                spawned = any(r['child_thread_id'] == child and r.get('operation') in ('spawnAgent', 'spawn_agent', 'spawn') for r in candidates)
                if not linked and spawned and child_turns:
                    linked = [min(child_turns, key=lambda t: t.get('start_ms') or t.get('updated_ms') or 0)]
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
        owned = [p for p in phases.values() if (p['thread_id'], p['turn_id']) in owners and not p.get('mirrored')]
        by_agent = {}
        for phase in owned:
            by_agent.setdefault(phase['thread_id'], []).append(phase)
        for agent in agents:
            agent['status'] = scoped_status(agent_turns.get(agent['thread_id'], []), by_agent.get(agent['thread_id'], []))
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
                          p['turn_id'] != selected and p['status'] == 'active'})
        earlier_relations = {}
        for relation in list(relations.values()):
            if relation['parent_thread_id'] != thread or relation['parent_turn_id'] == selected:
                continue
            earlier_relations.setdefault(relation['child_thread_id'], []).append(relation)
        for child, candidates in earlier_relations.items():
            load(child)
            child_turns = [t for t in turns.values() if t['thread_id'] == child]
            first = min(child_turns, key=lambda t: t.get('start_ms') or t.get('updated_ms') or 0) if child_turns else None
            active = [p for p in phases.values() if p['thread_id'] == child and p['status'] == 'active']
            for p in active:
                owned = turns.get((child, p['turn_id']), {})
                for relation in candidates:
                    linked = owned.get('parent_turn_id') == relation['parent_turn_id'] or (
                        relation.get('operation') in ('spawnAgent', 'spawn_agent', 'spawn') and first and p['turn_id'] == first['turn_id'])
                    if linked:
                        earlier.append(relation['parent_turn_id'])
        earlier = sorted(set(earlier))
        recent = root_turns[:6]
        for t in root_turns[6:]:
            if t['turn_id'] in earlier or t['turn_id'] == selected:
                recent.append(t)
        return {'version': 1, 'scope_id': scope, 'thread_id': thread, 'selected_turn_id': selected,
                'current_turn_id': current, 'turns': recent, 'agents': agents,
                'phases': [p for p in visible if p['track'] != 'marker'],
                'markers': [p for p in visible if p['track'] == 'marker'],
                'origin_ms': start, 'end_ms': end, 'earlier_active_turns': earlier,
                'quality': sorted(quality), 'revision': identity(revisions, selected), 'now_ms': time.time() * 1000}

    def detail(self, scope, thread, turn, phase_id, section_id=None, offset=0, limit=262144):
        index = None
        history_error = None
        try:
            previous_index = self.history.indices.get((scope, thread))
            index = self.history.index(scope, thread)
            if index is None and previous_index and not previous_index.path.exists():
                history_error = 'missing'
        except (OSError, ValueError, TypeError):
            history_error = 'unavailable'
        observed = self._observed(scope, thread)
        phase = next((p for p in observed['phases'] if p['id'] == phase_id and p['thread_id'] == thread and p['turn_id'] == turn), None)
        historical = index.phases.get(phase_id) if index else None
        if historical:
            phase = merge_phase(phase, historical)
        phase = display_phase(phase) if phase else None
        if not phase:
            return {'version': 1, 'state': 'content_unavailable', 'sections': [], 'attachments': []}
        relations = {r['id']: r for r in observed['relations']}
        if index:
            relations.update(index.relations)
        present_coordination([phase], relations.values(), scope)
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
            except FileNotFoundError:
                result = {'state': 'missing', 'sections': [], 'attachments': []}
            except (OSError, ValueError, TypeError):
                result = {'state': 'unavailable', 'sections': [], 'attachments': []}
        if phase.get('item_type') == 'contextCompaction' and not history_error:
            result = {'state': 'no_content', 'sections': [], 'attachments': [],
                      'source': 'codex_history' if historical else 'observed_metadata'}
        if self.book:
            self.book.pin(phase_id)
            if not result or not result.get('sections'):
                live = self.book.live_detail(phase)
                if live and live['text']:
                    sections = ([raw_section('input', '参数', live['text']), raw_section('output', '结果')]
                                if phase['namespace'] == 'input' else
                                [raw_section('input', '参数'), raw_section('output', '结果', live['text'])]
                                if phase['track'] == 'tool' else
                                [{'id': 'live', 'title': '实时内容', 'format': 'code', 'text': live['text']}])
                    result = {'state': 'partial', 'sections': sections,
                              'attachments': [], 'source': 'observed_stream'}
        fallback = ('pending' if phase['status'] in ('active', 'waiting') else
                    'not_retained' if index and phase['namespace'] == 'input' and phase.get('end_ms') is None else
                    'content_unavailable' if index else 'history_unavailable')
        result = result or {'state': history_error or fallback, 'sections': [], 'attachments': []}
        result = deepcopy(result)
        for s in result['sections']:
            text = s['text']; start = max(0, int(offset)) if s['id'] == section_id else 0
            stop = start + min(max(int(limit), 1), 262144)
            s.update(text=text[start:stop], offset=start, total_chars=len(text), next_offset=stop if stop < len(text) else None)
        result.update(version=1, phase=phase)
        return result

    def query(self, request):
        scope, thread = request.get('scope_id'), request.get('thread_id')
        if not isinstance(scope, str) or not isinstance(thread, str) or not thread:
            raise ValueError('invalid_identity')
        # Serialise each provider's offset/index updates, not the collector event loop.
        with self.history.lock:
            if request.get('action') == 'detail':
                return self.detail(scope, thread, request.get('turn_id'), request.get('phase_id'),
                                   request.get('section_id'), request.get('offset', 0))
            if request.get('action') == 'timeline':
                return self.bundle(scope, thread, request.get('turn_id'))
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
            self.wfile.write(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode() + b'\n')
        except OSError:
            pass


def query_runtime(runtime, request):
    """Does not create runtime, start observation, resume a chat, or change config."""
    runtime = Path(runtime)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(10)
            connection.connect(str(runtime / 'tl.sock'))
            connection.sendall(json.dumps(request).encode() + b'\n')
            with connection.makefile('rb') as response:
                return json.loads(response.readline(16 * 1024 * 1024))
    except (OSError, ValueError):
        return TimelineQuery(metadata=metadata_path(runtime)).query(request)
