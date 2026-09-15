"""Contracts based on nflreadpy 0.1.5 data inspected on 2026-09-15 UTC."""

import polars as pl

STATS_TEXT = (
    "player_id",
    "player_display_name",
    "position",
    "season_type",
    "game_id",
    "team",
    "opponent_team",
)
STATS_NUMERIC = ("season", "week", "passing_yards", "attempts", "completions")
SCHEDULE_TEXT = (
    "game_id",
    "game_type",
    "gameday",
    "gametime",
    "home_team",
    "away_team",
    "home_qb_id",
    "away_qb_id",
)
SCHEDULE_NUMERIC = ("season", "week", "home_score", "away_score")


class DataQualityError(ValueError):
    """A source violates a contract that would make the modeling table unsafe."""


def require_columns(
    frame: pl.DataFrame, name: str, text: tuple[str, ...], numeric: tuple[str, ...]
) -> None:
    missing = set(text + numeric) - set(frame.columns)
    if missing:
        raise DataQualityError(f"{name}: missing required columns: {sorted(missing)}")
    for column in text:
        if frame.schema[column] != pl.String:
            raise DataQualityError(f"{name}.{column}: expected String, got {frame.schema[column]}")
    for column in numeric:
        if not frame.schema[column].is_numeric():
            raise DataQualityError(f"{name}.{column}: expected a numeric dtype")
        if (
            frame.schema[column].is_float()
            and frame.filter(pl.col(column).is_not_null() & ~pl.col(column).is_finite()).height
        ):
            raise DataQualityError(f"{name}.{column}: non-finite values")


def require_keys(frame: pl.DataFrame, keys: list[str], name: str) -> None:
    if frame.select(pl.any_horizontal(pl.col(keys).is_null()).any()).item():
        raise DataQualityError(f"{name}: null keys in {keys}")
    if frame.select(keys).is_duplicated().any():
        raise DataQualityError(f"{name}: duplicate keys in {keys}")


def validate_sources(stats: pl.DataFrame, schedules: pl.DataFrame) -> None:
    require_columns(stats, "player_stats", STATS_TEXT, STATS_NUMERIC)
    require_columns(schedules, "schedules", SCHEDULE_TEXT, SCHEDULE_NUMERIC)
    # Unknown-position records cannot be identified as QBs and are counted in
    # exclusions. Fail on malformed QB identities, not unrelated source rows.
    require_keys(stats.filter(pl.col("position") == "QB"), ["player_id", "game_id"], "QB stats")
    require_keys(schedules, ["game_id"], "schedules")
