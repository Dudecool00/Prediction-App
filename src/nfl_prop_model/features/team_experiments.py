"""Past-only scoring, volume and regularized opponent-strength experiments."""

from bisect import bisect_left
from collections import defaultdict
from datetime import datetime
from typing import Any

import numpy as np
import polars as pl

from nfl_prop_model.data.schemas import DataQualityError, require_keys
from nfl_prop_model.features.team_epa import TeamHistory

SCORING_FEATURES = tuple(
    f"{side}_points_{kind}_prior" for side in ("home", "away") for kind in ("scored", "allowed")
)
VOLUME_FEATURES = tuple(
    f"{side}_{kind}_plays_prior" for side in ("home", "away") for kind in ("off", "def")
)
ADJUSTED_FEATURES = tuple(
    f"{side}_{kind}_adjusted_{split}"
    for side in ("home", "away")
    for kind in ("off", "def")
    for split in ("epa", "dropback_epa", "rush_epa")
)
EXPERIMENT_FEATURE_POLICY = {
    "scoring": "existing prior scoring/allowed means with four prior-season pseudo-games",
    "volume": "eligible offensive/defensive plays per game with four prior-season pseudo-games",
    "opponent": "EPA = league intercept + offensive team effect + opposing defensive effect",
    "opponent_history": "available current season at weight 1; previous season at weight 0.25",
    "opponent_penalty": "200 eligible pseudo-plays toward zero for each offense/defense effect",
    "opponent_outputs": "intercept plus team effect; defensive EPA allowed: lower is better",
    "availability": "all league and team inputs strictly available before each prediction time",
}


class OpponentHistory:
    """Fit each rating snapshot from that cutoff's available league games only."""

    def __init__(self, teams: pl.DataFrame) -> None:
        require_keys(teams, ["game_id", "team"], "opponent EPA history")
        self.by_season: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in teams.sort("result_available_at_utc", "game_id", "team").to_dicts():
            if not isinstance(row["result_available_at_utc"], datetime):
                raise DataQualityError("Missing opponent history availability")
            self.by_season[row["season"]].append(row)
        self.times = {
            season: [row["result_available_at_utc"] for row in rows]
            for season, rows in self.by_season.items()
        }
        self.cache: dict[tuple[int, datetime], dict[str, Any]] = {}

    def rating(self, season: int, cutoff: datetime) -> dict[str, Any]:
        key = (season, cutoff)
        if key in self.cache:
            return self.cache[key]
        rows, weights = [], []
        for year, weight in ((season - 1, 0.25), (season, 1.0)):
            count = bisect_left(self.times.get(year, []), cutoff)
            available = self.by_season.get(year, [])[:count]
            rows.extend(available)
            weights.extend([weight] * count)
        names = sorted({row[name] for row in rows for name in ("team", "opponent_team")})
        index = {team: number for number, team in enumerate(names)}
        result: dict[str, Any] = {
            "teams": {team: {} for team in names},
            "fallback": {},
            "latest_available_at_utc": max(
                (row["result_available_at_utc"] for row in rows), default=None
            ),
        }
        size = len(names)
        # Each team-game contributes its own offense once; defensive sums are not duplicated.
        x = np.zeros((len(rows), 2 * size), dtype=np.float64)
        for number, row in enumerate(rows):
            x[number, index[row["team"]]] = 1
            x[number, size + index[row["opponent_team"]]] = 1
        for split, group in (
            ("epa", "off_"),
            ("dropback_epa", "off_dropback_"),
            ("rush_epa", "off_rush_"),
        ):
            plays = np.asarray([row[group + "plays"] for row in rows], dtype=np.float64)
            sums = np.asarray([row[group + "epa_sum"] for row in rows], dtype=np.float64)
            weights_array = np.asarray(weights, dtype=np.float64)
            if not np.isfinite(plays).all() or not np.isfinite(sums).all() or (plays < 0).any():
                raise DataQualityError("Opponent EPA sums and play counts must be finite")
            if ((plays == 0) & (sums != 0)).any():
                raise DataQualityError("Opponent EPA without eligible plays")
            w = weights_array * plays
            weighted_sum = weights_array * sums
            # An unpenalized intercept is fitted jointly with the regularized team effects.
            design = np.column_stack([np.ones(len(rows)), x])
            normal = design.T @ (w[:, None] * design)
            normal += np.diag([0.0, *([200.0] * (2 * size))])
            if w.sum():
                coefficients = np.linalg.solve(normal, design.T @ weighted_sum)
                intercept = float(coefficients[0])
            else:
                coefficients = np.zeros(1 + 2 * size, dtype=np.float64)
                intercept = 0.0
            for kind, offset in (("off", 1), ("def", 1 + size)):
                feature = f"{kind}_adjusted_{split}"
                result["fallback"][feature] = intercept
                for team, number in index.items():
                    result["teams"][team][feature] = intercept + float(
                        coefficients[offset + number]
                    )
        self.cache[key] = result
        return result


def add_experiment_features(teams: pl.DataFrame, features: pl.DataFrame) -> pl.DataFrame:
    """Preserve canonical matchup rows and append only explicit historical predictors."""
    history, opponent = TeamHistory(teams), OpponentHistory(teams)
    records = []
    for row in features.to_dicts():
        cutoff, season = row["prediction_time_utc"], row["season"]
        adjusted = opponent.rating(season, cutoff)
        extra: dict[str, Any] = {
            "game_id": row["game_id"],
            "opponent_history_available_at_utc": adjusted["latest_available_at_utc"],
        }
        for side in ("home", "away"):
            team = row[f"{side}_team"]
            prior = history.available(team, cutoff)
            current = [game for game in prior if game["season"] == season]
            previous = [game for game in prior if game["season"] == season - 1]
            for kind in ("off", "def"):
                name = kind + "_plays"
                fallback = previous or prior[-5:]
                prior_mean = (
                    sum(game[name] for game in fallback) / len(fallback) if fallback else None
                )
                extra[f"{side}_{name}_prior"] = (
                    (sum(game[name] for game in current) + 4 * prior_mean) / (len(current) + 4)
                    if prior_mean is not None
                    else None
                )
            rating = adjusted["teams"].get(team, adjusted["fallback"])
            extra.update({f"{side}_{name}": value for name, value in rating.items()})
        records.append(extra)
    extras = pl.DataFrame(records, infer_schema_length=None).with_columns(
        pl.col(*VOLUME_FEATURES, *ADJUSTED_FEATURES).cast(pl.Float64),
        pl.col("opponent_history_available_at_utc").cast(pl.Datetime("us", "UTC")),
    )
    return features.join(extras, on="game_id", how="left", validate="1:1", maintain_order="left")
