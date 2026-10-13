"""Chronological two-score regression with margins derived from the same scores."""

from __future__ import annotations

import hashlib
import platform
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import polars as pl
from numpy.typing import NDArray

from nfl_prop_model.data.schemas import DataQualityError, require_keys
from nfl_prop_model.data.storage import load_frame, read_json, store_frame, write_json
from nfl_prop_model.data.team_games import load_team_cache, team_source_hash
from nfl_prop_model.features.team_epa import (
    FEATURE_POLICY,
    HORIZONS,
    TEAM_FEATURES,
    matchup_features,
)
from nfl_prop_model.modeling.reporting import markdown_table

if TYPE_CHECKING:
    from sklearn.pipeline import Pipeline

MODEL_NAMES = ("league_scores", "prior_team_scores", "ridge_epa_scores")
MODEL_POLICY: dict[str, Any] = {
    "model": "joint two-output Ridge regression for home and away final scores",
    "ridge_alpha": 20.0,
    "minimum_training_games": 64,
    "scoring_baseline": "prior scoring rates plus training home edge; neutral effect is zero",
    "outputs": "scores clipped at zero and rounded to tenths; margins derived from those scores",
    "features": list(TEAM_FEATURES),
    "feature_policy": FEATURE_POLICY,
    "probabilities": "not yet calibrated; no win or spread probabilities published",
    "validation": "retrospective expanding-window simulation; 2025 is already accessed",
}


def model_source_hash() -> str:
    return hashlib.sha256(team_source_hash().encode() + Path(__file__).read_bytes()).hexdigest()


def runtime_versions() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        **{name: version(name) for name in ("numpy", "polars", "scikit-learn")},
    }


def validate_features(frame: pl.DataFrame) -> None:
    required = {"game_id", "prediction_time_utc", "kickoff_utc", *TEAM_FEATURES}
    if missing := required - set(frame.columns):
        raise DataQualityError(f"Team model missing columns: {sorted(missing)}")
    require_keys(frame, ["game_id"], "team model features")
    if frame.filter(pl.col("prediction_time_utc") >= pl.col("kickoff_utc")).height:
        raise DataQualityError("Team features must precede kickoff")
    for side in ("home", "away"):
        if frame.filter(
            pl.col(f"{side}_history_available_at_utc") >= pl.col("prediction_time_utc")
        ).height:
            raise DataQualityError("Team history was not available at prediction time")
    if frame.select(
        pl.any_horizontal(~pl.col(TEAM_FEATURES).is_finite()).fill_null(False).any()
    ).item():
        raise DataQualityError("Team model features must be finite or null")


def score_pipeline() -> Pipeline:
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    return Pipeline(
        [
            (
                "imputer",
                SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True),
            ),
            ("scaler", StandardScaler()),
            ("regressor", Ridge(alpha=20.0)),
        ]
    )


def consistent_scores(home: float, away: float) -> dict[str, float]:
    """Display and stored margins agree with the displayed fractional score projections."""
    if not np.isfinite([home, away]).all():
        raise DataQualityError("Non-finite team score prediction")
    home, away = round(max(0.0, home), 1), round(max(0.0, away), 1)
    margin = round(home - away, 1)
    return {
        "predicted_home_score": home,
        "predicted_away_score": away,
        "predicted_home_margin": margin,
        "predicted_away_margin": -margin,
        "predicted_total": round(home + away, 1),
    }


def _matrix(frame: pl.DataFrame) -> NDArray[np.float64]:
    return np.asarray(frame.select(TEAM_FEATURES).to_numpy(), dtype=np.float64)


def predict_models(train: pl.DataFrame, test: pl.DataFrame) -> dict[str, NDArray[np.float64]]:
    if train.height < MODEL_POLICY["minimum_training_games"]:
        raise DataQualityError("At least 64 earlier completed games are required for team scoring")
    validate_features(train)
    validate_features(test)
    cutoff = test["prediction_time_utc"].min()
    if train.filter(pl.col("result_available_at_utc") >= cutoff).height:
        raise DataQualityError("Training outcomes were unavailable at the forecast cutoff")
    if set(train["game_id"]).intersection(test["game_id"]):
        raise DataQualityError("Training and predicted games overlap")
    y = np.asarray(train.select("home_score", "away_score").to_numpy(), dtype=np.float64)
    if not np.isfinite(y).all():
        raise DataQualityError("Team training scores must be known and finite")
    mean = y.mean(axis=0)
    league_score = float(mean.mean())
    home_advantage = float(mean[0] - mean[1])
    model = score_pipeline()
    model.fit(_matrix(train), y)
    prior = np.array(
        [
            [
                (
                    (
                        row["home_points_scored_prior"]
                        if row["home_points_scored_prior"] is not None
                        else league_score
                    )
                    + (
                        row["away_points_allowed_prior"]
                        if row["away_points_allowed_prior"] is not None
                        else league_score
                    )
                )
                / 2,
                (
                    (
                        row["away_points_scored_prior"]
                        if row["away_points_scored_prior"] is not None
                        else league_score
                    )
                    + (
                        row["home_points_allowed_prior"]
                        if row["home_points_allowed_prior"] is not None
                        else league_score
                    )
                )
                / 2,
            ]
            for row in test.to_dicts()
        ],
        dtype=np.float64,
    )
    venue = test["home_field"].to_numpy() * home_advantage / 2
    prior[:, 0] += venue
    prior[:, 1] -= venue
    return {
        "league_scores": np.tile(mean, (test.height, 1)),
        "prior_team_scores": prior,
        "ridge_epa_scores": np.asarray(model.predict(_matrix(test)), dtype=np.float64),
    }


def forecast_rows(
    test: pl.DataFrame,
    values: dict[str, NDArray[np.float64]],
    train: pl.DataFrame,
) -> list[dict[str, Any]]:
    records = []
    latest = train["result_available_at_utc"].max()
    for model, predictions in values.items():
        for row, scores in zip(test.to_dicts(), predictions, strict=True):
            result = {
                name: row[name]
                for name in (
                    "game_id",
                    "season",
                    "week",
                    "home_team",
                    "away_team",
                    "kickoff_utc",
                    "prediction_time_utc",
                    "horizon",
                    "home_score",
                    "away_score",
                    "target_home_margin",
                    "home_prior_games",
                    "away_prior_games",
                )
            }
            result.update(consistent_scores(float(scores[0]), float(scores[1])))
            result.update(
                {
                    "model": model,
                    "training_games": train.height,
                    "latest_training_result_available_utc": latest,
                }
            )
            records.append(result)
    return records


def score_metrics(predictions: pl.DataFrame) -> pl.DataFrame:
    records = []
    for scope in ("overall", "season"):
        keys = ["horizon", "model"] + (["season"] if scope == "season" else [])
        for group in predictions.partition_by(keys, maintain_order=True):
            first = group.row(0, named=True)
            values = {key: first[key] for key in keys}
            values.update(
                {
                    "scope": scope,
                    "season": first["season"] if scope == "season" else None,
                    "games": group.height,
                }
            )
            home_error = group["predicted_home_score"].to_numpy() - group["home_score"].to_numpy()
            away_error = group["predicted_away_score"].to_numpy() - group["away_score"].to_numpy()
            for name, errors in (
                ("score", np.concatenate([home_error, away_error])),
                (
                    "margin",
                    group["predicted_home_margin"].to_numpy()
                    - group["target_home_margin"].to_numpy(),
                ),
                ("total", home_error + away_error),
            ):
                values[f"{name}_mae"] = float(np.abs(errors).mean())
                values[f"{name}_rmse"] = float(np.sqrt(np.mean(errors**2)))
                values[f"{name}_bias"] = float(errors.mean())
            records.append(values)
    return pl.DataFrame(records, infer_schema_length=None).sort(
        "scope", "horizon", "model", "season"
    )


def evaluate_team_scores(
    teams: pl.DataFrame,
    games: pl.DataFrame,
    *,
    evaluation_seasons: tuple[int, ...] = (2022, 2023, 2024),
) -> tuple[pl.DataFrame, pl.DataFrame, list[dict[str, Any]]]:
    if not evaluation_seasons:
        raise ValueError("Specify at least one team evaluation season")
    records, folds = [], []
    for horizon in HORIZONS:
        features = matchup_features(teams, games, horizon=horizon)
        validate_features(features)
        evaluation = features.filter(
            pl.col("season").is_in(evaluation_seasons) & pl.col("home_score").is_not_null()
        )
        if evaluation.is_empty():
            raise DataQualityError("No completed games in the requested team evaluation seasons")
        for test in evaluation.partition_by("prediction_time_utc", maintain_order=True):
            cutoff = test["prediction_time_utc"][0]
            train = features.filter(
                (pl.col("result_available_at_utc") < cutoff) & pl.col("home_score").is_not_null()
            )
            values = predict_models(train, test)
            records.extend(forecast_rows(test, values, train))
            latest_available = train["result_available_at_utc"].max()
            if not isinstance(latest_available, datetime):
                raise DataQualityError("Missing latest training availability")
            folds.append(
                {
                    "horizon": horizon,
                    "cutoff_utc": cutoff.isoformat(),
                    "training_games": train.height,
                    "test_games": test.height,
                    "latest_training_result_available_utc": latest_available.isoformat(),
                }
            )
    predictions = pl.DataFrame(records, infer_schema_length=None)
    require_keys(predictions, ["game_id", "horizon", "model"], "team evaluation predictions")
    return predictions, score_metrics(predictions), folds


def save_team_evaluation(
    data_dir: Path,
    report_dir: Path,
    *,
    evaluation_seasons: tuple[int, ...] = (2022, 2023, 2024),
) -> dict[str, Any]:
    teams, games, sources = load_team_cache(data_dir)
    predictions, metrics, folds = evaluate_team_scores(
        teams, games, evaluation_seasons=evaluation_seasons
    )
    directory = data_dir / "processed" / "team_scores"
    report = {
        "format_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "model_source_sha256": model_source_hash(),
        "runtime": runtime_versions(),
        "policy": MODEL_POLICY,
        "evaluation_seasons": list(evaluation_seasons),
        "sources": sources,
        "metrics": metrics.to_dicts(),
        "folds": folds,
        "artifacts": {"predictions": store_frame(directory, "predictions", predictions)},
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    write_json(directory / "manifest.json", report)
    write_json(report_dir / "team_scores.json", report)
    lines = [
        "# Team EPA score and margin backtest",
        "",
        "Joint final-score projections; each margin comes from the same rounded scores.",
        "",
        "Retrospective simulation with kickoff-plus-24-hour availability. "
        "Historical source revisions "
        "are not reconstructed. 2025 is already accessed, not a fresh holdout.",
        "",
        "Score error counts both team scores; margin and total error count each game once. "
        "Forecast horizons are scored separately. No calibrated probabilities yet.",
        "",
        "## Overall metrics",
        "",
    ]
    lines.extend(
        markdown_table(metrics.filter(pl.col("scope") == "overall").drop("scope", "season"))
    )
    lines.extend(["", "## By season", ""])
    lines.extend(markdown_table(metrics.filter(pl.col("scope") == "season").drop("scope")))
    (report_dir / "team_scores.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def load_team_evaluation(data_dir: Path) -> tuple[dict[str, Any], pl.DataFrame]:
    directory = data_dir / "processed" / "team_scores"
    report = read_json(directory / "manifest.json")
    if report["model_source_sha256"] != model_source_hash():
        raise DataQualityError("Team model changed; run team-evaluate again")
    return report, load_frame(directory, report["artifacts"]["predictions"])


def save_team_forecast(
    data_dir: Path,
    *,
    horizon: str = "early_week",
    days: int = 7,
    as_of: datetime | None = None,
) -> Path:
    if horizon not in HORIZONS or not 1 <= days <= 28:
        raise ValueError("Use a supported team horizon and 1-28 days")
    now = datetime.now(UTC)
    as_of = as_of or now
    if as_of.tzinfo is None or as_of > now:
        raise ValueError("Forecast time must be timezone-aware and cannot be in the future")
    teams, games, sources = load_team_cache(data_dir)
    retrievals = [
        datetime.fromisoformat(source["retrieved_at_utc"]) for source in sources["sources"]
    ]
    if not retrievals:
        raise DataQualityError("Team forecasts require timestamped source snapshots")
    if max(retrievals) > as_of:
        raise DataQualityError(
            "A team source snapshot was retrieved after the requested forecast time"
        )
    # NFL January games belong to the previous season, not the calendar year.
    relevant_seasons = (
        games.filter(
            (pl.col("kickoff_utc") > as_of)
            & (pl.col("kickoff_utc") <= as_of + timedelta(days=days))
        )["season"]
        .unique()
        .to_list()
    )
    if not relevant_seasons:
        relevant_seasons = [as_of.year if as_of.month >= 9 else as_of.year - 1]
    fresh_seasons = {
        source["season"]
        for source in sources["sources"]
        if as_of - datetime.fromisoformat(source["retrieved_at_utc"]) <= timedelta(hours=24)
    }
    if not set(relevant_seasons).issubset(fresh_seasons):
        raise DataQualityError(
            "Refresh the current-season team source; required age is under 24 hours"
        )
    future = games.filter(
        (pl.col("kickoff_utc") > as_of + timedelta(hours=1))
        & (pl.col("kickoff_utc") <= as_of + timedelta(days=days))
        & pl.col("home_score").is_null()
    )
    if future.is_empty():
        raise DataQualityError("No upcoming regular-season games inside this forecast window")
    if horizon == "pregame_refresh":
        future = future.filter(pl.col("kickoff_utc") <= as_of + timedelta(hours=24))
        if future.is_empty():
            raise DataQualityError("Pregame refresh requires a game 1-24 hours before kickoff")
    test = matchup_features(
        teams,
        future,
        horizon=horizon,
        prediction_time=as_of,
        schedule_history=games,
    )
    historical = matchup_features(teams, games, horizon=horizon)
    train = historical.filter(
        (pl.col("result_available_at_utc") < as_of) & pl.col("home_score").is_not_null()
    )
    values = predict_models(train, test)
    predictions = pl.DataFrame(
        forecast_rows(test, {"ridge_epa_scores": values["ridge_epa_scores"]}, train),
        infer_schema_length=None,
    )
    # Each run has a fresh directory; never replace an earlier forecast version.
    directory = (
        data_dir / "journal" / "team_scores" / f"snapshot-{now.strftime('%Y%m%dT%H%M%S%fZ')}"
    )
    report = {
        "format_version": 1,
        "created_at_utc": now.isoformat(),
        "as_of_utc": as_of.isoformat(),
        "horizon": horizon,
        "model_source_sha256": model_source_hash(),
        "runtime": runtime_versions(),
        "policy": MODEL_POLICY,
        "sources": sources,
        "training_games": train.height,
        "status": "EPA research forecast; QB/injury/weather and uncertainty adjustments pending",
        "artifacts": {
            "predictions": store_frame(directory, "predictions", predictions),
            "features": store_frame(directory, "features", test),
            "training_features": store_frame(directory, "training_features", train),
        },
    }
    write_json(directory / "manifest.json", report)
    return directory / "manifest.json"
