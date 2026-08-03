import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from PIL import Image

from vitamine.cloud_app import (
    JOB_STOP,
    app,
    claim_next_background_job,
    create_blank_workspace_database,
    execute_background_job,
    fail_background_job,
    register_workspace,
    workspace_worker_is_running,
)
from vitamine.cloud_crypto import is_encrypted_private_data
from vitamine.scripts.manage_cloud import create_invitation


_png_buffer = io.BytesIO()
Image.new("RGB", (1, 1), "#178064").save(_png_buffer, format="PNG")
PNG_1X1 = _png_buffer.getvalue()


class CloudAppTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.directory.name) / "cloud.sqlite"
        self.environment = patch.dict(
            os.environ,
            {
                "VITAMINE_CLOUD_DB": str(self.db_path),
                "VITAMINE_DATABASE_URL": "",
                "VITAMINE_CLOUD_PEPPER": "test-only-pepper",
                "VITAMINE_SESSION_ROOT": str(Path(self.directory.name) / "sessions"),
                "VITAMINE_JOB_ROOT": str(Path(self.directory.name) / "jobs"),
                "VITAMINE_JOB_WORK_ROOT": str(Path(self.directory.name) / "job-work"),
                "VITAMINE_DISABLE_JOB_RUNNER": "1",
                "VITAMINE_DEPLOYMENT_CONFIG": str(
                    Path(__file__).resolve().parents[1] / "deploy" / "strato" / "vitamine-hosted.json"
                ),
                "OPENAI_API_KEY": "sk-test-managed-key",
            },
        )
        self.environment.start()
        self.mail_delivery = patch("vitamine.cloud_app.send_verification_email")
        self.send_verification_email = self.mail_delivery.start()
        self.password_mail_delivery = patch("vitamine.cloud_app.send_password_reset_email")
        self.send_password_reset_email = self.password_mail_delivery.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.mail_delivery.stop()
        self.password_mail_delivery.stop()
        self.environment.stop()
        self.directory.cleanup()

    def redeem(self, *, max_uses=1):
        code = create_invitation("Test invitation", max_uses=max_uses, expires_days=1)
        response = self.client.post("/api/invitations/redeem", json={"code": code})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("vitamine_session", response.cookies)
        return code, response.cookies["vitamine_session"]

    def register(self, *, email="tester@example.org", token=None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        response = self.client.post(
            "/api/account/register",
            headers=headers,
            json={
                "email": email,
                "password": "correct-horse-battery-staple",
                "display_name": "Test Researcher",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.verify_email(email)
        return response

    def verify_email(self, email):
        with sqlite3.connect(self.db_path) as con:
            con.execute("UPDATE members SET email_verified_at=account_created_at WHERE email=?", (email,))
            con.commit()

    def create_account(self, *, email="tester@example.org"):
        _, token = self.redeem()
        self.register(email=email, token=token)
        return token

    def test_new_account_starts_with_plus_trial_and_zero_usage_cost(self):
        self.create_account()
        response = self.client.get("/api/account/premium-account")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["balance_microusd"], 0)
        self.assertEqual(payload["credited_microusd"], 0)
        self.assertEqual(payload["charged_microusd"], 0)
        self.assertFalse(payload["enforcement_enabled"])
        self.assertFalse(payload["top_up"]["enabled"])
        account = self.client.get("/api/account/databases").json()
        self.assertTrue(account["plus"]["active"])
        self.assertEqual(account["plus"]["plan"], "trial")

    def test_expired_plus_account_keeps_core_access_but_llm_jobs_require_upgrade(self):
        self.create_account()
        with sqlite3.connect(self.db_path) as con:
            con.execute("UPDATE members SET plus_trial_ends_at='2020-01-01T00:00:00+00:00'")
            con.commit()
        account = self.client.get("/api/account/databases").json()
        self.assertFalse(account["plus"]["active"])
        self.assertEqual(account["plus"]["plan"], "free")
        page = self.client.get("/")
        self.assertIn('id="plusStatusButton"', page.text)
        self.assertIn("Upgrade to +", self.client.get("/assets/account.js").text)
        self.assertNotIn("Premium features account balance", page.text)
        created = self.client.post("/gateway/workspace/new")
        self.assertEqual(created.status_code, 200, created.text)
        blocked = self.client.post("/api/cloud/jobs/enrich-cv")
        self.assertEqual(blocked.status_code, 402, blocked.text)
        self.assertEqual(blocked.json()["detail"]["code"], "vitamine_plus_required")

    def test_plus_developer_toggle_is_account_scoped(self):
        self.create_account()
        hidden = self.client.put("/api/account/plus-developer-toggle", json={"active": False})
        self.assertEqual(hidden.status_code, 404)
        with sqlite3.connect(self.db_path) as con:
            con.execute("UPDATE members SET plus_dev_toggle_enabled=1")
            con.commit()
        disabled = self.client.put("/api/account/plus-developer-toggle", json={"active": False})
        self.assertEqual(disabled.status_code, 200, disabled.text)
        self.assertFalse(disabled.json()["plus"]["active"])
        self.assertEqual(disabled.json()["plus"]["plan"], "developer")
        enabled = self.client.put("/api/account/plus-developer-toggle", json={"active": True})
        self.assertTrue(enabled.json()["plus"]["active"])

    def test_legacy_paypal_topup_is_retired_without_creating_credit(self):
        self.create_account()
        with patch.dict(
            os.environ,
            {"VITAMINE_PAYPAL_BETA_TOPUP_URL": "https://paypal.me/tester/5USD"},
        ):
            headers = {"Idempotency-Key": "paypal-test-claim-1"}
            first = self.client.post(
                "/api/account/premium-account/paypal-beta-topup",
                headers=headers,
                json={"acknowledged_paid": True},
            )
            self.assertEqual(first.status_code, 410, first.text)

        with sqlite3.connect(self.db_path) as con:
            claims = con.execute("SELECT COUNT(*) FROM paypal_beta_topups").fetchone()[0]
            credits = con.execute(
                "SELECT COUNT(*) FROM premium_account_transactions WHERE kind='paypal_beta_topup'"
            ).fetchone()[0]
        self.assertEqual(claims, 0)
        self.assertEqual(credits, 0)

    def test_retired_paypal_topup_requires_authentication(self):
        self.create_account()
        unavailable = self.client.post(
            "/api/account/premium-account/paypal-beta-topup",
            headers={"Idempotency-Key": "paypal-test-claim-2"},
            json={"acknowledged_paid": True},
        )
        self.assertEqual(unavailable.status_code, 410)
        with patch.dict(
            os.environ,
            {"VITAMINE_PAYPAL_BETA_TOPUP_URL": "https://paypal.me/tester/5USD"},
        ):
            unconfirmed = self.client.post(
                "/api/account/premium-account/paypal-beta-topup",
                headers={"Idempotency-Key": "paypal-test-claim-3"},
                json={"acknowledged_paid": False},
            )
            self.assertEqual(unconfirmed.status_code, 410)
            missing_key = self.client.post(
                "/api/account/premium-account/paypal-beta-topup",
                json={"acknowledged_paid": True},
            )
            self.assertEqual(missing_key.status_code, 410)

    def test_new_account_requires_one_time_email_confirmation(self):
        _, token = self.redeem()
        response = self.client.post(
            "/api/account/register",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "email": "confirm@example.org",
                "password": "correct-horse-battery-staple",
                "display_name": "Confirm Me",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["email_verification_required"])
        self.assertEqual(self.client.get("/api/account/databases").status_code, 403)
        verification_token = self.send_verification_email.call_args.args[1]
        verified = self.client.get(
            "/api/account/verify-email",
            params={"token": verification_token},
            follow_redirects=False,
        )
        self.assertEqual(verified.status_code, 303)
        self.assertEqual(verified.headers["location"], "/?email_confirmation=verified")
        self.assertEqual(self.client.get("/api/account/databases").status_code, 200)
        reused = self.client.get(
            "/api/account/verify-email",
            params={"token": verification_token},
            follow_redirects=False,
        )
        self.assertEqual(reused.headers["location"], "/?email_confirmation=invalid")

    def test_password_reset_is_single_use_and_revokes_existing_sessions(self):
        self.create_account()
        requested = self.client.post(
            "/api/account/password-reset/request", json={"email": "tester@example.org"}
        )
        self.assertEqual(requested.status_code, 200, requested.text)
        reset_token = self.send_password_reset_email.call_args.args[1]
        completed = self.client.post(
            "/api/account/password-reset/complete",
            json={"token": reset_token, "password": "a-new-correct-horse-password"},
        )
        self.assertEqual(completed.status_code, 200, completed.text)
        self.assertEqual(self.client.get("/api/session").status_code, 401)
        reused = self.client.post(
            "/api/account/password-reset/complete",
            json={"token": reset_token, "password": "another-correct-horse-password"},
        )
        self.assertEqual(reused.status_code, 400)
        signed_in = self.client.post(
            "/api/account/login",
            json={"email": "tester@example.org", "password": "a-new-correct-horse-password"},
        )
        self.assertEqual(signed_in.status_code, 200, signed_in.text)

    def test_password_reset_request_does_not_disclose_unknown_accounts(self):
        self.redeem()
        response = self.client.post(
            "/api/account/password-reset/request", json={"email": "unknown@example.org"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.send_password_reset_email.assert_not_called()

    def test_workspace_worker_liveness_rejects_a_reused_pid(self):
        row = {"pid": 987654, "port": 58153}
        with (
            patch("vitamine.cloud_app.process_is_running", return_value=True),
            patch("vitamine.cloud_app.Path.exists", return_value=True),
            patch(
                "vitamine.cloud_app.Path.read_bytes",
                return_value=b"/usr/bin/apt-get\0install\0pandoc\0",
            ),
        ):
            self.assertFalse(workspace_worker_is_running(row))

    def test_workspace_worker_liveness_accepts_the_expected_worker(self):
        row = {"pid": 987654, "port": 58153}
        with (
            patch("vitamine.cloud_app.process_is_running", return_value=True),
            patch("vitamine.cloud_app.Path.exists", return_value=True),
            patch(
                "vitamine.cloud_app.Path.read_bytes",
                return_value=(
                    b"/srv/vitamine-cloud/venv/bin/python3\x00-m\x00uvicorn\x00"
                    b"vitamine.app:app\x00--host\x00127.0.0.1\x00--port\x0058153\x00"
                ),
            ),
        ):
            self.assertTrue(workspace_worker_is_running(row))

    def test_invitation_is_usage_limited(self):
        code, _ = self.redeem()
        second = self.client.post("/api/invitations/redeem", json={"code": code})
        self.assertEqual(second.status_code, 403)

    def test_invitation_can_be_configured_for_multiple_devices(self):
        code = create_invitation("Shared test invitation", max_uses=3, expires_days=1)
        for _ in range(3):
            client = TestClient(app)
            response = client.post("/api/invitations/redeem", json={"code": code})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(client.get("/api/session").status_code, 200)
            client.close()
        fourth = self.client.post("/api/invitations/redeem", json={"code": code})
        self.assertEqual(fourth.status_code, 403)

    def test_invitation_accepts_form_submission_and_returns_readable_validation_errors(self):
        code = create_invitation("Form invitation", max_uses=1, expires_days=1)
        accepted = self.client.post("/api/invitations/redeem", data={"code": code})
        self.assertEqual(accepted.status_code, 200, accepted.text)

        safari_code = create_invitation("Safari invitation", max_uses=1, expires_days=1)
        safari_style = self.client.post(
            "/api/invitations/redeem",
            content=json.dumps({"code": safari_code}),
            headers={"Content-Type": "text/plain;charset=UTF-8"},
        )
        self.assertEqual(safari_style.status_code, 200, safari_style.text)

        invalid = self.client.post("/api/invitations/redeem", json={"code": {"unexpected": True}})
        self.assertEqual(invalid.status_code, 422)
        self.assertIsInstance(invalid.json()["detail"], str)
        self.assertNotIn("[object Object]", invalid.json()["detail"])

    def test_session_cookie_remembers_device(self):
        self.redeem()
        response = self.client.get("/api/session")
        self.assertEqual(response.status_code, 200)
        self.assertIn("member_id", response.json())

    def test_workspace_requires_invitation_cookie(self):
        anonymous = TestClient(app)
        self.assertEqual(anonymous.get("/workspace").status_code, 401)
        anonymous.close()
        _, token = self.redeem()
        self.assertEqual(self.client.get("/workspace").status_code, 403)
        self.register(token=token)
        workspace = self.client.get("/workspace")
        self.assertEqual(workspace.status_code, 200)
        self.assertIn("Choose a database", workspace.text)

    def test_landing_page_describes_cv_management_without_profile_publishing(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Finally, an academic CV manager that works.", response.text)
        self.assertIn("Curate and manage your academic CVs for various", response.text)
        self.assertIn("occasions, in multiple languages.", response.text)
        self.assertNotIn("share a profile", response.text)

    def test_uploaded_database_runs_original_vitamine_app_in_isolated_worker(self):
        self.create_account()
        example = Path(__file__).resolve().parents[1] / "data" / "example.vitamine"
        with example.open("rb") as handle:
            opened = self.client.post(
                "/gateway/workspace/open",
                files={"file": ("example.vitamine", handle, "application/vnd.sqlite3")},
            )
        self.assertEqual(opened.status_code, 200, opened.text)
        self.assertIn("vitamine_workspace", opened.cookies)

        original_app = self.client.get("/")
        self.assertEqual(original_app.status_code, 200, original_app.text)
        self.assertIn('<title>VitaMine</title>', original_app.text)
        self.assertIn('id="dashboardTab"', original_app.text)

        database = self.client.get("/api/database")
        self.assertEqual(database.status_code, 200, database.text)
        self.assertEqual(database.json()["active_name"], "workspace.vitamine")

        llm_settings = self.client.get("/api/cv-import/settings")
        self.assertEqual(llm_settings.status_code, 200, llm_settings.text)
        self.assertEqual(llm_settings.json()["provider"], "openai")
        self.assertTrue(llm_settings.json()["managed"])
        self.assertFalse(llm_settings.json()["configuration_allowed"])
        self.assertTrue(llm_settings.json()["api_key_set"])

        rejected_change = self.client.put(
            "/api/cv-import/settings",
            json={"provider": "openai_compatible", "api_base_url": "https://example.invalid/v1"},
        )
        self.assertEqual(rejected_change.status_code, 403, rejected_change.text)

        downloaded = self.client.get("/gateway/workspace/download")
        self.assertEqual(downloaded.status_code, 200)
        self.assertTrue(downloaded.content.startswith(b"SQLite format 3"))

        closed = self.client.delete("/gateway/workspace")
        self.assertEqual(closed.status_code, 200)

    def test_start_from_scratch_creates_blank_original_app_workspace(self):
        self.create_account()
        opened = self.client.post("/gateway/workspace/new")
        self.assertEqual(opened.status_code, 200, opened.text)

        database = self.client.get("/api/database")
        self.assertEqual(database.status_code, 200, database.text)
        self.assertTrue(database.json()["is_empty"])
        self.assertEqual(database.json()["counts"]["entries"], 0)
        self.assertEqual(database.json()["counts"]["publications"], 0)

        onboarding = self.client.get("/api/onboarding")
        self.assertEqual(onboarding.status_code, 200, onboarding.text)
        self.assertTrue(onboarding.json()["llm_configured"])
        self.assertTrue(onboarding.json()["llm_managed"])
        self.assertTrue(onboarding.json()["skip_llm_configuration"])

        with sqlite3.connect(self.db_path) as con:
            stored_blob = con.execute(
                "SELECT sqlite_blob FROM account_databases WHERE id=?", (opened.json()["database_id"],)
            ).fetchone()[0]
            self.assertTrue(is_encrypted_private_data(stored_blob))
            self.assertFalse(stored_blob.startswith(b"SQLite format 3"))

        self.assertEqual(self.client.delete("/gateway/workspace").status_code, 200)

    def test_cloud_cv_import_is_queued_and_survives_leaving_the_workspace(self):
        self.create_account()
        opened = self.client.post("/gateway/workspace/new")
        self.assertEqual(opened.status_code, 200, opened.text)
        database_id = opened.json()["database_id"]

        queued = self.client.post(
            "/api/cloud/jobs/cv-import",
            files={"files": ("cv.txt", b"CURRICULUM VITAE\\nEducation\\n2020 Example", "text/plain")},
        )
        self.assertEqual(queued.status_code, 202, queued.text)
        self.assertTrue(queued.json()["background"])
        self.assertEqual(queued.json()["job"]["status"], "queued")
        job_id = queued.json()["job"]["id"]
        stored_uploads = list((Path(self.directory.name) / "jobs" / job_id / "uploads").iterdir())
        self.assertEqual(len(stored_uploads), 1)
        self.assertTrue(is_encrypted_private_data(stored_uploads[0].read_bytes()))
        self.assertNotIn(b"CURRICULUM VITAE", stored_uploads[0].read_bytes())

        blocked_edit = self.client.put("/api/person", json={"full_name": "Too Soon"})
        self.assertEqual(blocked_edit.status_code, 409, blocked_edit.text)

        closed = self.client.delete("/gateway/workspace")
        self.assertEqual(closed.status_code, 200, closed.text)
        jobs = self.client.get("/api/cloud/jobs")
        self.assertEqual(jobs.status_code, 200, jobs.text)
        self.assertEqual(jobs.json()["jobs"][0]["id"], job_id)
        self.assertEqual(jobs.json()["jobs"][0]["database_id"], database_id)

        with sqlite3.connect(self.db_path) as con:
            status = con.execute(
                "SELECT status FROM background_jobs WHERE id=?",
                (job_id,),
            ).fetchone()[0]
        self.assertEqual(status, "queued")

    def test_cloud_enrichment_is_queued_once_per_database(self):
        self.create_account()
        opened = self.client.post("/gateway/workspace/new")
        self.assertEqual(opened.status_code, 200, opened.text)

        queued = self.client.post("/api/cloud/jobs/enrich-cv")
        self.assertEqual(queued.status_code, 202, queued.text)
        duplicate = self.client.post("/api/cloud/jobs/enrich-cv")
        self.assertEqual(duplicate.status_code, 409, duplicate.text)
        self.assertIn("already has a background process", duplicate.json()["detail"])

    def test_cloud_enrichment_retry_reuses_the_idempotent_job(self):
        self.create_account()
        opened = self.client.post("/gateway/workspace/new")
        self.assertEqual(opened.status_code, 200, opened.text)
        headers = {"Idempotency-Key": "enrich-retry-0001"}

        # Treat the first response as lost and submit the same logical request again.
        first = self.client.post("/api/cloud/jobs/enrich-cv", headers=headers)
        retried = self.client.post("/api/cloud/jobs/enrich-cv", headers=headers)

        self.assertEqual(first.status_code, 202, first.text)
        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertFalse(first.json()["idempotent_replay"])
        self.assertTrue(retried.json()["idempotent_replay"])
        self.assertEqual(first.json()["job"]["id"], retried.json()["job"]["id"])
        with sqlite3.connect(self.db_path) as con:
            count = con.execute("SELECT COUNT(*) FROM background_jobs").fetchone()[0]
        self.assertEqual(count, 1)

    def test_cv_import_idempotency_rejects_changed_payload_or_operation(self):
        self.create_account()
        opened = self.client.post("/gateway/workspace/new")
        self.assertEqual(opened.status_code, 200, opened.text)
        headers = {"Idempotency-Key": "import-conflict-0001"}
        original = self.client.post(
            "/api/cloud/jobs/cv-import",
            headers=headers,
            files={"files": ("cv.txt", b"Original CV", "text/plain")},
        )
        retried = self.client.post(
            "/api/cloud/jobs/cv-import",
            headers=headers,
            files={"files": ("cv.txt", b"Original CV", "text/plain")},
        )
        changed = self.client.post(
            "/api/cloud/jobs/cv-import",
            headers=headers,
            files={"files": ("cv.txt", b"Changed CV", "text/plain")},
        )
        changed_operation = self.client.post("/api/cloud/jobs/enrich-cv", headers=headers)

        self.assertEqual(original.status_code, 202, original.text)
        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertTrue(retried.json()["idempotent_replay"])
        self.assertEqual(original.json()["job"]["id"], retried.json()["job"]["id"])
        self.assertEqual(changed.status_code, 409, changed.text)
        self.assertEqual(changed_operation.status_code, 409, changed_operation.text)
        self.assertIn("different background request", changed.json()["detail"])
        self.assertIn("different background request", changed_operation.json()["detail"])
        with sqlite3.connect(self.db_path) as con:
            count = con.execute("SELECT COUNT(*) FROM background_jobs").fetchone()[0]
        self.assertEqual(count, 1)
        job_directories = [
            path
            for path in (Path(self.directory.name) / "jobs").iterdir()
            if path.is_dir()
        ]
        self.assertEqual(len(job_directories), 1)

    def test_idempotency_keys_are_isolated_between_accounts(self):
        self.create_account(email="first@example.org")
        first_workspace = self.client.post("/gateway/workspace/new")
        self.assertEqual(first_workspace.status_code, 200, first_workspace.text)
        headers = {"Idempotency-Key": "shared-across-owners-0001"}
        first = self.client.post("/api/cloud/jobs/enrich-cv", headers=headers)
        self.assertEqual(first.status_code, 202, first.text)

        second_client = TestClient(app)
        try:
            code = create_invitation("Second account", max_uses=1, expires_days=1)
            redeemed = second_client.post("/api/invitations/redeem", json={"code": code})
            self.assertEqual(redeemed.status_code, 200, redeemed.text)
            registered = second_client.post(
                "/api/account/register",
                json={
                    "email": "second@example.org",
                    "password": "correct-horse-battery-staple",
                    "display_name": "Second Researcher",
                },
            )
            self.assertEqual(registered.status_code, 200, registered.text)
            self.verify_email("second@example.org")
            second_workspace = second_client.post("/gateway/workspace/new")
            self.assertEqual(second_workspace.status_code, 200, second_workspace.text)
            second = second_client.post("/api/cloud/jobs/enrich-cv", headers=headers)
        finally:
            second_client.close()

        self.assertEqual(second.status_code, 202, second.text)
        self.assertNotEqual(first.json()["job"]["id"], second.json()["job"]["id"])
        with sqlite3.connect(self.db_path) as con:
            owners = con.execute(
                "SELECT COUNT(DISTINCT member_id) FROM background_jobs WHERE idempotency_key=?",
                (headers["Idempotency-Key"],),
            ).fetchone()[0]
        self.assertEqual(owners, 2)

    def test_invalid_idempotency_key_is_rejected(self):
        self.create_account()
        self.assertEqual(self.client.post("/gateway/workspace/new").status_code, 200)
        response = self.client.post(
            "/api/cloud/jobs/enrich-cv",
            headers={"Idempotency-Key": "short"},
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("Idempotency-Key", response.json()["detail"])

    def test_unexpected_request_failure_returns_correlated_privacy_safe_support_id(self):
        private_values = (
            "confidential-cv-text oauth-secret-value private-cookie-value invitation-value"
        )
        client = TestClient(app, raise_server_exceptions=False)
        try:
            with (
                patch("vitamine.cloud_app.connect", side_effect=RuntimeError(private_values)),
                self.assertLogs("vitamine.cloud", level="ERROR") as captured,
            ):
                response = client.get(
                    "/health",
                    headers={"Cookie": "vitamine_session=private-cookie-value"},
                )
        finally:
            client.close()

        self.assertEqual(response.status_code, 500, response.text)
        support_id = response.json()["support_id"]
        self.assertRegex(support_id, r"^VM-[A-F0-9]{32}$")
        self.assertIn(support_id, response.json()["detail"])
        record = json.loads(captured.records[-1].getMessage())
        self.assertEqual(record["event"], "unexpected_request_failure")
        self.assertEqual(record["support_id"], support_id)
        self.assertEqual(record["endpoint"], "/health")
        self.assertEqual(record["category"], "internal_error")
        combined = response.text + "\n" + "\n".join(
            item.getMessage() for item in captured.records
        )
        for private_value in private_values.split():
            self.assertNotIn(private_value, combined)

    def test_expected_request_error_keeps_status_without_support_identifier(self):
        self.create_account()
        self.assertEqual(self.client.post("/gateway/workspace/new").status_code, 200)
        with patch("vitamine.cloud_app.log_support_event") as log_event:
            response = self.client.post(
                "/api/cloud/jobs/enrich-cv",
                headers={"Idempotency-Key": "short"},
            )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertNotIn("support_id", response.json())
        log_event.assert_not_called()

    def test_background_job_failure_stores_and_logs_only_support_metadata(self):
        self.create_account()
        self.assertEqual(self.client.post("/gateway/workspace/new").status_code, 200)
        queued = self.client.post("/api/cloud/jobs/enrich-cv")
        self.assertEqual(queued.status_code, 202, queued.text)
        job_id = queued.json()["job"]["id"]
        private_values = "confidential-cv-text oauth-secret-value private-cookie-value"

        with self.assertLogs("vitamine.cloud", level="ERROR") as captured:
            fail_background_job(job_id, RuntimeError(private_values))

        failed = self.client.get(f"/api/cloud/jobs/{job_id}")
        self.assertEqual(failed.status_code, 200, failed.text)
        job = failed.json()["job"]
        self.assertEqual(job["status"], "failed")
        self.assertRegex(job["support_id"], r"^VM-[A-F0-9]{32}$")
        self.assertIn(job["support_id"], job["error"])
        record = json.loads(captured.records[-1].getMessage())
        self.assertEqual(record["event"], "background_job_failed")
        self.assertEqual(record["support_id"], job["support_id"])
        self.assertEqual(record["job_id"], job_id)
        self.assertEqual(record["job_kind"], "enrich_cv")
        combined = job["error"] + "\n" + "\n".join(
            item.getMessage() for item in captured.records
        )
        for private_value in private_values.split():
            self.assertNotIn(private_value, combined)
        with sqlite3.connect(self.db_path) as con:
            stored = con.execute(
                "SELECT support_id, error_message FROM background_jobs WHERE id=?",
                (job_id,),
            ).fetchone()
        self.assertEqual(stored[0], job["support_id"])
        self.assertNotIn("confidential-cv-text", stored[1])

    def test_queued_cv_import_executes_and_refreshes_the_open_workspace(self):
        self.create_account()
        opened = self.client.post("/gateway/workspace/new")
        self.assertEqual(opened.status_code, 200, opened.text)
        queued = self.client.post(
            "/api/cloud/jobs/cv-import",
            files={
                "files": (
                    "cv.txt",
                    b"Curriculum Vitae\nEducation\n2020-2024 Example University - Researcher\n",
                    "text/plain",
                )
            },
        )
        self.assertEqual(queued.status_code, 202, queued.text)
        job = claim_next_background_job()
        self.assertIsNotNone(job)

        no_llm_profile = Path(self.directory.name) / "no-llm-hosted.json"
        no_llm_profile.write_text(
            json.dumps(
                {
                    "mode": "hosted",
                    "llm": {
                        "managed": True,
                        "allow_user_configuration": False,
                        "provider": "none",
                    },
                    "onboarding": {"skip_llm_configuration": True},
                }
            ),
            encoding="utf-8",
        )
        JOB_STOP.clear()
        with patch.dict(
            os.environ,
            {"VITAMINE_DEPLOYMENT_CONFIG": str(no_llm_profile)},
        ):
            execute_background_job(job)

        finished = self.client.get(f"/api/cloud/jobs/{queued.json()['job']['id']}")
        self.assertEqual(finished.status_code, 200, finished.text)
        self.assertEqual(finished.json()["job"]["status"], "succeeded")
        inbox = self.client.get("/api/import-inbox?status=pending&target_type=all")
        self.assertEqual(inbox.status_code, 200, inbox.text)
        self.assertGreater(len(inbox.json()["items"]), 0)

    def test_hosted_workspace_uses_account_menu_instead_of_close_session_button(self):
        self.create_account()
        opened = self.client.post("/gateway/workspace/new")
        self.assertEqual(opened.status_code, 200, opened.text)
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn('id="cloudAccountMenu"', page.text)
        self.assertIn('id="cloudSignOut"', page.text)
        self.assertIn("Download personal SQLite copy (.vitamine)", page.text)
        self.assertNotIn('id="cloudCloseSession"', page.text)
        self.assertNotIn('id="autoMapInstitution"', page.text)
        self.assertIn("Map coordinates are added automatically", page.text)
        self.assertNotIn(">Save Person</button>", page.text)
        self.assertIn('id="personAutosaveStatus"', page.text)
        self.assertIn('id="personPortraitInput"', page.text)
        self.assertIn('id="removePersonPortrait"', page.text)
        self.assertIn('id="brandHome"', page.text)
        self.assertIn('aria-label="Go to Dashboard"', page.text)
        stylesheet = self.client.get("/static/styles.css")
        self.assertEqual(stylesheet.status_code, 200, stylesheet.text)
        self.assertIn(".buttonLike {", stylesheet.text)
        self.assertIn("#removePersonPortrait {", stylesheet.text)
        self.assertIn("margin: 0;", stylesheet.text)
        self.assertIn(".brandHome {", stylesheet.text)
        script = self.client.get("/static/app.js")
        self.assertEqual(script.status_code, 200, script.text)
        self.assertIn("async function returnToWorkspaceHome()", script.text)
        self.assertIn(
            '$("#brandHome").addEventListener("click", returnToWorkspaceHome)',
            script.text,
        )
        self.assertIn(
            '$("#cloudMyCvs").addEventListener("click", returnToWorkspaceHome)',
            script.text,
        )
        self.assertIn("function createIdempotencyKey()", script.text)
        self.assertIn("async function submitCloudJob(path, options = {})", script.text)
        self.assertIn('"Idempotency-Key": idempotencyKey', script.text)
        self.assertIn("return api(path, request);", script.text)
        self.assertIn('id="addIdentifier"', page.text)
        self.assertIn('id="identifierDialog"', page.text)
        self.assertNotIn('id="newIdentifier"', page.text)

    def test_orcid_oauth_uses_one_time_state_and_stores_encrypted_tokens(self):
        self.create_account()
        opened = self.client.post("/gateway/workspace/new")
        self.assertEqual(opened.status_code, 200, opened.text)
        oauth_environment = {
            "ORCID_OAUTH_CLIENT_ID": "APP-TEST",
            "ORCID_OAUTH_CLIENT_SECRET": "orcid-test-secret",
            "ORCID_OAUTH_BASE_URL": "https://sandbox.orcid.org",
            "ORCID_OAUTH_REDIRECT_URI": (
                "https://vitamine.cloud/gateway/orcid/oauth/callback"
            ),
        }
        with patch.dict(os.environ, oauth_environment):
            cross_site = self.client.post(
                "/gateway/orcid/oauth/start",
                headers={"Origin": "https://malicious.example"},
            )
            self.assertEqual(cross_site.status_code, 403, cross_site.text)

            started = self.client.post(
                "/gateway/orcid/oauth/start",
            )
            self.assertEqual(started.status_code, 200, started.text)
            authorization_url = urlparse(started.json()["authorization_url"])
            self.assertEqual(authorization_url.netloc, "sandbox.orcid.org")
            authorization_query = parse_qs(authorization_url.query)
            self.assertEqual(authorization_query["scope"], ["/authenticate"])
            self.assertEqual(
                authorization_query["redirect_uri"],
                [oauth_environment["ORCID_OAUTH_REDIRECT_URI"]],
            )
            state = authorization_query["state"][0]

            token_payload = {
                "orcid_id": "0000-0002-1825-0097",
                "display_name": "Test Researcher",
                "access_token": "sensitive-access-token",
                "refresh_token": "sensitive-refresh-token",
                "token_type": "bearer",
                "scope": "/authenticate",
                "expires_in": 631138518,
            }
            with (
                patch(
                    "vitamine.cloud_app.exchange_orcid_authorization_code",
                    new=AsyncMock(return_value=token_payload),
                ) as exchange,
                patch(
                    "vitamine.cloud_app.apply_authenticated_orcid_to_workspace",
                    new=AsyncMock(),
                ) as apply_orcid,
            ):
                completed = self.client.get(
                    "/gateway/orcid/oauth/callback",
                    params={"code": "authorization-code", "state": state},
                    follow_redirects=False,
                )
            self.assertEqual(completed.status_code, 303, completed.text)
            self.assertEqual(
                parse_qs(urlparse(completed.headers["location"]).query)["orcid_oauth"],
                ["connected"],
            )
            exchange.assert_awaited_once_with("authorization-code")
            apply_orcid.assert_awaited_once()

            status = self.client.get("/gateway/orcid/oauth/status")
            self.assertEqual(status.status_code, 200, status.text)
            self.assertTrue(status.json()["configured"])
            self.assertTrue(status.json()["connected"])
            self.assertEqual(status.json()["orcid_id"], token_payload["orcid_id"])

            replayed = self.client.get(
                "/gateway/orcid/oauth/callback",
                params={"code": "replayed-code", "state": state},
                follow_redirects=False,
            )
            self.assertEqual(replayed.status_code, 400, replayed.text)

        with sqlite3.connect(self.db_path) as con:
            connection = con.execute(
                """
                SELECT access_token_ciphertext, refresh_token_ciphertext
                FROM orcid_oauth_connections
                """
            ).fetchone()
            remaining_states = con.execute(
                "SELECT COUNT(*) FROM oauth_authorization_states"
            ).fetchone()[0]
        self.assertIsNotNone(connection)
        self.assertNotEqual(connection[0], token_payload["access_token"])
        self.assertNotIn(token_payload["access_token"], connection[0])
        self.assertNotEqual(connection[1], token_payload["refresh_token"])
        self.assertEqual(remaining_states, 0)

    def test_zotero_oauth_uses_one_time_request_and_stores_encrypted_key(self):
        self.create_account()
        opened = self.client.post("/gateway/workspace/new")
        self.assertEqual(opened.status_code, 200, opened.text)
        oauth_environment = {
            "ZOTERO_OAUTH_CLIENT_KEY": "zotero-test-key",
            "ZOTERO_OAUTH_CLIENT_SECRET": "zotero-test-secret",
            "ZOTERO_OAUTH_CALLBACK_URL": "https://vitamine.cloud/gateway/zotero/oauth/callback",
        }
        temporary = {"token": "temporary-token", "secret": "temporary-secret"}
        token = {"api_key": "sensitive-zotero-api-key", "user_id": "12345", "username": "Researcher"}
        access = {"userID": 12345, "access": {"user": {"library": True}, "groups": {"all": {"library": True}}}}
        with (
            patch.dict(os.environ, oauth_environment),
            patch("vitamine.cloud_app.request_zotero_temporary_credentials", new=AsyncMock(return_value=temporary)),
        ):
            started = self.client.post("/gateway/zotero/oauth/start")
            self.assertEqual(started.status_code, 200, started.text)
            authorization = urlparse(started.json()["authorization_url"])
            query = parse_qs(authorization.query)
            self.assertEqual(query["oauth_token"], [temporary["token"]])
            self.assertEqual(query["library_access"], ["1"])
            self.assertEqual(query["write_access"], ["0"])
            self.assertEqual(query["all_groups"], ["read"])

            with (
                patch("vitamine.cloud_app.exchange_zotero_access_token", new=AsyncMock(return_value=token)) as exchange,
                patch("vitamine.cloud_app.verify_zotero_api_key", new=AsyncMock(return_value=access)),
                patch("vitamine.cloud_app.restart_workspace_with_current_connections") as restart,
            ):
                completed = self.client.get(
                    "/gateway/zotero/oauth/callback",
                    params={"oauth_token": temporary["token"], "oauth_verifier": "verifier"},
                    follow_redirects=False,
                )
            self.assertEqual(completed.status_code, 303, completed.text)
            self.assertEqual(parse_qs(urlparse(completed.headers["location"]).query)["zotero_oauth"], ["connected"])
            exchange.assert_awaited_once_with(temporary["token"], temporary["secret"], "verifier")
            restart.assert_called_once()

            status = self.client.get("/gateway/zotero/oauth/status")
            self.assertTrue(status.json()["connected"])
            self.assertEqual(status.json()["username"], token["username"])
            replayed = self.client.get(
                "/gateway/zotero/oauth/callback",
                params={"oauth_token": temporary["token"], "oauth_verifier": "replay"},
                follow_redirects=False,
            )
            self.assertEqual(replayed.status_code, 400, replayed.text)

        with sqlite3.connect(self.db_path) as con:
            stored = con.execute("SELECT api_key_ciphertext FROM zotero_oauth_connections").fetchone()[0]
            remaining = con.execute("SELECT COUNT(*) FROM zotero_oauth_requests").fetchone()[0]
        self.assertNotEqual(stored, token["api_key"])
        self.assertNotIn(token["api_key"], stored)
        self.assertEqual(remaining, 0)

    def test_account_database_survives_close_logout_and_reopen(self):
        self.create_account()
        opened = self.client.post("/gateway/workspace/new")
        self.assertEqual(opened.status_code, 200, opened.text)
        database_id = opened.json()["database_id"]

        updated = self.client.put(
            "/api/person",
            json={"full_name": "Ada Persisted", "position_title": "Researcher"},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        library = self.client.get("/api/account/databases")
        self.assertEqual(library.status_code, 200, library.text)
        self.assertEqual(library.json()["databases"][0]["id"], database_id)
        self.assertGreater(library.json()["databases"][0]["revision"], 1)

        closed = self.client.delete("/gateway/workspace")
        self.assertEqual(closed.status_code, 200, closed.text)
        signed_out = self.client.post("/api/account/logout")
        self.assertEqual(signed_out.status_code, 200, signed_out.text)
        signed_in = self.client.post(
            "/api/account/login",
            json={"email": "tester@example.org", "password": "correct-horse-battery-staple"},
        )
        self.assertEqual(signed_in.status_code, 200, signed_in.text)

        reopened = self.client.post(f"/gateway/databases/{database_id}/open")
        self.assertEqual(reopened.status_code, 200, reopened.text)
        entered = self.client.get("/gateway/workspace/enter", follow_redirects=False)
        self.assertEqual(entered.status_code, 303, entered.text)
        self.assertEqual(entered.headers["location"], "/")
        self.assertEqual(entered.headers["cache-control"], "no-store")
        person = self.client.get("/api/person")
        self.assertEqual(person.status_code, 200, person.text)
        self.assertEqual(person.json()["full_name"], "Ada Persisted")

        downloaded = self.client.get(f"/api/account/databases/{database_id}/download")
        self.assertEqual(downloaded.status_code, 200, downloaded.text)
        self.assertTrue(downloaded.content.startswith(b"SQLite format 3"))

    def test_saved_database_is_not_accessible_to_another_account(self):
        self.create_account(email="owner@example.org")
        opened = self.client.post("/gateway/workspace/new")
        self.assertEqual(opened.status_code, 200, opened.text)
        database_id = opened.json()["database_id"]
        self.assertEqual(self.client.delete("/gateway/workspace").status_code, 200)

        other = TestClient(app)
        code = create_invitation("Other account", max_uses=1, expires_days=1)
        self.assertEqual(other.post("/api/invitations/redeem", json={"code": code}).status_code, 200)
        registered = other.post(
            "/api/account/register",
            json={
                "email": "other@example.org",
                "password": "another-correct-horse-password",
                "display_name": "Other Researcher",
            },
        )
        self.assertEqual(registered.status_code, 200, registered.text)
        self.verify_email("other@example.org")
        self.assertEqual(other.post(f"/gateway/databases/{database_id}/open").status_code, 404)
        self.assertEqual(other.get(f"/api/account/databases/{database_id}/download").status_code, 404)
        other.close()

    def test_password_is_hashed_and_failed_login_is_rate_audited(self):
        self.create_account()
        with sqlite3.connect(self.db_path) as con:
            password_hash = con.execute(
                "SELECT password_hash FROM members WHERE email='tester@example.org'"
            ).fetchone()[0]
        self.assertTrue(password_hash.startswith("scrypt$"))
        self.assertNotIn("correct-horse", password_hash)

        self.assertEqual(self.client.post("/api/account/logout").status_code, 200)
        rejected = self.client.post(
            "/api/account/login",
            json={"email": "tester@example.org", "password": "wrong-password"},
        )
        self.assertEqual(rejected.status_code, 401)
        with sqlite3.connect(self.db_path) as con:
            failures = con.execute(
                "SELECT COUNT(*) FROM authentication_attempts WHERE succeeded=0"
            ).fetchone()[0]
        self.assertEqual(failures, 1)

    def test_login_page_reads_autofilled_credentials_before_disabling_inputs(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200, page.text)
        self.assertEqual(page.headers["cache-control"], "no-store")
        self.assertEqual(page.headers["vary"], "Cookie")
        self.assertIn('autocomplete="username webauthn"', page.text)
        self.assertIn("20260801-passkey-settings", page.text)
        self.assertIn("Stop recounting your career from scratch, over and over again.", page.text)
        self.assertIn('class="vitamine-bottle"', page.text)
        self.assertIn("Alex Researcher", page.text)
        self.assertIn("Northbridge University", page.text)
        self.assertIn("Selected research", page.text)
        self.assertIn('class="library-workspace"', page.text)
        self.assertIn('class="cv-library-panel"', page.text)
        self.assertIn('href="#what-is-vitamine">What is VitaMine?</a>', page.text)
        self.assertEqual(page.text.count('class="feature-chapter'), 7)
        self.assertIn('id="typedExportPrompt"', page.text)
        self.assertIn('class="feature-visual map-visual"', page.text)
        self.assertIn("Map geometry: Natural Earth", page.text)
        self.assertIn("A website you don’t have to maintain.", page.text)
        self.assertIn("Managing your CV should be simple.", page.text)
        self.assertIn("Let’s do it!", page.text)
        self.assertIn('class="researcher-vector"', page.text)
        self.assertIn("For your R01, we need a biosketch", page.text)
        self.assertIn('class="vitamine-bottle"', page.text)
        self.assertIn("Drop your existing CV here", page.text)
        self.assertIn("portable SQLite database", page.text)
        self.assertIn("18 research grants", page.text)
        self.assertIn("Skills and languages", page.text)
        self.assertIn('class="sync-lines"', page.text)
        self.assertIn('id="citationTicker"', page.text)
        script = self.client.get("/assets/account.js")
        self.assertIn("Download data (.vitamine)", script.text)
        self.assertIn("has-profile-action", script.text)
        self.assertIn("initializeFeatureStory", script.text)
        self.assertIn("prefers-reduced-motion: reduce", script.text)
        self.assertIn("scrollProgress", script.text)
        self.assertIn("window.scrollTo({ top: 0", script.text)
        self.assertEqual(script.status_code, 200, script.text)
        handler = script.text.split(
            'elements.loginForm.addEventListener("submit"',
            1,
        )[1].split(
            'elements.logoutButton.addEventListener',
            1,
        )[0]
        self.assertNotIn("new FormData(elements.loginForm)", handler)
        self.assertLess(handler.index('namedItem("email")'), handler.index("setBusy(elements.loginForm, true)"))
        self.assertIn("JSON.stringify({ email, password })", handler)
        self.assertIn('window.location.assign("/gateway/workspace/enter")', script.text)

    def test_existing_invited_workspace_is_promoted_during_registration(self):
        _, token = self.redeem()
        member_id = self.client.get("/api/session").json()["member_id"]
        session_dir = Path(self.directory.name) / "sessions" / "legacy-workspace"
        session_dir.mkdir(parents=True)
        db_path = session_dir / "workspace.vitamine"
        output_path = session_dir / "output"
        output_path.mkdir()
        create_blank_workspace_database(db_path)
        with sqlite3.connect(db_path) as con:
            con.execute("UPDATE person SET full_name='Legacy Workspace' WHERE id=1")
        workspace_token = register_workspace(
            member_id=member_id,
            database_id=None,
            workspace_id="legacy-workspace",
            db_path=db_path,
            output_path=output_path,
            original_filename="Legacy CV.vitamine",
        )
        self.client.cookies.set("vitamine_workspace", workspace_token)

        registered = self.register(email="legacy@example.org", token=token)
        database_id = registered.json()["promoted_database_id"]
        self.assertTrue(database_id)
        library = self.client.get("/api/account/databases").json()
        self.assertEqual(len(library["databases"]), 1)
        self.assertEqual(library["databases"][0]["id"], database_id)
        person = self.client.get("/api/person")
        self.assertEqual(person.status_code, 200, person.text)
        self.assertEqual(person.json()["full_name"], "Legacy Workspace")
        self.assertEqual(self.client.delete("/gateway/workspace").status_code, 200)

    def test_publish_read_update_and_delete_profile(self):
        token = self.create_account()
        headers = {"Authorization": f"Bearer {token}"}
        snapshot = {
            "display_name": "Ada Example",
            "headline": "Computational neuroscientist",
            "biography": "Builds transparent scientific tools.",
            "publications": [{"title": "A public paper"}],
        }
        published = self.client.put("/api/profiles/ada-example", headers=headers, json=snapshot)
        self.assertEqual(published.status_code, 200, published.text)

        public_json = self.client.get("/api/public/ada-example")
        self.assertEqual(public_json.status_code, 200)
        self.assertEqual(public_json.json()["display_name"], "Ada Example")
        self.assertEqual(public_json.headers["access-control-allow-origin"], "*")

        page = self.client.get("/ada-example")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Ada Example", page.text)
        self.assertNotIn("device_token", page.text)

        deleted = self.client.delete("/api/profiles/ada-example", headers=headers)
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.client.get("/ada-example").status_code, 404)

    def test_cv_backed_public_profile_supports_blocks_embeds_and_automatic_refresh(self):
        self.create_account()
        opened = self.client.post("/gateway/workspace/new")
        self.assertEqual(opened.status_code, 200, opened.text)
        database_id = opened.json()["database_id"]
        updated = self.client.put(
            "/api/person",
            json={
                "display_name": "Ada Profile",
                "full_name": "Ada Private Name",
                "position_title": "Professor",
                "home_address": "Never publish this address",
                "work_email": "private@example.org",
                "own_institution_name": "Example University",
                "own_institution_country": "Germany",
                "own_institution_country_code": "DE",
                "own_institution_latitude": 50.94,
                "own_institution_longitude": 6.96,
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)

        published = self.client.post(
            "/api/profile/publish",
            json={"slug": "ada-profile", "database_id": database_id},
        )
        self.assertEqual(published.status_code, 200, published.text)

        public = self.client.get("/api/public/ada-profile")
        self.assertEqual(public.status_code, 200, public.text)
        self.assertEqual(public.json()["schema_version"], 3)
        self.assertNotIn("source_database_id", public.json())
        self.assertNotIn("Never publish this address", public.text)
        self.assertNotIn("private@example.org", public.text)
        self.assertEqual(
            [block["key"] for block in public.json()["blocks"]],
            ["bio", "metrics", "publications", "collaborators"],
        )
        self.assertEqual(public.json()["profile_title"], "Ada Profile")
        self.assertNotIn("_profile_title_custom", public.json())
        self.assertNotIn("portrait_url", public.json())

        uploaded_portrait = self.client.put(
            "/api/person/portrait",
            files={"file": ("ada.png", PNG_1X1, "image/png")},
        )
        self.assertEqual(uploaded_portrait.status_code, 200, uploaded_portrait.text)
        public_with_portrait = self.client.get("/api/public/ada-profile").json()
        self.assertIn("/api/public/ada-profile/portrait?v=", public_with_portrait["portrait_url"])
        self.assertNotIn("_portrait_blob", public_with_portrait)
        public_portrait = self.client.get(public_with_portrait["portrait_url"])
        self.assertEqual(public_portrait.status_code, 200, public_portrait.text)
        self.assertEqual(public_portrait.headers["content-type"], "image/png")
        self.assertTrue(public_portrait.content.startswith(b"\x89PNG\r\n\x1a\n"))

        managed = self.client.get("/api/profile/manage")
        self.assertEqual(managed.status_code, 200, managed.text)
        self.assertEqual(managed.json()["profile"]["source_database_id"], database_id)
        library = self.client.get("/api/account/databases")
        self.assertEqual(library.json()["profile"]["slug"], "ada-profile")

        blocks = [
            {"key": "bio", "visible": True},
            {"key": "publications", "visible": True},
            {"key": "collaborators", "visible": True},
            {"key": "metrics", "visible": False},
        ]
        saved_layout = self.client.put("/api/profile/blocks", json={"blocks": blocks})
        self.assertEqual(saved_layout.status_code, 200, saved_layout.text)
        self.assertEqual(saved_layout.json()["blocks"], blocks)
        self.assertEqual(
            self.client.get("/embed/ada-profile?block=metrics&theme=dark").status_code,
            404,
        )
        saved_header = self.client.put(
            "/api/profile/header",
            json={"profile_title": "Ada Profile, MD, PhD"},
        )
        self.assertEqual(saved_header.status_code, 200, saved_header.text)
        self.assertEqual(saved_header.json()["profile_title"], "Ada Profile, MD, PhD")
        publication_embed = self.client.get(
            "/embed/ada-profile?block=publications&theme=simple"
        )
        self.assertEqual(publication_embed.status_code, 200, publication_embed.text)
        self.assertIn('data-profile-theme="simple"', publication_embed.text)
        self.assertIn('data-profile-block="publications"', publication_embed.text)

        refreshed_person = self.client.put(
            "/api/person",
            json={"display_name": "Ada Refreshed", "position_title": "Professor"},
        )
        self.assertEqual(refreshed_person.status_code, 200, refreshed_person.text)
        refreshed = self.client.get("/api/public/ada-profile")
        self.assertEqual(refreshed.json()["display_name"], "Ada Refreshed")
        self.assertEqual(refreshed.json()["profile_title"], "Ada Profile, MD, PhD")
        self.assertNotIn("_profile_title_custom", refreshed.json())
        self.assertIn("/api/public/ada-profile/portrait?v=", refreshed.json()["portrait_url"])
        self.assertEqual(refreshed.json()["blocks"], blocks)
        profile_page = self.client.get("/ada-profile")
        self.assertIn("Ada Profile, MD, PhD", profile_page.text)
        self.assertNotIn('class="profile-brand"', profile_page.text)
        self.assertIn("Made with VitaMine", profile_page.text)
        self.assertIn("20260801-citation-map", profile_page.text)

        removed_portrait = self.client.delete("/api/person/portrait")
        self.assertEqual(removed_portrait.status_code, 200, removed_portrait.text)
        without_portrait = self.client.get("/api/public/ada-profile").json()
        self.assertNotIn("portrait_url", without_portrait)
        self.assertEqual(
            self.client.get("/api/public/ada-profile/portrait").status_code,
            404,
        )

        blocked_delete = self.client.delete(f"/api/account/databases/{database_id}")
        self.assertEqual(blocked_delete.status_code, 409, blocked_delete.text)
        self.assertIn("public profile", blocked_delete.json()["detail"])

    def test_public_profile_assets_include_osm_map_and_publication_pagination(self):
        script = self.client.get("/assets/public-profile.js")
        self.assertEqual(script.status_code, 200, script.text)
        self.assertIn("https://tile.openstreetmap.org/", script.text)
        self.assertIn("const PUBLICATION_PAGE_SIZE = 25", script.text)
        self.assertIn('data-publication-page="previous"', script.text)
        self.assertIn('data-publication-page="next"', script.text)
        self.assertIn("function doiUrl(value)", script.text)
        self.assertIn('class="publication-doi"', script.text)
        self.assertIn('requestJson("/api/profile/header"', script.text)
        self.assertIn("function publicProfileTitle(profile)", script.text)
        self.assertIn('class="profile-portrait-frame"', script.text)
        self.assertIn('class="profile-portrait"', script.text)
        self.assertIn('metrics: "Citations"', script.text)
        self.assertIn("--citation-year-count", script.text)
        self.assertNotIn("Research impact", script.text)
        self.assertNotIn("OpenAlex citation data", script.text)
        self.assertNotIn("publications have OpenAlex", script.text)
        stylesheet = self.client.get("/assets/public-profile.css")
        self.assertEqual(stylesheet.status_code, 200, stylesheet.text)
        self.assertIn(".public-osm-credit", stylesheet.text)
        self.assertIn(".publication-pagination", stylesheet.text)
        self.assertIn(".profile-portrait-frame", stylesheet.text)
        self.assertIn("float: right", stylesheet.text)
        self.assertIn("width: clamp(230px, 28%, 310px)", stylesheet.text)
        self.assertIn(
            ".bio-content.has-portrait .profile-biography { max-width: none; }",
            stylesheet.text,
        )
        self.assertIn("--page: #f4f6f5", stylesheet.text)
        self.assertIn("border-bottom: 1px solid var(--line)", stylesheet.text)
        self.assertIn("repeat(var(--citation-year-count, 1), minmax(0, 1fr))", stylesheet.text)

    def test_slug_cannot_be_taken_by_another_member(self):
        _, first_token = self.redeem()
        _, second_token = self.redeem()
        self.register(email="first@example.org", token=first_token)
        self.register(email="second@example.org", token=second_token)
        snapshot = {"display_name": "First"}
        first = self.client.put(
            "/api/profiles/researcher",
            headers={"Authorization": f"Bearer {first_token}"},
            json=snapshot,
        )
        self.assertEqual(first.status_code, 200)
        second = self.client.put(
            "/api/profiles/researcher",
            headers={"Authorization": f"Bearer {second_token}"},
            json={"display_name": "Second"},
        )
        self.assertEqual(second.status_code, 409)

    def test_profile_output_escapes_html(self):
        token = self.create_account()
        response = self.client.put(
            "/api/profiles/safe-profile",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "display_name": "<script>alert(1)</script>",
                "biography": "<img src=x onerror=alert(1)>",
            },
        )
        self.assertEqual(response.status_code, 200)
        page = self.client.get("/safe-profile")
        self.assertNotIn("<script>", page.text)
        self.assertNotIn("<img src=x onerror=alert(1)>", page.text)
        self.assertIn("&lt;script&gt;", page.text)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", page.text)

    def test_reserved_slug_is_rejected(self):
        token = self.create_account()
        response = self.client.put(
            "/api/profiles/admin",
            headers={"Authorization": f"Bearer {token}"},
            json={"display_name": "Admin"},
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
