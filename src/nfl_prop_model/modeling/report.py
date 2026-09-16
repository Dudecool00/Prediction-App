"""Persist out-of-fold forecasts, fold preprocessing, and a readable comparison."""

import hashlib
import platform
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

import polars as pl

from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.storage import load_frame, read_json, store_frame, write_json
from nfl_prop_model.features.quarterback import FEATURE_COLUMNS
from nfl_prop_model.modeling.evaluate import evaluate

METHOD = {
    "cohort": "All recorded regular-season QB appearances; not a verified pregame starter cohort",
    "warmup_season": 2022,
    "evaluation_seasons": [2023, 2024],
    "reserved_holdout_season": 2025,
    "refit_schedule": "Before the earliest prediction timestamp in each season/week",
    "target_availability": "Kickoff plus 24 hours, strictly before the training cutoff",
    "features": list(FEATURE_COLUMNS),
    "ridge": {"alpha": 1.0, "solver": "svd", "tuning": "none"},
    "preprocessing": "Train-fold median imputation, missing indicators, standard scaling",
    "cold_start_fallback": "Training-fold target mean for rolling forecasts",
    "bias_definition": "Prediction minus actual passing yards",
}


def write_evaluation(data_dir: Path, report_dir: Path) -> dict[str, Any]:
    source_dir = data_dir / "processed" / "2022_2023_2024"
    source = read_json(source_dir / "manifest.json")
    if source["feature_columns"] != list(FEATURE_COLUMNS):
        raise DataQualityError("Processed feature allowlist differs from the baseline contract")
    table = load_frame(source_dir, source["table"])
    predictions, metrics, folds = evaluate(table)
    output_dir = data_dir / "processed" / "baselines_2022_2024"
    prediction_metadata = store_frame(output_dir, "predictions", predictions)
    source_hash = hashlib.sha256()
    package_root = Path(__file__).parents[1]
    for path in sorted(package_root.rglob("*.py")):
        source_hash.update(path.relative_to(package_root).as_posix().encode())
        source_hash.update(path.read_bytes())
    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "method": METHOD,
        "environment": {
            "python": platform.python_version(),
            **{name: version(name) for name in ("polars", "numpy", "scipy", "scikit-learn")},
        },
        "source_table": source["table"],
        "evaluation_source_sha256": source_hash.hexdigest(),
        "predictions": prediction_metadata,
        "counts": {
            "source_rows": table.height,
            "warmup_rows": table.filter(pl.col("season") == 2022).height,
            "evaluated_qb_games": predictions.select("player_id", "game_id").unique().height,
            "folds": len(folds),
            "models": predictions["model"].n_unique(),
            "dropped_evaluation_rows": 0,
        },
        "metrics": metrics.to_dicts(),
        "folds": folds,
    }
    write_json(output_dir / "manifest.json", report)
    write_json(report_dir / "evaluation.json", report)
    lines = [
        "# Milestone 2: chronological baseline evaluation",
        "",
        f"Generated: {report['generated_at_utc']}",
        "",
        "## Scope and method",
        "",
        "2022 supplies initial training history; every recorded QB appearance in 2023 and 2024 "
        "is scored once per model. The 2025 season remains untouched. "
        "All QBs, backups, zero-attempt "
        "games, and newcomers remain. This is a research comparison conditioned on recorded "
        "appearance, not validation of an upcoming-game starter selector.",
        "",
        "Each season/week is one fold. Fit before its earliest pregame timestamp (one hour before "
        "kickoff), using only games whose kickoff plus 24 hours is strictly earlier. Refit weekly "
        "with an expanding training window. A week's outcomes cannot train its own model. "
        "Lagged features remain specific to each game's prediction time.",
        "",
        "- **prior_five_mean:** mean of up to five previous recorded QB games; no history falls "
        "back to the training-fold target mean.",
        "- **season_to_date_mean:** previous games' mean in the same season; falls back to the "
        "prior-five mean, then the training-fold target mean.",
        "- **ridge:** the seven allowed lagged features; train-fold median imputation with missing "
        "indicators, standard scaling, and Ridge with fixed alpha=1, SVD solver. No tuning. "
        "An entirely missing training column is retained with zero imputation. "
        "Forecasts are not clipped.",
        "",
        "## Counts",
        "",
        "| Measure | Value |",
        "| --- | ---: |",
    ]
    lines.extend(f"| {key} | {value} |" for key, value in report["counts"].items())
    for scope, title in (
        ("overall", "Overall comparison"),
        ("season", "By season"),
        ("history", "By observed sample history"),
        ("season_history", "Season and history"),
    ):
        lines.extend(
            [
                "",
                f"## {title}",
                "",
                "| Group | Model | N | MAE | RMSE | Bias |",
                "| --- | --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in metrics.filter(pl.col("scope") == scope).to_dicts():
            lines.append(
                f"| {row['group']} | {row['model']} | {row['n']} | "
                f"{row['mae']:.2f} | {row['rmse']:.2f} | {row['bias']:.2f} |"
            )
    overall = {row["model"]: row for row in metrics.filter(pl.col("scope") == "overall").to_dicts()}
    delta_mae = overall["ridge"]["mae"] - overall["prior_five_mean"]["mae"]
    delta_rmse = overall["ridge"]["rmse"] - overall["prior_five_mean"]["rmse"]
    lines.extend(
        [
            "",
            "## Interpretation and limitations",
            "",
            f"Ridge minus prior-five mean: MAE {delta_mae:+.2f} yards; "
            f"RMSE {delta_rmse:+.2f} yards. "
            "Negative differences favor Ridge. These are descriptive development results, not "
            "evidence of a statistically established improvement or profitable betting.",
            "",
            "MAE is average absolute error; RMSE gives larger misses more weight. "
            "Positive bias means overprediction. Experience buckets count observed games "
            "in this sample, not career games. "
            "Missing subgroup rows mean zero observations, not zero error.",
            "",
            "The 37 historical schedule/starter discrepancies remain unresolved. Starter labels, "
            "2026 membership, current-game attempts, and outcomes are excluded from predictors. "
            "Absent/inactive players are not reconstructed. Retrospective participation is an "
            "availability limitation of this cohort. Historical source revisions and the assumed "
            "24-hour publication delay limit claims about information actually available pregame.",
            "",
            "No probability distribution, calibrated intervals, historical prices, ROI, "
            "or production prediction UI is supplied in this milestone. Fold-level imputations, "
            "scaling, coefficients, cutoffs and sample counts are in evaluation.json. "
            "Coefficients are on standardized features "
            "and describe associations, not causal effects.",
            "",
            "## Provenance",
            "",
            f"Historical table SHA-256: `{source['table']['sha256']}`.",
            "",
            f"Out-of-fold prediction SHA-256: `{prediction_metadata['sha256']}`.",
            "",
            "Data: [nflverse contributors](https://github.com/nflverse/nflverse-data), "
            "[CC BY 4.0 distribution]"
            "(https://github.com/nflverse/nflverse-data/blob/main/LICENSE.md). "
            "This report transforms their data into forecasts and aggregate error metrics.",
            "",
            "Implementation references: [Ridge]"
            "(https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.Ridge.html), "
            "[imputation]"
            "(https://scikit-learn.org/stable/modules/generated/"
            "sklearn.impute.SimpleImputer.html), "
            "[train-only preprocessing](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage).",
            "",
        ]
    )
    (report_dir / "evaluation.md").write_text("\n".join(lines), encoding="utf-8")
    return report
