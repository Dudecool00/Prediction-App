from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest
from polars.testing import assert_frame_equal
from test_upcoming import prospective_sources

from nfl_prop_model.cli import main
from nfl_prop_model.data.prospective import (
    build_feature_report,
    load_feature_report,
    load_prior_history,
    read_feature_snapshot,
    save_feature_snapshot,
)
from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.status_reviews import review_context
from nfl_prop_model.data.storage import read_json, sha256_file, store_frame, write_json
from nfl_prop_model.features.prospective import (
    candidate_features,
    observed_history,
    validate_history,
)
from nfl_prop_model.features.quarterback import add_lagged_features

AS_OF = datetime(2026, 10, 6, 12, tzinfo=UTC)


def feature_sources(as_of=AS_OF):
    schedules, depth, manifest = prospective_sources(as_of)
    upcoming = schedules.with_columns(
        pl.lit(None, dtype=pl.String).alias("away_qb_id"), pl.lit("Home").alias("location")
    )
    past = upcoming.with_columns(
        pl.lit("2026_past").alias("game_id"),
        pl.lit(2).alias("week"),
        pl.lit((as_of - timedelta(days=3)).date().isoformat()).alias("gameday"),
        pl.lit("13:00").alias("gametime"),
        pl.lit(24.0).alias("home_score"),
        pl.lit(17.0).alias("away_score"),
    )
    schedules = pl.concat([past, upcoming], how="vertical_relaxed")
    stats = pl.DataFrame(
        [
            {
                "player_id": f"gsis-{team}",
                "player_display_name": f"Current {team}",
                "position": "QB",
                "season_type": "REG",
                "game_id": "2026_past",
                "season": 2026,
                "week": 2,
                "team": team,
                "opponent_team": opponent,
                "passing_yards": yards,
                "attempts": attempts,
                "completions": 0,
            }
            for team, opponent, yards, attempts in (("BUF", "ARI", 400, 5), ("ARI", "BUF", 0, 0))
        ]
    )
    stats_manifest = {
        "format_version": 1,
        "season": 2026,
        "retrieved_at_utc": (as_of - timedelta(minutes=30)).isoformat(),
    }
    rows = [
        {
            "player_id": "gsis-BUF",
            "game_id": f"past-{index}",
            "season": 2024 if index < 3 else 2025,
            "kickoff_utc": datetime(2024 if index < 3 else 2025, 9, 1, tzinfo=UTC)
            + timedelta(weeks=index),
            "target_passing_yards": float(yards),
            "observed_attempts": float(index),
        }
        for index, yards in enumerate((0, -5, 100, 300, 200))
    ]
    history = observed_history(pl.DataFrame(rows), as_of - timedelta(days=1))
    return schedules, depth, stats, manifest, stats_manifest, history


def build(inputs=None, **kwargs):
    return build_feature_report(
        *(inputs or feature_sources()), as_of=kwargs.pop("as_of", AS_OF), **kwargs
    )


def buf(report):
    return next(row for row in report["candidates"] if row["gsis_id"] == "gsis-BUF")


def test_hand_calculated_features_keep_zero_negative_and_sparse_rows():
    inputs = feature_sources()
    originals = [frame.clone() for frame in (inputs[0], inputs[2], inputs[5])]
    report = build(inputs)
    row = buf(report)
    assert row["features"] == {
        "passing_yards_lag1": 400.0,
        "passing_yards_mean3": 300.0,
        "passing_yards_mean5": 199.0,
        "attempts_mean5": 3.0,
        "passing_yards_season_mean": 400.0,
        "prior_games_in_sample": 6,
        "prior_season_games_in_sample": 1,
        "is_designated_home": True,
        "is_neutral_site": False,
        "team_rest_days": 5 + 7.25 / 24,
        "opponent_rest_days": 5 + 7.25 / 24,
    }
    assert row["features_ready"] and row["history_minimum_met"]
    assert row["history_audit"]["last_five"][0]["target_passing_yards"] == -5
    assert row["history_audit"]["current_season_game_ids"] == ["2026_past"]
    assert report["history_counts"] == {"2022": 0, "2023": 0, "2024": 3, "2025": 2, "2026": 2}
    rookie = next(row for row in report["candidates"] if row["gsis_id"] is None)
    assert rookie["features"]["prior_games_in_sample"] == 0
    assert rookie["features"]["passing_yards_lag1"] is None
    assert "missing_gsis_mapping" in rookie["feature_blockers"]
    assert all(not row["forecast_available"] for row in report["candidates"])
    for original, frame in zip(originals, (inputs[0], inputs[2], inputs[5]), strict=True):
        assert_frame_equal(frame, original)


def test_available_features_match_shifted_historical_definition():
    inputs = feature_sources()
    report = build(inputs)
    row = buf(report)
    prior = inputs[5].drop("available_at_utc")
    from nfl_prop_model.data.quarterback_games import build_target_table

    current, _ = build_target_table(inputs[2], inputs[0])
    prior = pl.concat(
        [prior, current.filter(pl.col("player_id") == "gsis-BUF").select(prior.columns)]
    )
    candidate = prior.tail(1).with_columns(
        pl.lit(row["game_id"]).alias("game_id"),
        pl.lit(datetime.fromisoformat(row["kickoff_utc"])).alias("kickoff_utc"),
        pl.lit(987654.0).alias("target_passing_yards"),
    )
    table = pl.concat([prior, candidate]).with_columns(
        (pl.col("kickoff_utc") - pl.duration(hours=1)).alias("prediction_time_utc")
    )
    reference = add_lagged_features(table).tail(1).to_dicts()[0]
    from nfl_prop_model.features.quarterback import FEATURE_COLUMNS

    assert {name: reference[name] for name in FEATURE_COLUMNS} == {
        name: row["features"][name] for name in FEATURE_COLUMNS
    }


def test_current_and_future_candidate_outcomes_do_not_enter_features():
    inputs = list(feature_sources())
    original = build(inputs)
    new_stats = inputs[2].with_columns(
        pl.lit("2026_03_ARI_BUF").alias("game_id"),
        pl.lit(3).alias("week"),
        pl.lit(999999).alias("passing_yards"),
    )
    inputs[2] = pl.concat([inputs[2], new_stats], how="vertical_relaxed")
    assert build(inputs) == original
    inputs[0] = inputs[0].with_columns(
        pl.lit("unverified").alias("home_qb_id"), pl.lit("unverified").alias("away_qb_id")
    )
    assert buf(build(inputs))["features"] == buf(original)["features"]


def test_late_results_excluded_and_source_retrieval_never_backdated():
    inputs = feature_sources()
    candidate = buf(build(inputs))
    late = (
        inputs[5]
        .tail(1)
        .with_columns(
            pl.lit("late").alias("game_id"),
            pl.lit(AS_OF - timedelta(hours=30)).alias("kickoff_utc"),
            pl.lit(AS_OF + timedelta(minutes=1)).alias("available_at_utc"),
            pl.lit(100000.0).alias("target_passing_yards"),
        )
    )
    history = pl.concat([inputs[5], late])
    result = candidate_features(
        candidate, history, inputs[0], as_of=AS_OF, observed_at=AS_OF - timedelta(minutes=30)
    )
    assert result["features"]["prior_games_in_sample"] == 5
    assert result["features"]["passing_yards_lag1"] == 200
    observed = observed_history(inputs[5], AS_OF)
    assert observed["available_at_utc"].min() == AS_OF
    validate_history(observed)


@pytest.mark.parametrize("source", ["stats", "schedule"])
def test_future_source_timestamps_rejected(source):
    inputs = list(feature_sources())
    index = 4 if source == "stats" else 3
    inputs[index] = {
        **inputs[index],
        "retrieved_at_utc": (AS_OF + timedelta(seconds=1)).isoformat(),
    }
    with pytest.raises(ValueError, match="retrieved after"):
        build(inputs)


@pytest.mark.parametrize(
    "change", ["stale_stats", "stale_chart", "partial_stats", "unknown_location", "cutoff"]
)
def test_feature_gates_are_explicit(change):
    inputs = list(feature_sources())
    expected = {
        "stale_stats": "current_stats_cache_older_than_24h",
        "stale_chart": "schedule_or_chart_stale",
        "partial_stats": "current_stats_coverage_incomplete",
        "unknown_location": "schedule_location_unknown",
        "cutoff": "snapshot_after_forecast_cutoff",
    }[change]
    as_of = AS_OF
    if change == "stale_stats":
        inputs[4] = {**inputs[4], "retrieved_at_utc": (AS_OF - timedelta(hours=25)).isoformat()}
    elif change == "stale_chart":
        inputs[1] = inputs[1].with_columns(
            pl.lit((AS_OF - timedelta(hours=49)).isoformat()).alias("dt")
        )
    elif change == "partial_stats":
        inputs[2] = inputs[2].filter(pl.col("team") == "BUF")
    elif change == "unknown_location":
        inputs[0] = inputs[0].with_columns(pl.lit("unknown").alias("location"))
    else:
        as_of = datetime(2026, 10, 8, 23, 15, tzinfo=UTC)
    row = buf(build(inputs, as_of=as_of))
    assert not row["features_ready"]
    assert expected in row["feature_blockers"]


@pytest.mark.parametrize("change", ["future", "one_score", "unknown_time"])
def test_intervening_games_do_not_invent_rest(change):
    inputs = list(feature_sources())
    extra = (
        inputs[0]
        .tail(1)
        .with_columns(
            pl.lit("intervening").alias("game_id"),
            pl.lit(2).alias("week"),
            pl.lit("2026-10-07").alias("gameday"),
        )
    )
    if change == "one_score":
        extra = extra.with_columns(pl.lit(0.0).alias("home_score"))
    elif change == "unknown_time":
        extra = extra.with_columns(pl.lit(None, dtype=pl.String).alias("gametime"))
    inputs[0] = pl.concat([inputs[0], extra], how="vertical_relaxed")
    row = next(
        row
        for row in build(inputs)["candidates"]
        if row["gsis_id"] == "gsis-BUF" and row["game_id"] != "intervening"
    )
    assert row["features"]["team_rest_days"] is None
    assert "team_rest_unresolved" in row["feature_blockers"]
    assert row["rest_audit"]["opponent"]["unresolved_game_ids"] == ["intervening"]


def test_manual_confirmed_active_claims_do_not_enable_forecasts():
    inputs = feature_sources()
    row = buf(build(inputs))
    record = {
        "format_version": 1,
        "scope": "manual_pregame_status_review",
        "record_id": "a" * 32,
        "logged_at_utc": (AS_OF - timedelta(minutes=5)).isoformat(),
        "context": review_context(row),
        "sources": inputs[3],
        "notes": "test",
        "starter": {
            "status": "confirmed",
            "source_url": "https://example.com/starter",
            "source_at_utc": (AS_OF - timedelta(minutes=10)).isoformat(),
        },
        "availability": {
            "status": "active",
            "source_url": "https://example.com/active",
            "source_at_utc": (AS_OF - timedelta(minutes=10)).isoformat(),
        },
    }
    row = buf(build(inputs, status_reviews=[record]))
    assert row["starter_confirmed"] and row["active_status_reviewed"]
    assert "starter_status_unknown" not in row["forecast_blockers"]
    assert "status_source_contents_not_independently_verified" in row["forecast_blockers"]
    assert not row["forecast_available"]


def test_recent_current_result_is_excluded_and_neutral_first_game_rest_is_nullable():
    inputs = list(feature_sources())
    inputs[0] = inputs[0].with_columns(
        pl.when(pl.col("game_id") == "2026_past")
        .then(pl.lit("2026-10-05"))
        .otherwise(pl.col("gameday"))
        .alias("gameday")
    )
    row = buf(build(inputs))
    assert row["features"]["prior_games_in_sample"] == 5
    assert row["features"]["passing_yards_season_mean"] is None
    assert "team_rest_unresolved" in row["feature_blockers"]
    inputs[2] = inputs[2].with_columns(pl.lit(99999).alias("passing_yards"))
    assert buf(build(inputs))["features"] == row["features"]
    schedule = inputs[0].tail(1).with_columns(pl.lit("Neutral").alias("location"))
    result = candidate_features(
        row, inputs[5], schedule, as_of=AS_OF, observed_at=AS_OF - timedelta(minutes=30)
    )
    assert result["features"]["is_neutral_site"] is True
    assert result["features"]["team_rest_days"] is None
    assert result["features"]["opponent_rest_days"] is None
    assert not result["feature_issues"]


@pytest.mark.parametrize(
    "change",
    [
        "duplicate",
        "null_time",
        "naive_time",
        "infinite",
        "negative_attempts",
        "early_availability",
        "wrong_season",
    ],
)
def test_history_contracts(change):
    history = feature_sources()[5]
    if change == "duplicate":
        history = pl.concat([history, history.head(1)])
    elif change == "null_time":
        history = history.with_columns(
            pl.lit(None, dtype=pl.Datetime("us", "UTC")).alias("available_at_utc")
        )
    elif change == "naive_time":
        history = history.with_columns(pl.col("kickoff_utc").dt.replace_time_zone(None))
    elif change == "infinite":
        history = history.with_columns(pl.lit(float("inf")).alias("target_passing_yards"))
    elif change == "negative_attempts":
        history = history.with_columns(pl.lit(-1.0).alias("observed_attempts"))
    elif change == "early_availability":
        history = history.with_columns(pl.col("kickoff_utc").alias("available_at_utc"))
    else:
        history = history.with_columns(pl.lit(2027).alias("season"))
    with pytest.raises(DataQualityError):
        validate_history(history)


def save_inputs(data: Path, as_of=AS_OF):
    schedules, depth, stats, upcoming, stats_manifest, history = feature_sources(as_of)
    directory = data / "raw" / "upcoming_2026"
    upcoming["datasets"] = {
        name: store_frame(directory, name, frame)
        for name, frame in (("schedules", schedules), ("depth_charts", depth))
    }
    write_json(directory / "manifest.json", upcoming)
    stats_dir = data / "raw" / "current_stats_2026"
    stats_manifest["player_stats"] = store_frame(stats_dir, "player_stats", stats)
    write_json(stats_dir / "manifest.json", stats_manifest)
    dev_dir = data / "processed" / "2022_2023_2024"
    write_json(
        dev_dir / "manifest.json",
        {
            "raw_manifest": {"retrieved_at_utc": (as_of - timedelta(days=1)).isoformat()},
            "table": store_frame(dev_dir, "features", history.filter(pl.col("season") == 2024)),
        },
    )
    root = data / "holdout" / "2025"
    run = root / ("run-" + "a" * 64)
    write_json(
        run / "manifest.json",
        {
            "status": "completed_reserved_diagnostic",
            "freeze_sha256": "a" * 64,
            "generated_at_utc": (as_of - timedelta(hours=2)).isoformat(),
            "inputs": {"retrieved_at_utc": (as_of - timedelta(days=1)).isoformat()},
            "artifacts": {
                "features": store_frame(run, "features", history.filter(pl.col("season") == 2025))
            },
        },
    )
    write_json(
        root / "access.json",
        {
            "status": "complete",
            "freeze_sha256": "a" * 64,
            "report_sha256": sha256_file(run / "manifest.json"),
        },
    )
    return directory


def test_offline_cli_preserves_sources_and_snapshots_detect_corruption(tmp_path, monkeypatch):
    import nflreadpy as nfl

    def no_network(*args, **kwargs):
        raise AssertionError("Offline feature command must never download/rescore")

    for name in ("load_schedules", "load_depth_charts", "load_player_stats"):
        monkeypatch.setattr(nfl, name, no_network)
    data = tmp_path / "data"
    save_inputs(data, datetime.now(UTC))
    before = {path: path.read_bytes() for path in data.rglob("*") if path.is_file()}
    report_dir = tmp_path / "report"
    assert main(["current-features", "--data-dir", str(data), "--report-dir", str(report_dir)]) == 0
    report = read_json(report_dir / "current_features.json")
    assert report["counts"]["candidate_rows"] == 3
    first = next((data / "prospective").iterdir())
    assert read_feature_snapshot(first) == report
    second = save_feature_snapshot(data, report)
    assert first != second
    assert all(path.read_bytes() == contents for path, contents in before.items())
    (second / "features.json").write_bytes(b"changed")
    with pytest.raises(DataQualityError, match="checksum"):
        read_feature_snapshot(second)


@pytest.mark.parametrize(
    "change",
    ["pending", "report_corrupt", "features_corrupt", "future_snapshot", "wrong_stats_season"],
)
def test_verified_loader_fails_closed(tmp_path, change):
    save_inputs(tmp_path)
    root = tmp_path / "holdout" / "2025"
    registry = read_json(root / "access.json")
    if change == "pending":
        registry["status"] = "failed"
        write_json(root / "access.json", registry)
    elif change == "report_corrupt":
        (root / ("run-" + "a" * 64) / "manifest.json").write_bytes(b"changed")
    elif change == "features_corrupt":
        next((root / ("run-" + "a" * 64)).glob("*.parquet")).write_bytes(b"changed")
    elif change == "future_snapshot":
        with pytest.raises(ValueError, match="retrieved after"):
            load_prior_history(tmp_path, as_of=AS_OF - timedelta(days=2))
        return
    else:
        directory = tmp_path / "raw" / "current_stats_2026"
        metadata = read_json(directory / "manifest.json")
        metadata["season"] = 2025
        write_json(directory / "manifest.json", metadata)
    with pytest.raises(ValueError):
        load_feature_report(tmp_path, as_of=AS_OF)


def test_current_features_ui_and_repeated_save(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    save_inputs(tmp_path, datetime.now(UTC))
    monkeypatch.setenv("NFL_PROP_DATA_DIR", str(tmp_path))
    app = AppTest.from_file(Path(__file__).parents[1] / "app.py", default_timeout=20).run()
    app.radio(key="page").set_value("Current features").run()
    assert not app.exception and not app.error
    assert app.metric[0].value == "3"
    assert len(app.dataframe[0].value) == 11
    app.button(key="save_features").click().run()
    first = {path: path.read_bytes() for path in (tmp_path / "prospective").rglob("*.json")}
    app.button(key="save_features").click().run()
    assert len(list((tmp_path / "prospective").iterdir())) == 2
    assert all(path.read_bytes() == contents for path, contents in first.items())
