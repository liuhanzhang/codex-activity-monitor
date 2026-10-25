from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from codex_progress_monitor.observation_lease import ObservationLease
from codex_progress_monitor.publication import DetachedSnapshot
from codex_progress_monitor.state_client import authorize_session, state_once, StateClientError
from codex_progress_monitor.state_transport import StateServer


class StateClientTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='cpm-client-', dir='/tmp')
        self.addCleanup(directory.cleanup)
        self.runtime = Path(directory.name)
        self.lease = ObservationLease(managed=True)
        self.server = StateServer(self.runtime/'state.sock', lease=self.lease, control_token='management-secret')
        self.addCleanup(self.server.close)
        self.server.publish(DetachedSnapshot.capture({'schema_version': 1, 'updated_at': 0,
                                                     'running': True, 'threads': []}))

    def test_state_probe_uses_nonce_health_without_touching_archive(self):
        with patch('pathlib.Path.read_text', side_effect=AssertionError('disk access')):
            live = state_once(self.runtime)
        self.assertTrue(live.is_fresh())
        self.assertEqual(live.payload['updated_at'], 0)
        self.assertFalse(live.is_fresh(live.acknowledged_at+2))
        self.assertFalse(live.is_fresh(live.acknowledged_at-1))
        self.server.progress(time.monotonic()-3)
        self.assertFalse(state_once(self.runtime).is_fresh())

    def test_probe_cannot_extend_managed_observation(self):
        self.assertFalse(self.lease.is_observing())
        state_once(self.runtime)
        state_once(self.runtime)
        self.assertFalse(self.lease.is_observing())

    def test_management_authorization_is_a_separate_bounded_operation(self):
        record = {'version': 2, 'id': 'i'*32, 'secret': 's'*64, 'owner_pid': 100, 'active': True}
        with self.assertRaisesRegex(StateClientError, 'invalid_lease'):
            authorize_session(self.runtime, record, {'token': 'bad'})
        authorize_session(self.runtime, record, {'token': 'management-secret', 'instance_id': self.server.instance})
        self.assertTrue(self.lease.is_observing())
        self.assertEqual(self.lease.status()['owner_pid'], 100)

    def test_probe_unavailable_state_and_deadline_never_fall_back_to_files(self):
        self.server.max_frame_bytes = 128
        self.server.publish(DetachedSnapshot.capture({'schema_version': 1, 'threads': [], 'padding': 'x'*1024}))
        with self.assertRaisesRegex(StateClientError, 'state_unavailable'):
            state_once(self.runtime)
        # A fresh server without any state cannot satisfy a read-only probe.
        self.server.close()
        empty = StateServer(self.runtime/'state.sock')
        self.addCleanup(empty.close)
        before = time.monotonic()
        with self.assertRaises(TimeoutError):
            state_once(self.runtime, timeout=.1)
        self.assertLess(time.monotonic()-before, .5)
