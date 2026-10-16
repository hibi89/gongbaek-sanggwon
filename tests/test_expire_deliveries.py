from __future__ import annotations

import csv
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from scripts.expire_deliveries import process_expirations


FIELDS = ["분석번호", "n8n_실행ID", "고객_전달일", "삭제_예정일", "삭제_확인일", "처리상태"]


class ExpireDeliveriesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ledger = self.root / "ledger.csv"
        self.db = self.root / "n8n.sqlite"
        self.delivery_root = self.root / "deliveries"
        self.order_dir = self.delivery_root / "GAP-272"
        self.delivery_root.mkdir()
        self.order_dir.mkdir()
        (self.order_dir / "report.pdf").write_bytes(b"test customer file")
        self.write_ledger()
        connection = sqlite3.connect(self.db)
        try:
            connection.execute("CREATE TABLE execution_entity (id TEXT PRIMARY KEY, status TEXT)")
            connection.commit()
        finally:
            connection.close()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_ledger(
        self,
        *,
        analysis_id: str = "GAP-272",
        execution_id: str = "272",
        delivered: str = "2026-08-01",
        expires: str = "2026-08-31",
        confirmed: str = "",
        status: str = "보관중",
    ) -> None:
        with self.ledger.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerow(dict(zip(FIELDS, (analysis_id, execution_id, delivered, expires, confirmed, status))))

    def add_execution(self, execution_id: str = "272") -> None:
        connection = sqlite3.connect(self.db)
        try:
            connection.execute("INSERT INTO execution_entity(id, status) VALUES (?, ?)", (execution_id, "success"))
            connection.commit()
        finally:
            connection.close()

    def read_row(self) -> dict[str, str]:
        with self.ledger.open("r", encoding="utf-8", newline="") as stream:
            return next(csv.DictReader(stream))

    def run_cleanup(self, *, apply: bool) -> list[dict]:
        return process_expirations(
            ledger_path=self.ledger,
            n8n_db=self.db,
            delivery_root=self.delivery_root,
            today=date(2026, 9, 1),
            apply=apply,
        )

    def test_dry_run_keeps_files_and_ledger_unchanged(self) -> None:
        result = self.run_cleanup(apply=False)
        self.assertEqual(result[0]["status"], "would_delete_files")
        self.assertTrue(self.order_dir.exists())
        self.assertEqual(self.read_row()["처리상태"], "보관중")

    def test_expiry_date_itself_is_eligible(self) -> None:
        result = process_expirations(
            ledger_path=self.ledger,
            n8n_db=self.db,
            delivery_root=self.delivery_root,
            today=date(2026, 8, 31),
            apply=False,
        )
        self.assertEqual(result[0]["status"], "would_delete_files")
        self.assertTrue(self.order_dir.exists())

    def test_execution_still_present_blocks_file_deletion(self) -> None:
        self.add_execution()
        result = self.run_cleanup(apply=True)
        self.assertEqual(result[0]["status"], "delete_n8n_execution_first")
        self.assertTrue(self.order_dir.exists())
        self.assertEqual(self.read_row()["처리상태"], "보관중")

    def test_apply_deletes_due_folder_only_after_execution_is_absent(self) -> None:
        result = self.run_cleanup(apply=True)
        row = self.read_row()
        self.assertEqual(result[0]["status"], "삭제완료")
        self.assertFalse(self.order_dir.exists())
        self.assertEqual(row["삭제_확인일"], "2026-09-01")
        self.assertEqual(row["처리상태"], "삭제완료")

    def test_future_expiry_is_not_deleted(self) -> None:
        self.write_ledger(delivered="2026-09-01", expires="2026-10-01")
        result = self.run_cleanup(apply=True)
        self.assertEqual(result[0]["status"], "not_due")
        self.assertTrue(self.order_dir.exists())
        self.assertEqual(self.read_row()["처리상태"], "보관중")

    def test_unrecognized_ledger_status_is_reported_and_not_deleted(self) -> None:
        self.write_ledger(status="")
        result = self.run_cleanup(apply=True)
        self.assertEqual(result[0]["status"], "manual_review_unknown_status")
        self.assertTrue(self.order_dir.exists())

    def test_invalid_expiry_date_is_rejected(self) -> None:
        self.write_ledger(expires="2026-08-30")
        with self.assertRaisesRegex(ValueError, "exactly 30 days"):
            self.run_cleanup(apply=True)
        self.assertTrue(self.order_dir.exists())

    def test_path_traversal_id_is_rejected(self) -> None:
        self.write_ledger(analysis_id="GAP-../outside")
        with self.assertRaisesRegex(ValueError, "invalid analysis ID"):
            self.run_cleanup(apply=True)
        self.assertTrue(self.order_dir.exists())

    def test_duplicate_execution_id_blocks_all_file_deletion(self) -> None:
        with self.ledger.open("a", encoding="utf-8", newline="") as stream:
            csv.writer(stream).writerow(["GAP-273", "272", "2026-08-01", "2026-08-31", "", "보관중"])
        (self.delivery_root / "GAP-273").mkdir()
        with self.assertRaisesRegex(ValueError, "execution ID appears more than once"):
            self.run_cleanup(apply=True)
        self.assertTrue(self.order_dir.exists())
        self.assertTrue((self.delivery_root / "GAP-273").exists())

    def test_missing_n8n_database_prevents_any_delete(self) -> None:
        with self.assertRaisesRegex(FileNotFoundError, "n8n database not found"):
            process_expirations(
                ledger_path=self.ledger,
                n8n_db=self.root / "missing.sqlite",
                delivery_root=self.delivery_root,
                today=date(2026, 9, 1),
                apply=True,
            )
        self.assertTrue(self.order_dir.exists())


if __name__ == "__main__":
    unittest.main()
