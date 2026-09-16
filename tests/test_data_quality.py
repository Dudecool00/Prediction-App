from datetime import UTC, datetime

import polars as pl
import pytest

from nfl_prop_model.data.quarterback_games import build_target_table
from nfl_prop_model.data.schemas import DataQualityError


def test_duplicates_are_rejected_instead_of_deduplicated(sources):
    stats, schedules = sources
    with pytest.raises(DataQualityError, match="duplicate"):
        build_target_table(pl.concat([stats, stats.head(1)]), schedules)
    with pytest.raises(DataQualityError, match="duplicate"):
        build_target_table(stats, pl.concat([schedules, schedules.head(1)]))


def test_null_qb_identity_fails_but_unknown_position_is_counted(sources):
    stats, schedules = sources
    null_qb = stats.with_columns(pl.lit(None, dtype=pl.String).alias("player_id"))
    with pytest.raises(DataQualityError, match="null keys"):
        build_target_table(null_qb, schedules)
    unknown = stats.head(1).with_columns(
        pl.lit(None, dtype=pl.String).alias("player_id"),
        pl.lit(None, dtype=pl.String).alias("position"),
    )
    table, counts = build_target_table(pl.concat([stats, unknown]), schedules)
    assert table.height == stats.height
    assert counts["source_unknown_position_rows"] == 1
    assert counts["source_player_null_key_rows"] == 1
    assert counts["excluded_non_qb_or_unknown_position"] == 1


@pytest.mark.parametrize("column", ["game_id", "passing_yards", "team"])
def test_missing_required_columns_fail_with_a_clear_error(sources, column):
    stats, schedules = sources
    with pytest.raises(DataQualityError, match="missing required columns"):
        build_target_table(stats.drop(column), schedules)


def test_nonnumeric_and_nonfinite_targets_fail(sources):
    stats, schedules = sources
    with pytest.raises(DataQualityError, match="numeric"):
        build_target_table(stats.with_columns(pl.col("passing_yards").cast(pl.String)), schedules)
    for value in (float("nan"), float("inf")):
        with pytest.raises(DataQualityError, match="non-finite"):
            build_target_table(stats.with_columns(pl.lit(value).alias("passing_yards")), schedules)


def test_unmatched_games_and_inconsistent_matchups_fail(sources):
    stats, schedules = sources
    with pytest.raises(DataQualityError, match="missing from the schedules"):
        build_target_table(stats, schedules.slice(1))
    with pytest.raises(DataQualityError, match="mismatch"):
        build_target_table(stats.with_columns(pl.lit("WRONG").alias("opponent_team")), schedules)
    with pytest.raises(DataQualityError, match="mismatch"):
        build_target_table(stats.with_columns((pl.col("week") + 1).alias("week")), schedules)


def test_missing_target_and_incomplete_game_are_counted(sources):
    stats, schedules = sources
    first_game = schedules["game_id"][0]
    incomplete = schedules.with_columns(
        pl.when(pl.col("game_id") == first_game)
        .then(None)
        .otherwise(pl.col("home_score"))
        .alias("home_score")
    )
    table, counts = build_target_table(stats, incomplete)
    assert table.height == stats.height - 2
    assert counts["excluded_qb_uncompleted_games"] == 2
    missing = stats.with_columns(
        pl.when((pl.col("game_id") == first_game) & (pl.col("player_id") == "A"))
        .then(None)
        .otherwise(pl.col("passing_yards"))
        .alias("passing_yards")
    )
    table, counts = build_target_table(missing, schedules)
    assert table.height == stats.height - 1
    assert counts["excluded_qb_missing_target"] == 1
    assert len(counts["scheduled_starters_without_target_rows"]) == 1


def test_zero_attempts_backup_and_low_yards_are_retained(sources):
    stats, schedules = sources
    first_game = schedules["game_id"][0]
    low_opportunity = stats.with_columns(
        *[
            pl.when(pl.col("game_id") == first_game).then(0).otherwise(pl.col(column)).alias(column)
            for column in ("attempts", "completions", "passing_yards")
        ]
    )
    # A schedule discrepancy is an audit signal, never a reason to remove a QB.
    mismatched_starter = schedules.with_columns(pl.lit("OTHER").alias("home_qb_id"))
    table, counts = build_target_table(low_opportunity, mismatched_starter)
    assert table.height == stats.height
    assert counts["included_zero_attempt_rows"] == 2
    assert counts["included_recorded_nonstarters"] == schedules.height


def test_postseason_is_explicitly_excluded(sources):
    stats, schedules = sources
    extra = stats.head(1).with_columns(
        pl.lit("POST").alias("season_type"), pl.lit("post_game").alias("game_id")
    )
    table, counts = build_target_table(pl.concat([stats, extra]), schedules)
    assert table.height == stats.height
    assert counts["excluded_qb_non_regular_season"] == 1


def test_timezone_and_pregame_cutoff_respect_daylight_saving(sources):
    table, _ = build_target_table(*sources)
    winter = table.filter(pl.col("season") == 2023).row(0, named=True)
    summer = table.filter(pl.col("season") == 2024).row(0, named=True)
    assert winter["kickoff_utc"] == datetime(2023, 11, 5, 18, tzinfo=UTC)
    assert summer["kickoff_utc"] == datetime(2024, 9, 8, 17, tzinfo=UTC)
    assert (winter["kickoff_utc"] - winter["prediction_time_utc"]).total_seconds() == 3600


def test_missing_kickoff_is_rejected(sources):
    stats, schedules = sources
    with pytest.raises(DataQualityError, match="kickoff"):
        build_target_table(stats, schedules.with_columns(pl.lit("TBD").alias("gametime")))


def test_invalid_opportunity_statistics_are_rejected(sources):
    stats, schedules = sources
    with pytest.raises(DataQualityError, match="attempts/completions"):
        build_target_table(stats.with_columns(pl.lit(-1).alias("attempts")), schedules)
