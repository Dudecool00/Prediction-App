"""Outcome-conditioned diagnostics of saved forecasts; never train or alter a model."""

import argparse
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from nfl_prop_model.data.schemas import DataQualityError, require_keys
from nfl_prop_model.data.storage import load_frame, sha256_file, write_json
from nfl_prop_model.features.team_epa import HORIZONS
from nfl_prop_model.modeling import team_compact_replay as replay
from nfl_prop_model.modeling.reporting import markdown_table
from nfl_prop_model.modeling.team_scores import score_metrics
from nfl_prop_model.modeling.team_simplicity import POLICY as SELECTION_POLICY
from nfl_prop_model.modeling.team_simplicity import paired_gain

GROUPS = ("all_games", "actual_margin_under_21", "actual_margin_21_or_more")
POLICY = {
    "scope": "user-requested post-hoc 2025 diagnostic, conditioned on actual final margin",
    "exclusion": "abs(actual home score - actual away score) >= 21; same game IDs for every model",
    "groups": list(GROUPS),
    "fits": 0,
    "training": "unchanged; reuse verified compact replay predictions and controls",
    "bootstrap": SELECTION_POLICY["bootstrap"],
    "horizons": "reported separately; identical refresh is not independent evidence",
    "limits": "outcome filter unavailable pregame; not a replacement primary evaluation or holdout",
    "enriched_replay_ready": False,
}


def cohorts(panel: pl.DataFrame) -> dict[str, pl.DataFrame]:
    require_keys(panel, ["game_id", "horizon", "model"], "margin sensitivity panel")
    if (
        set(panel["season"]) != {2025}
        or set(panel["model"]) != set(replay.MODELS)
        or set(panel["horizon"]) != set(HORIZONS)
    ):
        raise DataQualityError("Sensitivity requires the 2025 replay models and both horizons")
    facts = ("season", "week", "home_score", "away_score", "target_home_margin")
    if (
        panel.filter(pl.any_horizontal(pl.col(facts).is_null())).height
        or panel.select(pl.any_horizontal(~pl.col(facts).is_finite()).any()).item()
        or panel.group_by("game_id")
        .agg(pl.col(facts).n_unique())
        .select(pl.any_horizontal(pl.col(facts) != 1).any())
        .item()
        or panel.filter(
            (pl.col("home_score") - pl.col("away_score") - pl.col("target_home_margin")).abs()
            > 1e-8
        ).height
    ):
        raise DataQualityError("Sensitivity outcomes are missing, nonfinite or inconsistent")
    expected = set(panel["game_id"])
    for rows in panel.partition_by("model", "horizon"):
        if set(rows["game_id"]) != expected:
            raise DataQualityError("Sensitivity candidates must have identical game cohorts")
    return {
        GROUPS[0]: panel,
        GROUPS[1]: panel.filter(pl.col("target_home_margin").abs() < 21),
        GROUPS[2]: panel.filter(pl.col("target_home_margin").abs() >= 21),
    }


def analyze(panel: pl.DataFrame) -> dict[str, Any]:
    metrics: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []
    weekly: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    for name, group in cohorts(panel).items():
        early = group.filter(pl.col("horizon") == "early_week")
        games = early["game_id"].n_unique()
        coverage.append(
            {"subset": name, "games": games, "observed_week_blocks": early["week"].n_unique()}
        )
        if not games:
            continue
        metrics.extend(
            {"subset": name, **row}
            for row in score_metrics(group).filter(pl.col("scope") == "overall").to_dicts()
        )
        for control in replay.MODELS[1:]:
            comparisons.append(
                {"subset": name, "control": control, **paired_gain(early, "compact_epa", control)}
            )
        for week in group.sort("week").partition_by("week"):
            weekly.extend(
                {"subset": name, "week": week["week"][0], **row}
                for row in score_metrics(week).filter(pl.col("scope") == "overall").to_dicts()
            )
    return {
        "coverage": coverage,
        "metrics": metrics,
        "comparisons": comparisons,
        "weekly_metrics": weekly,
    }


def write_charts(report: dict[str, Any], panel: pl.DataFrame, path: Path) -> None:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    metrics = pl.DataFrame(report["metrics"]).filter(pl.col("horizon") == "early_week")
    fig = make_subplots(
        rows=2,
        cols=2,
        subplot_titles=(
            "Margin MAE: same games for every method",
            "Team-score MAE",
            "Weekly margin MAE: actual margins below 21",
            "Retained games: actual vs predicted margin",
        ),
    )
    counts = {row["subset"]: row["games"] for row in report["coverage"]}
    labels = [
        f"All {counts[GROUPS[0]]}",
        f"Retained {counts[GROUPS[1]]} (<21)",
        f"Excluded {counts[GROUPS[2]]} (≥21)",
    ]
    colors = ("#176B9A", "#C45C22", "#3B7A57")
    for model, color in zip(replay.MODELS, colors, strict=True):
        rows = metrics.filter(pl.col("model") == model)
        for col, metric in ((1, "margin_mae"), (2, "score_mae")):
            values = [
                rows.filter(pl.col("subset") == s)[metric][0]
                if rows.filter(pl.col("subset") == s).height
                else None
                for s in GROUPS
            ]
            fig.add_trace(
                go.Bar(
                    x=labels,
                    y=values,
                    name=model,
                    legendgroup=model,
                    showlegend=col == 1,
                    marker_color=color,
                    text=[f"{v:.2f}" if v is not None else "" for v in values],
                    textposition="outside",
                ),
                row=1,
                col=col,
            )
        weekly = (
            pl.DataFrame(report["weekly_metrics"])
            .filter(
                (pl.col("subset") == GROUPS[1])
                & (pl.col("horizon") == "early_week")
                & (pl.col("model") == model)
            )
            .sort("week")
        )
        fig.add_trace(
            go.Scatter(
                x=weekly["week"].to_list(),
                y=weekly["margin_mae"].to_list(),
                name=model,
                legendgroup=model,
                showlegend=False,
                mode="lines+markers",
                line={"color": color},
            ),
            row=2,
            col=1,
        )
    kept = cohorts(panel)[GROUPS[1]].filter(
        (pl.col("horizon") == "early_week") & (pl.col("model") == "compact_epa")
    )
    fig.add_trace(
        go.Scatter(
            x=kept["target_home_margin"].to_list(),
            y=kept["predicted_home_margin"].to_list(),
            mode="markers",
            text=kept["game_id"].to_list(),
            marker={"color": colors[0]},
            name="Compact EPA",
            showlegend=False,
            hovertemplate="%{text}<br>Actual %{x}; predicted %{y}<extra></extra>",
        ),
        row=2,
        col=2,
    )
    fig.add_trace(
        go.Scatter(
            x=[-21, 21],
            y=[-21, 21],
            mode="lines",
            showlegend=False,
            line={"color": "gray", "dash": "dash"},
        ),
        row=2,
        col=2,
    )
    fig.update_xaxes(title_text="2025 week", dtick=1, row=2, col=1)
    fig.update_xaxes(title_text="Actual home margin", row=2, col=2)
    fig.update_yaxes(title_text="Predicted home margin", row=2, col=2)
    for col in (1, 2):
        fig.update_yaxes(title_text="Error (points)", row=1, col=col)
    fig.update_yaxes(title_text="Margin MAE (points)", row=2, col=1)
    fig.update_layout(
        template="plotly_white",
        barmode="group",
        height=950,
        title="2025 post-hoc margin sensitivity · saved Tuesday predictions · zero refits",
    )
    fig.write_html(path, include_plotlyjs="cdn")


def save_analysis(data_dir: Path, report_dir: Path) -> dict[str, Any]:
    paths = list(
        (data_dir / "processed" / "team_compact_replay").glob(
            f"{replay.source_hash()[:12]}-*/manifest.json"
        )
    )
    if len(paths) != 1:
        raise DataQualityError("Require one current verified compact replay")
    path = paths[0]
    source = replay.load_compact_replay(path, data_dir)
    panel = load_frame(path.parent, source["artifacts"]["predictions"])
    report = {
        "format_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "policy": POLICY,
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "inputs": {
            "replay_manifest_sha256": sha256_file(path),
            "replay_source_sha256": source["source_sha256"],
            "freeze_sha256": source["freeze_sha256"],
            "predictions_sha256": source["artifacts"]["predictions"]["sha256"],
        },
        **analyze(panel),
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    write_json(report_dir / "results.json", report)
    for name in ("metrics", "comparisons", "weekly_metrics", "coverage"):
        pl.DataFrame(report[name]).write_csv(report_dir / f"{name}.csv")
    write_charts(report, panel, report_dir / "comparison.html")
    metrics = pl.DataFrame(report["metrics"]).filter(pl.col("horizon") == "early_week")
    lines = [
        "# 2025 sensitivity: exclude actual final margins of 21+",
        "",
        "Saved frozen forecasts, no retraining. Identical games are selected for every method. "
        "The excluded group includes wins by exactly 21 points, for either team. "
        "Refresh duplicates Tuesday scores and is reported separately in the data.",
        "",
    ]
    lines += markdown_table(
        metrics.select(
            "subset", "model", "games", "margin_mae", "score_mae", "total_mae", "margin_rmse"
        )
    )
    lines += [
        "",
        "Positive paired margin gain favors compact EPA. Week-block 95% ranges are "
        "descriptive for this outcome-selected subset; they are not independent evidence "
        "of pregame selection skill.",
        "",
    ]
    lines += markdown_table(pl.DataFrame(report["comparisons"]))
    lines += [
        "",
        "The original all-game primary evaluation is preserved. This outcome-defined "
        "filter is unavailable before kickoff; it cannot justify replacing the full-season "
        "MAE or treating these as forecasts of close games. The enriched replay remains gated.",
    ]
    (report_dir / "findings.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument(
        "--report-dir", type=Path, default=Path("reports/local/team_margin_sensitivity")
    )
    args = parser.parse_args()
    save_analysis(args.data_dir, args.report_dir)
    print(f"Saved post-hoc sensitivity: {args.report_dir / 'findings.md'}. Zero new fits.")


if __name__ == "__main__":
    main()
