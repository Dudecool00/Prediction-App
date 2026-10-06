"""Immutable model/protocol checkpoint required before reserved-outcome access."""

import hashlib
import platform
import shutil
import zipfile
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any
from uuid import uuid4

from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.storage import read_json, sha256_file, write_json
from nfl_prop_model.modeling.candidate import (
    _checked_file,
    _source_archive,
    holdout_protocol,
    verify_candidate,
)
from nfl_prop_model.modeling.policy import policy_hash


def frozen_protocol() -> dict[str, Any]:
    protocol = holdout_protocol()
    protocol.update(
        {
            "version": "qb-passing-holdout-frozen-v1",
            "status": "frozen",
            "access": "one_reserved_diagnostic_run",
        }
    )
    return protocol


def _code_hashes() -> dict[str, str]:
    package = Path(__file__).parents[1]
    project = package.parent.parent
    files = sorted(package.rglob("*.py")) + [
        project / "pyproject.toml",
        project / "requirements-dev.lock",
    ]
    return {file.relative_to(project).as_posix(): sha256_file(file) for file in files}


def _runtime() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        **{
            name: version(name)
            for name in ("nflreadpy", "polars", "numpy", "xgboost-cpu", "tzdata")
        },
    }


def freeze_candidate(bundle: Path, output_dir: Path) -> Path:
    prepared = verify_candidate(bundle)
    now = datetime.now(UTC)
    directory = output_dir / f"frozen-{now.strftime('%Y%m%dT%H%M%S%fZ')}-{uuid4().hex[:8]}"
    directory.mkdir(parents=True, exist_ok=False)
    files = [bundle / metadata["file"] for metadata in prepared["artifacts"].values()]
    files.append(next(bundle.glob("manifest-*.json")))
    for file in files:
        shutil.copy2(file, directory / file.name)
    prepared_path = next(directory.glob("manifest-*.json"))
    code_dir = directory / "evaluation-code"
    code_dir.mkdir()
    code = _source_archive(code_dir)
    frozen = {
        "format_version": 1,
        "status": "frozen_for_reserved_diagnostic",
        "frozen_at_utc": now.isoformat(),
        "prepared_manifest": {"file": prepared_path.name, "sha256": sha256_file(prepared_path)},
        "policy_sha256": prepared["policy_sha256"],
        "draft_protocol_sha256": prepared["holdout_protocol_sha256"],
        "protocol": frozen_protocol(),
        "protocol_sha256": policy_hash(frozen_protocol()),
        "evaluation_source": code,
        "executable_sha256": policy_hash(_code_hashes()),
        "environment": _runtime(),
        "production_enabled": False,
        "claim_scope": "All recorded appearances; prospective starter/cohort/source "
        "gates remain open",
    }
    path = directory / "freeze.json"
    write_json(path, frozen)
    path.rename(directory / f"freeze-{sha256_file(path)}.json")
    verify_frozen(directory, require_current_code=True)
    return directory


def verify_frozen(directory: Path, *, require_current_code: bool = False) -> dict[str, Any]:
    paths = list(directory.glob("freeze-*.json"))
    if len(paths) != 1:
        raise DataQualityError(
            "A unique content-addressed freeze record is required before 2025 access"
        )
    path = paths[0]
    digest = sha256_file(path)
    if path.name != f"freeze-{digest}.json":
        raise DataQualityError("Freeze record checksum mismatch")
    frozen = read_json(path)
    if (
        frozen.get("format_version") != 1
        or frozen.get("status") != "frozen_for_reserved_diagnostic"
        or frozen.get("protocol") != frozen_protocol()
        or frozen.get("protocol_sha256") != policy_hash(frozen_protocol())
        or frozen.get("production_enabled") is not False
    ):
        raise DataQualityError("Frozen protocol or production gate differs")
    prepared = verify_candidate(directory)
    _checked_file(directory, frozen["prepared_manifest"])
    if (
        frozen["policy_sha256"] != prepared["policy_sha256"]
        or frozen["draft_protocol_sha256"] != prepared["holdout_protocol_sha256"]
    ):
        raise DataQualityError("Freeze record does not identify this prepared candidate")
    source = frozen["evaluation_source"]
    code_path = _checked_file(directory / "evaluation-code", source)
    with zipfile.ZipFile(code_path) as archive:
        members = source["members"]
        if len(archive.namelist()) != len(members) or set(archive.namelist()) != set(members):
            raise DataQualityError("Frozen source members differ")
        if any(hashlib.sha256(archive.read(n)).hexdigest() != h for n, h in members.items()):
            raise DataQualityError("Frozen source member checksum mismatch")
    executable = {
        name: value
        for name, value in members.items()
        if name.startswith("src/nfl_prop_model/")
        or name in ("pyproject.toml", "requirements-dev.lock")
    }
    if policy_hash(executable) != frozen["executable_sha256"]:
        raise DataQualityError("Frozen executable fingerprint differs")
    if require_current_code and executable != _code_hashes():
        raise DataQualityError(
            "Evaluator code/dependencies changed after freeze; do not access outcomes"
        )
    if require_current_code and frozen["environment"] != _runtime():
        raise DataQualityError("Evaluator runtime changed after freeze; do not access outcomes")
    frozen_time = datetime.fromisoformat(frozen["frozen_at_utc"])
    prepared_time = datetime.fromisoformat(prepared["created_at_utc"])
    if frozen_time.utcoffset() is None or not prepared_time <= frozen_time <= datetime.now(UTC):
        raise DataQualityError("Freeze timestamp must follow preparation and not be in the future")
    return {**frozen, "freeze_sha256": digest, "prepared": prepared}
