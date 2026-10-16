from copy import deepcopy
from datetime import timedelta

import numpy as np
import polars as pl
import pytest
from test_compact_replay import replay_data as replay_data
from test_team_scores import team_sources as team_sources
from test_team_walkforward import weekly_data as weekly_data

from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.modeling import team_compact_replay as replay
from nfl_prop_model.modeling import team_points_comparison as points


@pytest.fixture
def comparison_data(replay_data):
    features = replay_data[2]
    controls, _ = replay.evaluate_compact(features)
    columns = [c for c in replay.FACTS if c not in {"week_lock_utc", "result_available_at_utc"}]
    columns += list(replay.SCORES) + ["model"]
    return features, controls.select(columns)


def test_neutral_matchup_reversal_and_opponent_defense_effect():
    games = pl.DataFrame(
        {"home_team": ["A", "B", "A"], "away_team": ["B", "A", "C"], "home_field": [0.0, 0.0, 0.0]}
    )
    parameters = {
        "teams": ["A", "B", "C"],
        "coefficients": [4.0, 0.0, 0.0, 0.0, 2.0, -3.0, 3.0],
        "intercept": 22.0,
    }
    values = points.predict_points(games, parameters)
    np.testing.assert_allclose(values[0], values[1][::-1])
    assert values[0, 0] > values[2, 0]  # C's stronger defense reduces A's expected score.
    unseen = pl.DataFrame({"home_team": ["X"], "away_team": ["Y"], "home_field": [0.0]})
    np.testing.assert_allclose(points.predict_points(unseen, parameters), [[22, 22]])


def test_home_edge_is_full_margin_effect_and_neutral_site_has_none():
    games = pl.DataFrame(
        {"home_team": ["A", "A"], "away_team": ["B", "B"], "home_field": [0.0, 1.0]}
    )
    parameters = {"teams": ["A", "B"], "coefficients": [0.0, 0.0, 0.0, 0.0, 4.0], "intercept": 22.0}
    values = points.predict_points(games, parameters)
    assert values[0, 0] - values[0, 1] == 0
    assert values[1, 0] - values[1, 1] == 4


def test_only_eighteen_point_fits_and_saved_controls_preserved(comparison_data, monkeypatch):
    features, controls = comparison_data
    fit = points.fit_points
    calls = []

    def counted(train, lock, **kwargs):
        calls.append(train.height)
        return fit(train, lock, **kwargs)

    monkeypatch.setattr(points, "fit_points", counted)
    panel, folds = points.evaluate(features, controls, (2025,))
    assert len(calls) == 18 and len(folds) == 18
    assert calls[0] == 108
    assert folds[0]["parameters"]["allocated_weights_and_intercepts"] == 10
    assert 1 < folds[0]["parameters"]["effective_degrees_of_freedom"] < 10
    assert panel.filter(pl.col("model").is_in(replay.MODELS)).equals(controls)
    scores = ["game_id", "model", *replay.SCORES]
    assert (
        panel.filter(pl.col("horizon") == "early_week")
        .select(scores)
        .sort("game_id", "model")
        .equals(
            panel.filter(pl.col("horizon") == "pregame_refresh")
            .select(scores)
            .sort("game_id", "model")
        )
    )


def test_future_results_cannot_change_earlier_point_or_blend_forecasts(comparison_data):
    features, controls = comparison_data
    original, _ = points.evaluate(features, controls, (2025,))
    future = (pl.col("season") == 2025) & (pl.col("week") >= 12)
    changed = features.with_columns(
        pl.when(future).then(99.0).otherwise(pl.col("home_score")).alias("home_score")
    )
    changed = changed.with_columns(
        (pl.col("home_score") - pl.col("away_score")).alias("target_home_margin")
    )
    altered_controls = controls.with_columns(
        pl.when(future).then(99.0).otherwise(pl.col("home_score")).alias("home_score")
    )
    altered_controls = altered_controls.with_columns(
        (pl.col("home_score") - pl.col("away_score")).alias("target_home_margin")
    )
    altered, _ = points.evaluate(changed, altered_controls, (2025,))
    columns = ["game_id", "horizon", "model", *replay.SCORES]
    assert (
        original.filter(pl.col("week") <= 12)
        .select(columns)
        .equals(altered.filter(pl.col("week") <= 12).select(columns))
    )


def test_strict_boundary_and_same_week_outcomes_excluded(comparison_data):
    features, _ = comparison_data
    history = features.filter(pl.col("horizon") == "early_week")
    test = history.filter((pl.col("season") == 2025) & (pl.col("week") == 2))
    lock = test["week_lock_utc"][0]
    prior = history.filter((pl.col("season") == 2025) & (pl.col("week") == 1))["game_id"][0]
    changed = history.with_columns(
        pl.when(pl.col("game_id") == prior)
        .then(pl.lit(lock))
        .otherwise(pl.col("result_available_at_utc"))
        .alias("result_available_at_utc")
    )
    train, receipt = points.training_fold(changed, test)
    assert prior not in train["game_id"] and prior in receipt["unavailable_earlier_games"]
    earlier = changed.with_columns(
        pl.when(pl.col("game_id") == prior)
        .then(pl.lit(lock - timedelta(microseconds=1)))
        .otherwise(pl.col("result_available_at_utc"))
        .alias("result_available_at_utc")
    )
    assert prior in points.training_fold(earlier, test)[0]["game_id"]
    with pytest.raises(DataQualityError, match="unavailable"):
        points.fit_points(history, lock)


def test_selection_cannot_read_2025_or_missing_year(comparison_data):
    features, controls = comparison_data
    panel, _ = points.evaluate(features, controls, (2025,))
    with pytest.raises(DataQualityError, match="excludes 2025"):
        points.choose_model(panel, (2025,))
    with pytest.raises(DataQualityError, match="Missing"):
        points.choose_model(panel, (2023,))


def test_saved_coefficients_receipts_and_control_tampering_fail(comparison_data):
    features, controls = comparison_data
    panel, folds = points.evaluate(features, controls, (2025,))
    changed = deepcopy(folds)
    changed[0]["parameters"]["intercept"] += 1
    with pytest.raises(DataQualityError, match="saved fit parameters"):
        points.validate_result(panel, features, controls, changed)
    changed = deepcopy(folds)
    changed[0]["training_sha256"] = "changed"
    with pytest.raises(DataQualityError, match="training data changed"):
        points.validate_result(panel, features, controls, changed)
    broken = panel.with_columns(
        pl.when(pl.col("model") == "compact_epa")
        .then(pl.col("predicted_home_margin") + 1)
        .otherwise(pl.col("predicted_home_margin"))
        .alias("predicted_home_margin")
    )
    with pytest.raises(DataQualityError, match="contract|controls changed"):
        points.validate_result(broken, features, controls, folds)


def test_selection_guards_retain_epa_or_accept_genuine_development_gain(comparison_data):
    features, controls = comparison_data
    original, _ = points.evaluate(features, controls, (2025,))
    dev = original.with_columns(pl.lit(2023).alias("season"))
    choice = points.choose_model(dev, (2023,))
    assert choice["2025_outcomes_used"] is False
    # Construct perfect candidate scores on an explicitly synthetic development panel.
    ideal = dev.with_columns(
        pl.when(pl.col("model") == points.POINTS)
        .then(pl.col("home_score"))
        .otherwise(pl.col("predicted_home_score"))
        .alias("predicted_home_score"),
        pl.when(pl.col("model") == points.POINTS)
        .then(pl.col("away_score"))
        .otherwise(pl.col("predicted_away_score"))
        .alias("predicted_away_score"),
        pl.when(pl.col("model") == points.POINTS)
        .then(pl.col("target_home_margin"))
        .otherwise(pl.col("predicted_home_margin"))
        .alias("predicted_home_margin"),
    )
    ideal = ideal.with_columns(
        (-pl.col("predicted_home_margin")).alias("predicted_away_margin"),
        (pl.col("predicted_home_score") + pl.col("predicted_away_score")).alias("predicted_total"),
    )
    assert points.choose_model(ideal, (2023,))["model"] == points.POINTS


def test_held_year_outcomes_cannot_change_development_choice(comparison_data):
    features, controls = comparison_data
    panel, _ = points.evaluate(features, controls, (2025,))
    development = panel.with_columns(pl.lit(2023).alias("season"))
    original = points.choose_model(development, (2023,))
    future = panel.with_columns(pl.lit(999.0).alias("home_score"))
    assert (
        points.choose_model(pl.concat([development, future], how="vertical_relaxed"), (2023,))
        == original
    )


def test_freeze_precedes_any_2025_candidate_fit(tmp_path, comparison_data, monkeypatch):
    features, controls = comparison_data
    panel, _ = points.evaluate(features, controls, (2025,))
    dev = pl.concat(
        [
            panel.with_columns(
                pl.lit(year).alias("season"), pl.col("game_id").str.replace("^2025_", f"{year}_")
            )
            for year in (2023, 2024)
        ]
    )
    monkeypatch.setattr(points, "load_inputs", lambda _: (features, controls, {}))

    def evaluate(_, __, seasons):
        if seasons == (2023, 2024):
            assert list(tmp_path.glob("processed/team_points_comparison/*/protocol.json"))
            return dev, []
        assert list(tmp_path.glob("processed/team_points_comparison/*/frozen_2025.json"))
        raise DataQualityError("stop after verifying freeze")

    monkeypatch.setattr(points, "evaluate", evaluate)
    with pytest.raises(DataQualityError, match="verifying freeze"):
        points.save_comparison(tmp_path, tmp_path / "reports")


def test_outcome_breakdowns_keep_all_methods_on_the_same_games():
    from test_margin_sensitivity import panel

    rows = points.outcome_metrics(panel())
    for row in rows:
        assert row["games"] == (4 if row["actual_margin_group"] == "under_21" else 2)
    assert {row["model"] for row in rows} == set(replay.MODELS)
