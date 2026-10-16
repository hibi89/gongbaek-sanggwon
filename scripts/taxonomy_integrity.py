#!/usr/bin/env python3
"""Canonical content hash and version helpers for the reviewed taxonomy."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


HASH_FIELD = "taxonomy_hash"
VERSION_FIELD = "taxonomy_version"
VERSION_SUFFIX = re.compile(r"-sha256-[0-9a-f]{16}$")


def canonical_taxonomy_bytes(taxonomy: dict[str, Any]) -> bytes:
    """Serialize taxonomy semantics independent of JSON formatting/key order.

    The derived hash and version are excluded to avoid a self-referential hash.
    Category array order is normalized by code; every other taxonomy field is
    retained, so edits to a label, ABC tag, evidence, or source metadata change
    the digest.
    """
    payload = {
        key: value
        for key, value in taxonomy.items()
        if key not in {HASH_FIELD, VERSION_FIELD}
    }
    categories = payload.get("categories")
    if not isinstance(categories, list):
        raise ValueError("Taxonomy must contain a categories array")
    payload["categories"] = sorted(
        categories,
        key=lambda item: str(item.get("code", "")) if isinstance(item, dict) else "",
    )
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_taxonomy_hash(taxonomy: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_taxonomy_bytes(taxonomy)).hexdigest()


def seal_taxonomy(taxonomy: dict[str, Any]) -> tuple[str, str]:
    """Set a content-derived version and its full SHA-256 digest in-place."""
    digest = canonical_taxonomy_hash(taxonomy)
    current = str(taxonomy.get(VERSION_FIELD, "taxonomy")).strip() or "taxonomy"
    base = VERSION_SUFFIX.sub("", current)
    version = f"{base}-sha256-{digest[:16]}"
    taxonomy[HASH_FIELD] = digest
    taxonomy[VERSION_FIELD] = version
    return digest, version


def verify_taxonomy(taxonomy: dict[str, Any]) -> str:
    """Return the calculated hash or fail if the stored seal is stale."""
    digest = canonical_taxonomy_hash(taxonomy)
    declared = str(taxonomy.get(HASH_FIELD, "")).strip().lower()
    version = str(taxonomy.get(VERSION_FIELD, "")).strip()
    expected_suffix = f"-sha256-{digest[:16]}"
    if declared != digest:
        raise ValueError("Current taxonomy content hash does not match its declared hash")
    if not version.endswith(expected_suffix):
        raise ValueError("Current taxonomy version is not derived from its content hash")
    return digest
