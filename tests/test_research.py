from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest
from polars.testing import assert_frame_equal

from nfl_prop_model.cli import main
from nfl_prop_model.data.quarterback_games import build_target_table
from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.storage import read_json, store_frame, write_json
from nfl_prop_model.features.context import (
    OPPONENT_FEATURES,
    SCHEDULE_FEATURES,
    add_context_features,
)
from nfl_prop_model.features.quarterback import FEATURE_COLUMNS, add_lagged_features
from nfl_prop_model.modeling.evaluate import walk_forward_folds
from nfl_prop_model.modeling.research import evaluate_research, probability_diagnostics
from nfl_prop_model.modeling.uncertainty import ResidualCalibration, chronological_calibration_split


@pytest.fixture
def research_sources():
    stats, schedules = [], []
    for season, weeks in ((2022, 12), (2023, 3), (2024, 2)):
        for week in range(1, weeks + 1):
            for home, away in (("A", "B"), ("C", "D")):
                game = f"{season}_{week:02d}_{away}_{home}"
                schedules.append(
                    {
                        "game_id": game,
                        "season": season,
                        "week": week,
                        "game_type": "REG",
                        "gameday": (
                            date(season, 9, 1) + timedelta(days=(week - 1) * 7)
                        ).isoformat(),
                        "gametime": "13:00",
                        "home_team": home,
                        "away_team": away,
                        "home_qb_id": home,
                        "away_qb_id": away,
                        "home_score": 24,
                        "away_score": 17,
                        "location": "Neutral" if home == "C" and week == 2 else "Home",
                    }
                )
                for team, opponent, yards in (
                    (home, away, 100 + 4 * week),
                    (away, home, 240 + 3 * week),
                ):
                    stats.append(
                        {
                            "player_id": team,
                            "player_display_name": f"Player {team}",
                            "position": "QB",
                            "game_id": game,
                            "season": season,
                            "week": week,
                            "season_type": "REG",
                            "team": team,
                            "opponent_team": opponent,
                            "passing_yards": float(yards),
                            "attempts": 25.0,
                            "completions": 15.0,
                        }
                    )
    non_qb = {
        **stats[0],
        "player_id": "RB",
        "player_display_name": "Passer RB",
        "position": "RB",
        "passing_yards": 10.0,
        "attempts": 1.0,
        "completions": 1.0,
    }
    rookie = {
        **next(row for row in stats if row["season"] == 2023),
        "player_id": "Rookie",
        "player_display_name": "Rookie",
        "passing_yards": 0.0,
        "attempts": 0.0,
        "completions": 0.0,
    }
    return pl.DataFrame([*stats, non_qb, rookie]), pl.DataFrame(schedules)


def enriched(sources):
    stats, schedules = sources
    table, _ = build_target_table(stats, schedules)
    return add_context_features(add_lagged_features(table), stats, schedules)[0]


def test_context_uses_previous_team_games_and_all_passers(research_sources):
    table = enriched(research_sources)
    second = table.filter(
        (pl.col("season") == 2022) & (pl.col("week") == 2) & (pl.col("team") == "A")
    )
    assert second[OPPONENT_FEATURES[0]].item() == 114.0  # QB 104 + RB pass 10
    assert second[OPPONENT_FEATURES[1]].item() == 26.0
    assert second["team_rest_days"].item() == 7.0
    first = table.filter((pl.col("season") == 2023) & (pl.col("week") == 1))
    assert first["team_rest_days"].null_count() == first.height
    assert first[OPPONENT_FEATURES[0]].null_count() == 0  # prior-season defense is retained
    assert table.filter(pl.col("player_id") == "Rookie").height == 1
    assert table.filter((pl.col("team") == "C") & (pl.col("week") == 2))["is_neutral_site"].all()


def test_context_does_not_use_current_or_future_passing_results(research_sources):
    stats, schedules = research_sources
    original = enriched(research_sources)
    changed_stats = stats.with_columns(
        pl.when(pl.col("week") >= 2)
        .then(pl.lit(9999.0))
        .otherwise(pl.col("passing_yards"))
        .alias("passing_yards")
    )
    changed = enriched((changed_stats, schedules))
    where = (pl.col("season") == 2022) & (pl.col("week") <= 2)
    columns = ["player_id", "game_id", *SCHEDULE_FEATURES, *OPPONENT_FEATURES]
    assert_frame_equal(
        original.filter(where).select(columns), changed.filter(where).select(columns)
    )


def test_context_rejects_missing_team_totals(research_sources):
    stats, schedules = research_sources
    table, _ = build_target_table(stats, schedules)
    broken = stats.filter(
        ~((pl.col("season") == 2022) & (pl.col("week") == 1) & (pl.col("team") == "B"))
    )
    with pytest.raises(DataQualityError, match="Missing team passing totals"):
        add_context_features(add_lagged_features(table), broken, schedules)


def test_calibration_and_training_are_separate_in_time_and_by_game(research_sources):
    table = enriched(research_sources)
    fold = next(walk_forward_folds(table))
    split = chronological_calibration_split(fold.train, minimum_rows=10)
    assert split.train.height == 24
    assert split.calibration.height == 24
    assert split.train["kickoff_utc"].max() + timedelta(hours=24) < split.cutoff_utc
    assert split.calibration["kickoff_utc"].max() + timedelta(hours=24) < fold.cutoff_utc
    assert set(split.train["game_id"]).isdisjoint(split.calibration["game_id"])
    assert set(split.calibration["game_id"]).isdisjoint(fold.test["game_id"])


def test_conformal_radius_uses_finite_sample_order_statistic():
    calibration = ResidualCalibration(np.arange(1.0, 11.0))
    assert calibration.radius(0.5) == 6.0
    assert calibration.radius(0.8) == 9.0
    assert calibration.radius(0.9) == 10.0
    with pytest.raises(ValueError, match="Too few"):
        calibration.radius(0.99)
    with pytest.raises(ValueError, match="Coverage"):
        calibration.radius(1.0)


def test_residual_tail_is_strict_smoothed_bounded_and_monotone():
    values = np.array([-2.0, 0.0, 2.0])
    calibration = ResidualCalibration(values)
    values[:] = 999
    assert calibration.probability_over(100, 100) == 0.375
    assert calibration.probability_over(100, 90) > calibration.probability_over(100, 100)
    assert 0 < calibration.probability_over(100, 999) < 1
    with pytest.raises(ValueError):
        calibration.probability_over(float("nan"), 100)
    with pytest.raises(ValueError):
        ResidualCalibration(np.array([float("nan")]))


def test_probability_metrics_match_hand_calculation():
    frame = pl.DataFrame(
        {
            "model": ["test"] * 2,
            "line": [200.5] * 2,
            "season": [2023] * 2,
            "probability_over": [0.8, 0.2],
            "outcome_over": [1, 0],
        }
    )
    scores, reliability = probability_diagnostics(frame)
    assert scores["brier"].item() == pytest.approx(0.04)
    assert scores["log_loss"].item() == pytest.approx(-np.log(0.8))
    assert reliability["n"].sum() == 2


def test_test_outcomes_cannot_change_their_predictions_or_uncertainty(research_sources):
    stats, schedules = research_sources
    first = evaluate_research(enriched(research_sources), minimum_calibration_rows=10)
    mutated = stats.with_columns(
        pl.when(pl.col("season") >= 2023)
        .then(pl.col("passing_yards") + 500)
        .otherwise(pl.col("passing_yards"))
        .alias("passing_yards")
    )
    second = evaluate_research(enriched((mutated, schedules)), minimum_calibration_rows=10)
    condition = (pl.col("season") == 2023) & (pl.col("week") == 1)
    for left, right, columns in (
        (first.predictions, second.predictions, ["player_id", "model", "prediction"]),
        (first.intervals, second.intervals, ["player_id", "model", "coverage", "lower", "upper"]),
        (
            first.probabilities,
            second.probabilities,
            ["player_id", "model", "line", "probability_over"],
        ),
    ):
        assert_frame_equal(
            left.filter(condition).select(columns), right.filter(condition).select(columns)
        )
    assert first.folds[0] == second.folds[0]
    assert first.predictions.height == 21 * 6
    assert first.intervals.height == 21 * 6 * 3
    assert first.probabilities.height == 21 * 6 * 4


def test_calibration_targets_do_not_fit_the_models(research_sources):
    table = enriched(research_sources)
    before = evaluate_research(table, minimum_calibration_rows=10)
    changed = table.with_columns(
        pl.when((pl.col("season") == 2022) & (pl.col("week") >= 7))
        .then(pl.col("target_passing_yards") + 500)
        .otherwise(pl.col("target_passing_yards"))
        .alias("target_passing_yards")
    )
    after = evaluate_research(changed, minimum_calibration_rows=10)
    condition = (pl.col("season") == 2023) & (pl.col("week") == 1)
    cols = ["player_id", "model", "prediction"]
    assert_frame_equal(
        before.predictions.filter(condition).select(cols),
        after.predictions.filter(condition).select(cols),
    )
    assert before.folds[0]["tree_gain_importance"] == after.folds[0]["tree_gain_importance"]
    assert before.folds[0]["calibration"] != after.folds[0]["calibration"]


def test_research_cli_reads_offline_snapshot_and_preserves_history(
    tmp_path, research_sources, capsys
):
    stats, schedules = research_sources
    # Enough QB rows to exercise the real 100-row calibration minimum.
    stats = pl.concat(
        [
            stats,
            *[
                stats.filter(pl.col("position") == "QB").with_columns(
                    (pl.col("player_id") + pl.lit(f"-{index}")).alias("player_id")
                )
                for index in range(4)
            ],
        ]
    )
    data = tmp_path / "data"
    raw = data / "raw" / "2022_2023_2024"
    snapshot = {
        "seasons": [2022, 2023, 2024],
        "datasets": {
            "player_stats": store_frame(raw, "player_stats", stats),
            "schedules": store_frame(raw, "schedules", schedules),
        },
    }
    write_json(raw / "manifest.json", snapshot)
    historical = add_lagged_features(build_target_table(stats, schedules)[0])
    processed = data / "processed" / "2022_2023_2024"
    metadata = store_frame(processed, "quarterback_games", historical)
    source = {"table": metadata, "feature_columns": list(FEATURE_COLUMNS), "raw_manifest": snapshot}
    write_json(processed / "manifest.json", source)
    original = (processed / metadata["file"]).read_bytes()
    report_dir = tmp_path / "report"
    args = ["research", "--data-dir", str(data), "--report-dir", str(report_dir)]
    assert main(args) == 0
    report = read_json(report_dir / "research.json")
    assert report["counts"]["evaluated_qb_games"] == 105
    assert report["counts"]["dropped_evaluation_rows"] == 0
    assert report["artifacts"]["predictions"]["rows"] == 105 * 6
    assert report["artifacts"]["intervals"]["rows"] == 105 * 6 * 3
    assert report["artifacts"]["probabilities"]["rows"] == 105 * 6 * 4
    assert report["format_version"] == 2
    assert report["artifacts"]["calibration_residuals"]["rows"] == sum(
        fold["calibration_rows"] * 6 for fold in report["folds"]
    )
    assert "plotly.js" in (report_dir / "calibration.html").read_text(encoding="utf-8")
    assert (processed / metadata["file"]).read_bytes() == original
    changed_snapshot = {**snapshot, "datasets": {**snapshot["datasets"], "extra": {}}}
    write_json(raw / "manifest.json", changed_snapshot)
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 1
    assert "Raw/processed snapshots differ" in capsys.readouterr().err
