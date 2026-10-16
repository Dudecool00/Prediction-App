import polars as pl
import pytest

from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.features.team_epa import HORIZONS
from nfl_prop_model.modeling import team_margin_sensitivity as sensitivity
from nfl_prop_model.modeling.team_scores import consistent_scores


def panel(margins=(20, 21, -20, -21, 0, 7)):
    rows = []
    for game, margin in enumerate(margins):
        for horizon in HORIZONS:
            for index, model in enumerate(sensitivity.replay.MODELS):
                rows.append(
                    {
                        "game_id": str(game),
                        "season": 2025,
                        "week": game + 1,
                        "model": model,
                        "horizon": horizon,
                        "home_score": 30.0,
                        "away_score": 30.0 - margin,
                        "target_home_margin": float(margin),
                        **consistent_scores(30 + index, 28 + index),
                    }
                )
    return pl.DataFrame(rows)


def test_boundary_is_actual_absolute_margin_and_cohorts_are_identical():
    original = panel()
    groups = sensitivity.cohorts(original)
    assert set(groups["actual_margin_under_21"]["game_id"]) == {"0", "2", "4", "5"}
    assert set(groups["actual_margin_21_or_more"]["game_id"]) == {"1", "3"}
    for rows in groups["actual_margin_under_21"].partition_by("model", "horizon"):
        assert rows.height == 4
    assert groups["all_games"].equals(original)


def test_partition_metrics_recombine_to_original_without_double_counting():
    report = sensitivity.analyze(panel())
    metrics = pl.DataFrame(report["metrics"])
    for model in sensitivity.replay.MODELS:
        rows = metrics.filter((pl.col("model") == model) & (pl.col("horizon") == "early_week"))
        all_games = rows.filter(pl.col("subset") == "all_games").row(0, named=True)
        parts = rows.filter(pl.col("subset") != "all_games").to_dicts()
        assert all_games["games"] == 6
        for metric in ("score_mae", "margin_mae", "total_mae"):
            combined = sum(r[metric] * r["games"] for r in parts) / 6
            assert combined == pytest.approx(all_games[metric])


def test_inconsistent_outcomes_or_model_cohort_fail():
    incomplete = panel().filter(
        ~((pl.col("game_id") == "0") & (pl.col("model") == "league_scores"))
    )
    with pytest.raises(DataQualityError, match="identical game cohorts"):
        sensitivity.cohorts(incomplete)
    with pytest.raises(DataQualityError, match="inconsistent"):
        sensitivity.cohorts(
            panel().with_columns(pl.lit(None, dtype=pl.Float64).alias("target_home_margin"))
        )


def test_empty_excluded_group_is_reported_without_bootstrap_failure():
    report = sensitivity.analyze(panel((0, 7, -7)))
    assert report["coverage"][-1]["games"] == 0
    assert not [row for row in report["metrics"] if row["subset"] == "actual_margin_21_or_more"]
