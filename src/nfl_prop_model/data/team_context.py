"""Independent historical passer/injury snapshots; no current-game starter labels."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from nfl_prop_model.data.schemas import DataQualityError, require_keys
from nfl_prop_model.data.storage import load_frame, read_json, store_frame, write_json
from nfl_prop_model.data.team_games import load_team_cache, normalize_teams, team_source_hash

QB_COLUMNS = (
    "game_id",
    "season",
    "week",
    "season_type",
    "team",
    "opponent_team",
    "player_id",
    "position",
    "attempts",
    "sacks_suffered",
    "passing_epa",
)
INJURY_COLUMNS = (
    "season",
    "week",
    "game_type",
    "team",
    "gsis_id",
    "position",
    "report_status",
    "report_primary_injury",
    "date_modified",
)
DEFAULT_CONTEXT_SEASONS = tuple(range(2016, 2025))


def context_seasons(seasons: list[int]) -> list[int]:
    if not seasons or any(year < 2009 or year > 2024 for year in seasons):
        raise ValueError("Historical context research supports 2009–2024")
    return sorted(set(seasons))


def context_source_hash() -> str:
    return hashlib.sha256(
        team_source_hash().encode()
        + Path(__file__).read_bytes()
        + (Path(__file__).parents[1] / "features" / "team_context.py").read_bytes()
    ).hexdigest()


def fetch_context_sources(data_dir: Path, seasons: list[int], *, refresh: bool = False) -> None:
    for season in context_seasons(seasons):
        directory = data_dir / "raw" / "team_context" / str(season)
        if (directory / "manifest.json").exists() and not refresh:
            manifest = read_json(directory / "manifest.json")
            if manifest["season"] != season:
                raise DataQualityError("Context source season mismatch")
            for metadata in manifest["datasets"].values():
                load_frame(directory, metadata)
            continue
        import nflreadpy as nfl
        from nflreadpy.config import update_config

        update_config(cache_mode="memory")
        nfl.clear_cache()
        sources = {}
        for name, frame, columns in (
            ("player_stats", nfl.load_player_stats([season]), QB_COLUMNS),
            ("injuries", nfl.load_injuries([season]), INJURY_COLUMNS),
        ):
            if missing := set(columns) - set(frame.columns):
                raise DataQualityError(f"Context {name} missing columns: {sorted(missing)}")
            if frame.is_empty() or set(frame["season"]) != {season}:
                raise DataQualityError("Context source must contain exactly the requested season")
            sources[name] = {
                **store_frame(directory, name, frame.select(columns)),
                "upstream_schema": {key: str(value) for key, value in frame.schema.items()},
                "loader": f"nflreadpy.load_{name}([{season}])",
                "source": "https://github.com/nflverse/nflverse-data/releases/tag/"
                + ("stats_player" if name == "player_stats" else "injuries"),
            }
        manifest = {
            "format_version": 1,
            "season": season,
            "retrieved_at_utc": datetime.now(UTC).isoformat(),
            "datasets": sources,
        }
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        write_json(directory / f"manifest-{stamp}.json", manifest)
        write_json(directory / "manifest.json", manifest)


def prepare_context(
    stats: pl.DataFrame, injuries: pl.DataFrame, teams: pl.DataFrame, games: pl.DataFrame
) -> tuple[pl.DataFrame, pl.DataFrame, dict[str, Any]]:
    for frame, columns, name in (
        (stats, QB_COLUMNS, "passer"),
        (injuries, INJURY_COLUMNS, "injury"),
    ):
        if missing_columns := set(columns) - set(frame.columns):
            raise DataQualityError(f"{name} context missing columns: {sorted(missing_columns)}")
        if frame.filter(
            pl.any_horizontal(
                pl.col("season", "week").is_null()
                | ~pl.col("season", "week").is_finite()
                | ((pl.col("season", "week") % 1) != 0)
            )
        ).height:
            raise DataQualityError("Context season/week must be known integers")
    stats = stats.with_columns(pl.col("season", "week").cast(pl.Int32))
    injuries = injuries.with_columns(pl.col("season", "week").cast(pl.Int32))
    regular = normalize_teams(
        stats.filter(pl.col("season_type") == "REG"), ["team", "opponent_team"]
    )
    passers = regular.filter(
        (pl.col("position") == "QB") | ((pl.col("attempts") + pl.col("sacks_suffered")) > 0)
    )
    require_keys(passers, ["game_id", "team", "player_id"], "passer context")
    if passers.join(games.select("game_id"), on="game_id", how="anti").height:
        raise DataQualityError("Passer context has games outside the team schedule")
    completed = passers.join(
        games.filter(pl.col("home_score").is_not_null()).select("game_id"), on="game_id", how="semi"
    )
    if completed.join(teams.select("game_id", "team"), on=["game_id", "team"], how="anti").height:
        raise DataQualityError("Passer context team does not match the completed game")
    joined = completed.join(
        teams.select(
            "game_id",
            "team",
            pl.col("season").alias("expected_season"),
            pl.col("week").alias("expected_week"),
            pl.col("opponent_team").alias("expected_opponent"),
            "result_available_at_utc",
        ),
        on=["game_id", "team"],
        validate="m:1",
    )
    if joined.filter(
        (pl.col("season") != pl.col("expected_season"))
        | (pl.col("week") != pl.col("expected_week"))
        | (pl.col("opponent_team") != pl.col("expected_opponent"))
    ).height:
        raise DataQualityError("Passer context identities do not match team games")
    if joined.filter(
        pl.any_horizontal(
            pl.col("attempts", "sacks_suffered").is_null()
            | (pl.col("attempts", "sacks_suffered") < 0)
            | ~pl.col("attempts", "sacks_suffered").is_finite()
        )
        | (~pl.col("passing_epa").is_finite()).fill_null(False)
    ).height:
        raise DataQualityError("Passer EPA/counts must be finite, counts nonnegative")
    qb = joined.select(*QB_COLUMNS, "result_available_at_utc").with_columns(
        (pl.col("attempts") + pl.col("sacks_suffered")).cast(pl.Float64).alias("passing_plays")
    )
    reports = normalize_teams(injuries.filter(pl.col("game_type") == "REG"), ["team"])
    dtype = reports.schema["date_modified"]
    if not isinstance(dtype, pl.Datetime) or dtype.time_zone != "UTC":
        raise DataQualityError("Injury modification timestamps must explicitly use UTC")
    missing = reports.filter(
        pl.col("gsis_id").is_null() | (pl.col("gsis_id") == "") | pl.col("date_modified").is_null()
    )
    reports = reports.join(missing, on=list(INJURY_COLUMNS), how="anti", nulls_equal=True)
    require_keys(reports, ["season", "week", "team", "gsis_id", "date_modified"], "injury context")
    slots = (
        pl.concat(
            [
                games.select("season", "week", pl.col(f"{side}_team").alias("team"))
                for side in ("home", "away")
            ]
        )
        .unique()
        .with_columns(pl.col("season", "week").cast(pl.Int32))
    )
    unmatched = reports.join(slots, on=["season", "week", "team"], how="anti")
    reports = reports.join(slots, on=["season", "week", "team"], how="semi").with_columns(
        (pl.col("date_modified") + pl.duration(hours=24)).alias("available_at_utc")
    )
    statuses = set(reports["report_status"].drop_nulls())
    if statuses - {"Out", "Doubtful", "Questionable", "Probable", "Note"}:
        raise DataQualityError(f"Unknown injury report statuses: {sorted(statuses)}")
    represented = qb.select("game_id", "team").unique()
    expected = teams.filter(pl.col("season").is_in(stats["season"].unique().to_list()))
    audit = {
        "passer_rows": qb.height,
        "team_games_with_passers": represented.height,
        "team_games_missing_passers": expected.join(
            represented, on=["game_id", "team"], how="anti"
        ).height,
        "excluded_uncompleted_passer_rows": passers.height - completed.height,
        "passer_rows_missing_epa": qb["passing_epa"].null_count(),
        "injury_rows": reports.height,
        "injury_rows_missing_identity_or_time": missing.height,
        "injury_rows_outside_schedule": unmatched.height,
        "injury_rows_ambiguous_note_status": reports.filter(
            pl.col("report_status") == "Note"
        ).height,
        "injury_availability": "source modification + 24 hours, retrospective assumption",
        "rest": "only explicit Rest reason on an Out report; no coaching-plan inference",
    }
    return qb, reports, audit


def build_context_cache(data_dir: Path, seasons: list[int]) -> dict[str, Any]:
    sources, stats, reports = [], [], []
    for season in context_seasons(seasons):
        directory = data_dir / "raw" / "team_context" / str(season)
        manifest = read_json(directory / "manifest.json")
        if manifest["season"] != season:
            raise DataQualityError("Context source season mismatch")
        sources.append(manifest)
        stats.append(load_frame(directory, manifest["datasets"]["player_stats"]))
        reports.append(load_frame(directory, manifest["datasets"]["injuries"]))
    teams, games, team_sources = load_team_cache(data_dir)
    qb, injuries, audit = prepare_context(
        pl.concat(stats, how="vertical_relaxed"),
        pl.concat(reports, how="vertical_relaxed"),
        teams,
        games,
    )
    directory = data_dir / "processed" / "team_context"
    manifest = {
        "format_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "seasons": context_seasons(seasons),
        "sources": sources,
        "team_sources": team_sources,
        "context_source_sha256": context_source_hash(),
        "audit": audit,
        "artifacts": {
            "passers": store_frame(directory, "passers", qb),
            "injuries": store_frame(directory, "injuries", injuries),
        },
    }
    write_json(directory / "manifest.json", manifest)
    return manifest


def load_context_cache(data_dir: Path) -> tuple[pl.DataFrame, pl.DataFrame, dict[str, Any]]:
    directory = data_dir / "processed" / "team_context"
    manifest = read_json(directory / "manifest.json")
    if manifest["context_source_sha256"] != context_source_hash():
        raise DataQualityError("Context features changed; run team-context-build again")
    return (
        load_frame(directory, manifest["artifacts"]["passers"]),
        load_frame(directory, manifest["artifacts"]["injuries"]),
        manifest,
    )
