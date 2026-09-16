from datetime import UTC, datetime

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from nfl_prop_model.data.current_qbs import cross_reference, latest_quarterbacks
from nfl_prop_model.data.schemas import DataQualityError


def charts():
    return pl.DataFrame(
        {
            "dt": [
                "2026-09-12T12:00:00Z",
                "2026-09-14T12:00:00Z",
                "2026-09-14T12:00:00Z",
                "2026-09-16T12:00:00Z",
            ],
            "team": ["A"] * 4,
            "player_name": ["Departed", "Current", "Newcomer", "Future"],
            "espn_id": ["1", "2", "3", "4"],
            "gsis_id": ["old", "current", None, "future"],
            "pos_abb": ["QB"] * 4,
            "pos_rank": [1, 1, 2, 1],
        }
    )


def test_latest_team_snapshot_removes_departed_players_and_excludes_future():
    latest = latest_quarterbacks(charts(), datetime(2026, 9, 15, tzinfo=UTC), expected_teams={"A"})
    assert latest["player_name"].to_list() == ["Current", "Newcomer"]
    assert latest["gsis_id"].null_count() == 1


def test_partial_team_coverage_or_missing_current_qb_fails():
    with pytest.raises(DataQualityError, match="coverage"):
        latest_quarterbacks(charts(), datetime(2026, 9, 15, tzinfo=UTC), expected_teams={"A", "B"})
    broken = charts().with_columns(
        pl.when(pl.col("dt") == "2026-09-14T12:00:00Z")
        .then(pl.lit("RB"))
        .otherwise(pl.col("pos_abb"))
        .alias("pos_abb")
    )
    with pytest.raises(DataQualityError, match="no QB"):
        latest_quarterbacks(broken, datetime(2026, 9, 15, tzinfo=UTC), expected_teams={"A"})


def test_cross_reference_preserves_history_and_does_not_infer_retirement():
    history = pl.DataFrame(
        {
            "player_id": ["old", "current", "old"],
            "player_display_name": ["Departed", "Current", "Departed"],
            "kickoff_utc": [
                datetime(2023, 1, 1, tzinfo=UTC),
                datetime(2023, 1, 1, tzinfo=UTC),
                datetime(2024, 1, 1, tzinfo=UTC),
            ],
        }
    )
    original = history.clone()
    current = latest_quarterbacks(charts(), datetime(2026, 9, 15, tzinfo=UTC), expected_teams={"A"})
    matched, candidates, summary = cross_reference(history, current)
    assert_frame_equal(history, original)
    assert summary["historical_rows_deleted"] == 0
    assert summary["historical_rows_discarded_if_filtered"] == 2
    assert summary["current_qbs_missing_gsis_mapping"] == 1
    assert "retired" not in matched["chart_match_status"].to_list()
    assert "missing_gsis_mapping" in candidates["history_match_status"].to_list()


def test_ambiguous_identity_is_rejected():
    duplicate = charts().with_columns(pl.lit("same_id").alias("espn_id"))
    with pytest.raises(DataQualityError, match="duplicate"):
        latest_quarterbacks(duplicate, datetime(2026, 9, 15, tzinfo=UTC), expected_teams={"A"})


def test_unmapped_and_new_players_stay_in_current_candidates():
    depth = charts()
    newcomers = pl.DataFrame(
        {
            "dt": ["2026-09-14T12:00:00Z"] * 2,
            "team": ["A", "A"],
            "player_name": ["Unmapped", "Rookie"],
            "espn_id": ["5", "6"],
            "gsis_id": [None, "rookie"],
            "pos_abb": ["QB", "QB"],
            "pos_rank": [3, 4],
        }
    )
    current = latest_quarterbacks(
        pl.concat([depth, newcomers]), datetime(2026, 9, 15, tzinfo=UTC), expected_teams={"A"}
    )
    history = pl.DataFrame(
        {
            "player_id": ["current"],
            "player_display_name": ["Current"],
            "kickoff_utc": [datetime(2024, 1, 1, tzinfo=UTC)],
        }
    )
    _, candidates, summary = cross_reference(history, current)
    assert candidates.height == 4
    assert summary["current_qbs_without_sample_history"] == 1
    assert summary["current_qbs_missing_gsis_mapping"] == 2
    assert summary["historical_qbs_matched"] == 1
    assert candidates.filter(pl.col("gsis_id") == "rookie")["history_match_status"].item() == (
        "no_2022_2024_sample_history"
    )
