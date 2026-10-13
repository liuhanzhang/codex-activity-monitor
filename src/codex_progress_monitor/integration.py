"""Reversible macOS startup integration, with original values saved once."""

import json
import os
from pathlib import Path
import plistlib
import shlex
import subprocess
import sys

from .runtime import atomic_json
from .i18n import DEFAULT_LANGUAGE, tr

LABEL='com.local.codex-progress-monitor'
KEYS=('CODEX_CLI_PATH','CODEX_APP_SERVER_FORCE_CLI','CODEX_APP_SERVER_USE_LOCAL_DAEMON','CODEX_APP_SERVER_WS_URL')


def _run(args,check=True):
    return subprocess.run(args,capture_output=True,text=True,check=check)


def _values(wrapper):
    return dict(zip(KEYS,(str(wrapper),'1','0','')))


def setup(runtime,actual,launch_agent=False,language=DEFAULT_LANGUAGE):
    actual=actual.expanduser().resolve()
    if not actual.is_file() or not os.access(actual,os.X_OK):
        raise ValueError(tr('cli.original_invalid', language, path=str(actual)))
    wrapper=runtime/'codex-observed'
    if actual==wrapper:
        raise ValueError(tr('cli.original_wrapper', language))
    wrapper.write_text('#!/bin/sh\nexec '+shlex.quote(sys.executable)+' -m codex_progress_monitor.cli --runtime '+
                       shlex.quote(str(runtime))+' bridge --real-cli '+shlex.quote(str(actual))+' -- "$@"\n')
    wrapper.chmod(0o700)
    atomic_json(runtime/'installation.json',{'real_cli':str(actual),'python':sys.executable,'wrapper':str(wrapper)})
    if launch_agent:
        plist=Path.home()/'Library/LaunchAgents'/f'{LABEL}.plist'
        saved=runtime/'desktop-environment.json'
        if plist.exists():
            existing=plistlib.loads(plist.read_bytes())
            if str(runtime) not in json.dumps(existing):
                raise RuntimeError(tr('cli.agent_owned', language))
        if not saved.exists():
            previous={}
            for key in KEYS:
                result=_run(['/bin/launchctl','getenv',key],False)
                previous[key]=result.stdout.rstrip('\n') if result.returncode==0 else None
            atomic_json(saved,{'previous':previous,'installed':_values(wrapper)})
        script=runtime/'login-start'
        lines=['#!/bin/sh']
        lines.extend('/bin/launchctl setenv '+key+' '+shlex.quote(value) for key,value in _values(wrapper).items())
        lines.append('exec '+shlex.quote(sys.executable)+' -m codex_progress_monitor.cli --runtime '+shlex.quote(str(runtime))+' collect')
        script.write_text('\n'.join(lines)+'\n')
        script.chmod(0o700)
        plist.parent.mkdir(parents=True,exist_ok=True)
        data={'Label':LABEL,'ProgramArguments':[str(script)],'RunAtLoad':True,'KeepAlive':True,
              'ThrottleInterval':10,'StandardOutPath':'/dev/null','StandardErrorPath':'/dev/null',
              'ProcessType':'Background','WorkingDirectory':str(runtime)}
        plist.write_bytes(plistlib.dumps(data))
        plist.chmod(0o600)
        from .cli import stop
        stop(runtime)
        _run(['/bin/launchctl','bootout',f'gui/{os.getuid()}/{LABEL}'],False)
        _run(['/bin/launchctl','bootstrap',f'gui/{os.getuid()}',str(plist)])
        print(tr('cli.installed', language))
    else:
        from .cli import start
        start(runtime,language)
        print(tr('cli.prepared', language, path=str(wrapper)))


def uninstall(runtime,language=DEFAULT_LANGUAGE):
    plist=Path.home()/'Library/LaunchAgents'/f'{LABEL}.plist'
    saved=runtime/'desktop-environment.json'
    if saved.exists():
        state=json.loads(saved.read_text())
        if plist.exists() and str(runtime) in json.dumps(plistlib.loads(plist.read_bytes())):
            _run(['/bin/launchctl','bootout',f'gui/{os.getuid()}/{LABEL}'],False)
            plist.unlink()
        for key,value in state['installed'].items():
            current=_run(['/bin/launchctl','getenv',key],False).stdout.rstrip('\n')
            if current!=value:
                continue
            previous=state['previous'].get(key)
            _run(['/bin/launchctl','setenv',key,previous] if previous is not None else ['/bin/launchctl','unsetenv',key])
        saved.unlink()
    from .cli import stop
    stop(runtime)
    print(tr('cli.uninstalled', language))


def desktop(runtime,actual,home=None,profile=None,language=DEFAULT_LANGUAGE):
    from .cli import start
    setup(runtime,actual,language=language)
    start(runtime,language)
    values=_values(runtime/'codex-observed')
    command=['/usr/bin/open']
    if profile:
        command.extend(['-n','-g'])
        profile=profile.expanduser().resolve()
        values['CODEX_ELECTRON_USER_DATA_PATH']=str(profile)
    if home:
        home=home.expanduser().resolve()
        values.update(CODEX_HOME=str(home),CODEX_SQLITE_HOME=str(home/'sqlite'))
    for key,value in values.items():
        command.extend(['--env',key+'='+value])
    command.append('/Applications/ChatGPT.app')
    if profile:
        command.extend(['--args','--user-data-dir='+str(profile)])
    _run(command)
    if profile:
        print(tr('cli.desktop_isolated', language))
    else:
        print(tr('cli.desktop', language))
