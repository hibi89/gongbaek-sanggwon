#!/usr/bin/env python3
"""Independently compare live R*Tree analysis counts with a full-table scan."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from pathlib import Path
from typing import Any

try:
    from .build_deliverables import validate_result
    from .deliver_from_n8n_execution import DEFAULT_N8N_DB, read_execution
except ImportError:  # Running this file directly from the scripts directory.
    from build_deliverables import validate_result
    from deliver_from_n8n_execution import DEFAULT_N8N_DB, read_execution


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "processed" / "semas_store_20260630.sqlite"
TAXONOMY_PATH = ROOT / "설계" / "03_업종분류표.json"
RADII_METERS = (1000, 2000, 3000, 4000, 5000)
EARTH_RADIUS_METERS = 6_371_008.8
BOUNDARY_TOLERANCE_METERS = 1e-7


def independent_distance_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Haversine distance via atan2, independent of the analyzer's asin form."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    a = min(1.0, max(0.0, a))
    central_angle = 2 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return EARTH_RADIUS_METERS * central_angle


def full_scan_counts(
    connection: sqlite3.Connection,
    longitude: float,
    latitude: float,
) -> tuple[dict[str, list[int]], int]:
    counts = {
        row[0]: [0] * len(RADII_METERS)
        for row in connection.execute("SELECT code FROM categories ORDER BY code")
    }
    scanned = 0
    for code, store_lon, store_lat in connection.execute(
        "SELECT category_code,longitude,latitude FROM stores"
    ):
        scanned += 1
        distance = independent_distance_m(longitude, latitude, store_lon, store_lat)
        if distance > RADII_METERS[-1] and not math.isclose(
            distance, RADII_METERS[-1], rel_tol=0.0, abs_tol=BOUNDARY_TOLERANCE_METERS
        ):
            continue
        if code not in counts:
            raise ValueError(f"Store references a code outside categories: {code}")
        for index, radius in enumerate(RADII_METERS):
            if distance <= radius or math.isclose(
                distance, radius, rel_tol=0.0, abs_tol=BOUNDARY_TOLERANCE_METERS
            ):
                counts[code][index] += 1
    return counts, scanned


def verify_one(db_path: Path, n8n_db: Path, execution_id: int) -> dict[str, Any]:
    result, execution = read_execution(n8n_db, execution_id)
    taxonomy = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8-sig"))
    validate_result(result, taxonomy)
    center = result["center"]
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=30)
    try:
        meta = dict(connection.execute("SELECT key,value FROM meta"))
        if meta.get("taxonomy_hash") != result["source"].get("taxonomy_hash"):
            raise ValueError("Execution result and current database taxonomy hashes differ")
        full_counts, scanned = full_scan_counts(
            connection,
            float(center["longitude"]),
            float(center["latitude"]),
        )
    finally:
        connection.close()

    expected = {
        row["code"]: [int(row["counts"][f"{radius // 1000}km"]) for radius in RADII_METERS]
        for row in result["all_category_counts"]
    }
    if set(expected) != set(full_counts):
        raise ValueError("Execution result codes and database taxonomy codes differ")
    mismatches = [
        {"code": code, "rtree": expected[code], "full_scan": full_counts[code]}
        for code in sorted(expected)
        if expected[code] != full_counts[code]
    ]
    if mismatches:
        raise ValueError(json.dumps({"execution_id": execution_id, "mismatches": mismatches[:10]}, ensure_ascii=True))
    total = sum(values[-1] for values in full_counts.values())
    if total != int(result["coverage"]["stores_within_5km"]):
        raise ValueError("Independent 5km total differs from the execution coverage")
    return {
        "execution_id": int(execution["execution_id"]),
        "n8n_execution_status": execution["status"],
        "kakao_http_status": execution["kakao_http_status"],
        "analyzer_http_status": execution["analyzer_http_status"],
        "publication_gate": result["publication_gate"],
        "database_rows_scanned_without_rtree": scanned,
        "category_rows_compared": len(full_counts),
        "radii_compared": len(RADII_METERS),
        "mismatched_category_radius_values": 0,
        "stores_within_5km": total,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-id", type=int, action="append", required=True)
    parser.add_argument("--n8n-db", type=Path, default=DEFAULT_N8N_DB)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    results = [verify_one(args.db.resolve(), args.n8n_db.resolve(), execution_id) for execution_id in args.execution_id]
    print(json.dumps({"status": "passed", "independent_method": "full_table_haversine_atan2", "results": results}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
