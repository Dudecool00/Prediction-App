"""Check settlement rounding and integer pushes without inventing historical prices."""

from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nfl_prop_model.data.storage import load_frame, read_json, write_json
from nfl_prop_model.markets.distribution import DISTRIBUTION_VERSION, passing_yard_probabilities
from nfl_prop_model.modeling.research import probability_diagnostics
from nfl_prop_model.modeling.research_report import markdown_table
from nfl_prop_model.modeling.uncertainty import ResidualCalibration


def settlement_scores(
    predictions: pl.DataFrame, residuals: pl.DataFrame
) -> tuple[pl.DataFrame, pl.DataFrame]:
    if predictions.filter(pl.col("actual") != pl.col("actual").floor()).height:
        raise ValueError("Discrete settlement requires whole-yard observed outcomes")
    calibrations = {
        key: ResidualCalibration(np.asarray(group["residual"].to_numpy(), dtype=np.float64))
        for key, group in residuals.partition_by("fold", "model", as_dict=True).items()
    }
    rows = []
    for row in predictions.iter_rows(named=True):
        calibration = calibrations[(row["fold"], row["model"])]
        for line in (150.0, 150.5, 200.0, 200.5, 250.0, 250.5, 300.0, 300.5):
            values = passing_yard_probabilities(row["prediction"], calibration, line)
            rows.append(
                {
                    "player_id": row["player_id"],
                    "game_id": row["game_id"],
                    "model": row["model"],
                    "season": row["season"],
                    "line": line,
                    "probability_over": values.over,
                    "outcome_over": int(row["actual"] > line),
                    "probability_push": values.push,
                    "outcome_push": int(row["actual"] == line),
                }
            )
    probabilities = pl.DataFrame(rows)
    integer = probabilities.filter(pl.col("line") == pl.col("line").floor())
    push_scores = (
        integer.group_by("model", "line")
        .agg(
            pl.len().alias("n"),
            pl.col("probability_push").mean().alias("mean_predicted_push_probability"),
            pl.col("outcome_push").mean().alias("observed_push_rate"),
            ((pl.col("probability_push") - pl.col("outcome_push")) ** 2).mean().alias("push_brier"),
            ((pl.col("probability_push") == 0) & (pl.col("outcome_push") == 1))
            .sum()
            .alias("pushes_given_zero_probability"),
        )
        .sort("model", "line")
    )
    return probabilities, push_scores


def write_settlement_audit(data_dir: Path, report_dir: Path) -> dict[str, Any]:
    directory = data_dir / "processed" / "research_2022_2024"
    manifest = read_json(directory / "manifest.json")
    if manifest.get("format_version") != 2:
        raise ValueError("Run nfl-prop research to save reusable calibration errors first")
    artifacts = manifest["artifacts"]
    predictions = load_frame(directory, artifacts["predictions"])
    residuals = load_frame(directory, artifacts["calibration_residuals"])
    probabilities, pushes = settlement_scores(predictions, residuals)
    half = probabilities.filter(pl.col("line") != pl.col("line").floor())
    scores, _ = probability_diagnostics(half)
    previous = load_frame(directory, artifacts["probabilities"])
    differences = half.join(
        previous.select("player_id", "game_id", "model", "line", "probability_over"),
        on=["player_id", "game_id", "model", "line"],
        validate="1:1",
        suffix="_previous",
    ).with_columns(
        (pl.col("probability_over") - pl.col("probability_over_previous")).abs().alias("change")
    )
    if differences.height != half.height:
        raise ValueError("Settlement comparison lost historical probability rows")
    comparison = (
        differences.group_by("model")
        .agg(
            pl.len().alias("n"),
            pl.col("change").max().alias("max_probability_change"),
            pl.col("change").mean().alias("mean_probability_change"),
        )
        .sort("model")
    )
    report = {
        "distribution_version": DISTRIBUTION_VERSION,
        "source_artifacts": artifacts,
        "counts": {"forecasts": predictions.height, "half_line_probabilities": half.height},
        "half_line_probability_scores": scores.to_dicts(),
        "changes_from_continuous_residual_tail": comparison.to_dicts(),
        "integer_push_diagnostics": pushes.to_dicts(),
    }
    write_json(report_dir / "settlement_audit.json", report)
    lines = [
        "# Settlement probability audit",
        "",
        "The same saved 2023–2024 forecasts and calibration errors are used. Thresholds are "
        "fixed diagnostic events, with no historical prices or ROI. This checks the explicit "
        "whole-yard rounding approximation added for manual integer/half-yard markets.",
        "",
        "## Half-yard probability change",
        "",
        "Compare the rounded settlement CDF with Milestone 3's continuous signed-residual "
        "tail at 150.5, 200.5, 250.5, and 300.5. Differences can arise at exact boundaries.",
        "",
        *markdown_table(comparison),
        "",
        "## Half-yard probability scores",
        "",
        *markdown_table(scores),
        "",
        "## Integer push diagnostics",
        "",
        "These rows check 150, 200, 250, and 300 yards. Push mass is sparse and may be zero "
        "even when a push occurs. The last column exposes those failures; integer push "
        "probabilities are experimental. No conditional or individual-player guarantee is made.",
        "",
        *markdown_table(pushes),
        "",
        "Push Brier is mean squared error for the push event; rare events can score well "
        "with nearly zero predictions. Assess counts alongside the score. Model inputs, "
        "point errors, original interval bounds, and held-out season usage are unchanged.",
        "",
    ]
    (report_dir / "settlement_audit.md").write_text("\n".join(lines), encoding="utf-8")
    return report
