import fcntl
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codex_progress_monitor.app_control import AppController, ControlError, bridge_upgrade_required
from codex_progress_monitor.cli import main
from codex_progress_monitor.observation_lease import ObservationLease, SessionGrant
from codex_progress_monitor.publication import DetachedSnapshot
from codex_progress_monitor.runtime import atomic_json
from codex_progress_monitor.state_client import authorize_session, StateClientError
from codex_progress_monitor.state_transport import StateServer
from tests.test_app_control import MemoryEnvironment, FakeServices
from tests.test_state_transport import JSONPeer


class ManagedControlTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='cpm-mgmt-', dir='/tmp')
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        app = root/'Fixture.app'
        entry = app/'Contents/MacOS/MonitorApp'
        entry.parent.mkdir(parents=True)
        entry.write_text('#!/bin/sh\nexit 0\n'); entry.chmod(0o700)
        self.control = AppController(root/'data', environment=MemoryEnvironment(), services=FakeServices())
        self.control.configure(app, '/bin/echo')
        self.runtime = Path(self.control.status()['runtime'])
        self.start = self.enterContext(patch('codex_progress_monitor.cli.start'))
        self.stop = self.enterContext(patch('codex_progress_monitor.cli.stop'))
        self.session = self.control.resume(os.getpid())['session']
        self.start.reset_mock()
        handle = self.enterContext((self.runtime/'collector.lock').open('w'))
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.lease = ObservationLease(managed=True, grant=SessionGrant.from_record(self.session))
        self.server = StateServer(self.runtime/'state.sock', lease=self.lease, control_token='management-token')
        self.addCleanup(self.server.close)
        self.server.publish(DetachedSnapshot.capture({'schema_version': 1, 'running': True, 'threads': []}))
        self.metadata = {'state_protocol': 1, 'lease_protocol': 2, 'token': 'management-token',
                         'instance_id': self.server.instance}
        atomic_json(self.runtime/'collector.json', self.metadata)

    def owner(self, session=None):
        session = session or self.session
        peer = JSONPeer(self.runtime/'state.sock')
        self.addCleanup(peer.close)
        peer.send(role='owner', session_id=session['id'], session_secret=session['secret'])
        peer.until(lambda frame:frame['kind'] == 'hello')
        return peer

    def test_retried_resume_keeps_live_owner_and_credential(self):
        owner = self.owner()
        deadline = self.lease.expires
        result = self.control.resume(os.getpid())['session']
        self.assertEqual((result['id'], result['secret']), (self.session['id'], self.session['secret']))
        self.assertEqual(self.lease.expires, deadline)
        owner.send('heartbeat'); owner.until(lambda frame:frame['kind'] == 'heartbeat')
        self.start.assert_not_called(); self.stop.assert_not_called()

    def test_definite_rejection_restores_previous_record(self):
        self.owner()
        with patch('codex_progress_monitor.app_control.authorize_session', side_effect=StateClientError('owner_busy')):
            with self.assertRaisesRegex(ControlError, '实时状态通道'):
                self.control.resume(os.getpid())
        self.assertEqual(json.loads(self.control.session.read_text()), self.session)
        self.assertTrue(self.lease.status()['owner_connected'])

    def test_applied_but_timed_out_authorization_is_retried_without_wrong_rollback(self):
        old_owner = self.owner()
        atomic_json(self.control.session, {'version': 1, 'active': False})
        def applied(*args):
            authorize_session(*args)
            raise TimeoutError('lost acknowledgement')
        with patch('codex_progress_monitor.app_control.authorize_session', side_effect=applied):
            with self.assertRaises(ControlError):
                self.control.resume(os.getpid())
        pending = json.loads(self.control.session.read_text())
        self.assertNotEqual(pending['id'], self.session['id'])
        self.assertEqual(self.lease.status()['session_id'], pending['id'])
        retried = self.control.resume(os.getpid())['session']
        self.assertEqual((retried['id'], retried['secret']), (pending['id'], pending['secret']))
        replacement = self.owner(retried)
        old_owner.send('heartbeat')
        self.assertEqual(old_owner.until(lambda frame:frame['kind'] == 'error')['code'], 'lease_superseded')
        replacement.send('heartbeat'); replacement.until(lambda frame:frame['kind'] == 'heartbeat')
        self.start.assert_not_called(); self.stop.assert_not_called()

    def test_unavailable_new_transport_never_triggers_replacement(self):
        for scenario in ('timeout', 'missing-control'):
            with self.subTest(scenario=scenario):
                if scenario == 'missing-control':
                    (self.runtime/'collector.json').unlink()
                with patch('codex_progress_monitor.app_control.state_once', side_effect=TimeoutError()):
                    with self.assertRaises(ControlError):
                        self.control.resume(os.getpid())
                self.assertEqual(json.loads(self.control.session.read_text()), self.session)
                self.start.assert_not_called(); self.stop.assert_not_called()

    def test_another_gui_cannot_steal_in_memory_owner(self):
        self.owner()
        with patch('codex_progress_monitor.app_control.process_alive', return_value=True):
            with self.assertRaisesRegex(ControlError, '另一个 Monitor'):
                self.control.resume(os.getpid()+1)
        self.assertEqual(json.loads(self.control.session.read_text()), self.session)
        self.start.assert_not_called(); self.stop.assert_not_called()

    def test_old_gui_is_rejected_before_configuration_mutation(self):
        before = self.control.receipt_path.read_bytes()
        output = io.StringIO()
        with patch('sys.stdout', output):
            self.assertEqual(main(['app', 'configure', '--data-root', str(self.control.root)]), 1)
        self.assertEqual(json.loads(output.getvalue())['code'], 'gui_upgrade_required')
        self.assertEqual(self.control.receipt_path.read_bytes(), before)

    def test_resident_bridge_upgrade_diagnostic_verifies_identity_and_protocol(self):
        bridges = self.runtime/'bridges'; bridges.mkdir()
        path = bridges/'fixture.json'
        record = {'pid': os.getpid(), 'identity': 'registered-start'}
        atomic_json(path, record)
        with patch('codex_progress_monitor.app_session.process_identity', return_value='registered-start'):
            self.assertTrue(self.control.status()['bridge_restart_required'])
        with patch('codex_progress_monitor.app_session.process_identity', return_value='reused-pid-start'):
            self.assertFalse(bridge_upgrade_required(self.runtime))
        atomic_json(path, {**record, 'observation_protocol': 1})
        self.assertFalse(bridge_upgrade_required(self.runtime))
        atomic_json(path, record)
        with patch('codex_progress_monitor.app_control.process_alive', return_value=False):
            self.assertFalse(bridge_upgrade_required(self.runtime))
