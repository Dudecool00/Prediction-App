"""Past play tendencies and explicit schedule strength at each forecast cutoff."""

from bisect import bisect_left
from datetime import datetime
from typing import Any

import numpy as np
import polars as pl

from nfl_prop_model.data.team_playbook import PLAY_METRICS
from nfl_prop_model.features.team_epa import TeamHistory
from nfl_prop_model.features.team_experiments import OpponentHistory

PLAY_FEATURES = tuple(
    f"{side}_{kind}_{metric}_prior"
    for side in ("home", "away")
    for kind in ("off", "def")
    for metric in PLAY_METRICS
)
STRENGTH_FEATURES = tuple(
    f"{side}_{name}"
    for side in ("home", "away")
    for name in (
        "off_strength",
        "def_strength",
        "sos_off_recent",
        "sos_def_recent",
        "sos_off_season",
        "sos_def_season",
    )
)
PLAYBOOK_FEATURE_POLICY = {
    "success": "positive EPA on canonical eligible plays",
    "explosive": "at least 20 yards on dropbacks or 10 on designed rushes; finite yards only",
    "sacks": "recorded sack per dropback with known sack flag; not a pressure measure",
    "turnovers": "interception or lost fumble per play with both flags known, counted once",
    "play_history": "last five available games, decay 0.85; 100 exposure pseudo-plays "
    "at prior-season rate, past-league fallback",
    "strength": "regularized opponent-adjusted offense/defense EPA, current season weight 1, "
    "previous 0.25, 200-play effect penalty",
    "schedule": "mean current-cutoff ratings of previously faced opponents; recent five games "
    "and current season separately, repeated opponents count per game",
    "signs": "higher opposing offense EPA is harder; lower opposing defense EPA allowed is harder",
    "availability": "strictly available before forecast; kickoff plus 24h retrospective assumption",
    "unknown": "zero exposure or no schedule history remains null; no future opponents in SOS",
}


def add_playbook_features(teams: pl.DataFrame, features: pl.DataFrame) -> pl.DataFrame:
    history, opponents = TeamHistory(teams), OpponentHistory(teams)
    prefixes = {
        name: np.concatenate(([0.0], np.cumsum([game[name] for game in history.rows])))
        for kind in ("off", "def")
        for metric in PLAY_METRICS
        for name in (f"{kind}_{metric}_count", f"{kind}_{metric}_exposure")
    }
    records = []
    for row in features.to_dicts():
        cutoff: datetime = row["prediction_time_utc"]
        season = row["season"]
        ratings = opponents.rating(season, cutoff)
        available_count = bisect_left(history.times, cutoff)
        extra: dict[str, Any] = {
            "game_id": row["game_id"],
            "playbook_history_available_at_utc": ratings["latest_available_at_utc"],
        }
        for side in ("home", "away"):
            team = row[f"{side}_team"]
            prior = history.available(team, cutoff)
            previous = [game for game in prior if game["season"] == season - 1]
            recent = prior[-5:]
            weights = [0.85 ** (len(recent) - i - 1) for i in range(len(recent))]
            for kind in ("off", "def"):
                for metric in PLAY_METRICS:
                    group = f"{kind}_{metric}_"
                    fallback = previous
                    exposures = sum(game[group + "exposure"] for game in fallback)
                    if not exposures:
                        exposures = prefixes[group + "exposure"][available_count]
                        count_prior = prefixes[group + "count"][available_count]
                    else:
                        count_prior = sum(game[group + "count"] for game in fallback)
                    rate = float(count_prior / exposures) if exposures else None
                    observed = sum(
                        game[group + "exposure"] * w
                        for game, w in zip(recent, weights, strict=True)
                    )
                    count = sum(
                        game[group + "count"] * w for game, w in zip(recent, weights, strict=True)
                    )
                    extra[f"{side}_{kind}_{metric}_prior"] = (
                        (count + 100 * rate) / (observed + 100)
                        if rate is not None
                        else count / observed
                        if observed
                        else None
                    )
                rating_name = f"{kind}_adjusted_epa"
                extra[f"{side}_{kind}_strength"] = ratings["teams"].get(team, ratings["fallback"])[
                    rating_name
                ]
                for window, schedule in (
                    ("recent", recent),
                    ("season", [g for g in prior if g["season"] == season]),
                ):
                    values = [
                        ratings["teams"].get(g["opponent_team"], ratings["fallback"])[rating_name]
                        for g in schedule
                    ]
                    extra[f"{side}_sos_{kind}_{window}"] = (
                        sum(values) / len(values) if values else None
                    )
        records.append(extra)
    extras = pl.DataFrame(records, infer_schema_length=None).with_columns(
        pl.col(*PLAY_FEATURES, *STRENGTH_FEATURES).cast(pl.Float64),
        pl.col("playbook_history_available_at_utc").cast(pl.Datetime("us", "UTC")),
    )
    return features.join(extras, on="game_id", validate="1:1", maintain_order="left")
