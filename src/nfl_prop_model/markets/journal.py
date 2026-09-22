"""Append-only research snapshots: exclusive creation, checksummed contents, no edit API."""

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


def payload_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def save_quote_record(directory: Path, quote: dict[str, Any]) -> Path:
    if quote.get("scope") != "historical_research_demonstration":
        raise ValueError("This journal currently accepts historical research snapshots only")
    record_id = uuid4().hex
    payload = {
        "format_version": 1,
        "record_id": record_id,
        "logged_at_utc": datetime.now(UTC).isoformat(),
        "quote": quote,
    }
    envelope = {"payload": payload, "sha256": payload_hash(payload)}
    text = json.dumps(envelope, indent=2, sort_keys=True, allow_nan=False) + "\n"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"quote-{record_id}.json"
    # Exclusive creation prevents replacement even if a name collision occurs.
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    return path


def read_quote_records(directory: Path) -> list[dict[str, Any]]:
    records = []
    for path in sorted(directory.glob("quote-*.json")):
        with path.open(encoding="utf-8") as handle:
            envelope = json.load(handle)
        payload = envelope["payload"]
        if envelope["sha256"] != payload_hash(payload):
            raise ValueError(f"Saved snapshot checksum mismatch: {path.name}")
        if payload["format_version"] != 1 or path.name != f"quote-{payload['record_id']}.json":
            raise ValueError(f"Unsupported or mismatched snapshot identity: {path.name}")
        records.append(payload)
    return sorted(
        records, key=lambda value: (value["logged_at_utc"], value["record_id"]), reverse=True
    )
