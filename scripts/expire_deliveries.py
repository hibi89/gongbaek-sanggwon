#!/usr/bin/env python3
"""Remove expired local delivery folders after their n8n execution was deleted.

The default mode is a read-only preview. Use --apply only on or after the
delivery-date-based expiry date. The script refuses to remove local files while
the corresponding n8n execution row is still present.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LEDGER_PATH = ROOT / "운영" / "30일_보관_삭제_대장.csv"
DEFAULT_N8N_DB = Path.home() / ".n8n" / "database.sqlite"
LOCALAPPDATA_ROOT = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
DEFAULT_DELIVERY_ROOT = LOCALAPPDATA_ROOT / "GapAnalysis" / "deliveries"

LEDGER_COLUMNS = (
    "분석번호",
    "n8n_실행ID",
    "고객_전달일",
    "삭제_예정일",
    "삭제_확인일",
    "처리상태",
)
ACTIVE_STATUS = "보관중"
COMPLETE_STATUS = "삭제완료"
ANALYSIS_ID_RE = re.compile(r"^GAP-[A-Za-z0-9_-]{1,80}$")
EXECUTION_ID_RE = re.compile(r"^[0-9]{1,20}$")


def _parse_date(value: str, field: str, analysis_id: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be YYYY-MM-DD for {analysis_id}.") from exc


def _load_ledger(path: Path) -> tuple[list[str], list[dict[str, str]], str]:
    if not path.is_file():
        raise FileNotFoundError(f"Retention ledger not found: {path}")
    raw = path.read_bytes()
    encoding = "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8"
    with path.open("r", encoding=encoding, newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or not set(LEDGER_COLUMNS).issubset(reader.fieldnames):
            raise ValueError("Retention ledger is missing required columns.")
        rows = [{key: value or "" for key, value in row.items()} for row in reader]
        return list(reader.fieldnames), rows, encoding


def _execution_exists(db_path: Path, execution_id: str) -> bool:
    if not db_path.is_file():
        raise FileNotFoundError(f"n8n database not found: {db_path}")
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        row = connection.execute(
            "SELECT 1 FROM execution_entity WHERE CAST(id AS TEXT) = ? LIMIT 1",
            (execution_id,),
        ).fetchone()
    except sqlite3.Error as exc:
        raise RuntimeError("Could not inspect the n8n execution table read-only.") from exc
    finally:
        connection.close()
    return row is not None


def _safe_order_dir(delivery_root: Path, analysis_id: str) -> Path:
    if not ANALYSIS_ID_RE.fullmatch(analysis_id):
        raise ValueError("Analysis ID must use the GAP-<safe-id> format.")
    resolved_root = delivery_root.resolve()
    target = delivery_root / analysis_id
    if target.is_symlink():
        raise ValueError(f"Refusing a symlinked delivery folder for {analysis_id}.")
    resolved_target = target.resolve()
    try:
        resolved_target.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"Delivery path escapes its root for {analysis_id}.") from exc
    if resolved_target == resolved_root:
        raise ValueError(f"Delivery path resolves to the root for {analysis_id}.")
    return resolved_target


def _write_ledger(path: Path, columns: list[str], rows: list[dict[str, str]], encoding: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding=encoding, newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def process_expirations(
    *,
    ledger_path: Path = LEDGER_PATH,
    n8n_db: Path = DEFAULT_N8N_DB,
    delivery_root: Path = DEFAULT_DELIVERY_ROOT,
    today: date | None = None,
    apply: bool = False,
) -> list[dict[str, Any]]:
    """Preview or apply due file cleanup; never delete a retained n8n row."""
    as_of = today or date.today()
    columns, rows, encoding = _load_ledger(ledger_path)
    seen_ids: set[str] = set()
    seen_execution_ids: set[str] = set()
    due: list[tuple[int, str, str, date, Path]] = []
    results: list[dict[str, Any]] = []

    for index, row in enumerate(rows):
        if not any(value.strip() for value in row.values()):
            continue
        status = row.get("처리상태", "").strip()
        if status == COMPLETE_STATUS:
            continue
        if status != ACTIVE_STATUS:
            results.append({"ledger_row": index + 2, "status": "manual_review_unknown_status"})
            continue

        analysis_id = row.get("분석번호", "").strip()
        execution_id = row.get("n8n_실행ID", "").strip()
        if not ANALYSIS_ID_RE.fullmatch(analysis_id):
            raise ValueError("An active ledger row has an invalid analysis ID.")
        if analysis_id in seen_ids:
            raise ValueError(f"Duplicate active analysis ID: {analysis_id}")
        seen_ids.add(analysis_id)
        if not EXECUTION_ID_RE.fullmatch(execution_id):
            raise ValueError(f"An active ledger row has an invalid execution ID for {analysis_id}.")
        if execution_id in seen_execution_ids:
            raise ValueError("An active n8n execution ID appears more than once in the ledger.")
        seen_execution_ids.add(execution_id)

        delivered = _parse_date(row.get("고객_전달일", ""), "고객_전달일", analysis_id)
        expires = _parse_date(row.get("삭제_예정일", ""), "삭제_예정일", analysis_id)
        if expires != delivered + timedelta(days=30):
            raise ValueError(f"Expiry must be exactly 30 days after delivery for {analysis_id}.")
        if expires > as_of:
            results.append({"analysis_id": analysis_id, "execution_id": execution_id, "status": "not_due"})
            continue

        order_dir = _safe_order_dir(delivery_root, analysis_id)
        due.append((index, analysis_id, execution_id, expires, order_dir))

    # Read all due execution states before deleting any files.
    execution_states = {
        execution_id: _execution_exists(n8n_db, execution_id)
        for _, _, execution_id, _, _ in due
    }

    changed = False
    for index, analysis_id, execution_id, expires, order_dir in due:
        if execution_states[execution_id]:
            results.append({
                "analysis_id": analysis_id,
                "execution_id": execution_id,
                "status": "delete_n8n_execution_first",
            })
            continue

        if not apply:
            results.append({
                "analysis_id": analysis_id,
                "execution_id": execution_id,
                "status": "would_delete_files",
                "folder_exists": order_dir.exists(),
            })
            continue

        if order_dir.exists():
            if not order_dir.is_dir():
                raise ValueError(f"Delivery path is not a directory for {analysis_id}.")
            shutil.rmtree(order_dir)
        if order_dir.exists():
            raise OSError(f"Delivery folder still exists after removal for {analysis_id}.")

        rows[index]["삭제_확인일"] = as_of.isoformat()
        rows[index]["처리상태"] = COMPLETE_STATUS
        changed = True
        results.append({
            "analysis_id": analysis_id,
            "execution_id": execution_id,
            "status": COMPLETE_STATUS,
            "folder_exists": False,
        })

    if changed:
        _write_ledger(ledger_path, columns, rows, encoding)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=LEDGER_PATH)
    parser.add_argument("--n8n-db", type=Path, default=DEFAULT_N8N_DB)
    parser.add_argument("--delivery-root", type=Path, default=DEFAULT_DELIVERY_ROOT)
    parser.add_argument("--apply", action="store_true", help="delete due files and record completion")
    args = parser.parse_args()
    try:
        results = process_expirations(
            ledger_path=args.ledger,
            n8n_db=args.n8n_db,
            delivery_root=args.delivery_root,
            apply=args.apply,
        )
    except (FileNotFoundError, RuntimeError, ValueError, OSError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"mode": "apply" if args.apply else "dry-run", "results": results}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
