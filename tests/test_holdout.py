import shutil
from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest
from polars.testing import assert_frame_equal
from xgboost import XGBRegressor

from nfl_prop_model.cli import main
from nfl_prop_model.data.quarterback_games import build_target_table
from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.storage import load_frame, read_json
from nfl_prop_model.features.context import add_context_features
from nfl_prop_model.features.quarterback import add_lagged_features
from nfl_prop_model.modeling import candidate, freeze, holdout
from nfl_prop_model.modeling.uncertainty import ResidualCalibration


def synthetic_sources(season, weeks):
    stats, schedules = [], []
    for week in range(1, weeks + 1):
        for pair in range(10):
            home, away = f"T-{pair * 2:02}", f"T-{pair * 2 + 1:02}"
            game_id = f"{season}_{week:02}_{away}_{home}"
            schedules.append(
                {
                    "game_id": game_id,
                    "season": season,
                    "week": week,
                    "game_type": "REG",
                    "gameday": (date(season, 9, 1) + timedelta(weeks=week - 1)).isoformat(),
                    "gametime": "13:00",
                    "home_team": home,
                    "away_team": away,
                    "home_qb_id": f"QB-{pair * 2:02}",
                    "away_qb_id": f"QB-{pair * 2 + 1:02}",
                    "home_score": 24,
                    "away_score": 17,
                    "location": "Neutral" if pair == 1 and week == 2 else "Home",
                }
            )
            for player, team, opponent in ((pair * 2, home, away), (pair * 2 + 1, away, home)):
                stats.append(
                    {
                        "player_id": f"QB-{player:02}",
                        "player_display_name": f"Player {player}",
                        "position": "QB",
                        "season_type": "REG",
                        "game_id": game_id,
                        "season": season,
                        "week": week,
                        "team": team,
                        "opponent_team": opponent,
                        "passing_yards": float(100 + player * 10 + week),
                        "attempts": 25.0,
                        "completions": 15.0,
                    }
                )
        if season == 2025:
            # Keep newcomers, backups, zero attempts and negative yardage in the diagnostic.
            stats.append(
                {
                    **stats[-2],
                    "player_id": "NEW",
                    "player_display_name": "New QB",
                    "passing_yards": -4.0 if week == 2 else 0.0,
                    "attempts": 0.0,
                    "completions": 0.0,
                }
            )
    return pl.DataFrame(stats), pl.DataFrame(schedules)


@pytest.fixture(scope="module")
def development():
    stats, schedules = synthetic_sources(2024, 9)
    targets, _ = build_target_table(stats, schedules)
    features = add_lagged_features(targets)
    return add_context_features(features, stats, schedules)[0]


@pytest.fixture(scope="module")
def frozen(development, tmp_path_factory):
    root = tmp_path_factory.mktemp("frozen")
    prepared = candidate.prepare_candidate(development, root / "prepared", {"fixture": "synthetic"})
    return freeze.freeze_candidate(prepared, root / "frozen")


@pytest.fixture
def inputs():
    return synthetic_sources(2025, 3)


def forbid(*args, **kwargs):
    pytest.fail("Forbidden data access, fitting or repeated scoring")


def test_freeze_preserves_model_calibration_and_precedes_any_access(frozen):
    record = freeze.verify_frozen(frozen, require_current_code=True)
    assert record["production_enabled"] is False
    assert record["protocol"]["access"] == "one_reserved_diagnostic_run"
    assert record["prepared"]["holdout_access"] == "closed"
    assert record["frozen_at_utc"] >= record["prepared"]["created_at_utc"]


def test_unfrozen_or_corrupt_inputs_cannot_open_holdout(frozen, tmp_path, monkeypatch):
    monkeypatch.setattr(holdout, "_fetch_inputs", forbid)
    bundle = tmp_path / "bundle"
    shutil.copytree(frozen, bundle)
    path = next(bundle.glob("freeze-*.json"))
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(DataQualityError, match="checksum"):
        holdout.evaluate_holdout(bundle, tmp_path / "data")
    assert not (tmp_path / "data" / "holdout" / "2025" / "access.json").exists()
    path.unlink()
    with pytest.raises(DataQualityError, match="freeze record"):
        holdout.evaluate_holdout(bundle, tmp_path / "data")


def test_changed_evaluator_blocks_access_before_registry(frozen, tmp_path, monkeypatch):
    monkeypatch.setattr(freeze, "_code_hashes", lambda: {"changed": "source"})
    monkeypatch.setattr(holdout, "_fetch_inputs", forbid)
    with pytest.raises(DataQualityError, match="changed after freeze"):
        holdout.evaluate_holdout(frozen, tmp_path / "data")
    assert not (tmp_path / "data" / "holdout" / "2025" / "access.json").exists()


def test_lags_newcomers_season_reset_and_neutral_schedule(development, inputs):
    features, audit = holdout.build_holdout_features(development, *inputs)
    assert features.height == 63
    assert audit["included_zero_attempt_rows"] == 3
    newcomer = features.filter(pl.col("player_id") == "NEW").sort("week")
    assert newcomer["prior_games_in_sample"].to_list() == [0, 1, 2]
    assert newcomer["target_passing_yards"].to_list() == [0.0, -4.0, 0.0]
    assert features.filter(pl.col("week") == 1)["team_rest_days"].null_count() == 21
    assert features.filter(pl.col("week") == 2)["team_rest_days"].to_list() == [7.0] * 21
    assert features.filter(pl.col("is_neutral_site")).height == 2
    assert features.filter(
        pl.col("history_available_at_utc") >= pl.col("prediction_time_utc")
    ).is_empty()


def test_current_future_outcomes_cannot_change_forecasts(development, frozen, inputs):
    stats, schedules = inputs
    original, _ = holdout.build_holdout_features(development, stats, schedules)
    changed_stats = stats.with_columns(
        pl.when(pl.col("week") >= 2)
        .then(pl.col("passing_yards") + 9999)
        .otherwise(pl.col("passing_yards"))
        .alias("passing_yards")
    )
    changed, _ = holdout.build_holdout_features(development, changed_stats, schedules)
    columns = ["game_id", "player_id", *candidate.FEATURES]
    assert_frame_equal(
        original.filter(pl.col("week") <= 2).select(columns),
        changed.filter(pl.col("week") <= 2).select(columns),
    )
    manifest = freeze.verify_frozen(frozen)["prepared"]
    estimator = XGBRegressor(**manifest["policy"]["parameters"])
    estimator.load_model(frozen / manifest["artifacts"]["model"]["file"])
    pool = ResidualCalibration(
        load_frame(frozen, manifest["artifacts"]["calibration"])["residual"].to_numpy()
    )
    before, after = (holdout.score_holdout(t, estimator, pool) for t in (original, changed))
    for name in ("predictions", "intervals", "probabilities"):
        frame = before[name].filter(pl.col("week") <= 2)
        counterpart = after[name].filter(pl.col("week") <= 2)
        fields = [c for c in frame.columns if c not in ("actual", "outcome_over")]
        assert_frame_equal(frame.select(fields), counterpart.select(fields))
    assert (
        original.filter(pl.col("week") == 3)["passing_yards_lag1"].to_list()
        != changed.filter(pl.col("week") == 3)["passing_yards_lag1"].to_list()
    )


@pytest.mark.parametrize(
    "problem", ["season", "location", "missing_schedule", "unavailable_history"]
)
def test_invalid_sources_fail(development, inputs, problem):
    stats, schedules = inputs
    if problem == "season":
        stats = stats.with_columns(pl.lit(2026).alias("season"))
    elif problem == "location":
        schedules = schedules.with_columns(pl.lit("Unknown").alias("location"))
    elif problem == "missing_schedule":
        schedules = schedules.slice(1)
    else:
        schedules = schedules.with_columns(
            pl.when(pl.col("week") == 2)
            .then(pl.lit("2025-09-02"))
            .otherwise(pl.col("gameday"))
            .alias("gameday")
        )
    with pytest.raises(DataQualityError):
        holdout.build_holdout_features(development, stats, schedules)


def test_hand_calculated_scores_and_residual_probabilities(development, inputs):
    features, _ = holdout.build_holdout_features(development, *inputs)

    class ConstantEstimator:
        def predict(self, matrix):
            return np.full(len(matrix), 200.0)

    pool = ResidualCalibration(np.array([-10.0, 0.0, 10.0] * 4))
    frames = holdout.score_holdout(features, ConstantEstimator(), pool)
    report = holdout.summarize_holdout(frames)
    summary = next(
        r
        for r in report["point_metrics"]
        if r["scope"] == "overall" and r["population"].startswith("all")
    )
    actual = features["target_passing_yards"].to_numpy()
    assert summary["n"] == 63
    assert summary["mae"] == pytest.approx(np.abs(200 - actual).mean())
    assert summary["rmse"] == pytest.approx(np.sqrt(((200 - actual) ** 2).mean()))
    assert summary["bias"] == pytest.approx((200 - actual).mean())
    assert frames["intervals"].filter(pl.col("coverage") == 0.9)["lower"].to_list() == [190.0] * 63
    assert (
        frames["probabilities"].filter(pl.col("line") == 200.5)["probability_over"].to_list()
        == [4.5 / 13] * 63
    )


def test_one_run_reuses_results_and_never_fits(frozen, inputs, tmp_path, monkeypatch):
    stats, schedules = inputs
    monkeypatch.setattr(holdout, "_fetch_inputs", lambda _: (stats, schedules, {"synthetic": True}))
    monkeypatch.setattr(XGBRegressor, "fit", forbid)
    report, reused = holdout.evaluate_holdout(frozen, tmp_path / "data")
    assert not reused
    assert report["counts"]["qb_games"] == report["counts"]["scored_rows"] == 63
    assert report["counts"]["history_eligible_rows"] == 60
    assert report["first_access_started_at_utc"] > report["frozen_at_utc"]
    registry = read_json(tmp_path / "data" / "holdout" / "2025" / "access.json")
    assert registry["status"] == "complete"
    monkeypatch.setattr(holdout, "_fetch_inputs", forbid)
    monkeypatch.setattr(holdout, "score_holdout", forbid)
    again, reused = holdout.evaluate_holdout(frozen, tmp_path / "data")
    assert reused and again == report
    assert (
        main(
            [
                "evaluate-holdout",
                "--frozen",
                str(frozen),
                "--data-dir",
                str(tmp_path / "data"),
                "--report-dir",
                str(tmp_path / "reports"),
            ]
        )
        == 0
    )
    assert (tmp_path / "reports" / "holdout.md").exists()


def test_failed_attempt_is_audited_and_same_freeze_resume_required(
    frozen, inputs, tmp_path, monkeypatch
):
    def failed(_):
        raise ConnectionError("Synthetic retrieval failure")

    monkeypatch.setattr(holdout, "_fetch_inputs", failed)
    with pytest.raises(ConnectionError):
        holdout.evaluate_holdout(frozen, tmp_path / "data")
    registry = read_json(tmp_path / "data" / "holdout" / "2025" / "access.json")
    assert registry["status"] == "failed"
    assert registry["attempts"][0]["error_type"] == "ConnectionError"
    with pytest.raises(DataQualityError, match="audit trail"):
        holdout.evaluate_holdout(frozen, tmp_path / "data")
    stats, schedules = inputs
    monkeypatch.setattr(holdout, "_fetch_inputs", lambda _: (stats, schedules, {"synthetic": True}))
    report, reused = holdout.evaluate_holdout(frozen, tmp_path / "data", resume=True)
    assert not reused and report["counts"]["qb_games"] == 63
    registry = read_json(tmp_path / "data" / "holdout" / "2025" / "access.json")
    assert [a["status"] for a in registry["attempts"]] == ["failed", "complete"]


def test_concurrent_run_lock_prevents_access(frozen, tmp_path, monkeypatch):
    root = tmp_path / "data" / "holdout" / "2025"
    root.mkdir(parents=True)
    (root / "run.lock").write_text("Another writer", encoding="utf-8")
    monkeypatch.setattr(holdout, "_fetch_inputs", forbid)
    with pytest.raises(DataQualityError, match="writer is already active"):
        holdout.evaluate_holdout(frozen, tmp_path / "data")
    assert (root / "run.lock").exists()


def test_changed_runtime_blocks_access(frozen, tmp_path, monkeypatch):
    monkeypatch.setattr(freeze, "_runtime", lambda: {"python": "changed"})
    monkeypatch.setattr(holdout, "_fetch_inputs", forbid)
    with pytest.raises(DataQualityError, match="runtime changed"):
        holdout.evaluate_holdout(frozen, tmp_path / "data")
    assert not (tmp_path / "data" / "holdout" / "2025" / "access.json").exists()


def test_different_freeze_cannot_retest_accessed_season(frozen, inputs, tmp_path, monkeypatch):
    stats, schedules = inputs
    monkeypatch.setattr(holdout, "_fetch_inputs", lambda _: (stats, schedules, {"synthetic": True}))
    holdout.evaluate_holdout(frozen, tmp_path / "data")
    another = freeze.freeze_candidate(frozen, tmp_path / "another-freeze")
    monkeypatch.setattr(holdout, "_fetch_inputs", forbid)
    with pytest.raises(DataQualityError, match="already been accessed"):
        holdout.evaluate_holdout(another, tmp_path / "data")


def test_exclusions_are_counted_without_zero_filling(development, inputs):
    stats, schedules = inputs
    extra = pl.concat(
        [
            stats.head(1).with_columns(
                pl.lit("MISSING").alias("player_id"),
                pl.lit(None).cast(pl.Float64).alias("passing_yards"),
            ),
            stats.head(1).with_columns(
                pl.lit("RB").alias("player_id"), pl.lit("RB").alias("position")
            ),
            stats.head(1).with_columns(
                pl.lit("POST").alias("player_id"), pl.lit("POST").alias("season_type")
            ),
        ]
    )
    features, exclusions = holdout.build_holdout_features(
        development, pl.concat([stats, extra]), schedules
    )
    assert features.height == 63
    assert exclusions["excluded_qb_missing_target"] == 1
    assert exclusions["excluded_non_qb_or_unknown_position"] == 1
    assert exclusions["excluded_qb_non_regular_season"] == 1
