#!/usr/bin/env python3
"""Transfer an approved ZIP from a private GitHub repo without an existing .git directory.

Tries isolated temporary git clone (no checkout) then an authenticated gh API call.
Verifies exact blob, SHA256, ZIP CRC and every manifest SHA256 before saving.
Never changes the user's working directory, Git checkout, n8n, databases or credentials.
"""
from __future__ import annotations

import argparse
import base64
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

REPOSITORY = "hibi89/gongbaek-sanggwon"
REMOTE_URL = f"https://github.com/{REPOSITORY}.git"
ASSET_PATH = (
    "판매자료/크몽_최종등록_20261017/"
    "KMONG_840642_FINAL_CORRECTED_247_20261017.zip"
)
ASSET_NAME = "KMONG_840642_FINAL_CORRECTED_247_20261017.zip"
EXPECTED_BYTES = 1_061_871
EXPECTED_SHA256 = "13dafbac46b3ef7026bbac4b6bb9229183e7c49fda03640fb66425f07f5581cd"
EXPECTED_GIT_BLOB = "9e5ac1caa20676be27eaf7e59688f6bf8ecbeb33"


class TransferError(RuntimeError):
    pass


def process(argv: list[str], *, timeout: int = 120) -> bytes:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    try:
        job = subprocess.run(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=False, timeout=timeout, env=env,
        )
    except FileNotFoundError as exc:
        raise TransferError(f"Command unavailable: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise TransferError(f"Command timeout: {argv[0]}") from exc
    if job.returncode:
        # Never print full stderr: it can include credential-bearing URL strings.
        raise TransferError(f"{argv[0]} command failed (exit {job.returncode})")
    return job.stdout


def fetch_with_temporary_git(remote: str) -> bytes:
    with tempfile.TemporaryDirectory(prefix="gap_asset_transfer_") as temp:
        clone_dir = Path(temp) / "git-readonly-copy"
        process([
            "git", "clone", "--quiet", "--no-checkout", "--depth", "1",
            "--single-branch", "--branch", "main", "--", remote, str(clone_dir),
        ], timeout=180)
        blob = process(["git", "-C", str(clone_dir), "rev-parse", f"HEAD:{ASSET_PATH}"])
        blob_sha = blob.decode("ascii").strip()
        if blob_sha != EXPECTED_GIT_BLOB:
            raise TransferError("Git remote contains a different blob than the approved ZIP")
        return process(["git", "-C", str(clone_dir), "cat-file", "blob", blob_sha])


def fetch_with_gh() -> bytes:
    response = process([
        "gh", "api", "--method", "GET",
        f"repos/{REPOSITORY}/git/blobs/{EXPECTED_GIT_BLOB}",
    ], timeout=180)
    try:
        payload = json.loads(response.decode("utf-8"))
        if payload.get("sha") != EXPECTED_GIT_BLOB or payload.get("encoding") != "base64":
            raise TransferError("GitHub API returned an unexpected blob or encoding")
        return base64.b64decode(payload["content"], validate=False)
    except (KeyError, ValueError, UnicodeError) as exc:
        raise TransferError("Could not decode binary blob from GitHub API") from exc


def verify_zip_bytes(data: bytes) -> dict:
    if len(data) != EXPECTED_BYTES:
        raise TransferError(f"ZIP size mismatch: {len(data)} vs {EXPECTED_BYTES}")
    digest = hashlib.sha256(data).hexdigest()
    if digest != EXPECTED_SHA256:
        raise TransferError(f"ZIP SHA256 mismatch: {digest}")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            names = [v.filename for v in infos if not v.is_dir()]
            if len(infos) != 15 or len(names) != 15 or len(set(names)) != 15:
                raise TransferError("ZIP entry count / duplicate entries mismatch")
            if any(n.startswith("/") or "\\" in n or ".." in PurePosixPath(n).parts for n in names):
                raise TransferError("Unsafe ZIP entry path")
            damaged = zf.testzip()
            if damaged:
                raise TransferError(f"ZIP CRC mismatch in {damaged}")
            manifest_path = next((n for n in names if n.endswith("/MANIFEST_SHA256.txt")), None)
            if not manifest_path:
                raise TransferError("ZIP manifest not found")
            root = manifest_path[:-len("MANIFEST_SHA256.txt")]
            manifest = {}
            for line in zf.read(manifest_path).decode("utf-8-sig").splitlines():
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line.strip())
                if not match:
                    raise TransferError("Malformed ZIP manifest")
                sha, path = match.groups()
                if path in manifest:
                    raise TransferError("Duplicate manifest entry")
                manifest[path] = sha.lower()
            if len(manifest) != 14:
                raise TransferError("ZIP manifest count mismatch")
            for item in names:
                if item == manifest_path:
                    continue
                if not item.startswith(root):
                    raise TransferError("Unexpected ZIP root")
                key = item[len(root):]
                if manifest.get(key) != hashlib.sha256(zf.read(item)).hexdigest():
                    raise TransferError(f"Manifest mismatch: {key}")
    except (zipfile.BadZipFile, UnicodeError) as exc:
        raise TransferError(f"ZIP parse error: {type(exc).__name__}") from exc
    return {"size": len(data), "sha256": digest, "zip_crc": "PASS", "manifest": "14/14 PASS"}


def safe_save(data: bytes, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / ASSET_NAME
    if destination.exists():
        if not destination.is_file() or hashlib.sha256(destination.read_bytes()).hexdigest() != EXPECTED_SHA256:
            raise TransferError(f"Refusing to overwrite an unverified existing file at: {destination}")
        return destination
    name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=directory, prefix=".kmong-", suffix=".tmp", delete=False,
        ) as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
            name = target.name
        # Exclusive creation prevents overwriting unexpected existing data.
        with open(destination, "xb") as output:
            with open(name, "rb") as source:
                output.write(source.read())
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError as exc:
        raise TransferError("Output path was created concurrently; refuse overwrite") from exc
    finally:
        if name and os.path.isfile(name):
            os.unlink(name)
    if hashlib.sha256(destination.read_bytes()).hexdigest() != EXPECTED_SHA256:
        raise TransferError("Saved file checksum mismatch")
    return destination


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--out-dir", type=Path, default=None)
    cli.add_argument("--remote", default=REMOTE_URL, help="Git remote; default private GitHub repository")
    args = cli.parse_args()
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    output = args.out_dir or Path(base) / "GapAnalysis" / "verified_imports"
    failures = []
    for method, get_bytes in [
        ("isolated-git-clone", lambda: fetch_with_temporary_git(args.remote)),
        ("github-cli-api", fetch_with_gh),
    ]:
        try:
            data = get_bytes()
            result = verify_zip_bytes(data)
            saved = safe_save(data, output)
            print(json.dumps({"status": "PASS", "method": method, "saved_to": str(saved), **result}, ensure_ascii=False))
            return 0
        except TransferError as exc:
            failures.append({"method": method, "reason": str(exc)})
    print(json.dumps({"status": "FAIL", "errors": failures}, ensure_ascii=False), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
