"""Unit tests for services/profiles.py."""

import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from services.profiles import ProfileService


class TestProfileService(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.home_dir = Path(self.temp_dir) / "home"
        self.home_dir.mkdir()
        self.backup_dir = Path(self.temp_dir) / "backups"
        self.service = ProfileService(
            home_dir=self.home_dir,
            backup_dir=self.backup_dir,
            max_backups=3,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_get_active_email_empty_when_missing(self):
        self.assertEqual(self.service.get_active_email(), "")

    def test_get_active_email_extracts_correctly(self):
        active_file = self.home_dir / ".claude.json"
        active_file.write_text(
            json.dumps({"oauthAccount": {"emailAddress": "dev@example.com"}}),
            encoding="utf-8",
        )
        self.assertEqual(self.service.get_active_email(), "dev@example.com")

    def test_switch_profile_missing_source_raises(self):
        with self.assertRaises(FileNotFoundError):
            self.service.switch_profile(self.home_dir / "nonexistent.json")

    def test_switch_profile_atomic_replace(self):
        source = self.home_dir / ".claude-1.json"
        source.write_text(json.dumps({"profile": "one"}), encoding="utf-8")

        dest = self.home_dir / ".claude.json"
        dest.write_text(json.dumps({"profile": "old"}), encoding="utf-8")

        backup = self.service.switch_profile(source)
        self.assertIsNotNone(backup)
        self.assertTrue(backup.exists())

        # Check dest now contains source content
        content = json.loads(dest.read_text(encoding="utf-8"))
        self.assertEqual(content.get("profile"), "one")

    def test_backup_rotation_limits_count(self):
        source = self.home_dir / ".claude-source.json"
        source.write_text("{}", encoding="utf-8")

        dest = self.home_dir / ".claude.json"

        # Create 5 backups (max is set to 3 in setUp)
        for i in range(5):
            dest.write_text(f'{{"iteration": {i}}}', encoding="utf-8")
            self.service.switch_profile(source)
            time.sleep(0.01)

        backups = list(self.backup_dir.glob("claude-active-*.json"))
        self.assertEqual(len(backups), 3)

    def test_switch_account_number(self):
        source2 = self.home_dir / ".claude-2.json"
        source2.write_text('{"account": 2}', encoding="utf-8")

        self.assertTrue(self.service.switch_account_number(2))
        self.assertFalse(self.service.switch_account_number(99))

    def test_switch_account_syncs_outgoing_and_incoming_credentials(self):
        # Setup: Account 1 is currently active with unique email
        active_file = self.home_dir / ".claude.json"
        active_file.write_text(
            json.dumps({"oauthAccount": {"emailAddress": "acc1@test.com", "accountUuid": "uuid-1"}, "latest_turn": 42}),
            encoding="utf-8",
        )
        claude_dir = self.home_dir / ".claude"
        claude_dir.mkdir(parents=True)
        cred_active = claude_dir / ".credentials.json"
        cred_active.write_text(json.dumps({"claudeAiOauth": {"accessToken": "token-1"}}), encoding="utf-8")

        # Account 1 profile exists
        p1 = self.home_dir / ".claude-1.json"
        p1.write_text(json.dumps({"oauthAccount": {"emailAddress": "acc1@test.com", "accountUuid": "uuid-1"}}), encoding="utf-8")

        # Target Account 2 exists with saved credentials
        p2 = self.home_dir / ".claude-2.json"
        p2.write_text(json.dumps({"oauthAccount": {"emailAddress": "acc2@test.com", "accountUuid": "uuid-2"}}), encoding="utf-8")
        cred2 = claude_dir / ".credentials-2.json"
        cred2.write_text(json.dumps({"claudeAiOauth": {"accessToken": "token-2"}}), encoding="utf-8")

        # Switch to Account 2
        success = self.service.switch_account_number(2)
        self.assertTrue(success)

        # Verify Account 1 profile was updated with active state (latest_turn: 42)
        p1_updated = json.loads(p1.read_text(encoding="utf-8"))
        self.assertEqual(p1_updated.get("latest_turn"), 42)

        # Verify Account 1 credentials were saved to .credentials-1.json
        cred1_saved = json.loads((claude_dir / ".credentials-1.json").read_text(encoding="utf-8"))
        self.assertEqual(cred1_saved.get("claudeAiOauth", {}).get("accessToken"), "token-1")

        # Verify active .credentials.json now contains Account 2's token
        active_cred_now = json.loads(cred_active.read_text(encoding="utf-8"))
        self.assertEqual(active_cred_now.get("claudeAiOauth", {}).get("accessToken"), "token-2")


if __name__ == "__main__":
    unittest.main()
