"""Independent team EPA snapshots and schedule-first game outcomes."""

import hashlib
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

import polars as pl

from nfl_prop_model.data.schemas import DataQualityError, require_keys
from nfl_prop_model.data.storage import load_frame, read_json, store_frame, write_json

DEFAULT_TEAM_SEASONS = tuple(range(2016, 2025))
PBP_COLUMNS = (
    "game_id",
    "play_id",
    "season",
    "week",
    "season_type",
    "home_team",
    "away_team",
    "posteam",
    "defteam",
    "epa",
    "pass",
    "rush",
    "qb_dropback",
    "qb_kneel",
    "qb_spike",
    "play_type",
    "two_point_attempt",
)
SCHEDULE_COLUMNS = (
    "game_id",
    "season",
    "week",
    "game_type",
    "gameday",
    "gametime",
    "home_team",
    "away_team",
    "home_score",
    "away_score",
    "location",
)
TEAM_ALIASES = {"LAR": "LA", "STL": "LA", "OAK": "LV", "SD": "LAC"}
PLAY_POLICY = {
    "offense": "pass or rush flag; finite EPA; exclude no_play, kneels, spikes, two-point tries",
    "dropback": "eligible offense with qb_dropback == 1; includes sacks and scrambles",
    "rush": "eligible offense with rush == 1 and qb_dropback != 1",
    "defense": "opponent offensive EPA on identical eligible plays; lower allowed is better",
    "penalties": "retain penalties on eligible plays; exclude no_play/nullified plays",
    "availability": "retrospective assumption: kickoff + 24 hours; not upstream publication time",
    "target": "final scores including overtime; regular season only",
}


def team_seasons(seasons: list[int]) -> list[int]:
    if not seasons or any(year < 1999 or year > datetime.now(UTC).year for year in seasons):
        raise ValueError("Team seasons must be between 1999 and the current year")
    return sorted(set(seasons))


def team_source_hash() -> str:
    paths = [Path(__file__), Path(__file__).parents[1] / "features" / "team_epa.py"]
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def normalize_teams(frame: pl.DataFrame, columns: list[str]) -> pl.DataFrame:
    return frame.with_columns(pl.col(name).replace(TEAM_ALIASES) for name in columns)


def schedule_games(schedules: pl.DataFrame) -> pl.DataFrame:
    missing = set(SCHEDULE_COLUMNS) - set(schedules.columns)
    if missing:
        raise DataQualityError(f"Team schedules missing columns: {sorted(missing)}")
    require_keys(schedules, ["game_id"], "team schedules")
    games = normalize_teams(
        schedules.filter(pl.col("game_type") == "REG").select(SCHEDULE_COLUMNS),
        ["home_team", "away_team"],
    )
    invalid = (
        pl.col("home_team").is_null()
        | pl.col("away_team").is_null()
        | (pl.col("home_team") == pl.col("away_team"))
        | ~pl.col("location").is_in(["Home", "Neutral"]).fill_null(False)
        | (pl.col("home_score").is_null() != pl.col("away_score").is_null())
        | pl.any_horizontal(
            (pl.col("home_score", "away_score") < 0)
            | (~pl.col("home_score", "away_score").is_finite()).fill_null(False)
        )
    )
    if games.filter(invalid).height:
        raise DataQualityError("Invalid team matchup, location, or scores in schedules")
    games = games.with_columns(
        pl.concat_str("gameday", "gametime", separator=" ")
        .str.to_datetime("%Y-%m-%d %H:%M", strict=False)
        .dt.replace_time_zone("America/New_York")
        .dt.convert_time_zone("UTC")
        .alias("kickoff_utc"),
        pl.col("home_score").cast(pl.Float64),
        pl.col("away_score").cast(pl.Float64),
        (pl.col("location") == "Neutral").alias("is_neutral_site"),
    )
    if games.filter(pl.col("home_score").is_not_null() & pl.col("kickoff_utc").is_null()).height:
        raise DataQualityError("Completed team game has a missing/malformed kickoff")
    return games.with_columns(
        (pl.col("kickoff_utc") + pl.duration(hours=24)).alias("result_available_at_utc"),
        (pl.col("home_score") - pl.col("away_score")).alias("target_home_margin"),
    ).sort("kickoff_utc", "game_id")


def build_team_games(
    pbp: pl.DataFrame,
    schedules: pl.DataFrame,
) -> tuple[pl.DataFrame, pl.DataFrame, dict[str, Any]]:
    missing = set(PBP_COLUMNS) - set(pbp.columns)
    if missing:
        raise DataQualityError(f"Team play-by-play missing columns: {sorted(missing)}")
    games = schedule_games(schedules)
    completed = games.filter(pl.col("home_score").is_not_null())
    if completed.is_empty():
        raise DataQualityError("No completed regular-season team games")
    plays = normalize_teams(
        pbp.filter(pl.col("season_type") == "REG").select(PBP_COLUMNS),
        ["home_team", "away_team", "posteam", "defteam"],
    )
    require_keys(plays, ["game_id", "play_id"], "team plays")
    if plays.join(games.select("game_id"), on="game_id", how="anti").height:
        raise DataQualityError("Regular-season plays missing from schedules")
    plays = plays.join(
        completed.select(
            "game_id",
            pl.col("season").alias("s_season"),
            pl.col("week").alias("s_week"),
            pl.col("home_team").alias("s_home"),
            pl.col("away_team").alias("s_away"),
        ),
        on="game_id",
        how="inner",
        validate="m:1",
    )
    if plays.filter(
        (
            (pl.col("season") != pl.col("s_season"))
            | (pl.col("week") != pl.col("s_week"))
            | (pl.col("home_team") != pl.col("s_home"))
            | (pl.col("away_team") != pl.col("s_away"))
        ).fill_null(True)
    ).height:
        raise DataQualityError("Play-by-play and schedule game identity disagree")
    candidate = plays.filter(
        ((pl.col("pass") == 1) | (pl.col("rush") == 1)).fill_null(False)
        & (pl.col("play_type") != "no_play").fill_null(False)
        & (pl.col("qb_kneel") != 1).fill_null(True)
        & (pl.col("qb_spike") != 1).fill_null(True)
        & (pl.col("two_point_attempt") != 1).fill_null(True)
    )
    eligible = candidate.filter(pl.col("epa").is_finite().fill_null(False))
    matchup = (
        (pl.col("posteam") == pl.col("s_home")) & (pl.col("defteam") == pl.col("s_away"))
    ) | ((pl.col("posteam") == pl.col("s_away")) & (pl.col("defteam") == pl.col("s_home")))
    if eligible.filter(~matchup.fill_null(False)).height:
        raise DataQualityError("Eligible play has invalid offense/defense identity")
    if eligible.filter(~pl.col("qb_dropback").is_in([0, 1]).fill_null(False)).height:
        raise DataQualityError("Eligible play has an unknown dropback classification")
    dropback = pl.col("qb_dropback") == 1
    rush = (pl.col("rush") == 1) & ~dropback
    totals = (
        eligible.group_by("game_id", "posteam")
        .agg(
            pl.len().alias("off_plays"),
            pl.col("epa").sum().alias("off_epa_sum"),
            dropback.sum().alias("off_dropback_plays"),
            pl.col("epa").filter(dropback).sum().alias("off_dropback_epa_sum"),
            rush.sum().alias("off_rush_plays"),
            pl.col("epa").filter(rush).sum().alias("off_rush_epa_sum"),
        )
        .rename({"posteam": "team"})
    )
    sides = pl.concat(
        [
            completed.select(
                "game_id",
                "season",
                "week",
                "kickoff_utc",
                "result_available_at_utc",
                pl.col(f"{side}_team").alias("team"),
                pl.col(f"{other}_team").alias("opponent_team"),
                pl.col(f"{side}_score").alias("points_scored"),
                pl.col(f"{other}_score").alias("points_allowed"),
            )
            for side, other in (("home", "away"), ("away", "home"))
        ]
    )
    missing_sides = sides.join(totals, on=["game_id", "team"], how="anti")
    if missing_sides.height:
        raise DataQualityError(
            f"Missing eligible EPA for {missing_sides.height} completed team-games; "
            f"examples: {missing_sides.select('game_id', 'team').head(5).to_dicts()}"
        )
    defense = totals.rename(
        {
            "team": "opponent_team",
            **{
                name: name.replace("off_", "def_", 1)
                for name in totals.columns
                if name.startswith("off_")
            },
        }
    )
    teams = (
        sides.join(totals, on=["game_id", "team"], validate="1:1")
        .join(
            defense,
            on=["game_id", "opponent_team"],
            validate="1:1",
        )
        .with_columns(
            pl.when(pl.col(f"{prefix}{kind}plays") > 0)
            .then(pl.col(f"{prefix}{kind}epa_sum") / pl.col(f"{prefix}{kind}plays"))
            .otherwise(None)
            .alias(f"{prefix}{kind}epa_per_play")
            for prefix in ("off_", "def_")
            for kind in ("", "dropback_", "rush_")
        )
        .sort("kickoff_utc", "game_id", "team")
    )
    require_keys(teams, ["game_id", "team"], "team games")
    audit = {
        "schedule_regular_games": games.height,
        "completed_games": completed.height,
        "team_games": teams.height,
        "eligible_plays": eligible.height,
        "excluded_missing_or_nonfinite_epa": candidate.height - eligible.height,
        "excluded_play_types": plays.height - candidate.height,
        "uncompleted_schedule_games": games.height - completed.height,
        "future_unknown_kickoff": games.filter(pl.col("kickoff_utc").is_null()).height,
        "play_policy": PLAY_POLICY,
    }
    return teams, games, audit


def fetch_team_sources(data_dir: Path, seasons: list[int], *, refresh: bool = False) -> None:
    import nflreadpy as nfl
    from nflreadpy.config import update_config

    update_config(cache_mode="off")
    for season in team_seasons(seasons):
        directory = data_dir / "raw" / "team_epa" / str(season)
        if (directory / "manifest.json").exists() and not refresh:
            manifest = read_json(directory / "manifest.json")
            for name in ("pbp", "schedules"):
                load_frame(directory, manifest["datasets"][name])
            print(f"Verified team EPA cache: {season}", flush=True)
            continue
        print(f"Downloading team EPA: {season}", flush=True)
        pbp, schedules = nfl.load_pbp(season), nfl.load_schedules([season])
        if pbp.is_empty() or schedules.is_empty():
            raise DataQualityError(f"Empty team source for {season}")
        for frame in (pbp, schedules):
            if sorted(frame["season"].unique().to_list()) != [season]:
                raise DataQualityError("Team source returned unexpected seasons")
        # Validate before replacing the active manifest. Keep selected source columns unchanged.
        build_team_games(pbp, schedules)
        manifest = {
            "format_version": 1,
            "season": season,
            "retrieved_at_utc": datetime.now(UTC).isoformat(),
            "nflreadpy_version": version("nflreadpy"),
            "datasets": {
                "pbp": {
                    **store_frame(directory, "pbp", pbp.select(PBP_COLUMNS)),
                    "source": f"https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.parquet",
                    "observed_source_schema": {
                        key: str(value) for key, value in pbp.schema.items()
                    },
                },
                "schedules": {
                    **store_frame(directory, "schedules", schedules.select(SCHEDULE_COLUMNS)),
                    "source": "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.parquet",
                },
            },
            "license": "CC-BY-4.0",
        }
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        write_json(directory / f"manifest-{stamp}.json", manifest)
        write_json(directory / "manifest.json", manifest)


def build_team_cache(data_dir: Path, seasons: list[int]) -> dict[str, Any]:
    sources, all_pbp, all_schedules = [], [], []
    for season in team_seasons(seasons):
        directory = data_dir / "raw" / "team_epa" / str(season)
        manifest = read_json(directory / "manifest.json")
        if manifest["season"] != season:
            raise DataQualityError("Team cache season mismatch")
        sources.append(manifest)
        all_pbp.append(load_frame(directory, manifest["datasets"]["pbp"]))
        all_schedules.append(load_frame(directory, manifest["datasets"]["schedules"]))
    teams, games, audit = build_team_games(
        pl.concat(all_pbp, how="vertical_relaxed"),
        pl.concat(all_schedules, how="vertical_relaxed"),
    )
    directory = data_dir / "processed" / "team_epa"
    result = {
        "format_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "seasons": team_seasons(seasons),
        "sources": sources,
        "audit": audit,
        "pipeline_source_sha256": team_source_hash(),
        "artifacts": {
            "team_games": store_frame(directory, "team_games", teams),
            "games": store_frame(directory, "games", games),
        },
    }
    write_json(directory / "manifest.json", result)
    return result


def load_team_cache(data_dir: Path) -> tuple[pl.DataFrame, pl.DataFrame, dict[str, Any]]:
    directory = data_dir / "processed" / "team_epa"
    manifest = read_json(directory / "manifest.json")
    if manifest["pipeline_source_sha256"] != team_source_hash():
        raise DataQualityError("Team pipeline changed; run team-build again")
    return (
        load_frame(directory, manifest["artifacts"]["team_games"]),
        load_frame(directory, manifest["artifacts"]["games"]),
        manifest,
    )
