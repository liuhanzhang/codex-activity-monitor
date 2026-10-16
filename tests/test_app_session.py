import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import time
import select
import unittest

from codex_progress_monitor.app_session import observing
from codex_progress_monitor.bridge import Observer


class AppSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cpm-lease-', dir='/tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def setup_lease(self, age=0):
        enabled = self.root/'enabled'; enabled.touch()
        session = self.root/'session.json'
        session.write_text(json.dumps({'version':1,'owner_pid':os.getpid(),'active':True,'heartbeat_at':100-age}))
        (self.root/'app-managed.json').write_text(json.dumps({'version':1,'session_file':str(session),'enabled_file':str(enabled)}))
        return session, enabled

    def test_legacy_runtime_is_unchanged(self):
        self.assertTrue(observing(self.root))

    def test_expired_disabled_missing_and_future_sessions_are_inactive(self):
        session, enabled = self.setup_lease()
        self.assertTrue(observing(self.root, 101))
        self.assertFalse(observing(self.root, 106))
        self.assertFalse(observing(self.root, 90))
        enabled.unlink()
        self.assertFalse(observing(self.root, 101))
        enabled.touch(); session.unlink()
        self.assertFalse(observing(self.root, 101))

    def test_paused_observer_does_not_enqueue_or_cache_content(self):
        self.setup_lease(age=100)
        observer=Observer(self.root,'b','/h',Path('/bin/echo'))
        observer.offer('stderr',b'private content')
        self.assertEqual(observer.queued_bytes,0)
        self.assertTrue(observer.packets.empty())
        self.assertEqual(observer.cache_bytes,0)

    def test_malformed_marker_disables_observation_safely(self):
        (self.root/'app-managed.json').write_text('{')
        self.assertFalse(observing(self.root))

    def test_managed_collector_exits_when_gui_lease_disappears(self):
        session, enabled=self.setup_lease()
        session.write_text(json.dumps({'version':1,'owner_pid':os.getpid(),'active':True,'heartbeat_at':time.time()}))
        process=subprocess.Popen([sys.executable,'-m','codex_progress_monitor.cli','--runtime',str(self.root),'collect'],
                                 stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        deadline=time.monotonic()+5
        while not (self.root/'collector.json').exists() and process.poll() is None and time.monotonic()<deadline:
            time.sleep(.05)
        self.assertTrue((self.root/'collector.json').exists(), process.stderr.read() if process.poll() is not None else 'startup timeout')
        session.unlink()
        output,error=process.communicate(timeout=5)
        self.assertEqual(process.returncode,0,error)
        self.assertFalse((self.root/'collector.json').exists())
        self.assertFalse(json.loads((self.root/'snapshot.json').read_text())['running'])

    def test_bridge_preserves_protocol_across_pause_and_reconnect(self):
        session, enabled=self.setup_lease()
        def heartbeat():
            session.write_text(json.dumps({'version':1,'owner_pid':os.getpid(),'active':True,'heartbeat_at':time.time()}))
        heartbeat()
        cli=self.root/'echo-cli'
        cli.write_text('#!'+sys.executable+'\nimport sys\nfor line in sys.stdin:\n sys.stdout.write(line);sys.stdout.flush()\n')
        cli.chmod(0o700)
        bridge=subprocess.Popen([sys.executable,'-m','codex_progress_monitor.cli','--runtime',str(self.root),
                                 'bridge','--real-cli',str(cli),'--','app-server'],
                                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.addCleanup(lambda: bridge.poll() is None and bridge.kill())
        def echo(number):
            data=json.dumps({'id':number,'method':'ping'})+'\n'
            bridge.stdin.write(data);bridge.stdin.flush()
            self.assertTrue(select.select([bridge.stdout],[],[],5)[0], 'protocol forwarding stalled')
            self.assertEqual(bridge.stdout.readline(),data)
        echo(1)
        session.unlink()
        time.sleep(.65)
        echo(2)
        self.assertIsNone(bridge.poll(), 'pausing observation must not terminate the original CLI')
        heartbeat()
        collector=subprocess.Popen([sys.executable,'-m','codex_progress_monitor.cli','--runtime',str(self.root),'collect'],
                                   stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.addCleanup(lambda: collector.poll() is None and collector.kill())
        connected=False
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            heartbeat()
            try:
                snapshot=json.loads((self.root/'snapshot.json').read_text())
                connected=any(b.get('connected') for b in snapshot['backends'].values())
            except (FileNotFoundError,ValueError):
                pass
            if connected:break
            time.sleep(.1)
        self.assertTrue(connected,'bridge did not reconnect after observation resumed')
        echo(3)
        bridge.stdin.close();bridge.stdin=None
        _,error=bridge.communicate(timeout=5)
        self.assertEqual(bridge.returncode,0,error)
        self.assertFalse(list((self.root/'bridges').glob('*.json')))
        session.unlink()
        _,error=collector.communicate(timeout=5)
        self.assertEqual(collector.returncode,0,error)


if __name__=='__main__':
    unittest.main()
