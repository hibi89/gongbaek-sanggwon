from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_snapshot_db import make_database


ROOT = Path(__file__).resolve().parents[1]
TAXONOMY_PATH = ROOT / "설계" / "03_업종분류표.json"


class SnapshotIngestionGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.source = self.root / "snapshot.zip"
        self.validation = self.root / "validation.json"
        self.target = self.root / "stores.sqlite"
        self.source.write_bytes(b"tampered snapshot bytes")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write_validation(self, *, status: str, checksum: str) -> None:
        report = {
            "status": status,
            "zip_crc_check": "passed",
            "snapshot_manifest": {"sha256": checksum},
        }
        self.validation.write_text(json.dumps(report), encoding="utf-8")

    def test_checksum_mismatch_fails_before_creating_database(self) -> None:
        self.write_validation(status="validated", checksum="0" * 64)

        with self.assertRaisesRegex(ValueError, "checksum differs"):
            make_database(self.source, self.validation, TAXONOMY_PATH, self.target, replace=False)

        self.assertFalse(self.target.exists())
        self.assertFalse(self.target.with_suffix(".sqlite.building").exists())

    def test_unvalidated_snapshot_cannot_be_promoted(self) -> None:
        self.write_validation(status="incomplete", checksum="0" * 64)

        with self.assertRaisesRegex(ValueError, "has not passed validation"):
            make_database(self.source, self.validation, TAXONOMY_PATH, self.target, replace=False)

        self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main()
