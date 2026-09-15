import polars as pl
from polars.testing import assert_frame_equal

from nfl_prop_model.data.quarterback_games import build_target_table
from nfl_prop_model.features.quarterback import FEATURE_COLUMNS, add_lagged_features


def test_hand_calculated_rolling_and_season_reset(sources):
    table, _ = build_target_table(*sources)
    result = add_lagged_features(table).filter(pl.col("player_id") == "A")
    assert result["passing_yards_lag1"].to_list() == [None, 100, 200, 300, 400, 500, 600]
    assert result["passing_yards_mean3"].to_list() == [None, 100, 150, 200, 300, 400, 500]
    assert result["passing_yards_mean5"].to_list() == [None, 100, 150, 200, 250, 300, 400]
    assert result["passing_yards_season_mean"].to_list() == [None, 100, 150, 200, 250, None, 600]
    assert result["attempts_mean5"].to_list() == [None, 10, 10.5, 11, 11.5, 12, 13]
    assert result["prior_games_in_sample"].to_list() == list(range(7))
    assert result["prior_season_games_in_sample"].to_list() == [0, 1, 2, 3, 4, 0, 1]


def test_player_isolation_and_cold_start(sources):
    table, _ = build_target_table(*sources)
    result = add_lagged_features(table).filter(pl.col("player_id") == "B")
    assert result["passing_yards_mean5"].to_list() == [None] + [50] * 6
    assert result["prior_games_in_sample"][0] == 0


def test_input_order_does_not_change_features(sources):
    table, _ = build_target_table(*sources)
    assert_frame_equal(add_lagged_features(table), add_lagged_features(table.reverse()))


def test_feature_allowlist_excludes_outcomes_and_diagnostics():
    assert not set(FEATURE_COLUMNS) & {
        "target_passing_yards",
        "observed_attempts",
        "observed_completions",
        "schedule_reported_starter",
        "home_score",
        "away_score",
        "spread_line",
        "total_line",
        "temp",
        "wind",
    }
