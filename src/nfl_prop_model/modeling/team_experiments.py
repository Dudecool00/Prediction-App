"""Fixed team-score ablations on identical expanding-window games and cutoffs."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from numpy.typing import NDArray

from nfl_prop_model.data.schemas import DataQualityError, require_keys
from nfl_prop_model.data.storage import load_frame, read_json, store_frame, write_json
from nfl_prop_model.data.team_games import load_team_cache
from nfl_prop_model.features import team_experiments as feature_module
from nfl_prop_model.features.team_epa import HORIZONS, TEAM_FEATURES, matchup_features
from nfl_prop_model.features.team_experiments import (
    ADJUSTED_FEATURES,
    EXPERIMENT_FEATURE_POLICY,
    SCORING_FEATURES,
    VOLUME_FEATURES,
    add_experiment_features,
)
from nfl_prop_model.modeling.reporting import markdown_table
from nfl_prop_model.modeling.team_scores import (
    forecast_rows,
    model_source_hash,
    predict_models,
    runtime_versions,
    score_metrics,
    score_pipeline,
)

CANDIDATE_FEATURES = {
    "ridge_epa_scoring_scores": TEAM_FEATURES + SCORING_FEATURES,
    "ridge_epa_scoring_volume_scores": TEAM_FEATURES + SCORING_FEATURES + VOLUME_FEATURES,
    "ridge_opponent_epa_scores": TEAM_FEATURES + ADJUSTED_FEATURES,
    "ridge_epa_scoring_volume_opponent_scores": (
        TEAM_FEATURES + SCORING_FEATURES + VOLUME_FEATURES + ADJUSTED_FEATURES
    ),
}
EXPERIMENT_POLICY = {
    "design": "four fixed feature ablations, same games, chronological cutoffs and Ridge alpha 20",
    "candidate_features": {name: list(features) for name, features in CANDIDATE_FEATURES.items()},
    "feature_policy": EXPERIMENT_FEATURE_POLICY,
    "primary_metric": "early-week margin MAE; score MAE and seasonal stability also assessed",
    "comparison": "current ridge_epa_scores; league and prior-score controls retained",
    "resampling": "5000 paired NFL season/week blocks, seed 20261013; approximate diagnostic",
    "promotion": "research comparison only; no automatic change to published forecast model",
    "interpretation": "2022-2024 are development seasons; 2025 is already accessed",
}


def experiment_source_hash() -> str:
    return hashlib.sha256(
        model_source_hash().encode()
        + Path(feature_module.__file__).read_bytes()
        + Path(__file__).read_bytes()
    ).hexdigest()


def predict_experiments(train: pl.DataFrame, test: pl.DataFrame) -> dict[str, NDArray[np.float64]]:
    # Reuse the current controls and all of their chronology/target validation.
    values = predict_models(train, test)
    for frame in (train, test):
        if missing := set(SCORING_FEATURES + VOLUME_FEATURES + ADJUSTED_FEATURES) - set(
            frame.columns
        ):
            raise DataQualityError(f"Missing team experiment features: {sorted(missing)}")
        if frame.filter(
            pl.col("opponent_history_available_at_utc") >= pl.col("prediction_time_utc")
        ).height:
            raise DataQualityError("Opponent history was unavailable at prediction time")
        if frame.select(
            pl.any_horizontal(
                ~pl.col(SCORING_FEATURES + VOLUME_FEATURES + ADJUSTED_FEATURES).is_finite()
            )
            .fill_null(False)
            .any()
        ).item():
            raise DataQualityError("Experiment features must be finite or null")
    targets = np.asarray(train.select("home_score", "away_score").to_numpy(), dtype=np.float64)
    for name, columns in CANDIDATE_FEATURES.items():
        model = score_pipeline()
        model.fit(np.asarray(train.select(columns).to_numpy(), dtype=np.float64), targets)
        values[name] = np.asarray(
            model.predict(np.asarray(test.select(columns).to_numpy(), dtype=np.float64)),
            dtype=np.float64,
        )
    return values


def paired_comparisons(predictions: pl.DataFrame) -> list[dict[str, Any]]:
    """Pair by game, never treat refresh versions or the two team scores as extra games."""
    require_keys(predictions, ["game_id", "horizon", "model"], "team experiment predictions")
    records = []
    for horizon in HORIZONS:
        selected = predictions.filter(pl.col("horizon") == horizon).with_columns(
            (pl.col("predicted_home_margin") - pl.col("target_home_margin")).abs().alias("error")
        )
        control = selected.filter(pl.col("model") == "ridge_epa_scores").select(
            "game_id", pl.col("error").alias("control_error")
        )
        for model in CANDIDATE_FEATURES:
            candidate = selected.filter(pl.col("model") == model)
            paired = candidate.join(control, on="game_id", validate="1:1").with_columns(
                (pl.col("control_error") - pl.col("error")).alias("gain")
            )
            if paired.height != candidate.height or paired.height != control.height:
                raise DataQualityError("Experiment model cohorts do not match")
            weeks = (
                paired.group_by("season", "week")
                .agg(pl.col("gain").sum(), pl.len().alias("games"))
                .sort("season", "week")
            )
            if weeks.is_empty():
                raise DataQualityError("No paired team experiment weeks")
            rng = np.random.default_rng(20261013)
            indices = rng.integers(0, weeks.height, size=(5000, weeks.height))
            gains = weeks["gain"].to_numpy()[indices].sum(axis=1)
            gains /= weeks["games"].to_numpy()[indices].sum(axis=1)
            lower, upper = np.quantile(gains, [0.025, 0.975])
            records.append(
                {
                    "horizon": horizon,
                    "model": model,
                    "games": paired.height,
                    "margin_mae_gain": float(paired["gain"].to_numpy().mean()),
                    "week_bootstrap_gain_lower": float(lower),
                    "week_bootstrap_gain_upper": float(upper),
                }
            )
    return records


def evaluate_team_experiments(
    teams: pl.DataFrame,
    games: pl.DataFrame,
    *,
    evaluation_seasons: tuple[int, ...] = (2022, 2023, 2024),
) -> tuple[pl.DataFrame, pl.DataFrame, list[dict[str, Any]]]:
    if not evaluation_seasons:
        raise ValueError("Specify at least one team evaluation season")
    records, folds = [], []
    for horizon in HORIZONS:
        # Future seasons do not need feature construction for a development-only comparison.
        selected_games = games.filter(pl.col("season") <= max(evaluation_seasons))
        features = add_experiment_features(
            teams, matchup_features(teams, selected_games, horizon=horizon, schedule_history=games)
        )
        evaluation = features.filter(
            pl.col("season").is_in(evaluation_seasons) & pl.col("home_score").is_not_null()
        )
        if evaluation.is_empty():
            raise DataQualityError("No completed games in requested team experiment seasons")
        for test in evaluation.partition_by("prediction_time_utc", maintain_order=True):
            cutoff = test["prediction_time_utc"][0]
            train = features.filter(
                (pl.col("result_available_at_utc") < cutoff) & pl.col("home_score").is_not_null()
            )
            records.extend(forecast_rows(test, predict_experiments(train, test), train))
            latest = train["result_available_at_utc"].max()
            if not isinstance(latest, datetime):
                raise DataQualityError("Missing experiment training availability")
            folds.append(
                {
                    "horizon": horizon,
                    "cutoff_utc": cutoff.isoformat(),
                    "training_games": train.height,
                    "test_games": test.height,
                    "latest_training_result_available_utc": latest.isoformat(),
                }
            )
    predictions = pl.DataFrame(records, infer_schema_length=None)
    require_keys(predictions, ["game_id", "horizon", "model"], "team experiment predictions")
    return predictions, score_metrics(predictions), folds


def save_team_experiments(
    data_dir: Path, report_dir: Path, *, evaluation_seasons: tuple[int, ...] = (2022, 2023, 2024)
) -> dict[str, Any]:
    teams, games, sources = load_team_cache(data_dir)
    predictions, metrics, folds = evaluate_team_experiments(
        teams, games, evaluation_seasons=evaluation_seasons
    )
    directory = data_dir / "processed" / "team_experiments"
    report: dict[str, Any] = {
        "format_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "experiment_source_sha256": experiment_source_hash(),
        "runtime": runtime_versions(),
        "policy": EXPERIMENT_POLICY,
        "evaluation_seasons": list(evaluation_seasons),
        "sources": sources,
        "metrics": metrics.to_dicts(),
        "folds": folds,
        "paired_comparisons": paired_comparisons(predictions),
        "artifacts": {"predictions": store_frame(directory, "predictions", predictions)},
    }
    write_json(directory / "manifest.json", report)
    write_json(report_dir / "team_experiments.json", report)
    lines = [
        "# Team EPA feature experiments",
        "",
        "Four fixed ablations, identical games/cutoffs, Ridge alpha 20. "
        "Published forecasts retain the original model pending assessment.",
        "",
        "2022–2024 are development seasons. "
        "Historical publication/revisions are not reconstructed. "
        "2025 is already accessed. Gains are retrospective, not prospective validation.",
        "",
        "Score MAE counts both team scores; margin/total count each game once. "
        "Higher paired gain means lower margin error versus the current EPA model.",
        "",
        "## Overall",
        "",
        *markdown_table(metrics.filter(pl.col("scope") == "overall").drop("scope", "season")),
        "",
        "## By season",
        "",
        *markdown_table(metrics.filter(pl.col("scope") == "season").drop("scope")),
        "",
        "## Paired margin gains",
        "",
        *markdown_table(pl.DataFrame(report["paired_comparisons"])),
        "",
        "Week-block bootstrap ranges are approximate diagnostics. "
        "Serial dependence and candidate selection are not corrected.",
    ]
    (report_dir / "team_experiments.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def load_team_experiments(data_dir: Path) -> tuple[dict[str, Any], pl.DataFrame]:
    directory = data_dir / "processed" / "team_experiments"
    report = read_json(directory / "manifest.json")
    if report["experiment_source_sha256"] != experiment_source_hash():
        raise DataQualityError("Team experiments changed; run team-experiment again")
    return report, load_frame(directory, report["artifacts"]["predictions"])
