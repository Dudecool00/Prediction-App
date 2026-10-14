from datetime import timedelta

import polars as pl
import pytest
from polars.testing import assert_frame_equal
from test_team_scores import team_cache as team_cache
from test_team_scores import team_sources as team_sources

from nfl_prop_model.cli import main
from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.modeling.team_diagnostics import diagnostic_tables, load_season_diagnostics
from nfl_prop_model.modeling.team_scores import evaluate_team_scores


def test_diagnostic_denominators_tails_and_team_orientation(team_cache):
    _, teams, games, _ = team_cache
    predictions, _, _ = evaluate_team_scores(teams, games, evaluation_seasons=(2022,))
    tables = diagnostic_tables(predictions)
    assert set(tables["distribution"]["games"]) == {36}
    assert tables["team_games"].height == 36 * 2 * 2
    home = tables["team_games"].filter(
        (pl.col("team") == "A") & (pl.col("horizon") == "early_week")
    )
    away = tables["team_games"].filter(
        (pl.col("team") == "B") & (pl.col("horizon") == "early_week")
    )
    assert_frame_equal(
        home.select("game_id", "absolute_margin_error"),
        away.select("game_id", "absolute_margin_error"),
    )
    assert (home["actual_margin"] == -away["actual_margin"]).all()
    assert (home["predicted_margin"] == -away["predicted_margin"]).all()
    assert tables["outcome_bands"]["games"].sum() == 36 * 2
    assert tables["largest_errors"].height == 20
    assert tables["distribution"]["largest_10_errors_share"].is_between(0, 1).all()


def test_diagnostic_excludes_later_season_outcomes(team_cache):
    _, teams, games, _ = team_cache
    original, _, _ = evaluate_team_scores(teams, games, evaluation_seasons=(2022,))
    future_teams = teams.filter(pl.col("season") == 2022).with_columns(
        pl.col("game_id").str.replace("2022", "2023"),
        pl.lit(2023).alias("season"),
        (pl.col("result_available_at_utc") + timedelta(days=365)).alias("result_available_at_utc"),
        (pl.col("kickoff_utc") + timedelta(days=365)).alias("kickoff_utc"),
        pl.lit(99999.0).alias("off_epa_sum"),
    )
    changed, _, _ = evaluate_team_scores(
        pl.concat([teams, future_teams], how="vertical_relaxed"), games, evaluation_seasons=(2022,)
    )
    assert_frame_equal(original, changed)


def test_diagnostic_cli_saves_separate_results_charts_and_guards(team_cache, monkeypatch):
    data, _, _, _ = team_cache
    report_dir = data / "diagnostic_report"
    assert (
        main(
            [
                "team-diagnose",
                "--data-dir",
                str(data),
                "--season",
                "2022",
                "--report-dir",
                str(report_dir),
            ]
        )
        == 0
    )
    report, tables = load_season_diagnostics(data, season=2022)
    assert report["season"] == 2022
    assert report["policy"]["decision"].startswith("no model selection")
    assert tables["games"].height == 36 * 2 * 3
    assert not (data / "processed" / "team_scores" / "manifest.json").exists()
    html = (report_dir / "diagnostics.html").read_text(encoding="utf-8")
    assert "Plotly.newPlot" in html and "2022:" in html
    assert "Team/week margin error" in html
    assert (report_dir / "game_predictions.csv").is_file()
    path = data / "processed" / "team_diagnostics" / "2022" / report["artifacts"]["weekly"]["file"]
    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        load_season_diagnostics(data, season=2022)
    monkeypatch.setattr(
        "nfl_prop_model.modeling.team_diagnostics.diagnostic_source_hash", lambda: "changed"
    )
    with pytest.raises(DataQualityError, match="code changed"):
        load_season_diagnostics(data, season=2022)
