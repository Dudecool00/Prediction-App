"""Fixed opponent-adjusted scoring ratings versus EPA and their equal score blend."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.storage import load_frame, read_json, sha256_file, store_frame, write_json
from nfl_prop_model.features.team_epa import HORIZONS
from nfl_prop_model.modeling import team_compact_replay as replay
from nfl_prop_model.modeling import team_margin_calibration as calibration
from nfl_prop_model.modeling.reporting import markdown_table
from nfl_prop_model.modeling.team_scores import (
    consistent_scores,
    forecast_rows,
    runtime_versions,
    score_metrics,
)
from nfl_prop_model.modeling.team_simplicity import paired_gain
from nfl_prop_model.modeling.team_walkforward import frame_hash

POINTS = "points_ratings"
BLEND = "epa_points_blend"
CANDIDATES = ("compact_epa", POINTS, BLEND)
ALL_MODELS = (*replay.MODELS, POINTS, BLEND)
POLICY: dict[str, Any] = {
    "scope": "adaptive PEM-inspired research; unknown original PEM formula is not reproduced",
    "candidates": list(CANDIDATES),
    "controls": list(replay.MODELS[1:]),
    "points": "league intercept + own offense + opponent defense-conceded + signed half home edge",
    "ridge_alpha": 20.0,
    "solver": "svd",
    "scaled_indicators": False,
    "recency_half_life_days": 365.0,
    "blend": "fixed 50/50 average of coherent displayed EPA and points scores; round again",
    "training": "2022+ earlier seasons/weeks; result availability strictly before Tuesday lock",
    "refresh": "reuse Tuesday fit and scores; no outcome/context refresh",
    "selection": {
        "years": [2023, 2024],
        "minimum_margin_gain": 0.05,
        "controls": ["compact_epa", "prior_team_scores"],
        "require_positive_week_block_95_lower": True,
        "maximum_score_regression": 0.05,
        "maximum_total_regression": 0.10,
        "maximum_season_margin_regression": 0.25,
        "fallback": "compact_epa",
    },
    "bootstrap": {"draws": 5000, "seed": 20261015, "blocks": "season/week"},
    "2025": "already examined; freeze before candidate 2025 fits, after earlier diagnostics",
    "enriched_replay_ready": False,
    "promotion": "none; published app unchanged",
}


def source_hash() -> str:
    return hashlib.sha256(
        Path(__file__).read_bytes()
        + replay.source_hash().encode()
        + Path(calibration.__file__).read_bytes()
    ).hexdigest()


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def training_fold(history: pl.DataFrame, test: pl.DataFrame) -> tuple[pl.DataFrame, dict[str, Any]]:
    first = test.row(0, named=True)
    lock, season, week = first["week_lock_utc"], first["season"], first["week"]
    if test["week_lock_utc"].n_unique() != 1:
        raise DataQualityError("Points comparison requires one lock per week")
    eligible = history.filter(
        (pl.col("season") >= 2022)
        & ((pl.col("season") < season) | ((pl.col("season") == season) & (pl.col("week") < week)))
    )
    train = eligible.filter(
        (pl.col("result_available_at_utc") < lock) & (pl.col("prediction_time_utc") < lock)
    ).sort("game_id")
    if train.height < 64 or set(train["game_id"]) & set(test["game_id"]):
        raise DataQualityError("Points fit needs 64 available nonoverlapping earlier games")
    latest = train["result_available_at_utc"].max()
    if not isinstance(latest, datetime):
        raise DataQualityError("Missing points training availability")
    return train, {
        "season": season,
        "week": week,
        "lock_utc": lock.isoformat(),
        "training_games": train.height,
        "training_seasons": sorted(set(train["season"])),
        "training_game_ids_sha256": hashlib.sha256(
            "\n".join(train["game_id"]).encode()
        ).hexdigest(),
        "training_sha256": frame_hash(
            train.select(
                "game_id",
                "kickoff_utc",
                "result_available_at_utc",
                "home_team",
                "away_team",
                "home_score",
                "away_score",
                "home_field",
            )
        ),
        "latest_training_result_available_utc": latest.isoformat(),
        "test_game_ids": sorted(test["game_id"].to_list()),
        "unavailable_earlier_games": sorted(
            eligible.filter(pl.col("result_available_at_utc") >= lock)["game_id"].to_list()
        ),
    }


def design(games: pl.DataFrame, teams: list[str]) -> np.ndarray[Any, Any]:
    lookup = {name: i for i, name in enumerate(teams)}
    size = len(teams)
    matrix = np.zeros((games.height, 2, size * 2 + 1), dtype=float)
    for i, row in enumerate(games.to_dicts()):
        for j, side in enumerate(("home", "away")):
            other = "away" if side == "home" else "home"
            if row[f"{side}_team"] in lookup:
                matrix[i, j, lookup[row[f"{side}_team"]]] = 1
            if row[f"{other}_team"] in lookup:
                matrix[i, j, size + lookup[row[f"{other}_team"]]] = 1
            matrix[i, j, -1] = row["home_field"] * (0.5 if side == "home" else -0.5)
    return matrix.reshape(-1, size * 2 + 1)


def fit_points(
    train: pl.DataFrame, lock: datetime, *, audit_complexity: bool = False
) -> dict[str, Any]:
    from sklearn.linear_model import Ridge

    if train.filter(pl.col("result_available_at_utc") >= lock).height:
        raise DataQualityError("Points fit includes outcomes unavailable at lock")
    teams = sorted(set(train["home_team"]) | set(train["away_team"]))
    matrix = design(train, teams)
    target = train.select("home_score", "away_score").to_numpy().reshape(-1)
    if not np.isfinite(target).all():
        raise DataQualityError("Points training scores must be finite")
    age = np.array([(lock - t).total_seconds() / 86400 for t in train["kickoff_utc"]])
    weights = np.repeat(2.0 ** (-age / POLICY["recency_half_life_days"]), 2)
    model = Ridge(alpha=POLICY["ridge_alpha"], solver=POLICY["solver"])
    model.fit(matrix, target, sample_weight=weights)
    result = {
        "teams": teams,
        "coefficients": model.coef_.tolist(),
        "intercept": float(model.intercept_),
        "allocated_weights_and_intercepts": len(model.coef_) + 1,
        "weighted_team_observations": float(weights.sum()),
    }
    if audit_complexity:
        centered = matrix - np.average(matrix, axis=0, weights=weights)
        singular = np.linalg.svd(centered * np.sqrt(weights[:, None]), compute_uv=False)
        result["effective_degrees_of_freedom"] = float(
            1 + (singular**2 / (singular**2 + POLICY["ridge_alpha"])).sum()
        )
    return result


def predict_points(test: pl.DataFrame, parameters: dict[str, Any]) -> np.ndarray[Any, Any]:
    coefficients = np.asarray(parameters["coefficients"], dtype=float)
    if coefficients.size != 2 * len(parameters["teams"]) + 1 or not np.isfinite(coefficients).all():
        raise DataQualityError("Invalid scoring rating coefficients")
    values = (design(test, parameters["teams"]) @ coefficients + parameters["intercept"]).reshape(
        -1, 2
    )
    return np.array(
        [
            [
                consistent_scores(float(h), float(a))[k]
                for k in ("predicted_home_score", "predicted_away_score")
            ]
            for h, a in values
        ]
    )


def _new_predictions(
    test: pl.DataFrame, train: pl.DataFrame, parameters: dict[str, Any], controls: pl.DataFrame
) -> pl.DataFrame:
    points = predict_points(test, parameters)
    epa = controls.filter(
        (pl.col("model") == "compact_epa") & pl.col("game_id").is_in(test["game_id"].implode())
    ).sort("game_id")
    if epa["game_id"].to_list() != test["game_id"].to_list():
        raise DataQualityError("EPA and points comparison games differ")
    blend = (epa.select("predicted_home_score", "predicted_away_score").to_numpy() + points) / 2
    return pl.DataFrame(
        forecast_rows(test, {POINTS: points, BLEND: blend}, train), infer_schema_length=None
    ).select(controls.columns)


def evaluate(
    features: pl.DataFrame, controls: pl.DataFrame, seasons: tuple[int, ...]
) -> tuple[pl.DataFrame, list[dict[str, Any]]]:
    replay.validate_features(features)
    history = features.filter(pl.col("horizon") == "early_week")
    selected = history.filter(pl.col("season").is_in(seasons)).sort("season", "week", "game_id")
    records, receipts = [], []
    for test in selected.partition_by("season", "week", maintain_order=True):
        train, receipt = training_fold(history, test)
        parameters = fit_points(
            train, test["week_lock_utc"][0], audit_complexity=test["week"][0] == 1
        )
        receipt["parameters"] = parameters
        parameters["unseen_forecast_teams"] = sorted(
            (set(test["home_team"]) | set(test["away_team"])) - set(parameters["teams"])
        )
        receipts.append(receipt)
        for horizon in HORIZONS:
            current = features.filter(
                (pl.col("horizon") == horizon)
                & (pl.col("season") == receipt["season"])
                & (pl.col("week") == receipt["week"])
            ).sort("game_id")
            base = controls.filter(pl.col("horizon") == horizon)
            records.append(_new_predictions(current, train, parameters, base))
        print(
            f"Compared points {receipt['season']} week {receipt['week']}: {train.height} games",
            flush=True,
        )
    if not records:
        raise DataQualityError("No points comparison weeks")
    result = pl.concat(
        [controls.filter(pl.col("season").is_in(seasons)), *records], how="vertical_relaxed"
    )
    validate_result(result, features, controls, receipts)
    return result, receipts


def validate_result(
    panel: pl.DataFrame,
    features: pl.DataFrame,
    controls: pl.DataFrame,
    receipts: list[dict[str, Any]],
) -> None:
    if set(panel["model"]) != set(ALL_MODELS):
        raise DataQualityError("Points comparison models changed")
    calibration.forecast_bands(panel)
    seasons = sorted(set(panel["season"]))
    sort = ["game_id", "horizon", "model"]
    base = controls.filter(pl.col("season").is_in(seasons)).sort(sort)
    if not panel.filter(pl.col("model").is_in(replay.MODELS)).sort(sort).equals(base):
        raise DataQualityError("Frozen EPA/baseline controls changed")
    history = features.filter(pl.col("horizon") == "early_week")
    expected_weeks = {
        (r["season"], r["week"]) for r in base.select("season", "week").unique().to_dicts()
    }
    if (
        len(receipts) != len(expected_weeks)
        or {(r["season"], r["week"]) for r in receipts} != expected_weeks
    ):
        raise DataQualityError("Missing, duplicate or unexpected points fit receipts")
    for receipt in receipts:
        test = history.filter(
            (pl.col("season") == receipt["season"]) & (pl.col("week") == receipt["week"])
        ).sort("game_id")
        train, expected = training_fold(history, test)
        if {k: v for k, v in receipt.items() if k != "parameters"} != expected:
            raise DataQualityError("Points fit receipt or training data changed")
        for horizon in HORIZONS:
            current = features.filter(
                (pl.col("horizon") == horizon)
                & (pl.col("season") == receipt["season"])
                & (pl.col("week") == receipt["week"])
            ).sort("game_id")
            expected_panel = _new_predictions(
                current, train, receipt["parameters"], base.filter(pl.col("horizon") == horizon)
            )
            actual = panel.filter(
                pl.col("model").is_in([POINTS, BLEND])
                & (pl.col("horizon") == horizon)
                & pl.col("game_id").is_in(current["game_id"].implode())
            ).sort(sort)
            if not expected_panel.sort(sort).equals(actual):
                raise DataQualityError("Points/blend forecasts differ from saved fit parameters")


def choose_model(panel: pl.DataFrame, seasons: tuple[int, ...]) -> dict[str, Any]:
    if not seasons or not set(seasons) <= {2023, 2024}:
        raise DataQualityError("Point-model selection excludes 2025; use 2023-2024 only")
    selected = panel.filter(pl.col("season").is_in(seasons))
    if set(selected["season"]) != set(seasons):
        raise DataQualityError("Missing point-model selection season")
    calibration.forecast_bands(selected)
    metrics = score_metrics(selected)

    def metric(model: str, horizon: str, season: int | None = None) -> dict[str, Any]:
        scope = "overall" if season is None else "season"
        rows = metrics.filter(
            (pl.col("model") == model) & (pl.col("horizon") == horizon) & (pl.col("scope") == scope)
        )
        if season is not None:
            rows = rows.filter(pl.col("season") == season)
        return rows.row(0, named=True)

    audits = []
    for candidate in (POINTS, BLEND):
        checks = []
        gains = []
        for control in POLICY["selection"]["controls"]:
            gain = paired_gain(
                selected.filter(pl.col("horizon") == "early_week"), candidate, control
            )
            gains.append({"control": control, **gain})
            checks += [gain["margin_mae_gain"] >= 0.05, gain["week_block_95_lower"] > 0]
            for horizon in HORIZONS:
                current, prior = metric(candidate, horizon), metric(control, horizon)
                checks += [
                    current["margin_mae"] <= prior["margin_mae"],
                    current["score_mae"] <= prior["score_mae"] + 0.05,
                    current["total_mae"] <= prior["total_mae"] + 0.10,
                ]
                checks += [
                    metric(candidate, horizon, s)["margin_mae"]
                    <= metric(control, horizon, s)["margin_mae"] + 0.25
                    for s in seasons
                ]
        audits.append(
            {
                "model": candidate,
                "eligible": all(checks),
                "paired_gains": gains,
                "early_margin_mae": metric(candidate, "early_week")["margin_mae"],
            }
        )
    eligible = [r for r in audits if r["eligible"]]
    choice = (
        min(eligible, key=lambda r: (r["early_margin_mae"], CANDIDATES.index(r["model"])))["model"]
        if eligible
        else "compact_epa"
    )
    return {
        "model": choice,
        "selection_seasons": list(seasons),
        "2025_outcomes_used": False,
        "audits": audits,
    }


def outcome_metrics(panel: pl.DataFrame) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for period, seasons in (("development", (2023, 2024)), ("2025", (2025,))):
        for name, condition in (
            ("under_21", pl.col("target_home_margin").abs() < 21),
            ("21_or_more", pl.col("target_home_margin").abs() >= 21),
        ):
            subset = panel.filter(pl.col("season").is_in(seasons) & condition)
            if subset.height:
                records.extend(
                    {"period": period, "actual_margin_group": name, **row}
                    for row in score_metrics(subset).filter(pl.col("scope") == "overall").to_dicts()
                )
    return records


def comparison_summary(panel: pl.DataFrame) -> dict[str, Any]:
    gains = []
    for period, seasons in calibration.PERIODS.items():
        subset = panel.filter(pl.col("season").is_in(seasons) & (pl.col("horizon") == "early_week"))
        if subset.is_empty():
            continue
        for model in (POINTS, BLEND):
            for control in ("compact_epa", "prior_team_scores"):
                gains.append(
                    {
                        "period": period,
                        "model": model,
                        "control": control,
                        **paired_gain(subset, model, control),
                    }
                )
    return {
        "metrics": score_metrics(panel).to_dicts(),
        "comparisons": gains,
        "calibration": calibration.analyze(panel),
        "outcome_groups": outcome_metrics(panel),
    }


def load_inputs(data_dir: Path) -> tuple[pl.DataFrame, pl.DataFrame, dict[str, Any]]:
    controls, refs = calibration.load_panels(data_dir)
    features = replay._features(data_dir)
    refs["canonical_manifest_sha256"] = sha256_file(
        data_dir / "processed" / "team_epa" / "manifest.json"
    )
    return features, controls, refs


def load_report(path: Path, data_dir: Path) -> dict[str, Any]:
    report = read_json(path)
    if report["source_sha256"] != source_hash() or report["policy"] != POLICY:
        raise DataQualityError("Points comparison source/policy changed")
    features, controls, refs = load_inputs(data_dir)
    if refs != report["inputs"]:
        raise DataQualityError("Points comparison source inputs changed")
    panel = load_frame(path.parent, report["artifacts"]["predictions"])
    validate_result(panel, features, controls, report["folds"])
    development = panel.filter(pl.col("season") < 2025)
    frozen = read_json(path.parent / "frozen_2025.json")
    protocol = read_json(path.parent / "protocol.json")
    if (
        frozen != report["frozen_2025_choice"]
        or protocol != report["protocol"]
        or protocol["source_sha256"] != source_hash()
        or protocol["inputs"] != refs
        or protocol["policy"] != POLICY
        or frozen["source_sha256"] != source_hash()
        or frozen["inputs"] != refs
        or frozen["choice"] != choose_model(development, (2023, 2024))
        or report["following_season_choice"] != choose_model(development, (2023,))
        or report["new_points_fits"] != {"development": 36, "2025": 18}
        or report["enriched_replay_ready"] is not False
    ):
        raise DataQualityError("Points choices, fit counts or replay scope changed")
    if any(report[k] != v for k, v in comparison_summary(panel).items()):
        raise DataQualityError("Points comparison summaries changed")
    return report


def export_report(report: dict[str, Any], directory: Path, report_dir: Path) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    panel = load_frame(directory, report["artifacts"]["predictions"])
    write_json(report_dir / "results.json", report)
    pl.DataFrame(report["metrics"]).write_csv(report_dir / "metrics.csv")
    pl.DataFrame(report["comparisons"]).write_csv(report_dir / "comparisons.csv")
    pl.DataFrame(report["outcome_groups"]).write_csv(report_dir / "outcome_groups.csv")
    panel.filter(
        (pl.col("season") == 2025)
        & (pl.col("horizon") == "early_week")
        & pl.col("model").is_in([POINTS, BLEND])
    ).sort("game_id", "model").write_csv(report_dir / "candidate_2025.csv")
    calibration.write_charts(report["calibration"], report_dir / "calibration.html")
    lines = [
        "# EPA, opponent-adjusted points, and equal blend",
        "",
        "PEM-inspired transparent scoring ratings, not a reconstruction of the original PEM. "
        "All games retained. EPA and both original controls are reused exactly. "
        "The blend has fixed 50/50 score weights. 2025 is already-examined diagnostic data.",
        "",
        f"2023-only choice for 2024: **{report['following_season_choice']['model']}**. "
        "Development-only frozen 2025 research choice: "
        f"**{report['frozen_2025_choice']['choice']['model']}**.",
        "",
    ]
    metrics = pl.DataFrame(report["metrics"]).filter(
        (pl.col("scope") == "season") & (pl.col("horizon") == "early_week")
    )
    lines += markdown_table(
        metrics.select("season", "model", "games", "margin_mae", "score_mae", "total_mae")
    )
    lines += [
        "",
        "Positive paired gains favor the candidate. All ranges are adaptive research diagnostics, "
        "not fresh holdout evidence. No app-model promotion or enriched replay gate change.",
        "",
    ]
    lines += markdown_table(
        pl.DataFrame(report["comparisons"]).filter(pl.col("period").is_in(["development", "2025"]))
    )
    lines += [
        "",
        "Scores share the original nonnegative tenths contract. Ratings are in points: own offense "
        "minus defense-conceded is power; home advantage is the signed venue coefficient. "
        "Team indicators are unscaled; the same alpha number does not imply the same "
        "regularization strength as scaled EPA. No penalty/recency/blend-weight search.",
        "",
        "[Forecast-band calibration](calibration.html), "
        "[2025 candidate scores](candidate_2025.csv), "
        "[metrics](metrics.csv), and [source/fit/freeze receipts](results.json). "
        "Refresh duplicates Tuesday predictions. Missing rosters/starters remain unresolved.",
    ]
    (report_dir / "findings.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def save_comparison(data_dir: Path, report_dir: Path) -> dict[str, Any]:
    features, controls, refs = load_inputs(data_dir)
    source = source_hash()
    directory = (
        data_dir / "processed" / "team_points_comparison" / f"{source[:12]}-{_digest(refs)[:12]}"
    )
    path = directory / "manifest.json"
    if path.exists():
        report = load_report(path, data_dir)
    else:
        protocol = {
            "recorded_at_utc": datetime.now(UTC).isoformat(),
            "source_sha256": source,
            "inputs": refs,
            "policy": POLICY,
        }
        protocol_path = directory / "protocol.json"
        if protocol_path.exists():
            old = read_json(protocol_path)
            if any(old[k] != protocol[k] for k in ("source_sha256", "inputs", "policy")):
                raise DataQualityError("Never overwrite a changed points protocol")
            protocol = old
        else:
            write_json(protocol_path, protocol)
        development, dev_folds = evaluate(features, controls, (2023, 2024))
        frozen = {
            "recorded_at_utc": datetime.now(UTC).isoformat(),
            "source_sha256": source,
            "inputs": refs,
            "choice": choose_model(development, (2023, 2024)),
            "simulated_first_lock_utc": "2025-09-02T15:00:00+00:00",
        }
        frozen_path = directory / "frozen_2025.json"
        if frozen_path.exists():
            old = read_json(frozen_path)
            if {k: v for k, v in old.items() if k != "recorded_at_utc"} != {
                k: v for k, v in frozen.items() if k != "recorded_at_utc"
            }:
                raise DataQualityError("Never overwrite a changed points 2025 choice")
            frozen = old
        else:
            write_json(frozen_path, frozen)
        diagnostic, test_folds = evaluate(features, controls, (2025,))
        if source_hash() != source:
            raise DataQualityError("Points comparison code changed during run")
        panel = pl.concat([development, diagnostic], how="vertical_relaxed")
        report = {
            "format_version": 1,
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "source_sha256": source,
            "inputs": refs,
            "policy": POLICY,
            "protocol": protocol,
            "runtime": runtime_versions(),
            "following_season_choice": choose_model(development, (2023,)),
            "frozen_2025_choice": frozen,
            "folds": dev_folds + test_folds,
            "new_points_fits": {"development": len(dev_folds), "2025": len(test_folds)},
            "enriched_replay_ready": False,
            **comparison_summary(panel),
            "artifacts": {"predictions": store_frame(directory, "predictions", panel)},
        }
        write_json(path, report)
    export_report(report, directory, report_dir)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument(
        "--report-dir", type=Path, default=Path("reports/local/team_points_comparison")
    )
    args = parser.parse_args()
    report = save_comparison(args.data_dir, args.report_dir)
    print(f"Frozen points-study choice: {report['frozen_2025_choice']['choice']['model']}")


if __name__ == "__main__":
    main()
