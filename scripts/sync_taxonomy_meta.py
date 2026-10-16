#!/usr/bin/env python3
"""Safely seal an existing complete snapshot DB when its category rows match."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

try:
    from .taxonomy_integrity import verify_taxonomy
except ImportError:  # Running this file directly from the scripts directory.
    from taxonomy_integrity import verify_taxonomy


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "processed" / "semas_store_20260630.sqlite"
TAXONOMY_PATH = ROOT / "설계" / "03_업종분류표.json"


def synchronize(db_path: Path, taxonomy_path: Path = TAXONOMY_PATH) -> dict[str, Any]:
    taxonomy = json.loads(taxonomy_path.read_text(encoding="utf-8-sig"))
    taxonomy_hash = verify_taxonomy(taxonomy)
    expected = {item["code"]: item for item in taxonomy.get("categories", [])}
    if len(expected) != 247:
        raise ValueError("Current taxonomy must contain exactly 247 unique codes")

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN IMMEDIATE")
        meta = {row["key"]: row["value"] for row in connection.execute("SELECT key,value FROM meta")}
        if meta.get("database_state") != "complete":
            raise ValueError("Only a complete snapshot database can be synchronized")
        rows = [dict(row) for row in connection.execute("SELECT code,name,abc_tag FROM categories")]
        actual = {row["code"]: row for row in rows}
        if len(actual) != 247 or set(actual) != set(expected):
            raise ValueError("Database category codes do not match the 247-code taxonomy")
        mismatches = [
            code for code, category in expected.items()
            if actual[code]["name"] != category.get("name")
            or actual[code]["abc_tag"] != category.get("abc_tag")
        ]
        if mismatches:
            raise ValueError("Database category name or ABC tag differs from taxonomy; rebuild required")
        if connection.execute("SELECT COUNT(*) FROM stores").fetchone()[0] != int(meta.get("store_rows", "-1")):
            raise ValueError("Database store row count differs from its metadata")

        values = {
            "taxonomy_version": str(taxonomy["taxonomy_version"]),
            "taxonomy_hash": taxonomy_hash,
            "abc_status": str(taxonomy.get("abc_status", "unclassified")),
            "abc_unclassified_count": str(sum(item.get("abc_tag") == "미분류" for item in expected.values())),
            "category_count": "247",
        }
        connection.executemany(
            "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            list(values.items()),
        )
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity check failed")
        connection.commit()
        return {
            "status": "synchronized",
            "database": str(db_path),
            "taxonomy_version": values["taxonomy_version"],
            "taxonomy_hash": taxonomy_hash,
            "category_rows_checked": len(rows),
            "store_rows": int(meta["store_rows"]),
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--taxonomy", type=Path, default=TAXONOMY_PATH)
    args = parser.parse_args()
    print(json.dumps(synchronize(args.db.resolve(), args.taxonomy.resolve()), ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
