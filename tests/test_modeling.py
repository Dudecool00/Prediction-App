from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest
from polars.testing import assert_frame_equal

from nfl_prop_model.cli import main
from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.storage import read_json, store_frame, write_json
from nfl_prop_model.features.quarterback import FEATURE_COLUMNS, add_lagged_features
from nfl_prop_model.modeling.baselines import feature_matrix, ridge_pipeline, rolling_predictions
from nfl_prop_model.modeling.evaluate import evaluate, point_metrics, walk_forward_folds


@pytest.fixture
def model_table():
    rows = []
    for season in (2022, 2023, 2024):
        for week in (1, 2, 3):
            kickoff = datetime(season, 9, 1, 17, tzinfo=UTC) + timedelta(days=(week - 1) * 7)
            for player, offset in (("A", 0), ("B", 100)):
                rows.append(
                    {
                        "player_id": player,
                        "game_id": f"{season}-{week}",
                        "season": season,
                        "week": week,
                        "kickoff_utc": kickoff,
                        "prediction_time_utc": kickoff - timedelta(hours=1),
                        "target_passing_yards": float(week * 50 + offset),
                        "observed_attempts": float(week * 5 + offset / 10),
                        "schedule_reported_starter": player == "A",
                    }
                )
    rookie = {
        **rows[6],
        "player_id": "Rookie",
        "target_passing_yards": 0.0,
        "observed_attempts": 0.0,
        "schedule_reported_starter": False,
    }
    return add_lagged_features(pl.DataFrame([*rows, rookie]))


def test_weekly_folds_keep_games_together_and_only_use_available_results(model_table):
    folds = list(walk_forward_folds(model_table))
    assert len(folds) == 6
    assert sum(fold.test.height for fold in folds) == 13
    for fold in folds:
        assert (fold.train["kickoff_utc"].max() + timedelta(hours=24)) < fold.cutoff_utc
        assert fold.cutoff_utc <= fold.test["prediction_time_utc"].min()
        assert set(fold.train["game_id"]).isdisjoint(fold.test["game_id"])
    assert folds[0].train.height == 6
    assert folds[0].test.height == 3  # includes the zero-attempt newcomer


def test_result_at_cutoff_is_not_available(model_table):
    cutoff = list(walk_forward_folds(model_table))[0].cutoff_utc
    extra = model_table.head(1).with_columns(
        pl.lit("LateResult").alias("player_id"),
        pl.lit("2022-late").alias("game_id"),
        pl.lit(cutoff - timedelta(hours=24)).alias("kickoff_utc"),
        pl.lit(cutoff - timedelta(hours=25)).alias("prediction_time_utc"),
    )
    first = list(walk_forward_folds(pl.concat([model_table, extra])))[0]
    assert "LateResult" not in first.train["player_id"].to_list()


def test_rolling_fallbacks_are_explicit():
    test = pl.DataFrame(
        {
            "passing_yards_mean5": [None, 120.0, 160.0],
            "passing_yards_season_mean": [None, None, 180.0],
        }
    )
    actual = rolling_predictions(test, 90.0)
    assert actual["prior_five_mean"].to_list() == [90.0, 120.0, 160.0]
    assert actual["season_to_date_mean"].to_list() == [90.0, 120.0, 180.0]


def test_ridge_preprocessing_learns_only_from_training_and_retains_empty_columns(model_table):
    fold = list(walk_forward_folds(model_table))[0]
    train = fold.train.with_columns(pl.lit(None, dtype=pl.Float64).alias("passing_yards_lag1"))
    model = ridge_pipeline()
    matrix = feature_matrix(train)
    model.fit(matrix, train["target_passing_yards"].to_numpy())
    statistics = model.named_steps["imputer"].statistics_.copy()
    assert statistics[0] == 0.0
    assert statistics[1] == np.nanmedian(matrix[:, 1])
    extremes = fold.test.with_columns(pl.lit(1_000_000.0).alias("passing_yards_mean3"))
    assert np.isfinite(model.predict(feature_matrix(extremes))).all()
    np.testing.assert_array_equal(model.named_steps["imputer"].statistics_, statistics)


def test_current_and_future_outcomes_cannot_change_earlier_forecasts(model_table):
    before, _, before_audit = evaluate(model_table)
    mutated = model_table.with_columns(
        pl.when(pl.col("season") >= 2023)
        .then(pl.col("target_passing_yards") + 10000)
        .otherwise(pl.col("target_passing_yards"))
        .alias("target_passing_yards"),
        pl.when(pl.col("season") >= 2023)
        .then(pl.col("observed_attempts") + 1000)
        .otherwise(pl.col("observed_attempts"))
        .alias("observed_attempts"),
    )
    after, _, after_audit = evaluate(add_lagged_features(mutated))
    first_week = (pl.col("season") == 2023) & (pl.col("week") == 1)
    columns = ["player_id", "model", "prediction"]
    assert_frame_equal(
        before.filter(first_week).select(columns), after.filter(first_week).select(columns)
    )
    assert before_audit[0] == after_audit[0]
    assert before_audit[1]["training_target_mean"] != after_audit[1]["training_target_mean"]


def test_diagnostic_columns_do_not_enter_models_and_input_order_is_irrelevant(model_table):
    expected, _, _ = evaluate(model_table)
    changed = model_table.with_columns(
        pl.lit(999999.0).alias("observed_attempts"),
        pl.lit(True).alias("schedule_reported_starter"),
        pl.lit(999999.0).alias("unapproved_future_feature"),
    ).reverse()
    actual, _, _ = evaluate(changed)
    assert_frame_equal(expected, actual)


@pytest.mark.parametrize("problem", ["holdout", "duplicates", "late_history", "missing_target"])
def test_invalid_model_input_fails(model_table, problem):
    if problem == "holdout":
        broken = model_table.with_columns(pl.lit(2025).alias("season"))
    elif problem == "duplicates":
        broken = pl.concat([model_table, model_table.head(1)])
    elif problem == "late_history":
        broken = model_table.with_columns(
            pl.col("prediction_time_utc").alias("history_available_at_utc")
        )
    else:
        broken = model_table.with_columns(pl.lit(None).alias("target_passing_yards"))
    with pytest.raises(DataQualityError):
        evaluate(broken)


def test_metrics_match_hand_calculation():
    forecasts = pl.DataFrame(
        {
            "model": ["example"] * 2,
            "season": [2023] * 2,
            "history_bucket": ["5+ prior games"] * 2,
            "actual": [100.0, 200.0],
            "prediction": [110.0, 170.0],
        }
    )
    overall = point_metrics(forecasts).filter(pl.col("scope") == "overall").row(0, named=True)
    assert overall["n"] == 2
    assert overall["mae"] == 20
    assert overall["rmse"] == pytest.approx(np.sqrt(500))
    assert overall["bias"] == -10


def test_evaluation_cli_reproduces_predictions_offline_without_editing_input(tmp_path, model_table):
    data = tmp_path / "data"
    source = data / "processed" / "2022_2023_2024"
    metadata = store_frame(source, "quarterback_games", model_table)
    write_json(
        source / "manifest.json", {"table": metadata, "feature_columns": list(FEATURE_COLUMNS)}
    )
    source_bytes = (source / metadata["file"]).read_bytes()
    report_dir = tmp_path / "report"
    args = ["evaluate", "--data-dir", str(data), "--report-dir", str(report_dir)]
    assert main(args) == 0
    first = read_json(report_dir / "evaluation.json")
    assert first["counts"]["evaluated_qb_games"] == 13
    assert first["counts"]["dropped_evaluation_rows"] == 0
    assert first["predictions"]["rows"] == 39
    assert main(args) == 0
    assert first["predictions"] == read_json(report_dir / "evaluation.json")["predictions"]
    assert (source / metadata["file"]).read_bytes() == source_bytes
