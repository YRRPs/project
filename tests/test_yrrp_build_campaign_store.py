from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from yrrp_build_campaign.model import Campaign
from yrrp_build_campaign.store import CampaignStore


class CampaignStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "campaigns"
        self.store = CampaignStore(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_save_load_and_render_are_deterministic(self) -> None:
        campaign = Campaign.new("october-batch")
        self.store.save(campaign)
        first = self.store.markdown_path("october-batch").read_bytes()

        loaded = self.store.load("october-batch")
        self.store.save(loaded)

        self.assertEqual(
            first,
            self.store.markdown_path("october-batch").read_bytes(),
        )

    def test_runtime_permissions_are_private(self) -> None:
        self.store.save(Campaign.new("october-batch"))

        self.assertEqual(0o700, self.root.stat().st_mode & 0o777)
        self.assertEqual(
            0o600,
            self.store.json_path("october-batch").stat().st_mode & 0o777,
        )
        self.assertEqual(
            0o600,
            self.store.markdown_path("october-batch").stat().st_mode & 0o777,
        )

    def test_paths_reject_traversal_and_symlink_escape(self) -> None:
        with self.assertRaises(ValueError):
            self.store.json_path("../outside")

        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        (self.root / "escape.json").symlink_to(outside / "state.json")

        with self.assertRaises(ValueError):
            self.store.json_path("escape")

    def test_evidence_rejects_sensitive_content(self) -> None:
        self.store.save(Campaign.new("october-batch"))

        with self.assertRaises(ValueError):
            self.store.write_evidence(
                "october-batch",
                "preflight.txt",
                b"Authorization: Bearer secret-token\n",
            )

    def test_evidence_rejects_api_keys_and_environment_dumps(self) -> None:
        self.store.save(Campaign.new("october-batch"))

        for content in (b"API_KEY=secret\n", b"TOKEN=secret\n", b"-----BEGIN PRIVATE KEY-----\n"):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    self.store.write_evidence(
                        "october-batch",
                        "preflight.txt",
                        content,
                    )

    def test_evidence_name_rejects_nested_path(self) -> None:
        self.store.save(Campaign.new("october-batch"))

        with self.assertRaises(ValueError):
            self.store.write_evidence(
                "october-batch",
                "../outside.txt",
                b"safe\n",
            )

    def test_manifest_evidence_must_belong_to_campaign_and_match_digest(self) -> None:
        self.store.save(Campaign.new("october-batch"))
        relative = self.store.write_evidence(
            "october-batch",
            "manifest.xml",
            b"<manifest/>\n",
        )

        self.store.verify_evidence(
            "october-batch",
            relative,
            "5cd294a1b20c3f5f5cb8fd12ef19b535879a64a89a76560970041db03855b057",
        )
        with self.assertRaises(ValueError):
            self.store.verify_evidence("october-batch", relative, "0" * 64)
        with self.assertRaises(ValueError):
            self.store.verify_evidence(
                "other-campaign",
                relative,
                "5cd294a1b20c3f5f5cb8fd12ef19b535879a64a89a76560970041db03855b057",
            )

    def test_evidence_is_private(self) -> None:
        self.store.save(Campaign.new("october-batch"))

        relative = self.store.write_evidence(
            "october-batch",
            "preflight.txt",
            b"all checks passed\n",
        )
        path = self.root / relative

        self.assertEqual(0o600, path.stat().st_mode & 0o777)
        self.assertEqual(b"all checks passed\n", path.read_bytes())


if __name__ == "__main__":
    unittest.main()
