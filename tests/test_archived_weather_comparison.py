from datetime import UTC, datetime, timedelta

import polars as pl
import pytest
from polars.testing import assert_frame_equal
from test_team_scores import team_sources as team_sources
from test_team_walkforward import weekly_data as weekly_data

from nfl_prop_model.cli import main
from nfl_prop_model.data.gfs_archive import forecast_selection
from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.storage import read_json, store_frame, write_json
from nfl_prop_model.features.team_walkforward import add_pregame_features
from nfl_prop_model.modeling import team_archived_weather as study
from nfl_prop_model.modeling.team_scores import consistent_scores
from nfl_prop_model.modeling.team_walkforward import evaluate_weeks


def forecasts(base, status="complete"):
    records = []
    for row in base.to_dicts():
        complete = status == "complete"
        exposure = (
            None
            if status == "roof_unknown"
            else (0 if status in {"fixed_indoor", "moved_venue_excluded"} else 1)
        )
        records.append(
            {
                **{
                    k: row[k]
                    for k in ("game_id", "season", "week", "home_team", "away_team", "horizon")
                },
                "kickoff_utc": row["kickoff_utc"].isoformat(),
                "cutoff_utc": row["prediction_time_utc"].isoformat(),
                **forecast_selection(row["kickoff_utc"], row["prediction_time_utc"]),
                "exposure": exposure,
                "status": status,
                "temperature_c": 20.0 + row["week"] % 7 if complete else None,
                "wind_speed_m_s": row["week"] % 4 if complete else None,
                "precipitation_rate_mm_h": 0.0 if complete else None,
            }
        )
    return pl.DataFrame(records, infer_schema_length=None)


def decision_panel(features, *, flags_only=False):
    records = []
    fields = (
        "game_id",
        "season",
        "week",
        "home_team",
        "away_team",
        "horizon",
        "kickoff_utc",
        "prediction_time_utc",
        "week_lock_utc",
        "result_available_at_utc",
        "home_score",
        "away_score",
        "target_home_margin",
        "weather_complete",
    )
    for row in features.filter(pl.col("season").is_in([2023, 2024])).to_dicts():
        for model in study.MODELS:
            error = (
                2.0
                if model == study.CONTROL
                else (0.0 if flags_only or model == study.WEATHER else 1.0)
            )
            records.append(
                {
                    **{k: row[k] for k in fields},
                    "model": model,
                    **consistent_scores(row["home_score"] + error, row["away_score"]),
                    "training_games": 64,
                    "latest_training_result_available_utc": row["week_lock_utc"]
                    - timedelta(days=1),
                }
            )
    return pl.DataFrame(records)


def test_complete_zero_wind_rain_is_not_unknown(weekly_data):
    base = weekly_data[2]
    known = forecasts(base).with_columns(
        pl.lit(20.0).alias("temperature_c"), pl.lit(0.0).alias("wind_speed_m_s")
    )
    features = study.add_weather_context(base, known)
    assert features["weather_complete"].sum() == base.height
    assert all(features[k].sum() == 0 for k in study.TERMS)
    assert_frame_equal(base, features.select(base.columns))
    assert [len(columns) for columns in study.MODELS.values()] == [8, 11, 14]


@pytest.mark.parametrize(
    "status",
    ["roof_unknown", "fixed_indoor", "source_unavailable", "moved_venue_excluded", "partial"],
)
def test_missing_packages_are_neutral_and_keep_venue_flags(weekly_data, status):
    base = weekly_data[2]
    features = study.add_weather_context(base, forecasts(base, status))
    assert features.height == base.height
    assert features["weather_complete"].sum() == 0
    assert all(features[k].sum() == 0 for k in study.TERMS)
    assert features["weather_fixed_indoor"].sum() == (
        base.height if status == "fixed_indoor" else 0
    )
    assert features["weather_outdoor"].sum() == (
        base.height if status in {"partial", "source_unavailable"} else 0
    )


@pytest.mark.parametrize(
    "tamper", ["missing", "duplicate", "team", "cutoff", "run", "valid", "negative", "nan", "2025"]
)
def test_bad_cohort_identity_forecast_timing_and_values_rejected(weekly_data, tamper):
    base = weekly_data[2]
    weather = forecasts(base)
    if tamper == "missing":
        weather = weather.slice(1)
    elif tamper == "duplicate":
        weather = pl.concat([weather, weather.head(1)])
    elif tamper == "2025":
        base = base.with_columns(pl.lit(2025).alias("season"))
    elif tamper in {"team", "cutoff", "run", "valid"}:
        column = {
            "team": "home_team",
            "cutoff": "cutoff_utc",
            "run": "run_utc",
            "valid": "valid_utc",
        }[tamper]
        weather = weather.with_columns(
            pl.lit("wrong" if tamper == "team" else "2024-01-01T00:00:00+00:00").alias(column)
        )
    else:
        weather = weather.with_columns(
            pl.lit(-1.0 if tamper == "negative" else float("nan")).alias("wind_speed_m_s")
        )
    with pytest.raises((DataQualityError, pl.exceptions.PolarsError)):
        study.add_weather_context(base, weather)


def test_exact_control_weekly_training_and_future_outcome_invariance(weekly_data):
    _, _, base, old_weather, injuries = weekly_data
    features = study.add_weather_context(base, forecasts(base))
    panel, folds, complexity = study.evaluate_weather(features, seasons=(2024,))
    original, _, _ = evaluate_weeks(
        add_pregame_features(base, injuries, old_weather), seasons=(2024,), models=(study.CONTROL,)
    )
    study._check_control(panel, original.filter(pl.col("model") == study.CONTROL))
    assert len(folds) == 36
    assert set(c["raw_features"] for c in complexity) == {8, 11, 14}
    for row in folds:
        assert datetime.fromisoformat(
            row["latest_training_result_available_utc"]
        ) < datetime.fromisoformat(row["lock_utc"])
    changed = features.with_columns(
        *[
            pl.when(pl.col("season") == 2024).then(999.0).otherwise(pl.col(k)).alias(k)
            for k in ("home_score", "away_score")
        ]
    ).with_columns((pl.col("home_score") - pl.col("away_score")).alias("target_home_margin"))
    altered, _, _ = study.evaluate_weather(changed, seasons=(2024,))
    columns = ["game_id", "horizon", "model", "predicted_home_score", "predicted_away_score"]
    assert_frame_equal(
        panel.filter(pl.col("week") == 1).select(columns),
        altered.filter(pl.col("week") == 1).select(columns),
    )


def test_refresh_cannot_update_epa_history(weekly_data):
    base = weekly_data[2]
    features = study.add_weather_context(base, forecasts(base)).with_columns(
        pl.when(pl.col("horizon") == "pregame_refresh")
        .then(888.0)
        .otherwise(pl.col(study.COMPACT_EPA[0]))
        .alias(study.COMPACT_EPA[0])
    )
    with pytest.raises(DataQualityError, match="changed within"):
        study.evaluate_weather(features)


def test_selection_needs_gain_beyond_flags_and_past_validation(weekly_data):
    features = study.add_weather_context(weekly_data[2], forecasts(weekly_data[2]))
    lock = datetime(2025, 9, 2, 15, tzinfo=UTC)
    assert (
        study.choose_weather(decision_panel(features), (2023, 2024), lock)["model"] == study.WEATHER
    )
    assert (
        study.choose_weather(decision_panel(features, flags_only=True), (2023, 2024), lock)["model"]
        == study.CONTROL
    )
    with pytest.raises(DataQualityError, match="unavailable"):
        study.choose_weather(
            decision_panel(features), (2023, 2024), datetime(2024, 1, 1, tzinfo=UTC)
        )


@pytest.mark.parametrize("change", ["cohort", "contract", "timing", "actual", "nan"])
def test_panel_rejects_missing_games_bad_contract_facts_and_timing(weekly_data, change):
    panel = decision_panel(study.add_weather_context(weekly_data[2], forecasts(weekly_data[2])))
    if change == "cohort":
        panel = panel.slice(1)
    else:
        column = {
            "contract": "predicted_home_margin",
            "timing": "latest_training_result_available_utc",
            "actual": "home_score",
            "nan": "predicted_home_score",
        }[change]
        value = (
            pl.col("week_lock_utc")
            if change == "timing"
            else pl.lit(float("nan") if change == "nan" else 1234.0)
        )
        panel = panel.with_columns(
            pl.when(pl.col("model") == study.WEATHER)
            .then(value)
            .otherwise(pl.col(column))
            .alias(column)
        )
    with pytest.raises(DataQualityError):
        study.validate_panel(panel)


@pytest.mark.parametrize("tamper", ["context", "input", "choice", "code", "score_source"])
def test_reader_rechecks_weather_sources_and_frozen_evidence(
    weekly_data, tmp_path, monkeypatch, tamper
):
    base = weekly_data[2]
    weather = forecasts(base)
    features = study.add_weather_context(base, weather)
    panel = decision_panel(features)
    controls = panel.filter(pl.col("model") == study.CONTROL)
    refs = {"original_source": "original-digest"}
    monkeypatch.setattr(study, "_inputs", lambda _: (base, controls, weather, refs))
    frozen = study.choose_weather(panel, (2023, 2024), datetime(2025, 9, 2, 15, tzinfo=UTC))
    context = features.select(study.CONTEXT)
    report = {
        "format_version": 1,
        "source_sha256": study.source_hash(),
        "policy": study.POLICY,
        "inputs": refs,
        "metrics": study.score_metrics(panel).to_dicts(),
        "frozen_research_choice": frozen,
        "enriched_replay_ready": False,
        "2025_fits_performed": 0,
        "artifacts": {
            "context": store_frame(tmp_path, "context", context),
            "predictions": store_frame(tmp_path, "predictions", panel),
        },
    }
    path = tmp_path / "manifest.json"
    write_json(path, report)
    write_json(tmp_path / "frozen_2025.json", frozen)
    assert study.load_weather_comparison(path, tmp_path)["enriched_replay_ready"] is False
    if tamper == "context":
        report["artifacts"]["context"] = store_frame(
            tmp_path, "changed", context.with_columns(pl.lit(77.0).alias(study.TERMS[0]))
        )
    elif tamper == "input":
        report["inputs"] = {"source": "changed"}
    elif tamper == "choice":
        changed = read_json(tmp_path / "frozen_2025.json")
        changed["model"] = study.CONTROL
        write_json(tmp_path / "frozen_2025.json", changed)
    elif tamper == "score_source":
        altered = panel.with_columns(
            (pl.col("home_score") + 0.1).alias("home_score"),
            (pl.col("away_score") + 0.1).alias("away_score"),
        )
        report["artifacts"]["predictions"] = store_frame(tmp_path, "changed", altered)
        report["metrics"] = study.score_metrics(altered).to_dicts()
    else:
        report["source_sha256"] = "changed-code"
    write_json(path, report)
    with pytest.raises(DataQualityError):
        study.load_weather_comparison(path, tmp_path)


def test_cli_wires_comparison_without_a_replay_override(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        study,
        "save_weather_comparison",
        lambda data, report: (
            calls.append((data, report)) or {"frozen_research_choice": {"model": study.CONTROL}}
        ),
    )
    assert main(["team-archived-weather-compare", "--data-dir", str(tmp_path)]) == 0
    assert calls[0][0] == tmp_path
