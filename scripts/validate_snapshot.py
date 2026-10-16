#!/usr/bin/env python3
"""Validate the official SEMAS nationwide store CSV snapshot.

The source ZIP is preserved unchanged. This script streams every CSV member,
validates required columns and UTF-8, records coverage against the workflow
taxonomy, and writes a machine-readable validation report.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .korea_coordinates import is_korea_plausible, korea_bbox
except ImportError:  # Running this file directly from the scripts directory.
    from korea_coordinates import is_korea_plausible, korea_bbox


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ZIP = ROOT / "data" / "raw" / "semas_store_20260630.zip"
DEFAULT_TAXONOMY = ROOT / "설계" / "03_업종분류표.json"
DEFAULT_MANIFEST = ROOT / "data" / "raw" / "manifest_20260630.json"
DEFAULT_REPORT = ROOT / "data" / "processed" / "snapshot_validation_20260630.json"
SOURCE_PAGE = "https://www.data.go.kr/data/15083033/fileData.do"
REQUIRED_COLUMNS = {
    "상가업소번호",
    "상권업종소분류코드",
    "상권업종소분류명",
    "경도",
    "위도",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_name(value: str) -> str:
    return re.sub(r"[\W_]+", "", value or "", flags=re.UNICODE)


def load_taxonomy(path: Path) -> dict[str, dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    categories = payload.get("categories")
    if not isinstance(categories, list):
        raise ValueError("Taxonomy JSON has no categories array")
    result: dict[str, dict[str, str]] = {}
    for category in categories:
        code = str(category.get("code", "")).strip()
        name = str(category.get("name", "")).strip()
        if not code or not name:
            raise ValueError("Taxonomy contains an empty code or name")
        if code in result:
            raise ValueError(f"Duplicate taxonomy code: {code}")
        result[code] = {"name": name, "large_code": str(category.get("large_code", ""))}
    return result


def inspect_snapshot(zip_path: Path, taxonomy_path: Path) -> dict[str, Any]:
    taxonomy = load_taxonomy(taxonomy_path)
    total_rows = 0
    files: list[dict[str, Any]] = []
    code_counts: Counter[str] = Counter()
    names_by_code: dict[str, set[str]] = defaultdict(set)
    hierarchy_by_code: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    store_ids: set[str] = set()
    duplicate_store_id_rows = 0
    missing_id = missing_code = missing_coordinate = invalid_coordinate = 0
    coordinate_plausibility_outlier_rows = 0
    coordinate_plausibility_examples: list[dict[str, Any]] = []
    outside_taxonomy: Counter[str] = Counter()

    with zipfile.ZipFile(zip_path) as archive:
        bad_member = archive.testzip()
        if bad_member:
            raise ValueError(f"ZIP CRC validation failed at {bad_member}")
        csv_members = [item for item in archive.infolist() if item.filename.lower().endswith(".csv")]
        if not csv_members:
            raise ValueError("ZIP contains no CSV members")

        for member in csv_members:
            file_rows = 0
            with archive.open(member, "r") as binary_stream:
                import io

                text_stream = io.TextIOWrapper(binary_stream, encoding="utf-8-sig", newline="")
                reader = csv.DictReader(text_stream)
                columns = set(reader.fieldnames or [])
                missing_columns = sorted(REQUIRED_COLUMNS - columns)
                if missing_columns:
                    raise ValueError(f"{member.filename}: missing required columns {missing_columns}")

                for row in reader:
                    file_rows += 1
                    store_id = (row.get("상가업소번호") or "").strip()
                    code = (row.get("상권업종소분류코드") or "").strip()
                    name = (row.get("상권업종소분류명") or "").strip()
                    lon_raw = (row.get("경도") or "").strip()
                    lat_raw = (row.get("위도") or "").strip()

                    if not store_id:
                        missing_id += 1
                    elif store_id in store_ids:
                        duplicate_store_id_rows += 1
                    else:
                        store_ids.add(store_id)
                    if not code:
                        missing_code += 1
                    elif code not in taxonomy:
                        outside_taxonomy[code] += 1
                    else:
                        code_counts[code] += 1
                        if name:
                            names_by_code[code].add(name)
                        hierarchy_by_code[code].add((
                            (row.get("상권업종대분류코드") or "").strip(),
                            (row.get("상권업종대분류명") or "").strip(),
                            (row.get("상권업종중분류코드") or "").strip(),
                            (row.get("상권업종중분류명") or "").strip(),
                            code,
                            name,
                        ))

                    if not lon_raw or not lat_raw:
                        missing_coordinate += 1
                    else:
                        try:
                            lon = float(lon_raw)
                            lat = float(lat_raw)
                        except ValueError:
                            invalid_coordinate += 1
                        else:
                            if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                                invalid_coordinate += 1
                            elif not is_korea_plausible(lon, lat):
                                coordinate_plausibility_outlier_rows += 1
                                if len(coordinate_plausibility_examples) < 10:
                                    coordinate_plausibility_examples.append({
                                        "member": member.filename,
                                        "row_in_member": file_rows,
                                        "longitude": lon,
                                        "latitude": lat,
                                    })

            total_rows += file_rows
            files.append({
                "member": member.filename,
                "uncompressed_bytes": member.file_size,
                "compressed_bytes": member.compress_size,
                "rows": file_rows,
            })

    observed_codes = set(code_counts)
    taxonomy_codes = set(taxonomy)
    unobserved = sorted(taxonomy_codes - observed_codes)
    name_mismatches = []
    for code in sorted(observed_codes & taxonomy_codes):
        expected = taxonomy[code]["name"]
        observed = sorted(names_by_code[code])
        if any(normalized_name(name) != normalized_name(expected) for name in observed):
            name_mismatches.append({
                "code": code,
                "taxonomy_name": expected,
                "snapshot_names": observed,
                "rows": code_counts[code],
            })

    hierarchy_conflicts = [
        {"code": code, "mappings": [list(value) for value in sorted(mappings)]}
        for code, mappings in sorted(hierarchy_by_code.items())
        if len(mappings) != 1
    ]

    return {
        "status": "validated" if invalid_coordinate == 0 and coordinate_plausibility_outlier_rows == 0 else "rejected",
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "source_as_of": "2026-06-30",
        "source_url": SOURCE_PAGE,
        "zip_crc_check": "passed",
        "taxonomy_path": str(taxonomy_path.relative_to(ROOT)),
        "taxonomy_code_count": len(taxonomy),
        "csv_member_count": len(files),
        "csv_members": files,
        "total_source_rows": total_rows,
        "observed_category_code_count": len(observed_codes),
        "unobserved_taxonomy_codes": unobserved,
        "codes_outside_taxonomy": [
            {"code": code, "rows": count} for code, count in sorted(outside_taxonomy.items())
        ],
        "category_name_mismatches": name_mismatches,
        "category_hierarchy_conflicts": hierarchy_conflicts,
        "duplicate_store_id_rows": duplicate_store_id_rows,
        "missing_store_id_rows": missing_id,
        "missing_category_code_rows": missing_code,
        "missing_coordinate_rows": missing_coordinate,
        "invalid_coordinate_rows": invalid_coordinate,
        "coordinate_plausibility": {
            "bounds": korea_bbox(),
            "outlier_rows": coordinate_plausibility_outlier_rows,
            "examples": coordinate_plausibility_examples,
        },
        "coordinate_plausibility_outlier_rows": coordinate_plausibility_outlier_rows,
        "category_counts": [
            {
                "code": code,
                "taxonomy_name": taxonomy[code]["name"],
                "snapshot_names": sorted(names_by_code[code]),
                "official_hierarchy": [
                    {
                        "large_code": mapping[0],
                        "large_name": mapping[1],
                        "medium_code": mapping[2],
                        "medium_name": mapping[3],
                        "small_code": mapping[4],
                        "small_name": mapping[5],
                    }
                    for mapping in sorted(hierarchy_by_code[code])
                ],
                "rows": code_counts[code],
            }
            for code in sorted(observed_codes)
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", type=Path, default=DEFAULT_ZIP)
    parser.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    zip_path = args.zip.resolve()
    taxonomy_path = args.taxonomy.resolve()
    manifest_path = args.manifest.resolve()
    report_path = args.report.resolve()
    if not zip_path.is_file():
        raise FileNotFoundError(zip_path)
    if not taxonomy_path.is_file():
        raise FileNotFoundError(taxonomy_path)

    prior = {}
    if manifest_path.is_file():
        prior = json.loads(manifest_path.read_text(encoding="utf-8-sig"))

    report = inspect_snapshot(zip_path, taxonomy_path)
    zip_size = zip_path.stat().st_size
    zip_hash = sha256_file(zip_path)
    manifest = {
        "source_url": SOURCE_PAGE,
        "dataset_name": "소상공인시장진흥공단_상가(상권)정보_20260630",
        "source_as_of": "2026-06-30",
        "downloaded_at": prior.get("downloaded_at"),
        "download_url": prior.get("download_url"),
        "file_name": zip_path.name,
        "bytes": zip_size,
        "sha256": zip_hash,
        "zip_integrity": "passed",
        "csv_member_count": len(report["csv_members"]),
        "total_source_rows": report["total_source_rows"],
        "taxonomy_code_count": report["taxonomy_code_count"],
        "observed_category_code_count": report["observed_category_code_count"],
        "zip_csv_entries": report["csv_members"],
    }
    report["snapshot_id"] = f"semas-20260630-{zip_hash[:12]}"
    report["snapshot_manifest"] = manifest

    report_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({
        "status": report["status"],
        "snapshot_id": report["snapshot_id"],
        "zip_bytes": zip_size,
        "sha256": zip_hash,
        "csv_member_count": report["csv_member_count"],
        "total_source_rows": report["total_source_rows"],
        "taxonomy_code_count": report["taxonomy_code_count"],
        "observed_category_code_count": report["observed_category_code_count"],
        "unobserved_taxonomy_code_count": len(report["unobserved_taxonomy_codes"]),
        "codes_outside_taxonomy": len(report["codes_outside_taxonomy"]),
        "category_name_mismatches": len(report["category_name_mismatches"]),
        "category_hierarchy_conflicts": len(report["category_hierarchy_conflicts"]),
        "duplicate_store_id_rows": report["duplicate_store_id_rows"],
        "missing_store_id_rows": report["missing_store_id_rows"],
        "missing_category_code_rows": report["missing_category_code_rows"],
        "missing_coordinate_rows": report["missing_coordinate_rows"],
        "invalid_coordinate_rows": report["invalid_coordinate_rows"],
        "coordinate_plausibility_outlier_rows": report["coordinate_plausibility_outlier_rows"],
        "coordinate_plausibility_bounds": report["coordinate_plausibility"]["bounds"],
        "report_path": str(report_path.relative_to(ROOT)),
    }, ensure_ascii=True))
    return 0 if report["status"] == "validated" else 2


if __name__ == "__main__":
    raise SystemExit(main())
