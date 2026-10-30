"""Bounded background session-header lookup; never parses transcripts or holds the event lock."""

from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
from queue import Queue, Empty, Full
import threading
import time

from .agent_presentation import agent_metadata
from .agent_relations import thread_identity
from .overview import annotate_parents
from .history_source import RolloutResolver


def read_header(scope, thread, path=None, resolver=None):
    if not Path(scope).is_absolute() or not thread_identity(thread):
        return None, {'quality': ['identity_unavailable']}
    try:
        source = (resolver or RolloutResolver()).resolve(scope, thread)
        if source is not None:
            logical_path = Path(scope)/source.path.relative_to(Path(scope).resolve())
            return logical_path, source.parts[0].header.agent
    except (OSError, ValueError):
        pass
    return None, {'quality': ['identity_unavailable']}


class AgentIdentityCache:
    def __init__(self):
        self.resolver = RolloutResolver()
        self.ready = OrderedDict()
        self.pending = set()
        self.paths = {}
        self.contexts = OrderedDict()
        self.lock = threading.Lock()
        self.queue = Queue(maxsize=128)
        self.stopping = threading.Event()
        self.worker = threading.Thread(target=self._run, name='agent-headers', daemon=True)
        self.worker.start()

    def get(self, scope, thread):
        key = (scope, thread); now = time.monotonic()
        with self.lock:
            cached = self.ready.get(key)
            if key not in self.pending and (cached is None or now - cached[0] >= 2):
                try:
                    self.queue.put_nowait(key)
                    self.pending.add(key)
                except Full:
                    pass
            return deepcopy(cached[1]) if cached else {}

    def _run(self):
        while not self.stopping.is_set():
            try:
                key = self.queue.get(timeout=.2)
            except Empty:
                continue
            try:
                path, metadata = read_header(*key, self.paths.get(key), self.resolver)
            except (OSError, ValueError, TypeError):
                path, metadata = None, {'quality': ['identity_unavailable']}
            with self.lock:
                self.ready[key] = (time.monotonic(), metadata)
                self.ready.move_to_end(key)
                self.paths[key] = path
                self.pending.discard(key)
                while len(self.ready) > 512:
                    expired, _ = self.ready.popitem(last=False)
                    self.paths.pop(expired, None)
            self.queue.task_done()

    def enrich(self, views):
        now = time.monotonic()
        for view in views:
            key = (view.get('home') or view['backend'], view['id'])
            metadata = self.get(view.get('home') or view['backend'], view['id'])
            view['identity_quality'] = metadata.get('quality', [])
            parent = metadata.get('parent_thread_id')
            conflict = (parent and view.get('parent_thread_id') not in (None, parent)) or (
                metadata.get('thread_source') == 'user' and view.get('parent_thread_id'))
            if conflict:
                view.pop('parent_thread_id', None)
                view['parent_quality'] = ['parent_identity_conflict']
            elif parent:
                view.update(parent_thread_id=parent, parent_source='session_header')
            elif view.get('parent_source') == 'session_header':
                view.pop('parent_thread_id', None)
                view.pop('parent_source', None)
            if metadata.get('thread_source'):
                view['thread_source'] = metadata['thread_source']
            if metadata.get('nickname') and view.get('name') in (None, '', view['id'], view['id'][:8]):
                view['name'] = metadata['nickname']
            # Reconnect metadata can omit an existing user-chat title/cwd. Keep
            # only the last observed scoped context, beyond the activity TTL;
            # header-derived child names still invalidate with their header.
            previous = self.contexts.get(key)
            if previous and view.get('thread_source') == previous[1].get('thread_source') == 'user':
                if view.get('name') in (None, '', view['id'], view['id'][:8]):
                    view['name'] = previous[1].get('name', view.get('name'))
                if not view.get('cwd') and previous[1].get('cwd'):
                    view['cwd'] = previous[1]['cwd']
            keep = {k: deepcopy(view[k]) for k in ('id', 'home', 'backend', 'name', 'cwd', 'model', 'thread_source',
                    'parent_thread_id', 'parent_source', 'parent_quality', 'last_progress_seconds', 'status') if k in view}
            self.contexts[key] = (now, keep)
            self.contexts.move_to_end(key)
        present = {(v.get('home') or v['backend'], v['id']) for v in views}
        # The explicit child header permits an unknown parent context even when
        # that parent has expired. It does not supply a parent turn or status.
        for view in list(views):
            cursor = view
            for _ in range(32):
                parent = cursor.get('parent_thread_id')
                key = (cursor.get('home') or cursor['backend'], parent)
                if not parent or key in present:
                    break
                metadata = self.get(*key)
                old = self.contexts.get(key)
                context = deepcopy(old[1]) if old else dict(id=parent, backend=cursor['backend'],
                    home=cursor.get('home'), name='父聊天 · '+parent[-6:], status='unknown')
                if context.get('parent_source') == 'session_header' and not metadata.get('parent_thread_id'):
                    context.pop('parent_thread_id', None)
                    context.pop('parent_source', None)
                if old and context.get('last_progress_seconds') is not None:
                    context['last_progress_seconds'] += now - old[0]
                for field in ('thread_source', 'parent_thread_id', 'parent_source'):
                    if metadata.get(field):
                        context[field] = metadata[field]
                context.update(context_only=True, has_active_work=False, activities=[], connected=False, visible=True)
                views.append(context); present.add(key); cursor = context
        while len(self.contexts) > 512:
            self.contexts.popitem(last=False)
        annotate_parents(views)
        return views

    def close(self):
        self.stopping.set()
        self.worker.join(timeout=2)
