#!/usr/bin/env python3
"""Refresh the official crosswalk while preserving the reviewed ABC mapping."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

try:
    from .taxonomy_integrity import seal_taxonomy
except ImportError:  # Running this file directly from the scripts directory.
    from taxonomy_integrity import seal_taxonomy


ROOT = Path(__file__).resolve().parents[1]
TAXONOMY_PATH = ROOT / "설계" / "03_업종분류표.json"
WORKSHEET_PATH = ROOT / "설계" / "05_ABC_분류_작업표.csv"
VALIDATION_PATH = ROOT / "data" / "processed" / "snapshot_validation_20260630.json"
SOURCE_URL = "https://www.data.go.kr/data/15083033/fileData.do"
ALLOWED_ABC_TAGS = {"A", "B", "C", "미분류"}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    taxonomy = load_json(TAXONOMY_PATH)
    taxonomy_version = str(taxonomy.get("taxonomy_version", "")).strip()
    if not taxonomy_version:
        raise ValueError("Taxonomy version is missing")
    validation = load_json(VALIDATION_PATH)
    if validation.get("status") != "validated" or validation.get("zip_crc_check") != "passed":
        raise ValueError("A fully validated source snapshot is required")
    if validation.get("codes_outside_taxonomy") or validation.get("unobserved_taxonomy_codes"):
        raise ValueError("Snapshot taxonomy coverage is incomplete")
    if validation.get("category_hierarchy_conflicts"):
        raise ValueError("Official category hierarchy has conflicts")
    if validation.get("duplicate_store_id_rows"):
        raise ValueError("Snapshot contains duplicate stable store IDs")

    categories = taxonomy["categories"]
    if len(categories) != 247 or len({item["code"] for item in categories}) != 247:
        raise ValueError("Expected exactly 247 unique workflow categories")
    by_code = {item["code"]: item for item in validation["category_counts"]}
    if set(by_code) != {item["code"] for item in categories}:
        raise ValueError("Snapshot observed codes do not exactly match the 247-code workflow taxonomy")

    worksheet = list(csv.DictReader(WORKSHEET_PATH.open("r", encoding="utf-8-sig", newline="")))
    if len(worksheet) != 247 or {row["workflow_code"] for row in worksheet} != set(by_code):
        raise ValueError("ABC worksheet rows do not match the validated taxonomy")
    fields = list(worksheet[0])
    if "classification_rule_draft" not in fields:
        fields.insert(fields.index("classification_basis") + 1, "classification_rule_draft")
    worksheet_by_code = {row["workflow_code"]: row for row in worksheet}

    abc_counts = {"A": 0, "B": 0, "C": 0, "미분류": 0}
    for item in categories:
        code = item["code"]
        record = by_code[code]
        hierarchy = record["official_hierarchy"]
        if len(hierarchy) != 1:
            raise ValueError(f"Expected one official hierarchy row for {code}")
        official = hierarchy[0]
        if official["small_code"] != code:
            raise ValueError(f"Workflow code and official small code differ for {code}")
        official_name = official["small_name"]
        item["workflow_name_snapshot"] = item.get("name", "")
        item["name"] = official_name
        item["large_code"] = official["large_code"]
        item["large_name"] = official["large_name"]
        item["medium_code"] = official["medium_code"]
        item["medium_name"] = official["medium_name"]
        item["official_current_name"] = official_name
        item["official_source"] = SOURCE_URL
        item["official_source_as_of"] = validation["source_as_of"]
        item["source_snapshot_id"] = validation["snapshot_id"]
        item["observed_snapshot_rows"] = record["rows"]
        item["status"] = "verified_current_and_observed"

        row = worksheet_by_code[code]
        tag = row.get("abc_tag", "").strip()
        if tag not in ALLOWED_ABC_TAGS:
            raise ValueError(f"Missing or unsupported reviewed ABC tag for {code}: {tag!r}")
        basis = row.get("classification_basis", "").strip()
        rule = row.get("classification_rule_draft", "").strip()
        if not basis or not rule:
            raise ValueError(f"ABC review basis is missing for {code}")
        abc_counts[tag] += 1

        item["abc_tag"] = tag
        item["classification_basis"] = basis
        item["abc_rule_draft"] = rule
        item["abc_review_note"] = row.get("abc_review_note", "")
        item["abc_review_status"] = "ambiguity_hold" if tag == "미분류" else "reviewed_before_results"

        row["official_large_code"] = official["large_code"]
        row["official_medium_code"] = official["medium_code"]
        row["official_small_code"] = official["small_code"]
        row["official_current_name"] = official_name
        row["official_use_status"] = "현행 데이터에서 점포 관측"
        row["code_relation"] = "workflow_code=official_small_code (정확 일치)"
        row["previous_code"] = ""
        row["successor_code"] = ""
        row["data_support_status"] = "전국 CSV에서 관측 확인"
        row["unsupported_reason"] = ""
        row["exclusion_reason"] = ""
        row["official_source"] = SOURCE_URL
        row["official_source_as_of"] = validation["source_as_of"]
        row["taxonomy_version"] = taxonomy.get("taxonomy_version", "")

    taxonomy["official_crosswalk_status"] = "전국 CSV 2026-06-30의 247개 현행 코드·명칭·계층·점포 관측으로 대조 완료"
    taxonomy["official_source"] = SOURCE_URL
    taxonomy["official_source_as_of"] = validation["source_as_of"]
    taxonomy["source_snapshot_id"] = validation["snapshot_id"]
    if taxonomy.get("abc_status") == "owner_approved" and abc_counts["미분류"]:
        raise ValueError("An owner-approved taxonomy cannot contain 미분류 categories")
    if taxonomy.get("abc_status") != "owner_approved":
        taxonomy["abc_status"] = "draft_owner_review_required"
    taxonomy["abc_counts"] = abc_counts

    taxonomy_hash, taxonomy_version = seal_taxonomy(taxonomy)
    for row in worksheet:
        row["taxonomy_version"] = taxonomy_version

    TAXONOMY_PATH.write_text(json.dumps(taxonomy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with WORKSHEET_PATH.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(worksheet)

    print(json.dumps({
        "taxonomy_version": taxonomy_version,
        "taxonomy_hash": taxonomy_hash,
        "verified_codes": len(categories),
        "snapshot_rows": validation["total_source_rows"],
        "observed_codes": validation["observed_category_code_count"],
        "abc_status": taxonomy["abc_status"],
        "abc_counts": taxonomy["abc_counts"],
        "classification_basis": "preserved from code-level ABC review in 설계/05_ABC_분류_작업표.csv",
    }, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
