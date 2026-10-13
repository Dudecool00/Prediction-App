import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from nfl_prop_model.cli import main
from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.storage import load_frame, read_json, store_frame, write_json
from nfl_prop_model.data.team_games import build_team_games, team_source_hash
from nfl_prop_model.features.team_epa import TEAM_FEATURES, matchup_features
from nfl_prop_model.modeling.team_scores import (
    consistent_scores,
    evaluate_team_scores,
    load_team_evaluation,
    predict_models,
    save_team_evaluation,
    save_team_forecast,
)


@pytest.fixture
def team_sources():
    schedules, plays = [], []
    for season in (2020, 2021, 2022):
        for week in range(1, 19):
            day = datetime(season, 9, 6, 17, tzinfo=UTC) + timedelta(weeks=week - 1)
            for home, away in (("A", "B"), ("C", "D")):
                game_id = f"{season}_{week:02d}_{away}_{home}"
                schedules.append(
                    {
                        "game_id": game_id,
                        "season": season,
                        "week": week,
                        "game_type": "REG",
                        "gameday": day.strftime("%Y-%m-%d"),
                        "gametime": "13:00",
                        "home_team": home,
                        "away_team": away,
                        "home_score": 20 + week % 12,
                        "away_score": 14 + week % 10,
                        "location": "Neutral" if week == 3 else "Home",
                    }
                )
                for team, other, sign in ((home, away, 1), (away, home, -1)):
                    for index, kind in enumerate(
                        ("pass", "scramble", "run", "kneel", "spike", "no_play", "missing")
                    ):
                        plays.append(
                            {
                                "game_id": game_id,
                                "play_id": len(plays),
                                "season": season,
                                "week": week,
                                "season_type": "REG",
                                "home_team": home,
                                "away_team": away,
                                "posteam": team,
                                "defteam": other,
                                "epa": None if kind == "missing" else float(sign * (index + 1)),
                                "pass": int(
                                    kind in {"pass", "scramble", "spike", "no_play", "missing"}
                                ),
                                "rush": int(kind in {"scramble", "run", "kneel"}),
                                "qb_dropback": int(
                                    kind in {"pass", "scramble", "spike", "no_play", "missing"}
                                ),
                                "qb_kneel": int(kind == "kneel"),
                                "qb_spike": int(kind == "spike"),
                                "two_point_attempt": 0,
                                "play_type": "no_play"
                                if kind == "no_play"
                                else "pass"
                                if kind in {"pass", "missing"}
                                else "run",
                            }
                        )
    return pl.DataFrame(plays), pl.DataFrame(schedules)


@pytest.fixture
def team_cache(tmp_path, team_sources):
    teams, games, audit = build_team_games(*team_sources)
    directory = tmp_path / "processed" / "team_epa"
    manifest = {
        "format_version": 1,
        "seasons": [2020, 2021, 2022],
        "audit": audit,
        "pipeline_source_sha256": team_source_hash(),
        "sources": [],
        "artifacts": {
            "team_games": store_frame(directory, "team_games", teams),
            "games": store_frame(directory, "games", games),
        },
    }
    write_json(directory / "manifest.json", manifest)
    return tmp_path, teams, games, manifest


def test_epa_denominators_signs_and_scramble_classification(team_sources):
    teams, games, audit = build_team_games(*team_sources)
    home = teams.filter(pl.col("team") == "A").row(0, named=True)
    assert teams.height == 2 * games.height == 216
    assert home["off_plays"] == 3
    assert home["off_epa_sum"] == 6
    assert home["off_epa_per_play"] == 2
    assert home["def_epa_per_play"] == -2
    assert home["off_dropback_plays"] == 2
    assert home["off_dropback_epa_sum"] == 3
    assert home["off_rush_plays"] == 1
    assert home["off_rush_epa_sum"] == 3
    assert audit["excluded_missing_or_nonfinite_epa"] == 216


def test_schedule_first_and_incomplete_epa_fail_visibly(team_sources):
    pbp, schedules = team_sources
    with pytest.raises(DataQualityError, match="Missing eligible EPA"):
        build_team_games(pbp.filter(pl.col("posteam") != "A"), schedules)
    with pytest.raises(DataQualityError, match="duplicate"):
        build_team_games(pl.concat([pbp, pbp.head(1)]), schedules)
    with pytest.raises(DataQualityError, match="identity"):
        build_team_games(pbp.with_columns(pl.lit("WRONG").alias("home_team")), schedules)


@pytest.mark.parametrize("horizon", ["early_week", "pregame_refresh"])
def test_current_and_future_outcomes_do_not_enter_features_or_training(team_sources, horizon):
    teams, games, _ = build_team_games(*team_sources)
    features = matchup_features(teams, games, horizon=horizon)
    first = features.filter(pl.col("season") == 2022).head(2)
    cutoff = first["prediction_time_utc"].min()
    changed = teams.with_columns(
        pl.when(pl.col("result_available_at_utc") >= cutoff)
        .then(10000.0)
        .otherwise(pl.col(name))
        .alias(name)
        for name in ("off_epa_sum", "def_epa_sum", "points_scored", "points_allowed")
    )
    changed_features = matchup_features(changed, games, horizon=horizon)
    original_before = features.filter(pl.col("prediction_time_utc") <= cutoff)
    changed_before = changed_features.filter(pl.col("prediction_time_utc") <= cutoff)
    assert_frame_equal(
        original_before.select("game_id", *TEAM_FEATURES),
        changed_before.select("game_id", *TEAM_FEATURES),
    )
    train = features.filter(pl.col("result_available_at_utc") < cutoff)
    changed_train = changed_features.filter(pl.col("result_available_at_utc") < cutoff)
    original_predictions = predict_models(train, first)
    changed_predictions = predict_models(changed_train, changed_before.tail(2))
    for model in original_predictions:
        assert original_predictions[model] == pytest.approx(changed_predictions[model])


def test_cutoffs_neutral_sites_cold_start_and_order(team_sources):
    teams, games, _ = build_team_games(*team_sources)
    early = matchup_features(teams, games)
    refresh = matchup_features(teams, games, horizon="pregame_refresh")
    first = early.row(0, named=True)
    assert first["home_prior_games"] == 0
    assert first["home_history_available_at_utc"] is None
    assert first["rest_difference"] is None
    assert first["prediction_time_utc"].weekday() == 1
    assert early.filter(pl.col("week") == 3)["home_field"].to_list() == [0.0] * 6
    assert refresh.select(
        (pl.col("kickoff_utc") - pl.col("prediction_time_utc")).dt.total_hours().unique()
    ).to_series().to_list() == [1]
    assert_frame_equal(early, matchup_features(teams.reverse(), games.reverse()))
    assert not set(TEAM_FEATURES).intersection(
        {"home_score", "away_score", "spread_line", "target_home_margin"}
    )


def test_scores_margin_sign_and_rounding():
    values = consistent_scores(31.2, 27.5)
    assert values["predicted_home_margin"] == 3.7
    assert values["predicted_away_margin"] == -3.7
    assert consistent_scores(31.24, 27.55)["predicted_home_margin"] == 3.6
    assert consistent_scores(14.0, 21.0)["predicted_home_margin"] == -7
    assert consistent_scores(20.0, 20.0)["predicted_home_margin"] == 0
    assert consistent_scores(-5, 3)["predicted_home_score"] == 0
    with pytest.raises(DataQualityError, match="Non-finite"):
        consistent_scores(float("nan"), 20)


def test_score_cache_reader_does_not_import_estimators():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import nfl_prop_model.modeling.team_scores; "
            "assert 'sklearn' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


def test_scoring_baseline_handles_home_field_and_neutral_sites(team_sources):
    teams, games, _ = build_team_games(*team_sources)
    features = matchup_features(teams, games)
    home = features.filter((pl.col("season") == 2022) & (pl.col("week") == 1)).head(1)
    neutral = home.with_columns(
        pl.lit("neutral-example").alias("game_id"), pl.lit(0.0).alias("home_field")
    )
    train = features.filter(pl.col("result_available_at_utc") < home["prediction_time_utc"][0])
    values = predict_models(train, pl.concat([home, neutral]))["prior_team_scores"]
    assert values[0, 0] > values[1, 0]
    assert values[0, 1] < values[1, 1]
    assert sum(values[0]) == pytest.approx(sum(values[1]))


def test_schedule_rest_survives_unavailable_monday_epa(team_sources):
    teams, games, _ = build_team_games(*team_sources)
    target = games.filter((pl.col("season") == 2022) & (pl.col("week") == 2)).head(1)
    cutoff = matchup_features(teams, target)["prediction_time_utc"][0]
    monday = cutoff - timedelta(hours=15)
    previous = games.filter(
        (pl.col("season") == 2022) & (pl.col("week") == 1) & (pl.col("home_team") == "A")
    )["game_id"][0]
    schedules = games.with_columns(
        pl.when(pl.col("game_id") == previous)
        .then(monday)
        .otherwise(pl.col("kickoff_utc"))
        .alias("kickoff_utc")
    )
    delayed = teams.with_columns(
        pl.when(pl.col("game_id") == previous)
        .then(monday + timedelta(hours=24))
        .otherwise(pl.col("result_available_at_utc"))
        .alias("result_available_at_utc")
    )
    features = matchup_features(delayed, target, schedule_history=schedules).row(0, named=True)
    assert features["home_rest_game_id"] == previous
    assert features["home_rest_days"] == pytest.approx(
        (target["kickoff_utc"][0] - monday).total_seconds() / 86400
    )
    assert features["home_prior_games"] == 36  # Monday's EPA is not eligible.
    assert features["home_history_available_at_utc"] < cutoff


def test_uncompleted_intervening_game_leaves_schedule_rest_unknown(team_sources):
    teams, games, _ = build_team_games(*team_sources)
    target = games.filter((pl.col("season") == 2022) & (pl.col("week") == 2)).head(1)
    previous = games.filter(
        (pl.col("season") == 2022) & (pl.col("week") == 1) & (pl.col("home_team") == "A")
    )["game_id"][0]
    schedules = games.with_columns(
        pl.when(pl.col("game_id") == previous).then(None).otherwise(pl.col(name)).alias(name)
        for name in ("home_score", "away_score")
    )
    row = matchup_features(teams, target, schedule_history=schedules).row(0, named=True)
    assert row["home_rest_days"] is None and row["rest_difference"] is None


def test_weekly_evaluation_scores_games_once_and_keeps_horizons_separate(team_sources):
    teams, games, _ = build_team_games(*team_sources)
    predictions, metrics, folds = evaluate_team_scores(teams, games, evaluation_seasons=(2022,))
    assert predictions.height == 36 * 3 * 2
    overall = metrics.filter(pl.col("scope") == "overall")
    assert overall.height == 6
    assert overall["games"].to_list() == [36] * 6
    assert all(
        datetime.fromisoformat(fold["latest_training_result_available_utc"])
        < datetime.fromisoformat(fold["cutoff_utc"])
        for fold in folds
    )
    for row in predictions.to_dicts():
        assert row["predicted_home_margin"] == round(
            row["predicted_home_score"] - row["predicted_away_score"], 1
        )
        assert row["predicted_away_margin"] == -row["predicted_home_margin"]


def test_saved_report_hashes_offline_cli_and_corruption(team_cache):
    data, _, _, _ = team_cache
    assert (
        main(
            [
                "team-evaluate",
                "--data-dir",
                str(data),
                "--report-dir",
                str(data / "reports"),
                "--evaluation-seasons",
                "2022",
            ]
        )
        == 0
    )
    report, predictions = load_team_evaluation(data)
    assert predictions.height == 216
    assert (data / "reports" / "team_scores.md").is_file()
    path = data / "processed" / "team_scores" / report["artifacts"]["predictions"]["file"]
    path.write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="checksum"):
        load_team_evaluation(data)


def test_forecast_rejects_future_source_and_stale_current_cache(team_cache):
    data, _, _, manifest = team_cache
    now = datetime.now(UTC)
    directory = data / "processed" / "team_epa"
    manifest["sources"] = [
        {"season": now.year, "retrieved_at_utc": (now + timedelta(days=1)).isoformat()}
    ]
    write_json(directory / "manifest.json", manifest)
    with pytest.raises(DataQualityError, match="retrieved after"):
        save_team_forecast(data)
    manifest["sources"][0]["retrieved_at_utc"] = (now - timedelta(days=2)).isoformat()
    write_json(directory / "manifest.json", manifest)
    with pytest.raises(DataQualityError, match="under 24"):
        save_team_forecast(data)


def test_team_app_defaults_and_historical_score_display(team_cache, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    data, _, _, _ = team_cache
    monkeypatch.setenv("NFL_PROP_DATA_DIR", str(data))
    save_team_evaluation(data, data / "reports", evaluation_seasons=(2022,))
    app = AppTest.from_file(Path(__file__).parents[1] / "app.py", default_timeout=30).run()
    assert not app.exception and not app.error
    assert app.radio(key="page").value == "Team scores"
    app.radio(key="page").set_value("Team backtest").run()
    assert not app.exception and not app.error
    assert len(app.metric) == 3
    assert "margin" in app.metric[0].delta
    assert float(app.metric[1].value) - float(app.metric[0].value) == pytest.approx(
        float(app.metric[1].delta.split()[0])
    )


def test_prospective_scores_append_only_and_game_refresh_window(team_cache, monkeypatch):
    data, _, games, manifest = team_cache
    now = datetime.now(UTC)
    kickoff = now + timedelta(days=2)
    future = games.head(1).with_columns(
        pl.lit("future-A-B").alias("game_id"),
        pl.lit(now.year).alias("season"),
        pl.lit(kickoff).alias("kickoff_utc"),
        pl.lit(kickoff + timedelta(hours=24)).alias("result_available_at_utc"),
        pl.lit(None).cast(pl.Float64).alias("home_score"),
        pl.lit(None).cast(pl.Float64).alias("away_score"),
        pl.lit(None).cast(pl.Float64).alias("target_home_margin"),
    )
    directory = data / "processed" / "team_epa"
    manifest["artifacts"]["games"] = store_frame(
        directory, "games", pl.concat([games, future], how="vertical_relaxed")
    )
    manifest["sources"] = [
        {"season": now.year, "retrieved_at_utc": (now - timedelta(minutes=1)).isoformat()}
    ]
    write_json(directory / "manifest.json", manifest)
    first = save_team_forecast(data)
    original = first.read_bytes()
    second = save_team_forecast(data)
    assert first != second and first.read_bytes() == original
    report = read_json(first)
    predictions = load_frame(first.parent, report["artifacts"]["predictions"])
    row = predictions.row(0, named=True)
    assert row["predicted_home_margin"] == round(
        row["predicted_home_score"] - row["predicted_away_score"], 1
    )
    assert row["latest_training_result_available_utc"] < datetime.fromisoformat(report["as_of_utc"])
    with pytest.raises(DataQualityError, match="1-24"):
        save_team_forecast(data, horizon="pregame_refresh")
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("NFL_PROP_DATA_DIR", str(data))
    app = AppTest.from_file(Path(__file__).parents[1] / "app.py", default_timeout=30).run()
    assert not app.exception and not app.error
    assert len(app.metric) == 3


def test_experiment_features_ignore_unavailable_and_current_game_outcomes(team_sources):
    from nfl_prop_model.features.team_experiments import (
        ADJUSTED_FEATURES,
        VOLUME_FEATURES,
        add_experiment_features,
    )

    teams, games, _ = build_team_games(*team_sources)
    features = matchup_features(teams, games).filter(pl.col("season") == 2022).head(2)
    cutoff = features["prediction_time_utc"][0]
    before = add_experiment_features(teams, features)
    poison = teams.with_columns(
        *[
            pl.when(pl.col("result_available_at_utc") >= cutoff)
            .then(pl.lit(99999.0))
            .otherwise(pl.col(name))
            .alias(name)
            for name in teams.columns
            if name.endswith("epa_sum") or name == "points_scored"
        ],
        *[
            pl.when(pl.col("result_available_at_utc") >= cutoff)
            .then(pl.col(name) + "-future")
            .otherwise(pl.col(name))
            .alias(name)
            for name in ("team", "opponent_team")
        ],
    )
    after = add_experiment_features(poison, features)
    assert_frame_equal(before, after)
    assert before["home_off_plays_prior"].to_list() == [3.0, 3.0]
    assert before.select(pl.col(ADJUSTED_FEATURES + VOLUME_FEATURES).is_finite().all()).row(0) == (
        True,
    ) * (len(ADJUSTED_FEATURES) + len(VOLUME_FEATURES))
    # Results that become available exactly at the cutoff are also excluded.
    exact = teams.with_columns(
        pl.when(pl.col("result_available_at_utc") >= cutoff)
        .then(pl.lit(cutoff))
        .otherwise(pl.col("result_available_at_utc"))
        .alias("result_available_at_utc")
    )
    assert_frame_equal(before, add_experiment_features(exact, features))


def test_opponent_adjustment_distinguishes_equal_epa_against_different_defenses():
    from nfl_prop_model.features.team_experiments import OpponentHistory

    rows = []
    for week in range(10):
        for index, (team, opponent, epa) in enumerate(
            (("A", "X", 0.1), ("B", "Y", 0.1), ("C", "X", 0.3), ("C", "Y", -0.3))
        ):
            row = {
                "game_id": f"{week}-{index}",
                "team": team,
                "opponent_team": opponent,
                "season": 2021,
                "result_available_at_utc": datetime(2021, 10, 1, tzinfo=UTC),
            }
            for group in ("off_", "off_dropback_", "off_rush_"):
                row[group + "plays"] = 60
                row[group + "epa_sum"] = 60 * epa
            rows.append(row)
    history = OpponentHistory(pl.DataFrame(rows))
    ratings = history.rating(2021, datetime(2021, 10, 2, tzinfo=UTC))["teams"]
    assert ratings["B"]["off_adjusted_epa"] > ratings["A"]["off_adjusted_epa"]
    assert ratings["X"]["def_adjusted_epa"] > ratings["Y"]["def_adjusted_epa"]
    empty = history.rating(2021, datetime(2021, 10, 1, tzinfo=UTC))
    assert not empty["teams"] and empty["latest_available_at_utc"] is None
    assert set(empty["fallback"].values()) == {0.0}


def test_experiment_validation_rejects_future_opponent_history_and_nonfinite_inputs(team_cache):
    from nfl_prop_model.features.team_experiments import add_experiment_features
    from nfl_prop_model.modeling.team_experiments import predict_experiments

    _, teams, games, _ = team_cache
    features = add_experiment_features(teams, matchup_features(teams, games))
    test = features.filter(pl.col("season") == 2022).head(2)
    train = features.filter(pl.col("result_available_at_utc") < test["prediction_time_utc"][0])
    invalid = test.with_columns(
        pl.col("prediction_time_utc").alias("opponent_history_available_at_utc")
    )
    with pytest.raises(DataQualityError, match="Opponent history was unavailable"):
        predict_experiments(train, invalid)
    invalid = test.with_columns(pl.lit(float("inf")).alias("home_off_plays_prior"))
    with pytest.raises(DataQualityError, match="finite"):
        predict_experiments(train, invalid)
    values = predict_experiments(train, test)
    control = predict_models(train, test)
    assert values["ridge_epa_scores"].tolist() == control["ridge_epa_scores"].tolist()


def test_team_experiment_cli_cohorts_consistency_and_checksum(team_cache, monkeypatch):
    from nfl_prop_model.modeling.team_experiments import CANDIDATE_FEATURES, load_team_experiments

    data, _, _, _ = team_cache
    assert (
        main(
            [
                "team-experiment",
                "--data-dir",
                str(data),
                "--report-dir",
                str(data / "reports"),
                "--evaluation-seasons",
                "2022",
            ]
        )
        == 0
    )
    report, predictions = load_team_experiments(data)
    with monkeypatch.context() as guarded:
        guarded.setattr(
            "nfl_prop_model.modeling.team_experiments.experiment_source_hash", lambda: "changed"
        )
        with pytest.raises(DataQualityError, match="changed"):
            load_team_experiments(data)
    assert len(report["paired_comparisons"]) == 2 * len(CANDIDATE_FEATURES)
    assert predictions.height == 36 * 2 * (3 + len(CANDIDATE_FEATURES))
    assert set(predictions.group_by("horizon", "model").len()["len"]) == {36}
    assert predictions.filter(
        pl.col("predicted_home_margin")
        != (pl.col("predicted_home_score") - pl.col("predicted_away_score")).round(1)
    ).is_empty()
    assert (data / "reports" / "team_experiments.md").is_file()
    path = data / "processed" / "team_experiments" / report["artifacts"]["predictions"]["file"]
    path.write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="checksum"):
        load_team_experiments(data)


def test_team_experiments_display_saved_error_comparison(team_cache, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    from nfl_prop_model.modeling.team_experiments import save_team_experiments

    data, _, _, _ = team_cache
    monkeypatch.setenv("NFL_PROP_DATA_DIR", str(data))
    save_team_evaluation(data, data / "scores", evaluation_seasons=(2022,))
    save_team_experiments(data, data / "experiments", evaluation_seasons=(2022,))
    app = AppTest.from_file(Path(__file__).parents[1] / "app.py", default_timeout=30).run()
    app.radio(key="page").set_value("Team backtest").run()
    assert not app.exception and not app.error
    assert any(expander.label == "Feature experiments" for expander in app.expander)
    comparisons = [frame.value for frame in app.dataframe if "Margin MAE" in frame.value.columns]
    assert any(len(frame) == 7 for frame in comparisons)
    assert any("EPA + scoring history" in frame["Model"].to_list() for frame in comparisons)
