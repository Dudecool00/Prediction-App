from datetime import UTC, datetime, timedelta

import polars as pl
import pytest
from polars.testing import assert_frame_equal
from test_team_scores import team_sources as team_sources

from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.storage import read_json, store_frame, write_json
from nfl_prop_model.data.team_games import build_team_games
from nfl_prop_model.data.team_pregame import forecast_lead
from nfl_prop_model.features.team_epa import TEAM_FEATURES
from nfl_prop_model.features.team_walkforward import (
    MODELS,
    add_pregame_features,
    weekly_features,
)
from nfl_prop_model.modeling.team_walkforward import (
    check_2025_replay,
    evaluate_weeks,
    freeze_choice,
    load_walkforward_development,
    paired_comparisons,
    require_replay_ready,
    walkforward_source_hash,
)


@pytest.fixture
def weekly_data(team_sources):
    plays, schedules = team_sources
    schedules = schedules.with_columns(
        pl.col("gameday").str.to_date().dt.offset_by("2y").dt.strftime("%Y-%m-%d")
    )
    for old, new in ((2022, 2024), (2021, 2023), (2020, 2022)):
        plays = plays.with_columns(
            pl.when(pl.col("season") == old).then(new).otherwise(pl.col("season")).alias("season"),
            pl.col("game_id").str.replace(f"^{old}_", f"{new}_"),
        )
        schedules = schedules.with_columns(
            pl.when(pl.col("season") == old).then(new).otherwise(pl.col("season")).alias("season"),
            pl.col("game_id").str.replace(f"^{old}_", f"{new}_"),
        )
    teams, games, _ = build_team_games(plays, schedules)
    base = weekly_features(teams, games)
    weather = base.select("game_id", "horizon", "prediction_time_utc").with_columns(
        pl.lit(1.0).alias("exposure"),
        pl.lit(10.0).alias("temperature_c"),
        (pl.col("prediction_time_utc") - pl.duration(days=1)).alias("assumed_available_bound_utc"),
    )
    injuries = pl.DataFrame(
        schema={
            "season": pl.Int32,
            "week": pl.Int32,
            "team": pl.String,
            "gsis_id": pl.String,
            "position": pl.String,
            "report_status": pl.String,
            "available_at_utc": pl.Datetime("us", "UTC"),
        }
    )
    return teams, games, base, weather, injuries


def test_weather_lead_uses_older_forecast_and_strict_release_buffer():
    valid = datetime(2025, 9, 7, 17, tzinfo=UTC)
    early = datetime(2025, 9, 2, 15, tzinfo=UTC)
    assert forecast_lead(valid, early) == (6, valid - timedelta(days=6) + timedelta(hours=6))
    assert forecast_lead(valid, valid - timedelta(hours=1))[0] == 1
    # An availability bound equal to cutoff is not yet admissible.
    assert forecast_lead(valid, valid - timedelta(hours=18))[0] == 2
    with pytest.raises(DataQualityError, match="seven-day"):
        forecast_lead(valid, valid - timedelta(days=9))
    with pytest.raises(DataQualityError, match="aware"):
        forecast_lead(valid.replace(tzinfo=None), early)


def test_future_results_and_pre2022_history_cannot_change_weekly_features(weekly_data):
    teams, games, base, _, _ = weekly_data
    chosen = (pl.col("season") == 2024) & (pl.col("week") == 5)
    original = base.filter(chosen)
    changed = teams.with_columns(
        pl.when((pl.col("season") == 2024) & (pl.col("week") >= 5))
        .then(9999.0)
        .otherwise(pl.col("off_epa_sum"))
        .alias("off_epa_sum")
    )
    old = teams.filter(pl.col("season") == 2022).with_columns(
        pl.lit(2021, dtype=teams.schema["season"]).alias("season")
    )
    altered = weekly_features(pl.concat([changed, old]), games).filter(chosen)
    assert_frame_equal(
        original.select("game_id", "horizon", *TEAM_FEATURES),
        altered.select("game_id", "horizon", *TEAM_FEATURES),
    )
    early = original.filter(pl.col("horizon") == "early_week")
    refresh = original.filter(pl.col("horizon") == "pregame_refresh")
    assert_frame_equal(
        early.select("game_id", *TEAM_FEATURES), refresh.select("game_id", *TEAM_FEATURES)
    )


def test_unknown_injury_is_not_healthy_and_future_report_is_excluded(weekly_data):
    _, _, base, weather, empty = weekly_data
    selected = base.filter((pl.col("season") == 2024) & (pl.col("week") == 4))
    first = selected.filter(pl.col("horizon") == "early_week").row(0, named=True)
    report = pl.DataFrame(
        [
            {
                "season": first["season"],
                "week": first["week"],
                "team": first["home_team"],
                "gsis_id": "qb",
                "position": "QB",
                "report_status": "Out",
                "available_at_utc": first["prediction_time_utc"],
            }
        ],
        schema=empty.schema,
    )
    result = add_pregame_features(selected, report, weather)
    early = result.filter(
        (pl.col("game_id") == first["game_id"]) & (pl.col("horizon") == "early_week")
    )
    refresh = result.filter(
        (pl.col("game_id") == first["game_id"]) & (pl.col("horizon") == "pregame_refresh")
    )
    assert early["home_qb_out_count"].item() is None
    assert refresh["home_qb_out_count"].item() == 1
    assert refresh["away_qb_out_count"].item() is None
    assert early["home_observed_injury_players"].item() == 0


def test_weather_cutoff_mismatch_and_future_bound_fail_visibly(weekly_data):
    _, _, base, weather, injuries = weekly_data
    changed = weather.with_columns(
        pl.col("prediction_time_utc").alias("assumed_available_bound_utc")
    )
    with pytest.raises(DataQualityError, match="unavailable weather"):
        add_pregame_features(base, injuries, changed)
    with pytest.raises(DataQualityError, match="Missing"):
        add_pregame_features(base, injuries, weather.head(1))
    undated = weather.with_columns(
        pl.lit(None, dtype=pl.Datetime("us", "UTC")).alias("assumed_available_bound_utc")
    )
    with pytest.raises(DataQualityError, match="unavailable weather"):
        add_pregame_features(base, injuries, undated)
    unknown = weather.with_columns(pl.lit(None, dtype=pl.Float64).alias("exposure"))
    result = add_pregame_features(base, injuries, unknown)
    assert result["forecast_temperature"].null_count() == result.height


def test_weekly_training_cohorts_and_score_margin_contract(weekly_data):
    _, _, base, weather, injuries = weekly_data
    features = add_pregame_features(base, injuries, weather)
    # Historical 2022+2023 fixture provides 72 games at 2024's first lock.
    predictions, folds, _ = evaluate_weeks(
        features, seasons=(2024,), models=("ridge_epa_scores", "compact_temperature")
    )
    assert len(folds) == 36
    assert folds[0]["training_games"] == 72
    for fold in folds:
        assert fold["training_seasons"][0] == 2022
        assert fold["latest_training_result_available_utc"] < fold["lock_utc"]
    assert predictions.filter(
        (
            pl.col("predicted_home_margin")
            - (pl.col("predicted_home_score") - pl.col("predicted_away_score"))
        ).abs()
        > 1e-8
    ).is_empty()
    assert predictions.filter(
        pl.col("predicted_home_margin") != -pl.col("predicted_away_margin")
    ).is_empty()
    for group in predictions.partition_by(["horizon", "model"]):
        assert group.height == 36


def test_freeze_rejects_2025_selection_and_requires_uncertainty_guard():
    rows, gains = [], []
    for name in MODELS:
        for scope, season in (("overall", None), ("season", 2023), ("season", 2024)):
            rows.append(
                {
                    "model": name,
                    "scope": scope,
                    "season": season,
                    "horizon": "early_week",
                    "margin_mae": 10.0 if name == "ridge_epa_scores" else 9.0,
                    "score_mae": 7.0,
                    "total_mae": 11.0,
                }
            )
        if name != "ridge_epa_scores":
            gains.append(
                {
                    "model": name,
                    "horizon": "early_week",
                    "margin_mae_gain": 1.0,
                    "week_block_95_lower": -0.01,
                }
            )
    metrics, comparisons = pl.DataFrame(rows), pl.DataFrame(gains)
    assert freeze_choice(metrics, comparisons)["model"] == "ridge_epa_scores"
    with pytest.raises(DataQualityError, match="2023-2024"):
        freeze_choice(metrics.with_columns(pl.lit(2025).alias("season")), comparisons)


def test_context_gain_is_separate_from_epa_pruning():
    rows = []
    for horizon in ("early_week", "pregame_refresh"):
        for name in MODELS:
            for week in (1, 2):
                rows.append(
                    {
                        "game_id": f"2024_{week}",
                        "horizon": horizon,
                        "season": 2024,
                        "week": week,
                        "model": name,
                        "target_home_margin": 0.0,
                        "predicted_home_margin": 5.0 if name == "ridge_epa_scores" else 4.0,
                    }
                )
    predictions = pl.DataFrame(rows)
    primary = paired_comparisons(predictions)
    incremental = paired_comparisons(predictions, control_model="compact_epa")
    assert set(primary["margin_mae_gain"]) == {1.0}
    assert set(incremental["margin_mae_gain"]) == {0.0}


def test_blocked_replay_never_fits_or_reads_2025_outcomes(tmp_path, monkeypatch):
    from nfl_prop_model.modeling import team_walkforward as module

    monkeypatch.setattr(
        module,
        "load_walkforward_development",
        lambda _: {"frozen_choice": {"model": "ridge_epa_scores"}, "source_sha256": "hash"},
    )
    monkeypatch.setattr(
        module,
        "fetch_pregame_audit",
        lambda _: {"blockers": ["untimed 2025 injuries"], "ready_for_enriched_2025": False},
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("Blocked replay read outcomes or fitted a model")

    monkeypatch.setattr(module, "load_team_cache", forbidden)
    monkeypatch.setattr(module, "predict_models", forbidden)
    status = check_2025_replay(tmp_path, tmp_path / "report")
    assert status["status"] == "blocked" and status["2025_fits_performed"] == 0
    assert read_json(tmp_path / "report" / "replay_status.json") == status
    with pytest.raises(DataQualityError, match="blocked"):
        require_replay_ready({"blockers": ["untimed"], "ready_for_enriched_2025": True})


def test_frozen_choice_and_artifact_tampering_are_detected(tmp_path):
    directory = tmp_path / "processed" / "team_walkforward"
    frame = pl.DataFrame({"a": [1]})
    metadata = store_frame(directory, "predictions", frame)
    frozen = {"model": "ridge_epa_scores"}
    write_json(
        directory / "manifest.json",
        {
            "source_sha256": walkforward_source_hash(),
            "frozen_choice": frozen,
            "artifacts": {"predictions": metadata},
        },
    )
    write_json(directory / "frozen_2025.json", frozen)
    assert load_walkforward_development(tmp_path)["frozen_choice"] == frozen
    write_json(directory / "frozen_2025.json", {"model": "compact_combined"})
    with pytest.raises(DataQualityError, match="changed after"):
        load_walkforward_development(tmp_path)
    write_json(directory / "frozen_2025.json", frozen)
    (directory / metadata["file"]).write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="checksum"):
        load_walkforward_development(tmp_path)
