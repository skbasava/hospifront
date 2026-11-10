import os
import unittest
from unittest.mock import patch

from remote_client import ClientConfig, FrappeClient, FrappeClientError


class ClientConfigTests(unittest.TestCase):
    def test_default_remote_uses_legacy_env(self):
        env = {
            "FRAPPE_BASE_URL": "https://remote.example/",
            "FRAPPE_API_KEY": "remote-key",
            "FRAPPE_API_SECRET": "remote-secret",
            "FRAPPE_TIMEOUT": "9",
        }
        with patch.dict(os.environ, env, clear=True):
            config = ClientConfig.from_env()

        self.assertEqual(config.target, "remote")
        self.assertEqual(config.base_url, "https://remote.example")
        self.assertEqual(config.api_key, "remote-key")
        self.assertEqual(config.api_secret, "remote-secret")
        self.assertEqual(config.timeout, 9.0)
        self.assertIsNone(config.site)

    def test_remote_prefixed_env(self):
        env = {
            "FRAPPE_TARGET": "remote",
            "FRAPPE_REMOTE_BASE_URL": "https://remote.example/",
            "FRAPPE_REMOTE_API_KEY": "remote-key",
            "FRAPPE_REMOTE_API_SECRET": "remote-secret",
            "FRAPPE_REMOTE_SITE": "remote.example",
            "FRAPPE_REMOTE_TIMEOUT": "7",
        }
        with patch.dict(os.environ, env, clear=True):
            config = ClientConfig.from_env()

        self.assertEqual(config.target, "remote")
        self.assertEqual(config.base_url, "https://remote.example")
        self.assertEqual(config.site, "remote.example")
        self.assertEqual(config.timeout, 7.0)

    def test_local_switch_uses_local_env(self):
        env = {
            "FRAPPE_TARGET": "local",
            "FRAPPE_LOCAL_BASE_URL": "http://localhost:8000/",
            "FRAPPE_LOCAL_API_KEY": "local-key",
            "FRAPPE_LOCAL_API_SECRET": "local-secret",
            "FRAPPE_LOCAL_SITE": "local.site",
            "FRAPPE_LOCAL_TIMEOUT": "5",
        }
        with patch.dict(os.environ, env, clear=True):
            config = ClientConfig.from_env()

        self.assertEqual(config.target, "local")
        self.assertEqual(config.base_url, "http://localhost:8000")
        self.assertEqual(config.api_key, "local-key")
        self.assertEqual(config.api_secret, "local-secret")
        self.assertEqual(config.site, "local.site")
        self.assertEqual(config.timeout, 5.0)

    def test_local_defaults_base_url_and_timeout(self):
        env = {
            "FRAPPE_TARGET": "local",
            "FRAPPE_LOCAL_API_KEY": "local-key",
            "FRAPPE_LOCAL_API_SECRET": "local-secret",
        }
        with patch.dict(os.environ, env, clear=True):
            config = ClientConfig.from_env()

        self.assertEqual(config.base_url, "http://127.0.0.1:8000")
        self.assertEqual(config.timeout, 15.0)

    def test_local_does_not_use_remote_credentials(self):
        env = {
            "FRAPPE_TARGET": "local",
            "FRAPPE_API_KEY": "remote-key",
            "FRAPPE_API_SECRET": "remote-secret",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(FrappeClientError) as raised:
                ClientConfig.from_env()

        self.assertIn("FRAPPE_LOCAL_API_KEY", str(raised.exception))
        self.assertIn("FRAPPE_LOCAL_API_SECRET", str(raised.exception))

    def test_explicit_target_overrides_environment(self):
        env = {
            "FRAPPE_TARGET": "local",
            "FRAPPE_LOCAL_API_KEY": "local-key",
            "FRAPPE_LOCAL_API_SECRET": "local-secret",
            "FRAPPE_REMOTE_BASE_URL": "https://remote.example",
            "FRAPPE_REMOTE_API_KEY": "remote-key",
            "FRAPPE_REMOTE_API_SECRET": "remote-secret",
        }
        with patch.dict(os.environ, env, clear=True):
            config = ClientConfig.from_env("remote")

        self.assertEqual(config.target, "remote")
        self.assertEqual(config.base_url, "https://remote.example")
        self.assertEqual(config.api_key, "remote-key")

    def test_invalid_target_rejected(self):
        with patch.dict(os.environ, {"FRAPPE_TARGET": "staging"}, clear=True):
            with self.assertRaises(FrappeClientError) as raised:
                ClientConfig.from_env()

        self.assertIn("Invalid FRAPPE_TARGET", str(raised.exception))

    def test_invalid_timeout_rejected(self):
        env = {
            "FRAPPE_TARGET": "local",
            "FRAPPE_LOCAL_API_KEY": "local-key",
            "FRAPPE_LOCAL_API_SECRET": "local-secret",
            "FRAPPE_LOCAL_TIMEOUT": "not-a-number",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(FrappeClientError):
                ClientConfig.from_env()

    def test_redacted_hides_secrets(self):
        env = {
            "FRAPPE_TARGET": "local",
            "FRAPPE_LOCAL_API_KEY": "top-secret-key",
            "FRAPPE_LOCAL_API_SECRET": "top-secret-value",
        }
        with patch.dict(os.environ, env, clear=True):
            config = ClientConfig.from_env()

        redacted = config.redacted()
        self.assertEqual(redacted["target"], "local")
        self.assertEqual(redacted["api_key"], "***redacted***")
        self.assertEqual(redacted["api_secret"], "***redacted***")
        self.assertNotIn("top-secret-key", str(redacted))
        self.assertNotIn("top-secret-value", str(redacted))

    def test_client_accepts_explicit_target(self):
        env = {
            "FRAPPE_TARGET": "remote",
            "FRAPPE_REMOTE_BASE_URL": "https://remote.example",
            "FRAPPE_REMOTE_API_KEY": "remote-key",
            "FRAPPE_REMOTE_API_SECRET": "remote-secret",
        }
        with patch.dict(os.environ, env, clear=True):
            client = FrappeClient(target="remote")

        self.assertEqual(client.config.target, "remote")
        self.assertIn("token remote-key:remote-secret", client.session.headers["Authorization"])


if __name__ == "__main__":
    unittest.main()
