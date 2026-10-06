"""As-observed features for current candidates; no estimator or outcome for the candidate."""

import hashlib
import json
import math
from datetime import datetime, timedelta
from typing import Any

import polars as pl

from nfl_prop_model.data.schemas import DataQualityError, require_keys
from nfl_prop_model.data.upcoming import schedule_games, utc_time
from nfl_prop_model.features.context import SCHEDULE_FEATURES
from nfl_prop_model.features.quarterback import FEATURE_COLUMNS

HISTORY_COLUMNS = (
    "player_id",
    "game_id",
    "season",
    "kickoff_utc",
    "target_passing_yards",
    "observed_attempts",
    "available_at_utc",
)
FEATURES = (*FEATURE_COLUMNS, *SCHEDULE_FEATURES)


def observed_history(table: pl.DataFrame, observed_at: datetime) -> pl.DataFrame:
    """A snapshot cannot establish knowledge before retrieval, even for old results."""
    observed_at = utc_time(observed_at)
    return table.select(
        *HISTORY_COLUMNS[:-1],
        pl.max_horizontal(pl.col("kickoff_utc") + pl.duration(hours=24), pl.lit(observed_at)).alias(
            "available_at_utc"
        ),
    )


def validate_history(history: pl.DataFrame) -> None:
    if set(HISTORY_COLUMNS) - set(history.columns):
        raise DataQualityError("Prospective history is missing required columns")
    require_keys(history, ["player_id", "game_id"], "prospective history")
    require_keys(history, ["player_id", "kickoff_utc"], "prospective history times")
    for column in ("kickoff_utc", "available_at_utc"):
        if history.schema[column] != pl.Datetime("us", "UTC") or history[column].null_count():
            raise DataQualityError("Prospective history requires nonnull UTC timestamps")
    if history["season"].null_count() or set(history["season"].unique()) - set(range(2022, 2027)):
        raise DataQualityError("Prospective history supports only 2022–2026")
    if history.filter(
        pl.col("available_at_utc") < pl.col("kickoff_utc") + pl.duration(hours=24)
    ).height:
        raise DataQualityError("History availability cannot precede kickoff plus 24 hours")
    for column in ("target_passing_yards", "observed_attempts"):
        if (
            not history.schema[column].is_numeric()
            or history.filter(pl.col(column).is_null() | ~pl.col(column).is_finite()).height
        ):
            raise DataQualityError("Prospective history requires finite yards and attempts")
    if history.filter(pl.col("observed_attempts") < 0).height:
        raise DataQualityError("Prospective history attempts cannot be negative")


def _time(value: str) -> datetime:
    return utc_time(datetime.fromisoformat(value))


def _record(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.isoformat() if isinstance(value, datetime) else value
        for key, value in row.items()
    }


def candidate_features(
    candidate: dict[str, Any],
    history: pl.DataFrame,
    schedules: pl.DataFrame,
    *,
    as_of: datetime,
    observed_at: datetime,
) -> dict[str, Any]:
    """Caller validates source/history contracts once; each candidate stays visible."""
    as_of, observed_at = utc_time(as_of), utc_time(observed_at)
    if observed_at > as_of:
        raise ValueError("Feature sources were retrieved after the requested time")
    kickoff = _time(candidate["kickoff_utc"])
    prior = (
        history.filter(
            (pl.col("player_id") == candidate["gsis_id"])
            & (pl.col("kickoff_utc") < as_of)
            & (pl.col("available_at_utc") <= as_of)
            & (pl.col("game_id") != candidate["game_id"])
        ).sort("kickoff_utc", "game_id")
        if candidate["gsis_id"]
        else history.head(0)
    )
    recent = prior.tail(5)
    season = prior.filter(pl.col("season") == candidate["season"])
    features: dict[str, Any] = {
        "passing_yards_lag1": recent["target_passing_yards"][-1] if recent.height else None,
        "passing_yards_mean3": prior.tail(3)["target_passing_yards"].mean(),
        "passing_yards_mean5": recent["target_passing_yards"].mean(),
        "attempts_mean5": recent["observed_attempts"].mean(),
        "passing_yards_season_mean": season["target_passing_yards"].mean(),
        "prior_games_in_sample": prior.height,
        "prior_season_games_in_sample": season.height,
        "is_designated_home": candidate["designated_home"],
    }
    games = schedule_games(schedules).join(
        schedules.select("game_id", "location", "home_score", "away_score"),
        on="game_id",
        validate="1:1",
    )
    selected = games.filter(pl.col("game_id") == candidate["game_id"]).to_dicts()
    if len(selected) != 1:
        raise DataQualityError("Candidate schedule coverage is incomplete")
    game = selected[0]
    if (
        (game["home_team"] if candidate["designated_home"] else game["away_team"])
        != candidate["team"]
        or (game["away_team"] if candidate["designated_home"] else game["home_team"])
        != candidate["opponent_team"]
        or game["kickoff_utc"] != kickoff
    ):
        raise DataQualityError("Candidate matchup/time differs from feature schedules")
    features["is_neutral_site"] = (
        game["location"] == "Neutral" if game["location"] in {"Home", "Neutral"} else None
    )
    issues: list[str] = []
    if features["is_neutral_site"] is None:
        issues.append("schedule_location_unknown")
    rest_inputs: dict[str, Any] = {}
    for side, team in (("team", candidate["team"]), ("opponent", candidate["opponent_team"])):
        earlier = games.filter(
            ((pl.col("home_team") == team) | (pl.col("away_team") == team))
            & (pl.col("game_id") != game["game_id"])
            & (
                (pl.col("kickoff_utc") < kickoff)
                | (pl.col("kickoff_utc").is_null() & (pl.col("week") <= game["week"]))
            )
        )
        unresolved = earlier.filter(
            pl.col("kickoff_utc").is_null()
            | pl.col("home_score").is_null()
            | pl.col("away_score").is_null()
            | (pl.col("kickoff_utc") + pl.duration(hours=24) > as_of)
        )
        completed = earlier.filter(
            pl.col("home_score").is_not_null()
            & pl.col("away_score").is_not_null()
            & (pl.col("kickoff_utc") + pl.duration(hours=24) <= as_of)
        ).sort("kickoff_utc", "game_id")
        latest = completed.tail(1).to_dicts()
        if unresolved.height:
            features[f"{side}_rest_days"] = None
            issues.append(f"{side}_rest_unresolved")
        else:
            features[f"{side}_rest_days"] = (
                (kickoff - latest[0]["kickoff_utc"]).total_seconds() / 86400 if latest else None
            )
        rest_inputs[side] = {
            "previous_completed_game_id": latest[0]["game_id"] if latest else None,
            "previous_kickoff_utc": latest[0]["kickoff_utc"].isoformat() if latest else None,
            "unresolved_game_ids": unresolved["game_id"].to_list(),
        }
    history_records = [_record(row) for row in prior.select(HISTORY_COLUMNS).iter_rows(named=True)]
    history_time = max([observed_at, *prior["available_at_utc"].to_list()])
    if any(value is not None and not math.isfinite(value) for value in features.values()):
        raise DataQualityError("Prospective features must be finite values or null")
    return {
        "features": {name: features[name] for name in FEATURES},
        "feature_available_at_utc": {
            name: (history_time if name in FEATURE_COLUMNS else observed_at).isoformat()
            for name in FEATURES
        },
        "history_audit": {
            "sample_seasons": sorted(prior["season"].unique().to_list()),
            "all_prior_rows_sha256": hashlib.sha256(
                json.dumps(
                    history_records, sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode()
            ).hexdigest(),
            "last_five": history_records[-5:],
            "current_season_game_ids": season["game_id"].to_list(),
            "latest_available_at_utc": history_time.isoformat(),
        },
        "rest_audit": rest_inputs,
        "feature_issues": issues,
        "forecast_cutoff_utc": (kickoff - timedelta(hours=1)).isoformat(),
    }
