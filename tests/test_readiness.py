import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.readiness import _check_secrets, _check_timezone_guidance, inspect


class ReadinessTests(unittest.TestCase):
    def test_secrets_structure_is_checked_without_values_in_detail(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".secrets.json"
            path.write_text(json.dumps({"choiceadvantage": {"username": "alice", "password": "super-secret"}}), encoding="utf-8")
            ok, detail = _check_secrets(path)
            self.assertTrue(ok)
            self.assertNotIn("alice", detail)
            self.assertNotIn("super-secret", detail)
            path.write_text("{}", encoding="utf-8")
            self.assertFalse(_check_secrets(path)[0])

    def test_property_scoped_pms_credentials_are_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".secrets.json"
            path.write_text(json.dumps({"CAF15": {"pms_username": "alice", "pms_password": "super-secret"}}), encoding="utf-8")
            ok, detail = _check_secrets(path)
            self.assertTrue(ok)
            self.assertNotIn("alice", detail)
            self.assertNotIn("super-secret", detail)

    def test_pms_setup_password_only_secret_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".secrets.json"
            path.write_text(
                json.dumps({"CAF15": {"pms_password": "super-secret"}}),
                encoding="utf-8",
            )
            ok, detail = _check_secrets(path)
            self.assertTrue(ok)
            self.assertIn("PMS Setup property config", detail)
            self.assertNotIn("super-secret", detail)

    def test_timezone_guidance_requires_content_not_just_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "AGENTS.md"
            path.write_text("Use UTC.", encoding="utf-8")
            self.assertFalse(_check_timezone_guidance(path)[0])
            path.write_text("Resolve dates in the hotel property's local time zone.", encoding="utf-8")
            self.assertTrue(_check_timezone_guidance(path)[0])

    def test_external_report_pull_helper_is_not_a_readiness_dependency(self):
        source = Path(__file__).resolve().parents[1] / "scripts" / "readiness.py"
        text = source.read_text(encoding="utf-8")
        self.assertNotIn("choiceadvantage-report-pull", text)
        self.assertNotIn("report-pull skill", text)

    def test_missing_timezone_guidance_is_deferred_not_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "workspace" / "skills" / "audit"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("skill", encoding="utf-8")
            (skill / "scripts").mkdir()
            for name in ("source_to_input.py", "audit_pipeline.py", "duplicate_analysis.py", "audit_report.py"):
                (skill / "scripts" / name).write_text("", encoding="utf-8")
            (skill / "assets").mkdir()
            (skill / "assets" / "caf15_audit_report.pdf").write_bytes(b"%PDF")
            workspace = skill.parent.parent
            (workspace / "AGENTS.md").write_text("Generic Kolo instructions.", encoding="utf-8")
            with patch("scripts.readiness.importlib.util.find_spec", return_value=True), patch("scripts.readiness.shutil.which", return_value="kolo"), patch("scripts.readiness.subprocess.run") as run:
                run.return_value.returncode = 0
                run.return_value.stdout = "log-action"
                run.return_value.stderr = ""
                results = inspect(skill, {})
            self.assertIn(("SKIP", "property-local time rules", "confirm the selected property's local timezone during the live audit"), results)

    def test_live_access_is_explicitly_deferred(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "workspace" / "skills" / "audit"
            skill.mkdir(parents=True)
            with patch("scripts.readiness.importlib.util.find_spec", return_value=None), patch("scripts.readiness.find_chromium", return_value=None), patch("scripts.readiness.shutil.which", return_value=None):
                results = inspect(skill, {})
            self.assertIn(("SKIP", "live ChoiceADVANTAGE access", "confirm property, report visibility, and MFA state during the requested audit"), results)

    def test_missing_pdf_verifier_fails_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "workspace" / "skills" / "audit"
            skill.mkdir(parents=True)

            def module_lookup(name):
                return None if name == "pypdf" else True

            with patch("scripts.readiness.importlib.util.find_spec", side_effect=module_lookup), \
                    patch("scripts.readiness.find_chromium", return_value="chromium"), \
                    patch("scripts.readiness.shutil.which", return_value=None):
                results = inspect(skill, {})
            self.assertIn((
                "FAIL", "PDF structural verifier",
                "install pypdf; PDF publication must fail closed without it",
            ), results)

    def test_standalone_test_readiness_uses_protected_environment_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "workspace" / "skills" / "audit"
            skill.mkdir(parents=True)
            env = {
                "PMS_RECON_TEST_USERNAME": "KUser.test",
                "PMS_RECON_TEST_PASSWORD": "super-secret",
                "PMS_RECON_TEST_TIMEZONE": "America/Los_Angeles",
            }
            with patch("scripts.readiness.importlib.util.find_spec", return_value=True), \
                    patch("scripts.readiness.find_chromium", return_value="chromium"), \
                    patch("scripts.readiness.shutil.which", return_value=None):
                results = inspect(skill, env, test_hotel="CAF15")
            self.assertIn((
                "PASS", "standalone test access",
                "protected credentials and IANA timezone resolve (values not displayed)",
            ), results)
            rendered = repr(results)
            self.assertNotIn("KUser.test", rendered)
            self.assertNotIn("super-secret", rendered)

    def test_production_readiness_resolves_pms_setup_without_secret_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "workspace" / "skills" / "audit"
            skill.mkdir(parents=True)
            config_root = Path(tmp) / "config"
            config_root.mkdir()
            (config_root / "CAF15.json").write_text(
                json.dumps(
                    {
                        "property_code": "CAF15",
                        "status": "ACCESS_VERIFIED",
                        "timezone": "America/Los_Angeles",
                        "pms": {
                            "vendor": "choice_advantage",
                            "auth_mode": "direct_login_no_mfa",
                            "legacy_username": "KUser.private",
                        },
                    }
                ),
                encoding="utf-8",
            )
            env = {
                "KOLO_HOTELS_CONFIG_DIR": str(config_root),
                "PMS_PASSWORD_CAF15": "super-secret",
            }
            with patch(
                "scripts.readiness.importlib.util.find_spec", return_value=True
            ), patch(
                "scripts.readiness.find_chromium", return_value="chromium"
            ), patch(
                "scripts.readiness.shutil.which", return_value=None
            ):
                results = inspect(skill, env, production_hotel="CAF15")
            self.assertIn(
                (
                    "PASS",
                    "PMS Setup access",
                    "CAF15 resolves through mf-hotel-pms-setup (values not displayed)",
                ),
                results,
            )
            rendered = repr(results)
            self.assertNotIn("KUser.private", rendered)
            self.assertNotIn("super-secret", rendered)

    def test_okta_readiness_requires_gateway_credential_without_displaying_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "workspace" / "skills" / "audit"
            skill.mkdir(parents=True)
            access = {
                "property_code": "CA139",
                "source": "mf-hotel-pms-setup",
                "auth_mode": "okta_sso",
            }
            common = [
                patch("scripts.readiness.resolve_access", return_value=access),
                patch("scripts.readiness.importlib.util.find_spec", return_value=True),
                patch("scripts.readiness.find_chromium", return_value="chromium"),
                patch("scripts.readiness.shutil.which", return_value=None),
            ]
            with common[0], common[1], common[2], common[3]:
                missing = inspect(skill, {}, production_hotel="CA139")
            self.assertIn((
                "FAIL", "Okta Gmail gateway",
                "MATON_API_KEY is required for Okta identity and SMS OTP retrieval",
            ), missing)

            with patch("scripts.readiness.resolve_access", return_value=access), \
                    patch("scripts.readiness.importlib.util.find_spec", return_value=True), \
                    patch("scripts.readiness.find_chromium", return_value="chromium"), \
                    patch("scripts.readiness.shutil.which", return_value=None):
                present = inspect(
                    skill,
                    {"MATON_API_KEY": "gateway-secret"},
                    production_hotel="CA139",
                )
            self.assertIn((
                "PASS", "Okta Gmail gateway",
                "gateway credential available (value not displayed)",
            ), present)
            self.assertNotIn("gateway-secret", repr(present))


if __name__ == "__main__":
    unittest.main()
