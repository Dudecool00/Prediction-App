"""Saved season diagnostics; descriptive outcome analysis never changes the forecast model."""

import hashlib
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nfl_prop_model.data.schemas import DataQualityError, require_keys
from nfl_prop_model.data.storage import load_frame, read_json, store_frame, write_json
from nfl_prop_model.data.team_games import load_team_cache
from nfl_prop_model.features.team_epa import HORIZONS
from nfl_prop_model.modeling.reporting import markdown_table
from nfl_prop_model.modeling.team_scores import (
    MODEL_POLICY,
    evaluate_team_scores,
    model_source_hash,
    runtime_versions,
)

LABELS = {
    "league_scores": "League scoring",
    "prior_team_scores": "Prior team scoring",
    "ridge_epa_scores": "Current EPA",
}
DIAGNOSTIC_POLICY = {
    "prediction": "unchanged current EPA and two scoring controls; expanding chronological fits",
    "scope": "completed regular-season games; both forecast horizons; future seasons excluded",
    "interpretation": "retrospective descriptive diagnostic; 2025 already accessed, "
    "not a fresh holdout",
    "outcome_groups": "actual margin bands and error-ranked games are postgame descriptions only",
    "calibration": "actual-on-predicted slope/intercept are descriptive; no recalibration applied",
    "league_calibration": "omitted: nearly constant home-edge baseline is not a matchup rating",
    "decision": "no model selection, feature tuning or forecast change from this report",
}


def diagnostic_source_hash() -> str:
    return hashlib.sha256(model_source_hash().encode() + Path(__file__).read_bytes()).hexdigest()


def diagnostic_tables(predictions: pl.DataFrame) -> dict[str, pl.DataFrame]:
    require_keys(predictions, ["game_id", "horizon", "model"], "season diagnostic predictions")
    rows = predictions.with_columns(
        (pl.col("predicted_home_margin") - pl.col("target_home_margin")).alias("margin_error"),
        (pl.col("predicted_home_score") - pl.col("home_score")).alias("home_error"),
        (pl.col("predicted_away_score") - pl.col("away_score")).alias("away_error"),
    ).with_columns(pl.col("margin_error").abs().alias("absolute_margin_error"))
    stats = []
    for group in rows.partition_by("horizon", "model", maintain_order=True):
        actual = group["target_home_margin"].to_numpy()
        predicted = group["predicted_home_margin"].to_numpy()
        errors = predicted - actual
        absolute = np.abs(errors)
        centered_actual = actual - actual.mean()
        centered_predicted = predicted - predicted.mean()
        variance = float(centered_predicted @ centered_predicted)
        varying_matchups = group["model"][0] != "league_scores"
        slope = (
            float(centered_predicted @ centered_actual / variance)
            if variance and varying_matchups
            else None
        )
        corr = (
            float(np.corrcoef(predicted, actual)[0, 1])
            if variance and np.std(actual) and varying_matchups
            else None
        )
        home = group["home_error"].to_numpy()
        away = group["away_error"].to_numpy()
        pair_corr = float(np.corrcoef(home, away)[0, 1]) if np.std(home) and np.std(away) else None
        tail_count = min(10, group.height)
        stats.append(
            {
                "horizon": group["horizon"][0],
                "model": group["model"][0],
                "games": group.height,
                "margin_mae": float(absolute.mean()),
                "margin_rmse": float(np.sqrt(np.mean(errors**2))),
                "margin_bias": float(errors.mean()),
                "median_absolute_error": float(np.median(absolute)),
                "p90_absolute_error": float(np.quantile(absolute, 0.9)),
                "predicted_margin_sd": float(np.std(predicted)),
                "actual_margin_sd": float(np.std(actual)),
                "margin_correlation": corr,
                "margin_r2": float(1 - errors @ errors / (centered_actual @ centered_actual))
                if np.std(actual)
                else None,
                "descriptive_calibration_slope": slope,
                "descriptive_calibration_intercept": float(actual.mean() - slope * predicted.mean())
                if slope is not None
                else None,
                "home_away_score_error_correlation": pair_corr,
                "within_7_points_fraction": float(np.mean(absolute <= 7)),
                "within_14_points_fraction": float(np.mean(absolute <= 14)),
                "largest_10_errors_share": float(
                    np.sort(absolute)[-tail_count:].sum() / absolute.sum()
                )
                if absolute.sum()
                else 0.0,
                "largest_10_error_games": tail_count,
            }
        )
    weekly = (
        rows.group_by("horizon", "model", "week")
        .agg(
            pl.len().alias("games"),
            pl.col("absolute_margin_error").mean().alias("margin_mae"),
            pl.col("margin_error").mean().alias("margin_bias"),
        )
        .sort("horizon", "model", "week")
    )
    current = rows.filter(pl.col("model") == "ridge_epa_scores")
    bands = (
        current.with_columns(
            pl.when(pl.col("target_home_margin").abs() <= 7)
            .then(pl.lit("Actual margin 0–7"))
            .when(pl.col("target_home_margin").abs() <= 20)
            .then(pl.lit("Actual margin 8–20"))
            .otherwise(pl.lit("Actual margin 21+"))
            .alias("outcome_band")
        )
        .group_by("horizon", "outcome_band")
        .agg(
            pl.len().alias("games"),
            pl.col("absolute_margin_error").mean().alias("margin_mae"),
            pl.col("absolute_margin_error").sum().alias("absolute_error_points"),
        )
        .sort("horizon", "outcome_band")
    )
    team_rows = pl.concat(
        [
            current.select(
                "game_id",
                "week",
                "horizon",
                "absolute_margin_error",
                pl.col(f"{side}_team").alias("team"),
                pl.col(f"{other}_team").alias("opponent"),
                pl.col(f"{side}_score").alias("actual_score"),
                pl.col(f"predicted_{side}_score").alias("predicted_score"),
                (pl.col("target_home_margin") * sign).alias("actual_margin"),
                (pl.col("predicted_home_margin") * sign).alias("predicted_margin"),
            )
            for side, other, sign in (("home", "away", 1), ("away", "home", -1))
        ]
    ).sort("horizon", "team", "week")
    team_summary = (
        team_rows.group_by("horizon", "team")
        .agg(
            pl.len().alias("games"),
            pl.col("absolute_margin_error").mean().alias("margin_mae"),
            (pl.col("predicted_score") - pl.col("actual_score")).abs().mean().alias("score_mae"),
        )
        .sort("horizon", "margin_mae", descending=[False, True])
    )
    top = pl.concat(
        [
            group.sort("absolute_margin_error", descending=True).head(10)
            for group in current.partition_by("horizon", maintain_order=True)
        ]
    )
    return {
        "games": rows,
        "distribution": pl.DataFrame(stats, infer_schema_length=None),
        "weekly": weekly,
        "outcome_bands": bands,
        "team_games": team_rows,
        "teams": team_summary,
        "largest_errors": top,
    }


def write_diagnostic_charts(
    tables: dict[str, pl.DataFrame], destination: Path, season: int
) -> None:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    current = tables["games"].filter(pl.col("model") == "ridge_epa_scores")
    figures: list[tuple[str, Any]] = []
    titles = ["Early week", "Pre-kickoff refresh"]
    for quantity in ("margin", "score"):
        figure = make_subplots(rows=1, cols=2, subplot_titles=titles)
        for column, horizon in enumerate(HORIZONS, 1):
            group = current.filter(pl.col("horizon") == horizon)
            for side in ("home", "away") if quantity == "score" else ("home",):
                x = group[
                    f"predicted_{side}_score" if quantity == "score" else "predicted_home_margin"
                ].to_list()
                y = group[
                    f"{side}_score" if quantity == "score" else "target_home_margin"
                ].to_list()
                labels = [
                    f"Week {r['week']}: {r['away_team']} at {r['home_team']} ({side})"
                    for r in group.to_dicts()
                ]
                figure.add_trace(
                    go.Scatter(
                        x=x,
                        y=y,
                        mode="markers",
                        name=side.title(),
                        legendgroup=side,
                        showlegend=column == 1 and quantity == "score",
                        text=labels,
                        marker={
                            "opacity": 0.65,
                            "size": 6,
                            "color": "#216fd2" if side == "home" else "#de8b16",
                        },
                        hovertemplate="%{text}<br>Predicted: %{x:.1f}<br>Actual: %{y:.0f}"
                        "<extra></extra>",
                    ),
                    row=1,
                    col=column,
                )
            all_values = (
                (
                    group["predicted_home_score"].to_list()
                    + group["predicted_away_score"].to_list()
                    + group["home_score"].to_list()
                    + group["away_score"].to_list()
                )
                if quantity == "score"
                else group["predicted_home_margin"].to_list()
                + group["target_home_margin"].to_list()
            )
            bounds = [min(all_values) - 3, max(all_values) + 3]
            figure.add_trace(
                go.Scatter(
                    x=bounds,
                    y=bounds,
                    mode="lines",
                    line={"color": "#626b76", "dash": "dash"},
                    showlegend=False,
                    hoverinfo="skip",
                ),
                row=1,
                col=column,
            )
            figure.update_xaxes(
                title_text=f"Predicted {quantity} (points)", range=bounds, row=1, col=column
            )
            figure.update_yaxes(
                title_text=f"Actual {quantity} (points)", range=bounds, row=1, col=column
            )
        figure.update_layout(height=490, template="plotly_white", margin={"t": 45, "b": 60})
        figures.append(
            (f"Predicted versus actual {quantity} · dashed line is perfect agreement", figure)
        )
    weekly = make_subplots(rows=1, cols=2, subplot_titles=titles)
    for column, horizon in enumerate(HORIZONS, 1):
        for model, label in LABELS.items():
            group = tables["weekly"].filter(
                (pl.col("horizon") == horizon) & (pl.col("model") == model)
            )
            weekly.add_trace(
                go.Scatter(
                    x=group["week"].to_list(),
                    y=group["margin_mae"].to_list(),
                    mode="lines+markers",
                    name=label,
                    legendgroup=model,
                    showlegend=column == 1,
                    line={
                        "color": {
                            "league_scores": "#8b929a",
                            "prior_team_scores": "#de8b16",
                            "ridge_epa_scores": "#216fd2",
                        }[model]
                    },
                ),
                row=1,
                col=column,
            )
        weekly.update_xaxes(title_text="NFL week", dtick=1, row=1, col=column)
        weekly.update_yaxes(title_text="Margin MAE (points)", rangemode="tozero", row=1, col=column)
    weekly.update_layout(height=450, template="plotly_white", margin={"t": 45, "b": 60})
    figures.append(("Weekly margin error against the scoring baselines", weekly))
    heat = make_subplots(rows=1, cols=2, subplot_titles=titles)
    teams = sorted(tables["team_games"]["team"].unique().to_list())
    weeks = sorted(tables["team_games"]["week"].unique().to_list())
    maximum = float(current["absolute_margin_error"].to_numpy().max())
    for column, horizon in enumerate(HORIZONS, 1):
        group = tables["team_games"].filter(pl.col("horizon") == horizon)
        lookup = {(r["team"], r["week"]): r["absolute_margin_error"] for r in group.to_dicts()}
        heat.add_trace(
            go.Heatmap(
                x=weeks,
                y=teams,
                z=[[lookup.get((team, week)) for week in weeks] for team in teams],
                zmin=0,
                zmax=maximum,
                coloraxis="coloraxis",
                hovertemplate="%{y}, week %{x}<br>Absolute margin error: %{z:.1f}<extra></extra>",
            ),
            row=1,
            col=column,
        )
        heat.update_xaxes(title_text="NFL week", dtick=1, row=1, col=column)
        heat.update_yaxes(title_text="Team", row=1, col=column)
    heat.update_layout(
        height=max(520, len(teams) * 20 + 100),
        template="plotly_white",
        coloraxis={"colorscale": "YlOrRd", "colorbar": {"title": "Error<br>points"}},
        margin={"t": 45, "b": 60},
    )
    figures.append(("Team/week margin error · blank cells are byes", heat))
    team_figure = make_subplots(rows=1, cols=2, subplot_titles=titles)
    for team_index, team in enumerate(teams):
        for column, horizon in enumerate(HORIZONS, 1):
            group = tables["team_games"].filter(
                (pl.col("horizon") == horizon) & (pl.col("team") == team)
            )
            for field, label, dash in (
                ("actual_margin", "Actual margin", "solid"),
                ("predicted_margin", "Predicted margin", "dash"),
            ):
                team_figure.add_trace(
                    go.Scatter(
                        x=group["week"].to_list(),
                        y=group[field].to_list(),
                        mode="lines+markers",
                        name=label,
                        legendgroup=field,
                        showlegend=column == 1,
                        visible=team_index == 0,
                        text=group["opponent"].to_list(),
                        line={
                            "dash": dash,
                            "color": "#216fd2" if field == "actual_margin" else "#de8b16",
                        },
                        hovertemplate="Week %{x} vs %{text}<br>Margin %{y:.1f}"
                        "<extra>%{fullData.name}</extra>",
                    ),
                    row=1,
                    col=column,
                )
    buttons = [
        {
            "label": team,
            "method": "update",
            "args": [{"visible": [index // 4 == number for index in range(len(teams) * 4)]}],
        }
        for number, team in enumerate(teams)
    ]
    team_figure.update_layout(
        height=470,
        template="plotly_white",
        updatemenus=[{"buttons": buttons, "x": 0, "y": 1.2}],
        margin={"t": 100, "b": 60},
    )
    team_figure.update_xaxes(title_text="NFL week", dtick=1)
    team_figure.update_yaxes(title_text="Team margin (points)", zeroline=True)
    figures.append(("Select a team: predicted and actual margins each week", team_figure))
    sections = []
    for number, (title, figure) in enumerate(figures):
        sections.append(
            f"<section><h2>{escape(title)}</h2>"
            + figure.to_html(
                full_html=False,
                include_plotlyjs=True if number == 0 else False,
                div_id=f"chart-{number}",
                config={"responsive": True, "displaylogo": False},
            )
            + "</section>"
        )
    content = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{season} EPA season diagnostics</title><style>"
        "body{font:16px system-ui;margin:24px auto;padding:0 18px;max-width:1200px;color:#17202a}"
        "h2{font-size:19px;margin-top:35px}section{margin-bottom:32px}"
        "p{max-width:900px;line-height:1.5}</style></head><body>"
        f"<h1>{season}: predicted scores and margins versus final results</h1>"
        "<p>Current EPA model, with the same historical forecast rules and separate early-week "
        "and pre-kickoff cutoffs. Every fit uses outcomes available strictly before that game's "
        "forecast. This is a retrospective diagnostic, not an untouched holdout. "
        "No recalibration or model change is applied. Hover for game details; "
        "use the team menu below.</p>" + "".join(sections) + "</body></html>"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")


def save_season_diagnostics(
    data_dir: Path, report_dir: Path, *, season: int = 2025
) -> dict[str, Any]:
    teams, games, sources = load_team_cache(data_dir)
    selected = games.filter((pl.col("season") == season) & pl.col("home_score").is_not_null())
    if selected.is_empty():
        raise DataQualityError("No completed games in diagnostic season")
    directory = data_dir / "processed" / "team_diagnostics" / str(season)
    write_json(
        directory / "protocol.json",
        {
            "locked_at_utc": datetime.now(UTC).isoformat(),
            "season": season,
            "diagnostic_policy": DIAGNOSTIC_POLICY,
            "model_policy": MODEL_POLICY,
            "diagnostic_source_sha256": diagnostic_source_hash(),
        },
    )
    predictions, metrics, folds = evaluate_team_scores(
        teams.filter(pl.col("season") <= season),
        games.filter(pl.col("season") <= season),
        evaluation_seasons=(season,),
    )
    if predictions.group_by("model", "horizon").len()["len"].unique().to_list() != [
        selected.height
    ]:
        raise DataQualityError("Diagnostic cohort differs from completed schedule")
    tables = diagnostic_tables(predictions)
    report: dict[str, Any] = {
        "format_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "season": season,
        "diagnostic_source_sha256": diagnostic_source_hash(),
        "runtime": runtime_versions(),
        "policy": DIAGNOSTIC_POLICY,
        "model_policy": MODEL_POLICY,
        "sources": sources,
        "metrics": metrics.filter(pl.col("scope") == "overall").to_dicts(),
        "folds": folds,
        "distribution": tables["distribution"].to_dicts(),
        "outcome_bands": tables["outcome_bands"].to_dicts(),
        "weekly": tables["weekly"].to_dicts(),
        "teams": tables["teams"].to_dicts(),
        "artifacts": {name: store_frame(directory, name, table) for name, table in tables.items()},
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    write_diagnostic_charts(tables, report_dir / "diagnostics.html", season)
    write_json(directory / "manifest.json", report)
    write_json(report_dir / "diagnostics.json", report)
    tables["games"].write_csv(report_dir / "game_predictions.csv")
    current = tables["distribution"].filter(pl.col("model") == "ridge_epa_scores")
    columns = [
        "horizon",
        "games",
        "margin_mae",
        "margin_rmse",
        "margin_bias",
        "median_absolute_error",
        "p90_absolute_error",
        "predicted_margin_sd",
        "actual_margin_sd",
        "largest_10_errors_share",
    ]
    lines = [
        f"# {season} team EPA diagnostic",
        "",
        "Saved expanding chronological predictions from the unchanged model. "
        "Already accessed diagnostic data; not an untouched holdout. "
        "No model selection or recalibration applied.",
        "",
        "## Metrics",
        "",
        *markdown_table(pl.DataFrame(report["metrics"]).drop("scope", "season")),
        "",
        "## Error distribution and prediction spread",
        "",
        *markdown_table(current.select(columns)),
        "",
        "## Outcome bands (postgame description)",
        "",
        *markdown_table(tables["outcome_bands"]),
        "",
        "## Largest errors (retained in all metrics)",
        "",
        *markdown_table(
            tables["largest_errors"].select(
                "horizon",
                "week",
                "away_team",
                "home_team",
                "predicted_away_score",
                "predicted_home_score",
                "away_score",
                "home_score",
                "predicted_home_margin",
                "target_home_margin",
                "absolute_margin_error",
            )
        ),
        "",
        "Open diagnostics.html for predicted/actual score and margin plots, weekly baseline "
        "comparisons, team/week heatmaps and selectable team trajectories. "
        "game_predictions.csv retains every prediction.",
        "",
        "Source revisions and exact historical publication are not reconstructed. "
        "Results require kickoff plus 24 hours before entering training or features. "
        "Outcome-selected subgroups describe errors and cannot "
        "be treated as pregame filters or evidence of a causal explanation.",
    ]
    (report_dir / "diagnostics.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def load_season_diagnostics(
    data_dir: Path, *, season: int = 2025
) -> tuple[dict[str, Any], dict[str, pl.DataFrame]]:
    directory = data_dir / "processed" / "team_diagnostics" / str(season)
    report = read_json(directory / "manifest.json")
    if report["diagnostic_source_sha256"] != diagnostic_source_hash():
        raise DataQualityError("Diagnostic code changed; run team-diagnose again")
    return report, {name: load_frame(directory, item) for name, item in report["artifacts"].items()}
