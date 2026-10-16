from __future__ import annotations

import json
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULT_PATH = ROOT / "data" / "results" / "sample_workflow_address_taxonomy_v3_20260930.json"
REPORT_PATH = ROOT / "판매자료" / "샘플_공백상권_결과_검토용.html"
RADII = ("1km", "2km", "3km", "4km", "5km")


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self.current_row: list[str] | None = None
        self.current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self.tables.append([])
        elif tag == "tr":
            self.current_row = []
        elif tag in {"td", "th"}:
            self.current_cell = []

    def handle_data(self, data: str) -> None:
        if self.current_cell is not None:
            self.current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self.current_cell is not None and self.current_row is not None:
            self.current_row.append("".join(self.current_cell).strip())
            self.current_cell = None
        elif tag == "tr" and self.current_row is not None and self.tables:
            self.tables[-1].append(self.current_row)
            self.current_row = None


class SampleReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
        cls.report = REPORT_PATH.read_text(encoding="utf-8")
        cls.parser = TableParser()
        cls.parser.feed(cls.report)

    def test_report_tables_match_top20_and_a_top10_results(self) -> None:
        self.assertEqual(len(self.parser.tables), 2)
        for table, expected in zip(
            self.parser.tables,
            (self.result["overall_top20"], self.result["a_top10"]),
            strict=True,
        ):
            rows = table[1:]
            self.assertEqual(len(rows), len(expected))
            for row, category in zip(rows, expected, strict=True):
                self.assertEqual(row[0], str(category["rank"]))
                self.assertIn(category["name"], row[1])
                self.assertIn(category["code"], row[1])
                self.assertEqual(row[2], category["abc_tag"])
                self.assertEqual(row[3:], [str(category["counts"][radius]) for radius in RADII])

    def test_summary_cards_match_all_247_category_counts(self) -> None:
        totals = {
            radius: sum(item["counts"][radius] for item in self.result["all_category_counts"])
            for radius in RADII
        }
        self.assertEqual(len(self.result["all_category_counts"]), 247)
        for radius, total in totals.items():
            self.assertIn(f"<span>{radius} 내 관측 점포</span><strong>{total:,}</strong>", self.report)

    def test_report_is_masked_and_clearly_internal(self) -> None:
        self.assertIn("검토용 · 외부공개 금지", self.report)
        self.assertIn("내부 표본 · 분석 발행 게이트 통과", self.report)
        self.assertIn("부산광역시 연제구 · 상세주소 비공개", self.report)
        address = self.result.get("address")
        if address:
            self.assertNotIn(address, self.report)

    def test_report_includes_data_date_and_no_demand_claim(self) -> None:
        self.assertIn(self.result["source"]["source_as_of"], self.report)
        self.assertIn(self.result["source"]["snapshot_id"], self.report)
        self.assertIn("공백이 수요나 수익성을 의미하지 않으며", self.report)

    def test_report_result_database_and_taxonomy_versions_match(self) -> None:
        taxonomy = json.loads((ROOT / "설계" / "03_업종분류표.json").read_text(encoding="utf-8"))
        import sqlite3

        database = ROOT / "data" / "processed" / "semas_store_20260630.sqlite"
        if database.is_file():
            with sqlite3.connect(database) as connection:
                database_meta = dict(connection.execute("SELECT key,value FROM meta"))
        else:
            database_meta = json.loads(
                (ROOT / "data" / "results" / "sample_database_meta.json").read_text(encoding="utf-8")
            )
        database_version = database_meta["taxonomy_version"]
        database_hash = database_meta["taxonomy_hash"]
        version = self.result["source"]["taxonomy_version"]
        content_hash = self.result["source"]["taxonomy_hash"]
        self.assertEqual(version, taxonomy["taxonomy_version"])
        self.assertEqual(content_hash, taxonomy["taxonomy_hash"])
        self.assertEqual(version, database_version)
        self.assertEqual(content_hash, database_hash)
        self.assertIn(version, self.report)
        self.assertNotIn("이 표본은 현재 분류표 기준의 결과가 아닙니다.", self.report)


if __name__ == "__main__":
    unittest.main()
