"""Controlled EPA plus prior-passer and timestamped-injury comparisons."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from numpy.typing import NDArray

from nfl_prop_model.data.schemas import DataQualityError, require_keys
from nfl_prop_model.data.storage import load_frame, read_json, store_frame, write_json
from nfl_prop_model.data.team_context import context_source_hash, load_context_cache
from nfl_prop_model.data.team_games import load_team_cache
from nfl_prop_model.features.team_context import (
    CONTEXT_FEATURE_POLICY,
    INJURY_FEATURES,
    INTERACTION_FEATURES,
    QB_FEATURES,
    add_context_features,
)
from nfl_prop_model.features.team_epa import HORIZONS, TEAM_FEATURES, matchup_features
from nfl_prop_model.modeling.reporting import markdown_table
from nfl_prop_model.modeling.team_scores import (
    forecast_rows,
    model_source_hash,
    predict_models,
    runtime_versions,
    score_metrics,
    score_pipeline,
)

CONTEXT_MODELS = {
    "ridge_epa_qb_history_scores": TEAM_FEATURES + QB_FEATURES,
    "ridge_epa_injury_scores": TEAM_FEATURES + INJURY_FEATURES,
    "ridge_epa_qb_injury_scores": TEAM_FEATURES
    + QB_FEATURES
    + INJURY_FEATURES
    + INTERACTION_FEATURES,
}
CONTEXT_MODEL_POLICY = {
    "models": {name: list(features) for name, features in CONTEXT_MODELS.items()},
    "feature_policy": CONTEXT_FEATURE_POLICY,
    "ridge_alpha": 20.0,
    "comparison": "same games and expanding-window cutoffs as current EPA and scoring controls",
    "probabilities": "none published; research comparison only",
    "interpretation": "2022-2024 development; injury publication/revisions not reconstructed",
    "promotion": "no automatic change to forecast model; live source integration remains separate",
}


def context_model_source_hash() -> str:
    return hashlib.sha256(
        context_source_hash().encode() + model_source_hash().encode() + Path(__file__).read_bytes()
    ).hexdigest()


def predict_context_models(
    train: pl.DataFrame, test: pl.DataFrame
) -> dict[str, NDArray[np.float64]]:
    values = predict_models(train, test)
    extra_features = QB_FEATURES + INJURY_FEATURES + INTERACTION_FEATURES
    for frame in (train, test):
        if missing := set(extra_features) - set(frame.columns):
            raise DataQualityError(f"Missing context features: {sorted(missing)}")
        for side in ("home", "away"):
            for kind in ("qb", "injury"):
                if frame.filter(
                    pl.col(f"{side}_{kind}_history_available_at_utc")
                    >= pl.col("prediction_time_utc")
                ).height:
                    raise DataQualityError("Context was unavailable at the forecast cutoff")
        if frame.select(
            pl.any_horizontal(~pl.col(extra_features).is_finite()).fill_null(False).any()
        ).item():
            raise DataQualityError("Context features must be finite or null")
    targets = np.asarray(train.select("home_score", "away_score").to_numpy(), dtype=np.float64)
    for name, columns in CONTEXT_MODELS.items():
        model = score_pipeline()
        model.fit(np.asarray(train.select(columns).to_numpy(), dtype=np.float64), targets)
        values[name] = np.asarray(
            model.predict(np.asarray(test.select(columns).to_numpy(), dtype=np.float64)),
            dtype=np.float64,
        )
    return values


def context_comparisons(predictions: pl.DataFrame) -> list[dict[str, Any]]:
    records = []
    for horizon in HORIZONS:
        frame = predictions.filter(pl.col("horizon") == horizon).with_columns(
            (pl.col("predicted_home_margin") - pl.col("target_home_margin")).abs().alias("error")
        )
        control = frame.filter(pl.col("model") == "ridge_epa_scores").select(
            "game_id", pl.col("error").alias("control_error")
        )
        for model in CONTEXT_MODELS:
            candidate = frame.filter(pl.col("model") == model)
            paired = candidate.join(control, on="game_id", validate="1:1").with_columns(
                (pl.col("control_error") - pl.col("error")).alias("gain")
            )
            if paired.height != control.height or paired.height != candidate.height:
                raise DataQualityError("Context model cohorts do not match")
            weeks = (
                paired.group_by("season", "week")
                .agg(pl.col("gain").sum(), pl.len().alias("games"))
                .sort("season", "week")
            )
            if weeks.is_empty():
                raise DataQualityError("No paired context weeks")
            rng = np.random.default_rng(20261013)
            indexes = rng.integers(0, weeks.height, size=(5000, weeks.height))
            gains = weeks["gain"].to_numpy()[indexes].sum(axis=1)
            gains /= weeks["games"].to_numpy()[indexes].sum(axis=1)
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


def evaluate_team_context(
    teams: pl.DataFrame,
    games: pl.DataFrame,
    passers: pl.DataFrame,
    reports: pl.DataFrame,
    *,
    evaluation_seasons: tuple[int, ...] = (2022, 2023, 2024),
) -> tuple[pl.DataFrame, pl.DataFrame, list[dict[str, Any]], pl.DataFrame]:
    if not evaluation_seasons:
        raise ValueError("Specify at least one context evaluation season")
    if max(evaluation_seasons) > 2024:
        raise ValueError("Context experiment is limited to historical seasons through 2024")
    records, folds, feature_frames = [], [], []
    selected = games.filter(pl.col("season") <= max(evaluation_seasons))
    for horizon in HORIZONS:
        features = add_context_features(
            passers,
            reports,
            teams,
            matchup_features(teams, selected, horizon=horizon, schedule_history=games),
        )
        evaluation = features.filter(
            pl.col("season").is_in(evaluation_seasons) & pl.col("home_score").is_not_null()
        )
        if evaluation.is_empty():
            raise DataQualityError("No completed context evaluation games")
        feature_frames.append(features)
        for test in evaluation.partition_by("prediction_time_utc", maintain_order=True):
            cutoff = test["prediction_time_utc"][0]
            train = features.filter(
                (pl.col("result_available_at_utc") < cutoff) & pl.col("home_score").is_not_null()
            )
            records.extend(forecast_rows(test, predict_context_models(train, test), train))
            latest = train["result_available_at_utc"].max()
            if not isinstance(latest, datetime):
                raise DataQualityError("Missing context training availability")
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
    require_keys(predictions, ["game_id", "horizon", "model"], "context predictions")
    return predictions, score_metrics(predictions), folds, pl.concat(feature_frames)


def save_context_evaluation(
    data_dir: Path, report_dir: Path, *, evaluation_seasons: tuple[int, ...] = (2022, 2023, 2024)
) -> dict[str, Any]:
    teams, games, team_sources = load_team_cache(data_dir)
    passers, injuries, sources = load_context_cache(data_dir)
    directory = data_dir / "processed" / "team_context_scores"
    write_json(
        directory / "protocol.json",
        {
            "locked_at_utc": datetime.now(UTC).isoformat(),
            "policy": CONTEXT_MODEL_POLICY,
            "context_model_source_sha256": context_model_source_hash(),
            "evaluation_seasons": list(evaluation_seasons),
        },
    )
    predictions, metrics, folds, features = evaluate_team_context(
        teams, games, passers, injuries, evaluation_seasons=evaluation_seasons
    )
    evaluated = features.filter(
        pl.col("season").is_in(evaluation_seasons) & pl.col("home_score").is_not_null()
    )
    coverage = (
        evaluated.group_by("horizon")
        .agg(
            pl.len().alias("games"),
            *[
                pl.col(f"{side}_last_primary_passer_id")
                .is_not_null()
                .sum()
                .alias(f"{side}_qb_known_games")
                for side in ("home", "away")
            ],
            *[
                (pl.col(f"{side}_injury_observed_players") > 0)
                .sum()
                .alias(f"{side}_observed_report_games")
                for side in ("home", "away")
            ],
            *[
                (pl.col(f"{side}_qb_reported_out") == 1)
                .sum()
                .alias(f"{side}_reported_primary_out_games")
                for side in ("home", "away")
            ],
            *[
                (pl.col(f"{side}_players_reported_rest_out") > 0)
                .sum()
                .alias(f"{side}_rest_report_games")
                for side in ("home", "away")
            ],
        )
        .sort("horizon")
    )
    report: dict[str, Any] = {
        "format_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "context_model_source_sha256": context_model_source_hash(),
        "runtime": runtime_versions(),
        "policy": CONTEXT_MODEL_POLICY,
        "evaluation_seasons": list(evaluation_seasons),
        "team_sources": team_sources,
        "context_sources": sources,
        "metrics": metrics.to_dicts(),
        "folds": folds,
        "coverage": coverage.to_dicts(),
        "paired_comparisons": context_comparisons(predictions),
        "artifacts": {
            "predictions": store_frame(directory, "predictions", predictions),
            "features": store_frame(directory, "features", features),
        },
    }
    write_json(directory / "manifest.json", report)
    write_json(report_dir / "team_context.json", report)
    lines = [
        "# EPA with QB history and injury observations",
        "",
        "Three fixed additions to the current EPA model, same games/cutoffs and Ridge alpha 20.",
        "",
        "QB identity is a past-primary-passer proxy, not today's confirmed starter. "
        "Injury observations require source modification plus 24 hours before the cutoff. "
        "Missing observations remain unknown. "
        "Historical publication/revisions are not reconstructed.",
        "",
        "Rest flags require an explicit Rest reason on an Out report. Coaching announcements "
        "are not reconstructed. This research comparison does not change published forecasts.",
        "",
        "2022–2024 are development seasons; 2025 is already accessed.",
        "",
        "## Coverage",
        "",
        *markdown_table(coverage),
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
        "Positive gain means lower margin error versus current EPA. "
        "Week-block ranges are approximate; "
        "serial dependence/candidate selection are not corrected.",
    ]
    (report_dir / "team_context.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def load_context_evaluation(data_dir: Path) -> tuple[dict[str, Any], pl.DataFrame]:
    directory = data_dir / "processed" / "team_context_scores"
    report = read_json(directory / "manifest.json")
    if report["context_model_source_sha256"] != context_model_source_hash():
        raise DataQualityError("Context model changed; run team-context-evaluate again")
    return report, load_frame(directory, report["artifacts"]["predictions"])
