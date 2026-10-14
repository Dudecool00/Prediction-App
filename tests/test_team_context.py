from datetime import timedelta
from pathlib import Path

import polars as pl
import pytest
from polars.testing import assert_frame_equal
from test_team_scores import team_cache as team_cache
from test_team_scores import team_sources as team_sources

from nfl_prop_model.cli import main
from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.storage import read_json, store_frame, write_json
from nfl_prop_model.data.team_context import (
    build_context_cache,
    load_context_cache,
    prepare_context,
)
from nfl_prop_model.data.team_games import build_team_games
from nfl_prop_model.features.team_context import (
    INJURY_FEATURES,
    QB_FEATURES,
    add_context_features,
)
from nfl_prop_model.features.team_epa import matchup_features
from nfl_prop_model.modeling.team_context import (
    CONTEXT_MODELS,
    load_context_evaluation,
    predict_context_models,
)


@pytest.fixture
def context_sources(team_sources):
    teams, games, _ = build_team_games(*team_sources)
    stats, injuries = [], []
    for row in teams.to_dicts():
        player = f"{row['team']}-starter"
        for identity, attempts in ((player, 30), (f"{row['team']}-backup", 2)):
            stats.append(
                {
                    "game_id": row["game_id"],
                    "season": row["season"],
                    "week": row["week"],
                    "season_type": "REG",
                    "team": row["team"],
                    "opponent_team": row["opponent_team"],
                    "player_id": identity,
                    "position": "QB",
                    "attempts": attempts,
                    "sacks_suffered": 2,
                    "passing_epa": attempts * 0.1,
                }
            )
        for identity, position, reason in (
            (player, "QB", "Ankle"),
            (f"{row['team']}-ol", "T", "Rest"),
        ):
            injuries.append(
                {
                    "season": row["season"],
                    "week": row["week"],
                    "game_type": "REG",
                    "team": row["team"],
                    "gsis_id": identity,
                    "position": position,
                    "report_status": "Out",
                    "report_primary_injury": reason,
                    "date_modified": row["kickoff_utc"] - timedelta(days=3),
                }
            )
    return pl.DataFrame(stats), pl.DataFrame(injuries), teams, games


@pytest.fixture
def context_cache(team_cache, context_sources):
    data, _, _, _ = team_cache
    stats, injuries, _, _ = context_sources
    for season in (2020, 2021, 2022):
        directory = data / "raw" / "team_context" / str(season)
        write_json(
            directory / "manifest.json",
            {
                "season": season,
                "datasets": {
                    "player_stats": store_frame(
                        directory, "stats", stats.filter(pl.col("season") == season)
                    ),
                    "injuries": store_frame(
                        directory, "injuries", injuries.filter(pl.col("season") == season)
                    ),
                },
            },
        )
    build_context_cache(data, [2020, 2021, 2022])
    return data


def test_context_preparation_preserves_missingness_and_validates_identities(context_sources):
    stats, injuries, teams, games = context_sources
    extra = injuries.head(1).with_columns(pl.lit(None).cast(pl.String).alias("gsis_id"))
    qb, reports, audit = prepare_context(
        stats,
        pl.concat([injuries, extra]).with_columns(pl.col("season", "week").cast(pl.Float64)),
        teams,
        games,
    )
    assert qb.height == 2 * teams.height and reports.height == injuries.height
    assert audit["injury_rows_missing_identity_or_time"] == 1
    assert reports.schema["season"] == pl.Int32
    bad = stats.with_columns(pl.lit("wrong").alias("team"))
    with pytest.raises(DataQualityError, match="team does not match"):
        prepare_context(bad, injuries, teams, games)
    naive = injuries.with_columns(pl.col("date_modified").dt.replace_time_zone(None))
    with pytest.raises(DataQualityError, match="explicitly use UTC"):
        prepare_context(stats, naive, teams, games)
    invalid = injuries.with_columns(pl.lit(2022.5).alias("season"))
    with pytest.raises(DataQualityError, match="known integers"):
        prepare_context(stats, invalid, teams, games)


def test_context_gates_reports_at_cutoff_and_does_not_carry_previous_season_qb(context_sources):
    stats, injuries, teams, games = context_sources
    qb, reports, _ = prepare_context(stats, injuries, teams, games)
    target = games.filter((pl.col("season") == 2022) & (pl.col("week") == 2)).head(1)
    cutoff = target["kickoff_utc"][0] - timedelta(hours=1)
    canonical = matchup_features(teams, target, prediction_time=cutoff, schedule_history=games)
    before = add_context_features(qb, reports, teams, canonical)
    assert before["home_qb_reported_out"][0] == 1
    assert before["home_players_reported_rest_out"][0] == 1
    assert before["home_qb_primary_share_last"][0] == pytest.approx(32 / 36)
    # A report becoming available exactly at the cutoff remains unknown.
    exact = reports.with_columns(pl.lit(cutoff).alias("available_at_utc"))
    absent = add_context_features(qb, exact, teams, canonical)
    assert absent["home_injury_observed_players"][0] == 0
    assert absent["home_qb_reported_out"][0] is None
    assert absent["home_ol_reported_out"][0] is None
    assert absent.select(pl.col(INJURY_FEATURES).is_null().all()).row(0) == (True,) * len(
        INJURY_FEATURES
    )
    first = games.filter((pl.col("season") == 2022) & (pl.col("week") == 1)).head(1)
    opening = add_context_features(qb, reports, teams, matchup_features(teams, first))
    assert opening["home_last_primary_passer_id"][0] is None
    assert opening["home_qb_epa_prior"][0] is None


def test_context_features_ignore_current_future_results_and_late_reports(context_sources):
    stats, injuries, teams, games = context_sources
    qb, reports, _ = prepare_context(stats, injuries, teams, games)
    target = games.filter((pl.col("season") == 2022) & (pl.col("week") == 5)).head(1)
    cutoff = target["kickoff_utc"][0] - timedelta(hours=1)
    canonical = matchup_features(teams, target, prediction_time=cutoff, schedule_history=games)
    before = add_context_features(qb, reports, teams, canonical)
    poisoned = qb.with_columns(
        [
            pl.when(pl.col("result_available_at_utc") >= cutoff)
            .then(pl.lit(999999.0))
            .otherwise(pl.col(name))
            .alias(name)
            for name in ("passing_plays", "passing_epa")
        ]
    )
    late = reports.with_columns(
        pl.lit(cutoff + timedelta(hours=1)).alias("available_at_utc"),
        pl.lit("Note").alias("report_status"),
    )
    after = add_context_features(poisoned, pl.concat([reports, late]), teams, canonical)
    assert_frame_equal(before, after)


def test_context_primary_ties_and_ambiguous_note_status_stay_unknown(context_sources):
    stats, injuries, teams, games = context_sources
    qb, reports, _ = prepare_context(stats, injuries, teams, games)
    target = games.filter((pl.col("season") == 2022) & (pl.col("week") == 2)).head(1)
    cutoff = target["kickoff_utc"][0] - timedelta(hours=1)
    canonical = matchup_features(teams, target, prediction_time=cutoff, schedule_history=games)
    tied = qb.with_columns(pl.lit(32.0).alias("passing_plays"))
    assert (
        add_context_features(tied, reports, teams, canonical)["home_last_primary_passer_id"][0]
        is None
    )
    notes = reports.with_columns(pl.lit("Note").alias("report_status"))
    assert add_context_features(qb, notes, teams, canonical)["home_qb_reported_out"][0] is None


def test_context_model_rejects_unavailable_features_and_preserves_control(context_sources):
    stats, injuries, teams, games = context_sources
    qb, reports, _ = prepare_context(stats, injuries, teams, games)
    features = add_context_features(qb, reports, teams, matchup_features(teams, games))
    test = features.filter(pl.col("season") == 2022).head(2)
    train = features.filter(pl.col("result_available_at_utc") < test["prediction_time_utc"][0])
    invalid = test.with_columns(
        pl.col("prediction_time_utc").alias("home_injury_history_available_at_utc")
    )
    with pytest.raises(DataQualityError, match="unavailable"):
        predict_context_models(train, invalid)
    invalid = test.with_columns(pl.lit(float("inf")).alias(QB_FEATURES[0]))
    with pytest.raises(DataQualityError, match="finite"):
        predict_context_models(train, invalid)
    values = predict_context_models(train, test)
    assert set(CONTEXT_MODELS).issubset(values)
    assert len(values["ridge_epa_scores"]) == test.height


def test_context_cli_comparison_same_cohorts_consistent_scores_and_artifact_guards(
    context_cache, monkeypatch
):
    data = context_cache
    assert (
        main(
            [
                "team-context-evaluate",
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
    report, predictions = load_context_evaluation(data)
    assert predictions.height == 36 * 2 * (3 + len(CONTEXT_MODELS))
    assert set(predictions.group_by("model", "horizon").len()["len"]) == {36}
    assert predictions.filter(
        pl.col("predicted_home_margin")
        != (pl.col("predicted_home_score") - pl.col("predicted_away_score")).round(1)
    ).is_empty()
    assert (data / "reports" / "team_context.md").is_file()
    assert len(report["coverage"]) == 2
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    from nfl_prop_model.modeling.team_scores import save_team_evaluation

    monkeypatch.setenv("NFL_PROP_DATA_DIR", str(data))
    save_team_evaluation(data, data / "scores", evaluation_seasons=(2022,))
    app = AppTest.from_file(Path(__file__).parents[1] / "app.py", default_timeout=30).run()
    app.radio(key="page").set_value("Team backtest").run()
    assert not app.exception and not app.error
    assert any(expander.label == "QB and injury context" for expander in app.expander)
    comparison = [frame.value for frame in app.dataframe if "Model" in frame.value.columns]
    assert any("EPA + QB history + injuries" in frame["Model"].to_list() for frame in comparison)
    with monkeypatch.context() as guard:
        guard.setattr(
            "nfl_prop_model.modeling.team_context.context_model_source_hash", lambda: "changed"
        )
        with pytest.raises(DataQualityError, match="changed"):
            load_context_evaluation(data)
    path = data / "processed" / "team_context_scores" / report["artifacts"]["predictions"]["file"]
    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        load_context_evaluation(data)
    manifest = read_json(data / "processed" / "team_context" / "manifest.json")
    manifest["context_source_sha256"] = "changed"
    write_json(data / "processed" / "team_context" / "manifest.json", manifest)
    with pytest.raises(DataQualityError, match="changed"):
        load_context_cache(data)
