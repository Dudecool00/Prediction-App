"""EPA matchup features built exclusively from results available at each cutoff."""

from bisect import bisect_left
from collections import defaultdict
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl

from nfl_prop_model.data.schemas import DataQualityError, require_keys

RATE_GROUPS = tuple(
    f"{side}_{kind}" for side in ("off", "def") for kind in ("", "dropback_", "rush_")
)
RATING_COLUMNS = tuple(
    f"{group}epa_{window}" for group in RATE_GROUPS for window in ("recent", "season")
)
TEAM_FEATURES = tuple(f"{side}_{name}" for side in ("home", "away") for name in RATING_COLUMNS) + (
    "home_field",
    "rest_difference",
    "home_prior_games",
    "away_prior_games",
)
HORIZONS = ("early_week", "pregame_refresh")
FEATURE_POLICY = {
    "recent": "last five available team games, decay 0.85 per game; play-weighted EPA rates",
    "season": "current season EPA shrunk with 200 plays at prior-season EPA (league fallback)",
    "scores": "prior team scoring/allowed means with four prior-season pseudo-games; baseline only",
    "early_week": "Tuesday 10:00 America/Chicago on/before the first scheduled game of the week",
    "pregame_refresh": "one hour before each recorded kickoff",
    "availability": "only result_available_at_utc strictly earlier than prediction time",
    "rest": "prior completed same-season schedule game; independent of EPA availability",
}


def weekly_cutoffs(games: pl.DataFrame) -> dict[tuple[int, int], datetime]:
    cutoffs = {}
    for row in (
        games.filter(pl.col("kickoff_utc").is_not_null())
        .group_by("season", "week")
        .agg(pl.col("kickoff_utc").min().alias("first"))
        .to_dicts()
    ):
        first = row["first"].astimezone(ZoneInfo("America/Chicago"))
        day = first.date() - timedelta(days=(first.weekday() - 1) % 7)
        cutoff = datetime.combine(day, time(10), ZoneInfo("America/Chicago"))
        if cutoff >= first:
            cutoff -= timedelta(days=7)
        cutoffs[(row["season"], row["week"])] = cutoff.astimezone(UTC)
    return cutoffs


class TeamHistory:
    """Sorted result indexes and league prefix sums; never fit on unavailable outcomes."""

    def __init__(self, teams: pl.DataFrame) -> None:
        require_keys(teams, ["game_id", "team"], "team feature history")
        self.rows = teams.sort("result_available_at_utc", "game_id", "team").to_dicts()
        self.times = [row["result_available_at_utc"] for row in self.rows]
        self.by_team: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in self.rows:
            if not isinstance(row["result_available_at_utc"], datetime):
                raise DataQualityError("Missing team result availability")
            self.by_team[row["team"]].append(row)
        self.team_times = {
            team: [row["result_available_at_utc"] for row in rows]
            for team, rows in self.by_team.items()
        }
        self.prefixes = {}
        for group in RATE_GROUPS:
            for suffix in ("epa_sum", "plays"):
                name = group + suffix
                self.prefixes[name] = np.concatenate(
                    ([0.0], np.cumsum([row[name] for row in self.rows]))
                )

    def available(self, team: str, cutoff: datetime) -> list[dict[str, Any]]:
        count = bisect_left(self.team_times.get(team, []), cutoff)
        return self.by_team.get(team, [])[:count]

    def league_rate(self, group: str, cutoff: datetime) -> float:
        count = bisect_left(self.times, cutoff)
        plays = self.prefixes[group + "plays"][count]
        return float(self.prefixes[group + "epa_sum"][count] / plays) if plays else 0.0

    def rating(self, team: str, season: int, cutoff: datetime) -> dict[str, Any]:
        history = self.available(team, cutoff)
        current = [row for row in history if row["season"] == season]
        prior = [row for row in history if row["season"] == season - 1]
        recent = history[-5:]
        weights = [0.85 ** (len(recent) - index - 1) for index in range(len(recent))]
        result: dict[str, Any] = {
            "prior_games": len(history),
            "history_available_at_utc": history[-1]["result_available_at_utc"] if history else None,
        }
        for group in RATE_GROUPS:
            prior_plays = sum(row[group + "plays"] for row in prior)
            prior_rate = (
                sum(row[group + "epa_sum"] for row in prior) / prior_plays
                if prior_plays
                else self.league_rate(group, cutoff)
            )
            plays = sum(row[group + "plays"] for row in current)
            result[group + "epa_season"] = (
                sum(row[group + "epa_sum"] for row in current) + 200 * prior_rate
            ) / (plays + 200)
            recent_plays = sum(
                row[group + "plays"] * weight for row, weight in zip(recent, weights, strict=True)
            )
            result[group + "epa_recent"] = (
                sum(
                    row[group + "epa_sum"] * weight
                    for row, weight in zip(recent, weights, strict=True)
                )
                / recent_plays
                if recent_plays
                else prior_rate
            )
        for name in ("points_scored", "points_allowed"):
            prior_mean = sum(row[name] for row in prior) / len(prior) if prior else None
            if prior_mean is None and recent:
                prior_mean = sum(row[name] for row in recent) / len(recent)
            result[name + "_prior"] = (
                (sum(row[name] for row in current) + 4 * prior_mean) / (len(current) + 4)
                if prior_mean is not None
                else None
            )
        return result


def matchup_features(
    teams: pl.DataFrame,
    games: pl.DataFrame,
    *,
    horizon: str = "early_week",
    prediction_time: datetime | None = None,
    schedule_history: pl.DataFrame | None = None,
) -> pl.DataFrame:
    if horizon not in HORIZONS:
        raise ValueError(f"Unknown team horizon: {horizon}")
    if prediction_time is not None and (
        prediction_time.tzinfo is None or prediction_time.utcoffset() is None
    ):
        raise ValueError("Prediction time must be timezone-aware")
    require_keys(games, ["game_id"], "matchup games")
    history = TeamHistory(teams)
    cutoffs = weekly_cutoffs(games)
    schedules = games if schedule_history is None else schedule_history
    rest_history: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for scheduled in (
        schedules.filter(pl.col("kickoff_utc").is_not_null()).sort("kickoff_utc").to_dicts()
    ):
        for side in ("home", "away"):
            rest_history[(scheduled[f"{side}_team"], scheduled["season"])].append(scheduled)
    records = []
    for game in games.filter(pl.col("kickoff_utc").is_not_null()).to_dicts():
        cutoff = prediction_time or (
            cutoffs[(game["season"], game["week"])]
            if horizon == "early_week"
            else game["kickoff_utc"] - timedelta(hours=1)
        )
        if cutoff >= game["kickoff_utc"]:
            raise DataQualityError("Team forecast must precede kickoff")
        home = history.rating(game["home_team"], game["season"], cutoff)
        away = history.rating(game["away_team"], game["season"], cutoff)
        row = {
            name: game[name]
            for name in (
                "game_id",
                "season",
                "week",
                "home_team",
                "away_team",
                "kickoff_utc",
                "result_available_at_utc",
                "home_score",
                "away_score",
                "target_home_margin",
            )
        }
        row.update(
            {
                "horizon": horizon,
                "prediction_time_utc": cutoff,
                "home_field": float(not game["is_neutral_site"]),
            }
        )
        for side, rating in (("home", home), ("away", away)):
            row.update({f"{side}_{name}": rating[name] for name in RATING_COLUMNS})
            for name in (
                "prior_games",
                "history_available_at_utc",
                "points_scored_prior",
                "points_allowed_prior",
            ):
                row[f"{side}_{name}"] = rating[name]
            past = rest_history.get((game[f"{side}_team"], game["season"]), [])
            index = bisect_left([scheduled["kickoff_utc"] for scheduled in past], cutoff)
            last = past[index - 1] if index else None
            # A still-running or uncompleted intervening game leaves rest unknown.
            known = (
                last is not None
                and last["home_score"] is not None
                and last["away_score"] is not None
                and last["kickoff_utc"] + timedelta(hours=4) < cutoff
            )
            row[f"{side}_rest_days"] = (
                (game["kickoff_utc"] - last["kickoff_utc"]).total_seconds() / 86400
                if known and last is not None
                else None
            )
            row[f"{side}_rest_game_id"] = last["game_id"] if known and last is not None else None
        row["rest_difference"] = (
            row["home_rest_days"] - row["away_rest_days"]
            if row["home_rest_days"] is not None and row["away_rest_days"] is not None
            else None
        )
        records.append(row)
    if not records:
        raise DataQualityError("No team games with known kickoff times")
    return (
        pl.DataFrame(records, infer_schema_length=None)
        .with_columns(
            pl.col("rest_difference").cast(pl.Float64),
            pl.col("home_rest_days", "away_rest_days").cast(pl.Float64),
            pl.col(
                "home_points_scored_prior",
                "home_points_allowed_prior",
                "away_points_scored_prior",
                "away_points_allowed_prior",
            ).cast(pl.Float64),
            pl.col("home_history_available_at_utc", "away_history_available_at_utc").cast(
                pl.Datetime("us", "UTC")
            ),
        )
        .sort("prediction_time_utc", "game_id")
    )
