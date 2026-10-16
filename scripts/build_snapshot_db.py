#!/usr/bin/env python3
"""Build a local SQLite/R*Tree index from a validated nationwide snapshot ZIP."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .korea_coordinates import is_korea_plausible
    from .taxonomy_integrity import verify_taxonomy
except ImportError:  # Running this file directly from the scripts directory.
    from korea_coordinates import is_korea_plausible
    from taxonomy_integrity import verify_taxonomy


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ZIP = ROOT / "data" / "raw" / "semas_store_20260630.zip"
DEFAULT_VALIDATION = ROOT / "data" / "processed" / "snapshot_validation_20260630.json"
DEFAULT_TAXONOMY = ROOT / "설계" / "03_업종분류표.json"
DEFAULT_DB = ROOT / "data" / "processed" / "semas_store_20260630.sqlite"
REQUIRED_COLUMNS = {
    "상가업소번호",
    "상권업종소분류코드",
    "경도",
    "위도",
}
ENGINE_VERSION = "0.1.0"
ALLOWED_ABC_TAGS = {"A", "B", "C", "미분류"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def make_database(zip_path: Path, validation_path: Path, taxonomy_path: Path, target_path: Path, replace: bool) -> dict[str, Any]:
    validation = load_json(validation_path)
    taxonomy = load_json(taxonomy_path)
    taxonomy_hash = verify_taxonomy(taxonomy)
    if validation.get("status") != "validated" or validation.get("zip_crc_check") != "passed":
        raise ValueError("Source snapshot has not passed validation")
    if validation.get("codes_outside_taxonomy") or validation.get("unobserved_taxonomy_codes"):
        raise ValueError("Snapshot does not match the full category taxonomy")
    if validation.get("category_hierarchy_conflicts") or validation.get("category_name_mismatches"):
        raise ValueError("Official taxonomy crosswalk still has unresolved differences")
    if any(validation.get(field) for field in (
        "duplicate_store_id_rows",
        "missing_store_id_rows",
        "missing_category_code_rows",
        "missing_coordinate_rows",
        "invalid_coordinate_rows",
        "coordinate_plausibility_outlier_rows",
    )):
        raise ValueError("Snapshot contains invalid IDs, categories, or coordinates")
    if not zip_path.is_file():
        raise FileNotFoundError(zip_path)
    source_hash = sha256_file(zip_path)
    if source_hash != validation.get("snapshot_manifest", {}).get("sha256"):
        raise ValueError("Snapshot checksum differs from the validated manifest")

    categories = taxonomy.get("categories", [])
    if len(categories) != 247 or len({item["code"] for item in categories}) != 247:
        raise ValueError("Expected 247 unique category records")
    if any(item.get("status") != "verified_current_and_observed" for item in categories):
        raise ValueError("Every category must have a verified source crosswalk")
    if any(item.get("abc_tag") not in ALLOWED_ABC_TAGS for item in categories):
        raise ValueError("Every category must have a supported ABC tag or be marked 미분류")
    if any(item.get("name") != item.get("official_current_name") for item in categories):
        raise ValueError("Taxonomy display names differ from the official current names")
    if sum(item.get("abc_tag") == "A" for item in categories) < 10:
        raise ValueError("Fewer than 10 A-tag categories are available")
    if taxonomy.get("abc_status") == "owner_approved" and any(item.get("abc_tag") == "미분류" for item in categories):
        raise ValueError("An owner-approved taxonomy cannot contain 미분류 categories")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists() and not replace:
        raise FileExistsError(f"Database already exists; use --replace to rebuild: {target_path}")
    building_path = target_path.with_suffix(target_path.suffix + ".building")
    if building_path.exists():
        building_path.unlink()

    category_by_code = {item["code"]: item for item in categories}
    counts: dict[str, int] = {code: 0 for code in category_by_code}
    total_rows = 0
    next_row_id = 1
    store_batch: list[tuple[int, str, str, float, float]] = []
    spatial_batch: list[tuple[int, float, float, float, float]] = []

    connection = sqlite3.connect(building_path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.executescript(
            """
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE categories (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                large_code TEXT NOT NULL,
                medium_code TEXT NOT NULL,
                abc_tag TEXT NOT NULL CHECK (abc_tag IN ('A','B','C','미분류')),
                classification_basis TEXT NOT NULL
            );
            CREATE TABLE stores (
                id INTEGER PRIMARY KEY,
                source_store_id TEXT NOT NULL UNIQUE,
                category_code TEXT NOT NULL REFERENCES categories(code),
                longitude REAL NOT NULL,
                latitude REAL NOT NULL
            );
            CREATE VIRTUAL TABLE store_spatial USING rtree(
                id, min_lon, max_lon, min_lat, max_lat
            );
            """
        )
        connection.executemany(
            "INSERT INTO categories(code,name,large_code,medium_code,abc_tag,classification_basis) VALUES(?,?,?,?,?,?)",
            [(
                item["code"],
                item["official_current_name"],
                item["large_code"],
                item["medium_code"],
                item["abc_tag"],
                item["classification_basis"],
            ) for item in categories],
        )

        def flush() -> None:
            if not store_batch:
                return
            connection.executemany(
                "INSERT INTO stores(id,source_store_id,category_code,longitude,latitude) VALUES(?,?,?,?,?)",
                store_batch,
            )
            connection.executemany(
                "INSERT INTO store_spatial(id,min_lon,max_lon,min_lat,max_lat) VALUES(?,?,?,?,?)",
                spatial_batch,
            )
            store_batch.clear()
            spatial_batch.clear()

        with zipfile.ZipFile(zip_path) as archive:
            members = [item for item in archive.infolist() if item.filename.lower().endswith(".csv")]
            if len(members) != validation.get("csv_member_count"):
                raise ValueError("CSV member count changed after validation")
            for member in members:
                with archive.open(member, "r") as binary_stream:
                    text_stream = io.TextIOWrapper(binary_stream, encoding="utf-8-sig", newline="")
                    reader = csv.DictReader(text_stream)
                    missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
                    if missing:
                        raise ValueError(f"{member.filename}: missing columns {sorted(missing)}")
                    for row in reader:
                        source_id = (row.get("상가업소번호") or "").strip()
                        code = (row.get("상권업종소분류코드") or "").strip()
                        try:
                            lon = float(row["경도"])
                            lat = float(row["위도"])
                        except (KeyError, TypeError, ValueError) as exc:
                            raise ValueError(f"Invalid coordinates for source row {total_rows + 1}") from exc
                        if not source_id or code not in category_by_code:
                            raise ValueError(f"Missing or unmapped source row {total_rows + 1}")
                        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                            raise ValueError(f"Out-of-range coordinates for source row {total_rows + 1}")
                        if not is_korea_plausible(lon, lat):
                            raise ValueError(f"Coordinates outside the Korea plausibility bounds for source row {total_rows + 1}")

                        store_batch.append((next_row_id, source_id, code, lon, lat))
                        spatial_batch.append((next_row_id, lon, lon, lat, lat))
                        counts[code] += 1
                        total_rows += 1
                        next_row_id += 1
                        if len(store_batch) >= 20_000:
                            flush()
            flush()

        if total_rows != validation.get("total_source_rows"):
            raise ValueError(f"Loaded row count {total_rows} differs from validated source row count")
        if counts != {item["code"]: item["rows"] for item in validation["category_counts"]}:
            raise ValueError("Per-category row counts differ from the validation report")

        connection.execute("CREATE INDEX stores_category_idx ON stores(category_code)")
        connection.executemany("INSERT INTO meta(key,value) VALUES(?,?)", [
            ("database_state", "building"),
            ("snapshot_id", validation["snapshot_id"]),
            ("source_as_of", validation["source_as_of"]),
            ("source_url", validation["source_url"]),
            ("source_zip_sha256", source_hash),
            ("taxonomy_version", taxonomy["taxonomy_version"]),
            ("taxonomy_hash", taxonomy_hash),
            ("abc_status", taxonomy.get("abc_status", "unclassified")),
            ("abc_unclassified_count", str(sum(item.get("abc_tag") == "미분류" for item in categories))),
            ("analysis_engine_version", ENGINE_VERSION),
            ("store_rows", str(total_rows)),
            ("category_count", str(len(categories))),
            ("built_at", datetime.now(timezone.utc).isoformat()),
        ])
        connection.commit()

        store_count = connection.execute("SELECT COUNT(*) FROM stores").fetchone()[0]
        spatial_count = connection.execute("SELECT COUNT(*) FROM store_spatial").fetchone()[0]
        if store_count != total_rows or spatial_count != total_rows:
            raise ValueError(f"Store/spatial index count mismatch: {store_count}/{spatial_count}/{total_rows}")
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity check failed")
        try:
            rtree_status = connection.execute("SELECT rtreecheck('store_spatial')").fetchone()[0]
        except sqlite3.OperationalError:
            rtree_status = "count_verified"
        if rtree_status != "ok":
            raise ValueError(f"R*Tree integrity check failed: {rtree_status}")

        connection.execute("UPDATE meta SET value='complete' WHERE key='database_state'")
        connection.commit()
        connection.execute("PRAGMA optimize")
    finally:
        connection.close()

    if target_path.exists():
        backup_path = target_path.with_suffix(target_path.suffix + ".previous")
        if backup_path.exists():
            backup_path.unlink()
        os.replace(target_path, backup_path)
    os.replace(building_path, target_path)

    return {
        "status": "complete",
        "snapshot_id": validation["snapshot_id"],
        "database_path": str(target_path.relative_to(ROOT)),
        "database_bytes": target_path.stat().st_size,
        "store_rows": total_rows,
        "spatial_index_rows": total_rows,
        "category_count": len(categories),
        "taxonomy_version": taxonomy["taxonomy_version"],
        "taxonomy_hash": taxonomy_hash,
        "abc_status": taxonomy.get("abc_status", "unclassified"),
        "analysis_engine_version": ENGINE_VERSION,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", type=Path, default=DEFAULT_ZIP)
    parser.add_argument("--validation", type=Path, default=DEFAULT_VALIDATION)
    parser.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    result = make_database(args.zip.resolve(), args.validation.resolve(), args.taxonomy.resolve(), args.db.resolve(), args.replace)
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
