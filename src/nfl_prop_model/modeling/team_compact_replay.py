"""Separately authorized, frozen compact-EPA 2025 diagnostic; no enriched gate bypass."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nfl_prop_model.data.schemas import DataQualityError, require_keys
from nfl_prop_model.data.storage import load_frame, read_json, sha256_file, store_frame, write_json
from nfl_prop_model.data.team_games import load_team_cache
from nfl_prop_model.features.team_epa import HORIZONS
from nfl_prop_model.features.team_walkforward import COMPACT_EPA, weekly_features
from nfl_prop_model.modeling import team_archived_weather as weather
from nfl_prop_model.modeling import team_dated_injuries as injuries
from nfl_prop_model.modeling import team_simplicity as simplicity
from nfl_prop_model.modeling.reporting import markdown_table
from nfl_prop_model.modeling.team_scores import (
    forecast_rows,
    runtime_versions,
    score_metrics,
    score_pipeline,
)
from nfl_prop_model.modeling.team_walkforward import load_walkforward_development

MODELS = ("compact_epa", "league_scores", "prior_team_scores")
POLICY: dict[str, Any] = {
    "scope": "user-authorized compact-only 2025 retrospective diagnostic",
    "selection": "verified 2022-2024 development choices only; no 2025 selection or tuning",
    "features": list(COMPACT_EPA),
    "estimator": "two-output Ridge alpha 20; train-only median imputation, indicators, scaling",
    "cohort": "all 272 completed regular-season 2025 games, weeks 1-18",
    "training": "2022-2024 initially; add available earlier-week 2025 games each Tuesday",
    "lock": "Tuesday 10:00 America/Chicago; strict result availability before lock",
    "availability": "existing kickoff +24h assumption; Monday results can miss Tuesday",
    "refresh": "kickoff -1h; reuse Tuesday fit and inputs; identical scores, not independent data",
    "fits": 18,
    "controls": list(MODELS[1:]),
    "primary": "early-week game margin MAE; no post-result promotion",
    "secondary": "team score/total MAE, RMSE, bias; weekly errors; descriptive control gains",
    "bootstrap": simplicity.POLICY["bootstrap"],
    "outputs": "nonnegative scores rounded to tenths; signed margins and total derived from scores",
    "enriched_replay_ready": False,
    "limits": "2025 previously accessed; source revisions/publication not reconstructed",
    "storage": "reuse sources offline; retain only 2025 features, predictions and receipts",
}
FACTS = (
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
    "home_prior_games",
    "away_prior_games",
)
FEATURE_COLUMNS = tuple(
    dict.fromkeys(
        (
            *FACTS,
            *COMPACT_EPA,
            "home_history_available_at_utc",
            "away_history_available_at_utc",
            "home_points_scored_prior",
            "away_points_scored_prior",
            "home_points_allowed_prior",
            "away_points_allowed_prior",
        )
    )
)
SCORES = (
    "predicted_home_score",
    "predicted_away_score",
    "predicted_home_margin",
    "predicted_away_margin",
    "predicted_total",
)


def source_hash() -> str:
    return hashlib.sha256(
        Path(__file__).read_bytes()
        + weather.source_hash().encode()
        + injuries.dated_source_hash().encode()
        + simplicity.simplicity_source_hash().encode()
    ).hexdigest()


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def selection_inputs(data_dir: Path) -> dict[str, Any]:
    """Verify development-only choices before opening any canonical outcome artifact."""
    specs = (
        (
            "team_simplicity",
            simplicity.simplicity_source_hash(),
            simplicity.load_simplicity_report,
            "frozen_2025_research_choice",
        ),
        (
            "team_dated_injuries",
            injuries.dated_source_hash(),
            lambda p: injuries.load_dated_report(p, data_dir),
            "frozen_research_choice",
        ),
        (
            "team_archived_weather",
            weather.source_hash(),
            lambda p: weather.load_weather_comparison(p, data_dir),
            "frozen_research_choice",
        ),
    )
    refs = {}
    for name, digest, loader, key in specs:
        paths = list((data_dir / "processed" / name).glob(f"{digest[:12]}-*/manifest.json"))
        if len(paths) != 1:
            raise DataQualityError(f"Require one current verified development report: {name}")
        path = paths[0]
        report = loader(path)
        choice = report[key]
        if choice["model"] != "compact_epa" or report["2025_fits_performed"] != 0:
            raise DataQualityError("Development did not freeze compact EPA without 2025 fits")
        refs[name] = {
            "manifest": path.relative_to(data_dir).as_posix(),
            "sha256": sha256_file(path),
            "choice": choice,
        }
    # Manifest metadata only: outcome parquet files are opened after freeze creation.
    for name in ("team_epa", "team_walkforward"):
        path = data_dir / "processed" / name / "manifest.json"
        refs[name] = {
            "manifest": path.relative_to(data_dir).as_posix(),
            "sha256": sha256_file(path),
        }
    return refs


def freeze(directory: Path, inputs: dict[str, Any]) -> dict[str, Any]:
    expected = {
        "format_version": 1,
        "source_sha256": source_hash(),
        "policy": POLICY,
        "inputs": inputs,
        "model": "compact_epa",
        "simulated_first_lock_utc": "2025-09-02T15:00:00+00:00",
    }
    path = directory / "frozen_2025.json"
    if path.exists():
        packet = read_json(path)
        if {k: v for k, v in packet.items() if k != "recorded_at_utc"} != expected:
            raise DataQualityError("Compact replay freeze changed; never overwrite a frozen run")
    else:
        packet = {**expected, "recorded_at_utc": datetime.now(UTC).isoformat()}
        write_json(path, packet)
    return packet


def validate_features(features: pl.DataFrame) -> None:
    if missing := set(FEATURE_COLUMNS) - set(features.columns):
        raise DataQualityError(f"Compact features missing columns: {sorted(missing)}")
    require_keys(features, ["game_id", "horizon"], "compact replay features")
    if not set(features["season"]) <= {2022, 2023, 2024, 2025}:
        raise DataQualityError("Compact replay history must be 2022-2025 only")
    if set(features["horizon"]) != set(HORIZONS):
        raise DataQualityError("Compact replay requires both horizons")
    numeric = (*COMPACT_EPA, "home_score", "away_score", "target_home_margin")
    times = ("prediction_time_utc", "week_lock_utc", "kickoff_utc", "result_available_at_utc")
    if features.filter(
        pl.any_horizontal(pl.col(times).is_null())
        | pl.any_horizontal(~pl.col(numeric).is_finite()).fill_null(False)
        | pl.any_horizontal(pl.col("home_score", "away_score", "target_home_margin").is_null())
        | (pl.col("week_lock_utc") > pl.col("prediction_time_utc"))
        | (pl.col("prediction_time_utc") >= pl.col("kickoff_utc"))
        | (pl.col("result_available_at_utc") != pl.col("kickoff_utc") + pl.duration(hours=24))
        | (
            (pl.col("home_score") - pl.col("away_score") - pl.col("target_home_margin")).abs()
            > 1e-8
        )
        | pl.any_horizontal(
            [
                pl.col(f"{side}_history_available_at_utc") >= pl.col("week_lock_utc")
                for side in ("home", "away")
            ]
        ).fill_null(False)
    ).height:
        raise DataQualityError("Compact features have invalid scores, availability or timing")
    for horizon in HORIZONS:
        rows = features.filter(pl.col("horizon") == horizon)
        expected = (
            pl.col("week_lock_utc")
            if horizon == "early_week"
            else (pl.col("kickoff_utc") - pl.duration(hours=1))
        )
        if rows.filter(pl.col("prediction_time_utc") != expected).height:
            raise DataQualityError("Compact horizon cutoff mismatch")
    if (
        features.group_by("season", "week")
        .agg(pl.col("week_lock_utc").n_unique())
        .filter(pl.col("week_lock_utc") != 1)
        .height
    ):
        raise DataQualityError("Compact forecast week has conflicting locks")
    columns = [c for c in FEATURE_COLUMNS if c not in {"horizon", "prediction_time_utc"}]
    if (
        not features.filter(pl.col("horizon") == HORIZONS[0])
        .select(columns)
        .sort("game_id")
        .equals(features.filter(pl.col("horizon") == HORIZONS[1]).select(columns).sort("game_id"))
    ):
        raise DataQualityError("Refresh must reuse all Tuesday inputs and facts")


def _features(data_dir: Path) -> pl.DataFrame:
    teams, games, _ = load_team_cache(data_dir)
    games = games.filter(pl.col("season").is_between(2022, 2025))
    selected = games.filter(pl.col("season") == 2025)
    if (
        selected.height != 272
        or set(selected["week"]) != set(range(1, 19))
        or set(selected["game_type"]) != {"REG"}
        or selected.select(
            pl.any_horizontal(pl.col("home_score", "away_score").is_null()).any()
        ).item()
    ):
        raise DataQualityError("Require every one of the 272 completed 2025 regular-season games")
    teams = teams.filter(pl.col("season").is_between(2022, 2025))
    require_keys(teams, ["game_id", "team"], "compact team history")
    team_pairs = teams.filter(pl.col("season") == 2025).select("game_id", "team")
    expected_pairs = pl.concat(
        [
            selected.select("game_id", pl.col(f"{side}_team").alias("team"))
            for side in ("home", "away")
        ]
    )
    if not team_pairs.sort("game_id", "team").equals(expected_pairs.sort("game_id", "team")):
        raise DataQualityError("Require both teams' EPA histories for all 272 games")
    features = weekly_features(teams, games).select(FEATURE_COLUMNS)
    validate_features(features)
    upstream = load_walkforward_development(data_dir)
    base = data_dir / "processed" / "team_walkforward"
    original = load_frame(base, upstream["artifacts"]["features"]).select(FEATURE_COLUMNS)
    development = features.filter(pl.col("season") < 2025)
    if not original.sort("game_id", "horizon").equals(development.sort("game_id", "horizon")):
        raise DataQualityError("Reconstructed compact history differs from preserved development")
    return features


def training_fold(history: pl.DataFrame, test: pl.DataFrame) -> tuple[pl.DataFrame, dict[str, Any]]:
    first = test.row(0, named=True)
    lock, week = first["week_lock_utc"], first["week"]
    eligible = history.filter((pl.col("season") < 2025) | (pl.col("week") < week))
    train = eligible.filter(
        (pl.col("result_available_at_utc") < lock) & (pl.col("prediction_time_utc") < lock)
    ).sort("game_id")
    if train.height < 64 or set(train["game_id"]) & set(test["game_id"]):
        raise DataQualityError("Compact fit requires 64 available, nonoverlapping earlier games")
    latest = train["result_available_at_utc"].max()
    if not isinstance(latest, datetime):
        raise DataQualityError("Missing compact training availability")
    receipt = {
        "season": 2025,
        "week": week,
        "lock_utc": lock.isoformat(),
        "training_games": train.height,
        "training_seasons": sorted(set(train["season"])),
        "prior_2025_games": train.filter(pl.col("season") == 2025).height,
        "latest_training_result_available_utc": latest.isoformat(),
        "training_game_ids_sha256": hashlib.sha256(
            "\n".join(train["game_id"]).encode()
        ).hexdigest(),
        "test_game_ids": sorted(test["game_id"].to_list()),
        "unavailable_earlier_games": sorted(
            eligible.filter(pl.col("result_available_at_utc") >= lock)["game_id"].to_list()
        ),
    }
    return train, receipt


def baseline_values(train: pl.DataFrame, test: pl.DataFrame) -> dict[str, Any]:
    mean = train.select("home_score", "away_score").to_numpy().mean(axis=0)
    league = float(mean.mean())
    prior = np.array(
        [
            [
                sum(row[k] if row[k] is not None else league for k in names) / 2
                for names in (
                    ("home_points_scored_prior", "away_points_allowed_prior"),
                    ("away_points_scored_prior", "home_points_allowed_prior"),
                )
            ]
            for row in test.to_dicts()
        ]
    )
    venue = test["home_field"].to_numpy() * float(mean[0] - mean[1]) / 2
    prior[:, 0] += venue
    prior[:, 1] -= venue
    return {"league_scores": np.tile(mean, (test.height, 1)), "prior_team_scores": prior}


def evaluate_compact(features: pl.DataFrame) -> tuple[pl.DataFrame, list[dict[str, Any]]]:
    validate_features(features)
    history = features.filter(pl.col("horizon") == "early_week")
    evaluation = history.filter(pl.col("season") == 2025).sort("week", "game_id")
    if set(evaluation["week"]) != set(range(1, 19)):
        raise DataQualityError("Compact replay requires all 18 forecast weeks")
    records, folds = [], []
    for test in evaluation.partition_by("week", maintain_order=True):
        train, receipt = training_fold(history, test)
        model = score_pipeline()
        model.fit(
            train.select(COMPACT_EPA).to_numpy(),
            train.select("home_score", "away_score").to_numpy(),
        )
        values = {
            "compact_epa": np.asarray(model.predict(test.select(COMPACT_EPA).to_numpy())),
            **baseline_values(train, test),
        }
        for horizon in HORIZONS:
            current = features.filter(
                (pl.col("horizon") == horizon)
                & (pl.col("season") == 2025)
                & (pl.col("week") == receipt["week"])
            ).sort("game_id")
            records.extend(forecast_rows(current, values, train))
            folds.append({**receipt, "horizon": horizon, "fit_reused": horizon != "early_week"})
        print(f"Replayed 2025 week {receipt['week']}: {train.height} training games", flush=True)
    panel = pl.DataFrame(records, infer_schema_length=None).join(
        evaluation.select("game_id", "week_lock_utc", "result_available_at_utc"),
        on="game_id",
        validate="m:1",
    )
    validate_panel(panel, features, folds)
    return panel, folds


def validate_panel(
    panel: pl.DataFrame, features: pl.DataFrame, folds: list[dict[str, Any]]
) -> None:
    require_keys(panel, ["game_id", "horizon", "model"], "compact predictions")
    if set(panel["model"]) != set(MODELS) or set(panel["horizon"]) != set(HORIZONS):
        raise DataQualityError("Compact prediction models or horizons changed")
    expected = features.filter(pl.col("season") == 2025)
    for group in panel.partition_by("model"):
        if (
            not group.select(FACTS)
            .sort("game_id", "horizon")
            .equals(expected.select(FACTS).sort("game_id", "horizon"))
        ):
            raise DataQualityError("Compact prediction cohort or facts changed")
    if panel.filter(
        pl.any_horizontal(pl.col(SCORES).is_null() | ~pl.col(SCORES).is_finite())
        | pl.any_horizontal(pl.col("predicted_home_score", "predicted_away_score") < 0)
        | (
            (
                pl.col("predicted_home_score")
                - pl.col("predicted_away_score")
                - pl.col("predicted_home_margin")
            ).abs()
            > 1e-8
        )
        | ((pl.col("predicted_home_margin") + pl.col("predicted_away_margin")).abs() > 1e-8)
        | (
            (
                pl.col("predicted_home_score")
                + pl.col("predicted_away_score")
                - pl.col("predicted_total")
            ).abs()
            > 1e-8
        )
        | pl.any_horizontal((pl.col(SCORES) * 10 - (pl.col(SCORES) * 10).round()).abs() > 1e-8)
    ).height:
        raise DataQualityError("Compact score/margin/total contract changed")
    columns = [
        "game_id",
        "model",
        *SCORES,
        "training_games",
        "latest_training_result_available_utc",
    ]
    if (
        not panel.filter(pl.col("horizon") == HORIZONS[0])
        .select(columns)
        .sort("game_id", "model")
        .equals(
            panel.filter(pl.col("horizon") == HORIZONS[1]).select(columns).sort("game_id", "model")
        )
    ):
        raise DataQualityError("Compact refresh predictions must equal Tuesday predictions")
    history = features.filter(pl.col("horizon") == "early_week")
    receipts = []
    for test in (
        expected.filter(pl.col("horizon") == "early_week")
        .sort("week", "game_id")
        .partition_by("week")
    ):
        train, receipt = training_fold(history, test)
        for horizon in HORIZONS:
            receipts.append({**receipt, "horizon": horizon, "fit_reused": horizon != "early_week"})
        current = panel.filter(pl.col("week") == receipt["week"])
        if current.filter(
            (pl.col("training_games") != train.height)
            | (
                pl.col("latest_training_result_available_utc")
                != train["result_available_at_utc"].max()
            )
        ).height:
            raise DataQualityError("Compact training receipt mismatch")
        controls = pl.DataFrame(forecast_rows(test, baseline_values(train, test), train))
        actual = current.filter(
            (pl.col("horizon") == "early_week") & (pl.col("model") != "compact_epa")
        )
        if (
            not controls.select(columns)
            .sort("game_id", "model")
            .equals(actual.select(columns).sort("game_id", "model"))
        ):
            raise DataQualityError("Compact descriptive control changed")
    if receipts != folds:
        raise DataQualityError("Compact fold cohort or cutoff receipts changed")


def summaries(panel: pl.DataFrame) -> dict[str, Any]:
    weekly = []
    for rows in panel.partition_by("week"):
        for metric in score_metrics(rows).filter(pl.col("scope") == "overall").to_dicts():
            weekly.append({"week": rows["week"][0], **metric})
    early = panel.filter(pl.col("horizon") == "early_week")
    return {
        "metrics": score_metrics(panel).to_dicts(),
        "weekly_metrics": weekly,
        "comparisons": [
            {"control": control, **simplicity.paired_gain(early, "compact_epa", control)}
            for control in MODELS[1:]
        ],
    }


def load_compact_replay(path: Path, data_dir: Path) -> dict[str, Any]:
    report = read_json(path)
    inputs = selection_inputs(data_dir)
    packet = read_json(path.parent / "frozen_2025.json")
    if (
        report["format_version"] != 1
        or report["source_sha256"] != source_hash()
        or report["policy"] != POLICY
        or report["inputs"] != inputs
        or packet != report["freeze"]
        or _digest(packet) != report["freeze_sha256"]
        or packet["source_sha256"] != source_hash()
        or packet["inputs"] != inputs
        or packet["policy"] != POLICY
        or packet["model"] != "compact_epa"
        or report["2025_fits_performed"] != 18
        or report["enriched_replay_ready"] is not False
    ):
        raise DataQualityError("Compact replay source, freeze, policy or scope changed")
    features = _features(data_dir)
    saved = load_frame(path.parent, report["artifacts"]["features"])
    if not saved.equals(features.filter(pl.col("season") == 2025)):
        raise DataQualityError("Compact replay features differ from canonical cutoff history")
    panel = load_frame(path.parent, report["artifacts"]["predictions"])
    validate_panel(panel, features, report["folds"])
    if any(report[key] != value for key, value in summaries(panel).items()):
        raise DataQualityError("Compact replay metrics changed")
    return report


def write_charts(panel: pl.DataFrame, path: Path) -> None:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    rows = panel.filter(
        (pl.col("horizon") == "early_week") & (pl.col("model") == "compact_epa")
    ).sort("week", "game_id")
    labels = [
        f"Week {r['week']}: {r['away_team']} at {r['home_team']}<br>"
        f"Predicted {r['predicted_away_score']:.1f}–{r['predicted_home_score']:.1f}; "
        f"actual {r['away_score']:.0f}–{r['home_score']:.0f}"
        for r in rows.to_dicts()
    ]
    fig = make_subplots(
        rows=2,
        cols=2,
        subplot_titles=(
            "Game margin: actual vs predicted (home perspective)",
            "Team score: actual vs predicted",
            "Weekly game margin MAE",
            "Margin residual: predicted minus actual",
        ),
    )
    fig.add_trace(
        go.Scatter(
            x=rows["target_home_margin"].to_list(),
            y=rows["predicted_home_margin"].to_list(),
            mode="markers",
            text=labels,
            name="Home margin",
            hovertemplate="%{text}<br>Actual margin %{x}; predicted %{y}<extra></extra>",
        ),
        row=1,
        col=1,
    )
    for side in ("home", "away"):
        fig.add_trace(
            go.Scatter(
                x=rows[f"{side}_score"].to_list(),
                y=rows[f"predicted_{side}_score"].to_list(),
                mode="markers",
                text=labels,
                name=f"{side.title()} score",
                hovertemplate="%{text}<br>Actual score %{x}; predicted %{y}<extra></extra>",
            ),
            row=1,
            col=2,
        )
    for col, limits in ((1, [-60, 60]), (2, [0, 65])):
        fig.add_trace(
            go.Scatter(
                x=limits,
                y=limits,
                mode="lines",
                line={"dash": "dash", "color": "gray"},
                showlegend=False,
                hoverinfo="skip",
            ),
            row=1,
            col=col,
        )
        fig.update_xaxes(title_text="Actual points", row=1, col=col)
        fig.update_yaxes(title_text="Predicted points", row=1, col=col)
    weekly = pl.DataFrame(summaries(panel)["weekly_metrics"])
    for name in MODELS:
        group = weekly.filter((pl.col("horizon") == "early_week") & (pl.col("model") == name)).sort(
            "week"
        )
        fig.add_trace(
            go.Scatter(
                x=group["week"].to_list(),
                y=group["margin_mae"].to_list(),
                mode="lines+markers",
                name=name,
            ),
            row=2,
            col=1,
        )
    fig.add_trace(
        go.Scatter(
            x=rows["week"].to_list(),
            y=(rows["predicted_home_margin"] - rows["target_home_margin"]).to_list(),
            mode="markers",
            text=labels,
            name="Margin error",
            hovertemplate="%{text}<br>Error %{y:.1f}<extra></extra>",
        ),
        row=2,
        col=2,
    )
    fig.add_hline(y=0, line_dash="dash", row=2, col=2)
    for col in (1, 2):
        fig.update_xaxes(title_text="2025 week", dtick=1, row=2, col=col)
        fig.update_yaxes(title_text="Points", row=2, col=col)
    fig.update_layout(
        height=950,
        title="Frozen compact EPA · 2025 diagnostic · all 272 games · Tuesday forecasts",
        template="plotly_white",
    )
    html = fig.to_html(full_html=False, include_plotlyjs="cdn")
    path.write_text(
        '<!doctype html><html><head><meta charset="utf-8">'
        "<title>2025 compact EPA replay</title></head>"
        "<body><p>Retrospective diagnostic; 2025 previously examined. "
        "Refresh duplicates Tuesday scores. "
        "Hover for game-level predictions and actuals. Plotly loads from its CDN.</p>"
        + html
        + "</body></html>",
        encoding="utf-8",
    )


def export_report(report: dict[str, Any], directory: Path, report_dir: Path) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    panel = load_frame(directory, report["artifacts"]["predictions"])
    panel.sort("week", "game_id", "horizon", "model").write_csv(report_dir / "predictions.csv")
    pl.DataFrame(report["metrics"]).write_csv(report_dir / "metrics.csv")
    pl.DataFrame(report["weekly_metrics"]).write_csv(report_dir / "weekly_metrics.csv")
    write_json(report_dir / "results.json", report)
    write_charts(panel, report_dir / "comparison.html")
    early = pl.DataFrame(report["metrics"]).filter(
        (pl.col("scope") == "overall") & (pl.col("horizon") == "early_week")
    )
    lines = [
        "# Frozen compact-EPA 2025 weekly diagnostic",
        "",
        "All 272 regular-season games; 18 Tuesday fits on expanding 2022–2025 history. "
        "Eight inputs and alpha 20 were fixed from development. No 2025 tuning or model selection.",
        "",
        "2025 was previously examined in this project; this is not an untouched holdout. "
        "The enriched replay stays gated and the published app model is unchanged.",
        "",
        "Early-week and refresh scores are identical because all compact inputs and coefficients "
        "stay Tuesday-locked. Metrics count each game once; score error counts 544 team scores.",
        "",
    ]
    lines += markdown_table(
        early.select(
            "model", "games", "margin_mae", "score_mae", "total_mae", "margin_rmse", "margin_bias"
        )
    )
    lines += [
        "",
        "Positive paired gain means compact EPA has lower margin MAE than the control. "
        "Week-block bootstrap ranges describe 18 weekly blocks; they are not promotion rules.",
        "",
    ]
    lines += markdown_table(pl.DataFrame(report["comparisons"]))
    lines += [
        "",
        "The kickoff-plus-24-hour assumption can exclude Monday games at Tuesday's lock. "
        "Fold receipts list exclusions, training counts and hashes. Historical source revisions "
        "and publication times are not reconstructed.",
        "",
        "Interactive [predicted-versus-actual charts](comparison.html), all "
        "[game predictions](predictions.csv), and [weekly metrics](weekly_metrics.csv) are saved. "
        "Charts load Plotly from its CDN; local parquet artifacts contain only 2025 rows.",
    ]
    (report_dir / "findings.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def save_compact_replay(data_dir: Path, report_dir: Path) -> dict[str, Any]:
    inputs = selection_inputs(data_dir)
    digest = source_hash()
    directory = (
        data_dir / "processed" / "team_compact_replay" / f"{digest[:12]}-{_digest(inputs)[:12]}"
    )
    path = directory / "manifest.json"
    if path.exists():
        report = load_compact_replay(path, data_dir)
    else:
        packet = freeze(directory, inputs)  # Must precede outcome artifact reads and fits.
        features = _features(data_dir)
        panel, folds = evaluate_compact(features)
        if source_hash() != digest or selection_inputs(data_dir) != inputs:
            raise DataQualityError("Compact replay sources changed during run")
        report = {
            "format_version": 1,
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "source_sha256": digest,
            "policy": POLICY,
            "inputs": inputs,
            "freeze": packet,
            "freeze_sha256": _digest(packet),
            "runtime": runtime_versions(),
            "2025_fits_performed": 18,
            "enriched_replay_ready": False,
            "folds": folds,
            **summaries(panel),
            "artifacts": {
                "features": store_frame(
                    directory, "features", features.filter(pl.col("season") == 2025)
                ),
                "predictions": store_frame(directory, "predictions", panel),
            },
        }
        write_json(path, report)
        load_compact_replay(path, data_dir)
    export_report(report, directory, report_dir)
    return report
