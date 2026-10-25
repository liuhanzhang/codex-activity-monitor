"""Independent local aggregator; closing it cannot block the CLI bridge."""

import fcntl
import json
import os
import selectors
import signal
import socket
import time
import threading
import uuid

from .engine import Engine
from .events import Event
from .runtime import SnapshotCadence, atomic_json, read_snapshot, snapshot_age, source_identity
from .publication import DetachedSnapshot, RecoveryWriter, encode_frame
from .state_transport import StateServer
from .app_session import load_lease
from .i18n import message, render_message
from .timeline import TimelineBook
from .timeline_storage import TimelineStorage, metadata_path
from .timeline_query import TimelineQuery, QueryServer
from .agent_identity import AgentIdentityCache
from .menu_presentation import menu_presentation
from .history_refresh import HistoryRefresh


def serve(runtime):
    observation = load_lease(runtime)
    lock=(runtime/'collector.lock').open('a+')
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError('Collector is already running')
    # Main owner plus all workers that can still write this runtime. A bounded
    # join never transfers ownership while one of them may publish later.
    ownership_lock = threading.Lock()
    owners_remaining = 4
    def release_ownership():
        nonlocal owners_remaining
        with ownership_lock:
            owners_remaining -= 1
            if owners_remaining == 0:
                lock.close()
    os.umask(0o077)
    socket_path=runtime/'events.sock'
    socket_path.unlink(missing_ok=True)
    listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    socket_path.chmod(0o600)
    listener.listen(16)
    listener.setblocking(False)
    selector=selectors.DefaultSelector()
    selector.register(listener,selectors.EVENT_READ,None)
    clients={}
    owners={}
    timeline_store = None
    timeline_error = None
    try:
        timeline_store = TimelineStorage(metadata_path(runtime), on_exit=release_ownership)
    except (OSError, ValueError):
        release_ownership()
        timeline_error = 'metadata_unavailable'
    timeline = TimelineBook(timeline_store)
    engine=Engine(timeline=timeline)
    cadence = SnapshotCadence()
    archive_cadence = SnapshotCadence(interval=10)
    history = HistoryRefresh()
    identities = AgentIdentityCache()
    query_server = None
    try:
        query_server = QueryServer(runtime/'tl.sock', TimelineQuery(timeline, history=history.provider))
        threading.Thread(target=query_server.serve_forever, kwargs={'poll_interval': .1}, daemon=True,
                         name='timeline-queries').start()
    except OSError:
        timeline_error = 'query_unavailable'
    implementation=source_identity('collector','app_session','engine','overview','menu_presentation','progress_state','events','usage','metrics','runtime','tool_labels','i18n','messages_zh_cn',
                                   'state','timeline','timeline_storage','timeline_query','history','reconciliation','agent_presentation','agent_relations','agent_identity',
                                   'publication','state_transport','observation_lease','history_read','content_version',
                                   'resource_budget','identity_budget','query_request','query_response','json_encoding','history_index','history_content','history_refresh','history_contract','tokenizer_process')
    previous=read_snapshot(runtime)
    previous_age = snapshot_age(previous)
    historical = previous.get('threads', []) if previous_age is not None and -5 <= previous_age < 600 else []
    if historical:
        engine.usage.restore(previous.get('usage_recovery'))
    for thread in historical:
        if isinstance(thread.get('usage'), dict):
            thread['usage'].pop('latest_reasoning', None)
        # Old snapshots inferred these edges from communication. Re-verify them
        # from session headers / new explicit live creation evidence.
        if thread.get('parent_source') not in ('session_header', 'spawn_call'):
            thread.pop('parent_thread_id', None)
        thread['subagents'] = {}
        thread['connected']=False
        thread['token_rates']={'text':None,'tool_input':None}
        thread['generation_rate']=None
        if thread.get('status')=='inProgress' or thread.get('active_count') or thread.get('has_unresolved_work'):
            unavailable = message('activity.unknown')
            thread.update(status='unknown',phase='unknown',phase_label=render_message(unavailable),display_label=render_message(unavailable),
                          display_message=unavailable,
                          display_active_count=0,display_seconds=None,active_count=0,phase_seconds=None,has_active_work=False)
            thread['quality']=sorted(set(thread.get('quality',[])) | {'collector_restarted'})
        for activity in thread.get('activities',[]):
            if activity.get('active'):
                activity.update(active=False,status='unknown')
                activity.pop('display_message', None)
            for stream in activity.get('streams',[]):
                stream['token_rate']=None
    recovered_at=time.time()
    restored = engine.restore_unresolved(historical, previous.get('updated_at'))
    historical = [t for t in historical if (t.get('home'), t['id']) not in restored]
    def retain_history(snapshot):
        if time.time()-recovered_at>=600:
            return
        older={(t.get('home'),t['id']):t for t in historical}
        fresh=set()
        for index,thread in enumerate(snapshot['threads']):
            identity=(thread.get('home'),thread['id'])
            fresh.add(identity)
            previous=older.get(identity)
            if previous:
                if not thread.get('name') or thread['name']==thread['id'][:8]:
                    thread['name']=previous.get('name',thread.get('name'))
                if not thread.get('cwd'):
                    thread['cwd']=previous.get('cwd','')
            if previous and thread['status']=='idle' and not thread['activities']:
                # Metadata replay must not erase the restored final metrics.
                snapshot['threads'][index]={**previous,'backend':thread['backend'],'name':thread['name'],'cwd':thread.get('cwd',''),
                                           'ephemeral':thread.get('ephemeral') if isinstance(thread.get('ephemeral'),bool) else previous.get('ephemeral'),
                                           'thread_source':thread.get('thread_source') or previous.get('thread_source'),
                                           'connected':thread['connected'] if previous['status']!='unknown' else False}
            elif previous:
                old_items={a['id']:a for a in previous.get('activities',[]) if not a['active'] and a['status'] not in ('unknown','inProgress')}
                for activity in thread.get('activities',[]):
                    old=old_items.get(activity['id'])
                    if old and not activity['active'] and old['status']==activity['status'] and not activity['streams']:
                        activity.update(streams=old['streams'],summary_chars=old['summary_chars'],summary_parts=old['summary_parts'],
                                        duration_seconds=old['duration_seconds'],start_known=old['start_known'])
        snapshot['threads'].extend(t.copy() for key,t in older.items() if key not in fresh)
        elapsed=time.time()-recovered_at
        for thread in snapshot['threads']:
            identity=(thread.get('home'),thread['id'])
            if identity in older and thread['activities']==older[identity]['activities']:
                if thread.get('last_progress_seconds') is not None:
                    thread['last_progress_seconds']=older[identity]['last_progress_seconds']+elapsed
    running=True
    def stop(*_):
        nonlocal running
        running=False
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    token=uuid.uuid4().hex
    instance_id=uuid.uuid4().hex
    atomic_json(runtime/'collector.json',{'pid':os.getpid(),'started_at':time.time(),'token':token,
                                        'instance_id':instance_id,'state_protocol':1,'lease_protocol':2,
                                        'implementation':implementation})
    writer = RecoveryWriter(runtime, on_exit=release_ownership)
    state_server = None
    try:
        state_server = StateServer(runtime/'state.sock', diagnostics=writer.diagnostics,
                                   on_exit=release_ownership, lease=observation, control_token=token,
                                   instance_id=instance_id)
    except OSError:
        # During migration the file reader still works. New readers report the
        # unavailable transport instead of treating a recovery file as live.
        release_ownership()

    def publish(snapshot, *, archive=False):
        detached = DetachedSnapshot.capture(snapshot)
        sequence = state_server.publish(detached) if state_server else None
        if archive:
            writer.submit(detached, instance_id=instance_id, state_sequence=sequence)

    # Keep token sampling responsive without publishing every tick to disk.
    next_tick=0
    next_history=0
    last_progress=time.monotonic()
    def close_client(conn,reason='shutdown'):
        engine.counts['ipc_close_'+reason]+=1
        state=clients.pop(conn,{})
        if state.get('backend') and owners.get(state['backend']) is conn:
            owners.pop(state['backend'],None)
            with timeline.lock:
                engine.ingest(Event(state['backend'],'lifecycle',{'type':'bridge/disconnected'},time.monotonic(),time.time()))
        selector.unregister(conn)
        conn.close()
    try:
        next_owner_check=0
        while running:
            if time.monotonic()>=next_owner_check:
                if not observation.is_observing():
                    break
                next_owner_check=time.monotonic()+0.5
            for key,events in selector.select(timeout=0.05):
                conn=key.fileobj
                if conn is listener:
                    connection,_=listener.accept()
                    connection.setblocking(False)
                    if len(clients)>=32:
                        connection.close()
                        continue
                    clients[connection]={'buffer':b'','backend':None,'last_seen':time.monotonic(),
                                         'outgoing':bytearray(),'write_deadline':0}
                    selector.register(connection,selectors.EVENT_READ,True)
                    continue
                state=clients[conn]
                if events & selectors.EVENT_WRITE:
                    try:
                        sent=conn.send(state['outgoing'])
                        if not sent:
                            raise ConnectionError('closed observation control')
                        del state['outgoing'][:sent]
                        if not state['outgoing']:
                            selector.modify(conn, selectors.EVENT_READ)
                            state['write_deadline']=0
                    except BlockingIOError:
                        pass
                    except OSError:
                        close_client(conn,'permit_send_error')
                        continue
                if not events & selectors.EVENT_READ:
                    continue
                try:
                    data=conn.recv(65536)
                except BlockingIOError:
                    continue
                except OSError:
                    close_client(conn,'receive_error')
                    continue
                if not data:
                    close_client(conn,'eof')
                    continue
                state['buffer']+=data
                state['last_seen']=time.monotonic()
                # Bound framing even when a producer sends malformed input.
                if len(state['buffer'])>12*1024*1024:
                    engine.counts['oversized_ipc']+=1
                    close_client(conn,'oversized')
                    continue
                lines=state['buffer'].split(b'\n')
                state['buffer']=lines.pop()
                for line in lines:
                    try:
                        row=json.loads(line)
                        if row.get('control')=='stop' and row.get('token')==token:
                            running=False
                            continue
                        if row.get('control')=='observe':
                            backend,nonce=row.get('backend'),row.get('nonce')
                            if (type(row.get('protocol_version')) is not int or row['protocol_version'] != 1 or
                                    not isinstance(backend,str) or not 1 <= len(backend) <= 128 or
                                    not isinstance(nonce,str) or not 1 <= len(nonce) <= 128 or
                                    state['backend'] not in (None,backend)):
                                raise ValueError('Invalid observation handshake')
                            if state['backend'] is None:
                                state['backend']=backend
                                owners[backend]=conn
                            state['permit_protocol']=1
                            tick_age=max(0,time.monotonic()-last_progress)*1000
                            allowed=observation.is_observing() and tick_age < 1000 and owners.get(backend) is conn
                            reply=encode_frame({'protocol_version':1,'kind':'observe_permit','nonce':nonce,
                                'instance_id':instance_id,'observing':allowed,'tick_age_ms':tick_age,
                                'ttl_ms':min(1500,observation.remaining()*1000) if allowed else 0})
                            if len(state['outgoing'])+len(reply)>4096:
                                raise ValueError('Observation control backpressure')
                            if not state['outgoing']:
                                state['write_deadline']=time.monotonic()+.75
                            state['outgoing'].extend(reply)
                            selector.modify(conn,selectors.EVENT_READ|selectors.EVENT_WRITE)
                            continue
                        if not isinstance(row.get('event'),dict) or not isinstance(row.get('backend'),str):
                            raise ValueError('Invalid event frame')
                        if state['backend'] is None:
                            state['backend']=row['backend']
                            owners[row['backend']]=conn
                        elif state['backend']!=row['backend']:
                            raise ValueError('A connection cannot change backend identity')
                        if owners.get(row['backend']) is not conn:
                            engine.counts['superseded_ipc_frames']+=1
                            continue
                        if not observation.is_observing():
                            continue
                        if observation.managed and state.get('permit_protocol') != 1 and row.get('event',{}).get('type') != 'bridge/started':
                            engine.counts['unsupported_bridge_frames']+=1
                            continue
                        with timeline.lock:
                            engine.backends.setdefault(row['backend'],{})['connected']=True
                            engine.ingest(row)
                    except (ValueError,TypeError,KeyError,AttributeError):
                        engine.counts['invalid_ipc']+=1
                        if state.get('backend') and owners.get(state['backend']) is conn:
                            with timeline.lock:
                                engine.ingest(Event(state['backend'],'lifecycle',{'type':'bridge/gap'},time.monotonic(),time.time()))
            now=time.monotonic()
            for conn,state in list(clients.items()):
                if state['write_deadline'] and now>=state['write_deadline']:
                    close_client(conn,'permit_timeout')
                elif now-state['last_seen']>10:
                    close_client(conn,'idle')
            if now>=next_tick:
                with timeline.lock:
                    engine.tick(now)
                    timeline.prune()
                    for summary in history.drain():
                        engine.apply_history(summary)
                    if now >= next_history:
                        history.schedule(engine.history_requests(), now)
                        next_history = now + 1
                    snapshot=engine.snapshot(now,time.time())
                    snapshot['resource_diagnostics']['history_cache'] = history.provider.diagnostics()
                    snapshot['resource_diagnostics']['history_refresh'] = history.diagnostics()
                    if timeline_store:
                        snapshot['resource_diagnostics']['metadata'] = timeline_store.diagnostics()
                    snapshot['timeline_summary']={'version':1,'revision':timeline.revision,
                                                   'available':query_server is not None,
                                                   'quality':[e for e in (timeline_error, timeline_store.error if timeline_store else None) if e]}
                retain_history(snapshot)
                identities.enrich(snapshot['threads'])
                snapshot['menu_presentation'] = menu_presentation(engine, snapshot['threads'], snapshot['overview'])
                if cadence.should_publish(snapshot, now):
                    snapshot['collector_pid']=os.getpid()
                    snapshot['implementation']=implementation
                    snapshot['running']=True
                    publish(snapshot, archive=archive_cadence.should_publish(snapshot, now))
                if state_server:
                    state_server.progress()
                last_progress=time.monotonic()
                next_tick=now+0.25
    finally:
        history.close()
        if query_server:
            query_server.shutdown()
            query_server.server_close()
            (runtime/'tl.sock').unlink(missing_ok=True)
        for conn in list(clients):
            close_client(conn)
        with timeline.lock:
            for backend in engine.backends.values():
                timeline.gap(backend.get('home') or '', time.time()*1000)
        if timeline_store:
            timeline_store.close()
        engine.tick(time.monotonic())
        snapshot=engine.snapshot(time.monotonic(),time.time())
        retain_history(snapshot)
        identities.enrich(snapshot['threads'])
        identities.close()
        snapshot['menu_presentation'] = menu_presentation(engine, snapshot['threads'], snapshot['overview'])
        snapshot['running']=False
        snapshot['implementation']=implementation
        snapshot['collector_pid']=os.getpid()
        publish(snapshot, archive=True)
        engine.close()
        selector.close()
        listener.close()
        socket_path.unlink(missing_ok=True)
        if state_server:
            state_server.close()
        writer.close(timeout=2)
        (runtime/'collector.json').unlink(missing_ok=True)
        release_ownership()
