"""Weekly locked EPA history plus timestamp-gated observed injuries and forecast temperature."""

from collections import defaultdict
from typing import Any

import polars as pl

from nfl_prop_model.data.schemas import DataQualityError, require_keys
from nfl_prop_model.features.team_context import DEFENSE, OL, SKILL
from nfl_prop_model.features.team_epa import TEAM_FEATURES, matchup_features

COMPACT_EPA = tuple(
    f"{side}_{kind}_epa_season" for side in ("home", "away") for kind in ("off", "def")
) + ("home_field", "rest_difference", "home_prior_games", "away_prior_games")
AVAILABILITY = tuple(
    f"{side}_{name}"
    for side in ("home", "away")
    for name in ("qb_out_count", "ol_out_count", "skill_out_count", "defense_out_count")
)
TEMPERATURE = ("forecast_temperature", "forecast_cold", "forecast_heat")
MODELS = {
    "ridge_epa_scores": TEAM_FEATURES,
    "compact_epa": COMPACT_EPA,
    "compact_availability": COMPACT_EPA + AVAILABILITY,
    "compact_temperature": COMPACT_EPA + TEMPERATURE,
    "compact_combined": COMPACT_EPA + AVAILABILITY + TEMPERATURE,
}


def weekly_features(teams: pl.DataFrame, games: pl.DataFrame) -> pl.DataFrame:
    """Discard pre-2022 history and freeze all outcome-derived EPA/rest inputs on Tuesday."""
    teams = teams.filter(pl.col("season") >= 2022)
    games = games.filter(pl.col("season") >= 2022)
    completed = games.filter(
        pl.col("home_score").is_not_null() & pl.col("away_score").is_not_null()
    )
    early = matchup_features(teams, completed, schedule_history=games).with_columns(
        pl.col("prediction_time_utc").alias("week_lock_utc")
    )
    refresh = early.with_columns(
        pl.lit("pregame_refresh").alias("horizon"),
        (pl.col("kickoff_utc") - pl.duration(hours=1)).alias("prediction_time_utc"),
    )
    return pl.concat([early, refresh]).sort("season", "week", "horizon", "game_id")


def add_pregame_features(
    canonical: pl.DataFrame, injuries: pl.DataFrame, weather: pl.DataFrame
) -> pl.DataFrame:
    require_keys(canonical, ["game_id", "horizon"], "weekly canonical")
    require_keys(weather, ["game_id", "horizon"], "forecast temperature")
    lookup: dict[tuple[int, int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in injuries.sort("available_at_utc").to_dicts():
        if row["available_at_utc"] is None or not row["gsis_id"]:
            continue
        lookup[(row["season"], row["week"], row["team"])].append(row)
    records = []
    for row in canonical.to_dicts():
        record: dict[str, Any] = {"game_id": row["game_id"], "horizon": row["horizon"]}
        for side in ("home", "away"):
            latest = {
                r["gsis_id"]: r
                for r in lookup.get((row["season"], row["week"], row[f"{side}_team"]), [])
                if r["available_at_utc"] < row["prediction_time_utc"]
            }
            out = [r for r in latest.values() if r["report_status"] == "Out"]
            for name, positions in (
                ("qb_out_count", {"QB"}),
                ("ol_out_count", OL),
                ("skill_out_count", SKILL),
                ("defense_out_count", DEFENSE),
            ):
                record[f"{side}_{name}"] = (
                    float(sum(r["position"] in positions for r in out)) if latest else None
                )
            record[f"{side}_observed_injury_players"] = len(latest)
            record[f"{side}_injury_available_at_utc"] = max(
                (r["available_at_utc"] for r in latest.values()), default=None
            )
        records.append(record)
    observations = pl.DataFrame(records, infer_schema_length=None).with_columns(
        pl.col(AVAILABILITY).cast(pl.Float64),
        pl.col("home_injury_available_at_utc", "away_injury_available_at_utc").cast(
            pl.Datetime("us", "UTC")
        ),
    )
    joined = canonical.join(observations, on=["game_id", "horizon"], validate="1:1").join(
        weather.rename({"prediction_time_utc": "weather_cutoff_utc"}),
        on=["game_id", "horizon"],
        how="left",
        validate="1:1",
    )
    if joined.filter(
        pl.col("weather_cutoff_utc").is_null()
        | (pl.col("weather_cutoff_utc") != pl.col("prediction_time_utc"))
        | (pl.col("assumed_available_bound_utc") >= pl.col("prediction_time_utc"))
        | (pl.col("temperature_c").is_not_null() & pl.col("assumed_available_bound_utc").is_null())
    ).height:
        raise DataQualityError("Missing, mismatched or unavailable weather forecast")
    result = joined.with_columns(
        pl.when(pl.col("exposure") == 0)
        .then(0.0)
        .when(pl.col("exposure") == 1)
        .then(pl.col("temperature_c"))
        .otherwise(None)
        .alias("forecast_temperature"),
        pl.when(pl.col("exposure") == 0)
        .then(0.0)
        .when(pl.col("exposure") == 1)
        .then((-pl.col("temperature_c")).clip(lower_bound=0))
        .otherwise(None)
        .alias("forecast_cold"),
        pl.when(pl.col("exposure") == 0)
        .then(0.0)
        .when(pl.col("exposure") == 1)
        .then((pl.col("temperature_c") - 30).clip(lower_bound=0))
        .otherwise(None)
        .alias("forecast_heat"),
    )
    validate_weekly_features(result)
    return result


def validate_weekly_features(frame: pl.DataFrame) -> None:
    require_keys(frame, ["game_id", "horizon"], "weekly feature table")
    if frame.filter(
        pl.any_horizontal(pl.col("week_lock_utc", "prediction_time_utc", "kickoff_utc").is_null())
        | (pl.col("week_lock_utc") > pl.col("prediction_time_utc"))
        | (pl.col("prediction_time_utc") >= pl.col("kickoff_utc"))
    ).height:
        raise DataQualityError("Invalid weekly forecast timing")
    if frame.filter(
        pl.col("weather_cutoff_utc").is_null()
        | (pl.col("weather_cutoff_utc") != pl.col("prediction_time_utc"))
        | (
            pl.col("temperature_c").is_not_null()
            & (
                pl.col("assumed_available_bound_utc").is_null()
                | (pl.col("assumed_available_bound_utc") >= pl.col("prediction_time_utc"))
            )
        )
    ).height:
        raise DataQualityError("Weather was unavailable at the weekly forecast cutoff")
    for side in ("home", "away"):
        if frame.filter(
            (pl.col(f"{side}_history_available_at_utc") >= pl.col("week_lock_utc"))
            | (pl.col(f"{side}_injury_available_at_utc") >= pl.col("prediction_time_utc"))
        ).height:
            raise DataQualityError("Weekly inputs include unavailable history or context")
    if frame.select(
        pl.any_horizontal(~pl.col(TEAM_FEATURES + AVAILABILITY + TEMPERATURE).is_finite())
        .fill_null(False)
        .any()
    ).item():
        raise DataQualityError("Weekly features must be finite or null")


def context_coverage(frame: pl.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for group in frame.partition_by(["season", "horizon"], maintain_order=True):
        first = group.row(0, named=True)
        rows.append(
            {
                "season": first["season"],
                "horizon": first["horizon"],
                "games": group.height,
                "team_slots": group.height * 2,
                "team_slots_with_observed_injury": sum(
                    group.filter(pl.col(f"{side}_observed_injury_players") > 0).height
                    for side in ("home", "away")
                ),
                "outdoor_temperature_games": group.filter(
                    (pl.col("exposure") == 1) & pl.col("temperature_c").is_not_null()
                ).height,
                "outdoor_missing_temperature_games": group.filter(
                    (pl.col("exposure") == 1) & pl.col("temperature_c").is_null()
                ).height,
                "fixed_indoor_games": group.filter(pl.col("exposure") == 0).height,
                "unknown_exposure_games": group.filter(pl.col("exposure").is_null()).height,
            }
        )
    return rows
