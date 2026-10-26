from unittest.mock import patch
import unittest

from codex_activity_monitor.observation_lease import LeaseError, ObservationLease, SessionGrant


def grant(pid=100, id='i'*32, secret='s'*64):
    return SessionGrant(id, pid, secret)


class ObservationLeaseTests(unittest.TestCase):
    def test_legacy_start_is_independent_of_gui_ownership(self):
        lease = ObservationLease(managed=False, now=100)
        self.assertTrue(lease.is_observing(1e6))
        with self.assertRaisesRegex(LeaseError, 'unmanaged_runtime'):
            lease.acquire('reader', 'i'*32, 's'*64, 100)

    def test_grant_bootstrap_expiry_and_explicit_release(self):
        lease = ObservationLease(managed=True, grant=grant(), now=100)
        self.assertTrue(lease.is_observing(104.9))
        self.assertFalse(lease.is_observing(105))
        with self.assertRaisesRegex(LeaseError, 'lease_expired'):
            lease.acquire('late', 'i'*32, 's'*64, 105)
        lease.authorize(grant(), 105)
        lease.acquire('gui', 'i'*32, 's'*64, 106)
        lease.renew('gui', 110)
        self.assertTrue(lease.is_observing(114))
        lease.release('gui')
        self.assertFalse(lease.is_observing(114))

    def test_reconnect_supersedes_connection_and_disconnect_never_extends_deadline(self):
        lease = ObservationLease(managed=True, grant=grant(), now=100)
        lease.acquire('first', 'i'*32, 's'*64, 101)
        lease.disconnect('first')
        self.assertFalse(lease.status(102)['owner_connected'])
        self.assertEqual(lease.remaining(102), 4)
        lease.acquire('second', 'i'*32, 's'*64, 103)
        lease.disconnect('first')
        with self.assertRaisesRegex(LeaseError, 'lease_superseded'):
            lease.release('first')
        with self.assertRaisesRegex(LeaseError, 'lease_superseded'):
            lease.renew('first', 104)
        self.assertTrue(lease.status(104)['owner_connected'])
        self.assertEqual(lease.remaining(104), 4)
        lease.disconnect('second')
        lease.disconnect('second')
        self.assertFalse(lease.is_observing(108))

    def test_other_owner_cannot_replace_live_grant_but_can_after_expiry(self):
        lease = ObservationLease(managed=True, grant=grant(), now=100)
        lease.acquire('first', 'i'*32, 's'*64, 101)
        with self.assertRaisesRegex(LeaseError, 'owner_busy'):
            lease.authorize(grant(pid=200), 105)
        lease.authorize(grant(pid=200), 106)
        self.assertEqual(lease.status(106)['owner_pid'], 200)
        # PID alone never authorizes a subscriber.
        with self.assertRaisesRegex(LeaseError, 'invalid_lease'):
            lease.acquire('intruder', 'i'*32, 'bad'*16, 106)

    def test_renewal_is_memory_only_and_status_never_exposes_secret(self):
        lease = ObservationLease(managed=True, grant=grant(), now=100)
        with patch('pathlib.Path.read_text', side_effect=AssertionError('disk access')):
            lease.acquire('gui', 'i'*32, 's'*64, 101)
            lease.renew('gui', 102)
            self.assertTrue(lease.is_observing(103))
            self.assertNotIn('s'*64, str(lease.status(103)))
            self.assertNotIn('s'*64, repr(grant()))

    def test_record_validation_rejects_legacy_and_malformed_credentials(self):
        valid = {'version': 2, 'id': 'i'*32, 'secret': 's'*64, 'owner_pid': 100, 'active': True}
        self.assertEqual(SessionGrant.from_record(valid), grant())
        for change in ({'version': True}, {'version': 1}, {'active': 1}, {'owner_pid': True},
                       {'owner_pid': 1}, {'secret': '短'*32}, {'id': []}, {'secret': 'x'*129}):
            with self.subTest(change=change), self.assertRaises(LeaseError):
                SessionGrant.from_record({**valid, **change})
