#!/usr/bin/env python3
"""Complete-bundle and corruption coverage for companion-aware M4R1."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(TESTS))

from m4r1_bundle import validate_bundle  # noqa: E402
from m4r1_records import ValidationError  # noqa: E402
from m4r1_test_bundle import build_bundle, write_json  # noqa: E402


class M4R1BundleIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="m4r1-bundle-")
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_complete_absent_companion_bundle_validates(self) -> None:
        build_bundle(self.root)
        validated = validate_bundle(self.root)
        self.assertEqual(validated["manifest"]["file_count"], 579)

    def test_present_companions_are_retained_and_validated(self) -> None:
        present = {
            ("M4-V20-U-01", "creator"),
            ("M4-V20-U-01", "reopen"),
        }
        build_bundle(self.root, present)
        validated = validate_bundle(self.root)
        self.assertEqual(validated["manifest"]["file_count"], 581)
        roles = [row["role"] for row in validated["manifest"]["files"]]
        self.assertEqual(roles.count("companion"), 2)

    def test_companion_corruption_is_rejected(self) -> None:
        present = {("M4-V20-U-01", "creator")}
        build_bundle(self.root, present)
        companion = self.root / "evidence/samples/M4-V20-U-01/creator.ldb"
        companion.write_bytes(b"corrupt")
        with self.assertRaises(ValidationError):
            validate_bundle(self.root)

    def test_companion_cannot_be_retyped_as_prefix(self) -> None:
        present = {("M4-V20-U-01", "creator")}
        build_bundle(self.root, present)
        manifest_path = self.root / "bundle-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entry = next(row for row in manifest["files"] if row["role"] == "companion")
        entry["role"] = "prefix"
        write_json(manifest_path, manifest)
        with self.assertRaises(ValidationError):
            validate_bundle(self.root)


if __name__ == "__main__":
    unittest.main()
