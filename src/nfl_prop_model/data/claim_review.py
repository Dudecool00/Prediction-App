"""Named, append-only reviews of archived pregame claims; no model approval."""

import json
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from nfl_prop_model.data.status_evidence import read_status_evidence
from nfl_prop_model.data.status_reviews import MAX_AGES, checksum, review_context, timestamp
from nfl_prop_model.data.storage import read_json, write_json

VERDICTS = ("supported", "contradicted", "unclear")


def review_deadline(evidence: dict[str, Any]) -> datetime:
    age = MAX_AGES[evidence["kind"]]
    return min(
        timestamp(evidence["published_at_utc"]) + age,
        timestamp(evidence["retrieved_at_utc"]) + age,
        timestamp(evidence["context"]["kickoff_utc"]) - timedelta(hours=1),
    )


def validate_review(record: dict[str, Any], evidence: dict[str, Any]) -> None:
    if (
        record.get("format_version") != 1
        or record.get("scope") != "named_pregame_claim_review"
        or not re.fullmatch(r"[a-f0-9]{32}", record.get("record_id", ""))
        or record.get("evidence_id") != evidence["record_id"]
        or record.get("evidence_sha256") != checksum(evidence)
        or record.get("context") != evidence["context"]
        or record.get("verdict") not in VERDICTS
        or not isinstance(record.get("reviewer"), str)
        or not 1 <= len(record["reviewer"].strip()) <= 100
        or not isinstance(record.get("notes"), str)
        or not 1 <= len(record["notes"].strip()) <= 2000
    ):
        raise ValueError("Invalid claim review or changed archived evidence")
    logged = timestamp(record["logged_at_utc"])
    sources_at = timestamp(record["sources"]["retrieved_at_utc"])
    cutoff = timestamp(evidence["context"]["kickoff_utc"]) - timedelta(hours=1)
    if (
        not timestamp(evidence["retrieved_at_utc"]) <= logged <= review_deadline(evidence)
        or logged >= cutoff
        or not sources_at <= logged <= sources_at + timedelta(hours=24)
    ):
        raise ValueError("Claim review must use fresh sources and precede expiry/game cutoff")


def read_claim_reviews(data_dir: Path, evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    archives = {item["record_id"]: item for item in evidence}
    records = []
    directory = data_dir / "claim_reviews"
    if directory.is_symlink():
        raise ValueError("Claim review directory must not be a symlink")
    for path in sorted(directory.glob("review-*.json")):
        if path.is_symlink():
            raise ValueError("Claim review must not be a symlink")
        envelope = read_json(path)
        record = envelope["payload"]
        if (
            path.name != f"review-{record['record_id']}.json"
            or envelope["sha256"] != checksum(record)
            or record["evidence_id"] not in archives
        ):
            raise ValueError("Claim review identity/checksum/archive mismatch")
        validate_review(record, archives[record["evidence_id"]])
        records.append(record)
    return records


def review_result(records: list[dict[str, Any]]) -> str:
    """Latest verdict per named reviewer; equal-time or cross-reviewer disagreement conflicts."""
    latest: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        reviewer = record["reviewer"].strip().casefold()
        prior = latest.get(reviewer, [])
        logged = timestamp(record["logged_at_utc"])
        previous = timestamp(prior[0]["logged_at_utc"]) if prior else None
        if previous is None or logged > previous:
            latest[reviewer] = [record]
        elif logged == previous:
            latest[reviewer].append(record)
    verdicts = {item["verdict"] for group in latest.values() for item in group}
    return next(iter(verdicts)) if len(verdicts) == 1 else "conflict" if verdicts else "unreviewed"


def claim_review_queue(data_dir: Path, *, as_of: datetime | None = None) -> dict[str, Any]:
    from nfl_prop_model.data.upcoming import load_upcoming_report

    now = timestamp((as_of or datetime.now(UTC)).isoformat())
    upcoming = load_upcoming_report(data_dir, as_of=now, days=28)
    archives = read_status_evidence(data_dir)
    reviews = read_claim_reviews(data_dir, archives)
    candidates = {(row["game_id"], row["espn_id"]): row for row in upcoming["candidates"]}
    rows = []
    for evidence in archives:
        if timestamp(evidence["retrieved_at_utc"]) > now:
            continue
        context = evidence["context"]
        row = candidates.get((context["game_id"], context["espn_id"]))
        cutoff = timestamp(context["kickoff_utc"]) - timedelta(hours=1)
        reason = (
            "past_game_cutoff"
            if now >= cutoff
            else "evidence_expired"
            if now > review_deadline(evidence)
            else "candidate_missing"
            if row is None
            else "candidate_context_changed"
            if review_context(row) != context
            else "source_cache_stale"
            if not row["sources_fresh"]
            else "ready"
        )
        history = sorted(
            [
                record
                for record in reviews
                if record["evidence_id"] == evidence["record_id"]
                and timestamp(record["logged_at_utc"]) <= now
            ],
            key=lambda record: (record["logged_at_utc"], record["record_id"]),
        )
        rows.append(
            {
                "evidence": evidence,
                "review_state": review_result(history),
                "review_history": history,
                "readiness": reason,
                "review_allowed": reason == "ready",
                "fresh_until_utc": review_deadline(evidence).isoformat(),
            }
        )
    return {
        "format_version": 1,
        "scope": "pregame_claim_review_queue",
        "as_of_utc": now.isoformat(),
        "sources": upcoming["sources"],
        "evidence_count": len(rows),
        "review_counts": dict(sorted(Counter(row["review_state"] for row in rows).items())),
        "readiness_counts": dict(sorted(Counter(row["readiness"] for row in rows).items())),
        "evidence": rows,
        "production_enabled": False,
        "policy": {
            "verdict": "Reviewer judges whether the archived article supports its exact claim.",
            "expiry": "Reviewing never resets publication/retrieval expiry or the game cutoff.",
            "identity": "Reviewer names are self-reported; independence is not certified.",
            "disagreement": "Latest verdict per named reviewer; differing reviewers conflict.",
            "forecast_gate": "Reviews do not establish participation or enable forecasts.",
        },
    }


def save_claim_review(
    data_dir: Path,
    evidence_id: str,
    *,
    reviewer: str,
    verdict: str,
    notes: str,
    expected_evidence_sha256: str | None = None,
    now: datetime | None = None,
) -> Path:
    """Recheck live cached context at save time; never accept a caller's earlier queue."""
    report = claim_review_queue(data_dir, as_of=now)
    matches = [row for row in report["evidence"] if row["evidence"]["record_id"] == evidence_id]
    if len(matches) != 1 or not matches[0]["review_allowed"]:
        raise ValueError(
            "Select fresh archived evidence with an unchanged pregame candidate context"
        )
    evidence = matches[0]["evidence"]
    if expected_evidence_sha256 is not None and expected_evidence_sha256 != checksum(evidence):
        raise ValueError("Archived evidence changed since the review form opened")
    record = {
        "format_version": 1,
        "scope": "named_pregame_claim_review",
        "record_id": uuid4().hex,
        "evidence_id": evidence_id,
        "evidence_sha256": checksum(evidence),
        "context": evidence["context"],
        "reviewer": reviewer.strip(),
        "verdict": verdict,
        "notes": notes.strip(),
        "logged_at_utc": report["as_of_utc"],
        "sources": report["sources"],
    }
    validate_review(record, evidence)
    directory = data_dir / "claim_reviews"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"review-{record['record_id']}.json"
    with path.open("x", encoding="utf-8") as handle:
        json.dump(
            {"payload": record, "sha256": checksum(record)}, handle, indent=2, allow_nan=False
        )
    return path


def write_claim_review_report(report_dir: Path, report: dict[str, Any]) -> None:
    write_json(report_dir / "claim_review.json", report)
    lines = [
        "# Pregame article claim reviews",
        "",
        f"Checked: {report['as_of_utc']}",
        "",
        "| Game | QB | Kind / claim | Review | Readiness | Fresh until (UTC) |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in report["evidence"]:
        evidence = row["evidence"]
        lines.append(
            f"| {evidence['context']['game_id']} | {evidence['player_name']} | "
            f"{evidence['kind']} / {evidence['claim']} | {row['review_state']} | "
            f"{row['readiness']} | {row['fresh_until_utc']} |"
        )
    lines.extend(["", *report["policy"].values(), ""])
    (report_dir / "claim_review.md").write_text("\n".join(lines), encoding="utf-8")
