"""Storage-conscious development comparison and a gated, frozen weekly replay."""

import hashlib
import io
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nfl_prop_model.data.schemas import DataQualityError, require_keys
from nfl_prop_model.data.storage import load_frame, read_json, store_frame, write_json
from nfl_prop_model.data.team_context import load_context_cache
from nfl_prop_model.data.team_games import load_team_cache
from nfl_prop_model.data.team_pregame import WEATHER_POLICY, fetch_pregame_audit
from nfl_prop_model.features.team_epa import HORIZONS
from nfl_prop_model.features.team_walkforward import (
    MODELS,
    add_pregame_features,
    context_coverage,
    validate_weekly_features,
    weekly_features,
)
from nfl_prop_model.modeling.reporting import markdown_table
from nfl_prop_model.modeling.team_scores import (
    forecast_rows,
    model_source_hash,
    predict_models,
    runtime_versions,
    score_metrics,
    score_pipeline,
)

POLICY: dict[str, Any] = {
    "format_version": 1,
    "initial_history": "2022 only; 2021 and earlier excluded from features and training",
    "development": "2023-2024 chronological predictions; 2022 supplies initial fitting history",
    "replay": "2025 Week 1 trains on 2022-2024; each later week adds available prior weeks",
    "fit": "separate horizon pipelines refit once at Tuesday lock; never refit within a week",
    "history": (
        "EPA, scoring priors and rest frozen at Tuesday; refresh changes pregame context only"
    ),
    "availability": "kickoff +24h strictly before lock; Monday results may miss Tuesday's fit",
    "context": "observed QB/OL/skill/defense Out counts; no row is unknown, not a healthy team",
    "starters": "excluded from candidates: development depth charts have no publication times",
    "weather": WEATHER_POLICY,
    "ridge_alpha": 20.0,
    "models": {name: list(columns) for name, columns in MODELS.items()},
    "preprocessing": "train-only median imputation, indicators, standardization; two score outputs",
    "selection": {
        "horizon": "early_week",
        "minimum_margin_gain": 0.05,
        "maximum_score_regression": 0.05,
        "maximum_total_regression": 0.10,
        "maximum_single_season_margin_regression": 0.25,
        "require_positive_95_percent_week_block_gain_lower": True,
        "tie_break": "lowest margin MAE, then fewer declared features, then fixed model order",
        "fallback": "ridge_epa_scores",
    },
    "bootstrap": {"draws": 5000, "seed": 20261014, "blocks": "season/week"},
    "2025_readiness": "all audited starter, injury and complete-weather timing gaps resolved first",
    "promotion": "research only; published model remains unchanged",
    "interpretation": (
        "already examined retrospective data; exact revisions/publication unreconstructed"
    ),
    "storage": "one features/predictions table, fold IDs/hashes; no copied training tables per fit",
}


def walkforward_source_hash() -> str:
    base = Path(__file__).parents[1]
    return hashlib.sha256(
        model_source_hash().encode()
        + Path(__file__).read_bytes()
        + (base / "data" / "team_pregame.py").read_bytes()
        + (base / "features" / "team_walkforward.py").read_bytes()
    ).hexdigest()


def frame_hash(frame: pl.DataFrame) -> str:
    stream = io.BytesIO()
    frame.write_ipc(stream)
    return hashlib.sha256(stream.getvalue()).hexdigest()


def evaluate_weeks(
    features: pl.DataFrame,
    *,
    seasons: tuple[int, ...] = (2023, 2024),
    models: tuple[str, ...] = tuple(MODELS),
) -> tuple[pl.DataFrame, list[dict[str, Any]], list[dict[str, Any]]]:
    """Pure evaluator: the public save command gates 2025 before reaching this function."""
    validate_weekly_features(features)
    if not models or set(models) - set(MODELS):
        raise ValueError("Unknown weekly comparison model")
    records, folds, complexity = [], [], []
    for horizon in HORIZONS:
        history = features.filter(pl.col("horizon") == horizon)
        selected = history.filter(pl.col("season").is_in(seasons))
        for test in selected.partition_by(["season", "week"], maintain_order=True):
            if test["week_lock_utc"].n_unique() != 1:
                raise DataQualityError("One forecast week must have one Tuesday lock")
            first = test.row(0, named=True)
            lock = first["week_lock_utc"]
            train = history.filter(
                (pl.col("season") >= 2022)
                & (
                    (pl.col("season") < first["season"])
                    | ((pl.col("season") == first["season"]) & (pl.col("week") < first["week"]))
                )
                & (pl.col("result_available_at_utc") < lock)
                & (pl.col("prediction_time_utc") < lock)
                & pl.col("home_score").is_not_null()
                & pl.col("away_score").is_not_null()
            ).sort("game_id")
            if train.height < 64:
                raise DataQualityError("Weekly fit requires at least 64 earlier available games")
            if set(train["game_id"]) & set(test["game_id"]):
                raise DataQualityError("Weekly training and prediction overlap")
            values = predict_models(train, test)
            if "ridge_epa_scores" not in models:
                values.pop("ridge_epa_scores")
            targets = train.select("home_score", "away_score").to_numpy()
            for name in models:
                if name == "ridge_epa_scores":
                    continue
                columns = MODELS[name]
                model = score_pipeline()
                matrix = np.asarray(train.select(columns).to_numpy(), dtype=np.float64)
                model.fit(matrix, targets)
                values[name] = np.asarray(model.predict(test.select(columns).to_numpy()))
                if first["week"] == 1:
                    encoded = model[:-1].transform(matrix)
                    coefficients = model[-1].coef_
                    singular = np.linalg.svd(encoded, compute_uv=False)
                    complexity.append(
                        {
                            "season": first["season"],
                            "horizon": horizon,
                            "model": name,
                            "raw_features": len(columns),
                            "encoded_features": encoded.shape[1],
                            "allocated_weights_and_intercepts": int(coefficients.size + 2),
                            "active_encoded_features": int(
                                (np.abs(coefficients).max(axis=0) > 1e-8).sum()
                            ),
                            "matrix_rank": int(np.linalg.matrix_rank(encoded)),
                            "ridge_degrees_of_freedom_per_output": float(
                                (singular**2 / (singular**2 + 20)).sum() + 1
                            ),
                        }
                    )
            records.extend(forecast_rows(test, values, train))
            latest = train["result_available_at_utc"].max()
            if not isinstance(latest, datetime):
                raise DataQualityError("Missing weekly training availability")
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
                    f"Compared {first['season']} week {first['week']} {horizon}: "
                    f"{train.height} training games",
                    flush=True,
                )
    if not records:
        raise DataQualityError("No weekly evaluation games")
    predictions = pl.DataFrame(records, infer_schema_length=None)
    require_keys(predictions, ["game_id", "horizon", "model"], "weekly predictions")
    expected = set(features.filter(pl.col("season").is_in(seasons))["game_id"])
    for group in predictions.partition_by(["horizon", "model"]):
        if set(group["game_id"]) != expected:
            raise DataQualityError("Weekly comparison cohorts differ")
    return predictions, folds, complexity


def paired_comparisons(
    predictions: pl.DataFrame, *, control_model: str = "ridge_epa_scores"
) -> pl.DataFrame:
    rows = []
    for horizon in HORIZONS:
        current = predictions.filter(pl.col("horizon") == horizon).with_columns(
            (pl.col("predicted_home_margin") - pl.col("target_home_margin")).abs().alias("error")
        )
        control = current.filter(pl.col("model") == control_model).select(
            "game_id", pl.col("error").alias("control_error")
        )
        for name in MODELS:
            if name in {"ridge_epa_scores", control_model}:
                continue
            candidate = current.filter(pl.col("model") == name)
            paired = candidate.join(control, on="game_id", validate="1:1").with_columns(
                (pl.col("control_error") - pl.col("error")).alias("gain")
            )
            if paired.height != control.height or paired.height != candidate.height:
                raise DataQualityError("Paired weekly cohorts differ")
            blocks = (
                paired.group_by("season", "week")
                .agg(pl.col("gain").sum(), pl.len().alias("games"))
                .sort("season", "week")
            )
            rng = np.random.default_rng(POLICY["bootstrap"]["seed"])
            sample = rng.integers(
                0, blocks.height, size=(POLICY["bootstrap"]["draws"], blocks.height)
            )
            gains = blocks["gain"].to_numpy()[sample].sum(axis=1) / blocks["games"].to_numpy()[
                sample
            ].sum(axis=1)
            lower, upper = np.quantile(gains, [0.025, 0.975])
            rows.append(
                {
                    "horizon": horizon,
                    "model": name,
                    "games": paired.height,
                    "margin_mae_gain": float(paired["gain"].to_numpy().mean()),
                    "week_block_95_lower": float(lower),
                    "week_block_95_upper": float(upper),
                }
            )
    return pl.DataFrame(rows)


def freeze_choice(metrics: pl.DataFrame, comparisons: pl.DataFrame) -> dict[str, Any]:
    if set(metrics["season"].drop_nulls()) != {2023, 2024}:
        raise DataQualityError("Freeze selection requires exactly 2023-2024 development metrics")
    overall = metrics.filter((pl.col("scope") == "overall") & (pl.col("horizon") == "early_week"))
    lookup = {r["model"]: r for r in overall.to_dicts()}
    control = lookup["ridge_epa_scores"]
    gains = {
        r["model"]: r for r in comparisons.filter(pl.col("horizon") == "early_week").to_dicts()
    }
    selection = POLICY["selection"]
    checks = []
    eligible = []
    for name in MODELS:
        if name == "ridge_epa_scores":
            continue
        row = lookup[name]
        reasons = []
        if gains[name]["margin_mae_gain"] < selection["minimum_margin_gain"]:
            reasons.append("insufficient margin improvement")
        if gains[name]["week_block_95_lower"] <= 0:
            reasons.append("paired week-block range includes no improvement")
        for quantity in ("score", "total"):
            if (
                row[f"{quantity}_mae"]
                > control[f"{quantity}_mae"] + selection[f"maximum_{quantity}_regression"]
            ):
                reasons.append(f"{quantity} error regresses")
        for season in (2023, 2024):
            yearly = metrics.filter(
                (pl.col("scope") == "season")
                & (pl.col("horizon") == "early_week")
                & (pl.col("season") == season)
            )
            baseline = yearly.filter(pl.col("model") == "ridge_epa_scores")["margin_mae"].item()
            candidate = yearly.filter(pl.col("model") == name)["margin_mae"].item()
            if candidate > baseline + selection["maximum_single_season_margin_regression"]:
                reasons.append(f"{season} margin error regresses")
        checks.append({"model": name, "eligible": not reasons, "reasons": reasons})
        if not reasons:
            eligible.append(name)
    chosen = (
        min(
            eligible,
            key=lambda name: (
                lookup[name]["margin_mae"],
                len(MODELS[name]),
                list(MODELS).index(name),
            ),
        )
        if eligible
        else "ridge_epa_scores"
    )
    return {
        "model": chosen,
        "features": list(MODELS[chosen]),
        "ridge_alpha": 20.0,
        "selection_seasons": [2023, 2024],
        "initial_training_season": 2022,
        "checks": checks,
        "2025_outcomes_used_for_selection": False,
        "interpretation": (
            "2025 already accessed elsewhere; this lock does not create an untouched holdout"
        ),
    }


def require_replay_ready(audit: dict[str, Any]) -> None:
    blockers = audit.get("blockers", [])
    if blockers or audit.get("ready_for_enriched_2025") is not True:
        raise DataQualityError(
            "Enriched 2025 replay blocked: " + "; ".join(blockers or ["source audit incomplete"])
        )


def write_comparison_charts(predictions: pl.DataFrame, destination: Path) -> None:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    metrics = score_metrics(predictions).filter(pl.col("scope") == "overall")
    figure = make_subplots(
        rows=1, cols=2, subplot_titles=["Tuesday forecast", "Pre-kickoff context refresh"]
    )
    for column, horizon in enumerate(HORIZONS, 1):
        group = metrics.filter(pl.col("horizon") == horizon)
        figure.add_trace(
            go.Bar(
                x=group["model"].to_list(),
                y=group["margin_mae"].to_list(),
                name="Margin MAE",
                showlegend=column == 1,
            ),
            row=1,
            col=column,
        )
        figure.update_yaxes(title_text="Points of error (lower is better)", row=1, col=column)
    figure.update_layout(
        template="plotly_white",
        height=470,
        title="2023–2024 development: same games, weekly locked fits",
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    figure.write_html(destination, include_plotlyjs="cdn", config={"displaylogo": False})


def save_walkforward_development(data_dir: Path, report_dir: Path) -> dict[str, Any]:
    audit = fetch_pregame_audit(data_dir)
    directory = data_dir / "processed" / "team_walkforward"
    source_hash = walkforward_source_hash()
    # Freeze the computational protocol before constructing features or evaluating candidates.
    write_json(
        directory / "protocol.json",
        {
            "policy": POLICY,
            "source_sha256": source_hash,
            "recorded_at_utc": datetime.now(UTC).isoformat(),
        },
    )
    teams, games, team_manifest = load_team_cache(data_dir)
    _, injuries, context_manifest = load_context_cache(data_dir)
    teams = teams.filter(pl.col("season").is_between(2022, 2024))
    games = games.filter(pl.col("season").is_between(2022, 2024))
    injuries = injuries.filter(pl.col("season").is_between(2022, 2024))
    weather_dir = data_dir / "raw" / "team_pregame" / "weather"
    weather_manifest = read_json(weather_dir / "manifest.json")
    if weather_manifest["policy"] != WEATHER_POLICY:
        raise DataQualityError("Weather policy/cache mismatch")
    weather = load_frame(weather_dir, weather_manifest["artifact"])
    features = add_pregame_features(weekly_features(teams, games), injuries, weather)
    predictions, folds, complexity = evaluate_weeks(features)
    metrics = score_metrics(predictions)
    comparisons = paired_comparisons(predictions)
    incremental = paired_comparisons(predictions, control_model="compact_epa")
    frozen = {
        **freeze_choice(metrics, comparisons),
        "frozen_at_utc": datetime.now(UTC).isoformat(),
        "source_sha256": source_hash,
        "policy": POLICY,
        "development_features_sha256": frame_hash(features),
        "development_predictions_sha256": frame_hash(predictions),
    }
    if walkforward_source_hash() != source_hash:
        raise DataQualityError("Code changed during the weekly experiment")
    report = {
        "format_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "source_sha256": source_hash,
        "policy": POLICY,
        "runtime": runtime_versions(),
        "input_scope": [2022, 2023, 2024],
        "input_hashes": {
            "teams": frame_hash(teams),
            "games": frame_hash(games),
            "injuries": frame_hash(injuries),
        },
        "canonical_pipeline_sha256": team_manifest["pipeline_source_sha256"],
        "context_pipeline_sha256": context_manifest["context_source_sha256"],
        "source_manifests": {"team": team_manifest, "context": context_manifest},
        "incremental_context_control": "compact_epa",
        "audit": audit,
        "weather_manifest": weather_manifest,
        "folds": folds,
        "complexity": complexity,
        "coverage": context_coverage(features),
        "frozen_choice": frozen,
        "artifacts": {
            "features": store_frame(directory, "features", features),
            "predictions": store_frame(directory, "predictions", predictions),
            "metrics": store_frame(directory, "metrics", metrics),
            "comparisons": store_frame(directory, "comparisons", comparisons),
            "incremental_context": store_frame(directory, "incremental_context", incremental),
        },
    }
    write_json(directory / "manifest.json", report)
    write_json(directory / "frozen_2025.json", frozen)
    report_dir.mkdir(parents=True, exist_ok=True)
    metrics.write_csv(report_dir / "metrics.csv")
    comparisons.write_csv(report_dir / "paired_comparisons.csv")
    incremental.write_csv(report_dir / "incremental_context.csv")
    pl.DataFrame(report["coverage"]).write_csv(report_dir / "coverage.csv")
    pl.DataFrame(complexity).write_csv(report_dir / "complexity.csv")
    write_json(report_dir / "frozen_2025.json", frozen)
    write_json(report_dir / "source_audit.json", audit)
    write_comparison_charts(predictions, report_dir / "development.html")
    summary = metrics.filter(
        (pl.col("scope") == "overall") & (pl.col("horizon") == "early_week")
    ).select("model", "games", "margin_mae", "score_mae", "total_mae")
    text = (
        "# Weekly context development and 2025 readiness\n\n"
        "2022 supplies the initial history; 2023–2024 supply 544 development games per horizon. "
        "No 2021/earlier history or 2025 outcomes enter this experiment. Fits and outcome-derived "
        "EPA/rest inputs lock on Tuesday; refreshed injuries/temperature are gated by their own "
        "cutoffs. This protocol differs from the older 2016-initialized evaluation.\n\n"
        + "\n".join(markdown_table(summary))
        + "\n\n"
        + f"Frozen choice: **{frozen['model']}**. Selection requires 0.05 points of margin "
        "improvement, limited score/total regression, seasonal stability, and a positive lower "
        "bound in the approximate paired week-block range. No published model is changed.\n\n"
        "The exact-minimum rule can select extra inputs for a tiny difference: inspect the "
        "compact-EPA comparisons in `incremental_context.csv` before interpreting the lock. "
        "They are descriptive ablations added after the initial development run, and do not "
        "change its selection rule. A lower combined-model error does not establish that the "
        "added context helped rather than the reduced EPA inputs.\n\n"
        "The availability trial measures partial observed Out counts, not confirmed starters or "
        "complete rosters. Missing reports stay unknown. Temperature uses older GFS city forecasts "
        "with a six-hour assumed publication buffer; refresh is at least 24 hours old. Wind/rain "
        "are excluded because 2022–2023 values are unavailable. Fixed indoor effects are zero; "
        "retractable/canopy exposure stays unknown. Observed game weather/roof and actual game QB "
        "labels never enter the candidates.\n\n"
        "## Enriched 2025 replay\n\n"
        "**Blocked by input readiness. No enriched 2025 prediction/evaluation has been run.**\n\n"
        + "\n".join(f"- {reason}" for reason in audit["blockers"])
        + "\n\n"
        "2025 depth-chart snapshots do contain timestamps, but the development sources do not. "
        "A weekly game label or file retrieval date cannot stand in for pregame publication. "
        "NFL articles can be edited after kickoff; their initial publication date does not "
        "date every injury status. A dated source provider or preserved report versions is "
        "required before the full 272-game test can pass the readiness gate.\n\n"
        "Sources: [nflverse timing]"
        "(https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html), "
        "[depth archives](https://github.com/nflverse/nflverse-data/releases/tag/depth_charts), "
        "[injury archives](https://github.com/nflverse/nflverse-data/releases/tag/injuries), "
        "[forecast archive](https://open-meteo.com/en/docs/previous-runs-api).\n\n"
        "Features/predictions are stored once in ignored local Parquet files. Folds reference "
        "training game IDs by checksum rather than copying training tables. The compact HTML "
        "chart loads Plotly from its CDN. Exact historical source publication and revisions "
        "remain assumptions; 2025 has already been examined elsewhere.\n"
    )
    (report_dir / "findings.md").write_text(text, encoding="utf-8")
    return report


def load_walkforward_development(data_dir: Path) -> dict[str, Any]:
    directory = data_dir / "processed" / "team_walkforward"
    report = read_json(directory / "manifest.json")
    if report["source_sha256"] != walkforward_source_hash():
        raise DataQualityError("Weekly experiment code changed; cached lock is stale")
    for metadata in report["artifacts"].values():
        load_frame(directory, metadata)
    frozen = read_json(directory / "frozen_2025.json")
    if frozen != report["frozen_choice"]:
        raise DataQualityError("Frozen choice changed after development")
    return report


def check_2025_replay(data_dir: Path, report_dir: Path) -> dict[str, Any]:
    """A blocked readiness check never touches 2025 outcomes or fits a model."""
    report = load_walkforward_development(data_dir)
    audit = fetch_pregame_audit(data_dir)
    status = {
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "frozen_model": report["frozen_choice"]["model"],
        "source_sha256": report["source_sha256"],
        "status": "blocked",
        "blockers": audit["blockers"],
        "2025_fits_performed": 0,
        "next": "Integrate dated roster/starter/injury versions and audit forecast weather",
    }
    try:
        require_replay_ready(audit)
    except DataQualityError:
        write_json(report_dir / "replay_status.json", status)
        return status
    # Do not automatically turn a new provider/schema into model features. It requires a
    # new audited adapter and development lock; no force/bypass option is provided.
    raise DataQualityError(
        "Sources are ready; integrate the audited adapters before the full replay"
    )
