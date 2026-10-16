import polars as pl
import pytest

from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.features.team_epa import HORIZONS
from nfl_prop_model.modeling import team_margin_calibration as calibration
from nfl_prop_model.modeling.team_scores import consistent_scores


def panel(predictions=(0, 2.9, 3, -7, 14, -14)):
    rows = []
    for index, prediction in enumerate(predictions):
        for horizon in HORIZONS:
            for model in calibration.replay.MODELS:
                rows.append(
                    {
                        "game_id": str(index),
                        "season": 2025,
                        "week": index + 1,
                        "horizon": horizon,
                        "model": model,
                        "home_score": 31.0,
                        "away_score": 10.0,
                        "target_home_margin": 21.0,
                        **consistent_scores(30 + prediction / 2, 30 - prediction / 2),
                    }
                )
    return pl.DataFrame(rows)


def test_bands_use_forecasts_with_exact_boundaries_and_every_model_shares_games():
    result = calibration.forecast_bands(panel())
    expected = {
        "0": calibration.BANDS[0],
        "1": calibration.BANDS[0],
        "2": calibration.BANDS[1],
        "3": calibration.BANDS[2],
        "4": calibration.BANDS[3],
        "5": calibration.BANDS[3],
    }
    for row in result.to_dicts():
        assert row["band"] == expected[row["game_id"]]
    changed = panel().with_columns(
        pl.lit(0.0).alias("target_home_margin"), pl.lit(31.0).alias("away_score")
    )
    assert result.select("game_id", "horizon", "model", "band").equals(
        calibration.forecast_bands(changed).select("game_id", "horizon", "model", "band")
    )


def test_predicted_ties_retain_errors_but_have_no_favorite():
    report = calibration.analyze(panel((0,)))
    rows = report["bands"]
    assert all(r["games"] == 1 and r["margin_mae"] == 21 for r in rows)
    assert all(r["reference_predicted_ties"] == 1 for r in rows)
    assert all(r["reference_favorite_win_fraction"] is None for r in rows)
    assert sum(r["games"] for r in report["coverage"] if r["horizon"] == "early_week") == 1


def test_favorite_orientation_and_band_comparison_do_not_change_by_model():
    report = calibration.analyze(panel((-7,)))
    assert all(r["reference_favorite_actual_margin"] == -21 for r in report["bands"])
    assert all(r["reference_favorite_win_fraction"] == 0 for r in report["bands"])


def test_linear_calibration_recovers_known_relationship_and_flat_forecasts_are_unidentified():
    rows = panel((-10, -5, 5, 10)).filter(
        (pl.col("model") == "compact_epa") & (pl.col("horizon") == "early_week")
    )
    rows = rows.with_columns(
        (2 + 1.5 * pl.col("predicted_home_margin")).alias("target_home_margin")
    )
    result = calibration.linear_calibration(rows)
    assert result["intercept"] == pytest.approx(2)
    assert result["slope"] == pytest.approx(1.5)
    assert result["slope_week_block_95_lower"] == pytest.approx(1.5)
    assert (
        calibration.linear_calibration(
            rows.with_columns(pl.lit(2.0).alias("predicted_home_margin"))
        )["slope"]
        is None
    )


def test_missing_candidate_games_and_nonfinite_predictions_fail():
    broken = panel().filter(~((pl.col("game_id") == "0") & (pl.col("model") == "league_scores")))
    with pytest.raises(DataQualityError, match="same game cohort"):
        calibration.forecast_bands(broken)
    with pytest.raises(DataQualityError, match="finite"):
        calibration.forecast_bands(
            panel().with_columns(pl.lit(float("nan")).alias("predicted_home_margin"))
        )
