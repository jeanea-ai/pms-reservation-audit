from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts.pms_access import AccessError, resolve_access


class PmsAccessTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def _write_config(self, pms: dict, status: str = "ACCESS_VERIFIED"):
        (self.root / "CAF15.json").write_text(
            json.dumps(
                {
                    "property_code": "CAF15",
                    "status": status,
                    "timezone": "America/Los_Angeles",
                    "pms": pms,
                }
            ),
            encoding="utf-8",
        )

    def test_current_pms_setup_contract_resolves(self):
        self._write_config(
            {"vendor": "choice_advantage", "legacy_username": "KUser.caf15"}
        )
        (self.root / ".secrets.json").write_text(
            json.dumps({"CAF15": {"pms_password": "secret"}}),
            encoding="utf-8",
        )
        config_before = (self.root / "CAF15.json").read_bytes()
        secrets_before = (self.root / ".secrets.json").read_bytes()
        access = resolve_access("caf15", environ={}, config_dir=self.root)
        self.assertEqual("KUser.caf15", access["username"])
        self.assertEqual("secret", access["password"])
        self.assertEqual("America/Los_Angeles", access["timezone"])
        self.assertEqual(config_before, (self.root / "CAF15.json").read_bytes())
        self.assertEqual(secrets_before, (self.root / ".secrets.json").read_bytes())

    def test_real_pms_setup_vendor_label_resolves_without_mutation(self):
        self._write_config(
            {
                "vendor": "SkyTouch / Choice Advantage",
                "legacy_username": "KUser.caf15",
            }
        )
        (self.root / ".secrets.json").write_text(
            json.dumps({"CAF15": {"pms_password": "secret"}}),
            encoding="utf-8",
        )
        config_before = (self.root / "CAF15.json").read_bytes()
        secrets_before = (self.root / ".secrets.json").read_bytes()
        access = resolve_access("CAF15", environ={}, config_dir=self.root)
        self.assertEqual("KUser.caf15", access["username"])
        self.assertEqual("secret", access["password"])
        self.assertEqual(config_before, (self.root / "CAF15.json").read_bytes())
        self.assertEqual(secrets_before, (self.root / ".secrets.json").read_bytes())

    def test_older_combined_secret_shape_remains_supported(self):
        self._write_config({"vendor": "choice_advantage"})
        (self.root / ".secrets.json").write_text(
            json.dumps(
                {
                    "CAF15": {
                        "pms_username": "KUser.legacy",
                        "pms_password": "secret",
                    }
                }
            ),
            encoding="utf-8",
        )
        access = resolve_access("CAF15", environ={}, config_dir=self.root)
        self.assertEqual("KUser.legacy", access["username"])

    def test_unverified_setup_is_refused(self):
        self._write_config(
            {"vendor": "choice_advantage", "legacy_username": "KUser.caf15"},
            status="ACCESS_PENDING",
        )
        (self.root / ".secrets.json").write_text(
            json.dumps({"CAF15": {"pms_password": "secret"}}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(AccessError, "finish PMS Setup"):
            resolve_access("CAF15", environ={}, config_dir=self.root)

    def test_other_vendor_is_refused_without_touching_setup(self):
        self._write_config(
            {"vendor": "hotelkey", "username": "hotelkey-user"}
        )
        (self.root / ".secrets.json").write_text(
            json.dumps({"CAF15": {"pms_password": "secret"}}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(AccessError, "does not support"):
            resolve_access("CAF15", environ={}, config_dir=self.root)

    def test_standalone_test_access_resolves_from_environment_only_when_enabled(self):
        env = {
            "PMS_RECON_TEST_USERNAME": "KUser.test",
            "PMS_RECON_TEST_PASSWORD": "super-secret",
            "PMS_RECON_TEST_TIMEZONE": "America/Los_Angeles",
            "PMS_RECON_TEST_PROPERTY_CODE": "CAF15",
        }
        access = resolve_access("caf15", environ=env, allow_test_access=True)
        self.assertEqual("standalone-test", access["source"])
        self.assertTrue(access["test_only"])
        self.assertEqual("super-secret", access["password"])
        with self.assertRaisesRegex(AccessError, "property config"):
            resolve_access("CAF15", environ=env, config_dir=self.root)

    def test_standalone_file_must_be_owner_only_and_match_property(self):
        path = self.root / "test-access.json"
        path.write_text(json.dumps({
            "property_code": "CAF15",
            "username": "KUser.test",
            "password": "super-secret",
            "timezone": "America/Los_Angeles",
        }), encoding="utf-8")
        path.chmod(0o644)
        with self.assertRaisesRegex(AccessError, "chmod 600"):
            resolve_access("CAF15", environ={}, allow_test_access=True, test_access_file=path)
        path.chmod(0o600)
        access = resolve_access("CAF15", environ={}, allow_test_access=True, test_access_file=path)
        self.assertEqual("standalone-test", access["source"])
        with self.assertRaisesRegex(AccessError, "does not match"):
            resolve_access("CAA00", environ={}, allow_test_access=True, test_access_file=path)

    def test_standalone_access_rejects_partial_or_invalid_timezone(self):
        with self.assertRaisesRegex(AccessError, "requires protected"):
            resolve_access("CAF15", environ={
                "PMS_RECON_TEST_USERNAME": "KUser.test",
            }, allow_test_access=True)
        with self.assertRaisesRegex(AccessError, "valid IANA"):
            resolve_access("CAF15", environ={
                "PMS_RECON_TEST_USERNAME": "KUser.test",
                "PMS_RECON_TEST_PASSWORD": "super-secret",
                "PMS_RECON_TEST_TIMEZONE": "Not/AZone",
            }, allow_test_access=True)


if __name__ == "__main__":
    unittest.main()
