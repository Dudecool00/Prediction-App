"""Local, dated human reviews of prospective QB status; never model eligibility."""

import hashlib
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

MAX_AGES = {"starter": timedelta(hours=24), "availability": timedelta(hours=6)}
STATUSES = {
    "starter": {"unknown", "confirmed", "not_starter"},
    "availability": {"unknown", "active", "inactive"},
}
CONTEXT_FIELDS = (
    "game_id",
    "season",
    "week",
    "team",
    "opponent_team",
    "designated_home",
    "kickoff_utc",
    "espn_id",
    "gsis_id",
    "source_snapshot_utc",
    "depth_rank",
)


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Evidence timestamps must include a time zone")
    return parsed.astimezone(UTC)


def checksum(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def review_context(candidate: dict[str, Any]) -> dict[str, Any]:
    return {key: candidate[key] for key in CONTEXT_FIELDS}


def evidence(status: str, source_url: str = "", source_at_utc: str = "") -> dict[str, str]:
    """Inputs to a review. The source timestamp is supplied by the reviewer."""
    return {
        "status": status,
        "source_url": source_url.strip(),
        "source_at_utc": source_at_utc.strip(),
    }


def validate_record(record: dict[str, Any]) -> None:
    if record["format_version"] != 1 or record["scope"] != "manual_pregame_status_review":
        raise ValueError("Unsupported status review")
    if not re.fullmatch(r"[0-9a-f]{32}", record["record_id"]):
        raise ValueError("Invalid status review identity")
    context = record["context"]
    if set(context) != set(CONTEXT_FIELDS) or context["season"] != 2026:
        raise ValueError("Status reviews require a 2026 candidate context")
    logged = timestamp(record["logged_at_utc"])
    kickoff = timestamp(context["kickoff_utc"])
    if timestamp(context["source_snapshot_utc"]) > logged or logged >= kickoff:
        raise ValueError("Status review must precede kickoff and follow its chart snapshot")
    if timestamp(record["sources"]["retrieved_at_utc"]) > logged:
        raise ValueError("Status review cannot use a future source cache")
    for kind, statuses in STATUSES.items():
        claim = record[kind]
        if claim["status"] not in statuses:
            raise ValueError(f"Invalid {kind} status")
        if claim["status"] == "unknown":
            if claim["source_url"] or claim["source_at_utc"]:
                raise ValueError("Unknown status must have empty evidence fields")
            continue
        url = urlsplit(claim["source_url"])
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or any(c.isspace() for c in claim["source_url"])
        ):
            raise ValueError("Evidence requires a public HTTP(S) source URL without credentials")
        if timestamp(claim["source_at_utc"]) > logged:
            raise ValueError("Evidence source timestamp cannot be after the review")
    if not isinstance(record["notes"], str) or len(record["notes"]) > 2000:
        raise ValueError("Review notes must be at most 2,000 characters")


def read_status_reviews(directory: Path) -> list[dict[str, Any]]:
    records = []
    for path in sorted(directory.glob("status-*.json")):
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            record = envelope["payload"]
            if envelope["sha256"] != checksum(record):
                raise ValueError(f"Status review checksum mismatch: {path.name}")
            validate_record(record)
            if path.name != f"status-{record['record_id']}.json":
                raise ValueError(f"Status review filename mismatch: {path.name}")
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError(f"Malformed status review: {path.name}") from error
        records.append(record)
    return records


def save_status_review(
    data_dir: Path,
    game_id: str,
    espn_id: str,
    *,
    starter: dict[str, str],
    availability: dict[str, str],
    notes: str = "",
    now: datetime | None = None,
    expected_context: dict[str, Any] | None = None,
) -> Path:
    """Re-read verified sources at save time; append a review without replacing history."""
    from nfl_prop_model.data.upcoming import CACHE_MAX_AGE, CHART_MAX_AGE, load_upcoming_report

    as_of = timestamp((now or datetime.now(UTC)).isoformat())
    report = load_upcoming_report(data_dir, as_of=as_of, days=28)
    matches = [
        row
        for row in report["candidates"]
        if row["game_id"] == game_id and row["espn_id"] == espn_id
    ]
    if len(matches) != 1:
        raise ValueError("Select a unique upcoming QB; the game or chart may have changed")
    candidate = matches[0]
    context = review_context(candidate)
    if expected_context is not None and context != expected_context:
        raise ValueError("Candidate changed while reviewing; reload and check the new sources")
    logged = timestamp((now or datetime.now(UTC)).isoformat())
    if logged < as_of:
        raise ValueError("The clock moved backwards while reviewing; reload and try again")
    if logged >= timestamp(candidate["kickoff_utc"]):
        raise ValueError("Status review must precede kickoff")
    if (
        logged - timestamp(report["sources"]["retrieved_at_utc"]) > CACHE_MAX_AGE
        or logged - timestamp(candidate["source_snapshot_utc"]) > CHART_MAX_AGE
    ):
        raise ValueError("Refresh stale schedule/chart sources before saving a status review")
    record_id = uuid4().hex
    record = {
        "format_version": 1,
        "scope": "manual_pregame_status_review",
        "record_id": record_id,
        "logged_at_utc": logged.isoformat(),
        "context": context,
        "sources": report["sources"],
        "starter": starter,
        "availability": availability,
        "notes": notes.strip(),
    }
    validate_record(record)
    directory = data_dir / "status_reviews"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"status-{record_id}.json"
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(
            {"payload": record, "sha256": checksum(record)},
            handle,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return path


def apply_status_reviews(report: dict[str, Any], records: list[dict[str, Any]]) -> None:
    """Overlay reviews as known at the report cutoff; stale claims never clear checks."""
    as_of = timestamp(report["as_of_utc"])
    latest: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for record in records:
        validate_record(record)
        logged = timestamp(record["logged_at_utc"])
        if logged > as_of:
            continue
        context = record["context"]
        key = (context["game_id"], context["team"], context["espn_id"])
        previous = latest.get(key, [])
        if not previous or logged > timestamp(previous[0]["logged_at_utc"]):
            latest[key] = [record]
        elif logged == timestamp(previous[0]["logged_at_utc"]):
            latest[key].append(record)
    confirmed: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in report["candidates"]:
        matches = latest.get((row["game_id"], row["team"], row["espn_id"]), [])
        row["status_review"] = {
            "state": "unreviewed",
            "record_ids": [],
            "starter": "unknown",
            "availability": "unknown",
        }
        review = row["status_review"]
        review["record_ids"] = [record["record_id"] for record in matches]
        if len(matches) > 1:
            review["state"] = "simultaneous_reviews"
        elif matches:
            record = matches[0]
            review["logged_at_utc"] = record["logged_at_utc"]
            review["evidence"] = {kind: record[kind] for kind in STATUSES}
            review["notes"] = record["notes"]
            review["reviewed_context"] = record["context"]
            review["source_manifest"] = record["sources"]
            if record["context"] != review_context(row):
                review["state"] = "candidate_changed"
            else:
                review["state"] = "reviewed"
                for kind, max_age in MAX_AGES.items():
                    claim = record[kind]
                    if claim["status"] == "unknown":
                        continue
                    source_age = as_of - timestamp(claim["source_at_utc"])
                    check_age = as_of - timestamp(record["logged_at_utc"])
                    review[kind] = (
                        "stale" if max(source_age, check_age) > max_age else claim["status"]
                    )
                if review["starter"] == "confirmed":
                    confirmed.setdefault((row["game_id"], row["team"]), []).append(row)
        # Reviews alone never enable forecasts or override source/history/model checks.
        row["starter_confirmed"] = False
        row["active_status_reviewed"] = False
        row["forecast_available"] = False
    for rows in confirmed.values():
        if len(rows) > 1:
            for row in rows:
                row["status_review"]["starter"] = "conflict"
    for row in report["candidates"]:
        review = row["status_review"]
        row["starter_confirmed"] = review["starter"] == "confirmed" and row["sources_fresh"]
        row["active_status_reviewed"] = review["availability"] == "active" and row["sources_fresh"]
        reasons = row["review_reasons"]
        reasons.remove("starter_and_injury_status_unverified")
        if not row["starter_confirmed"]:
            reasons.append(f"starter_status_{review['starter']}")
        if not row["active_status_reviewed"]:
            reasons.append(f"active_status_{review['availability']}")
        if review["state"] in {"candidate_changed", "simultaneous_reviews"}:
            reasons.append(review["state"])
        if review["starter"] == "confirmed" and review["availability"] == "inactive":
            row["starter_confirmed"] = False
            reasons.append("starter_and_inactive_evidence_conflict")
    report["status_review_policy"] = {
        "starter_max_hours": 24,
        "availability_max_hours": 6,
        "timestamp_basis": "Both source publication and local review must be within the window",
        "method": "Human-entered source claims; links and contents are not fetched or verified",
    }
    report["counts"]["manually_reviewed_starter_rows"] = sum(
        row["starter_confirmed"] for row in report["candidates"]
    )
    report["counts"]["manually_reviewed_active_rows"] = sum(
        row["active_status_reviewed"] for row in report["candidates"]
    )
    report["limitations"].append(
        "Status reviews are manual source claims, not independently verified participation. "
        "Active status does not establish health or playing time. Changed candidates, older "
        "evidence, and conflicting starter claims require another review. Saved reviews never "
        "enable forecasts or modify historical training data."
    )
