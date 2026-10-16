#!/usr/bin/env python3
"""Build customer PDF/XLSX files from a successful local n8n execution."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from openpyxl import load_workbook

try:
    from . import build_deliverables as generator
except ImportError:  # Running this file directly from the scripts directory.
    import build_deliverables as generator


ROOT = Path(__file__).resolve().parents[1]
GATE_NODE = "Verify Results and Publication Gate"
DEFAULT_N8N_DB = Path.home() / ".n8n" / "database.sqlite"
LOCALAPPDATA_ROOT = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
DEFAULT_DELIVERY_ROOT = LOCALAPPDATA_ROOT / "GapAnalysis" / "deliveries"
MAX_EXECUTION_DATA_BYTES = 64 * 1024 * 1024


def decode_flatted(payload: str | bytes) -> Any:
    """Decode n8n's flatted execution_data column, including object cycles."""
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    try:
        table = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("n8n execution data is not valid UTF-8 flatted JSON.") from exc
    if not isinstance(table, list) or not table:
        raise ValueError("n8n execution data has an unexpected flatted root.")

    cache: dict[int, Any] = {}

    def resolve_index(index: int) -> Any:
        if index < 0 or index >= len(table):
            raise ValueError("n8n execution data contains an invalid reference.")
        if index in cache:
            return cache[index]
        value = table[index]
        if isinstance(value, dict):
            resolved: dict[str, Any] = {}
            cache[index] = resolved
            for key, item in value.items():
                resolved[key] = resolve_value(item)
            return resolved
        if isinstance(value, list):
            resolved_list: list[Any] = []
            cache[index] = resolved_list
            resolved_list.extend(resolve_value(item) for item in value)
            return resolved_list
        cache[index] = value
        return value

    def resolve_value(value: Any) -> Any:
        if isinstance(value, str) and value.isdecimal():
            return resolve_index(int(value))
        return value

    return resolve_index(0)


def extract_node_result(execution_data: Any, node_name: str) -> dict[str, Any]:
    if not isinstance(execution_data, dict):
        raise ValueError("Decoded n8n execution data must be an object.")
    result_data = execution_data.get("resultData")
    if not isinstance(result_data, dict):
        raise ValueError("Execution result data is missing or invalid.")
    run_data = result_data.get("runData")
    runs = run_data.get(node_name) if isinstance(run_data, dict) else None
    if not isinstance(runs, list) or not runs:
        raise ValueError(f"Node output is missing from the execution record: {node_name}.")
    main = (runs[-1].get("data") or {}).get("main")
    if not isinstance(main, list) or not main or not isinstance(main[0], list) or not main[0]:
        raise ValueError(f"Node produced no output item: {node_name}.")
    item = main[0][0]
    result = item.get("json") if isinstance(item, dict) else None
    if not isinstance(result, dict):
        raise ValueError(f"Node output is not a JSON object: {node_name}.")
    return result


def extract_gate_result(execution_data: Any) -> dict[str, Any]:
    if not isinstance(execution_data, dict):
        raise ValueError("Decoded n8n execution data must be an object.")
    result_data = execution_data.get("resultData")
    if not isinstance(result_data, dict) or result_data.get("lastNodeExecuted") != GATE_NODE:
        raise ValueError("Execution did not finish at the publication gate node.")
    return extract_node_result(execution_data, GATE_NODE)


def validate_execution_outputs(execution_data: Any) -> tuple[dict[str, Any], dict[str, int]]:
    """Require successful status codes from both HTTP nodes and the downstream validators."""
    result = extract_gate_result(execution_data)
    kakao = extract_node_result(execution_data, "Kakao Address")
    coordinate_check = extract_node_result(execution_data, "Validate Address and Coordinates")
    analyzer = extract_node_result(execution_data, "Analyze 247 Categories")
    if kakao.get("statusCode") != 200 or coordinate_check.get("kakao_http_status") != 200:
        raise ValueError("Kakao address lookup was not HTTP 200.")
    if analyzer.get("statusCode") != 200 or result.get("analyzer_http_status") != 200:
        raise ValueError("Analyzer response was not HTTP 200.")
    if result.get("geocode_match_count") != 1 or coordinate_check.get("geocode_match_count") != 1:
        raise ValueError("Address lookup did not produce one valid match.")
    return result, {
        "kakao_http_status": int(kakao["statusCode"]),
        "analyzer_http_status": int(analyzer["statusCode"]),
    }


def read_execution(db_path: Path, execution_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    if not db_path.is_file():
        raise FileNotFoundError(f"n8n database not found: {db_path}")
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        row = connection.execute(
            """SELECT e.id, e.status, e.mode, e.finished, d.data
               FROM execution_entity AS e
               JOIN execution_data AS d ON d.executionId = e.id
               WHERE e.id = ?""",
            (execution_id,),
        ).fetchone()
    except sqlite3.Error as exc:
        raise RuntimeError("Could not read the local n8n execution database schema.") from exc
    finally:
        connection.close()
    if row is None:
        raise ValueError(f"n8n execution {execution_id} was not found or has no saved data.")
    stored_id, status, mode, finished, serialized = row
    if status != "success" or finished != 1:
        raise ValueError(f"n8n execution {execution_id} is not a finished success.")
    if serialized is None or len(serialized) > MAX_EXECUTION_DATA_BYTES:
        raise ValueError("Saved n8n execution data is empty or exceeds the allowed size.")
    decoded = decode_flatted(serialized)
    result, http_statuses = validate_execution_outputs(decoded)
    return result, {
        "execution_id": stored_id,
        "mode": mode,
        "status": status,
        **http_statuses,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_output_dir(value: Path) -> Path:
    out_dir = value.resolve()
    allowed_roots = (ROOT.resolve(), DEFAULT_DELIVERY_ROOT.resolve())
    if not any(out_dir.is_relative_to(root) and out_dir != root for root in allowed_roots):
        raise ValueError("Output directory must be inside the project or the local GapAnalysis delivery folder.")
    if out_dir.exists():
        raise FileExistsError("Output directory already exists; refusing to overwrite it.")
    return out_dir


def generate_delivery(
    result: dict[str, Any], execution: dict[str, Any], output_dir: Path
) -> dict[str, Any]:
    taxonomy = json.loads(generator.TAXONOMY_PATH.read_text(encoding="utf-8"))
    validated = generator.validate_result(result, taxonomy)
    raw_request_id = result.get("request_id")
    if not isinstance(raw_request_id, str) or not raw_request_id.strip():
        raise ValueError("Analysis request id is missing or invalid.")
    request_id = generator._safe_request_id(raw_request_id)
    address = generator._display_address(result)
    out_dir = _safe_output_dir(output_dir)
    pdf = out_dir / f"공백상권_{request_id}_요약.pdf"
    xlsx = out_dir / f"공백상권_{request_id}_247업종.xlsx"
    created_dir = False
    try:
        out_dir.mkdir(parents=True, exist_ok=False)
        created_dir = True
        generator.build_pdf(result, validated, pdf)
        generator.build_xlsx(result, validated, xlsx)

        pdf_reader = PdfReader(str(pdf))
        pdf_text = "\n".join(page.extract_text() or "" for page in pdf_reader.pages)
        if len(pdf_reader.pages) != 2 or address not in pdf_text:
            raise ValueError("Generated PDF is missing the address or expected two-page layout.")

        workbook = load_workbook(xlsx, read_only=True, data_only=True)
        try:
            if workbook.sheetnames != ["요약", "전체 247개"]:
                raise ValueError("Generated XLSX has unexpected worksheet names.")
            summary = workbook["요약"]
            detail = workbook["전체 247개"]
            summary_values = [
                str(cell.value)
                for row in summary.iter_rows()
                for cell in row
                if cell.value is not None
            ]
            if not any(address in value for value in summary_values):
                raise ValueError("Generated XLSX summary is missing the address.")
            if detail.max_row != 251:
                raise ValueError("Generated XLSX does not contain all 247 detail rows.")
        finally:
            workbook.close()

        files = [
            {"name": pdf.name, "bytes": pdf.stat().st_size, "sha256": _sha256(pdf)},
            {"name": xlsx.name, "bytes": xlsx.stat().st_size, "sha256": _sha256(xlsx)},
        ]
        manifest = {
            "status": "validated_and_written",
            "exported_at_utc": datetime.now(timezone.utc).isoformat(),
            "request_id": result["request_id"],
            "n8n_execution_id": execution["execution_id"],
            "n8n_execution_mode": execution["mode"],
            "kakao_http_status": execution["kakao_http_status"],
            "analyzer_http_status": execution["analyzer_http_status"],
            "analysis_generated_at": result["generated_at"],
            "source_as_of": result["source"]["source_as_of"],
            "snapshot_id": result["source"]["snapshot_id"],
            "taxonomy_version": result["source"]["taxonomy_version"],
            "analysis_engine_version": result["source"]["analysis_engine_version"],
            "category_rows": len(validated["categories"]),
            "overall_top20_rows": len(result["overall_top20"]),
            "a_top10_rows": len(result["a_top10"]),
            "total_5km": validated["total_5km"],
            "publication_gate": result["publication_gate"],
            "pdf_pages": len(pdf_reader.pages),
            "files": files,
        }
        temporary_manifest = out_dir / "manifest.json.tmp"
        temporary_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary_manifest.replace(out_dir / "manifest.json")
        return {
            "status": manifest["status"],
            "request_id": result["request_id"],
            "n8n_execution_id": execution["execution_id"],
            "kakao_http_status": execution["kakao_http_status"],
            "analyzer_http_status": execution["analyzer_http_status"],
            "publication_gate": result["publication_gate"],
            "category_rows": manifest["category_rows"],
            "overall_top20_rows": manifest["overall_top20_rows"],
            "a_top10_rows": manifest["a_top10_rows"],
            "total_5km": manifest["total_5km"],
            "pdf_pages": manifest["pdf_pages"],
            # Customer files default to LOCALAPPDATA, outside the project tree.
            "output_dir": str(out_dir),
            "files": files,
        }
    except Exception:
        allowed_roots = (ROOT.resolve(), DEFAULT_DELIVERY_ROOT.resolve())
        if created_dir and out_dir.exists() and any(out_dir.is_relative_to(root) and out_dir != root for root in allowed_roots):
            shutil.rmtree(out_dir)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-id", required=True, type=int)
    parser.add_argument("--db", type=Path, default=DEFAULT_N8N_DB)
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="new destination inside the project or local GapAnalysis delivery folder",
    )
    parser.add_argument("--check-only", action="store_true", help="validate execution without writing files")
    args = parser.parse_args()
    result, execution = read_execution(args.db, args.execution_id)
    taxonomy = json.loads(generator.TAXONOMY_PATH.read_text(encoding="utf-8"))
    validated = generator.validate_result(result, taxonomy)
    generator._display_address(result)
    if args.check_only:
        print(json.dumps({
            "status": "validated",
            "n8n_execution_id": execution["execution_id"],
            "kakao_http_status": execution["kakao_http_status"],
            "analyzer_http_status": execution["analyzer_http_status"],
            "request_id": result["request_id"],
            "publication_gate": result["publication_gate"],
            "category_rows": len(validated["categories"]),
            "overall_top20_rows": len(result["overall_top20"]),
            "a_top10_rows": len(result["a_top10"]),
            "total_5km": validated["total_5km"],
            "wrote_files": False,
            "default_output_dir": str(DEFAULT_DELIVERY_ROOT / generator._safe_request_id(str(result.get("request_id") or "analysis"))),
        }, ensure_ascii=False))
        return 0
    request_id = generator._safe_request_id(str(result.get("request_id") or "analysis"))
    output_dir = args.output_dir or (DEFAULT_DELIVERY_ROOT / request_id)
    summary = generate_delivery(result, execution, output_dir)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
