"""Predeclared weather ablation on weekly locked 2023-2024 predictions."""

import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nfl_prop_model.data import team_historical_weather as archive
from nfl_prop_model.data.gfs_archive import forecast_selection
from nfl_prop_model.data.schemas import DataQualityError, require_keys
from nfl_prop_model.data.storage import load_frame, read_json, sha256_file, store_frame, write_json
from nfl_prop_model.features.team_epa import HORIZONS
from nfl_prop_model.features.team_walkforward import COMPACT_EPA
from nfl_prop_model.modeling.reporting import markdown_table
from nfl_prop_model.modeling.team_scores import forecast_rows, score_metrics, score_pipeline
from nfl_prop_model.modeling.team_simplicity import paired_gain, simplicity_source_hash
from nfl_prop_model.modeling.team_walkforward import (
    load_walkforward_development,
    write_comparison_charts,
)

CONTROL = "compact_epa"
FLAGS = "compact_weather_flags"
WEATHER = "compact_archived_weather"
COVERAGE = ("weather_fixed_indoor", "weather_outdoor", "weather_complete")
TERMS = ("weather_temperature_delta_c", "weather_wind_m_s", "weather_rate_mm_h")
MODELS = {
    CONTROL: COMPACT_EPA,
    FLAGS: COMPACT_EPA + COVERAGE,
    WEATHER: COMPACT_EPA + COVERAGE + TERMS,
}
POLICY: dict[str, Any] = {
    "scope": "2022 initialization, all 544 2023-2024 development games at both horizons",
    "timing": "EPA/history and separate horizon fits locked on Tuesday; context-only refresh",
    "source": archive.POLICY,
    "models": {k: list(v) for k, v in MODELS.items()},
    "terms": "complete outdoor package: temperature minus 20C, wind m/s, instantaneous rate mm/h",
    "unknown": "neutral zero for all three terms with shared venue/coverage flags",
    "flags": "fixed indoor, known outdoor exposure, complete outdoor weather package",
    "fit": "two-output Ridge alpha 20, train-only preprocessing, common fractional score contract",
    "selection": "early-week gain >=0.05 against EPA and flags; positive paired 95% lower bound",
    "guards": {
        "score_regression": 0.05,
        "total_regression": 0.10,
        "season_margin_regression": 0.25,
    },
    "bootstrap": {"draws": 5000, "seed": 20261015, "blocks": "season/week"},
    "following_season": "select on 2023, assess 2024; separately record pre-2025 choice from both",
    "limits": "examined adaptive development; static city/venue and publication-delay assumptions",
    "2025": "no outcomes, weather features or fits; original enriched-replay gate remains in force",
    "promotion": "separate research choice only, fallback compact EPA; flags cannot be selected",
    "storage": "one context delta and prediction panel; reuse original features and raw weather",
}
CONTEXT = (
    "game_id",
    "horizon",
    "weather_status",
    *COVERAGE,
    *TERMS,
    "archive_run_utc",
    "archive_available_utc",
    "archive_valid_utc",
)


def source_hash() -> str:
    return hashlib.sha256(
        Path(__file__).read_bytes()
        + archive.source_hash().encode()
        + simplicity_source_hash().encode()
    ).hexdigest()


def add_weather_context(base: pl.DataFrame, weather: pl.DataFrame) -> pl.DataFrame:
    require_keys(base, ["game_id", "horizon"], "weather base")
    require_keys(weather, ["game_id", "horizon"], "archived weather")
    if (
        not set(base["season"]) <= {2022, 2023, 2024}
        or set(base["horizon"]) != set(HORIZONS)
        or set(base.select("game_id", "horizon").iter_rows())
        != set(weather.select("game_id", "horizon").iter_rows())
    ):
        raise DataQualityError(
            "Weather comparison requires identical development cohorts, excluding 2025"
        )
    lookup = {(r["game_id"], r["horizon"]): r for r in weather.to_dicts()}
    records = []
    for row in base.to_dicts():
        w = lookup[(row["game_id"], row["horizon"])]
        if any(w[k] != row[k] for k in ("season", "week", "home_team", "away_team")) or (
            datetime.fromisoformat(w["kickoff_utc"]) != row["kickoff_utc"]
            or datetime.fromisoformat(w["cutoff_utc"]) != row["prediction_time_utc"]
        ):
            raise DataQualityError("Weather schedule identity or cutoff mismatch")
        selection = forecast_selection(row["kickoff_utc"], row["prediction_time_utc"])
        if any(w[k] != v for k, v in selection.items()):
            raise DataQualityError("Weather run/valid time selection mismatch")
        status, exposure = w["status"], w["exposure"]
        values = [w[k] for k in ("temperature_c", "wind_speed_m_s", "precipitation_rate_mm_h")]
        if status not in {
            "complete",
            "partial",
            "source_unavailable",
            "roof_unknown",
            "fixed_indoor",
            "moved_venue_excluded",
        }:
            raise DataQualityError("Unknown weather status")
        if (
            (status == "complete" and (exposure != 1 or any(v is None for v in values)))
            or (status == "fixed_indoor" and exposure != 0)
            or (status == "roof_unknown" and exposure is not None)
            or (status in {"partial", "source_unavailable"} and exposure != 1)
            or (
                status
                in {"fixed_indoor", "roof_unknown", "source_unavailable", "moved_venue_excluded"}
                and any(v is not None for v in values)
            )
            or any(v is not None and not math.isfinite(v) for v in values)
            or any(v is not None and v < 0 for v in values[1:])
        ):
            raise DataQualityError("Weather status/exposure/value mismatch")
        complete = status == "complete"
        records.append(
            {
                "game_id": row["game_id"],
                "horizon": row["horizon"],
                "weather_status": status,
                COVERAGE[0]: float(status == "fixed_indoor"),
                COVERAGE[1]: float(exposure == 1 and status != "moved_venue_excluded"),
                COVERAGE[2]: float(complete),
                TERMS[0]: float(values[0] - 20) if complete else 0.0,
                TERMS[1]: float(values[1]) if complete else 0.0,
                TERMS[2]: float(values[2]) if complete else 0.0,
                "archive_run_utc": datetime.fromisoformat(w["run_utc"]),
                "archive_available_utc": datetime.fromisoformat(w["assumed_available_utc"]),
                "archive_valid_utc": datetime.fromisoformat(w["valid_utc"]),
            }
        )
    result = base.join(pl.DataFrame(records), on=["game_id", "horizon"], validate="1:1", how="left")
    validate_features(result)
    return result


def validate_features(frame: pl.DataFrame) -> None:
    require_keys(frame, ["game_id", "horizon"], "weather features")
    if not set(frame["season"]) <= {2022, 2023, 2024} or set(frame["horizon"]) != set(HORIZONS):
        raise DataQualityError("Weather evaluation excludes 2025 and pre-2022 history")
    if frame.filter(
        pl.any_horizontal(
            pl.col(
                "week_lock_utc", "prediction_time_utc", "kickoff_utc", "archive_available_utc"
            ).is_null()
        )
        | (pl.col("week_lock_utc") > pl.col("prediction_time_utc"))
        | (pl.col("prediction_time_utc") >= pl.col("kickoff_utc"))
        | (pl.col("archive_available_utc") >= pl.col("prediction_time_utc"))
        | (
            (pl.col("horizon") == "early_week")
            & (pl.col("prediction_time_utc") != pl.col("week_lock_utc"))
        )
        | (
            (pl.col("horizon") == "pregame_refresh")
            & (pl.col("prediction_time_utc") != pl.col("kickoff_utc") - pl.duration(hours=1))
        )
        | pl.any_horizontal(
            pl.col("home_history_available_at_utc", "away_history_available_at_utc")
            >= pl.col("week_lock_utc")
        )
        | pl.any_horizontal(
            pl.col(COVERAGE + TERMS).is_null() | ~pl.col(COVERAGE + TERMS).is_finite()
        )
        | pl.any_horizontal(~pl.col(COMPACT_EPA).is_finite()).fill_null(False)
    ).height:
        raise DataQualityError("Invalid weather feature timing or numeric values")
    if (
        frame.group_by("game_id")
        .agg(pl.col(COMPACT_EPA).n_unique())
        .select(pl.any_horizontal(pl.col(COMPACT_EPA) != 1).any())
        .item()
    ):
        raise DataQualityError("EPA/history changed within the forecast week")


def evaluate_weather(
    features: pl.DataFrame, *, seasons: tuple[int, ...] = (2023, 2024)
) -> tuple[pl.DataFrame, list[dict[str, Any]], list[dict[str, Any]]]:
    validate_features(features)
    if not seasons or not set(seasons) <= {2023, 2024}:
        raise DataQualityError("Weather evaluation requires 2023/2024 development seasons")
    records, folds, complexity = [], [], []
    for horizon in HORIZONS:
        history = features.filter(pl.col("horizon") == horizon)
        for test in history.filter(pl.col("season").is_in(seasons)).partition_by(
            ["season", "week"], maintain_order=True
        ):
            first = test.row(0, named=True)
            lock = first["week_lock_utc"]
            if test["week_lock_utc"].n_unique() != 1:
                raise DataQualityError("Conflicting Tuesday locks")
            train = history.filter(
                (
                    (pl.col("season") < first["season"])
                    | ((pl.col("season") == first["season"]) & (pl.col("week") < first["week"]))
                )
                & (pl.col("result_available_at_utc") < lock)
                & (pl.col("prediction_time_utc") < lock)
                & pl.col("home_score").is_not_null()
                & pl.col("away_score").is_not_null()
            ).sort("game_id")
            if train.height < 64 or set(train["game_id"]) & set(test["game_id"]):
                raise DataQualityError("Insufficient or overlapping weekly weather training")
            targets = train.select("home_score", "away_score").to_numpy()
            predictions = {}
            for name, columns in MODELS.items():
                matrix = np.asarray(train.select(columns).to_numpy(), dtype=np.float64)
                model = score_pipeline().fit(matrix, targets)
                predictions[name] = np.asarray(model.predict(test.select(columns).to_numpy()))
                if first["week"] == 1:
                    encoded = model[:-1].transform(matrix)
                    singular = np.linalg.svd(encoded, compute_uv=False)
                    complexity.append(
                        {
                            "season": first["season"],
                            "horizon": horizon,
                            "model": name,
                            "raw_features": len(columns),
                            "encoded_features": encoded.shape[1],
                            "allocated_weights_and_intercepts": int(model[-1].coef_.size + 2),
                            "ridge_degrees_of_freedom_per_output": float(
                                (singular**2 / (singular**2 + 20)).sum() + 1
                            ),
                            "context_standardized_coefficients": {
                                c: model[-1].coef_[:, i].tolist()
                                for i, c in enumerate(columns)
                                if c in COVERAGE + TERMS
                            },
                        }
                    )
            records.extend(forecast_rows(test, predictions, train))
            latest = train["result_available_at_utc"].max()
            if not isinstance(latest, datetime):
                raise DataQualityError("Missing training result availability")
            folds.append(
                {
                    "season": first["season"],
                    "week": first["week"],
                    "horizon": horizon,
                    "lock_utc": lock.isoformat(),
                    "training_games": train.height,
                    "training_seasons": sorted(set(train["season"])),
                    "latest_training_result_available_utc": latest.isoformat(),
                    "training_game_ids_sha256": hashlib.sha256(
                        "\n".join(train["game_id"]).encode()
                    ).hexdigest(),
                    "test_game_ids": sorted(test["game_id"].to_list()),
                }
            )
            if first["week"] in (1, 9, 18):
                print(
                    f"Weather comparison: {first['season']} week {first['week']} {horizon}",
                    flush=True,
                )
    panel = pl.DataFrame(records, infer_schema_length=None).join(
        features.select(
            "game_id", "horizon", "week_lock_utc", "result_available_at_utc", "weather_complete"
        ),
        on=["game_id", "horizon"],
        validate="m:1",
    )
    validate_panel(panel)
    return panel, folds, complexity


def validate_panel(panel: pl.DataFrame) -> None:
    require_keys(panel, ["game_id", "horizon", "model"], "weather predictions")
    if (
        set(panel["model"]) != set(MODELS)
        or set(panel["horizon"]) != set(HORIZONS)
        or not set(panel["season"]) <= {2023, 2024}
    ):
        raise DataQualityError("Weather panel models/horizons/seasons differ")
    expected = set(panel["game_id"])
    for group in panel.partition_by(["model", "horizon"]):
        if set(group["game_id"]) != expected:
            raise DataQualityError("Weather candidate cohorts differ")
    quantities = (
        "home_score",
        "away_score",
        "target_home_margin",
        "predicted_home_score",
        "predicted_away_score",
        "predicted_home_margin",
        "predicted_away_margin",
        "predicted_total",
    )
    times = (
        "latest_training_result_available_utc",
        "week_lock_utc",
        "prediction_time_utc",
        "kickoff_utc",
        "result_available_at_utc",
    )
    if panel.filter(
        pl.any_horizontal(pl.col(times).is_null())
        | pl.any_horizontal(pl.col(quantities).is_null() | ~pl.col(quantities).is_finite())
        | (pl.col("latest_training_result_available_utc") >= pl.col("week_lock_utc"))
        | (pl.col("week_lock_utc") > pl.col("prediction_time_utc"))
        | (pl.col("prediction_time_utc") >= pl.col("kickoff_utc"))
        | (
            (
                pl.col("predicted_home_score")
                - pl.col("predicted_away_score")
                - pl.col("predicted_home_margin")
            ).abs()
            > 1e-8
        )
        | (
            (
                pl.col("predicted_home_score")
                + pl.col("predicted_away_score")
                - pl.col("predicted_total")
            ).abs()
            > 1e-8
        )
        | ((pl.col("predicted_home_margin") + pl.col("predicted_away_margin")).abs() > 1e-8)
        | (
            (pl.col("home_score") - pl.col("away_score") - pl.col("target_home_margin")).abs()
            > 1e-8
        )
    ).height:
        raise DataQualityError("Weather score contract or training timing mismatch")
    facts = (
        "season",
        "week",
        "home_team",
        "away_team",
        "kickoff_utc",
        "prediction_time_utc",
        "week_lock_utc",
        "result_available_at_utc",
        "home_score",
        "away_score",
        "target_home_margin",
        "weather_complete",
    )
    if (
        panel.group_by("game_id", "horizon")
        .agg(pl.col(facts).n_unique())
        .select(pl.any_horizontal(pl.col(facts) != 1).any())
        .item()
    ):
        raise DataQualityError("Weather candidates disagree on game facts")


def choose_weather(panel: pl.DataFrame, seasons: tuple[int, ...], lock: datetime) -> dict[str, Any]:
    validate_panel(panel)
    selected = panel.filter(pl.col("season").is_in(seasons))
    if (
        seasons not in {(2023,), (2023, 2024)}
        or lock.tzinfo is None
        or set(selected["season"]) != set(seasons)
        or selected.filter(pl.col("result_available_at_utc") >= lock).height
    ):
        raise DataQualityError("Weather selection includes unavailable validation outcomes")
    metrics = score_metrics(selected)
    checks = []
    for control in (CONTROL, FLAGS):
        gain = paired_gain(selected.filter(pl.col("horizon") == "early_week"), WEATHER, control)
        reasons = []
        if gain["margin_mae_gain"] < 0.05:
            reasons.append("insufficient incremental margin improvement")
        if gain["week_block_95_lower"] <= 0:
            reasons.append("paired range includes no improvement")
        for horizon in HORIZONS:
            overall = {
                r["model"]: r
                for r in metrics.filter(
                    (pl.col("scope") == "overall") & (pl.col("horizon") == horizon)
                ).to_dicts()
            }
            for quantity, tolerance in (("margin", 0.0), ("score", 0.05), ("total", 0.10)):
                if (
                    overall[WEATHER][quantity + "_mae"]
                    > overall[control][quantity + "_mae"] + tolerance + 1e-12
                ):
                    reasons.append(f"{horizon} {quantity} regression")
            for season in seasons:
                yearly = {
                    r["model"]: r
                    for r in metrics.filter(
                        (pl.col("scope") == "season")
                        & (pl.col("horizon") == horizon)
                        & (pl.col("season") == season)
                    ).to_dicts()
                }
                if yearly[WEATHER]["margin_mae"] > yearly[control]["margin_mae"] + 0.25:
                    reasons.append(f"{season} {horizon} margin regression")
        checks.append({"control": control, **gain, "eligible": not reasons, "reasons": reasons})
    chosen = WEATHER if all(c["eligible"] for c in checks) else CONTROL
    return {
        "model": chosen,
        "features": list(MODELS[chosen]),
        "selection_seasons": list(seasons),
        "simulated_lock_utc": lock.isoformat(),
        "checks": checks,
        "primary_horizon": "early_week",
        "2025_outcomes_used": False,
    }


def _inputs(data_dir: Path) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, dict[str, Any]]:
    upstream = load_walkforward_development(data_dir)
    weekly = data_dir / "processed" / "team_walkforward" / "manifest.json"
    base = load_frame(weekly.parent, upstream["artifacts"]["features"])
    controls = load_frame(weekly.parent, upstream["artifacts"]["predictions"]).filter(
        pl.col("model") == CONTROL
    )
    games, inputs = archive.schedule(data_dir)  # metadata projection, no outcome columns
    directory = data_dir / "raw" / "team_historical_weather"
    manifest = directory / "manifest.json"
    if read_json(manifest) != {
        "format_version": 1,
        "source_sha256": archive.source_hash(),
        "policy": archive.POLICY,
        "inputs": inputs,
    }:
        raise DataQualityError("Historical weather source/policy/input changed")
    points = archive._points(data_dir, directory, games, offline=True)
    weather = pl.DataFrame(
        archive.build_rows(games.filter(pl.col("season") <= 2024), points, directory, offline=True),
        infer_schema_length=None,
    )
    receipts = {}
    for row in weather.to_dicts():
        if row["point_url"] is not None:
            for url in (row["url"] + ".das", row["point_url"]):
                path = archive._receipt_path(directory, url)
                if path.exists():
                    receipts[path.name] = sha256_file(path)
    refs = {
        "weekly_sha256": sha256_file(weekly),
        "weather_manifest_sha256": sha256_file(manifest),
        "points_sha256": sha256_file(directory / "points.json"),
        "development_receipts_sha256": hashlib.sha256(
            json.dumps(receipts, sort_keys=True).encode()
        ).hexdigest(),
    }
    if (
        set(base["season"]) != {2022, 2023, 2024}
        or base.height != 1630
        or controls.height != 1088
        or weather.height != 1630
    ):
        raise DataQualityError(
            "Weather comparison requires the complete original development cohort"
        )
    return base, controls, weather, refs


def _check_control(panel: pl.DataFrame, saved: pl.DataFrame) -> None:
    columns = [
        "game_id",
        "horizon",
        "predicted_home_score",
        "predicted_away_score",
        "predicted_home_margin",
    ]
    observed = panel.filter(pl.col("model") == CONTROL).select(columns).sort("game_id", "horizon")
    if not observed.equals(saved.select(columns).sort("game_id", "horizon")):
        raise DataQualityError("Compact EPA differs from the original saved control")


def load_weather_comparison(path: Path, data_dir: Path) -> dict[str, Any]:
    report = read_json(path)
    if (
        report["format_version"] != 1
        or report["source_sha256"] != source_hash()
        or report["policy"] != POLICY
    ):
        raise DataQualityError("Weather comparison code/policy changed")
    base, controls, weather, refs = _inputs(data_dir)
    if report["inputs"] != refs:
        raise DataQualityError("Weather comparison source inputs changed")
    context = load_frame(path.parent, report["artifacts"]["context"])
    features = add_weather_context(base, weather)
    if not context.equals(features.select(CONTEXT)):
        raise DataQualityError("Weather context differs from preserved sources")
    panel = load_frame(path.parent, report["artifacts"]["predictions"])
    validate_panel(panel)
    _check_control(panel, controls)
    facts = [
        "game_id",
        "horizon",
        "season",
        "week",
        "home_team",
        "away_team",
        "kickoff_utc",
        "prediction_time_utc",
        "week_lock_utc",
        "result_available_at_utc",
        "home_score",
        "away_score",
        "target_home_margin",
        "weather_complete",
    ]
    expected = features.filter(pl.col("season").is_in([2023, 2024])).select(facts)
    if (
        not panel.select(facts)
        .unique()
        .sort("game_id", "horizon")
        .equals(expected.sort("game_id", "horizon"))
    ):
        raise DataQualityError("Weather prediction facts differ from original features")
    if score_metrics(panel).to_dicts() != report["metrics"]:
        raise DataQualityError("Weather metrics changed")
    frozen = read_json(path.parent / "frozen_2025.json")
    if frozen != report["frozen_research_choice"] or frozen != choose_weather(
        panel, (2023, 2024), datetime(2025, 9, 2, 15, tzinfo=UTC)
    ):
        raise DataQualityError("Weather frozen research choice changed")
    if report["enriched_replay_ready"] is not False or report["2025_fits_performed"] != 0:
        raise DataQualityError("Weather test cannot clear enriched replay readiness")
    return report


def save_weather_comparison(data_dir: Path, report_dir: Path) -> dict[str, Any]:
    source = source_hash()
    base, controls, weather, refs = _inputs(data_dir)
    signature = hashlib.sha256(json.dumps(refs, sort_keys=True).encode()).hexdigest()
    folder = data_dir / "processed" / "team_archived_weather" / f"{source[:12]}-{signature[:12]}"
    path = folder / "manifest.json"
    if path.exists():
        report = load_weather_comparison(path, data_dir)
    else:
        write_json(
            folder / "protocol.json",
            {
                "policy": POLICY,
                "source_sha256": source,
                "inputs": refs,
                "recorded_at_utc": datetime.now(UTC).isoformat(),
            },
        )
        features = add_weather_context(base, weather)
        panel, folds, complexity = evaluate_weather(features)
        _check_control(panel, controls)
        lock = base.filter(pl.col("season") == 2024)["week_lock_utc"].min()
        if not isinstance(lock, datetime):
            raise DataQualityError("Missing following-season lock")
        chosen_2024 = choose_weather(panel, (2023,), lock)
        frozen = choose_weather(panel, (2023, 2024), datetime(2025, 9, 2, 15, tzinfo=UTC))
        comparisons = [
            {
                "horizon": h,
                "model": c,
                "control": b,
                **paired_gain(panel.filter(pl.col("horizon") == h), c, b),
            }
            for h in HORIZONS
            for c, b in ((FLAGS, CONTROL), (WEATHER, CONTROL), (WEATHER, FLAGS))
        ]
        if source_hash() != source or _inputs(data_dir)[3] != refs:
            raise DataQualityError("Weather inputs changed during comparison")
        report = {
            "format_version": 1,
            "recorded_at_utc": datetime.now(UTC).isoformat(),
            "source_sha256": source,
            "policy": POLICY,
            "inputs": refs,
            "metrics": score_metrics(panel).to_dicts(),
            "comparisons": comparisons,
            "complete_outdoor_secondary_metrics": (
                score_metrics(panel.filter(pl.col("weather_complete") == 1)).to_dicts()
                if panel.filter(pl.col("weather_complete") == 1).height
                else []
            ),
            "coverage": features.group_by("season", "horizon", "weather_status")
            .len()
            .sort("season", "horizon", "weather_status")
            .to_dicts(),
            "folds": folds,
            "complexity": complexity,
            "following_season_choice": chosen_2024,
            "following_season_selected_metrics": score_metrics(
                panel.filter((pl.col("season") == 2024) & (pl.col("model") == chosen_2024["model"]))
            ).to_dicts(),
            "frozen_research_choice": frozen,
            "2025_fits_performed": 0,
            "enriched_replay_ready": False,
            "artifacts": {
                "context": store_frame(folder, "context", features.select(CONTEXT)),
                "predictions": store_frame(folder, "predictions", panel),
            },
        }
        write_json(folder / "frozen_2025.json", frozen)
        write_json(path, report)
    load_weather_comparison(path, data_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    write_json(report_dir / "results.json", report)
    summary = (
        pl.DataFrame(report["metrics"])
        .filter(pl.col("scope") == "overall")
        .select("horizon", "model", "games", "margin_mae", "score_mae", "total_mae")
    )
    summary.write_csv(report_dir / "metrics.csv")
    pl.DataFrame(report["comparisons"]).write_csv(report_dir / "comparisons.csv")
    write_comparison_charts(
        load_frame(folder, report["artifacts"]["predictions"]), report_dir / "comparison.html"
    )
    (report_dir / "findings.md").write_text(
        "# Archived weather development comparison\n\n2022 initialization, all development games, "
        "Tuesday-locked EPA and fits, no 2025 evaluation.\n\n"
        + "\n".join(markdown_table(summary))
        + f"\n\n2023-only choice for 2024: **{report['following_season_choice']['model']}**. "
        + f"Separate pre-2025 research choice: **{report['frozen_research_choice']['model']}**.\n\n"
        "Weather must improve early-week margin against both compact EPA and venue/coverage flags "
        "with a positive paired lower bound, while passing both-horizon score/total/season guards. "
        "All games remain in primary results. Unknown packages use neutral zero and shared flags; "
        "precipitation is a rate, not accumulated rain. Publication delay and static city/venue "
        "assumptions remain explicit.\n\n"
        "These are previously examined, adaptively explored development seasons. The active model, "
        "earlier locks and original enriched-replay gate remain intact. Dated roster/starter "
        "coverage precedes the enriched 2025 replay; PEM testing follows the original plan.\n",
        encoding="utf-8",
    )
    return report
