"""Independent local aggregator; closing it cannot block the CLI bridge."""

import fcntl
import json
import os
import selectors
import signal
import socket
import time
import uuid

from .engine import Engine
from .events import Event
from .runtime import atomic_json,read_snapshot,source_identity
from .i18n import message, render_message


def serve(runtime):
    lock=(runtime/'collector.lock').open('a+')
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError('Collector is already running')
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
    engine=Engine()
    implementation=source_identity('collector','engine','progress_state','events','usage','metrics','runtime','tool_labels','i18n','messages_zh_cn')
    previous=read_snapshot(runtime)
    historical=previous.get('threads',[]) if time.time()-(previous.get('updated_at') or 0)<600 else []
    if historical:
        engine.usage.restore(previous.get('usage_recovery'))
    for thread in historical:
        thread['connected']=False
        thread['token_rates']={'text':None,'tool_input':None}
        thread['generation_rate']=None
        if thread.get('status')=='inProgress' or thread.get('active_count'):
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
    atomic_json(runtime/'collector.json',{'pid':os.getpid(),'started_at':time.time(),'token':token,
                                        'implementation':implementation})
    next_snapshot=0
    def close_client(conn,reason='shutdown'):
        engine.counts['ipc_close_'+reason]+=1
        state=clients.pop(conn,{})
        if state.get('backend') and owners.get(state['backend']) is conn:
            owners.pop(state['backend'],None)
            engine.ingest(Event(state['backend'],'lifecycle',{'type':'bridge/disconnected'},time.monotonic(),time.time()))
        selector.unregister(conn)
        conn.close()
    try:
        while running:
            for key,_ in selector.select(timeout=0.05):
                conn=key.fileobj
                if conn is listener:
                    connection,_=listener.accept()
                    connection.setblocking(False)
                    if len(clients)>=32:
                        connection.close()
                        continue
                    clients[connection]={'buffer':b'','backend':None,'last_seen':time.monotonic()}
                    selector.register(connection,selectors.EVENT_READ,True)
                    continue
                state=clients[conn]
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
                        engine.backends.setdefault(row['backend'],{})['connected']=True
                        engine.ingest(row)
                    except (ValueError,TypeError,KeyError,AttributeError):
                        engine.counts['invalid_ipc']+=1
                        if state.get('backend') and owners.get(state['backend']) is conn:
                            engine.ingest(Event(state['backend'],'lifecycle',{'type':'bridge/gap'},time.monotonic(),time.time()))
            now=time.monotonic()
            for conn,state in list(clients.items()):
                if now-state['last_seen']>10:
                    close_client(conn,'idle')
            if now>=next_snapshot:
                engine.tick(now)
                snapshot=engine.snapshot(now,time.time())
                retain_history(snapshot)
                snapshot['collector_pid']=os.getpid()
                snapshot['implementation']=implementation
                snapshot['running']=True
                atomic_json(runtime/'snapshot.json',snapshot)
                next_snapshot=now+0.25
    finally:
        for conn in list(clients):
            close_client(conn)
        engine.tick(time.monotonic())
        snapshot=engine.snapshot(time.monotonic(),time.time())
        retain_history(snapshot)
        snapshot['running']=False
        snapshot['implementation']=implementation
        atomic_json(runtime/'snapshot.json',snapshot)
        engine.close()
        selector.close()
        listener.close()
        socket_path.unlink(missing_ok=True)
        (runtime/'collector.json').unlink(missing_ok=True)
        lock.close()
