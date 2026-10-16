import json
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from codex_progress_monitor import integration


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='cpm-integration-',dir='/tmp')
        self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name)/'home'
        self.home.mkdir()
        self.runtime=Path(self.temp.name)/'runtime'
        self.runtime.mkdir()
        self.env={}
        self.calls=[]
        self.enterContext(patch('codex_progress_monitor.cli.start'))
        self.enterContext(patch('codex_progress_monitor.cli.stop'))
        self.enterContext(patch('codex_progress_monitor.integration.Path.home',return_value=self.home))
        self.enterContext(patch('codex_progress_monitor.integration._run',side_effect=self.run_command))

    def run_command(self,args,check=True):
        self.calls.append(args)
        code,output=0,''
        if args[:2]==['/bin/launchctl','getenv']:
            code=0
            output=self.env[args[2]]+'\n' if args[2] in self.env else ''
        elif args[:2]==['/bin/launchctl','setenv']:
            self.env[args[2]]=args[3]
        elif args[:2]==['/bin/launchctl','unsetenv']:
            self.env.pop(args[2],None)
        return subprocess.CompletedProcess(args,code,stdout=output,stderr='')

    def install(self):
        with patch('builtins.print'):
            integration.setup(self.runtime,Path('/bin/echo'),launch_agent=True)
        return json.loads((self.runtime/'desktop-environment.json').read_text())

    def test_login_install_keeps_local_wrapper_without_global_force(self):
        self.env={'CODEX_APP_SERVER_FORCE_CLI':'user-original'}
        state=self.install()
        commands=[shlex.split(line) for line in (self.runtime/'login-start').read_text().splitlines()
                  if line.startswith('/bin/launchctl ')]
        installed={command[2]:command[3] for command in commands}
        self.assertEqual(installed['CODEX_CLI_PATH'],str(self.runtime/'codex-observed'))
        self.assertEqual(installed['CODEX_APP_SERVER_FORCE_CLI'],'0')
        self.assertEqual(installed,state['installed'])
        self.assertEqual(state['previous']['CODEX_APP_SERVER_FORCE_CLI'],'user-original')

    def test_upgrade_updates_owned_values_and_uninstall_restores_originals(self):
        previous={'CODEX_CLI_PATH':'/original/codex','CODEX_APP_SERVER_FORCE_CLI':None,
                  'CODEX_APP_SERVER_USE_LOCAL_DAEMON':'1','CODEX_APP_SERVER_WS_URL':None}
        old={'CODEX_CLI_PATH':str(self.runtime/'codex-observed'),
             'CODEX_APP_SERVER_FORCE_CLI':'1','CODEX_APP_SERVER_USE_LOCAL_DAEMON':'0',
             'CODEX_APP_SERVER_WS_URL':''}
        (self.runtime/'desktop-environment.json').write_text(json.dumps({'previous':previous,'installed':old}))
        self.env=dict(old)
        state=self.install()
        self.assertEqual(state['previous'],previous)
        self.assertEqual(state['installed']['CODEX_APP_SERVER_FORCE_CLI'],'0')
        # Model the login script applying the new install before uninstall.
        self.env.update(state['installed'])
        with patch('builtins.print'):
            integration.uninstall(self.runtime)
        self.assertEqual(self.env,{key:value for key,value in previous.items() if value is not None})
        self.assertFalse((self.runtime/'desktop-environment.json').exists())

    def test_uninstall_preserves_environment_changed_by_user(self):
        state=self.install()
        self.env.update(state['installed'])
        self.env['CODEX_CLI_PATH']='/user/replacement'
        with patch('builtins.print'):
            integration.uninstall(self.runtime)
        self.assertEqual(self.env,{'CODEX_CLI_PATH':'/user/replacement'})

    def test_empty_unset_and_trailing_newline_are_preserved(self):
        self.env={'CODEX_CLI_PATH':'/original\n','CODEX_APP_SERVER_FORCE_CLI':''}
        original=self.env.copy()
        state=self.install()
        self.assertEqual(state['previous']['CODEX_CLI_PATH'],'/original\n')
        self.assertEqual(state['previous']['CODEX_APP_SERVER_FORCE_CLI'],'')
        self.assertIsNone(state['previous']['CODEX_APP_SERVER_WS_URL'])
        self.env.update(state['installed'])
        with patch('builtins.print'):
            integration.uninstall(self.runtime)
        self.assertEqual(self.env,original)

    def test_user_unset_is_different_from_installed_empty(self):
        self.env={'CODEX_APP_SERVER_WS_URL':'original'}
        state=self.install()
        self.env.update(state['installed'])
        self.env.pop('CODEX_APP_SERVER_WS_URL')
        with patch('builtins.print'):
            integration.uninstall(self.runtime)
        self.assertNotIn('CODEX_APP_SERVER_WS_URL',self.env)

    def test_explicit_desktop_launch_preserves_cloud_routing(self):
        with patch('builtins.print'):
            integration.desktop(self.runtime,Path('/bin/echo'))
        command=next(call for call in self.calls if call[0]=='/usr/bin/open')
        env=dict(command[i+1].split('=',1) for i,arg in enumerate(command) if arg=='--env')
        self.assertEqual(env['CODEX_APP_SERVER_FORCE_CLI'],'0')
        self.assertEqual(env['CODEX_CLI_PATH'],str(self.runtime/'codex-observed'))
        self.assertEqual(command[-1],'/Applications/ChatGPT.app')


if __name__=='__main__':
    unittest.main()
