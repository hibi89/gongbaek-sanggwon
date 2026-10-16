from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from scripts import analyze_gap
from scripts.analyzer_service import AnalyzerHTTPServer, Handler
from scripts.taxonomy_integrity import seal_taxonomy


class AnalyzerServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "service-test.sqlite"
        connection = sqlite3.connect(self.db_path)
        connection.executescript(
            """
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE categories (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                large_code TEXT NOT NULL,
                medium_code TEXT NOT NULL,
                abc_tag TEXT NOT NULL,
                classification_basis TEXT NOT NULL
            );
            CREATE TABLE stores (
                id INTEGER PRIMARY KEY,
                source_store_id TEXT UNIQUE NOT NULL,
                category_code TEXT NOT NULL,
                longitude REAL NOT NULL,
                latitude REAL NOT NULL
            );
            CREATE VIRTUAL TABLE store_spatial USING rtree(id,min_lon,max_lon,min_lat,max_lat);
            """
        )
        tags = ["A"] * 150 + ["B"] * 35 + ["C"] * 33 + ["미분류"] * 29
        tags[1] = "미분류"
        tags[-1] = "A"
        taxonomy_categories = [
            {"code": f"C{index:05d}", "name": f"업종 {index}", "abc_tag": tag}
            for index, tag in enumerate(tags)
        ]
        self.taxonomy = {
            "taxonomy_version": "service-test-taxonomy",
            "abc_status": "draft_owner_review_required",
            "categories": taxonomy_categories,
        }
        self.taxonomy_hash, self.taxonomy_version = seal_taxonomy(self.taxonomy)
        self.taxonomy_path = Path(self.tempdir.name) / "taxonomy.json"
        self.taxonomy_path.write_text(json.dumps(self.taxonomy, ensure_ascii=False), encoding="utf-8")
        self.taxonomy_patch = patch.object(analyze_gap, "TAXONOMY_PATH", self.taxonomy_path)
        self.taxonomy_patch.start()
        categories = [
            (f"C{index:05d}", f"업종 {index}", "G2", "G204", tag, "test")
            for index, tag in enumerate(tags)
        ]
        connection.executemany("INSERT INTO categories VALUES(?,?,?,?,?,?)", categories)
        connection.execute("INSERT INTO stores VALUES(1,'STORE-1','C00000',126.9780,37.5665)")
        connection.execute("INSERT INTO store_spatial VALUES(1,126.9780,126.9780,37.5665,37.5665)")
        connection.executemany("INSERT INTO meta VALUES(?,?)", [
            ("database_state", "complete"),
            ("snapshot_id", "test-snapshot"),
            ("source_as_of", "2026-06-30"),
            ("source_url", "https://example.invalid/source"),
            ("taxonomy_version", self.taxonomy_version),
            ("taxonomy_hash", self.taxonomy_hash),
            ("abc_status", "draft_owner_review_required"),
            ("analysis_engine_version", "test"),
            ("store_rows", "1"),
        ])
        connection.commit()
        connection.close()

        self.token = "test-token-" + ("x" * 40)
        self.server = AnalyzerHTTPServer(("127.0.0.1", 0), Handler, self.db_path, self.token)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.taxonomy_patch.stop()
        self.tempdir.cleanup()

    def test_health_reports_snapshot_ready(self) -> None:
        with urllib.request.urlopen(self.base_url + "/health", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["category_count"], 247)
        self.assertEqual(payload["taxonomy_version"], self.taxonomy_version)
        self.assertEqual(payload["expected_taxonomy_version"], self.taxonomy_version)
        self.assertEqual(payload["taxonomy_hash"], self.taxonomy_hash)
        self.assertEqual(payload["expected_taxonomy_hash"], self.taxonomy_hash)

    def test_health_rejects_stale_taxonomy_database(self) -> None:
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute("UPDATE meta SET value='old-taxonomy' WHERE key='taxonomy_version'")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(self.base_url + "/health", timeout=5)
        self.assertEqual(error.exception.code, 503)
        payload = json.loads(error.exception.read().decode("utf-8"))
        self.assertEqual(payload["status"], "not_ready")
        self.assertEqual(payload["reason"], "stale_taxonomy_version")
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute("UPDATE meta SET value=? WHERE key='taxonomy_version'", (self.taxonomy_version,))
            connection.execute("UPDATE meta SET value=? WHERE key='taxonomy_hash'", ("0" * 64,))
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(urllib.error.HTTPError) as hash_error:
            urllib.request.urlopen(self.base_url + "/health", timeout=5)
        self.assertEqual(hash_error.exception.code, 503)
        hash_payload = json.loads(hash_error.exception.read().decode("utf-8"))
        self.assertEqual(hash_payload["reason"], "stale_taxonomy_hash")

    def test_analyze_requires_bearer_token(self) -> None:
        request = urllib.request.Request(
            self.base_url + "/analyze",
            data=b'{"latitude":0,"longitude":0}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(error.exception.code, 401)

    def test_unclassified_categories_stay_in_overall_top20_and_block_publication(self) -> None:
        request = urllib.request.Request(
            self.base_url + "/analyze",
            data=json.dumps({"request_id": "test-1", "address": "테스트 주소", "latitude": 37.5665, "longitude": 126.9780}).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertEqual(payload["status"], "analysis_complete")
        self.assertEqual(payload["source"]["taxonomy_hash"], self.taxonomy_hash)
        self.assertFalse(payload["publishable"])
        self.assertEqual(payload["publication_gate"], "blocked_unclassified_taxonomy")
        self.assertEqual(len(payload["overall_top20"]), 20)
        self.assertEqual(len(payload["a_top10"]), 10)
        self.assertEqual(payload["coverage"]["unclassified_category_count"], 29)
        self.assertIn("미분류", {item["abc_tag"] for item in payload["overall_top20"]})
        self.assertTrue(all(item["abc_tag"] == "A" for item in payload["a_top10"]))
        self.assertEqual(payload["all_category_counts"][0]["counts"]["5km"], 1)

    def test_owner_review_still_blocks_after_all_tags_are_classified(self) -> None:
        for category in self.taxonomy["categories"]:
            if category["abc_tag"] == "미분류":
                category["abc_tag"] = "C"
        self.taxonomy["abc_status"] = "draft_owner_review_required"
        self.taxonomy_hash, self.taxonomy_version = seal_taxonomy(self.taxonomy)
        self.taxonomy_path.write_text(json.dumps(self.taxonomy, ensure_ascii=False), encoding="utf-8")
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute("UPDATE categories SET abc_tag='C' WHERE abc_tag='미분류'")
            connection.execute("UPDATE meta SET value=? WHERE key='taxonomy_version'", (self.taxonomy_version,))
            connection.execute("UPDATE meta SET value=? WHERE key='taxonomy_hash'", (self.taxonomy_hash,))
            connection.commit()
        finally:
            connection.close()
        request = urllib.request.Request(
            self.base_url + "/analyze",
            data=json.dumps({"request_id": "test-owner-review", "latitude": 37.5665, "longitude": 126.9780}).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertFalse(payload["publishable"])
        self.assertEqual(payload["coverage"]["unclassified_category_count"], 0)
        self.assertEqual(payload["publication_gate"], "blocked_abc_owner_review")

    def test_stale_database_taxonomy_version_is_rejected(self) -> None:
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute("UPDATE meta SET value='old-taxonomy' WHERE key='taxonomy_version'")
            connection.commit()
        finally:
            connection.close()
        request = urllib.request.Request(
            self.base_url + "/analyze",
            data=json.dumps({"request_id": "test-stale-taxonomy", "latitude": 37.5665, "longitude": 126.9780}).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(error.exception.code, 400)
        payload = json.loads(error.exception.read().decode("utf-8"))
        self.assertIn("taxonomy version is stale", payload["error"])

    def test_invalid_coordinates_return_error_instead_of_zero_report(self) -> None:
        for latitude, longitude in ((91, 0), (0, 0), (47.61, -122.33)):
            with self.subTest(latitude=latitude, longitude=longitude):
                request = urllib.request.Request(
                    self.base_url + "/analyze",
                    data=json.dumps({"latitude": latitude, "longitude": longitude}).encode("utf-8"),
                    headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(request, timeout=5)
                self.assertEqual(error.exception.code, 400)
                payload = json.loads(error.exception.read().decode("utf-8"))
                self.assertEqual(payload["status"], "analysis_failed")
                self.assertFalse(payload["publishable"])


if __name__ == "__main__":
    unittest.main()
