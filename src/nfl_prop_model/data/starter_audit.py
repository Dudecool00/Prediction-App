"""Reconcile flagged historical starter labels without changing training data."""

import hashlib
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlopen
from uuid import uuid4

import polars as pl

from nfl_prop_model.data.ingest_nfl import DEFAULT_SEASONS, Snapshot, load_snapshot
from nfl_prop_model.data.quarterback_games import build_target_table
from nfl_prop_model.data.schemas import DataQualityError, require_columns
from nfl_prop_model.data.storage import (
    load_frame,
    read_json,
    sha256_file,
    store_frame,
    write_json,
)

CACHE = "starter_evidence_2022_2024"
IDENTITY_SOURCE = "https://github.com/nflverse/nflverse-data/releases/tag/players"
POSITION_SOURCE = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/positions/8"
TEAM_ALIASES = {"WSH": "WAS", "LAR": "LA"}


def request_json(url: str) -> dict[str, Any]:
    with urlopen(url, timeout=30) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise DataQualityError("ESPN returned a non-object response")
    return value


def flagged_slots(snapshot: Snapshot) -> tuple[pl.DataFrame, list[dict[str, Any]], int]:
    for frame in (snapshot.player_stats, snapshot.schedules):
        if set(frame["season"].to_list()) - set(DEFAULT_SEASONS):
            raise DataQualityError("Starter audit uses only 2022–2024 development seasons")
    table, counts = build_target_table(snapshot.player_stats, snapshot.schedules)
    games = {row["game_id"]: row for row in snapshot.schedules.iter_rows(named=True)}
    slots = []
    for missing in counts["scheduled_starters_without_target_rows"]:
        game = games[missing["game_id"]]
        side = "home" if missing["team"] == game["home_team"] else "away"
        event_id = game.get("espn")
        if not isinstance(event_id, str) or not event_id.isdecimal():
            raise DataQualityError(f"{game['game_id']}: missing ESPN event identity")
        slots.append(
            {
                **missing,
                "season": game["season"],
                "week": game["week"],
                "side": side,
                "scheduled_name": game.get(f"{side}_qb_name"),
                "event_id": event_id,
                "home_team": game["home_team"],
                "away_team": game["away_team"],
            }
        )
    return (
        table,
        sorted(slots, key=lambda row: (row["game_id"], row["team"])),
        (2 * counts["completed_regular_schedule_games"]),
    )


def event_team(header: dict[str, Any], slot: dict[str, Any]) -> str:
    """Validate the event and both sides before using a roster's starter flags."""
    try:
        competition = header["competitions"][0]
        if (
            str(header["id"]) != slot["event_id"]
            or str(competition["id"]) != slot["event_id"]
            or header["season"]["year"] != slot["season"]
            or header["season"]["type"] != 2
            or header.get("week") != slot["week"]
            or competition["status"]["type"]["completed"] is not True
        ):
            raise DataQualityError("Starter evidence event/season/completion mismatch")
        competitors = competition["competitors"]
        if len(competitors) != 2:
            raise DataQualityError("Starter evidence must identify exactly two teams")
        teams = {}
        for competitor in competitors:
            side = competitor["homeAway"]
            abbreviation = competitor["team"]["abbreviation"]
            team = TEAM_ALIASES.get(abbreviation, abbreviation)
            team_id = str(competitor["team"]["id"])
            if side in teams or not team_id.isdecimal() or team != slot[f"{side}_team"]:
                raise DataQualityError("Starter evidence team/side mismatch")
            teams[side] = team_id
        if set(teams) != {"home", "away"}:
            raise DataQualityError("Starter evidence lacks a home or away team")
        return str(teams[slot["side"]])
    except (KeyError, IndexError, TypeError) as error:
        raise DataQualityError("Malformed ESPN event identity") from error


def store_source(directory: Path, name: str, value: dict[str, Any], url: str) -> dict[str, Any]:
    temporary = directory / f"source-{uuid4().hex}.json"
    try:
        write_json(temporary, value)
        digest = sha256_file(temporary)
        destination = directory / f"{name}-{digest}.json"
        if destination.exists():
            if sha256_file(destination) != digest:
                raise ValueError(f"Cache checksum mismatch: {destination}")
        else:
            temporary.replace(destination)
        return {
            "file": destination.name,
            "sha256": digest,
            "source": url,
            "retrieved_at_utc": datetime.now(UTC).isoformat(),
        }
    finally:
        temporary.unlink(missing_ok=True)


def load_source(directory: Path, metadata: dict[str, Any]) -> dict[str, Any]:
    filename = metadata["file"]
    if not isinstance(filename, str) or Path(filename).name != filename:
        raise ValueError("Invalid starter evidence cache filename")
    path = directory / filename
    if sha256_file(path) != metadata["sha256"]:
        raise ValueError(f"Cache checksum mismatch: {path}; restore or use --refresh")
    return read_json(path)


def fetch_starter_evidence(data_dir: Path, *, refresh: bool = False) -> dict[str, Any]:
    directory = data_dir / "raw" / CACHE
    if (directory / "manifest.json").exists() and not refresh:
        return read_json(directory / "manifest.json")
    snapshot = load_snapshot(data_dir, list(DEFAULT_SEASONS))
    _, slots, _ = flagged_slots(snapshot)
    import nflreadpy as nfl
    from nflreadpy.config import update_config

    update_config(cache_mode="memory")
    nfl.clear_cache()
    identities = nfl.load_players().select("gsis_id", "espn_id", "display_name", "position")
    require_columns(identities, "player identities", tuple(identities.columns), ())
    position = request_json(POSITION_SOURCE)
    if position.get("id") != "8" or position.get("abbreviation") != "QB":
        raise DataQualityError("ESPN QB position identity changed")
    position_metadata = store_source(directory, "qb-position", position, POSITION_SOURCE)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for slot in slots:
        grouped.setdefault(slot["event_id"], []).append(slot)

    def fetch_event(event_slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
        event_id = event_slots[0]["event_id"]
        url = f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={event_id}"
        summary = request_json(url)
        header = summary.get("header", {})
        # Only event identity is retained from the summary; no odds or market fields.
        team_ids = [event_team(header, slot) for slot in event_slots]
        header_metadata = store_source(directory, f"event-{event_id}", header, url)
        evidence = []
        for slot, team_id in zip(event_slots, team_ids, strict=True):
            roster_url = (
                "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/"
                f"events/{event_id}/competitions/{event_id}/competitors/{team_id}/roster?limit=1000"
            )
            roster = request_json(roster_url)
            roster_starters(roster, slot, team_id)
            evidence.append(
                {
                    "game_id": slot["game_id"],
                    "team": slot["team"],
                    "header": header_metadata,
                    "roster": store_source(
                        directory, f"roster-{event_id}-{team_id}", roster, roster_url
                    ),
                }
            )
        return evidence

    with ThreadPoolExecutor(max_workers=4) as pool:
        evidence = [entry for result in pool.map(fetch_event, grouped.values()) for entry in result]
    retrieved = datetime.now(UTC)
    manifest = {
        "format_version": 1,
        "scope": "flagged_historical_starter_labels",
        "retrieved_at_utc": retrieved.isoformat(),
        "raw_manifest": snapshot.manifest,
        "identities": {
            **store_frame(directory, "identities", identities),
            "source": IDENTITY_SOURCE,
        },
        "qb_position": position_metadata,
        "evidence": evidence,
    }
    write_json(directory / f"manifest-{retrieved.strftime('%Y%m%dT%H%M%S%fZ')}.json", manifest)
    write_json(directory / "manifest.json", manifest)
    return manifest


def roster_starters(roster: dict[str, Any], slot: dict[str, Any], team_id: str) -> list[str]:
    expected_path = (
        "/v2/sports/football/leagues/nfl/"
        f"events/{slot['event_id']}/competitions/{slot['event_id']}/competitors/{team_id}/roster"
    )
    reference = urlparse(roster.get("$ref", ""))
    if reference.hostname != "sports.core.api.espn.com" or reference.path != expected_path:
        raise DataQualityError("Starter roster event/team identity mismatch")
    entries = roster.get("entries")
    if not isinstance(entries, list):
        raise DataQualityError("ESPN roster lacks entries")
    starters = []
    qb_ids = set()
    for entry in entries:
        position = urlparse(entry.get("position", {}).get("$ref", ""))
        if position.path != "/v2/sports/football/leagues/nfl/positions/8":
            continue
        if position.hostname != "sports.core.api.espn.com":
            raise DataQualityError("Unknown QB position source")
        player_id = str(entry.get("playerId", ""))
        if not player_id.isdecimal() or player_id in qb_ids:
            raise DataQualityError("Missing or duplicate ESPN QB identity")
        qb_ids.add(player_id)
        if type(entry.get("starter")) is not bool or type(entry.get("didNotPlay")) is not bool:
            raise DataQualityError("ESPN QB lacks explicit starter/participation flags")
        if entry["starter"]:
            if entry.get("period") != 0 or entry.get("valid") is not True or entry["didNotPlay"]:
                raise DataQualityError("Contradictory ESPN QB starter flags")
            starters.append(player_id)
    return starters


def load_starter_audit(data_dir: Path) -> dict[str, Any]:
    """Rebuild a report from hash-verified local evidence; never fetch or edit history."""
    snapshot = load_snapshot(data_dir, list(DEFAULT_SEASONS))
    table, slots, total_slots = flagged_slots(snapshot)
    directory = data_dir / "raw" / CACHE
    manifest = read_json(directory / "manifest.json")
    if manifest.get("format_version") != 1 or manifest.get("raw_manifest") != snapshot.manifest:
        raise ValueError("Starter evidence does not match raw data; run starter-audit --refresh")
    position = load_source(directory, manifest["qb_position"])
    if position.get("id") != "8" or position.get("abbreviation") != "QB":
        raise DataQualityError("ESPN QB position identity changed")
    identities = load_frame(directory, manifest["identities"])
    require_columns(
        identities, "player identities", ("gsis_id", "espn_id", "display_name", "position"), ()
    )
    evidence = {(row["game_id"], row["team"]): row for row in manifest["evidence"]}
    expected = {(row["game_id"], row["team"]) for row in slots}
    if set(evidence) != expected or len(evidence) != len(manifest["evidence"]):
        raise DataQualityError("Starter evidence coverage differs from flagged cases")
    cases = []
    for slot in slots:
        entry = evidence[slot["game_id"], slot["team"]]
        header = load_source(directory, entry["header"])
        team_id = event_team(header, slot)
        starters = roster_starters(load_source(directory, entry["roster"]), slot, team_id)
        case = {
            "game_id": slot["game_id"],
            "season": slot["season"],
            "team": slot["team"],
            "scheduled_player_id": slot["player_id"],
            "scheduled_name": slot["scheduled_name"],
            "espn_starter_ids": starters,
            "reconciled_player_id": None,
            "reconciled_name": None,
            "status": "unresolved_no_unique_qb_starter",
            "pregame_verified": False,
            "sources": {"header": entry["header"], "roster": entry["roster"]},
        }
        if len(starters) == 1:
            mapped = identities.filter(pl.col("espn_id") == starters[0])
            if mapped.height > 1:
                raise DataQualityError(f"Ambiguous ESPN-to-GSIS mapping: {starters[0]}")
            case["status"] = "unresolved_identity_mapping"
            if mapped.height == 1:
                identity = mapped.row(0, named=True)
                if (
                    identity["gsis_id"]
                    and identity["display_name"]
                    and identity["position"] == "QB"
                ):
                    if identities.filter(pl.col("gsis_id") == identity["gsis_id"]).height != 1:
                        raise DataQualityError("Ambiguous GSIS-to-ESPN mapping")
                    case["reconciled_player_id"] = identity["gsis_id"]
                    case["reconciled_name"] = identity["display_name"]
                    target = table.filter(
                        (pl.col("game_id") == slot["game_id"])
                        & (pl.col("team") == slot["team"])
                        & (pl.col("player_id") == identity["gsis_id"])
                    )
                    case["status"] = (
                        "corrected_schedule_label"
                        if target.height == 1
                        else "unresolved_target_missing"
                    )
        cases.append(case)
    status_counts = Counter(row["status"] for row in cases)
    return {
        "format_version": 1,
        "scope": "flagged_historical_starter_labels",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "counts": {
            "historical_qb_rows": table.height,
            "completed_team_game_slots": total_slots,
            "flagged_starter_labels": len(cases),
            "corrected_schedule_labels": status_counts["corrected_schedule_label"],
            "needs_review": len(cases) - status_counts["corrected_schedule_label"],
            "unflagged_slots_not_independently_verified": total_slots - len(cases),
        },
        "status_counts": dict(status_counts),
        "cases": cases,
        "evidence_manifest": manifest,
        "audit_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "limitations": [
            "ESPN event-roster starter flags are retrospective evidence, not pregame knowledge.",
            "Only schedule-listed starters missing a QB target row are reviewed. "
            "Other schedule starter labels remain unverified, even when a target row exists.",
            "Training rows, targets, features, and schedule_reported_starter remain unchanged. "
            "Corrections are a separate diagnostic overlay, never a predictor or cohort filter.",
            "No starter is inferred from passing volume, first passer, depth rank, "
            "or player names. "
            "Missing or conflicting starter/identity evidence remains unresolved.",
            "No 2025 outcomes, current-season player statistics, forecasts, or EV are loaded.",
            "ESPN's public endpoints have no versioned schema guarantee. Refresh requires "
            "network access; local evidence is retained by hash with retrieval timestamps.",
        ],
    }


def write_starter_audit(report_dir: Path, report: dict[str, Any]) -> None:
    write_json(report_dir / "starter_audit.json", report)
    counts = report["counts"]
    lines = [
        "# Historical starter reconciliation · 2022–2024",
        "",
        f"Generated {report['generated_at_utc']}. "
        f"Evidence retrieved {report['evidence_manifest']['retrieved_at_utc']}.",
        "",
        f"{counts['flagged_starter_labels']} flagged labels; "
        f"{counts['corrected_schedule_labels']} reconciled to an existing QB target row; "
        f"{counts['needs_review']} need review.",
        f"All {counts['historical_qb_rows']} historical QB rows are retained. "
        f"The other {counts['unflagged_slots_not_independently_verified']} team-game slots "
        "have not been independently verified by this audit.",
        "",
        "| Game | Team | Schedule-listed QB | ESPN-listed starter | Resolution |",
        "| --- | --- | --- | --- | --- |",
    ]
    for case in report["cases"]:
        lines.append(
            f"| {case['game_id']} | {case['team']} | {case['scheduled_name']} | "
            f"{case['reconciled_name'] or 'Unresolved'} | {case['status']} |"
        )
    lines += ["", "## Limits", "", *[f"- {item}" for item in report["limitations"]]]
    lines += [
        "",
        "## Sources",
        "",
        f"ESPN event rosters and [nflverse player identities]({IDENTITY_SOURCE}); "
        "ESPN and nflverse contributors. nflverse identities are distributed under CC-BY-4.0. "
        "ESPN's source material remains subject to its terms; raw rosters are kept locally. "
        "The report is a derived ID cross-reference. JSON retains per-case URLs, hashes, "
        "retrieval times, and the original statistics/schedule manifest.",
        "",
    ]
    (report_dir / "starter_audit.md").write_text("\n".join(lines), encoding="utf-8")
