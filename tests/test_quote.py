import polars as pl
import pytest

from nfl_prop_model.cli import main
from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.storage import load_frame, read_json, store_frame, write_json
from nfl_prop_model.markets.odds import ManualMarket
from nfl_prop_model.markets.quote import create_quote, load_historical_forecast


def test_manual_prices_and_actual_outcomes_do_not_change_forecast_or_probabilities(saved_research):
    data, directory, manifest = saved_research
    forecast = load_historical_forecast(data, "QB", "2023_02_A_B", "xgb_schedule")
    first = create_quote(forecast, ManualMarket(200, -110, -110))
    second = create_quote(forecast, ManualMarket(200, 150, -200, game_total=55, game_spread=-10))
    assert first["forecast"] == second["forecast"]
    assert first["probabilities"] == second["probabilities"]
    assert first["sides"] != second["sides"]
    assert "actual" not in first["forecast"]
    point = load_frame(directory, manifest["artifacts"]["predictions"])
    manifest["artifacts"]["predictions"] = store_frame(
        directory, "predictions", point.with_columns(pl.lit(-1000.0).alias("actual"))
    )
    write_json(directory / "manifest.json", manifest)
    changed = create_quote(
        load_historical_forecast(data, "QB", "2023_02_A_B", "xgb_schedule"),
        ManualMarket(200, -110, -110),
    )
    assert changed["forecast"] == first["forecast"]
    assert changed["probabilities"] == first["probabilities"]
    assert changed["sides"] == first["sides"]
    assert first["scope"] == "historical_research_demonstration"


def test_custom_line_changes_only_settlement_not_forecast(saved_research):
    data, _, _ = saved_research
    forecast = load_historical_forecast(data, "QB", "2023_02_A_B", "xgb_schedule")
    first = create_quote(forecast, ManualMarket(200, -110, -110))
    second = create_quote(forecast, ManualMarket(225.5, -110, -110))
    assert first["forecast"] == second["forecast"]
    assert first["uncertainty"] == second["uncertainty"]
    assert second["probabilities"]["over"] < first["probabilities"]["over"]
    assert first["probabilities"]["push"] == pytest.approx(1 / 101)
    assert second["probabilities"]["push"] == 0


@pytest.mark.parametrize("problem", ["overlap", "future", "missing", "duplicates", "small"])
def test_inconsistent_saved_calibration_is_rejected(saved_research, problem):
    data, directory, manifest = saved_research
    records = load_frame(directory, manifest["artifacts"]["calibration_residuals"])
    if problem == "overlap":
        records = records.with_columns(pl.lit("2023_02_A_B").alias("game_id"))
    elif problem == "future":
        records = records.with_columns(pl.col("evaluation_cutoff_utc").alias("kickoff_utc"))
    elif problem == "missing":
        records = records.with_columns(pl.lit(None).alias("residual"))
    elif problem == "duplicates":
        records = pl.concat([records, records.head(1)])
    else:
        records = records.head(99)
    manifest["artifacts"]["calibration_residuals"] = store_frame(
        directory, "calibration_residuals", records
    )
    write_json(directory / "manifest.json", manifest)
    with pytest.raises(DataQualityError):
        load_historical_forecast(data, "QB", "2023_02_A_B", "xgb_schedule")


def test_forecast_selection_old_manifest_and_cache_corruption_fail(saved_research):
    data, directory, manifest = saved_research
    with pytest.raises(DataQualityError, match="exactly one"):
        load_historical_forecast(data, "wrong", "2023_02_A_B", "xgb_schedule")
    write_json(directory / "manifest.json", {**manifest, "format_version": 1})
    with pytest.raises(ValueError, match="run nfl-prop research"):
        load_historical_forecast(data, "QB", "2023_02_A_B", "xgb_schedule")
    write_json(directory / "manifest.json", manifest)
    path = directory / manifest["artifacts"]["calibration_residuals"]["file"]
    path.write_bytes(path.read_bytes() + b"corrupt")
    with pytest.raises(ValueError, match="checksum mismatch"):
        load_historical_forecast(data, "QB", "2023_02_A_B", "xgb_schedule")


def test_quote_cli_reports_math_uncertainty_and_freshness_offline(saved_research, tmp_path, capsys):
    data, directory, _ = saved_research
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    report = tmp_path / "quote"
    args = [
        "quote",
        "--data-dir",
        str(data),
        "--report-dir",
        str(report),
        "--player-id",
        "QB",
        "--game-id",
        "2023_02_A_B",
        "--model",
        "xgb_schedule",
        "--line",
        "200",
        "--over-odds=-110",
        "--under-odds=+125",
        "--sportsbook",
        "Example",
    ]
    assert main(args) == 0
    quote = read_json(report / "quote.json")
    text = (report / "quote.md").read_text(encoding="utf-8")
    assert quote["sides"]["over"]["estimated_ev_per_dollar"] == pytest.approx(
        49.5 / 101 * 10 / 11 - 50.5 / 101
    )
    assert (
        "Estimated EV" in text
        and "Coverage evidence" in text
        and "Source snapshot retrieved" in text
    )
    assert "Historical research demonstration" in capsys.readouterr().out
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before
    with pytest.raises(SystemExit) as error:
        main([*args[:-2], "--line", "nan"])
    assert error.value.code == 1
