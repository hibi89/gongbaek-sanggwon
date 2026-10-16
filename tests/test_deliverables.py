from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_deliverables


class DeliverableValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = json.loads((ROOT / "data" / "results" / "sample_workflow_address_taxonomy_v3_20260930.json").read_text(encoding="utf-8"))
        cls.taxonomy = json.loads((ROOT / "설계" / "03_업종분류표.json").read_text(encoding="utf-8"))

    def test_current_masked_e2e_result_passes_export_validation(self) -> None:
        result = build_deliverables.validate_result(self.result, self.taxonomy)
        self.assertEqual(len(result["categories"]), 247)
        self.assertEqual(len(self.result["overall_top20"]), 20)
        self.assertEqual(len(self.result["a_top10"]), 10)
        self.assertEqual(result["total_5km"], 58488)
        self.assertEqual(self.result["source"]["taxonomy_hash"], self.taxonomy["taxonomy_hash"])

    def test_export_rejected_when_publication_gate_fails(self) -> None:
        result = copy.deepcopy(self.result)
        result["publication_gate"] = "blocked"
        with self.assertRaisesRegex(ValueError, "발행 게이트"):
            build_deliverables.validate_result(result, self.taxonomy)

    def test_export_rejected_when_category_count_is_incomplete(self) -> None:
        result = copy.deepcopy(self.result)
        result["all_category_counts"].pop()
        with self.assertRaisesRegex(ValueError, "247개"):
            build_deliverables.validate_result(result, self.taxonomy)

        stale_result = copy.deepcopy(self.result)
        stale_result["source"]["taxonomy_hash"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "taxonomy 내용 해시"):
            build_deliverables.validate_result(stale_result, self.taxonomy)

        changed_taxonomy = copy.deepcopy(self.taxonomy)
        changed_taxonomy["categories"][0]["name"] += " 변경"
        with self.assertRaisesRegex(ValueError, "taxonomy content hash"):
            build_deliverables.validate_result(self.result, changed_taxonomy)

    def test_export_rejected_when_cumulative_counts_reverse(self) -> None:
        result = copy.deepcopy(self.result)
        result["all_category_counts"][0]["counts"]["2km"] = -1
        with self.assertRaisesRegex(ValueError, "역전|음수"):
            build_deliverables.validate_result(result, self.taxonomy)

        for field, value, message in (("name", "잘못된 업종명", "업종명"), ("abc_tag", "C", "ABC 태그")):
            result = copy.deepcopy(self.result)
            result["all_category_counts"][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, message):
                build_deliverables.validate_result(result, self.taxonomy)

        result = copy.deepcopy(self.result)
        result["overall_top20"][0]["name"] = "잘못된 순위 업종명"
        with self.assertRaisesRegex(ValueError, "TOP20 업종명"):
            build_deliverables.validate_result(result, self.taxonomy)

    def test_customer_result_requires_address_but_sample_hides_it(self) -> None:
        self.assertEqual(build_deliverables._display_address(self.result), "주소 비식별 처리 샘플")
        result = copy.deepcopy(self.result)
        result.pop("sample_redaction")
        result["normalized_address"] = ""
        result["address"] = ""
        with self.assertRaisesRegex(ValueError, "분석 주소"):
            build_deliverables._display_address(result)

    def test_sample_pdf_and_xlsx_embed_current_taxonomy_identity(self) -> None:
        sample_dir = ROOT / "output" / "samples" / "gap-271"
        pdf_path = sample_dir / "공백상권_sample-n8n-e2e-271_요약_샘플.pdf"
        xlsx_path = sample_dir / "공백상권_sample-n8n-e2e-271_247업종_샘플.xlsx"
        self.assertTrue(pdf_path.is_file(), "현재 PDF 샘플이 없습니다.")
        self.assertTrue(xlsx_path.is_file(), "현재 XLSX 샘플이 없습니다.")

        pdf = PdfReader(str(pdf_path))
        self.assertEqual(len(pdf.pages), 2)
        pdf_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        self.assertIn(self.taxonomy["taxonomy_version"], pdf_text)
        self.assertIn(self.taxonomy["taxonomy_hash"], pdf_text)

        from openpyxl import load_workbook

        workbook = load_workbook(xlsx_path, read_only=True, data_only=True)
        try:
            self.assertEqual(workbook.sheetnames, ["요약", "전체 247개"])
            summary = workbook["요약"]
            self.assertEqual(summary["B5"].value, self.taxonomy["taxonomy_version"])
            self.assertEqual(summary["A8"].value, "taxonomy SHA-256")
            self.assertEqual(summary["B8"].value, self.taxonomy["taxonomy_hash"])
            self.assertEqual(workbook["전체 247개"].max_row, 251)
        finally:
            workbook.close()


if __name__ == "__main__":
    unittest.main()
