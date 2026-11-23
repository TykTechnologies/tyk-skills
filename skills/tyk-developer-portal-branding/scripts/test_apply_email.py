"""Tests for the email section of apply_content.py: the SMTP password the form asks for again.

Standard library only, no network: the admin session is a small in-memory fake that behaves
like the General settings form (the stored password is never rendered back).
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

import apply_content  # noqa: E402

FORM = """
<form action="/admin/general" method="POST">
  <input type="text" name="QorResource.EmailSMTPHost" value="{host}">
  <input type="text" name="QorResource.EmailSMTPPort" value="{port}">
  <input type="text" name="QorResource.EmailSMTPUser" value="{user}">
  <input type="password" name="QorResource.EmailSMTPPass" value="">
  <input type="text" name="QorResource.EmailResetSubject" value="{subject}">
  <textarea name="QorResource.RobotsTxtContent">
User-agent: *
Disallow: /</textarea>
</form>
"""


class FakeGeneralForm:
    """Refuses a new host, port or user without the password while one is stored."""

    def __init__(self):
        self.values = {"host": "mail.example.com", "port": "25", "user": "portal", "password": "stored",
                       "subject": ""}
        self.posted: list[dict] = []

    def get(self, path):
        return 200, FORM.format(**self.values), {}

    def post_multipart(self, path, pairs):
        sent = dict(pairs)
        self.posted.append(sent)
        new = {"host": sent["QorResource.EmailSMTPHost"], "port": sent["QorResource.EmailSMTPPort"],
               "user": sent["QorResource.EmailSMTPUser"]}
        changed = any(new[k] != self.values[k] for k in new)
        if changed and self.values["password"] and not sent.get("QorResource.EmailSMTPPass"):
            return 422, "Re-enter the SMTP password after changing the host, port, or user.", {}
        self.values.update(new)
        self.values["subject"] = sent["QorResource.EmailResetSubject"]
        if sent.get("QorResource.EmailSMTPPass"):
            self.values["password"] = sent["QorResource.EmailSMTPPass"]
        return 302, "", {}


class EmailTests(unittest.TestCase):
    def run_email(self, wanted, password=None):
        form = FakeGeneralForm()
        run = apply_content.Run(api=mock.Mock(), base=Path("."), dry_run=False)
        run._session = form
        env = {k: v for k, v in os.environ.items() if k != "PORTAL_SMTP_PASSWORD"}
        if password:
            env["PORTAL_SMTP_PASSWORD"] = password
        with mock.patch.dict(os.environ, env, clear=True):
            apply_content.apply_email(run, wanted)
        return run, form

    def test_port_change_without_password_explains_why(self):
        run, form = self.run_email({"smtp_port": "2525"})
        self.assertEqual(form.values["port"], "25")
        self.assertTrue(any("PORTAL_SMTP_PASSWORD" in error for error in run.errors), run.errors)

    def test_port_change_with_password_persists(self):
        run, form = self.run_email({"smtp_port": "2525"}, password="new-secret")
        self.assertEqual(run.errors, [])
        self.assertEqual(form.values["port"], "2525")

    def test_subject_change_sends_password_field_empty_and_keeps_stored_one(self):
        run, form = self.run_email({"subjects": {"reset_password": "Reset your password"}})
        self.assertEqual(run.errors, [])
        self.assertEqual(form.posted[0]["QorResource.EmailSMTPPass"], "")
        self.assertEqual(form.values["subject"], "Reset your password")
        self.assertEqual(form.values["password"], "stored")

if __name__ == "__main__":
    unittest.main()
