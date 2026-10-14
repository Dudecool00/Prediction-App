from pathlib import Path

import polars as pl
import pytest
from polars.testing import assert_frame_equal
from test_team_scores import team_cache as team_cache
from test_team_scores import team_sources as team_sources

from nfl_prop_model.cli import main
from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.storage import read_json, store_frame, write_json
from nfl_prop_model.data.team_games import build_team_games
from nfl_prop_model.data.team_playbook import (
    aggregate_playbook,
    enrich_plays,
    load_playbook_cache,
)
from nfl_prop_model.features.team_epa import matchup_features
from nfl_prop_model.features.team_playbook import PLAY_FEATURES, add_playbook_features
from nfl_prop_model.modeling.team_playbook import load_playbook_evaluation, predict_playbook_models


@pytest.fixture
def expanded_sources(team_sources):
    pbp, schedules = team_sources
    return pbp.with_columns(
        pl.when(pl.col("qb_dropback") == 1).then(20.0).otherwise(10.0).alias("yards_gained"),
        pl.lit(0).alias("sack"),
        pl.lit(0).alias("interception"),
        pl.lit(0).alias("fumble_lost"),
    ), schedules


def test_play_exposure_defense_mirror_and_unknowns(expanded_sources):
    pbp, schedules = expanded_sources
    teams, _, _ = build_team_games(pbp, schedules)
    modified = pbp.with_columns(
        pl.when(pl.col("play_id") % 7 == 0)
        .then(None)
        .otherwise(pl.col("yards_gained"))
        .alias("yards_gained"),
        pl.when(pl.col("play_id") % 7 == 1).then(1).otherwise(0).alias("interception"),
        pl.when(pl.col("play_id") % 7 == 1).then(1).otherwise(0).alias("fumble_lost"),
    )
    expanded, audit = aggregate_playbook(modified, teams)
    home = expanded.filter(pl.col("team") == "A").row(0, named=True)
    assert home["off_success_count"] == 3 and home["def_success_count"] == 0
    assert home["off_explosive_dropback_exposure"] == 1
    assert home["off_explosive_dropback_count"] == 1
    assert home["off_explosive_rush_count"] == 1
    assert home["off_turnover_count"] == 1  # Two flags on one play count once.
    assert home["off_success_exposure"] == 3
    assert audit["missing_yards"] == expanded.height
    unknown = pbp.with_columns(pl.lit(None, dtype=pl.Int64).alias("sack"))
    expanded, _ = aggregate_playbook(unknown, teams)
    assert expanded["off_sack_exposure"].sum() == 0
    with pytest.raises(DataQualityError, match="Invalid sack"):
        aggregate_playbook(pbp.with_columns(pl.lit(2).alias("sack")), teams)


def test_enrichment_retains_original_epa_and_rejects_bad_join(expanded_sources):
    expanded, _ = expanded_sources
    original = expanded.drop("yards_gained", "sack", "interception", "fumble_lost")
    revised = expanded.with_columns(pl.lit(999.0).alias("epa"))
    assert_frame_equal(enrich_plays(original, revised).select(original.columns), original)
    with pytest.raises(DataQualityError, match="missing canonical play IDs"):
        enrich_plays(original, expanded.slice(1))
    with pytest.raises(DataQualityError, match="identities disagree"):
        enrich_plays(original, expanded.with_columns(pl.lit("WRONG").alias("home_team")))


def test_unavailable_play_and_strength_data_cannot_change_features(expanded_sources):
    pbp, schedules = expanded_sources
    teams, games, _ = build_team_games(pbp, schedules)
    expanded, _ = aggregate_playbook(pbp, teams)
    game = games.filter((pl.col("season") == 2022) & (pl.col("week") == 5)).head(1)
    features = matchup_features(teams, game, schedule_history=games)
    cutoff = features["prediction_time_utc"][0]
    expected = add_playbook_features(expanded, features)
    poisoned = expanded.with_columns(
        pl.when(pl.col("result_available_at_utc") >= cutoff)
        .then(999999)
        .otherwise(pl.col(name))
        .alias(name)
        for name in expanded.columns
        if name.endswith(("_count", "_exposure", "_epa_sum"))
    )
    assert_frame_equal(add_playbook_features(poisoned, features), expected)
    first = matchup_features(teams, games.head(1), schedule_history=games)
    no_history = add_playbook_features(expanded, first)
    assert no_history["home_sos_off_season"][0] is None
    assert no_history["home_off_sack_prior"][0] is None


def test_schedule_strength_counts_previously_faced_opponents(expanded_sources):
    pbp, schedules = expanded_sources
    teams, games, _ = build_team_games(pbp, schedules)
    expanded, _ = aggregate_playbook(pbp, teams)
    game = games.filter((pl.col("season") == 2022) & (pl.col("week") == 5)).head(1)
    features = matchup_features(teams, game, schedule_history=games)
    base = add_playbook_features(expanded, features)
    # A faced B four times this season. Replacing those opponents with strong C must alter SOS.
    changed = expanded.with_columns(
        pl.when((pl.col("team") == "A") & (pl.col("season") == 2022) & (pl.col("week") < 5))
        .then(pl.lit("C"))
        .otherwise(pl.col("opponent_team"))
        .alias("opponent_team"),
        pl.when(pl.col("team") == "C")
        .then(pl.col("off_epa_sum") + 50)
        .otherwise(pl.col("off_epa_sum"))
        .alias("off_epa_sum"),
    )
    result = add_playbook_features(changed, features)
    assert result["home_sos_off_season"][0] != base["home_sos_off_season"][0]
    assert result["home_off_success_prior"][0] == base["home_off_success_prior"][0]


def test_model_rejects_unavailable_or_nonfinite_inputs(expanded_sources):
    pbp, schedules = expanded_sources
    teams, games, _ = build_team_games(pbp, schedules)
    expanded, _ = aggregate_playbook(pbp, teams)
    features = add_playbook_features(expanded, matchup_features(teams, games))
    test = features.filter(pl.col("season") == 2022).head(1)
    train = features.filter(pl.col("result_available_at_utc") < test["prediction_time_utc"][0])
    bad = test.with_columns(
        pl.col("prediction_time_utc").alias("playbook_history_available_at_utc")
    )
    with pytest.raises(DataQualityError, match="unavailable"):
        predict_playbook_models(train, bad)
    with pytest.raises(DataQualityError, match="finite"):
        predict_playbook_models(
            train, test.with_columns(pl.lit(float("inf")).alias(PLAY_FEATURES[0]))
        )


def test_playbook_cli_backtest_and_snapshot_guards(team_cache, expanded_sources, monkeypatch):
    data, _, _, _ = team_cache
    pbp, _ = expanded_sources
    for season in (2020, 2021, 2022):
        directory = data / "raw" / "team_playbook" / str(season)
        write_json(
            directory / "manifest.json",
            {
                "season": season,
                "dataset": store_frame(directory, "plays", pbp.filter(pl.col("season") == season)),
            },
        )
    assert (
        main(["team-playbook-build", "--data-dir", str(data), "--seasons", "2020", "2021", "2022"])
        == 0
    )
    assert load_playbook_cache(data)[0].height == 216
    assert (
        main(
            [
                "team-playbook-evaluate",
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
    report, predictions = load_playbook_evaluation(data)
    assert predictions.height == 36 * 2 * 6
    assert all(
        row["latest_training_result_available_utc"] < row["cutoff_utc"] for row in report["folds"]
    )
    assert (predictions["predicted_home_margin"] == -predictions["predicted_away_margin"]).all()
    assert (
        (
            predictions["predicted_home_score"]
            - predictions["predicted_away_score"]
            - predictions["predicted_home_margin"]
        ).abs()
        < 1e-10
    ).all()
    from nfl_prop_model.modeling import team_playbook

    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    from nfl_prop_model.modeling.team_scores import save_team_evaluation

    monkeypatch.setenv("NFL_PROP_DATA_DIR", str(data))
    save_team_evaluation(data, data / "scores", evaluation_seasons=(2022,))
    app = AppTest.from_file(Path(__file__).parents[1] / "app.py", default_timeout=30).run()
    app.radio(key="page").set_value("Team backtest").run()
    assert not app.exception and not app.error
    assert any(expander.label == "Play-by-play and schedule strength" for expander in app.expander)
    frames = [frame.value for frame in app.dataframe]
    assert any("Faced offense EPA/play (season)" in frame.columns for frame in frames)
    assert any(
        "EPA + plays + opponent strength + schedule" in frame["Model"].to_list()
        for frame in frames
        if "Model" in frame.columns
    )
    path = data / "processed" / "team_playbook_scores" / report["artifacts"]["predictions"]["file"]
    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        load_playbook_evaluation(data)

    monkeypatch.setattr(team_playbook, "playbook_model_source_hash", lambda: "changed")
    with pytest.raises(DataQualityError, match="model changed"):
        load_playbook_evaluation(data)
    manifest_path = data / "processed" / "team_playbook" / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["playbook_source_sha256"] = "changed"
    write_json(manifest_path, manifest)
    with pytest.raises(DataQualityError, match="source changed"):
        load_playbook_cache(data)
