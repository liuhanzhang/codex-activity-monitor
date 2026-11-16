"""Discover and follow the Antigravity desktop independently of its windows."""

from collections import deque
from dataclasses import dataclass, field
import http.client
from pathlib import Path
import struct
import subprocess
import threading
import time
from uuid import uuid4

from .antigravity_transport import LocalRPC, VERBOSITY, discover, installed_apps, uuid_string


MAX_BYTES = 16 * 1024 * 1024
MAX_SESSIONS = 16
WARM_SESSIONS = 3
RECENT_SESSIONS = 64
RPC_ERRORS = (OSError, ValueError, http.client.HTTPException, struct.error, subprocess.SubprocessError)
LIVE = {'BUSY', 'RUNNING', 'CANCELING'}


def active(summary):
    return str(summary.get('status', '')).removeprefix('CASCADE_RUN_STATUS_') in LIVE


@dataclass(eq=False)
class Subscription:
    conversation: str
    stop: threading.Event = field(default_factory=threading.Event)
    stream: object = None


class AntigravitySource:
    def __init__(self, discover_endpoint=discover, apps=installed_apps, client_factory=LocalRPC):
        self.discover, self.apps, self.client_factory = discover_endpoint, apps, client_factory
        self.stop = threading.Event()
        self.condition = threading.Condition()
        self.inbox = deque()
        self.queued_bytes = 0
        self.directory = {}
        self.visible = set()
        self.subscriptions = {}
        self.catalogue_stream = None
        self.client = None
        self.worker = threading.Thread(target=self._run, daemon=True, name='antigravity-desktop')
        self.worker.start()

    def _publish(self, kind, value, size=0, subscription=None):
        at, wall = time.monotonic(), time.time()
        with self.condition:
            while not self.stop.is_set() and not (subscription and subscription.stop.is_set()):
                if len(self.inbox) < 128 and self.queued_bytes + size <= MAX_BYTES:
                    self.inbox.append((kind, value, at, wall, size))
                    self.queued_bytes += size
                    return
                self.condition.wait(.25)

    def drain(self):
        with self.condition:
            records = list(self.inbox)
            self.inbox.clear()
            self.queued_bytes = 0
            self.condition.notify_all()
            return records

    def _status(self, state, scope=None, version=None, **extra):
        self._publish('status', dict(state=state, scope=scope, version=version, **extra))

    def _run(self):
        while not self.stop.is_set():
            try:
                apps = self.apps()
                endpoint = self.discover(apps)
                if endpoint is None:
                    self._status('waiting' if apps else 'not_installed')
                    self.stop.wait(2 if apps else 10)
                    continue
                self._watch(self.client_factory(endpoint))
            except RPC_ERRORS:
                self._status('unavailable')
            finally:
                self._detach()
            self.stop.wait(1)

    def _watch(self, client):
        scope = str(client.endpoint.scope)
        result = client.call('GetAllCascadeTrajectories', {'excludeSubtrajectories': False})
        summaries = result.get('trajectorySummaries', {})
        if not isinstance(summaries, dict):
            raise ValueError('invalid_antigravity_directory')
        with self.condition:
            if self.stop.is_set():
                return
            self.client = client
        try:
            configs = (client.call('GetUserStatus', {}).get('userStatus', {})
                       .get('cascadeModelConfigData', {}).get('clientModelConfigs', []))
            models = {row['modelOrAlias']['model']: row['label'][:100] for row in configs
                      if isinstance(row.get('modelOrAlias', {}).get('model'), str) and isinstance(row.get('label'), str)}
            self._publish('models', models)
            del configs
        except (*RPC_ERRORS, AttributeError, TypeError, KeyError):
            pass
        self._status('connected', scope, client.endpoint.version)
        self._apply_directory(client, summaries, [])
        while not self.stop.is_set():
            try:
                stream = client.stream('JetboxSubscribeToSummaries', {})
                with self.condition:
                    if self.stop.is_set():
                        return
                    self.catalogue_stream = stream
                for frame, _ in stream.frames():
                    self._apply_directory(client, frame.get('updates', {}), frame.get('deletes', []))
            except RPC_ERRORS:
                pass
            finally:
                self.catalogue_stream = None
            # An older server may still offer a snapshot directory. Reconcile
            # with it before reopening the reactive feed after a lost socket.
            for _ in range(5):
                if self.stop.wait(1):
                    return
                snapshot = client.call('GetAllCascadeTrajectories', {'excludeSubtrajectories': False})
                summaries = snapshot.get('trajectorySummaries', {})
                self._apply_directory(client, summaries, self.directory.keys() - summaries.keys())

    def _apply_directory(self, client, updates, deleted):
        if self.stop.is_set():
            return
        if not isinstance(updates, dict) or not isinstance(deleted, (list, set)):
            raise ValueError('invalid_antigravity_directory')
        scope = str(client.endpoint.scope)
        changed = {}
        for conversation, summary in updates.items():
            if uuid_string(conversation) and isinstance(summary, dict) and self.directory.get(conversation) != summary:
                self.directory[conversation] = summary
                changed[conversation] = summary
        removed = [key for key in deleted if uuid_string(key) and key in self.directory]
        for key in removed:
            self.directory.pop(key)
        in_progress = [key for key, value in self.directory.items() if active(value)]
        idle = sorted((key for key in self.directory if key not in in_progress),
                      key=lambda key: str(self.directory[key].get('lastModifiedTime', '')), reverse=True)
        parents = {summary.get('trajectoryMetadata', {}).get('parentConversationId')
                   for summary in self.directory.values()
                   if isinstance(summary.get('trajectoryMetadata'), dict)}
        parent_ids = [key for key in idle if key in parents]
        visible = set(in_progress + idle[:RECENT_SESSIONS] + parent_ids[:MAX_SESSIONS])
        listed = {key: self.directory[key] for key in visible if key in changed or key not in self.visible}
        hidden = list(self.visible - visible)
        if listed or hidden:
            self._publish('directory', (scope, listed, hidden))
        self.visible = visible
        priority = dict.fromkeys(in_progress + idle[:WARM_SESSIONS] + parent_ids)
        wanted = set(list(priority)[:MAX_SESSIONS])
        for key in self.subscriptions.keys() - wanted:
            self._cancel(self.subscriptions.pop(key))
        for key in wanted - self.subscriptions.keys():
            sub = self.subscriptions[key] = Subscription(key)
            threading.Thread(target=self._follow, args=(client, sub), daemon=True,
                             name='antigravity-conversation').start()
        if len(in_progress) > MAX_SESSIONS:
            self._status('limited', scope, client.endpoint.version, active=len(in_progress))
        else:
            self._status('connected', scope, client.endpoint.version)

    def _follow(self, client, subscription):
        scope, conversation = str(client.endpoint.scope), subscription.conversation
        delay = .25
        while not subscription.stop.is_set() and not self.stop.is_set():
            initial = True
            with self.condition:
                if subscription.stop.is_set() or self.stop.is_set():
                    return
                stream = subscription.stream = client.stream('StreamAgentStateUpdates', {
                    'conversationId': conversation, 'subscriberId': str(uuid4()), 'trajectoryVerbosity': VERBOSITY,
                })
            try:
                for frame, size in stream.frames():
                    self._publish('frame', (scope, conversation, frame, initial), size, subscription)
                    initial = False
                    delay = .25
            except RPC_ERRORS:
                pass
            if subscription.stop.is_set() or self.stop.is_set():
                return
            self._publish('gap', (scope, conversation), subscription=subscription)
            if subscription.stop.wait(delay):
                return
            delay = min(delay * 2, 5)

    def read_steps(self, scope, conversation, index):
        with self.condition:
            client = self.client if not self.stop.is_set() else None
        if client is None or Path(scope) != client.endpoint.scope or not uuid_string(conversation):
            return []
        response = client.call('GetCascadeTrajectorySteps', {'cascadeId': conversation, 'stepOffset': index,
                               'trajectoryVerbosity': VERBOSITY})
        steps = response.get('steps', [])
        return steps if isinstance(steps, list) else []

    def _cancel(self, subscription):
        with self.condition:
            subscription.stop.set()
            if subscription.stream:
                subscription.stream.cancel()
            self.condition.notify_all()

    def _detach(self):
        with self.condition:
            client, self.client = self.client, None
        for subscription in self.subscriptions.values():
            self._cancel(subscription)
        self.subscriptions.clear()
        self.directory.clear()
        self.visible.clear()
        if client is not None:
            self._publish('disconnect', str(client.endpoint.scope))

    def close(self):
        self.stop.set()
        with self.condition:
            if self.catalogue_stream is not None:
                self.catalogue_stream.cancel()
            self.condition.notify_all()
        self.worker.join(timeout=.25)
