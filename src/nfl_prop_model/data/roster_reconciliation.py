"""Postgame roster corroboration for the original, immutable prospective pool."""

from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from nfl_prop_model.data.participation import audit_participation
from nfl_prop_model.data.prospective import _frame
from nfl_prop_model.data.schemas import DataQualityError, require_columns
from nfl_prop_model.data.starter_audit import (
    IDENTITY_SOURCE,
    POSITION_SOURCE,
    event_team,
    load_source,
    request_json,
    starter_identity,
    store_source,
)
from nfl_prop_model.data.status_reviews import checksum, timestamp
from nfl_prop_model.data.storage import read_json, sha256_file, store_frame, write_json

ROSTER_RULES = {
    "version": 1,
    "scope": "prospective_postgame_roster_reconciliation",
    "denominator": "The existing registry's complete candidate pool; enrollment is never replaced",
    "availability": "Completed game, matching kickoff, and evidence retrieved "
    "after kickoff plus 24 hours",
    "identity": "Unique ESPN-to-GSIS QB mapping must agree with the original candidate IDs",
    "played": "Period-zero valid roster entry with didNotPlay=false "
    "plus a recorded QB stats target",
    "dnp": "Explicit didNotPlay=true, starter=false, valid=false plus no recorded QB appearance",
    "ambiguous": "didNotPlay=false with valid=false is unresolved, even when stats exist",
    "conflict": "Roster DNP with a QB stats target remains a conflict, including zero-yard targets",
    "activation": "Roster active is ignored; DNP and missing roster entries "
    "do not establish inactive status",
    "claims": "Compare enrollment-time starter annotations with retrospective flags; "
    "outcome agreement is not source-content adjudication",
    "forecast": "No model execution, probabilities, EV, calibration validation "
    "or production enablement",
}
PENDING = {"pending_game", "pending_fresh_postgame_sources", "schedule_missing", "schedule_changed"}


def cache_directory(data_dir: Path, registry_id: str) -> Path:
    return data_dir / "raw" / "participation_rosters" / registry_id


def _slot(context: dict[str, Any], event_id: Any) -> dict[str, Any]:
    if not isinstance(event_id, str) or not event_id.isdecimal():
        raise DataQualityError("Participation evidence requires a decimal ESPN event identity")
    home = context["team"] if context["designated_home"] else context["opponent_team"]
    away = context["opponent_team"] if context["designated_home"] else context["team"]
    return {
        "game_id": context["game_id"],
        "season": context["season"],
        "week": context["week"],
        "team": context["team"],
        "side": "home" if context["designated_home"] else "away",
        "event_id": event_id,
        "home_team": home,
        "away_team": away,
        "kickoff_utc": context["kickoff_utc"],
    }


def _event_team(header: dict[str, Any], slot: dict[str, Any]) -> str:
    team_id = event_team(header, slot)
    try:
        if timestamp(header["competitions"][0]["date"]) != timestamp(slot["kickoff_utc"]):
            raise DataQualityError("Roster event kickoff differs from the enrolled game")
    except (KeyError, TypeError, IndexError) as error:
        raise DataQualityError("Roster event lacks an explicit kickoff timestamp") from error
    return team_id


def roster_qbs(
    roster: dict[str, Any], slot: dict[str, Any], team_id: str
) -> dict[str, dict[str, Any]]:
    """Validate literal roster flags without assigning meaning to the active field."""
    expected = (
        "/v2/sports/football/leagues/nfl/"
        f"events/{slot['event_id']}/competitions/{slot['event_id']}/competitors/{team_id}/roster"
    )
    reference = urlsplit(roster.get("$ref", ""))
    if reference.hostname != "sports.core.api.espn.com" or reference.path != expected:
        raise DataQualityError("Participation roster event/team reference mismatch")
    if not isinstance(roster.get("entries"), list):
        raise DataQualityError("Participation roster lacks entries")
    result = {}
    for entry in roster["entries"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("position"), dict):
            raise DataQualityError("Roster entry lacks a position reference")
        position = urlsplit(entry.get("position", {}).get("$ref", ""))
        if position.path != "/v2/sports/football/leagues/nfl/positions/8":
            continue
        identity = str(entry.get("playerId", ""))
        if (
            position.hostname != "sports.core.api.espn.com"
            or not identity.isdecimal()
            or identity in result
        ):
            raise DataQualityError("Missing, duplicate or foreign QB roster identity")
        if (
            any(type(entry.get(field)) is not bool for field in ("starter", "didNotPlay", "valid"))
            or type(entry.get("period")) is not int
            or entry["period"] != 0
        ):
            raise DataQualityError("QB roster requires explicit period-zero boolean flags")
        starter, dnp, valid = entry["starter"], entry["didNotPlay"], entry["valid"]
        if (starter and (dnp or not valid)) or (dnp and valid):
            raise DataQualityError("Contradictory QB roster flags")
        result[identity] = {
            "starter": starter,
            "did_not_play": dnp,
            "valid": valid,
            "period": 0,
            "state": "reported_dnp"
            if dnp
            else "reported_participant"
            if valid
            else "ambiguous_flags",
        }
    return result


def _metadata_time(metadata: dict[str, Any], as_of: datetime, cutoff: datetime) -> None:
    if not cutoff <= timestamp(metadata["retrieved_at_utc"]) <= as_of:
        raise DataQualityError(
            "Roster evidence must be available after the game cutoff and before audit"
        )


def _load_manifest(
    directory: Path, audit: dict[str, Any], as_of: datetime
) -> dict[str, Any] | None:
    path = directory / "manifest.json"
    if not path.exists():
        return None
    envelope = read_json(path)
    manifest: dict[str, Any] = envelope["payload"]
    if (
        directory.is_symlink()
        or path.is_symlink()
        or envelope["sha256"] != checksum(manifest)
        or manifest["format_version"] != 1
        or manifest["scope"] != "prospective_event_roster_evidence"
        or manifest["registry_sha256"] != audit["registry_sha256"]
        or manifest["snapshot_sha256"] != audit["registry"]["snapshot_sha256"]
    ):
        raise DataQualityError("Roster evidence manifest differs from its registry/checksum")
    if timestamp(manifest["retrieved_at_utc"]) > as_of:
        raise DataQualityError("Roster evidence manifest was retrieved after the audit time")
    keys = [(entry["slot"]["game_id"], entry["slot"]["team"]) for entry in manifest["evidence"]]
    expected = {(row["context"]["game_id"], row["context"]["team"]) for row in audit["candidates"]}
    if len(keys) != len(set(keys)) or not set(keys) <= expected:
        raise DataQualityError("Roster evidence contains duplicate or unenrolled team/game slots")
    return manifest


def fetch_roster_evidence(
    data_dir: Path,
    registry: Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Explicit network action: only query completed, available enrolled games."""
    as_of = timestamp((now or datetime.now(UTC)).isoformat())
    audit = audit_participation(data_dir, registry, as_of=as_of)
    directory = cache_directory(data_dir, audit["registry"]["registry_id"])
    prior = _load_manifest(directory, audit, as_of)
    entries = (
        {(entry["slot"]["game_id"], entry["slot"]["team"]): entry for entry in prior["evidence"]}
        if prior
        else {}
    )
    eligible = [row for row in audit["candidates"] if row["state"] not in PENDING]
    position_metadata = prior["qb_position"] if prior else None
    identities_metadata = prior["identities"] if prior else None
    if eligible:
        import nflreadpy as nfl
        from nflreadpy.config import update_config

        update_config(cache_mode="memory")
        nfl.clear_cache()
        schedules_dir = data_dir / "raw" / "upcoming_2026"
        schedule_source = audit["sources"]["schedules"]
        schedules = _frame(schedules_dir, schedule_source["datasets"]["schedules"])
        events = {row["game_id"]: row.get("espn") for row in schedules.iter_rows(named=True)}
        slots = {
            (row["context"]["game_id"], row["context"]["team"]): _slot(
                row["context"], events.get(row["context"]["game_id"])
            )
            for row in eligible
        }
        position = request_json(POSITION_SOURCE)
        if position.get("id") != "8" or position.get("abbreviation") != "QB":
            raise DataQualityError("ESPN QB position definition changed")
        position_metadata = store_source(directory, "qb-position", position, POSITION_SOURCE)
        identities = nfl.load_players().select("gsis_id", "espn_id", "display_name", "position")
        require_columns(identities, "roster identities", tuple(identities.columns), ())
        identities_metadata = {
            **store_frame(directory, "identities", identities),
            "source": IDENTITY_SOURCE,
            "retrieved_at_utc": (now or datetime.now(UTC)).isoformat(),
        }
        headers: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        for key, slot in sorted(slots.items()):
            event = slot["event_id"]
            if event not in headers:
                url = f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={event}"
                header = request_json(url)["header"]
                _event_team(header, slot)
                headers[event] = header, store_source(directory, f"event-{event}", header, url)
            header, header_metadata = headers[event]
            team_id = _event_team(header, slot)
            url = (
                "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/"
                f"events/{event}/competitions/{event}/competitors/{team_id}/roster?limit=1000"
            )
            roster = request_json(url)
            roster_qbs(roster, slot, team_id)
            roster_metadata = store_source(directory, f"roster-{event}-{team_id}", roster, url)
            if now is not None:
                for metadata in (position_metadata, header_metadata, roster_metadata):
                    metadata["retrieved_at_utc"] = as_of.isoformat()
            entries[key] = {"slot": slot, "header": header_metadata, "roster": roster_metadata}
    finished = timestamp((now or datetime.now(UTC)).isoformat())
    if finished < as_of:
        raise ValueError("Clock moved backwards during roster retrieval")
    manifest = {
        "format_version": 1,
        "scope": "prospective_event_roster_evidence",
        "registry_sha256": audit["registry_sha256"],
        "snapshot_sha256": audit["registry"]["snapshot_sha256"],
        "retrieved_at_utc": finished.isoformat(),
        "qb_position": position_metadata,
        "identities": identities_metadata,
        "evidence": list(entries.values()),
    }
    directory.mkdir(parents=True, exist_ok=True)
    envelope = {"payload": manifest, "sha256": checksum(manifest)}
    write_json(directory / f"manifest-{finished.strftime('%Y%m%dT%H%M%S%fZ')}.json", envelope)
    write_json(directory / "manifest.json", envelope)
    return manifest


def reconcile_rosters(
    data_dir: Path, registry: Path, *, as_of: datetime | None = None
) -> dict[str, Any]:
    """Read local evidence only; keep every pending, ambiguous and conflicting row."""
    as_of = timestamp((as_of or datetime.now(UTC)).isoformat())
    audit = audit_participation(data_dir, registry, as_of=as_of)
    directory = cache_directory(data_dir, audit["registry"]["registry_id"])
    manifest = _load_manifest(directory, audit, as_of)
    evidence = (
        {(entry["slot"]["game_id"], entry["slot"]["team"]): entry for entry in manifest["evidence"]}
        if manifest
        else {}
    )
    identities = None
    if manifest and manifest["evidence"]:
        if (
            manifest["qb_position"]["source"] != POSITION_SOURCE
            or manifest["identities"]["source"] != IDENTITY_SOURCE
        ):
            raise DataQualityError("Roster identity/position source reference changed")
        position = load_source(directory, manifest["qb_position"])
        if position.get("id") != "8" or position.get("abbreviation") != "QB":
            raise DataQualityError("ESPN QB position definition changed")
        identities = _frame(directory, manifest["identities"])
        require_columns(identities, "roster identities", tuple(identities.columns), ())
        if timestamp(manifest["identities"]["retrieved_at_utc"]) > as_of:
            raise DataQualityError("Identity evidence was retrieved after the audit time")
    parsed = {}
    for row in audit["candidates"]:
        context = row["context"]
        check: dict[str, Any] = {
            "state": row["state"] if row["state"] in PENDING else "roster_evidence_missing",
            "flags": None,
            "starter_claim_result": "not_adjudicated",
            "availability_claim_result": "not_adjudicated",
        }
        row["roster_reconciliation"] = check
        if row["state"] in PENDING:
            continue
        key = (context["game_id"], context["team"])
        entry = evidence.get(key)
        if entry is None or identities is None or manifest is None:
            continue
        slot = entry["slot"]
        if slot != _slot(context, slot["event_id"]):
            raise DataQualityError(
                "Roster evidence slot differs from the original candidate context"
            )
        cutoff = timestamp(context["kickoff_utc"]) + timedelta(hours=24)
        for metadata in (entry["header"], entry["roster"], manifest["qb_position"]):
            _metadata_time(metadata, as_of, cutoff)
            if (directory / metadata["file"]).is_symlink():
                raise DataQualityError("Roster source must be a local nonsymlink file")
        if key not in parsed:
            header = load_source(directory, entry["header"])
            team_id = _event_team(header, slot)
            event = slot["event_id"]
            header_url = (
                f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={event}"
            )
            roster_url = (
                "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/"
                f"events/{event}/competitions/{event}/competitors/{team_id}/roster?limit=1000"
            )
            if entry["header"]["source"] != header_url or entry["roster"]["source"] != roster_url:
                raise DataQualityError("Roster event source reference changed")
            parsed[key] = roster_qbs(load_source(directory, entry["roster"]), slot, team_id)
        check["sources"] = {
            "header": entry["header"],
            "roster": entry["roster"],
            "identities": manifest["identities"],
        }
        if not str(context["espn_id"]).isdecimal():
            check["state"] = "espn_identity_unmapped"
            continue
        identity = starter_identity(identities, context["espn_id"])
        if identity is None or identity["position"] != "QB" or not context["gsis_id"]:
            check["state"] = "identity_unmapped"
            continue
        if identity["gsis_id"] != context["gsis_id"]:
            check["state"] = "identity_mapping_conflict"
            continue
        flags = parsed[key].get(context["espn_id"])
        check["flags"] = flags
        if flags is None:
            check["state"] = "candidate_missing_from_qb_roster"
            continue
        stats_state = row["state"]
        if flags["state"] == "ambiguous_flags":
            check["state"] = "roster_flags_ambiguous"
        elif flags["state"] == "reported_dnp":
            check["state"] = (
                "roster_stats_conflict"
                if stats_state == "recorded_qb_appearance"
                else "corroborated_dnp"
                if stats_state == "no_recorded_qb_appearance"
                else "roster_dnp_stats_unresolved"
            )
        else:
            check["state"] = (
                "corroborated_participation"
                if stats_state == "recorded_qb_appearance"
                else "roster_participant_target_unresolved"
            )
        status = row["primary_status_at_enrollment"]
        starter_claim = status.get("starter", "unknown")
        if flags["state"] != "ambiguous_flags" and starter_claim in {"confirmed", "not_starter"}:
            check["starter_claim_result"] = (
                "agrees_with_roster"
                if flags["starter"] == (starter_claim == "confirmed")
                else "contradicts_roster"
            )
        availability_claim = status.get("availability", "unknown")
        if availability_claim == "active":
            check["availability_claim_result"] = "activation_not_established_by_roster_flags"
        elif availability_claim in {"ruled_out", "inactive"}:
            check["availability_claim_result"] = (
                "contradicted_by_corroborated_participation"
                if check["state"] == "corroborated_participation"
                else "consistent_with_dnp_only"
                if check["state"] == "corroborated_dnp"
                else "not_adjudicated"
            )
    counts = dict(
        sorted(
            Counter(row["roster_reconciliation"]["state"] for row in audit["candidates"]).items()
        )
    )
    audit.update(
        {
            "scope": ROSTER_RULES["scope"],
            "roster_rules": ROSTER_RULES,
            "roster_rules_sha256": checksum(ROSTER_RULES),
            "roster_evidence_manifest": manifest,
            "roster_counts": counts,
            "corroborated_rows": sum(
                counts.get(state, 0) for state in ("corroborated_participation", "corroborated_dnp")
            ),
            "production_enabled": False,
            "participation_validation_complete": False,
        }
    )
    package = Path(__file__).parents[1]
    audit["reconciliation_source_hashes"] = {
        path.relative_to(package).as_posix(): sha256_file(path)
        for path in sorted(package.rglob("*.py"))
    }
    return audit


def write_reconciliation_report(report_dir: Path, report: dict[str, Any]) -> None:
    write_json(report_dir / "reconciliation.json", report)
    write_json(
        report_dir / "roster_rules.json", {"rules": ROSTER_RULES, "sha256": checksum(ROSTER_RULES)}
    )
    lines = [
        "# Prospective postgame roster reconciliation",
        "",
        f"As of {report['as_of_utc']}.",
        "",
        f"{report['candidate_count']} enrolled candidates; "
        f"{report['corroborated_rows']} corroborated. "
        "Participation validation is incomplete; forecasts remain disabled.",
        "",
        *[f"- {state}: {count}" for state, count in report["roster_counts"].items()],
        "",
        f"Enrollment registry SHA-256: `{report['registry_sha256']}`",
        "",
        f"Roster rules SHA-256: `{report['roster_rules_sha256']}`",
        "",
        "## Current source fingerprints",
        "",
        f"Schedules retrieved {report['sources']['schedules']['retrieved_at_utc']}; "
        f"SHA-256 `{report['sources']['schedules']['datasets']['schedules']['sha256']}`.",
        "",
        f"2026 stats retrieved {report['sources']['player_stats']['retrieved_at_utc']}; "
        f"SHA-256 `{report['sources']['player_stats']['player_stats']['sha256']}`.",
        "",
        f"Code fingerprints SHA-256: `{checksum(report['reconciliation_source_hashes'])}`. "
        "Full JSON retains each package file hash and cached roster/identity source metadata.",
        "",
        "## Source interpretation",
        "",
        *[f"- {key}: {value}" for key, value in ROSTER_RULES.items()],
        "",
        "The completed Week 4 contract check is retrospective source inspection, "
        "not an enrolled cohort outcome. "
        "Roster flags can corroborate participation or DNP, but do not verify an article's "
        "meaning or establish inactive status.",
        "",
        "ESPN event rosters and nflverse QB stats/identity distribution (CC-BY-4.0). "
        "Raw ESPN responses remain local; their rights are separate from "
        "nflverse's distribution license.",
        "",
    ]
    (report_dir / "reconciliation.md").write_text("\n".join(lines), encoding="utf-8")
