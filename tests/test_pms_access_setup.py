from __future__ import annotations

import json
from http.server import HTTPServer
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from scripts.pms_access_setup import (
    SetupError,
    _form_page,
    _make_handler,
    write_access_file,
)


class PmsAccessSetupTests(unittest.TestCase):
    def test_writes_owner_only_access_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "private" / "CAF15.json"
            payload = {
                "property_code": "CAF15",
                "username": "KUser.test",
                "password": "very-private",
                "timezone": "America/Los_Angeles",
            }
            write_access_file(output, payload)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), payload)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)

    def test_refuses_implicit_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "CAF15.json"
            output.write_text("existing", encoding="utf-8")
            with self.assertRaisesRegex(SetupError, "already exists"):
                write_access_file(output, {"password": "replacement"})
            self.assertEqual(output.read_text(encoding="utf-8"), "existing")

    def test_refuses_symlink_target_even_during_rotation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.json"
            target.write_text("existing", encoding="utf-8")
            link = root / "access.json"
            link.symlink_to(target)
            with self.assertRaisesRegex(SetupError, "symbolic link"):
                write_access_file(link, {"password": "replacement"}, replace=True)
            self.assertEqual(target.read_text(encoding="utf-8"), "existing")

    def test_form_has_no_credential_values_or_external_actions(self):
        rendered = _form_page("token", "CAF15", "America/Los_Angeles").decode()
        self.assertIn('type="password"', rendered)
        self.assertIn('action="/token"', rendered)
        self.assertNotIn("very-private", rendered)
        self.assertNotIn("http://", rendered)
        self.assertNotIn("https://", rendered)

    def test_loopback_post_saves_without_echoing_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "CAF15.json"
            handler = _make_handler(
                token="one-use-token",
                hotel="CAF15",
                timezone_name="America/Los_Angeles",
                output=output,
                replace=False,
            )
            server = HTTPServer(("127.0.0.1", 0), handler)
            server.saved = False
            thread = threading.Thread(target=server.handle_request)
            thread.start()
            try:
                body = urlencode(
                    {
                        "token": "one-use-token",
                        "username": "KUser.test",
                        "password": "very-private",
                    }
                ).encode("utf-8")
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/one-use-token",
                    data=body,
                    method="POST",
                )
                response = urlopen(request, timeout=2)
                rendered = response.read().decode("utf-8")
            finally:
                thread.join(timeout=2)
                server.server_close()
            self.assertTrue(server.saved)
            self.assertNotIn("KUser.test", rendered)
            self.assertNotIn("very-private", rendered)
            self.assertEqual(json.loads(output.read_text())["property_code"], "CAF15")


if __name__ == "__main__":
    unittest.main()
