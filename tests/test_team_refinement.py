from datetime import timedelta

import numpy as np
import polars as pl
import pytest
from polars.testing import assert_frame_equal
from test_team_scores import team_cache as team_cache
from test_team_scores import team_sources as team_sources

from nfl_prop_model.cli import main
from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.storage import store_frame, write_json
from nfl_prop_model.features.team_epa import TEAM_FEATURES, matchup_features
from nfl_prop_model.modeling.team_refinement import (
    PROFILES,
    SELECTED_MODEL,
    RefinementProfile,
    candidate_pipeline,
    complexity_audit,
    evaluate_refinements,
    load_refinements,
    predict_candidate,
    select_refinements,
)
from nfl_prop_model.modeling.team_scores import predict_models


@pytest.fixture
def refinement_cache(team_cache):
    data, teams, games, manifest = team_cache

    def earlier(frame):
        return frame.filter(pl.col("season") <= 2021).with_columns(
            (pl.col("season") - 2).alias("season"),
            ((pl.col("season") - 2).cast(pl.String) + pl.col("game_id").str.slice(4)).alias(
                "game_id"
            ),
            (pl.col("kickoff_utc") - timedelta(days=730)).alias("kickoff_utc"),
            (pl.col("result_available_at_utc") - timedelta(days=730)).alias(
                "result_available_at_utc"
            ),
        )

    teams = pl.concat([earlier(teams), teams])
    games = pl.concat([earlier(games), games])
    manifest = {
        **manifest,
        "seasons": list(range(2018, 2023)),
        "artifacts": {
            "team_games": store_frame(data / "processed/team_epa", "team_games", teams),
            "games": store_frame(data / "processed/team_epa", "games", games),
        },
    }
    write_json(data / "processed/team_epa/manifest.json", manifest)
    return data, teams, games


def test_profile_definitions_and_control_equivalence(refinement_cache):
    _, teams, games = refinement_cache
    features = matchup_features(teams, games)
    test = features.filter(pl.col("season") == 2022).head(2)
    train = features.filter(pl.col("result_available_at_utc") < test["prediction_time_utc"][0])
    np.testing.assert_allclose(
        predict_candidate(train, test, PROFILES[0]),
        predict_models(train, test)["ridge_epa_scores"],
        atol=1e-12,
    )
    assert {len(p.features) for p in PROFILES} == {8, 12, 26, 28}
    with pytest.raises(ValueError, match="unique"):
        RefinementProfile("bad", (TEAM_FEATURES[0], TEAM_FEATURES[0]))
    with pytest.raises(ValueError, match="positive"):
        RefinementProfile("bad", alpha=0)


def test_all_model_families_produce_finite_coherent_scores(refinement_cache):
    _, teams, games = refinement_cache
    features = matchup_features(teams, games)
    test = features.filter(pl.col("season") == 2022).head(2)
    train = features.filter(pl.col("result_available_at_utc") < test["prediction_time_utc"][0])
    for index in (0, 6, 8, 9, 10, 11, 12):
        values = predict_candidate(train, test, PROFILES[index])
        assert values.shape == (2, 2) and np.isfinite(values).all()
    boost = candidate_pipeline(PROFILES[10]).named_steps["regressor"].estimator
    assert boost.early_stopping is False and boost.max_leaf_nodes == 7
    from nfl_prop_model.modeling.team_refinement import predicted_scores

    class KnownMarginTotal:
        def predict(self, _):
            return np.array([[3.7, 58.7], [0, 40]])

    np.testing.assert_allclose(
        predicted_scores(KnownMarginTotal(), test, PROFILES[8]), [[31.2, 27.5], [20, 20]]
    )


def test_complexity_and_temporal_guards(refinement_cache):
    _, teams, games = refinement_cache
    features = matchup_features(teams, games)
    test = features.filter(pl.col("season") == 2022).head(1)
    train = features.filter(pl.col("result_available_at_utc") < test["prediction_time_utc"][0])
    full = complexity_audit(train, PROFILES[0])
    compact = complexity_audit(train, PROFILES[2])
    assert full["allocated_linear_coefficients_and_intercepts"] == 2 * (
        full["encoded_features"] + 1
    )
    assert compact["encoded_features"] < full["encoded_features"]
    assert 1 <= full["ridge_effective_df_per_output"] <= full["encoded_features"] + 1
    unknown = train.with_columns(
        pl.lit(test["prediction_time_utc"][0]).alias("result_available_at_utc")
    )
    with pytest.raises(DataQualityError, match="unavailable"):
        predict_candidate(unknown, test, PROFILES[0])
    with pytest.raises(DataQualityError, match="overlaps"):
        predict_candidate(pl.concat([train, test]), test, PROFILES[0])
    altered = test.with_columns(
        pl.lit(9999.0).alias("home_score"), pl.lit(7777.0).alias("away_score")
    )
    np.testing.assert_allclose(
        predict_candidate(train, test, PROFILES[2]), predict_candidate(train, altered, PROFILES[2])
    )


def test_one_standard_error_selection_is_prior_only_and_prefers_simpler(refinement_cache):
    _, teams, games = refinement_cache
    features = matchup_features(teams, games)
    lock = features.filter(pl.col("season") == 2022)["prediction_time_utc"].min()
    rows = []
    profiles = PROFILES[:3]
    for game in features.filter(pl.col("season").is_in([2020, 2021, 2022])).to_dicts():
        for index, profile in enumerate(profiles):
            error = 10 if index == 0 else (2 if game["week"] % 2 else 12) if index == 1 else 7.2
            rows.append(
                {
                    **{
                        key: game[key]
                        for key in (
                            "game_id",
                            "horizon",
                            "season",
                            "week",
                            "result_available_at_utc",
                            "home_score",
                            "away_score",
                            "target_home_margin",
                        )
                    },
                    "model": profile.name,
                    "predicted_home_score": game["home_score"] + error / 2,
                    "predicted_away_score": game["away_score"] - error / 2,
                    "predicted_home_margin": game["target_home_margin"] + error,
                }
            )
    panel = pl.DataFrame(rows)
    latest = features.filter(pl.col("result_available_at_utc") < lock)[
        "result_available_at_utc"
    ].max()
    audits = [
        {
            "season": 2022,
            "profile": p.name,
            "selection_active_features": len(p.features),
            "latest_training_result_available_utc": latest.isoformat(),
        }
        for p in profiles
    ]
    choice = select_refinements(panel, {2022: lock}, audits, profiles=profiles)
    assert choice[0]["best_validation_profile"] == profiles[1].name
    assert choice[0]["selected_profile"] == profiles[2].name
    poisoned = panel.with_columns(
        pl.when(pl.col("season") == 2022)
        .then(99999.0)
        .otherwise(pl.col("predicted_home_margin"))
        .alias("predicted_home_margin")
    )
    assert select_refinements(poisoned, {2022: lock}, audits, profiles=profiles) == choice
    invalid = panel.with_columns(
        pl.when(pl.col("season") < 2022)
        .then(lock)
        .otherwise(pl.col("result_available_at_utc"))
        .alias("result_available_at_utc")
    )
    with pytest.raises(DataQualityError, match="precede"):
        select_refinements(invalid, {2022: lock}, audits, profiles=profiles)
    missing = panel.filter(pl.col("season") != 2020)
    with pytest.raises(DataQualityError, match="Both"):
        select_refinements(missing, {2022: lock}, audits, profiles=profiles)


def test_selected_cohorts_output_consistency_and_future_exclusion(refinement_cache):
    _, teams, games = refinement_cache
    profiles = PROFILES[:3]
    predictions, panel, selections, audits, folds = evaluate_refinements(
        teams, games, evaluation_seasons=(2022,), profiles=profiles
    )
    assert predictions.height == 36 * 2 * 4
    assert selections[0]["validation_seasons"] == [2020, 2021]
    assert panel["horizon"].unique().to_list() == ["early_week"]
    assert len(audits) == 3
    assert all(
        f["latest_training_result_available_utc"] < f["cutoff_utc"]
        for f in folds
        if f["stage"] != "candidate_validation"
    )
    assert predictions.filter(
        pl.col("predicted_home_margin")
        != (pl.col("predicted_home_score") - pl.col("predicted_away_score")).round(1)
    ).is_empty()
    assert (predictions["predicted_home_margin"] == -predictions["predicted_away_margin"]).all()
    assert predictions.filter(pl.col("model") == SELECTED_MODEL).height == 72
    future = teams.filter(pl.col("season") == 2022).with_columns(
        pl.lit(2026).alias("season"),
        (pl.lit("future_") + pl.col("game_id")).alias("game_id"),
        (pl.col("result_available_at_utc") + timedelta(days=1461)).alias("result_available_at_utc"),
        (pl.col("kickoff_utc") + timedelta(days=1461)).alias("kickoff_utc"),
        pl.lit(99999.0).alias("off_epa_sum"),
    )
    changed, *_ = evaluate_refinements(
        pl.concat([teams, future], how="vertical_relaxed"),
        games,
        evaluation_seasons=(2022,),
        profiles=profiles,
    )
    assert_frame_equal(predictions, changed)


def test_refinement_cli_and_cached_artifact_guards(refinement_cache, monkeypatch):
    data, _, _ = refinement_cache
    from nfl_prop_model.modeling import team_refinement

    original = team_refinement.evaluate_refinements

    def compact_run(teams, games, *, evaluation_seasons):
        return original(teams, games, evaluation_seasons=evaluation_seasons, profiles=PROFILES[:3])

    monkeypatch.setattr(team_refinement, "evaluate_refinements", compact_run)
    assert (
        main(
            [
                "team-refine",
                "--data-dir",
                str(data),
                "--evaluation-seasons",
                "2022",
                "--report-dir",
                str(data / "reports"),
            ]
        )
        == 0
    )
    report, predictions = load_refinements(data)
    assert predictions.height == 36 * 2 * 4
    path = data / "processed/team_refinement" / report["artifacts"]["predictions"]["file"]
    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        load_refinements(data)
    monkeypatch.setattr(team_refinement, "refinement_source_hash", lambda: "changed")
    with pytest.raises(DataQualityError, match="model changed"):
        load_refinements(data)
