#!/usr/bin/env python3
"""Count observed store records at 1-5 km and rank overall/A sparse categories."""

from __future__ import annotations

import argparse
import base64
import json
import math
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from .korea_coordinates import is_korea_plausible
    from .taxonomy_integrity import verify_taxonomy
except ImportError:  # Running this file directly from the scripts directory.
    from korea_coordinates import is_korea_plausible
    from taxonomy_integrity import verify_taxonomy


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "processed" / "semas_store_20260630.sqlite"
DEFAULT_RESULTS_DIR = ROOT / "data" / "results"
TAXONOMY_PATH = ROOT / "설계" / "03_업종분류표.json"
RADIUS_METERS = (1000, 2000, 3000, 4000, 5000)
EARTH_RADIUS_METERS = 6_371_008.8
BOUNDARY_TOLERANCE_METERS = 1e-7
ENGINE_VERSION = "0.1.0"


def load_current_taxonomy() -> dict[str, Any]:
    taxonomy = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8-sig"))
    verify_taxonomy(taxonomy)
    return taxonomy


def current_taxonomy_version() -> str:
    taxonomy = load_current_taxonomy()
    version = str(taxonomy.get("taxonomy_version", "")).strip()
    if not version:
        raise ValueError("Current taxonomy version is missing")
    return version


def current_taxonomy_hash() -> str:
    return verify_taxonomy(load_current_taxonomy())


def validate_database_taxonomy(
    connection: sqlite3.Connection,
    meta: dict[str, str],
    taxonomy: dict[str, Any],
) -> list[dict[str, Any]]:
    expected_hash = verify_taxonomy(taxonomy)
    if meta.get("taxonomy_hash") != expected_hash:
        raise ValueError("Snapshot database taxonomy content hash is stale")
    if meta.get("taxonomy_version") != taxonomy.get("taxonomy_version"):
        raise ValueError("Snapshot database taxonomy version is stale")

    rows = [dict(row) for row in connection.execute(
        "SELECT code,name,large_code,medium_code,abc_tag,classification_basis FROM categories ORDER BY code"
    )]
    expected = {item["code"]: item for item in taxonomy.get("categories", [])}
    if len(expected) != 247 or len(rows) != 247 or len({row["code"] for row in rows}) != 247:
        raise ValueError("Snapshot database does not contain exactly 247 unique categories")
    for row in rows:
        category = expected.get(row["code"])
        if category is None:
            raise ValueError(f"Snapshot database contains a category absent from taxonomy: {row['code']}")
        if row["name"] != category.get("name") or row["abc_tag"] != category.get("abc_tag"):
            raise ValueError(f"Snapshot database category name or ABC tag differs from taxonomy: {row['code']}")
    if set(expected) != {row["code"] for row in rows}:
        raise ValueError("Snapshot database category codes differ from taxonomy")
    return rows


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_METERS * math.asin(min(1.0, math.sqrt(max(0.0, a))))


def bounding_box(lat: float, lon: float, radius_m: float) -> tuple[float, float, list[tuple[float, float]]]:
    angular = radius_m / EARTH_RADIUS_METERS
    lat_delta = math.degrees(angular)
    south = max(-90.0, lat - lat_delta)
    north = min(90.0, lat + lat_delta)
    if south <= -90.0 or north >= 90.0:
        longitude_boxes = [(-180.0, 180.0)]
    else:
        ratio = min(1.0, math.sin(angular) / max(1e-15, math.cos(math.radians(lat))))
        lon_delta = math.degrees(math.asin(ratio))
        raw_west = lon - lon_delta
        raw_east = lon + lon_delta
        if raw_west < -180.0:
            longitude_boxes = [(-180.0, raw_east), (raw_west + 360.0, 180.0)]
        elif raw_east > 180.0:
            longitude_boxes = [(raw_west, 180.0), (-180.0, raw_east - 360.0)]
        else:
            longitude_boxes = [(raw_west, raw_east)]
    # R*Tree stores coordinates as float32 and rounds rectangles outward.
    pad = 1e-5
    return (
        max(-90.0, south - pad),
        min(90.0, north + pad),
        [(max(-180.0, west - pad), min(180.0, east + pad)) for west, east in longitude_boxes],
    )


def count_points(
    rows: Iterable[tuple[str, float, float]],
    categories: list[dict[str, str]],
    center_lat: float,
    center_lon: float,
) -> tuple[dict[str, list[int]], int, int]:
    counts = {category["code"]: [0, 0, 0, 0, 0] for category in categories}
    candidate_rows = included_rows = 0
    for code, lon, lat in rows:
        candidate_rows += 1
        distance = haversine_m(center_lon, center_lat, lon, lat)
        if distance > RADIUS_METERS[-1] and not math.isclose(
            distance, RADIUS_METERS[-1], rel_tol=0.0, abs_tol=BOUNDARY_TOLERANCE_METERS
        ):
            continue
        if code not in counts:
            raise ValueError(f"Store row contains an unmapped category code: {code}")
        included_rows += 1
        for index, radius in enumerate(RADIUS_METERS):
            if distance <= radius or math.isclose(distance, radius, rel_tol=0.0, abs_tol=BOUNDARY_TOLERANCE_METERS):
                counts[code][index] += 1
    for code, values in counts.items():
        if any(left > right for left, right in zip(values, values[1:])):
            raise ValueError(f"Cumulative counts are not monotonic for {code}: {values}")
    return counts, candidate_rows, included_rows


def ranked_categories(categories: list[dict[str, str]], counts: dict[str, list[int]], tag: str | None = None) -> list[dict[str, Any]]:
    population = [category for category in categories if tag is None or category["abc_tag"] == tag]
    population.sort(key=lambda category: (*reversed(counts[category["code"]]), category["code"]))
    return [
        {
            "rank": rank,
            "code": category["code"],
            "name": category["name"],
            "abc_tag": category["abc_tag"],
            "counts": {f"{radius // 1000}km": counts[category["code"]][index] for index, radius in enumerate(RADIUS_METERS)},
        }
        for rank, category in enumerate(population, start=1)
    ]


def query_candidates(connection: sqlite3.Connection, lat: float, lon: float) -> tuple[list[tuple[str, float, float]], int]:
    south, north, boxes = bounding_box(lat, lon, RADIUS_METERS[-1])

    rows: list[tuple[str, float, float]] = []
    seen_ids: set[int] = set()
    for box_west, box_east in boxes:
        cursor = connection.execute(
            """
            SELECT s.id, s.category_code, s.longitude, s.latitude
            FROM store_spatial AS r
            JOIN stores AS s ON s.id = r.id
            WHERE r.min_lon <= ? AND r.max_lon >= ?
              AND r.min_lat <= ? AND r.max_lat >= ?
            """,
            (box_east, box_west, north, south),
        )
        for row_id, code, store_lon, store_lat in cursor:
            if row_id in seen_ids:
                continue
            seen_ids.add(row_id)
            rows.append((code, store_lon, store_lat))
    return rows, len(seen_ids)


def run_analysis(db_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        lat = float(payload["latitude"])
        lon = float(payload["longitude"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("latitude and longitude must be numeric") from exc
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Coordinates are outside WGS84 longitude/latitude bounds")
    if not is_korea_plausible(lon, lat):
        raise ValueError("Coordinates are outside the Korea plausibility bounds")

    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        meta = {row["key"]: row["value"] for row in connection.execute("SELECT key,value FROM meta")}
        if meta.get("database_state") != "complete":
            raise ValueError("Snapshot database is not complete")
        taxonomy = load_current_taxonomy()
        categories = validate_database_taxonomy(connection, meta, taxonomy)
        allowed_tags = {"A", "B", "C", "미분류"}
        if not categories or any(item["abc_tag"] not in allowed_tags for item in categories):
            raise ValueError("Category taxonomy contains a missing or unsupported ABC tag")
        a_categories = [item for item in categories if item["abc_tag"] == "A"]
        unclassified_categories = [item for item in categories if item["abc_tag"] == "미분류"]
        if len(a_categories) < 10:
            raise ValueError("Fewer than 10 A-tag categories are available")
        store_count = int(meta.get("store_rows", "-1"))
        if connection.execute("SELECT COUNT(*) FROM stores").fetchone()[0] != store_count:
            raise ValueError("Stored row count differs from the complete snapshot manifest")

        candidate_rows, candidate_count = query_candidates(connection, lat, lon)
        counts, count_candidates, included_rows = count_points(candidate_rows, categories, lat, lon)
        if count_candidates != candidate_count:
            raise ValueError("Spatial candidate count changed while counting")
        all_ranked = ranked_categories(categories, counts)
        a_ranked = ranked_categories(categories, counts, tag="A")
        if len(all_ranked) != 247 or len(a_ranked) < 10:
            raise ValueError("Cannot fulfill the promised complete rankings")

        taxonomy_state = meta.get("abc_status", "unclassified")
        if unclassified_categories:
            publication_gate = "blocked_unclassified_taxonomy"
        elif taxonomy_state != "owner_approved":
            publication_gate = "blocked_abc_owner_review"
        else:
            publication_gate = "passed"
        owner_approved = publication_gate == "passed"
        request_id = str(payload.get("request_id") or uuid.uuid4())
        return {
            "status": "analysis_complete",
            "publishable": owner_approved,
            "publication_gate": publication_gate,
            "request_id": request_id,
            "address": str(payload.get("address", "")).strip(),
            "normalized_address": str(payload.get("normalized_address", payload.get("address", ""))).strip(),
            "geocode_match_count": int(payload.get("geocode_match_count", 1)),
            "center": {"latitude": lat, "longitude": lon, "crs": "EPSG:4326"},
            "radii_m": list(RADIUS_METERS),
            "distance_method": "haversine_mean_sphere",
            "boundary_rule": "distance <= radius; floating-point tolerance 1e-7 m",
            "source": {
                "snapshot_id": meta["snapshot_id"],
                "source_as_of": meta["source_as_of"],
                "source_url": meta["source_url"],
                "taxonomy_version": meta["taxonomy_version"],
                "taxonomy_hash": meta["taxonomy_hash"],
                "analysis_engine_version": ENGINE_VERSION,
                "snapshot_store_rows": store_count,
            },
            "coverage": {
                "category_count": len(categories),
                "a_category_count": len(a_categories),
                "unclassified_category_count": len(unclassified_categories),
                "spatial_candidate_rows": candidate_count,
                "stores_within_5km": included_rows,
            },
            "overall_top20": all_ranked[:20],
            "a_top10": a_ranked[:10],
            "all_category_counts": [
                {
                    "code": category["code"],
                    "name": category["name"],
                    "abc_tag": category["abc_tag"],
                    "counts": {f"{radius // 1000}km": counts[category["code"]][index] for index, radius in enumerate(RADIUS_METERS)},
                }
                for category in categories
            ],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        connection.close()


def decode_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.input_b64:
        try:
            return json.loads(base64.b64decode(args.input_b64, validate=True).decode("utf-8"))
        except Exception as exc:
            raise ValueError("input-b64 must contain base64-encoded UTF-8 JSON") from exc
    if args.input_json:
        return json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    if args.latitude is None or args.longitude is None:
        raise ValueError("Provide --input-b64, --input-json, or both --latitude and --longitude")
    return {
        "request_id": args.request_id,
        "address": args.address,
        "latitude": args.latitude,
        "longitude": args.longitude,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--input-b64")
    parser.add_argument("--input-json")
    parser.add_argument("--request-id")
    parser.add_argument("--address", default="")
    parser.add_argument("--latitude", type=float)
    parser.add_argument("--longitude", type=float)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        payload = decode_payload(args)
        result = run_analysis(args.db.resolve(), payload)
        if args.output:
            output_path = args.output.resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        else:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        error = {
            "status": "analysis_failed",
            "publishable": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        print(json.dumps(error, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
