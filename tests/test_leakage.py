import polars as pl
import pytest
from polars.testing import assert_frame_equal

from nfl_prop_model.data.quarterback_games import build_target_table
from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.features.quarterback import FEATURE_COLUMNS, add_lagged_features


def test_changing_current_and_future_outcomes_cannot_change_pregame_features(sources):
    stats, schedules = sources
    table, _ = build_target_table(stats, schedules)
    original = add_lagged_features(table)
    cutoff = original.filter(pl.col("player_id") == "A")["kickoff_utc"][3]
    changed = table.with_columns(
        pl.when(pl.col("kickoff_utc") >= cutoff)
        .then(9999.0)
        .otherwise(pl.col("target_passing_yards"))
        .alias("target_passing_yards"),
        pl.when(pl.col("kickoff_utc") >= cutoff)
        .then(999.0)
        .otherwise(pl.col("observed_attempts"))
        .alias("observed_attempts"),
    )
    rebuilt = add_lagged_features(changed)
    columns = ["game_id", "player_id", *FEATURE_COLUMNS]
    assert_frame_equal(
        original.filter(pl.col("kickoff_utc") <= cutoff).select(columns),
        rebuilt.filter(pl.col("kickoff_utc") <= cutoff).select(columns),
    )
    assert original["passing_yards_lag1"].to_list() != rebuilt["passing_yards_lag1"].to_list()


def test_appending_future_games_does_not_change_past_features(sources):
    table, _ = build_target_table(*sources)
    cutoff = table["kickoff_utc"][7]
    assert_frame_equal(
        add_lagged_features(table.filter(pl.col("kickoff_utc") <= cutoff)),
        add_lagged_features(table).filter(pl.col("kickoff_utc") <= cutoff),
    )


def test_no_history_is_available_after_prediction_time(sources):
    table, _ = build_target_table(*sources)
    result = add_lagged_features(table)
    assert result.filter(
        pl.col("history_available_at_utc") >= pl.col("prediction_time_utc")
    ).is_empty()
    broken = table.with_columns(pl.col("kickoff_utc").min().alias("prediction_time_utc"))
    with pytest.raises(DataQualityError, match="not available"):
        add_lagged_features(broken)


def test_same_player_same_kickoff_is_rejected(sources):
    table, _ = build_target_table(*sources)
    extra = table.head(1).with_columns(pl.lit("other_game").alias("game_id"))
    with pytest.raises(DataQualityError, match="duplicate"):
        add_lagged_features(pl.concat([table, extra]))
