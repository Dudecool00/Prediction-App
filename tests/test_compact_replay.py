from copy import deepcopy
from datetime import timedelta

import polars as pl
import pytest
from polars.testing import assert_frame_equal
from test_team_scores import team_sources as team_sources
from test_team_walkforward import weekly_data as weekly_data

from nfl_prop_model.cli import main
from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.storage import read_json, store_frame, write_json
from nfl_prop_model.features.team_walkforward import COMPACT_EPA, weekly_features
from nfl_prop_model.modeling import team_compact_replay as replay


@pytest.fixture
def replay_data(weekly_data):
    teams, games, *_ = weekly_data

    def next_season(frame):
        return frame.filter(pl.col("season") == 2024).with_columns(
            pl.lit(2025, dtype=frame.schema["season"]).alias("season"),
            pl.col("game_id").str.replace("^2024_", "2025_"),
            pl.col("kickoff_utc", "result_available_at_utc").dt.offset_by("1y"),
        )

    teams = pl.concat([teams, next_season(teams)])
    games = pl.concat([games, next_season(games)])
    features = weekly_features(teams, games).select(replay.FEATURE_COLUMNS)
    return teams, games, features


def test_exactly_eight_inputs_and_eighteen_fits_with_reused_refresh(replay_data, monkeypatch):
    _, _, features = replay_data
    fits = []
    original = replay.score_pipeline

    def pipeline():
        model = original()
        fit = model.fit

        def counted(x, y):
            fits.append(x.shape)
            return fit(x, y)

        model.fit = counted
        return model

    monkeypatch.setattr(replay, "score_pipeline", pipeline)
    panel, folds = replay.evaluate_compact(features)
    assert len(COMPACT_EPA) == 8
    assert len(fits) == 18
    assert all(shape[1] == 8 for shape in fits)
    assert len(folds) == 36
    assert folds[0]["training_games"] == 108
    assert folds[0]["training_seasons"] == [2022, 2023, 2024]
    assert folds[0]["prior_2025_games"] == 0
    assert folds[-1]["prior_2025_games"] > 0
    assert panel.height == 36 * 2 * 3
    columns = ["game_id", "model", *replay.SCORES]
    assert_frame_equal(
        panel.filter(pl.col("horizon") == "early_week").select(columns).sort("game_id", "model"),
        panel.filter(pl.col("horizon") == "pregame_refresh")
        .select(columns)
        .sort("game_id", "model"),
    )


def test_future_scores_and_epa_do_not_change_earlier_predictions(replay_data):
    teams, games, features = replay_data
    future = (pl.col("season") == 2025) & (pl.col("week") >= 12)
    altered_teams = teams.with_columns(
        pl.when(future).then(99999.0).otherwise(pl.col("off_epa_sum")).alias("off_epa_sum")
    )
    altered_games = games.with_columns(
        pl.when(future).then(99.0).otherwise(pl.col("home_score")).alias("home_score")
    ).with_columns((pl.col("home_score") - pl.col("away_score")).alias("target_home_margin"))
    changed = weekly_features(altered_teams, altered_games).select(replay.FEATURE_COLUMNS)
    original_panel, _ = replay.evaluate_compact(features)
    changed_panel, _ = replay.evaluate_compact(changed)
    columns = ["game_id", "horizon", "model", *replay.SCORES]
    assert_frame_equal(
        original_panel.filter(pl.col("week") <= 12).select(columns),
        changed_panel.filter(pl.col("week") <= 12).select(columns),
    )


def test_result_at_lock_is_excluded_and_monday_assumption_is_explicit(replay_data):
    _, _, features = replay_data
    history = features.filter(pl.col("horizon") == "early_week")
    test = history.filter((pl.col("season") == 2025) & (pl.col("week") == 2))
    lock = test["week_lock_utc"][0]
    previous = history.filter((pl.col("season") == 2025) & (pl.col("week") == 1))["game_id"][0]
    changed = history.with_columns(
        pl.when(pl.col("game_id") == previous)
        .then(pl.lit(lock))
        .otherwise(pl.col("result_available_at_utc"))
        .alias("result_available_at_utc")
    )
    train, receipt = replay.training_fold(changed, test)
    assert previous not in train["game_id"]
    assert previous in receipt["unavailable_earlier_games"]
    available = changed.with_columns(
        pl.when(pl.col("game_id") == previous)
        .then(pl.lit(lock - timedelta(microseconds=1)))
        .otherwise(pl.col("result_available_at_utc"))
        .alias("result_available_at_utc")
    )
    assert previous in replay.training_fold(available, test)[0]["game_id"]


@pytest.mark.parametrize("change", ["refresh", "availability", "nonfinite", "season", "cutoff"])
def test_invalid_features_rejected(replay_data, change):
    _, _, features = replay_data
    if change == "refresh":
        features = features.with_columns(
            pl.when(pl.col("horizon") == "pregame_refresh")
            .then(pl.col(COMPACT_EPA[0]) + 1)
            .otherwise(pl.col(COMPACT_EPA[0]))
            .alias(COMPACT_EPA[0])
        )
    elif change == "availability":
        features = features.with_columns(pl.col("kickoff_utc").alias("result_available_at_utc"))
    elif change == "nonfinite":
        features = features.with_columns(pl.lit(float("inf")).alias(COMPACT_EPA[0]))
    elif change == "season":
        features = features.with_columns(pl.lit(2026).alias("season"))
    else:
        features = features.with_columns(pl.col("kickoff_utc").alias("prediction_time_utc"))
    with pytest.raises(DataQualityError):
        replay.validate_features(features)


def test_missing_week_and_small_history_rejected(replay_data):
    _, _, features = replay_data
    with pytest.raises(DataQualityError, match="18 forecast weeks"):
        replay.evaluate_compact(features.filter(pl.col("week") != 18))
    with pytest.raises(DataQualityError, match="64 available"):
        replay.evaluate_compact(features.filter(pl.col("season") == 2025))


def test_freeze_is_preserved_and_changed_inputs_fail(tmp_path):
    packet = replay.freeze(tmp_path, {"test": "original"})
    assert replay.freeze(tmp_path, {"test": "original"}) == packet
    with pytest.raises(DataQualityError, match="never overwrite"):
        replay.freeze(tmp_path, {"test": "changed"})
    assert read_json(tmp_path / "frozen_2025.json") == packet


def test_incomplete_real_season_cohort_rejected(replay_data, monkeypatch, tmp_path):
    teams, games, _ = replay_data
    monkeypatch.setattr(replay, "load_team_cache", lambda _: (teams, games, {}))
    with pytest.raises(DataQualityError, match="272 completed"):
        replay._features(tmp_path)


@pytest.mark.parametrize("model,fits", [("other_model", 0), ("compact_epa", 1)])
def test_selection_requires_verified_compact_choice_without_2025_fits(
    tmp_path, monkeypatch, model, fits
):
    directory = (
        tmp_path
        / "processed"
        / "team_simplicity"
        / (replay.simplicity.simplicity_source_hash()[:12] + "-test")
    )
    write_json(directory / "manifest.json", {})
    monkeypatch.setattr(
        replay.simplicity,
        "load_simplicity_report",
        lambda _: {
            "frozen_2025_research_choice": {"model": model},
            "2025_fits_performed": fits,
        },
    )
    with pytest.raises(DataQualityError, match="without 2025 fits"):
        replay.selection_inputs(tmp_path)


def test_missing_development_reports_fail_before_replay(tmp_path):
    with pytest.raises(DataQualityError, match="current verified development report"):
        replay.save_compact_replay(tmp_path, tmp_path / "reports")
    assert not list(tmp_path.glob("processed/team_compact_replay/*/frozen_2025.json"))


def test_freeze_precedes_canonical_outcome_read(tmp_path, monkeypatch):
    monkeypatch.setattr(replay, "selection_inputs", lambda _: {"test": "verified"})

    def read_after_freeze(_):
        assert len(list(tmp_path.glob("processed/team_compact_replay/*/frozen_2025.json"))) == 1
        raise DataQualityError("stop before outcomes")

    monkeypatch.setattr(replay, "_features", read_after_freeze)
    with pytest.raises(DataQualityError, match="stop before outcomes"):
        replay.save_compact_replay(tmp_path, tmp_path / "reports")


def test_save_reader_reuses_run_and_rejects_tampering(tmp_path, replay_data, monkeypatch):
    features = replay_data[2]
    monkeypatch.setattr(replay, "selection_inputs", lambda _: {"test": "verified"})
    monkeypatch.setattr(replay, "_features", lambda _: features)
    monkeypatch.setattr(replay, "export_report", lambda *_: None)
    report = replay.save_compact_replay(tmp_path, tmp_path / "reports")
    path = next(tmp_path.glob("processed/team_compact_replay/*/manifest.json"))
    monkeypatch.setattr(replay, "evaluate_compact", lambda _: pytest.fail("must reuse, not refit"))
    assert replay.save_compact_replay(tmp_path, tmp_path / "reports") == report
    for key, value in (
        ("2025_fits_performed", 36),
        ("enriched_replay_ready", True),
        ("source_sha256", "changed"),
        ("metrics", []),
    ):
        changed = deepcopy(report)
        changed[key] = value
        write_json(path, changed)
        with pytest.raises(DataQualityError):
            replay.load_compact_replay(path, tmp_path)
    write_json(path, report)
    panel = pl.read_parquet(path.parent / report["artifacts"]["predictions"]["file"])
    panel = panel.with_columns((pl.col("predicted_home_margin") + 1).alias("predicted_home_margin"))
    report["artifacts"]["predictions"] = store_frame(path.parent, "predictions", panel)
    write_json(path, report)
    with pytest.raises(DataQualityError, match="contract"):
        replay.load_compact_replay(path, tmp_path)


def test_graphs_and_exports_have_actual_and_predicted_scores(tmp_path, replay_data):
    panel, _ = replay.evaluate_compact(replay_data[2])
    replay.write_charts(panel, tmp_path / "comparison.html")
    html = (tmp_path / "comparison.html").read_text(encoding="utf-8")
    assert "actual vs predicted" in html
    assert "2025 previously examined" in html
    assert "plotly" in html


def test_command_is_separate_and_has_no_gate_override(capsys):
    with pytest.raises(SystemExit) as result:
        main(["team-compact-replay", "--help"])
    assert result.value.code == 0
    help_text = capsys.readouterr().out
    assert "--data-dir" in help_text
    assert "--force" not in help_text
