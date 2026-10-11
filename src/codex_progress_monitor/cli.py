"""Command line product entry points."""

import argparse
import gzip
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

from .runtime import ROOT,REAL_CLI,atomic_json,read_snapshot,runtime_dir

_children={}


def start(runtime):
    if running(runtime):
        return
    log=runtime/'collector.log'
    if log.exists() and log.stat().st_size>1024*1024:
        log.replace(runtime/'collector.previous.log')
    with log.open('a') as out:
        process=subprocess.Popen([sys.executable,'-m','codex_progress_monitor.cli','--runtime',str(runtime),'collect'],
                                 stdin=subprocess.DEVNULL,stdout=out,stderr=out,start_new_session=True)
        _children[str(runtime)]=process
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        if running(runtime):
            return
        if process.poll() is not None:
            raise RuntimeError('Collector failed to start; inspect '+str(log))
        time.sleep(0.05)
    raise RuntimeError('Collector startup timed out; inspect '+str(log))


def running(runtime):
    snapshot=read_snapshot(runtime)
    return bool(snapshot.get('running') and time.time()-(snapshot.get('updated_at') or 0)<3 and (runtime/'events.sock').exists())


def stop(runtime):
    try:
        control=json.loads((runtime/'collector.json').read_text())
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
            connection.settimeout(1)
            connection.connect(str(runtime/'events.sock'))
            connection.sendall(json.dumps({'control':'stop','token':control['token']}).encode()+b'\n')
    except (OSError,ValueError,KeyError):
        return
    deadline=time.monotonic()+5
    while (runtime/'collector.json').exists() and time.monotonic()<deadline:
        time.sleep(0.05)
    child=_children.pop(str(runtime),None)
    if child:
        child.wait(timeout=5)


def replay(path,output=None):
    from .engine import Engine
    from .events import filter_event,Event
    engine=Engine(asynchronous_counts=False)
    at=0
    opener=gzip.open if str(path).endswith('.gz') else open
    with opener(path,'rt') as source:
        for line in source:
            row=json.loads(line)
            safe=filter_event(row['direction'],row['event'])
            if safe is None:
                continue
            row['event']=safe
            event=Event.from_row(row)
            engine.ingest(event)
            at=max(at,event.at)
            engine.tick(at)
    engine.tick(at+0.3)
    value=engine.snapshot(at,None)
    engine.close()
    if output:
        output.parent.mkdir(parents=True,exist_ok=True)
        atomic_json(output,value)
    else:
        print(json.dumps(value,ensure_ascii=False,indent=2))
    return value


def main(argv=None):
    parser=argparse.ArgumentParser(description='Codex Desktop 本机实时进度')
    parser.add_argument('--runtime',type=Path)
    subs=parser.add_subparsers(dest='command',required=True)
    for name in ('collect','start','stop','status','setup','uninstall','desktop'):
        p=subs.add_parser(name)
        if name in ('setup','desktop'):
            p.add_argument('--real-cli',type=Path,default=REAL_CLI)
        if name=='setup':
            p.add_argument('--launch-agent',action='store_true',help='配置登录启动和 Desktop 默认入口')
        if name=='desktop':
            p.add_argument('--home',type=Path)
            p.add_argument('--profile',type=Path)
    p=subs.add_parser('bridge')
    p.add_argument('--real-cli',type=Path)
    p.add_argument('args',nargs=argparse.REMAINDER)
    p=subs.add_parser('watch')
    p.add_argument('--once',action='store_true')
    p.add_argument('--json',action='store_true')
    p.add_argument('--thread',help='聊天名称或 ID 筛选')
    p=subs.add_parser('replay')
    p.add_argument('path',type=Path)
    p.add_argument('--output',type=Path)
    args=parser.parse_args(argv)
    runtime=runtime_dir(args.runtime)
    try:
        if args.command=='collect':
            from .collector import serve
            serve(runtime)
        elif args.command=='start':
            start(runtime)
            print('采集进程已运行：'+str(runtime))
        elif args.command=='stop':
            stop(runtime)
            print('采集进程已停止；Codex 通信继续运行')
        elif args.command=='status':
            value=read_snapshot(runtime)
            print(json.dumps({'running':running(runtime),'runtime':str(runtime),'updated_at':value.get('updated_at'),
                              'backends':value.get('backends',{}),'threads':len(value.get('threads',[])),
                              'counters':value.get('counters',{})},ensure_ascii=False,indent=2))
        elif args.command=='bridge':
            from .bridge import forward
            return forward(args.args[1:] if args.args[:1]==['--'] else args.args,runtime,args.real_cli)
        elif args.command=='watch':
            from .display import watch
            watch(runtime,args.once,args.json,args.thread)
        elif args.command=='replay':
            replay(args.path,args.output)
        else:
            from .integration import setup,uninstall,desktop
            if args.command=='setup':
                setup(runtime,args.real_cli,args.launch_agent)
            elif args.command=='uninstall':
                uninstall(runtime)
            else:
                desktop(runtime,args.real_cli,args.home,args.profile)
        return 0
    except (OSError,ValueError,RuntimeError) as exc:
        print(str(exc),file=sys.stderr)
        return 1


if __name__=='__main__':
    sys.exit(main())
