import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import time
import select
import unittest

from codex_activity_monitor.app_session import load_lease
from codex_activity_monitor.observation_lease import LeaseError
from codex_activity_monitor.bridge import Observer
from codex_activity_monitor.state_client import current_state
from tests.test_state_transport import JSONPeer


class AppSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cam-lease-', dir='/tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_explicit_terminal_runtime_needs_no_gui_lease(self):
        self.assertTrue(load_lease(self.root).is_observing())

    def test_startup_rejects_expired_disabled_missing_and_legacy_grants(self):
        session=self.managed_record()
        self.assertTrue(load_lease(self.root).is_observing())
        for change in ({'created_at':time.time()-10}, {'created_at':time.time()+6}, {'created_at':True},
                       {'version':1}, {'active':False}, {'owner_pid':True}):
            with self.subTest(change=change), self.assertRaises(LeaseError):
                session.write_text(json.dumps({**self.record, **change}))
                load_lease(self.root)
        session.write_text(json.dumps(self.record))
        (self.root/'enabled').unlink()
        with self.assertRaises(LeaseError): load_lease(self.root)
        (self.root/'enabled').touch();session.unlink()
        with self.assertRaises(LeaseError): load_lease(self.root)

    def test_paused_observer_does_not_enqueue_or_cache_content(self):
        self.managed_record()
        observer=Observer(self.root,'b','/h',Path('/bin/echo'))
        observer.offer('stderr',b'private content')
        self.assertEqual(observer.queued_bytes,0)
        self.assertTrue(observer.packets.empty())
        self.assertEqual(observer.cache_bytes,0)

    def test_malformed_marker_disables_observation_safely(self):
        (self.root/'app-managed.json').write_text('{')
        with self.assertRaises(LeaseError): load_lease(self.root)

    def managed_record(self):
        self.record = {'version': 2, 'id': 'i'*32, 'secret': 's'*64, 'owner_pid': os.getpid(),
                       'active': True, 'created_at': time.time()}
        enabled = self.root/'enabled'; enabled.touch()
        session = self.root/'session.json'
        session.write_text(json.dumps(self.record))
        (self.root/'app-managed.json').write_text(json.dumps({'version': 1, 'lease_protocol': 2,
            'session_file': str(session), 'enabled_file': str(enabled)}))
        return session

    def collector(self):
        process=subprocess.Popen([sys.executable, '-m', 'codex_activity_monitor.cli', '--runtime', str(self.root), 'collect'],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        def cleanup():
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=4)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=2)
            process.stderr.close()
        self.addCleanup(cleanup)
        deadline=time.monotonic()+4
        while not current_state(self.root, timeout=.2)['connection']['healthy'] and time.monotonic()<deadline:
            if process.poll() is not None:
                self.fail(process.stderr.read())
            time.sleep(.02)
        self.assertIsNone(process.poll())
        peer=JSONPeer(self.root/'state.sock')
        self.addCleanup(peer.close)
        peer.send(role='owner', session_id=self.record['id'], session_secret=self.record['secret'])
        peer.until(lambda value:value['kind']=='hello')
        return process,peer

    def test_connected_owner_is_independent_of_session_file_and_release_stops_collector(self):
        session=self.managed_record()
        process,owner=self.collector()
        session.unlink()
        deadline=time.monotonic()+5.2
        while time.monotonic()<deadline:
            owner.send('heartbeat')
            owner.until(lambda value:value['kind']=='heartbeat')
            self.assertIsNone(process.poll())
            time.sleep(.2)
        owner.send('release')
        owner.until(lambda value:value['kind']=='released')
        self.assertEqual(process.wait(timeout=4),0)
        self.assertFalse((self.root/'collector.json').exists())
        self.assertFalse(json.loads((self.root/'snapshot.json').read_text())['running'])

    def test_bridge_preserves_protocol_across_pause_and_reconnect(self):
        self.managed_record()
        cli=self.root/'echo-cli'
        cli.write_text('#!'+sys.executable+'\nimport sys\nfor line in sys.stdin:\n sys.stdout.write(line);sys.stdout.flush()\n')
        cli.chmod(0o700)
        bridge=subprocess.Popen([sys.executable,'-m','codex_activity_monitor.cli','--runtime',str(self.root),
                                 'bridge','--real-cli',str(cli),'--','app-server'],
                                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        def cleanup():
            if bridge.poll() is None: bridge.terminate()
            bridge.communicate(timeout=4)
        self.addCleanup(cleanup)
        def echo(number):
            data=json.dumps({'id':number,'method':'ping'})+'\n'
            bridge.stdin.write(data);bridge.stdin.flush()
            self.assertTrue(select.select([bridge.stdout],[],[],2)[0], 'protocol forwarding stalled')
            self.assertEqual(bridge.stdout.readline(),data)
        def wait_bridge(owner):
            deadline=time.monotonic()+4
            while time.monotonic()<deadline:
                owner.send('heartbeat');owner.until(lambda value:value['kind']=='heartbeat')
                if any(b.get('connected') for b in current_state(self.root)['backends'].values()): return
                time.sleep(.05)
            self.fail('bridge did not reconnect after observation resumed')
        echo(1)
        collector,owner=self.collector()
        wait_bridge(owner)
        echo(2)
        owner.send('release');owner.until(lambda value:value['kind']=='released')
        self.assertEqual(collector.wait(timeout=4),0)
        echo(3)
        self.managed_record()
        replacement,new_owner=self.collector()
        wait_bridge(new_owner)
        echo(4)
        bridge.stdin.close();bridge.stdin=None
        _,error=bridge.communicate(timeout=4)
        self.assertEqual(bridge.returncode,0,error)
        self.assertFalse(list((self.root/'bridges').glob('*.json')))
        new_owner.send('release');new_owner.until(lambda value:value['kind']=='released')
        self.assertEqual(replacement.wait(timeout=4),0)


if __name__=='__main__':
    unittest.main()
