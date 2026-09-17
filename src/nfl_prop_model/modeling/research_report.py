"""Reproducible point, interval, and threshold-probability research reports."""

import hashlib
import platform
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

import plotly.graph_objects as go
import polars as pl
from plotly.subplots import make_subplots

from nfl_prop_model.data.ingest_nfl import load_snapshot
from nfl_prop_model.data.storage import load_frame, read_json, store_frame, write_json
from nfl_prop_model.features.context import add_context_features
from nfl_prop_model.features.quarterback import FEATURE_COLUMNS
from nfl_prop_model.modeling.research import (
    COVERAGES,
    DIAGNOSTIC_THRESHOLDS,
    TREE_FEATURES,
    TREE_PARAMETERS,
    evaluate_research,
)


def calibration_chart(reliability: pl.DataFrame, path: Path) -> None:
    figure = make_subplots(
        rows=2,
        cols=2,
        subplot_titles=[f"Diagnostic threshold: {line} yards" for line in DIAGNOSTIC_THRESHOLDS],
    )
    models = reliability["model"].unique().sort().to_list()
    colors = ["#64748b", "#2563eb", "#d97706", "#059669", "#9333ea", "#dc2626"]
    for index, line in enumerate(DIAGNOSTIC_THRESHOLDS):
        row, column = index // 2 + 1, index % 2 + 1
        figure.add_trace(
            go.Scatter(
                x=[0, 1],
                y=[0, 1],
                mode="lines",
                line={"color": "#a1a1aa", "dash": "dash"},
                showlegend=False,
                hoverinfo="skip",
            ),
            row=row,
            col=column,
        )
        for model, color in zip(models, colors, strict=True):
            group = reliability.filter((pl.col("line") == line) & (pl.col("model") == model)).sort(
                "bin"
            )
            figure.add_trace(
                go.Scatter(
                    x=group["mean_predicted_probability"].to_list(),
                    y=group["observed_over_rate"].to_list(),
                    mode="lines+markers",
                    name=model,
                    legendgroup=model,
                    showlegend=index == 0,
                    line={"color": color},
                    customdata=group["n"].to_list(),
                    hovertemplate=(
                        "Predicted: %{x:.1%}<br>Observed: %{y:.1%}<br>N: %{customdata}"
                        "<extra>%{fullData.name}</extra>"
                    ),
                ),
                row=row,
                col=column,
            )
    figure.update_xaxes(
        title_text="Mean predicted over probability", range=[0, 1], tickformat=".0%"
    )
    figure.update_yaxes(title_text="Observed over rate", range=[0, 1], tickformat=".0%")
    figure.update_layout(
        title="Chronological calibration · 2023–2024 recorded QB appearances",
        template="plotly_white",
        height=850,
        legend={"orientation": "h", "y": -0.15},
        margin={"t": 95, "b": 135},
    )
    figure.write_html(
        path,
        include_plotlyjs=True,
        full_html=True,
        config={"displaylogo": False, "responsive": True},
    )


def markdown_table(frame: pl.DataFrame) -> list[str]:
    lines = [
        "| " + " | ".join(frame.columns) + " |",
        "| " + " | ".join(["---"] * frame.width) + " |",
    ]
    for row in frame.iter_rows():
        lines.append(
            "| " + " | ".join(f"{v:.3f}" if isinstance(v, float) else str(v) for v in row) + " |"
        )
    return lines


def feature_contributions(metrics: pl.DataFrame) -> pl.DataFrame:
    comparisons = []
    for scope in ("overall", "season"):
        groups = metrics.filter(pl.col("scope") == scope)["group"].unique().sort()
        for group in groups:
            values = {
                row["model"]: row
                for row in metrics.filter(
                    (pl.col("scope") == scope) & (pl.col("group") == group)
                ).to_dicts()
            }
            for before, after, added in (
                ("xgb_qb", "xgb_schedule", "schedule"),
                ("xgb_schedule", "xgb_context", "opponent"),
            ):
                comparisons.append(
                    {
                        "group": group,
                        "added_features": added,
                        "mae_change": values[after]["mae"] - values[before]["mae"],
                        "rmse_change": values[after]["rmse"] - values[before]["rmse"],
                    }
                )
    return pl.DataFrame(comparisons)


def write_research(data_dir: Path, report_dir: Path) -> dict[str, Any]:
    directory = data_dir / "processed" / "2022_2023_2024"
    source = read_json(directory / "manifest.json")
    if source["feature_columns"] != list(FEATURE_COLUMNS):
        raise ValueError("Historical feature allowlist does not match the research contract")
    historical = load_frame(directory, source["table"])
    snapshot = load_snapshot(data_dir, [2022, 2023, 2024])
    # Do not combine a revised raw cache with an older processed QB history.
    if source["raw_manifest"]["datasets"] != snapshot.manifest["datasets"]:
        raise ValueError("Raw/processed snapshots differ; rebuild the historical table first")
    table, context = add_context_features(historical, snapshot.player_stats, snapshot.schedules)
    result = evaluate_research(table)
    contributions = feature_contributions(result.point_metrics)
    output = data_dir / "processed" / "research_2022_2024"
    artifacts = {
        name: store_frame(output, name, frame)
        for name, frame in {
            "features": table,
            "predictions": result.predictions,
            "intervals": result.intervals,
            "probabilities": result.probabilities,
        }.items()
    }
    expected = table.filter(pl.col("season").is_in([2023, 2024])).height
    scored = result.predictions.select("game_id", "player_id").unique().height
    if expected != scored or result.predictions.height != expected * 6:
        raise ValueError("Research evaluation did not score every QB-game for every model")
    interval_overall = (
        result.intervals.group_by("model", "coverage")
        .agg(
            pl.len().alias("n"),
            ((pl.col("actual") >= pl.col("lower")) & (pl.col("actual") <= pl.col("upper")))
            .mean()
            .alias("observed_coverage"),
            (pl.col("upper") - pl.col("lower")).mean().alias("mean_width"),
        )
        .sort("coverage", "model")
    )
    history_coverage = (
        result.intervals.group_by("model", "coverage", "history_bucket")
        .agg(
            pl.len().alias("n"),
            ((pl.col("actual") >= pl.col("lower")) & (pl.col("actual") <= pl.col("upper")))
            .mean()
            .alias("observed_coverage"),
            (pl.col("upper") - pl.col("lower")).mean().alias("mean_width"),
        )
        .sort("coverage", "model", "history_bucket")
    )
    source_hash = hashlib.sha256()
    root = Path(__file__).parents[1]
    for path in sorted(root.rglob("*.py")):
        source_hash.update(path.relative_to(root).as_posix().encode())
        source_hash.update(path.read_bytes())
    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "historical_source": source["table"],
        "context_sources": snapshot.manifest,
        "context": context,
        "artifacts": artifacts,
        "research_source_sha256": source_hash.hexdigest(),
        "environment": {
            "python": platform.python_version(),
            **{
                name: version(name)
                for name in ("polars", "numpy", "scipy", "scikit-learn", "xgboost-cpu", "plotly")
            },
        },
        "method": {
            "calibration_weeks": 6,
            "minimum_calibration_rows": 100,
            "nominal_coverages": COVERAGES,
            "diagnostic_thresholds": DIAGNOSTIC_THRESHOLDS,
            "tree_parameters": TREE_PARAMETERS,
            "tree_feature_groups": TREE_FEATURES,
            "hyperparameter_tuning": "none",
            "reserved_season": 2025,
        },
        "counts": {
            "source_qb_games": table.height,
            "evaluated_qb_games": scored,
            "weekly_folds": len(result.folds),
            "forecasts_per_qb_game": 6,
            "dropped_evaluation_rows": expected - scored,
        },
        "point_metrics": result.point_metrics.to_dicts(),
        "feature_contributions": contributions.to_dicts(),
        "interval_metrics": result.interval_metrics.to_dicts(),
        "interval_overall": interval_overall.to_dicts(),
        "interval_by_history": history_coverage.to_dicts(),
        "probability_metrics": result.probability_metrics.to_dicts(),
        "reliability": result.reliability.to_dicts(),
        "folds": result.folds,
    }
    write_json(output / "manifest.json", report)
    write_json(report_dir / "research.json", report)
    calibration_chart(result.reliability, report_dir / "calibration.html")
    lines = [
        "# Milestone 3: nonlinear forecasts and chronological uncertainty",
        "",
        f"Generated: {report['generated_at_utc']}",
        "",
        "## Method",
        "",
        "Evaluate every recorded 2023–2024 QB appearance, including backups and newcomers. "
        "2022 supplies initial history; 2025 remains untouched. Retrain before each evaluation "
        "week. Reserve the latest six eligible weeks for calibration, fit on earlier games, "
        "and require at least 100 calibration rows. Results must clear kickoff plus 24 hours "
        "before the relevant cutoff. Training, calibration, and evaluation games are separate.",
        "",
        "Compare two rolling forecasts, Ridge, and one fixed XGBoost model with three feature "
        "sets: QB history; QB plus schedule; QB plus schedule and opponent context. All six "
        "use the same training/calibration/evaluation cohorts. No tuning or early stopping uses "
        "evaluation outcomes. The baseline numbers differ from Milestone 2 because recent "
        "training observations are now reserved for calibration.",
        "",
        "XGBoost uses 150 depth-2 trees, learning rate 0.05, min_child_weight=10, L2 penalty=10, "
        "full row/column sampling, histogram bins=256, squared-error objective, CPU, one worker, "
        "and seed 42. CPU-only version 3.2.0 retains Python 3.11 support. Missing values follow "
        "learned tree directions; the Ridge pipeline fits imputation/scaling only on "
        "training rows.",
        "",
        "Schedule features are designated home, neutral site, and each team's days since its "
        "previous regular-season game in that season. Opponent features are prior-five-game "
        "gross passing yards allowed and pass attempts faced, including all recorded passers. "
        "Opponent history is shifted and checked for pregame availability.",
        "",
        "## Point errors (yards)",
        "",
        *markdown_table(
            result.point_metrics.filter(pl.col("scope") == "overall").drop("scope", "group")
        ),
        "",
        "## Point errors by season",
        "",
        *markdown_table(result.point_metrics.filter(pl.col("scope") == "season").drop("scope")),
        "",
        "## Incremental feature contribution",
        "",
        "Changes compare identical rows and fixed tree settings. Negative error changes are "
        "better. These are descriptive development comparisons without a significance claim.",
        "",
        *markdown_table(contributions),
        "",
        "## Point errors by observed history",
        "",
        *markdown_table(result.point_metrics.filter(pl.col("scope") == "history").drop("scope")),
        "",
        "## Interval coverage and width",
        "",
        "Intervals use the ceil((n+1) × nominal coverage)-th ordered absolute calibration error. "
        "Bounds are the raw point forecast plus/minus that radius, without clipping. Coverage "
        "below is an observed fraction, not a promised success rate.",
        "",
        *markdown_table(interval_overall),
        "",
        "## 90% interval coverage by observed history",
        "",
        *markdown_table(history_coverage.filter(pl.col("coverage") == 0.9).drop("coverage")),
        "",
        "## Diagnostic threshold probability scores",
        "",
        "Thresholds 150.5, 200.5, 250.5, and 300.5 yards were fixed before evaluation; they are "
        "diagnostic events, not historical sportsbook lines. Over probabilities use the signed "
        "calibration residual tail with 0.5-count smoothing. Positive/negative residual patterns "
        "therefore affect these probabilities. No Gaussian error shape is assumed. Symmetric "
        "absolute-error intervals and the signed-residual CDF are distinct summaries.",
        "",
        *markdown_table(result.probability_metrics),
        "",
        "Open `calibration.html` beside this report for four interactive reliability plots. "
        "It embeds Plotly for offline use and is regenerated rather than committed. Each point "
        "compares mean forecast probability with observed frequency in a fixed ten-percent bin. "
        "Hover for sample counts; empty bins are omitted. All bin values are in research.json.",
        "",
        "## Limits and next checks",
        "",
        "Football observations are time-dependent, repeated by player, and clustered by game. "
        "The exchangeability assumption behind split-conformal guarantees is not established. "
        "Report empirical coverage and width, including history subgroups; nominal 90% is not "
        "a guarantee for a particular player. Tail estimates and small bins are uncertain. "
        "These probabilities are initial research estimates, not validated betting probabilities.",
        "",
        "The cohort is conditioned on recorded participation. The 37 historical starter-label "
        "discrepancies remain open, and source corrections may postdate games. Current depth "
        "charts never filter historical rows or enter predictors. Models have no injuries or "
        "confirmed pregame starter feature. No prices, ROI, EV, or production UI are evaluated.",
        "",
        "Weather remains a separate data-readiness task: the 2023 GFS prior-day sample supplied "
        "temperature but no wind/precipitation. A verified stadium map and roof policy are also "
        "needed. Missing historical forecasts were not replaced by observed weather or zeros.",
        "",
        "## Provenance",
        "",
        f"Historical QB-table hash: `{source['table']['sha256']}`.",
        "",
        f"Prediction hash: `{artifacts['predictions']['sha256']}`.",
        "",
        "Data: [nflverse](https://github.com/nflverse/nflverse-data), "
        "[CC BY 4.0](https://github.com/nflverse/nflverse-data/blob/main/LICENSE.md). "
        "This report aggregates their data and derives forecasts and diagnostics.",
        "",
        "Methods: [XGBoost](https://xgboost.readthedocs.io/en/release_3.2.0/), "
        "[conformal prediction overview](https://arxiv.org/abs/2107.07511), "
        "[archived forecast coverage](https://open-meteo.com/en/docs/previous-runs-api).",
        "",
    ]
    (report_dir / "research.md").write_text("\n".join(lines), encoding="utf-8")
    return report
