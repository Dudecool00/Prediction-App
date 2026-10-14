"""Controlled richer-play and schedule-strength score/margin comparisons."""

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
from nfl_prop_model.data.team_playbook import load_playbook_cache, playbook_source_hash
from nfl_prop_model.features.team_epa import HORIZONS, TEAM_FEATURES, matchup_features
from nfl_prop_model.features.team_playbook import (
    PLAY_FEATURES,
    PLAYBOOK_FEATURE_POLICY,
    STRENGTH_FEATURES,
    add_playbook_features,
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

PLAYBOOK_MODELS = {
    "ridge_epa_play_scores": TEAM_FEATURES + PLAY_FEATURES,
    "ridge_epa_schedule_scores": TEAM_FEATURES + STRENGTH_FEATURES,
    "ridge_epa_play_schedule_scores": TEAM_FEATURES + PLAY_FEATURES + STRENGTH_FEATURES,
}
PLAYBOOK_MODEL_POLICY = {
    "models": {name: list(columns) for name, columns in PLAYBOOK_MODELS.items()},
    "features": PLAYBOOK_FEATURE_POLICY,
    "ridge_alpha": 20.0,
    "comparison": "same games and expanding chronological cutoffs as current EPA controls",
    "promotion": "research comparison; no automatic change to published forecasts",
    "interpretation": "development seasons; historical source revisions not reconstructed",
}


def playbook_model_source_hash() -> str:
    return hashlib.sha256(
        playbook_source_hash().encode() + model_source_hash().encode() + Path(__file__).read_bytes()
    ).hexdigest()


def predict_playbook_models(
    train: pl.DataFrame, test: pl.DataFrame
) -> dict[str, NDArray[np.float64]]:
    values = predict_models(train, test)
    for frame in (train, test):
        if missing := set(PLAY_FEATURES + STRENGTH_FEATURES) - set(frame.columns):
            raise DataQualityError(f"Missing playbook predictors: {sorted(missing)}")
        if frame.filter(
            pl.col("playbook_history_available_at_utc") >= pl.col("prediction_time_utc")
        ).height:
            raise DataQualityError("Playbook history unavailable at forecast cutoff")
        if frame.select(
            pl.any_horizontal(~pl.col(PLAY_FEATURES + STRENGTH_FEATURES).is_finite())
            .fill_null(False)
            .any()
        ).item():
            raise DataQualityError("Playbook predictors must be finite or null")
    targets = np.asarray(train.select("home_score", "away_score").to_numpy(), dtype=np.float64)
    for name, columns in PLAYBOOK_MODELS.items():
        model = score_pipeline()
        model.fit(np.asarray(train.select(columns).to_numpy(), dtype=np.float64), targets)
        values[name] = np.asarray(
            model.predict(np.asarray(test.select(columns).to_numpy(), dtype=np.float64)),
            dtype=np.float64,
        )
    return values


def paired_playbook_comparisons(predictions: pl.DataFrame) -> list[dict[str, Any]]:
    result = []
    for horizon in HORIZONS:
        rows = predictions.filter(pl.col("horizon") == horizon).with_columns(
            (pl.col("predicted_home_margin") - pl.col("target_home_margin")).abs().alias("error")
        )
        control = rows.filter(pl.col("model") == "ridge_epa_scores").select(
            "game_id", pl.col("error").alias("control_error")
        )
        for name in PLAYBOOK_MODELS:
            candidate = rows.filter(pl.col("model") == name)
            paired = candidate.join(control, on="game_id", validate="1:1").with_columns(
                (pl.col("control_error") - pl.col("error")).alias("gain")
            )
            if paired.height != control.height or paired.height != candidate.height:
                raise DataQualityError("Playbook comparison cohorts differ")
            weeks = (
                paired.group_by("season", "week")
                .agg(pl.col("gain").sum(), pl.len().alias("games"))
                .sort("season", "week")
            )
            if weeks.is_empty():
                raise DataQualityError("No paired playbook weeks")
            rng = np.random.default_rng(20261014)
            indexes = rng.integers(0, weeks.height, size=(5000, weeks.height))
            samples = weeks["gain"].to_numpy()[indexes].sum(axis=1) / weeks["games"].to_numpy()[
                indexes
            ].sum(axis=1)
            lower, upper = np.quantile(samples, [0.025, 0.975])
            result.append(
                {
                    "horizon": horizon,
                    "model": name,
                    "games": paired.height,
                    "margin_mae_gain": float(paired["gain"].to_numpy().mean()),
                    "week_bootstrap_gain_lower": float(lower),
                    "week_bootstrap_gain_upper": float(upper),
                }
            )
    return result


def evaluate_playbook(
    teams: pl.DataFrame,
    games: pl.DataFrame,
    *,
    evaluation_seasons: tuple[int, ...] = (2022, 2023, 2024),
) -> tuple[pl.DataFrame, pl.DataFrame, list[dict[str, Any]], pl.DataFrame]:
    if not evaluation_seasons:
        raise ValueError("Specify playbook evaluation seasons")
    # No games without expanded measurements may enter training or evaluation silently.
    selected = games.filter(pl.col("season").is_in(teams["season"].unique().to_list()))
    expected = games.filter(
        pl.col("season").is_in(evaluation_seasons) & pl.col("home_score").is_not_null()
    )
    if expected.is_empty() or expected.join(selected, on="game_id", how="anti").height:
        raise DataQualityError("Missing playbook evaluation seasons")
    records, folds, feature_frames = [], [], []
    for horizon in HORIZONS:
        features = add_playbook_features(
            teams, matchup_features(teams, selected, horizon=horizon, schedule_history=games)
        )
        feature_frames.append(features)
        evaluation = features.filter(
            pl.col("season").is_in(evaluation_seasons) & pl.col("home_score").is_not_null()
        )
        for test in evaluation.partition_by("prediction_time_utc", maintain_order=True):
            cutoff = test["prediction_time_utc"][0]
            train = features.filter(
                (pl.col("result_available_at_utc") < cutoff) & pl.col("home_score").is_not_null()
            )
            records.extend(forecast_rows(test, predict_playbook_models(train, test), train))
            latest = train["result_available_at_utc"].max()
            if not isinstance(latest, datetime):
                raise DataQualityError("Missing playbook training availability")
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
    require_keys(predictions, ["game_id", "horizon", "model"], "playbook predictions")
    return predictions, score_metrics(predictions), folds, pl.concat(feature_frames)


def save_playbook_evaluation(
    data_dir: Path, report_dir: Path, *, evaluation_seasons: tuple[int, ...] = (2022, 2023, 2024)
) -> dict[str, Any]:
    teams, sources = load_playbook_cache(data_dir)
    canonical, games, canonical_sources = load_team_cache(data_dir)
    expected = canonical.filter(pl.col("season").is_in(sources["seasons"]))
    if (
        expected.select(canonical.columns).sort("game_id", "team").to_dicts()
        != teams.select(canonical.columns).sort("game_id", "team").to_dicts()
    ):
        raise DataQualityError("Playbook canonical team snapshot changed; rebuild playbook")
    directory = data_dir / "processed" / "team_playbook_scores"
    write_json(
        directory / "protocol.json",
        {
            "locked_at_utc": datetime.now(UTC).isoformat(),
            "policy": PLAYBOOK_MODEL_POLICY,
            "evaluation_seasons": list(evaluation_seasons),
            "playbook_model_source_sha256": playbook_model_source_hash(),
        },
    )
    predictions, metrics, folds, features = evaluate_playbook(
        teams, games, evaluation_seasons=evaluation_seasons
    )
    report: dict[str, Any] = {
        "format_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "playbook_model_source_sha256": playbook_model_source_hash(),
        "runtime": runtime_versions(),
        "policy": PLAYBOOK_MODEL_POLICY,
        "evaluation_seasons": list(evaluation_seasons),
        "sources": sources,
        "canonical_sources": canonical_sources,
        "metrics": metrics.to_dicts(),
        "folds": folds,
        "paired_comparisons": paired_playbook_comparisons(predictions),
        "artifacts": {
            "predictions": store_frame(directory, "predictions", predictions),
            "features": store_frame(directory, "features", features),
        },
    }
    write_json(directory / "manifest.json", report)
    write_json(report_dir / "team_playbook.json", report)
    lines = [
        "# Play-by-play and strength-of-schedule comparison",
        "",
        "Three fixed additions; same games/cutoffs, Ridge alpha 20, and original EPA controls.",
        "",
        "Past games only. Opponent ratings use the forecast cutoff, including earlier games "
        "against the evaluated team. SOS is split into opposing offense and defense; "
        "no future opponents or final-season ratings enter it.",
        "",
        "Research comparison; historical publication/revisions are not reconstructed. "
        "2022–2024 are development seasons.",
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
        "Positive gain means lower margin error than current EPA. Week-block ranges are "
        "approximate; serial dependence and repeated candidate selection are not corrected.",
    ]
    (report_dir / "team_playbook.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def load_playbook_evaluation(data_dir: Path) -> tuple[dict[str, Any], pl.DataFrame]:
    directory = data_dir / "processed" / "team_playbook_scores"
    report = read_json(directory / "manifest.json")
    if report["playbook_model_source_sha256"] != playbook_model_source_hash():
        raise DataQualityError("Playbook model changed; run team-playbook-evaluate again")
    return report, load_frame(directory, report["artifacts"]["predictions"])
