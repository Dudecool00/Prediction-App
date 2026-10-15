from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from nfl_prop_model.cli import main
from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.storage import read_json, sha256_file, store_frame, write_json
from nfl_prop_model.features.team_walkforward import MODELS
from nfl_prop_model.modeling import team_simplicity as selection
from nfl_prop_model.modeling.team_scores import consistent_scores
from nfl_prop_model.modeling.team_walkforward import walkforward_source_hash


@pytest.fixture
def panel():
    rows = []
    for year in (2023, 2024):
        for week in range(1, 4):
            lock = datetime(year, 9, 5, 15, tzinfo=UTC) + timedelta(weeks=week - 1)
            kickoff = lock + timedelta(days=5)
            for game in (1, 2):
                for horizon in ("early_week", "pregame_refresh"):
                    for model in MODELS:
                        home, away = (25, 15) if model == selection.CONTROL else (24, 16)
                        if model == "compact_availability" and week == game == 1:
                            home = 23.9  # Tiny gain cannot justify eight extra raw inputs.
                        rows.append(
                            {
                                "game_id": f"{year}-{week}-{game}",
                                "season": year,
                                "week": week,
                                "horizon": horizon,
                                "model": model,
                                "kickoff_utc": kickoff,
                                "week_lock_utc": lock,
                                "prediction_time_utc": lock
                                if horizon == "early_week"
                                else kickoff - timedelta(hours=1),
                                "result_available_at_utc": kickoff + timedelta(hours=24),
                                "latest_training_result_available_utc": lock - timedelta(days=1),
                                "home_score": 20.0,
                                "away_score": 20.0,
                                "target_home_margin": 0.0,
                                **consistent_scores(home, away),
                            }
                        )
    return pl.DataFrame(rows)


def test_tiny_context_gain_cannot_capture_epa_pruning_credit(panel):
    choice = selection.select_model(panel, (2023, 2024), datetime(2025, 9, 2, 15, tzinfo=UTC))
    assert choice["model"] == "compact_epa" and len(choice["features"]) == 8
    injury = next(c for c in choice["checks"] if c["model"] == "compact_availability")
    comparisons = {c["control"]: c for c in injury["comparisons"]}
    assert not comparisons[selection.CONTROL]["reasons"]
    assert comparisons[selection.COMPACT]["margin_mae_gain"] < 0.05
    assert "margin gain below 0.05 points" in comparisons[selection.COMPACT]["reasons"]
    assert not injury["eligible"]


def test_following_season_outcomes_cannot_change_prior_selection(panel):
    lock = datetime(2024, 9, 5, 15, tzinfo=UTC)
    original = selection.select_model(panel, (2023,), lock)
    poison = panel.with_columns(
        pl.when(pl.col("season") == 2024)
        .then(9999.0)
        .otherwise(pl.col("home_score"))
        .alias("home_score")
    )
    assert selection.select_model(poison, (2023,), lock) == original
    with pytest.raises(ValueError, match="2023"):
        selection.select_model(panel, (2025,), lock)
    future = panel.filter(pl.col("season") == 2024).with_columns(
        pl.lit(2025, dtype=panel.schema["season"]).alias("season")
    )
    with pytest.raises(DataQualityError, match="excludes 2025"):
        selection.audit_selection(pl.concat([panel, future]))


def test_validation_outcomes_must_precede_lock_and_cohorts_match(panel):
    lock = datetime(2024, 9, 5, 15, tzinfo=UTC)
    late = panel.with_columns(
        pl.when(pl.col("season") == 2023)
        .then(lock)
        .otherwise(pl.col("result_available_at_utc"))
        .alias("result_available_at_utc")
    )
    with pytest.raises(DataQualityError, match="unavailable"):
        selection.select_model(late, (2023,), lock)
    missing = panel.filter(
        ~((pl.col("model") == "compact_epa") & (pl.col("game_id") == "2023-1-1"))
    )
    with pytest.raises(DataQualityError, match="cohorts"):
        selection.select_model(missing, (2023,), lock)
    changed = panel.with_columns(
        pl.when(pl.col("model") == "compact_epa")
        .then(21.0)
        .otherwise(pl.col("home_score"))
        .alias("home_score")
    )
    with pytest.raises(DataQualityError, match="disagree"):
        selection.select_model(changed, (2023,), lock)


def test_one_standard_error_prefers_compact_when_extra_inputs_are_tied(panel):
    # Both context and compact pass the incremental gate, but varying block errors
    # make the context edge smaller than the error estimate for the best candidate.
    changed = panel.with_columns(
        (pl.col("predicted_home_score") + pl.col("week") * 5.0).alias("predicted_home_score"),
        (pl.col("predicted_home_margin") + pl.col("week") * 5.0).alias("predicted_home_margin"),
        (pl.col("predicted_away_margin") - pl.col("week") * 5.0).alias("predicted_away_margin"),
        (pl.col("predicted_total") + pl.col("week") * 5.0).alias("predicted_total"),
    ).with_columns(
        pl.when(pl.col("model") == "compact_availability")
        .then(pl.col("predicted_home_score") - 0.2)
        .otherwise(pl.col("predicted_home_score"))
        .alias("predicted_home_score"),
        pl.when(pl.col("model") == "compact_availability")
        .then(pl.col("predicted_home_margin") - 0.2)
        .otherwise(pl.col("predicted_home_margin"))
        .alias("predicted_home_margin"),
        pl.when(pl.col("model") == "compact_availability")
        .then(pl.col("predicted_away_margin") + 0.2)
        .otherwise(pl.col("predicted_away_margin"))
        .alias("predicted_away_margin"),
        pl.when(pl.col("model") == "compact_availability")
        .then(pl.col("predicted_total") - 0.2)
        .otherwise(pl.col("predicted_total"))
        .alias("predicted_total"),
    )
    choice = selection.select_model(changed, (2023,), datetime(2024, 9, 5, 15, tzinfo=UTC))
    assert choice["best_eligible_model"] == "compact_availability"
    assert choice["one_standard_error"] > 0.2
    assert choice["model"] == "compact_epa"


def test_material_context_improvement_can_earn_inclusion(panel):
    changed = panel.with_columns(
        pl.when(pl.col("model") == "compact_availability")
        .then(20.0)
        .otherwise(pl.col("predicted_home_score"))
        .alias("predicted_home_score"),
        pl.when(pl.col("model") == "compact_availability")
        .then(20.0)
        .otherwise(pl.col("predicted_away_score"))
        .alias("predicted_away_score"),
        pl.when(pl.col("model") == "compact_availability")
        .then(0.0)
        .otherwise(pl.col("predicted_home_margin"))
        .alias("predicted_home_margin"),
        pl.when(pl.col("model") == "compact_availability")
        .then(0.0)
        .otherwise(pl.col("predicted_away_margin"))
        .alias("predicted_away_margin"),
        pl.when(pl.col("model") == "compact_availability")
        .then(40.0)
        .otherwise(pl.col("predicted_total"))
        .alias("predicted_total"),
    )
    choice = selection.select_model(changed, (2023,), datetime(2024, 9, 5, 15, tzinfo=UTC))
    assert choice["model"] == "compact_availability" and choice["one_standard_error"] == 0.0


def test_following_season_predictions_preserve_scores_and_both_horizons(panel):
    report, chosen = selection.audit_selection(panel)
    assert report["following_season_choice"]["validation_seasons"] == [2023]
    assert report["frozen_2025_research_choice"]["validation_seasons"] == [2023, 2024]
    assert set(chosen["season"]) == {2024} and set(chosen["horizon"]) == {
        "early_week",
        "pregame_refresh",
    }
    assert chosen.height == 12 and set(chosen["model"]) == {selection.SELECTED}
    assert report["2025_fits_performed"] == 0
    assert chosen.filter(
        (
            pl.col("predicted_home_score")
            - pl.col("predicted_away_score")
            - pl.col("predicted_home_margin")
        ).abs()
        > 1e-8
    ).is_empty()


def test_cli_reuses_verified_cache_preserves_original_and_checks_new_artifacts(
    tmp_path, panel, monkeypatch
):
    from nfl_prop_model.data import team_games
    from nfl_prop_model.modeling import team_scores

    def forbidden(*args, **kwargs):
        pytest.fail("Selection follow-up fetched or fitted new models")

    monkeypatch.setattr(team_games, "load_team_cache", forbidden)
    monkeypatch.setattr(team_scores, "predict_models", forbidden)
    original_dir = tmp_path / "processed" / "team_walkforward"
    features = panel.select(
        "game_id", "horizon", "season", "result_available_at_utc", "week_lock_utc"
    ).unique()
    older = (
        features.filter(pl.col("season") == 2023)
        .head(1)
        .with_columns(
            pl.lit(2022, dtype=features.schema["season"]).alias("season"),
            pl.lit("initial-2022").alias("game_id"),
        )
    )
    frozen = {"model": "compact_availability"}
    write_json(
        original_dir / "manifest.json",
        {
            "source_sha256": walkforward_source_hash(),
            "frozen_choice": frozen,
            "complexity": [],
            "coverage": [],
            "audit": {"blockers": ["untimed injuries"]},
            "folds": [{"training_seasons": [2022, 2023]}],
            "artifacts": {
                "features": store_frame(original_dir, "features", pl.concat([features, older])),
                "predictions": store_frame(
                    original_dir,
                    "predictions",
                    panel.drop("result_available_at_utc", "week_lock_utc"),
                ),
            },
        },
    )
    write_json(original_dir / "frozen_2025.json", frozen)
    old_files = {path: sha256_file(path) for path in original_dir.iterdir()}
    reports = tmp_path / "report"
    assert (
        main(["team-simplicity-check", "--data-dir", str(tmp_path), "--report-dir", str(reports)])
        == 0
    )
    path = next((tmp_path / "processed" / "team_simplicity").glob("*/manifest.json"))
    report = selection.load_simplicity_report(path)
    assert report["frozen_2025_research_choice"]["model"] == "compact_epa"
    assert old_files == {path: sha256_file(path) for path in original_dir.iterdir()}
    assert (reports / "comparison.html").is_file()
    assert "2025 outcomes" in (reports / "findings.md").read_text()
    original = path.read_bytes()
    selection.save_simplicity_check(tmp_path, reports)
    assert path.read_bytes() == original
    with monkeypatch.context() as guarded:
        guarded.setattr(selection, "simplicity_source_hash", lambda: "changed")
        with pytest.raises(DataQualityError, match="stale"):
            selection.load_simplicity_report(path)
    write_json(path.parent / "frozen_2025.json", {"model": "compact_combined"})
    with pytest.raises(DataQualityError, match="changed after"):
        selection.load_simplicity_report(path)
    write_json(path.parent / "frozen_2025.json", report["frozen_2025_research_choice"])
    (path.parent / report["artifacts"]["selected_2024_predictions"]["file"]).write_bytes(b"broken")
    with pytest.raises(ValueError, match="checksum"):
        selection.load_simplicity_report(path)
    assert read_json(original_dir / "frozen_2025.json") == frozen
