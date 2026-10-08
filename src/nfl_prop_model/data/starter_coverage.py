"""Full development starter coverage, with resumable retrospective evidence collection."""

import json
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import polars as pl

from nfl_prop_model.data import starter_audit as flagged
from nfl_prop_model.data.ingest_nfl import DEFAULT_SEASONS, Snapshot, load_snapshot
from nfl_prop_model.data.quarterback_games import build_target_table
from nfl_prop_model.data.roster_reconciliation import roster_qbs
from nfl_prop_model.data.schemas import DataQualityError, require_columns
from nfl_prop_model.data.status_reviews import checksum, timestamp
from nfl_prop_model.data.storage import load_frame, read_json, sha256_file, store_frame, write_json

CACHE = "starter_coverage_2022_2024"
RULES = {
    "version": 1,
    "scope": "all_development_team_game_starter_labels",
    "denominator": "Both teams in every completed regular-season 2022–2024 schedule game",
    "starter": "Exactly one explicit ESPN period-zero QB starter, valid and not DNP, "
    "with a unique ESPN/GSIS mapping and an existing same-team QB target row",
    "missing": "Missing events, sources, flags, mappings or targets retain unresolved slots",
    "timing": "Retrospective evidence; never proof of pregame publication or availability",
    "model": "No training-row changes, model fitting, 2025 access or forecasts",
}


def all_slots(snapshot: Snapshot) -> tuple[pl.DataFrame, list[dict[str, Any]]]:
    for frame in (snapshot.player_stats, snapshot.schedules):
        if set(frame["season"].to_list()) - set(DEFAULT_SEASONS):
            raise DataQualityError("Starter coverage uses only 2022–2024 development seasons")
    table, _ = build_target_table(snapshot.player_stats, snapshot.schedules)
    games = snapshot.schedules.filter(
        (pl.col("game_type") == "REG")
        & pl.col("home_score").is_not_null()
        & pl.col("away_score").is_not_null()
    )
    slots = []
    for game in games.iter_rows(named=True):
        try:
            kickoff = datetime.strptime(f"{game['gameday']} {game['gametime']}", "%Y-%m-%d %H:%M")
            kickoff_utc = (
                kickoff.replace(tzinfo=ZoneInfo("America/New_York")).astimezone(UTC).isoformat()
            )
        except (ValueError, TypeError):
            kickoff_utc = None
        for side in ("home", "away"):
            slots.append(
                {
                    "game_id": game["game_id"],
                    "season": game["season"],
                    "week": game["week"],
                    "side": side,
                    "team": game[f"{side}_team"],
                    "home_team": game["home_team"],
                    "away_team": game["away_team"],
                    "scheduled_player_id": game[f"{side}_qb_id"],
                    "scheduled_name": game.get(f"{side}_qb_name"),
                    "event_id": game.get("espn"),
                    "kickoff_utc": kickoff_utc,
                }
            )
    return table, sorted(slots, key=lambda row: (row["game_id"], row["team"]))


def _event_team(header: dict[str, Any], slot: dict[str, Any]) -> str:
    team_id = flagged.event_team(header, slot)
    try:
        if timestamp(header["competitions"][0]["date"]) != timestamp(slot["kickoff_utc"]):
            raise DataQualityError("Starter event kickoff differs from the recorded schedule")
    except (KeyError, TypeError, ValueError) as error:
        raise DataQualityError("Missing or changed starter event kickoff") from error
    return team_id


def _source(directory: Path, metadata: dict[str, Any], url: str) -> dict[str, Any]:
    if metadata["source"] != url or (directory / metadata["file"]).is_symlink():
        raise ValueError("Starter coverage source URL/path mismatch")
    if timestamp(metadata["retrieved_at_utc"]) > datetime.now(UTC):
        raise ValueError("Starter coverage evidence has a future retrieval timestamp")
    return flagged.load_source(directory, metadata)


def _envelope(path: Path, payload: dict[str, Any]) -> None:
    write_json(path, {"payload": payload, "sha256": checksum(payload)})


def _read_envelope(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise ValueError("Starter coverage manifest/checkpoint must not be a symlink")
    envelope = read_json(path)
    payload: dict[str, Any] = envelope["payload"]
    if envelope["sha256"] != checksum(payload):
        raise ValueError("Starter coverage manifest/checkpoint checksum mismatch")
    return payload


def fetch_starter_coverage(
    data_dir: Path,
    *,
    refresh: bool = False,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    snapshot = load_snapshot(data_dir, list(DEFAULT_SEASONS))
    _, slots = all_slots(snapshot)
    directory = data_dir / "raw" / CACHE
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise ValueError("Starter coverage cache must not be a symlink")
    # Reuse hash-verified identity/position evidence from the flagged audit when available.
    prior_dir = data_dir / "raw" / flagged.CACHE
    if (prior_dir / "manifest.json").exists():
        prior = read_json(prior_dir / "manifest.json")
        if (
            prior["identities"]["source"] != flagged.IDENTITY_SOURCE
            or prior["qb_position"]["source"] != flagged.POSITION_SOURCE
        ):
            raise ValueError("Historical identity/position source URL mismatch")
        identities = load_frame(prior_dir, prior["identities"])
        position = flagged.load_source(prior_dir, prior["qb_position"])
        identity_at = prior["retrieved_at_utc"]
    else:
        import nflreadpy as nfl
        from nflreadpy.config import update_config

        update_config(cache_mode="memory")
        nfl.clear_cache()
        identities = nfl.load_players().select("gsis_id", "espn_id", "display_name", "position")
        position = flagged.request_json(flagged.POSITION_SOURCE)
        identity_at = datetime.now(UTC).isoformat()
    require_columns(
        identities, "player identities", ("gsis_id", "espn_id", "display_name", "position"), ()
    )
    if position.get("id") != "8" or position.get("abbreviation") != "QB":
        raise DataQualityError("ESPN QB position identity changed")
    position_meta = flagged.store_source(
        directory, "qb-position", position, flagged.POSITION_SOURCE
    )
    if (prior_dir / "manifest.json").exists():
        position_meta["retrieved_at_utc"] = prior["qb_position"]["retrieved_at_utc"]
    identity_meta = {
        **store_frame(directory, "identities", identities),
        "source": flagged.IDENTITY_SOURCE,
        "retrieved_at_utc": identity_at,
    }
    grouped: dict[str, list[dict[str, Any]]] = {}
    event_games: dict[str, set[str]] = {}
    for slot in slots:
        grouped.setdefault(slot["game_id"], []).append(slot)
        event_games.setdefault(str(slot["event_id"]), set()).add(slot["game_id"])

    def collect(game_slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
        binding = {
            "slots": game_slots,
            "raw_manifest_sha256": checksum(snapshot.manifest),
            "rules_sha256": checksum(RULES),
        }
        checkpoint = directory / f"checkpoint-{checksum(binding)}.json"
        if checkpoint.exists() and not refresh:
            prior_checkpoint = _read_envelope(checkpoint)
            if prior_checkpoint["binding"] != binding:
                raise ValueError("Starter checkpoint context mismatch")
            keys = [(entry["game_id"], entry["team"]) for entry in prior_checkpoint["evidence"]]
            expected = {(slot["game_id"], slot["team"]) for slot in game_slots}
            if len(keys) != len(set(keys)) or set(keys) != expected:
                raise ValueError("Starter checkpoint team-game coverage mismatch")
            if all(row["state"] == "fetched" for row in prior_checkpoint["evidence"]):
                # Recheck hashes before skipping requests on a resumed collection.
                for entry in prior_checkpoint["evidence"]:
                    for kind in ("header", "roster"):
                        _source(directory, entry[kind], entry[kind]["source"])
                return list(prior_checkpoint["evidence"])
        event_id = game_slots[0]["event_id"]
        base = [
            {
                "game_id": slot["game_id"],
                "team": slot["team"],
                "state": "source_error",
                "error": None,
            }
            for slot in game_slots
        ]
        if not isinstance(event_id, str) or not event_id.isdecimal():
            for entry in base:
                entry["error"] = "missing_event_identity"
        elif len(event_games[event_id]) != 1:
            for entry in base:
                entry["error"] = "duplicate_event_identity"
        elif not all(slot["kickoff_utc"] for slot in game_slots):
            for entry in base:
                entry["error"] = "missing_recorded_kickoff"
        else:
            url = f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={event_id}"
            try:
                header = flagged.request_json(url).get("header", {})
                header_meta = flagged.store_source(directory, f"event-{event_id}", header, url)
                for entry in base:
                    entry["header"] = header_meta
                team_ids = [_event_team(header, slot) for slot in game_slots]
                for entry, team_id in zip(base, team_ids, strict=True):
                    roster_url = (
                        "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/"
                        f"events/{event_id}/competitions/{event_id}/competitors/{team_id}/roster?limit=1000"
                    )
                    try:
                        roster = flagged.request_json(roster_url)
                        entry["roster"] = flagged.store_source(
                            directory, f"roster-{event_id}-{team_id}", roster, roster_url
                        )
                        entry["state"] = "fetched"
                    except (OSError, ValueError) as error:
                        entry["error"] = str(error)
            except (OSError, ValueError) as error:
                for entry in base:
                    entry["error"] = str(error)
        _envelope(checkpoint, {"binding": binding, "evidence": base})
        return base

    evidence = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(collect, rows) for rows in grouped.values()]
        for completed, future in enumerate(as_completed(futures), start=1):
            evidence.extend(future.result())
            if progress:
                progress(completed, len(futures))
    manifest = {
        "format_version": 1,
        "scope": RULES["scope"],
        "rules": RULES,
        "rules_sha256": checksum(RULES),
        "retrieved_at_utc": datetime.now(UTC).isoformat(),
        "raw_manifest": snapshot.manifest,
        "slots_sha256": checksum({"slots": slots}),
        "identities": identity_meta,
        "qb_position": position_meta,
        "evidence": sorted(evidence, key=lambda row: (row["game_id"], row["team"])),
    }
    archived = directory / f"manifest-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}.json"
    _envelope(archived, manifest)
    if (
        refresh
        and (directory / "manifest.json").exists()
        and any(entry["state"] != "fetched" for entry in evidence)
    ):
        raise ConnectionError(
            f"Refresh incomplete; prior active manifest retained. Attempt: {archived}"
        )
    _envelope(directory / "manifest.json", manifest)
    return manifest


def load_starter_coverage(data_dir: Path) -> dict[str, Any]:
    snapshot = load_snapshot(data_dir, list(DEFAULT_SEASONS))
    table, slots = all_slots(snapshot)
    directory = data_dir / "raw" / CACHE
    manifest = _read_envelope(directory / "manifest.json")
    if directory.is_symlink():
        raise ValueError("Starter coverage cache must not be a symlink")
    if (
        manifest["format_version"] != 1
        or manifest["scope"] != RULES["scope"]
        or manifest["raw_manifest"] != snapshot.manifest
        or manifest["rules"] != RULES
        or manifest["rules_sha256"] != checksum(RULES)
        or manifest["slots_sha256"] != checksum({"slots": slots})
        or timestamp(manifest["retrieved_at_utc"]) > datetime.now(UTC)
    ):
        raise ValueError("Starter coverage manifest differs from development inputs/rules")
    position = _source(directory, manifest["qb_position"], flagged.POSITION_SOURCE)
    if position.get("id") != "8" or position.get("abbreviation") != "QB":
        raise DataQualityError("ESPN QB position identity changed")
    identity_meta = manifest["identities"]
    if (
        identity_meta["source"] != flagged.IDENTITY_SOURCE
        or Path(identity_meta["file"]).name != identity_meta["file"]
        or (directory / identity_meta["file"]).is_symlink()
        or timestamp(identity_meta["retrieved_at_utc"]) > datetime.now(UTC)
    ):
        raise ValueError("Starter identity source/path/time mismatch")
    identities = load_frame(directory, identity_meta)
    require_columns(
        identities, "player identities", ("gsis_id", "espn_id", "display_name", "position"), ()
    )
    entries = {(row["game_id"], row["team"]): row for row in manifest["evidence"]}
    if len(entries) != len(manifest["evidence"]) or set(entries) != {
        (row["game_id"], row["team"]) for row in slots
    }:
        raise DataQualityError("Starter coverage denominator differs from completed team-games")
    targets = {
        (row["game_id"], row["team"], row["player_id"]): row for row in table.iter_rows(named=True)
    }
    cases = []
    for slot in slots:
        entry = entries[slot["game_id"], slot["team"]]
        case = {
            **slot,
            "status": "unresolved_source_evidence",
            "error": entry.get("error"),
            "reconciled_player_id": None,
            "reconciled_name": None,
            "espn_starter_ids": [],
            "pregame_verified": False,
            "target_passing_yards": None,
            "sources": {key: entry[key] for key in ("header", "roster") if key in entry},
        }
        if "header" in entry:
            url = f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={slot['event_id']}"
            header = _source(directory, entry["header"], url)
            try:
                team_id = _event_team(header, slot)
            except DataQualityError as error:
                case["status"], case["error"] = "unresolved_event_context", str(error)
                cases.append(case)
                continue
            if "roster" in entry:
                url = (
                    "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/"
                    f"events/{slot['event_id']}/competitions/{slot['event_id']}/competitors/{team_id}/roster?limit=1000"
                )
                roster = _source(directory, entry["roster"], url)
                try:
                    roster_qbs(roster, slot, team_id)
                    starters = flagged.roster_starters(roster, slot, team_id)
                    case["espn_starter_ids"] = starters
                    case["status"] = "unresolved_no_unique_qb_starter"
                    if len(starters) == 1:
                        identity = flagged.starter_identity(identities, starters[0])
                        case["status"] = "unresolved_identity_mapping"
                        if identity and identity["gsis_id"] and identity["position"] == "QB":
                            case["reconciled_player_id"] = identity["gsis_id"]
                            case["reconciled_name"] = identity["display_name"]
                            target = targets.get(
                                (slot["game_id"], slot["team"], identity["gsis_id"])
                            )
                            case["status"] = "unresolved_target_missing"
                            if target is not None:
                                case["target_passing_yards"] = target["target_passing_yards"]
                                case["status"] = (
                                    "verified_schedule_label"
                                    if identity["gsis_id"] == slot["scheduled_player_id"]
                                    else "corrected_schedule_label"
                                )
                except DataQualityError as error:
                    case["status"], case["error"] = "unresolved_roster_or_identity", str(error)
        cases.append(case)
    status_counts = Counter(case["status"] for case in cases)
    resolved = {
        (case["game_id"], case["team"]): case["reconciled_player_id"]
        for case in cases
        if case["status"] in {"verified_schedule_label", "corrected_schedule_label"}
    }
    overlay = [
        {
            "game_id": row["game_id"],
            "team": row["team"],
            "player_id": row["player_id"],
            "classification": (
                "starter_unresolved"
                if (row["game_id"], row["team"]) not in resolved
                else "roster_starter"
                if resolved[row["game_id"], row["team"]] == row["player_id"]
                else "other_recorded_qb"
            ),
        }
        for row in table.iter_rows(named=True)
    ]
    package = Path(__file__).parents[1]
    return {
        "format_version": 1,
        "scope": RULES["scope"],
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "audit_source_hashes": {
            path.relative_to(package).as_posix(): sha256_file(path)
            for path in sorted(package.rglob("*.py"))
        },
        "production_enabled": False,
        "rules": RULES,
        "status_counts": dict(sorted(status_counts.items())),
        "counts": {
            "historical_qb_rows": table.height,
            "completed_team_game_slots": len(slots),
            "verified_schedule_labels": status_counts["verified_schedule_label"],
            "corrected_schedule_labels": status_counts["corrected_schedule_label"],
            "resolved_slots": len(resolved),
            "needs_review": len(slots) - len(resolved),
        },
        "appearance_counts": dict(
            sorted(Counter(row["classification"] for row in overlay).items())
        ),
        "cases": cases,
        "appearance_overlay": overlay,
        "evidence_manifest": manifest,
        "audit_source_sha256": sha256_file(Path(__file__)),
        "limitations": [
            "Every completed development team-game is retained, including missing evidence.",
            "ESPN starter flags are retrospective; pregame knowledge is not established.",
            "Missing targets remain unknown, never zero. Original rows, targets, features "
            "and starter labels are unchanged.",
            "The appearance overlay is descriptive only; it does not refit or filter a model.",
            "No 2025 outcomes, current outcomes, calibration selection or forecasts are accessed.",
        ],
    }


def write_starter_coverage(report_dir: Path, report: dict[str, Any]) -> None:
    write_json(report_dir / "starter_coverage.json", report)
    lines = [
        "# Full historical starter coverage · 2022–2024",
        "",
        f"Generated {report['generated_at_utc']}. Retrospective evidence only.",
        "",
        *[f"- {name}: {value}" for name, value in report["counts"].items()],
        "",
        "| Season | Team-games | Verified schedule label | Corrected label | Unresolved |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for season in DEFAULT_SEASONS:
        cases = [row for row in report["cases"] if row["season"] == season]
        counts = Counter(row["status"] for row in cases)
        lines.append(
            f"| {season} | {len(cases)} | {counts['verified_schedule_label']} | "
            f"{counts['corrected_schedule_label']} | "
            f"{sum(count for state, count in counts.items() if state.startswith('unresolved_'))} |"
        )
    lines.extend(
        [
            "",
            "Appearance overlay: " + str(report["appearance_counts"]),
            "",
            "## Unresolved cases",
            "",
        ]
    )
    lines.extend(
        f"- {row['game_id']} · {row['team']}: {row['status']} "
        f"({row['error'] or 'no unique mapping/target'})"
        for row in report["cases"]
        if row["status"].startswith("unresolved_")
    )
    lines.extend(
        [
            "",
            *report["limitations"],
            "",
            f"[nflverse identity mapping]({flagged.IDENTITY_SOURCE}); nflverse CC-BY-4.0. "
            "ESPN raw rosters remain local. JSON includes all slots, source URLs, hashes "
            "and retrieval times.",
            "",
        ]
    )
    (report_dir / "starter_coverage.md").write_text("\n".join(lines), encoding="utf-8")


def export_starter_labels(report_dir: Path, report: dict[str, Any]) -> None:
    """Compact public cross-reference; raw ESPN responses remain in the ignored cache."""
    report_dir.mkdir(parents=True, exist_ok=True)
    fields = (
        "game_id",
        "season",
        "week",
        "team",
        "event_id",
        "kickoff_utc",
        "scheduled_player_id",
        "reconciled_player_id",
        "status",
        "error",
        "sources",
    )
    labels = [{name: row[name] for name in fields} for row in report["cases"]]
    path = report_dir / "starter_labels.jsonl"
    path.write_bytes(
        "".join(
            json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
            for row in labels
        ).encode("utf-8")
    )
    summary = {
        name: report[name]
        for name in (
            "format_version",
            "scope",
            "generated_at_utc",
            "rules",
            "counts",
            "status_counts",
            "appearance_counts",
            "audit_source_sha256",
            "audit_source_hashes",
            "limitations",
            "production_enabled",
        )
    }
    manifest = report["evidence_manifest"]
    summary["source_manifest"] = {
        name: manifest[name]
        for name in ("raw_manifest", "identities", "qb_position", "retrieved_at_utc")
    }
    summary["labels"] = {"file": path.name, "sha256": sha256_file(path), "rows": len(labels)}
    write_json(report_dir / "summary.json", summary)
