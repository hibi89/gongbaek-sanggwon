#!/usr/bin/env python3
"""Fetch an approved ZIP via Git binary objects and verify it before local use.

Reads Git objects as raw bytes; never modifies working tree, n8n, SQLite, or credentials.
Requires Python 3.9+ and an authenticated Git CLI pointing to the private repository.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
import zipfile

ASSET_PATH = (
    "판매자료/크몽_최종등록_20261017/"
    "KMONG_840642_FINAL_CORRECTED_247_20261017.zip"
)
ASSET_NAME = "KMONG_840642_FINAL_CORRECTED_247_20261017.zip"
EXPECTED_SIZE = 1_061_871
EXPECTED_SHA256 = "13dafbac46b3ef7026bbac4b6bb9229183e7c49fda03640fb66425f07f5581cd"
EXPECTED_BLOB = "9e5ac1caa20676be27eaf7e59688f6bf8ecbeb33"
EXPECTED_FILES = 15
EXPECTED_MANIFEST_FILES = 14


class TransferError(RuntimeError):
    pass


def git(repo: Path, *args: str) -> bytes:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=180,
        )
    except FileNotFoundError as exc:
        raise TransferError("Git CLI is not installed or not in PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise TransferError(f"Git operation timed out: {args[0]}") from exc
    if proc.returncode:
        details = proc.stderr.decode("utf-8", "replace")[-700:]
        raise TransferError(f"Git {args[0]} failed ({proc.returncode}): {details}")
    return proc.stdout  # Never decode raw ZIP bytes as text.


def verify_zip(data: bytes) -> dict:
    if len(data) != EXPECTED_SIZE:
        raise TransferError(f"Size mismatch: got {len(data)}, expected {EXPECTED_SIZE}")
    actual = hashlib.sha256(data).hexdigest()
    if actual != EXPECTED_SHA256:
        raise TransferError(f"SHA-256 mismatch: {actual}")

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            names = [info.filename for info in infos if not info.is_dir()]
            if len(infos) != EXPECTED_FILES or len(names) != EXPECTED_FILES:
                raise TransferError(f"Unexpected ZIP entry count: {len(names)}/{len(infos)}")
            if len(set(names)) != len(names):
                raise TransferError("Duplicate ZIP entry names")
            if any(
                name.startswith("/") or "\\" in name or ".." in PurePosixPath(name).parts
                for name in names
            ):
                raise TransferError("Unsafe ZIP entry path")
            bad = zf.testzip()
            if bad is not None:
                raise TransferError(f"ZIP CRC failure: {bad}")

            manifest_paths = [name for name in names if name.endswith("/MANIFEST_SHA256.txt")]
            if len(manifest_paths) != 1:
                raise TransferError("Expected exactly one MANIFEST_SHA256.txt")
            manifest_path = manifest_paths[0]
            prefix = manifest_path[: -len("MANIFEST_SHA256.txt")]
            manifest_text = zf.read(manifest_path).decode("utf-8-sig")
            manifest = {}
            for line in manifest_text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line)
                if not match:
                    raise TransferError("Invalid manifest line format")
                digest, relative_name = match.groups()
                if relative_name in manifest:
                    raise TransferError(f"Duplicate manifest path: {relative_name}")
                manifest[relative_name] = digest.lower()
            if len(manifest) != EXPECTED_MANIFEST_FILES:
                raise TransferError(f"Expected 14 manifest entries, got {len(manifest)}")
            for name in names:
                if name == manifest_path:
                    continue
                if not name.startswith(prefix):
                    raise TransferError(f"Unexpected ZIP root: {name}")
                relative_name = name[len(prefix):]
                if manifest.get(relative_name) != hashlib.sha256(zf.read(name)).hexdigest():
                    raise TransferError(f"Manifest SHA-256 mismatch: {relative_name}")
    except (zipfile.BadZipFile, UnicodeError) as exc:
        raise TransferError(f"ZIP format or manifest error: {exc}") from exc
    return {
        "bytes": len(data),
        "sha256": actual,
        "zip_crc": "PASS",
        "zip_files": len(names),
        "manifest_sha256": "14/14 PASS",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="Use an already fetched Git blob for offline verification",
    )
    args = parser.parse_args()
    repo = args.repo.expanduser().resolve()
    try:
        if not (repo / ".git").exists():
            raise TransferError(f"Not a local Git worktree: {repo}")
        if not args.no_fetch:
            # Fetch updates FETCH_HEAD, not checked-out files or branches.
            git(repo, "fetch", "--no-tags", "origin", "main")
            blob = git(repo, "rev-parse", f"FETCH_HEAD:{ASSET_PATH}").decode("ascii").strip()
            if blob != EXPECTED_BLOB:
                raise TransferError(f"Git blob differs from approved package: {blob}")
        else:
            blob = EXPECTED_BLOB
        data = git(repo, "cat-file", "blob", blob)
        result = verify_zip(data)

        base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
        out_dir = (
            args.out_dir.expanduser().resolve()
            if args.out_dir
            else Path(base) / "GapAnalysis" / "verified_imports"
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        destination = out_dir / ASSET_NAME
        if destination.exists():
            with destination.open("rb") as fh:
                current_sha = hashlib.sha256(fh.read()).hexdigest()
            if current_sha != EXPECTED_SHA256:
                raise TransferError(f"Refusing to overwrite a different file: {destination}")
        else:
            temp_name = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=out_dir,
                    prefix=".kmong-transfer-",
                    suffix=".tmp",
                    delete=False,
                ) as temp:
                    temp.write(data)
                    temp.flush()
                    os.fsync(temp.fileno())
                    temp_name = temp.name
                os.replace(temp_name, destination)
            finally:
                if temp_name and os.path.exists(temp_name):
                    os.unlink(temp_name)

        with destination.open("rb") as fh:
            final_sha = hashlib.sha256(fh.read()).hexdigest()
        if final_sha != EXPECTED_SHA256:
            raise TransferError("Saved file did not retain the verified SHA-256")
        print(json.dumps({
            "status": "PASS",
            "source": "Git object (raw binary)",
            "saved_to": str(destination),
            "git_blob": blob,
            **result,
        }, ensure_ascii=False))
        return 0
    except TransferError as exc:
        print(json.dumps({"status": "FAIL", "reason": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
