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


if __name__ == "__main__":
    unittest.main()
