"""Regression tests for the LDAP login integration (TC-FUNC-01, TC-SEC-02 prerequisite).

Why this file exists: ldap3 packs ``receive_timeout`` with ``struct``; passing a float (the project
setting LDAP_CONNECT_TIMEOUT is a float) raised ``struct.error`` inside every bind, so *valid* LDAP
credentials were rejected and accounts were locked after five attempts. Unit tests of the pure
core could not detect this because LDAP is only used when LDAP_ENABLED=1.

Run (no LDAP server needed):   python -m unittest tests.test_integrations -v
Run against a live directory:  LDAP_LIVE_TEST=1 LDAP_URL=ldap://openldap:389 python -m unittest tests.test_integrations -v
(inside the stand: docker compose exec -e LDAP_LIVE_TEST=1 backend python -m unittest tests.test_integrations -v)
"""
import os
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402

django.setup()

from django.test import SimpleTestCase, override_settings  # noqa: E402
from ppm_app import integrations  # noqa: E402


class _FakeConnection:
    """Context manager imitating ldap3.Connection(auto_bind=True) and recording its arguments."""
    calls = []

    def __init__(self, server, **kwargs):
        type(self).calls.append(kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@override_settings(LDAP_CONNECT_TIMEOUT=3.0, LDAP_URL="ldap://ldap.test:389")
class LdapBindArguments(SimpleTestCase):
    def setUp(self):
        _FakeConnection.calls = []

    def test_timeouts_passed_to_ldap3_are_integers(self):
        server_calls = []
        with mock.patch.object(integrations, "Connection", _FakeConnection), \
                mock.patch.object(integrations, "Server", side_effect=lambda *a, **k: server_calls.append(k) or object()):
            self.assertTrue(integrations.ldap_bind_valid("manager", "manager123"))
        self.assertIsInstance(_FakeConnection.calls[0]["receive_timeout"], int)
        self.assertIsInstance(server_calls[0]["connect_timeout"], int)

    def test_bind_error_means_invalid_credentials(self):
        with mock.patch.object(integrations, "Server"), \
                mock.patch.object(integrations, "Connection", side_effect=RuntimeError("bind failed")):
            self.assertFalse(integrations.ldap_bind_valid("manager", "wrong"))

    def test_rejects_malformed_username_without_network_call(self):
        with mock.patch.object(integrations, "Connection") as conn:
            for bad in ("", "a,b=c", "*", "x" * 65, "uid=admin"):
                self.assertFalse(integrations.ldap_bind_valid(bad, "pw"), bad)
            self.assertFalse(integrations.ldap_bind_valid("manager", ""))
            conn.assert_not_called()


@unittest.skipUnless(os.getenv("LDAP_LIVE_TEST") == "1", "set LDAP_LIVE_TEST=1 to run against a real directory")
class LiveDirectory(SimpleTestCase):
    """Binds against the real OpenLDAP loaded from infra/ldap/bootstrap.ldif."""
    CREDENTIALS = {
        "initiator": "initiator123", "expert1": "expert12345", "expert2": "expert23456", "manager": "manager123",
        "committee1": "committee123", "committee2": "committee234", "committee3": "committee345", "admin": "admin12345",
    }

    def test_all_seeded_users_can_bind(self):
        for user, password in self.CREDENTIALS.items():
            with self.subTest(user=user):
                self.assertTrue(integrations.ldap_bind_valid(user, password))

    def test_wrong_password_and_unknown_user_are_rejected(self):
        self.assertFalse(integrations.ldap_bind_valid("manager", "not-the-password"))
        self.assertFalse(integrations.ldap_bind_valid("nobody", "manager123"))

    def test_unreachable_server_is_reported_as_failure_quickly(self):
        import time
        with override_settings(LDAP_URL="ldap://127.0.0.1:1"):
            start = time.perf_counter()
            self.assertFalse(integrations.ldap_bind_valid("manager", "manager123"))
            self.assertLess(time.perf_counter() - start, 10)


if __name__ == "__main__":
    unittest.main()
