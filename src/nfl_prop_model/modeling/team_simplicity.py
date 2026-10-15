"""Adaptive selection audit on existing weekly predictions; no new fits or 2025 outcomes."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nfl_prop_model.data.schemas import DataQualityError, require_keys
from nfl_prop_model.data.storage import load_frame, read_json, sha256_file, store_frame, write_json
from nfl_prop_model.features.team_epa import HORIZONS
from nfl_prop_model.features.team_walkforward import MODELS
from nfl_prop_model.modeling.reporting import markdown_table
from nfl_prop_model.modeling.team_scores import score_metrics
from nfl_prop_model.modeling.team_walkforward import (
    POLICY as ORIGINAL_POLICY,
)
from nfl_prop_model.modeling.team_walkforward import (
    load_walkforward_development,
    walkforward_source_hash,
)

CONTROL = "ridge_epa_scores"
COMPACT = "compact_epa"
SELECTED = "simplicity_selected"
POLICY: dict[str, Any] = {
    "adaptive": "new rule after inspecting the existing 2023-2024 development results",
    "inputs": "reuse verified weekly predictions; no new estimator families or penalty search",
    "history": "2022 initialization; outcome history/fits frozen at Tuesday; original timing",
    "following_season_check": "select on 2023 only before 2024 lock; assess 2024 both horizons",
    "research_lock": "select on 2023-2024 before simulated 2025-09-02T15:00:00Z lock",
    "guards": {
        key: value
        for key, value in ORIGINAL_POLICY["selection"].items()
        if key.startswith(("minimum_", "maximum_", "require_"))
    },
    "context_increment": "each context addition must independently pass the same guards "
    "against compact EPA",
    "simplicity": "within one block-bootstrap standard error of best eligible margin MAE, "
    "prefer fewer declared inputs",
    "bootstrap": {"draws": 5000, "seed": 20261015, "blocks": "season/week"},
    "limits": "examined development data; one-standard-error rule is a heuristic; "
    "no independent holdout claim",
    "2025": "zero outcomes read or fits performed; original enriched replay gate remains unchanged",
    "promotion": "separate research choice; published scores and original frozen record unchanged",
    "storage": "reference original inputs; retain compact derived tables only; no training copies",
}


def simplicity_source_hash() -> str:
    return hashlib.sha256(
        walkforward_source_hash().encode() + Path(__file__).read_bytes()
    ).hexdigest()


def validate_panel(panel: pl.DataFrame) -> None:
    require_keys(panel, ["game_id", "horizon", "model"], "selection predictions")
    if set(panel["model"]) != set(MODELS) or set(panel["horizon"]) != set(HORIZONS):
        raise DataQualityError("Selection requires every declared candidate and both horizons")
    expected = set(panel["game_id"])
    for group in panel.partition_by(["horizon", "model"]):
        if set(group["game_id"]) != expected:
            raise DataQualityError("Selection candidate cohorts differ")
    facts = (
        "season",
        "week",
        "kickoff_utc",
        "prediction_time_utc",
        "week_lock_utc",
        "result_available_at_utc",
        "home_score",
        "away_score",
        "target_home_margin",
    )
    if (
        panel.group_by("game_id", "horizon")
        .agg(pl.col(facts).n_unique())
        .select(pl.any_horizontal(pl.col(facts) != 1).any())
        .item()
    ):
        raise DataQualityError("Selection candidates disagree on outcomes or timing")
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
    if panel.select(
        pl.any_horizontal(pl.col(quantities).is_null() | ~pl.col(quantities).is_finite()).any()
    ).item():
        raise DataQualityError("Selection scores must be finite and known")
    if panel.filter(
        ((pl.col("home_score") - pl.col("away_score") - pl.col("target_home_margin")).abs() > 1e-8)
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
    ).height:
        raise DataQualityError("Selection score/margin/total contract mismatch")
    timing = (
        "latest_training_result_available_utc",
        "prediction_time_utc",
        "week_lock_utc",
        "kickoff_utc",
        "result_available_at_utc",
    )
    if panel.filter(
        pl.any_horizontal(pl.col(timing).is_null())
        | (pl.col("latest_training_result_available_utc") >= pl.col("week_lock_utc"))
        | (pl.col("week_lock_utc") > pl.col("prediction_time_utc"))
        | (pl.col("prediction_time_utc") >= pl.col("kickoff_utc"))
    ).height:
        raise DataQualityError("Selection predictions include invalid weekly timing")
    if (
        panel.group_by("season", "week")
        .agg(pl.col("week_lock_utc").n_unique())
        .filter(pl.col("week_lock_utc") != 1)
        .height
    ):
        raise DataQualityError("Selection week has conflicting locks")


def paired_gain(panel: pl.DataFrame, candidate: str, control: str) -> dict[str, float]:
    def errors(model: str) -> pl.DataFrame:
        return panel.filter(pl.col("model") == model).select(
            "game_id",
            "season",
            "week",
            (pl.col("predicted_home_margin") - pl.col("target_home_margin")).abs().alias(model),
        )

    paired = errors(candidate).join(
        errors(control).select("game_id", control), on="game_id", validate="1:1"
    )
    blocks = (
        paired.group_by("season", "week")
        .agg((pl.col(control) - pl.col(candidate)).sum().alias("gain"), pl.len().alias("games"))
        .sort("season", "week")
    )
    rng = np.random.default_rng(POLICY["bootstrap"]["seed"])
    indexes = rng.integers(0, blocks.height, size=(POLICY["bootstrap"]["draws"], blocks.height))
    resampled = blocks["gain"].to_numpy()[indexes].sum(axis=1) / blocks["games"].to_numpy()[
        indexes
    ].sum(axis=1)
    lower, upper = np.quantile(resampled, [0.025, 0.975])
    return {
        "margin_mae_gain": float(
            (paired[control].to_numpy() - paired[candidate].to_numpy()).mean()
        ),
        "week_block_95_lower": float(lower),
        "week_block_95_upper": float(upper),
    }


def margin_standard_error(panel: pl.DataFrame, model: str) -> float:
    blocks = (
        panel.filter(pl.col("model") == model)
        .group_by("season", "week")
        .agg(
            (pl.col("predicted_home_margin") - pl.col("target_home_margin"))
            .abs()
            .sum()
            .alias("error"),
            pl.len().alias("games"),
        )
        .sort("season", "week")
    )
    rng = np.random.default_rng(POLICY["bootstrap"]["seed"])
    indexes = rng.integers(0, blocks.height, size=(POLICY["bootstrap"]["draws"], blocks.height))
    values = blocks["error"].to_numpy()[indexes].sum(axis=1) / blocks["games"].to_numpy()[
        indexes
    ].sum(axis=1)
    return float(values.std(ddof=1))


def select_model(panel: pl.DataFrame, seasons: tuple[int, ...], lock: datetime) -> dict[str, Any]:
    if seasons not in {(2023,), (2023, 2024)} or lock.tzinfo is None:
        raise ValueError("Select on 2023 or 2023-2024 with an aware lock")
    validation = panel.filter(pl.col("season").is_in(seasons))
    validate_panel(validation)
    if (
        set(validation["season"]) != set(seasons)
        or validation.filter(pl.col("result_available_at_utc") >= lock).height
    ):
        raise DataQualityError("Selection validation outcomes were unavailable at lock")
    early = validation.filter(pl.col("horizon") == "early_week")
    metrics = score_metrics(early)
    overall = {r["model"]: r for r in metrics.filter(pl.col("scope") == "overall").to_dicts()}
    yearly = {
        (r["model"], r["season"]): r for r in metrics.filter(pl.col("scope") == "season").to_dicts()
    }
    guard = POLICY["guards"]
    checks = []
    eligible = [CONTROL]
    for name in MODELS:
        if name == CONTROL:
            continue
        comparisons = []
        reasons: list[str] = []
        for control in (CONTROL,) if name == COMPACT else (CONTROL, COMPACT):
            gain = paired_gain(early, name, control)
            failures = []
            if gain["margin_mae_gain"] < guard["minimum_margin_gain"]:
                failures.append("margin gain below 0.05 points")
            if gain["week_block_95_lower"] <= 0:
                failures.append("paired range includes no gain")
            for quantity in ("score", "total"):
                if (
                    overall[name][f"{quantity}_mae"]
                    > overall[control][f"{quantity}_mae"] + guard[f"maximum_{quantity}_regression"]
                ):
                    failures.append(f"{quantity} error regresses")
            for season in seasons:
                if (
                    yearly[(name, season)]["margin_mae"]
                    > yearly[(control, season)]["margin_mae"]
                    + guard["maximum_single_season_margin_regression"]
                ):
                    failures.append(f"{season} margin error regresses")
            comparisons.append({"control": control, **gain, "reasons": failures})
            reasons.extend(f"against {control}: {reason}" for reason in failures)
        checks.append(
            {"model": name, "eligible": not reasons, "reasons": reasons, "comparisons": comparisons}
        )
        if not reasons:
            eligible.append(name)
    best = min(eligible, key=lambda name: overall[name]["margin_mae"])
    error = margin_standard_error(early, best)
    within = [
        name
        for name in eligible
        if overall[name]["margin_mae"] <= overall[best]["margin_mae"] + error
    ]
    chosen = min(
        within,
        key=lambda name: (len(MODELS[name]), overall[name]["margin_mae"], list(MODELS).index(name)),
    )
    return {
        "model": chosen,
        "features": list(MODELS[chosen]),
        "ridge_alpha": 20.0,
        "validation_seasons": list(seasons),
        "simulated_lock_utc": lock.isoformat(),
        "validation_games": early.filter(pl.col("model") == CONTROL).height,
        "best_eligible_model": best,
        "one_standard_error": error,
        "within_one_standard_error": within,
        "checks": checks,
        "2025_outcomes_used": False,
        "adaptive_development": True,
    }


def audit_selection(panel: pl.DataFrame) -> tuple[dict[str, Any], pl.DataFrame]:
    if set(panel["season"]) != {2023, 2024}:
        raise DataQualityError("Simplicity audit requires exactly 2023-2024; excludes 2025")
    validate_panel(panel)
    lock = panel.filter(pl.col("season") == 2024)["week_lock_utc"].min()
    if not isinstance(lock, datetime):
        raise DataQualityError("Missing 2024 season lock")
    following = select_model(panel, (2023,), lock)
    frozen = select_model(panel, (2023, 2024), datetime(2025, 9, 2, 15, tzinfo=UTC))
    selected = panel.filter(
        (pl.col("season") == 2024) & (pl.col("model") == following["model"])
    ).with_columns(pl.lit(SELECTED).alias("model"))
    evaluated = pl.concat([panel.filter(pl.col("season") == 2024), selected])
    report = {
        "following_season_choice": following,
        "frozen_2025_research_choice": frozen,
        "following_season_metrics": score_metrics(evaluated).to_dicts(),
        "following_season_margin_gains": [
            {
                "horizon": horizon,
                "control": control,
                **paired_gain(evaluated.filter(pl.col("horizon") == horizon), SELECTED, control),
            }
            for horizon in HORIZONS
            for control in (CONTROL, COMPACT)
        ],
        "2025_fits_performed": 0,
    }
    return report, selected


def load_simplicity_report(path: Path) -> dict[str, Any]:
    report = read_json(path)
    if report["source_sha256"] != simplicity_source_hash() or report["policy"] != POLICY:
        raise DataQualityError("Simplicity source/policy changed; cached choice is stale")
    source = Path(report["upstream_manifest"])
    if sha256_file(source) != report["upstream_manifest_sha256"]:
        raise DataQualityError("Original weekly development manifest changed")
    load_walkforward_development(source.parents[2])
    for metadata in report["artifacts"].values():
        load_frame(path.parent, metadata)
    if read_json(path.parent / "frozen_2025.json") != report["frozen_2025_research_choice"]:
        raise DataQualityError("Simplicity choice changed after selection")
    return report


def save_simplicity_check(data_dir: Path, report_dir: Path) -> dict[str, Any]:
    source_hash = simplicity_source_hash()
    upstream_path = data_dir / "processed" / "team_walkforward" / "manifest.json"
    upstream_hash = sha256_file(upstream_path)
    directory = (
        data_dir / "processed" / "team_simplicity" / f"{source_hash[:12]}-{upstream_hash[:12]}"
    )
    path = directory / "manifest.json"
    if path.exists():
        report = load_simplicity_report(path)
    else:
        write_json(
            directory / "protocol.json",
            {
                "policy": POLICY,
                "source_sha256": source_hash,
                "recorded_at_utc": datetime.now(UTC).isoformat(),
            },
        )
        upstream = load_walkforward_development(data_dir)
        base = upstream_path.parent
        features = load_frame(base, upstream["artifacts"]["features"])
        predictions = load_frame(base, upstream["artifacts"]["predictions"])
        if set(features["season"]) != {2022, 2023, 2024}:
            raise DataQualityError("Original weekly features must exclude pre-2022 and 2025")
        for fold in upstream["folds"]:
            if min(fold["training_seasons"]) < 2022:
                raise DataQualityError("Original weekly fit includes pre-2022 history")
        panel = predictions.filter(pl.col("model").is_in(MODELS)).join(
            features.select("game_id", "horizon", "result_available_at_utc", "week_lock_utc"),
            on=["game_id", "horizon"],
            validate="m:1",
            how="left",
        )
        expected = features.filter(pl.col("season").is_in([2023, 2024])).select(
            "game_id", "horizon"
        )
        observed = panel.select("game_id", "horizon").unique()
        if (
            expected.join(observed, on=["game_id", "horizon"], how="anti").height
            or observed.join(expected, on=["game_id", "horizon"], how="anti").height
        ):
            raise DataQualityError("Selection games differ from canonical weekly features")
        results, selected = audit_selection(panel)
        if simplicity_source_hash() != source_hash or sha256_file(upstream_path) != upstream_hash:
            raise DataQualityError("Sources changed during simplicity audit")
        report = {
            "format_version": 1,
            "recorded_at_utc": datetime.now(UTC).isoformat(),
            "source_sha256": source_hash,
            "policy": POLICY,
            "upstream_manifest": str(upstream_path.resolve()),
            "upstream_manifest_sha256": upstream_hash,
            "original_frozen_choice": upstream["frozen_choice"]["model"],
            "original_complexity": upstream["complexity"],
            "original_coverage": upstream["coverage"],
            "replay_blockers": upstream["audit"]["blockers"],
            **results,
            "artifacts": {
                "selected_2024_predictions": store_frame(directory, "selected_2024", selected)
            },
        }
        write_json(directory / "frozen_2025.json", report["frozen_2025_research_choice"])
        write_json(path, report)
    load_simplicity_report(path)
    report_dir.mkdir(parents=True, exist_ok=True)
    write_json(report_dir / "results.json", report)
    metrics = pl.DataFrame(report["following_season_metrics"]).filter(pl.col("scope") == "overall")
    metrics.write_csv(report_dir / "metrics.csv")
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    figure = make_subplots(
        rows=1, cols=2, subplot_titles=["2024 margin MAE", "2024 per-team score MAE"]
    )
    for horizon in HORIZONS:
        group = metrics.filter(
            (pl.col("horizon") == horizon) & pl.col("model").is_in([CONTROL, COMPACT, SELECTED])
        )
        for column, quantity in enumerate(("margin", "score"), 1):
            figure.add_trace(
                go.Bar(
                    x=group["model"].to_list(),
                    y=group[f"{quantity}_mae"].to_list(),
                    name=horizon,
                    showlegend=column == 1,
                ),
                row=1,
                col=column,
            )
    figure.update_layout(
        template="plotly_white",
        height=450,
        title="Select on 2023, assess following 2024 season (examined development data)",
    )
    figure.write_html(report_dir / "comparison.html", include_plotlyjs="cdn")
    summary = metrics.select("horizon", "model", "games", "margin_mae", "score_mae", "total_mae")
    (report_dir / "findings.md").write_text(
        "# Weekly model simplicity follow-up\n\nAdaptive development rule using existing "
        "predictions, not a fresh holdout. No new fits or 2025 outcomes.\n\n"
        f"2023-only selection for 2024: **{report['following_season_choice']['model']}**.\n\n"
        + "\n".join(markdown_table(summary))
        + "\n\n"
        "Separate simulated pre-2025 research choice: "
        f"**{report['frozen_2025_research_choice']['model']}**. "
        f"Original choice **{report['original_frozen_choice']}** remains intact. "
        "Published model unchanged.\n\n"
        "Context additions must earn at least 0.05 points against compact EPA, "
        "with score/total/season and paired-range guards. "
        "Among eligible candidates within one block standard error of the best margin MAE, "
        "prefer fewer declared inputs. "
        "The rule was added after seeing the old results; following-season separation "
        "does not undo that earlier access.\n\n"
        "Enriched 2025 replay remains gated; this command performs zero 2025 fits.\n",
        encoding="utf-8",
    )
    return report
