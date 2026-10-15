"""Dated listed QB leaders, never confirmed starters or postgame roster labels."""

import hashlib
import io
from bisect import bisect_left
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from nfl_prop_model.data.schemas import DataQualityError, require_keys
from nfl_prop_model.data.storage import load_frame, read_json, sha256_file, store_frame, write_json
from nfl_prop_model.data.team_games import TEAM_ALIASES, team_source_hash
from nfl_prop_model.data.team_pregame import _download

DEPTH_URL = "https://github.com/nflverse/nflverse-data/releases/download/depth_charts/depth_charts_2025.parquet"
DEPTH_POLICY = {
    "scope": "2025 dated ESPN depth-chart archive via nflverse, including January 2026",
    "timestamp": "dt is record-load time, not verified source publication",
    "availability": "assume loaded time +24h strictly before cutoff; zero-delay sensitivity",
    "freshness": "latest eligible whole team snapshot, no more than 72h old at cutoff",
    "identity": "ESPN ID; exclude GSIS mapping and names remapped retroactively by producer",
    "leader": "unique distinct QB ESPN ID at rank one across slots; ties/missing/stale unknown",
    "roster": "depth chart is not complete active/IR roster or a game-start confirmation",
    "missing": "do not carry a removed player or fall back past an empty/ambiguous snapshot",
}
SCHEDULE_FIELDS = ("game_id", "season", "week", "home_team", "away_team", "kickoff_utc")


def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DataQualityError("Availability cutoff must be timezone-aware")
    return value.astimezone(UTC)


def fetch_depth_source(data_dir: Path) -> Path:
    directory = data_dir / "raw" / "team_availability" / "2025"
    path = directory / "manifest.json"
    if path.exists():
        load_depth_source(path)
        return path
    payload = _download(DEPTH_URL)
    frame = pl.read_parquet(io.BytesIO(payload))
    required = {"dt", "team", "pos_abb", "espn_id", "pos_slot", "pos_rank"}
    if missing := required - set(frame.columns):
        raise DataQualityError(f"Dated depth source missing fields: {sorted(missing)}")
    qb = frame.filter(pl.col("pos_abb") == "QB").select(
        "dt", "team", "espn_id", "pos_slot", "pos_rank"
    )
    inventory = (
        frame.group_by("dt", "team")
        .agg(pl.len().alias("chart_rows"), (pl.col("pos_abb") == "QB").sum().alias("qb_rows"))
        .sort("dt", "team")
    )
    DepthSnapshots(qb, inventory)
    write_json(
        path,
        {
            "format_version": 1,
            "url": DEPTH_URL,
            "retrieved_at_utc": datetime.now(UTC).isoformat(),
            "upstream_sha256": hashlib.sha256(payload).hexdigest(),
            "download_bytes": len(payload),
            "upstream_schema": {key: str(value) for key, value in frame.schema.items()},
            "artifacts": {
                "quarterbacks": store_frame(directory, "quarterbacks", qb),
                "inventory": store_frame(directory, "inventory", inventory),
            },
        },
    )
    return path


def load_depth_source(path: Path) -> tuple[pl.DataFrame, pl.DataFrame]:
    manifest = read_json(path)
    if manifest["format_version"] != 1 or manifest["url"] != DEPTH_URL:
        raise DataQualityError("Unsupported dated depth source")
    qb = load_frame(path.parent, manifest["artifacts"]["quarterbacks"])
    inventory = load_frame(path.parent, manifest["artifacts"]["inventory"])
    DepthSnapshots(qb, inventory)
    return qb, inventory


def _normalize(frame: pl.DataFrame) -> pl.DataFrame:
    if (
        frame.schema["dt"] != pl.String
        or frame.filter(
            pl.col("dt").is_null() | ~pl.col("dt").str.contains(r"(?:Z|[+-]\d{2}:\d{2})$")
        ).height
    ):
        raise DataQualityError("Depth load times require explicit ISO UTC/offset timestamps")
    return frame.with_columns(
        pl.col("dt").str.to_datetime(format="%+", time_zone="UTC").alias("loaded_at_utc"),
        pl.col("team").replace(TEAM_ALIASES),
    )


class DepthSnapshots:
    def __init__(self, qb: pl.DataFrame, inventory: pl.DataFrame, *, delay_hours: int = 24) -> None:
        if delay_hours not in {0, 24}:
            raise ValueError("Depth audit supports the declared 24h policy or 0h sensitivity")
        for frame, fields in (
            (qb, {"dt", "team", "espn_id", "pos_slot", "pos_rank"}),
            (inventory, {"dt", "team", "chart_rows", "qb_rows"}),
        ):
            if missing := fields - set(frame.columns):
                raise DataQualityError(f"Depth snapshot fields missing: {sorted(missing)}")
            if frame.filter(pl.col("team").is_null() | (pl.col("team") == "")).height:
                raise DataQualityError("Depth snapshot team is missing")
        qb, inventory = _normalize(qb), _normalize(inventory)
        require_keys(inventory, ["loaded_at_utc", "team"], "depth snapshot inventory")
        require_keys(
            qb, ["loaded_at_utc", "team", "espn_id", "pos_slot", "pos_rank"], "depth QB rows"
        )
        if qb.filter(
            pl.col("espn_id").is_null()
            | ~pl.col("espn_id").str.contains(r"^\d+$")
            | pl.any_horizontal(
                pl.col("pos_rank", "pos_slot").is_null()
                | ~pl.col("pos_rank", "pos_slot").is_finite()
                | (pl.col("pos_rank", "pos_slot") < 1)
                | ((pl.col("pos_rank", "pos_slot") % 1) != 0)
            )
        ).height:
            raise DataQualityError("Depth QB identity/rank/slot is invalid")
        if inventory.filter(
            pl.any_horizontal(
                pl.col("chart_rows", "qb_rows").is_null()
                | ~pl.col("chart_rows", "qb_rows").is_finite()
                | (pl.col("chart_rows", "qb_rows") < 0)
                | ((pl.col("chart_rows", "qb_rows") % 1) != 0)
            )
            | (pl.col("chart_rows") < 1)
            | (pl.col("qb_rows") > pl.col("chart_rows"))
        ).height:
            raise DataQualityError("Depth snapshot inventory counts invalid")
        counts = qb.group_by("loaded_at_utc", "team").len().rename({"len": "actual_qb_rows"})
        if counts.join(inventory, on=["loaded_at_utc", "team"], how="anti").height:
            raise DataQualityError("QB rows lack whole-team snapshot inventory")
        checked = inventory.join(counts, on=["loaded_at_utc", "team"], how="left").with_columns(
            pl.col("actual_qb_rows").fill_null(0)
        )
        if checked.filter(pl.col("qb_rows") != pl.col("actual_qb_rows")).height:
            raise DataQualityError("Depth snapshot QB counts mismatch")
        leaders = (
            qb.filter(pl.col("pos_rank") == 1)
            .group_by("loaded_at_utc", "team")
            .agg(pl.col("espn_id").unique().sort().alias("leaders"))
        )
        self.by_team: dict[str, list[dict[str, Any]]] = {}
        for frame in (
            inventory.join(leaders, on=["loaded_at_utc", "team"], how="left")
            .sort("loaded_at_utc")
            .partition_by("team")
        ):
            self.by_team[frame["team"][0]] = frame.to_dicts()
        self.times = {
            team: [r["loaded_at_utc"] + timedelta(hours=delay_hours) for r in rows]
            for team, rows in self.by_team.items()
        }
        self.delay_hours = delay_hours

    def as_of(self, team: str, cutoff: datetime) -> dict[str, Any]:
        cutoff = aware(cutoff)
        team = TEAM_ALIASES.get(team, team)
        index = bisect_left(self.times.get(team, []), cutoff) - 1
        if index < 0:
            return {
                "status": "unavailable",
                "listed_qb_espn_id": None,
                "loaded_at_utc": None,
                "assumed_available_at_utc": None,
                "age_hours": None,
                "reason": "no eligible dated team snapshot",
            }
        row = self.by_team[team][index]
        loaded = row["loaded_at_utc"]
        age = (cutoff - loaded).total_seconds() / 3600
        leaders = row["leaders"] or []
        reason = (
            "stale snapshot"
            if age > 72
            else "empty QB snapshot"
            if row["qb_rows"] == 0
            else "no rank-one QB"
            if not leaders
            else "ambiguous rank-one QBs"
            if len(leaders) != 1
            else None
        )
        return {
            "status": "listed_leader" if reason is None else "unavailable",
            "listed_qb_espn_id": leaders[0] if reason is None else None,
            "loaded_at_utc": loaded,
            "assumed_available_at_utc": loaded + timedelta(hours=self.delay_hours),
            "age_hours": age,
            "reason": reason,
        }


def load_schedule_only(data_dir: Path) -> tuple[pl.DataFrame, dict[str, str]]:
    """Read only metadata columns; score, actual QB and outcome columns are never selected."""
    path = data_dir / "processed" / "team_epa" / "manifest.json"
    manifest = read_json(path)
    if manifest["pipeline_source_sha256"] != team_source_hash():
        raise DataQualityError("Canonical team schedule source changed")
    metadata = manifest["artifacts"]["games"]
    artifact = path.parent / metadata["file"]
    if sha256_file(artifact) != metadata["sha256"]:
        raise DataQualityError("Schedule metadata artifact checksum mismatch")
    games = (
        pl.scan_parquet(artifact).select(SCHEDULE_FIELDS).filter(pl.col("season") == 2025).collect()
    )
    require_keys(games, ["game_id"], "2025 schedule metadata")
    if games.height != 272 or games["kickoff_utc"].null_count():
        raise DataQualityError("Availability audit requires the complete 272-game 2025 schedule")
    return games.sort("season", "week", "game_id"), {
        "manifest": str(path.resolve()),
        "manifest_sha256": sha256_file(path),
        "artifact_sha256": metadata["sha256"],
    }
