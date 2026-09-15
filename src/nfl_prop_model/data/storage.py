"""Small, inspectable storage helpers; raw snapshots are content addressed."""

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import polars as pl


def sha256_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
    ) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value: dict[str, Any] = json.load(handle)
    return value


def store_frame(directory: Path, name: str, frame: pl.DataFrame) -> dict[str, Any]:
    """Publish a new file by hash, never overwrite an older raw snapshot."""
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=directory, suffix=".parquet", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        frame.write_parquet(temporary)
        digest = sha256_file(temporary)
        destination = directory / f"{name}-{digest}.parquet"
        if not destination.exists():
            os.replace(temporary, destination)
        return {
            "file": destination.name,
            "sha256": digest,
            "rows": frame.height,
            "schema": {key: str(dtype) for key, dtype in frame.schema.items()},
        }
    finally:
        temporary.unlink(missing_ok=True)


def load_frame(directory: Path, metadata: dict[str, Any]) -> pl.DataFrame:
    path = directory / metadata["file"]
    if sha256_file(path) != metadata["sha256"]:
        raise ValueError(f"Cache checksum mismatch: {path}. Restore the file or fetch --refresh.")
    return pl.read_parquet(path)
