#!/usr/bin/env python3
"""Serve the local gap analyzer to n8n over an authenticated loopback HTTP API."""

from __future__ import annotations

import argparse
import hmac
import json
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

try:
    from .analyze_gap import (
        DEFAULT_DB,
        load_current_taxonomy,
        run_analysis,
        validate_database_taxonomy,
    )
    from .taxonomy_integrity import verify_taxonomy
except ImportError:  # Running this file directly from the scripts directory.
    from analyze_gap import DEFAULT_DB, load_current_taxonomy, run_analysis, validate_database_taxonomy
    from taxonomy_integrity import verify_taxonomy


MAX_BODY_BYTES = 64 * 1024


class AnalyzerHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler], db_path: Path, token: str) -> None:
        super().__init__(address, handler)
        self.db_path = db_path
        self.token = token


class Handler(BaseHTTPRequestHandler):
    server: AnalyzerHTTPServer

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        expected = f"Bearer {self.server.token}"
        received = self.headers.get("Authorization", "")
        return hmac.compare_digest(received, expected)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self.path != "/health":
            self._send_json(404, {"status": "not_found"})
            return
        try:
            connection = sqlite3.connect(f"file:{self.server.db_path.as_posix()}?mode=ro", uri=True)
            try:
                meta = dict(connection.execute("SELECT key,value FROM meta"))
                category_count = connection.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
                taxonomy = load_current_taxonomy()
                expected_hash = verify_taxonomy(taxonomy)
            finally:
                connection.close()
            expected_taxonomy = str(taxonomy.get("taxonomy_version", ""))
            database_taxonomy = meta.get("taxonomy_version")
            reason = None
            if meta.get("database_state") != "complete":
                reason = "snapshot_incomplete"
            elif category_count != 247:
                reason = "category_count_mismatch"
            elif meta.get("taxonomy_hash") != expected_hash:
                reason = "stale_taxonomy_hash"
            elif database_taxonomy != expected_taxonomy:
                reason = "stale_taxonomy_version"
            else:
                try:
                    # Verify per-code display name and ABC tag before reporting ready.
                    connection = sqlite3.connect(f"file:{self.server.db_path.as_posix()}?mode=ro", uri=True)
                    connection.row_factory = sqlite3.Row
                    try:
                        validate_database_taxonomy(connection, meta, taxonomy)
                    finally:
                        connection.close()
                except ValueError:
                    reason = "taxonomy_category_mismatch"
            ready = reason is None
            self._send_json(200 if ready else 503, {
                "status": "ready" if ready else "not_ready",
                "reason": reason,
                "snapshot_id": meta.get("snapshot_id"),
                "source_as_of": meta.get("source_as_of"),
                "category_count": category_count,
                "taxonomy_version": database_taxonomy,
                "expected_taxonomy_version": expected_taxonomy,
                "taxonomy_hash": meta.get("taxonomy_hash"),
                "expected_taxonomy_hash": expected_hash,
                "abc_status": meta.get("abc_status"),
            })
        except Exception as exc:
            self._send_json(503, {"status": "not_ready", "error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self.path != "/analyze":
            self._send_json(404, {"status": "not_found"})
            return
        if not self._authorized():
            self._send_json(401, {"status": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > MAX_BODY_BYTES:
                raise ValueError("Request body must be between 1 byte and 64 KiB")
            if "application/json" not in self.headers.get("Content-Type", "").lower():
                raise ValueError("Content-Type must be application/json")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("JSON request body must be an object")
            result = run_analysis(self.server.db_path, payload)
            self._send_json(200, result)
        except ValueError as exc:
            self._send_json(400, {"status": "analysis_failed", "publishable": False, "error": str(exc)})
        except Exception as exc:
            self._send_json(503, {"status": "analysis_failed", "publishable": False, "error_type": type(exc).__name__, "error": str(exc)})

    def log_message(self, format: str, *args: Any) -> None:
        # Keep logs to method/path/status; never log request bodies or auth headers.
        super().log_message(format, *args)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    token = os.environ.get("GAP_ANALYZER_TOKEN", "").strip()
    if len(token) < 32:
        raise SystemExit("Set GAP_ANALYZER_TOKEN to a random value of at least 32 characters.")
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        print("Warning: listening beyond loopback. Restrict access with a private container network and firewall.")

    server = AnalyzerHTTPServer((args.host, args.port), Handler, args.db.resolve(), token)
    print(f"Gap analyzer listening at http://{args.host}:{args.port}")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
