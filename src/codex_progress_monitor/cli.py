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

from .runtime import (
    ROOT, REAL_CLI, atomic_json, collector_alive, read_snapshot,
    runtime_dir, snapshot_fresh, ui_language,
)

from .i18n import DEFAULT_LANGUAGE, available_languages, tr

_children={}


def start(runtime, language=DEFAULT_LANGUAGE):
    if running(runtime) or collector_alive(runtime):
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
            if collector_alive(runtime):
                return  # Another starter acquired the lifetime lock first.
            raise RuntimeError(tr('cli.start_failed', language, path=str(log)))
        time.sleep(0.05)
    if not collector_alive(runtime):
        raise RuntimeError(tr('cli.start_timeout', language, path=str(log)))


def running(runtime):
    snapshot=read_snapshot(runtime)
    return snapshot.get('running') is True and snapshot_fresh(snapshot) and (runtime/'events.sock').exists()


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
    argv = list(sys.argv[1:] if argv is None else argv)
    # Never interpret language options inside forwarded original CLI arguments.
    seed = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    seed.add_argument('--runtime', type=Path)
    seed.add_argument('--lang')
    known, _ = seed.parse_known_args(argv[:argv.index('--')] if '--' in argv else argv)
    initial_runtime = Path(known.runtime or os.environ.get('CODEX_PROGRESS_RUNTIME', ROOT/'.local/runtime')).expanduser()
    try:
        language = ui_language(initial_runtime, known.lang)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    parser=argparse.ArgumentParser(description=tr('cli.description', language))
    parser.add_argument('--lang', help=tr('cli.language', language))
    parser.add_argument('--runtime',type=Path)
    subs=parser.add_subparsers(dest='command',required=True)
    for name in ('collect','start','stop','status','setup','uninstall','desktop'):
        p=subs.add_parser(name)
        if name in ('setup','desktop'):
            p.add_argument('--real-cli',type=Path,default=REAL_CLI)
        if name=='setup':
            p.add_argument('--launch-agent',action='store_true',help=tr('cli.launch_agent', language))
        if name=='desktop':
            p.add_argument('--home',type=Path)
            p.add_argument('--profile',type=Path)
    p=subs.add_parser('bridge')
    p.add_argument('--real-cli',type=Path)
    p.add_argument('args',nargs=argparse.REMAINDER)
    p=subs.add_parser('watch')
    p.add_argument('--once',action='store_true')
    p.add_argument('--json',action='store_true')
    p.add_argument('--thread',help=tr('cli.thread', language))
    p.add_argument('--lang', default=argparse.SUPPRESS, help=tr('cli.language', language))
    p=subs.add_parser('replay')
    p.add_argument('path',type=Path)
    p.add_argument('--output',type=Path)
    p=subs.add_parser('timeline-query', help=tr('cli.timeline_help', language))
    p.add_argument('request', help=argparse.SUPPRESS)
    p=subs.add_parser('app', help=tr('cli.app_help', language))
    p.add_argument('action', choices=('status','configure','resume','stop','restore','cleanup','preferences'))
    p.add_argument('--data-root', type=Path)
    p.add_argument('--app', type=Path)
    p.add_argument('--real-cli', type=Path, default=REAL_CLI)
    p.add_argument('--owner-pid', type=int)
    p.add_argument('--legacy-runtime', type=Path)
    p.add_argument('--legacy-unset', action='store_true')
    p.add_argument('--purge', action='store_true')
    p.add_argument('--launch-at-login', choices=('on','off'))
    p.add_argument('--ui-language', choices=available_languages(), help=argparse.SUPPRESS)
    p.add_argument('--test-profile', type=Path, help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    if args.command == 'timeline-query':
        from .timeline_query import query_runtime
        try:
            # Deliberately bypass runtime_dir(): browsing cannot create or resume observation.
            query_path=Path(args.runtime or os.environ.get('CODEX_PROGRESS_RUNTIME', ROOT/'.local/runtime')).expanduser().resolve()
            value=query_runtime(query_path,json.loads(args.request))
            print(json.dumps(value,ensure_ascii=False))
            return 0
        except (OSError,ValueError,TypeError,KeyError) as exc:
            print(json.dumps({'version':1,'error':'query_unavailable'}))
            return 1
    if args.command == 'app':
        from .app_control import AppController, ControlError, DATA_ROOT
        try:
            control = AppController(args.data_root or DATA_ROOT, runtime=args.runtime, current_app=args.app)
            if args.test_profile:
                from .test_profile import controller
                control, args.real_cli = controller(args.test_profile, args.app)
                args.legacy_runtime = None
            if args.action == 'configure':
                if args.app is None:
                    raise ControlError('app_missing', 'control.app_missing.2')
                value = control.configure(args.app, args.real_cli, legacy_runtime=args.legacy_runtime,
                                          legacy_unset=args.legacy_unset)
            elif args.action == 'resume':
                if args.owner_pid is None:
                    raise ControlError('owner_missing', 'control.owner_missing.2')
                value = control.resume(args.owner_pid)
            elif args.action == 'restore':
                value = control.restore(args.purge)
            elif args.action == 'stop':
                value = control.stop(args.owner_pid)
            elif args.action == 'preferences':
                if args.launch_at_login is None and args.ui_language is None:
                    raise ControlError('preference_missing', 'control.preference_missing')
                value = control.set_preferences(None if args.launch_at_login is None else args.launch_at_login == 'on', args.ui_language)
            else:
                value = getattr(control, args.action)()
            print(json.dumps(value, ensure_ascii=False))
            return 0
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({'version': 1, 'ok': False, 'code': getattr(exc, 'code', 'operation_failed'),
                              'error': str(exc), 'display_message': getattr(exc, 'display_message', None)}, ensure_ascii=False))
            return 1
    try:
        runtime=runtime_dir(args.runtime, language)
        if args.command=='collect':
            from .collector import serve
            serve(runtime)
        elif args.command=='start':
            start(runtime, language)
            print(tr('cli.started', language, path=str(runtime)))
        elif args.command=='stop':
            stop(runtime)
            print(tr('cli.stopped', language))
        elif args.command=='status':
            value=read_snapshot(runtime)
            print(json.dumps({'running':running(runtime),'collector_alive':collector_alive(runtime),
                              'runtime':str(runtime),'updated_at':value.get('updated_at'),
                              'backends':value.get('backends',{}),'threads':len(value.get('threads',[])),
                              'counters':value.get('counters',{})},ensure_ascii=False,indent=2))
        elif args.command=='bridge':
            from .bridge import forward
            return forward(args.args[1:] if args.args[:1]==['--'] else args.args,runtime,args.real_cli)
        elif args.command=='watch':
            from .display import watch
            watch(runtime,args.once,args.json,args.thread,language)
        elif args.command=='replay':
            replay(args.path,args.output)
        else:
            if args.command in ('setup', 'uninstall') and (runtime/'app-managed.json').exists():
                raise RuntimeError(tr('cli.gui_managed', language))
            from .integration import setup,uninstall,desktop
            if args.command=='setup':
                setup(runtime,args.real_cli,args.launch_agent,language)
            elif args.command=='uninstall':
                uninstall(runtime,language)
            else:
                desktop(runtime,args.real_cli,args.home,args.profile,language)
        return 0
    except (OSError,ValueError,RuntimeError) as exc:
        print(str(exc),file=sys.stderr)
        return 1


if __name__=='__main__':
    sys.exit(main())
