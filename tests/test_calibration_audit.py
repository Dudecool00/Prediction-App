import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from polars.testing import assert_frame_equal

from nfl_prop_model.cli import main
from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.storage import store_frame, write_json
from nfl_prop_model.features.quarterback import FEATURE_COLUMNS
from nfl_prop_model.modeling.calibration_audit import (
    BUCKETS,
    MODELS,
    build_calibration_audit,
    load_calibration_audit,
    summarize_intervals,
)
from nfl_prop_model.modeling.policy import candidate_policy, history_policy_status, policy_hash
from nfl_prop_model.modeling.research import (
    COVERAGES,
    DIAGNOSTIC_THRESHOLDS,
    TREE_FEATURES,
    TREE_PARAMETERS,
)


@pytest.fixture
def calibration_inputs():
    train_cutoff = datetime(2022, 10, 1, tzinfo=UTC)
    kickoff = datetime(2023, 9, 10, 17, tzinfo=UTC)
    cutoff = kickoff - timedelta(hours=1)
    features, points, residuals = [], [], []

    def feature(player, game, time, prior, actual, season, week):
        return {
            **dict.fromkeys(FEATURE_COLUMNS, 200.0),
            "player_id": player,
            "game_id": game,
            "season": season,
            "week": week,
            "kickoff_utc": time,
            "prediction_time_utc": time - timedelta(hours=1),
            "history_available_at_utc": time - timedelta(days=2) if prior else None,
            "prior_games_in_sample": prior,
            "prior_season_games_in_sample": 0,
            "target_passing_yards": actual,
        }

    for i in range(100):
        time = train_cutoff + timedelta(days=2, hours=3 * (i // 2))
        prior = 0 if i < 5 else 2 if i < 25 else 5
        item = feature(f"cal-QB-{i % 15}", f"cal-{i // 2}", time, prior, 200.0 + i - 50, 2022, 7)
        features.append(item)
        for model in sorted(MODELS):
            residuals.append(
                {
                    key: item[key]
                    for key in ("player_id", "game_id", "kickoff_utc", "prediction_time_utc")
                }
                | {
                    "model": model,
                    "fold": "2023-W01",
                    "training_cutoff_utc": train_cutoff,
                    "evaluation_cutoff_utc": cutoff,
                    "residual": float(i - 50),
                }
            )
    for i in range(6):
        prior = (0, 2, 5)[i // 2]
        item = feature(f"test-QB-{i}", f"test-{i // 2}", kickoff, prior, 244.0 + i % 2, 2023, 1)
        features.append(item)
        for model in sorted(MODELS):
            points.append(
                {
                    key: item[key]
                    for key in (
                        "player_id",
                        "game_id",
                        "season",
                        "week",
                        "kickoff_utc",
                        "prediction_time_utc",
                        "prior_games_in_sample",
                    )
                }
                | {
                    "model": model,
                    "fold": "2023-W01",
                    "training_cutoff_utc": train_cutoff,
                    "evaluation_cutoff_utc": cutoff,
                    "prediction": 200.0,
                    "actual": item["target_passing_yards"],
                    "history_bucket": BUCKETS[i // 2],
                }
            )
    frames = {
        "features": pl.DataFrame(features),
        "predictions": pl.DataFrame(points),
        "calibration_residuals": pl.DataFrame(residuals),
    }
    manifest = {
        "format_version": 2,
        "generated_at_utc": "2026-10-01T00:00:00+00:00",
        "research_source_sha256": "synthetic-source",
        "historical_source": {"sha256": "synthetic-history"},
        "method": {
            "reserved_season": 2025,
            "calibration_weeks": 6,
            "minimum_calibration_rows": 100,
            "tree_parameters": dict(TREE_PARAMETERS),
            "tree_feature_groups": {key: list(value) for key, value in TREE_FEATURES.items()},
            "nominal_coverages": list(COVERAGES),
            "diagnostic_thresholds": list(DIAGNOSTIC_THRESHOLDS),
        },
        "folds": [
            {
                "fold": "2023-W01",
                "calibration_rows": 100,
                "test_rows": 6,
                "training_cutoff_utc": train_cutoff.isoformat(),
                "evaluation_cutoff_utc": cutoff.isoformat(),
            }
        ],
    }
    return frames, manifest


def save_inputs(tmp_path, frames, manifest):
    directory = tmp_path / "processed" / "research_2022_2024"
    manifest = deepcopy(manifest)
    manifest["artifacts"] = {
        key: store_frame(directory, key, value) for key, value in frames.items()
    }
    write_json(directory / "manifest.json", manifest)
    return directory, manifest


def test_hand_calculated_coverage_support_scores_and_no_input_mutation(calibration_inputs):
    frames, manifest = calibration_inputs
    original = {key: value.clone() for key, value in frames.items()}
    report = build_calibration_audit(frames, manifest)
    assert report["counts"] == {"evaluated_qb_games": 6, "models": 6, "weekly_folds": 1}
    rows = [
        r
        for r in report["interval_metrics"]
        if r["model"] == "xgb_schedule" and r["scope"] == "overall" and r["coverage"] == 0.9
    ]
    assert len(rows) == 3
    for r in rows:
        assert r["n"] == 2 and r["distinct_players"] == 2
        assert r["distinct_games"] == 1 and r["distinct_weeks"] == 1
        assert r["pooled_coverage"] == 1 and r["pooled_mean_width"] == 90
        if r["history_bucket"] != BUCKETS[2]:
            assert r["matched_available_n"] == 0 and r["matched_coverage"] is None
            assert r["paired_pooled_coverage"] is None
        else:
            assert r["matched_available_n"] == 2 and r["matched_coverage"] == 0
            assert r["paired_pooled_coverage"] == 1 and r["matched_mean_width"] == 86
    support = [r for r in report["calibration_support"] if r["model"] == "xgb_schedule"]
    assert [r["group_n"] for r in support] == [5, 20, 75]
    p = 49.5 / 101
    score = next(
        r
        for r in report["probability_scores"]
        if r["model"] == "xgb_schedule" and r["line"] == 200.5
    )
    assert score["brier"] == pytest.approx((1 - p) ** 2)
    assert score["log_loss"] == pytest.approx(-np.log(p))
    for key in frames:
        assert_frame_equal(frames[key], original[key])


def test_evaluation_outcomes_change_scores_but_not_calibration_or_policy(calibration_inputs):
    frames, manifest = calibration_inputs
    before = build_calibration_audit(frames, manifest)
    frames["predictions"] = frames["predictions"].with_columns(pl.col("actual") + 500)
    frames["features"] = frames["features"].with_columns(
        pl.when(pl.col("season") == 2023)
        .then(pl.col("target_passing_yards") + 500)
        .otherwise(pl.col("target_passing_yards"))
        .alias("target_passing_yards")
    )
    after = build_calibration_audit(frames, manifest)
    assert before["policy"] == after["policy"] and before["policy_sha256"] == after["policy_sha256"]
    assert before["calibration_support"] == after["calibration_support"]
    assert before["interval_metrics"] != after["interval_metrics"]
    for a, b in zip(before["reliability"], after["reliability"], strict=True):
        assert a["mean_probability"] == b["mean_probability"]


def test_partial_matched_availability_uses_the_same_pooled_subset():
    frame = pl.DataFrame(
        {
            "model": ["xgb_schedule"] * 4,
            "history_bucket": [BUCKETS[1]] * 4,
            "coverage": [0.9] * 4,
            "season": [2023] * 4,
            "player_id": ["A", "B", "A", "B"],
            "game_id": ["g1", "g1", "g2", "g2"],
            "fold": ["w1", "w1", "w2", "w2"],
            "pooled_hit": [True, True, False, False],
            "pooled_width": [100.0] * 4,
            "matched_hit": [True, False, None, None],
            "matched_width": [120.0, 120.0, None, None],
        }
    )
    result = summarize_intervals(frame).filter(pl.col("scope") == "overall").row(0, named=True)
    assert result["n"] == 4 and result["matched_available_n"] == 2
    assert result["pooled_coverage"] == result["matched_coverage"] == 0.5
    assert result["paired_pooled_coverage"] == 1.0
    assert result["matched_distinct_players"] == 2
    assert result["matched_distinct_games"] == result["matched_distinct_weeks"] == 1
    assert result["distinct_players"] == result["distinct_games"] == result["distinct_weeks"] == 2


def test_source_coherent_late_calibration_result_is_rejected(calibration_inputs):
    frames, manifest = calibration_inputs
    cutoff = datetime.fromisoformat(manifest["folds"][0]["evaluation_cutoff_utc"])
    late = cutoff - timedelta(hours=23)
    for key in ("features", "calibration_residuals"):
        frames[key] = frames[key].with_columns(
            pl.when(pl.col("game_id") == "cal-0")
            .then(pl.lit(late))
            .otherwise(pl.col("kickoff_utc"))
            .alias("kickoff_utc"),
            pl.when(pl.col("game_id") == "cal-0")
            .then(pl.lit(late - timedelta(hours=1)))
            .otherwise(pl.col("prediction_time_utc"))
            .alias("prediction_time_utc"),
        )
    with pytest.raises(DataQualityError, match="unavailable results"):
        build_calibration_audit(frames, manifest)


def test_equal_sized_but_different_model_calibration_cohorts_fail(calibration_inputs):
    frames, manifest = calibration_inputs
    alternative = (
        frames["features"]
        .head(1)
        .with_columns(
            pl.lit("alternative-QB").alias("player_id"), pl.lit("alternative-game").alias("game_id")
        )
    )
    frames["features"] = pl.concat([frames["features"], alternative])
    condition = (
        (pl.col("model") == "ridge")
        & (pl.col("game_id") == "cal-0")
        & (pl.col("player_id") == "cal-QB-0")
    )
    frames["calibration_residuals"] = frames["calibration_residuals"].with_columns(
        pl.when(condition)
        .then(pl.lit("alternative-QB"))
        .otherwise(pl.col("player_id"))
        .alias("player_id"),
        pl.when(condition)
        .then(pl.lit("alternative-game"))
        .otherwise(pl.col("game_id"))
        .alias("game_id"),
    )
    with pytest.raises(DataQualityError, match="different calibration cohorts"):
        build_calibration_audit(frames, manifest)


@pytest.mark.parametrize(
    "problem",
    [
        "holdout",
        "duplicate",
        "missing_model",
        "missing_feature",
        "future_result",
        "overlap",
        "fold_count",
        "cutoff",
        "wrong_protocol",
        "nonfinite",
        "fractional_history",
        "cohort_mismatch",
    ],
)
def test_invalid_or_leaking_saved_inputs_fail(calibration_inputs, problem):
    frames, manifest = calibration_inputs
    if problem == "holdout":
        frames["features"] = frames["features"].with_columns(pl.lit(2025).alias("season"))
    elif problem == "duplicate":
        frames["predictions"] = pl.concat([frames["predictions"], frames["predictions"].head(1)])
    elif problem == "missing_model":
        frames["predictions"] = frames["predictions"].filter(pl.col("model") != "ridge")
    elif problem == "missing_feature":
        frames["features"] = frames["features"].filter(pl.col("player_id") != "cal-QB-0")
    elif problem == "future_result":
        frames["calibration_residuals"] = frames["calibration_residuals"].with_columns(
            pl.col("evaluation_cutoff_utc").alias("kickoff_utc")
        )
    elif problem == "overlap":
        frames["calibration_residuals"] = frames["calibration_residuals"].with_columns(
            pl.lit("test-0").alias("game_id")
        )
    elif problem == "fold_count":
        manifest["folds"][0]["calibration_rows"] = 101
    elif problem == "cutoff":
        manifest["folds"][0]["evaluation_cutoff_utc"] = "2023-09-10T17:00:00+00:00"
    elif problem == "wrong_protocol":
        manifest["method"]["tree_parameters"]["max_depth"] = 3
    elif problem == "nonfinite":
        frames["calibration_residuals"] = frames["calibration_residuals"].with_columns(
            pl.lit(float("nan")).alias("residual")
        )
    elif problem == "fractional_history":
        frames["features"] = frames["features"].with_columns(
            pl.lit(1.5).alias("prior_games_in_sample")
        )
    else:
        frames["calibration_residuals"] = frames["calibration_residuals"].filter(
            ~((pl.col("model") == "ridge") & (pl.col("game_id") == "cal-0"))
        )
    with pytest.raises(DataQualityError):
        build_calibration_audit(frames, manifest)


def test_offline_cli_preserves_artifacts_and_rejects_tampering(
    tmp_path, calibration_inputs, monkeypatch
):
    frames, manifest = calibration_inputs
    directory, manifest = save_inputs(tmp_path, frames, manifest)
    original = {p.name: p.read_bytes() for p in directory.iterdir()}
    import nflreadpy as nfl

    monkeypatch.setattr(
        nfl, "load_player_stats", lambda *a, **k: pytest.fail("No network or holdout")
    )
    destination = tmp_path / "report"
    assert (
        main(["calibration-audit", "--data-dir", str(tmp_path), "--report-dir", str(destination)])
        == 0
    )
    assert (destination / "calibration_audit.json").exists()
    assert "paired" in (destination / "calibration_audit.md").read_text()
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == original
    path = directory / manifest["artifacts"]["calibration_residuals"]["file"]
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="checksum"):
        load_calibration_audit(tmp_path)


def test_policy_is_copy_isolated_and_never_enables_production():
    policy = candidate_policy()
    original_hash = policy_hash(policy)
    policy["parameters"]["max_depth"] = 999
    assert candidate_policy()["parameters"]["max_depth"] == 2
    assert original_hash == policy_hash(candidate_policy())
    assert history_policy_status(0) == history_policy_status(4) == "research_only_sparse_history"
    assert history_policy_status(5) == "history_minimum_met_production_pending"
    assert not candidate_policy()["production_enabled"]
    for value in (True, -1, 2.5):
        with pytest.raises(ValueError):
            history_policy_status(value)


def test_diagnostic_import_does_not_load_fitting_libraries():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import nfl_prop_model.modeling.calibration_audit; "
            "assert 'xgboost' not in sys.modules; assert 'sklearn' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_old_cache_gives_rebuild_instructions(tmp_path, calibration_inputs):
    frames, manifest = calibration_inputs
    directory, manifest = save_inputs(tmp_path, frames, manifest)
    manifest["format_version"] = 1
    manifest["artifacts"].pop("calibration_residuals")
    write_json(directory / "manifest.json", manifest)
    with pytest.raises(DataQualityError, match="run nfl-prop research"):
        load_calibration_audit(tmp_path)


def test_manifest_change_during_read_cannot_publish_mixed_provenance(
    tmp_path, calibration_inputs, monkeypatch
):
    import nfl_prop_model.modeling.calibration_audit as module

    frames, manifest = calibration_inputs
    save_inputs(tmp_path, frames, manifest)
    hashes = iter(["first-version", "revised-version"])
    monkeypatch.setattr(module, "sha256_file", lambda path: next(hashes))
    with pytest.raises(DataQualityError, match="manifest changed while reading"):
        load_calibration_audit(tmp_path)


def test_calibration_page_and_model_switch_work_without_raw_cache(
    tmp_path, calibration_inputs, monkeypatch
):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    frames, manifest = calibration_inputs
    save_inputs(tmp_path, frames, manifest)
    monkeypatch.setenv("NFL_PROP_DATA_DIR", str(tmp_path))
    app = AppTest.from_file(Path(__file__).parents[1] / "app.py", default_timeout=30).run()
    app.radio(key="page").set_value("Calibration audit").run()
    assert not app.exception and not app.error
    assert app.metric[0].value == "6" and len(app.dataframe[0].value) == 3
    app.selectbox(key="audit_model").set_value("ridge").run()
    app.selectbox(key="audit_history").set_value(BUCKETS[0]).run()
    assert not app.exception and not app.error
    assert not (tmp_path / "raw").exists()


def test_sparse_history_comparison_is_flagged_without_changing_math(saved_research, monkeypatch):
    from nfl_prop_model.data.storage import load_frame

    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    data, directory, manifest = saved_research
    points = load_frame(directory, manifest["artifacts"]["predictions"])
    manifest["artifacts"]["predictions"] = store_frame(
        directory,
        "predictions",
        points.with_columns(
            pl.lit(0).alias("prior_games_in_sample"), pl.lit(BUCKETS[0]).alias("history_bucket")
        ),
    )
    write_json(directory / "manifest.json", manifest)
    monkeypatch.setenv("NFL_PROP_DATA_DIR", str(data))
    app = AppTest.from_file(Path(__file__).parents[1] / "app.py", default_timeout=30).run()
    assert not app.exception and not app.error
    assert any("fewer than five" in w.value for w in app.warning)
    assert app.metric[0].value == "200.0 yd"
