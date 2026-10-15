"""Small dated-injury ablation; original model choices and replay gates stay intact."""

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nfl_prop_model.data.schemas import DataQualityError, require_keys
from nfl_prop_model.data.storage import load_frame, read_json, sha256_file, store_frame, write_json
from nfl_prop_model.data.team_injury_news import load_news, news_path, news_schedule
from nfl_prop_model.data.team_injury_news_audit import (
    FORMAL,
    GROUPS,
    load_news_audit,
    news_records,
    news_source_hash,
    news_summary,
)
from nfl_prop_model.data.team_injury_news_variants import qualified_path
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
FLAGS = "compact_report_flags"
INJURY = "compact_dated_out_counts"
REPORT_FLAGS = (
    "home_formal_report",
    "away_formal_report",
    "home_positions_known",
    "away_positions_known",
    "both_positions_known",
)
CONTRASTS = ("offensive_out_difference", "defensive_out_difference")
MODELS = {
    CONTROL: COMPACT_EPA,
    FLAGS: COMPACT_EPA + REPORT_FLAGS,
    INJURY: COMPACT_EPA + REPORT_FLAGS + CONTRASTS,
}
POLICY: dict[str, Any] = {
    "scope": "2022 initialization; all 544 development games in 2023-2024 at both horizons",
    "timing": "Tuesday-locked EPA/history and coefficient fits, separately by horizon",
    "sources": "2022 canonical-only initialization; qualified canonical/tracking 2023-2024 audit",
    "initialization_revision": (
        "uniform canonical-only source declared before real fits after one tracking index "
        "remained unavailable; no partially collected tracking editions used for 2022"
    ),
    "reports": "formal designations or explicit none; missing/partial/stale reports stay unknown",
    "positions": "all Out positions must be known; no player-to-starter inference",
    "contrasts": "home minus away QB+OL+skill and defense Out counts; both teams required",
    "unknown_contrasts": "neutral zero with five availability flags, including both-known",
    "coverage_control": "flags-only includes the same five report/position availability flags",
    "models": {name: list(columns) for name, columns in MODELS.items()},
    "fit": "Ridge alpha 20; train-only median/indicators/scaling; common fractional-score contract",
    "primary_horizon": "pregame_refresh; this source has no Tuesday development formal reports",
    "selection": "injury must pass against both EPA and flags-only; otherwise compact EPA",
    "guards": {
        "minimum_margin_gain": 0.05,
        "maximum_score_regression": 0.05,
        "maximum_total_regression": 0.10,
        "maximum_single_season_margin_regression": 0.25,
        "positive_paired_lower_bound": True,
    },
    "bootstrap": {"draws": 5000, "seed": 20261015, "blocks": "season/week"},
    "secondary": "both-position-covered games only; retain every game in primary metrics",
    "following_season": "select on 2023 only, assess 2024; both seasons previously examined",
    "2025": "zero outcome reads, fits or replay; original enriched readiness gate unchanged",
    "promotion": "separate research record; no active-model or original-lock changes",
    "storage": "reuse features; one context delta and prediction panel; no training copies",
}


def dated_source_hash() -> str:
    return hashlib.sha256(
        Path(__file__).read_bytes()
        + news_source_hash().encode()
        + simplicity_source_hash().encode()
    ).hexdigest()


def add_dated_context(base: pl.DataFrame, news: pl.DataFrame) -> pl.DataFrame:
    """Join complete cutoff records; neutral unknown contrasts retain explicit missingness."""
    require_keys(base, ["game_id", "horizon"], "dated-injury base")
    require_keys(news, ["game_id", "horizon", "side"], "dated injury reports")
    if (
        not set(base["season"]) <= {2022, 2023, 2024}
        or set(base["horizon"]) != set(HORIZONS)
        or news.height != 2 * base.height
    ):
        raise DataQualityError("Dated injury comparison requires complete development cutoffs")
    lookup = {(r["game_id"], r["horizon"], r["side"]): r for r in news.to_dicts()}
    records = []
    for row in base.to_dicts():
        if not row["week_lock_utc"] <= row["prediction_time_utc"] < row["kickoff_utc"]:
            raise DataQualityError("Invalid dated injury prediction timing")
        record: dict[str, Any] = {"game_id": row["game_id"], "horizon": row["horizon"]}
        values: dict[str, tuple[Any, Any]] = {}
        for side in ("home", "away"):
            source = lookup.get((row["game_id"], row["horizon"], side))
            if source is None or any(
                source[k] != expected
                for k, expected in (
                    ("team", row[f"{side}_team"]),
                    ("season", row["season"]),
                    ("week", row["week"]),
                    ("cutoff_utc", row["prediction_time_utc"]),
                )
            ):
                raise DataQualityError("Dated injury team/week/cutoff mismatch")
            formal = source["status"] in FORMAL
            stamp = source["archive_captured_at_utc"]
            if formal and (
                source["reason"] is not None
                or stamp is None
                or not stamp < row["prediction_time_utc"] <= stamp + timedelta(hours=72)
                or source["out_count"] is None
            ):
                raise DataQualityError("Formal injury report has unavailable timing or counts")
            counts = [source[g + "_out_count"] for g in GROUPS]
            known = (
                formal
                and source["unknown_position_out_players"] == 0
                and all(c is not None for c in counts)
            )
            if known and (
                any(c < 0 or int(c) != c for c in counts) or sum(counts) != source["out_count"]
            ):
                raise DataQualityError("Injury position counts do not reconcile")
            record[f"{side}_formal_report"] = float(formal)
            record[f"{side}_positions_known"] = float(known)
            record[f"{side}_news_captured_at_utc"] = stamp if formal else None
            values[side] = (
                (
                    source["QB_out_count"] + source["OL_out_count"] + source["skill_out_count"],
                    source["defense_out_count"],
                )
                if known
                else (None, None)
            )
        both = bool(record["home_positions_known"] and record["away_positions_known"])
        record["both_positions_known"] = float(both)
        for i, name in enumerate(CONTRASTS):
            record[name] = float(values["home"][i] - values["away"][i]) if both else 0.0
        records.append(record)
    delta = pl.DataFrame(records, infer_schema_length=None).with_columns(
        pl.col("home_news_captured_at_utc", "away_news_captured_at_utc").cast(
            pl.Datetime("us", "UTC")
        )
    )
    return base.join(delta, on=["game_id", "horizon"], validate="1:1", how="left")


def evaluate_dated(
    features: pl.DataFrame, *, seasons: tuple[int, ...] = (2023, 2024)
) -> tuple[pl.DataFrame, list[dict[str, Any]], list[dict[str, Any]]]:
    if (
        not seasons
        or not set(seasons) <= {2023, 2024}
        or not set(features["season"]) <= {2022, 2023, 2024}
    ):
        raise DataQualityError("Dated evaluation excludes 2025 and pre-2022 history")
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
                raise DataQualityError("Insufficient or overlapping dated-injury training")
            latest = train["result_available_at_utc"].max()
            if not isinstance(latest, datetime):
                raise DataQualityError("Missing dated-injury training availability")
            targets = train.select("home_score", "away_score").to_numpy()
            values = {}
            for name, columns in MODELS.items():
                matrix = np.asarray(train.select(columns).to_numpy(), dtype=np.float64)
                model = score_pipeline().fit(matrix, targets)
                values[name] = np.asarray(model.predict(test.select(columns).to_numpy()))
                if first["week"] == 1:
                    encoded = model[:-1].transform(matrix)
                    complexity.append(
                        {
                            "season": first["season"],
                            "horizon": horizon,
                            "model": name,
                            "raw_features": len(columns),
                            "encoded_features": encoded.shape[1],
                            "allocated_weights_and_intercepts": int(model[-1].coef_.size + 2),
                            "context_standardized_coefficients": {
                                col: model[-1].coef_[:, i].tolist()
                                for i, col in enumerate(columns)
                                if col in REPORT_FLAGS + CONTRASTS
                            },
                        }
                    )
            records.extend(forecast_rows(test, values, train))
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
                    f"Dated injury comparison: {first['season']} week {first['week']} {horizon}",
                    flush=True,
                )
    panel = pl.DataFrame(records, infer_schema_length=None).join(
        features.select(
            "game_id", "horizon", "week_lock_utc", "result_available_at_utc", "both_positions_known"
        ),
        on=["game_id", "horizon"],
        validate="m:1",
    )
    require_keys(panel, ["game_id", "horizon", "model"], "dated injury predictions")
    expected = set(features.filter(pl.col("season").is_in(seasons))["game_id"])
    for horizon in HORIZONS:
        for name in MODELS:
            if (
                set(
                    panel.filter((pl.col("horizon") == horizon) & (pl.col("model") == name))[
                        "game_id"
                    ]
                )
                != expected
            ):
                raise DataQualityError("Dated comparison cohorts differ")
    return panel, folds, complexity


def choose_dated(panel: pl.DataFrame, seasons: tuple[int, ...], lock: datetime) -> dict[str, Any]:
    selected = panel.filter(pl.col("season").is_in(seasons))
    if (
        seasons not in {(2023,), (2023, 2024)}
        or set(selected["season"]) != set(seasons)
        or selected.filter(pl.col("result_available_at_utc") >= lock).height
    ):
        raise DataQualityError("Dated selection includes unavailable validation outcomes")
    metrics = score_metrics(selected)
    refresh = selected.filter(pl.col("horizon") == "pregame_refresh")
    checks = []
    for control in (CONTROL, FLAGS):
        paired = paired_gain(refresh, INJURY, control)
        reasons = []
        if paired["margin_mae_gain"] < POLICY["guards"]["minimum_margin_gain"]:
            reasons.append("insufficient incremental margin improvement")
        if paired["week_block_95_lower"] <= 0:
            reasons.append("paired range includes no improvement")
        for horizon in HORIZONS:
            group = metrics.filter((pl.col("scope") == "overall") & (pl.col("horizon") == horizon))
            by_model = {r["model"]: r for r in group.to_dicts()}
            for quantity, tolerance in (("score", 0.05), ("total", 0.10), ("margin", 0.0)):
                if (
                    by_model[INJURY][quantity + "_mae"]
                    > by_model[control][quantity + "_mae"] + tolerance + 1e-12
                ):
                    reasons.append(f"{horizon} {quantity} regression")
            for season in seasons:
                rows = metrics.filter(
                    (pl.col("scope") == "season")
                    & (pl.col("horizon") == horizon)
                    & (pl.col("season") == season)
                )
                yearly = {r["model"]: r for r in rows.to_dicts()}
                if yearly[INJURY]["margin_mae"] > yearly[control]["margin_mae"] + 0.25:
                    reasons.append(f"{season} {horizon} margin regression")
        checks.append({"control": control, **paired, "eligible": not reasons, "reasons": reasons})
    name = INJURY if all(r["eligible"] for r in checks) else CONTROL
    return {
        "model": name,
        "features": list(MODELS[name]),
        "selection_seasons": list(seasons),
        "simulated_lock_utc": lock.isoformat(),
        "checks": checks,
        "primary_horizon": "pregame_refresh",
        "2025_outcomes_used": False,
    }


def _inputs(data_dir: Path) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, dict[str, Any]]:
    upstream = load_walkforward_development(data_dir)
    path = data_dir / "processed" / "team_walkforward" / "manifest.json"
    refs: dict[str, Any] = {"weekly": {"path": str(path.resolve()), "sha256": sha256_file(path)}}
    base = load_frame(path.parent, upstream["artifacts"]["features"])
    controls = load_frame(path.parent, upstream["artifacts"]["predictions"]).filter(
        pl.col("model") == CONTROL
    )
    initial_games, initial_schedule = news_schedule(data_dir, (2022,))
    initial_raw = news_path(data_dir, initial_games)
    initial = news_records(initial_games, load_news(initial_raw, initial_games), initial_raw.parent)
    sources = [initial]
    refs["(2022,)"] = {
        "path": str(initial_raw.resolve()),
        "sha256": sha256_file(initial_raw),
        "schedule": initial_schedule,
        "collection_policy": "canonical-only, uniformly across all initialization weeks",
        "coverage": news_summary(initial, ["season", "horizon"]),
    }
    for seasons in ((2023, 2024),):
        games, schedule = news_schedule(data_dir, seasons)
        raw = qualified_path(data_dir, games)
        input_hash = hashlib.sha256(
            (sha256_file(raw) + schedule["manifest_sha256"]).encode()
        ).hexdigest()
        path = (
            data_dir
            / "processed"
            / "team_injury_news"
            / f"{news_source_hash()[:12]}-{input_hash[:12]}"
            / "manifest.json"
        )
        audit = load_news_audit(path, data_dir)
        sources.append(load_frame(path.parent, audit["artifact"]))
        refs[str(seasons)] = {
            "path": str(path.resolve()),
            "sha256": sha256_file(path),
            "coverage": audit["coverage"],
        }
    if set(base["season"]) != {2022, 2023, 2024} or base.height != 1630 or controls.height != 1088:
        raise DataQualityError("Dated comparison requires the original complete weekly cohort")
    return base, controls, pl.concat(sources), refs


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


def load_dated_report(path: Path, data_dir: Path) -> dict[str, Any]:
    report = read_json(path)
    if (
        report["format_version"] != 1
        or report["source_sha256"] != dated_source_hash()
        or report["policy"] != POLICY
    ):
        raise DataQualityError("Dated comparison code/policy changed")
    base, controls, news, refs = _inputs(data_dir)
    if refs != report["inputs"]:
        raise DataQualityError("Dated comparison source inputs changed")
    context = load_frame(path.parent, report["artifacts"]["context"])
    expected = add_dated_context(base, news).select(context.columns)
    if not context.equals(expected):
        raise DataQualityError("Dated comparison context differs from source cutoffs")
    panel = load_frame(path.parent, report["artifacts"]["predictions"])
    _check_control(panel, controls)
    if score_metrics(panel).to_dicts() != report["metrics"]:
        raise DataQualityError("Dated comparison metrics changed")
    frozen = read_json(path.parent / "frozen_2025.json")
    if frozen != report["frozen_research_choice"] or frozen != choose_dated(
        panel, (2023, 2024), datetime(2025, 9, 2, 15, tzinfo=UTC)
    ):
        raise DataQualityError("Dated comparison research choice changed")
    if report["enriched_replay_ready"] is not False or report["2025_fits_performed"] != 0:
        raise DataQualityError("Dated comparison cannot clear enriched replay readiness")
    return report


def save_dated_comparison(data_dir: Path, report_dir: Path) -> dict[str, Any]:
    source = dated_source_hash()
    base, controls, news, refs = _inputs(data_dir)
    signature = hashlib.sha256(
        str(sorted((k, v["sha256"]) for k, v in refs.items())).encode()
    ).hexdigest()
    folder = data_dir / "processed" / "team_dated_injuries" / f"{source[:12]}-{signature[:12]}"
    path = folder / "manifest.json"
    if path.exists():
        report = load_dated_report(path, data_dir)
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
        features = add_dated_context(base, news)
        panel, folds, complexity = evaluate_dated(features)
        _check_control(panel, controls)
        lock_2024 = base.filter(pl.col("season") == 2024)["week_lock_utc"].min()
        if not isinstance(lock_2024, datetime):
            raise DataQualityError("Missing following-season lock")
        chosen_2024 = choose_dated(panel, (2023,), lock_2024)
        frozen = choose_dated(panel, (2023, 2024), datetime(2025, 9, 2, 15, tzinfo=UTC))
        comparisons = []
        for horizon in HORIZONS:
            group = panel.filter(pl.col("horizon") == horizon)
            for candidate, control in ((FLAGS, CONTROL), (INJURY, CONTROL), (INJURY, FLAGS)):
                comparisons.append(
                    {
                        "horizon": horizon,
                        "model": candidate,
                        "control": control,
                        **paired_gain(group, candidate, control),
                    }
                )
        covered = panel.filter(pl.col("both_positions_known") == 1)
        coverage = (
            features.group_by("season", "horizon")
            .agg(pl.len().alias("games"), pl.col(REPORT_FLAGS).sum())
            .sort("season", "horizon")
        )
        if dated_source_hash() != source or _inputs(data_dir)[3] != refs:
            raise DataQualityError("Dated sources changed during comparison")
        report = {
            "format_version": 1,
            "recorded_at_utc": datetime.now(UTC).isoformat(),
            "source_sha256": source,
            "policy": POLICY,
            "inputs": refs,
            "metrics": score_metrics(panel).to_dicts(),
            "comparisons": comparisons,
            "both_covered_secondary_metrics": score_metrics(covered).to_dicts()
            if covered.height
            else [],
            "coverage": coverage.to_dicts(),
            "folds": folds,
            "complexity": complexity,
            "following_season_choice": chosen_2024,
            "following_season_selected_metrics": score_metrics(
                panel.filter((pl.col("season") == 2024) & (pl.col("model") == chosen_2024["model"]))
            ).to_dicts(),
            "frozen_research_choice": frozen,
            "2025_fits_performed": 0,
            "enriched_replay_ready": False,
            "remaining_plan": (
                "dated active/IR rosters and confirmed starters; historical forecast weather "
                "and audited adapters; enriched 2025 replay; then PEM-inspired comparison"
            ),
            "artifacts": {
                "context": store_frame(
                    folder,
                    "context",
                    features.select(
                        "game_id",
                        "horizon",
                        *REPORT_FLAGS,
                        *CONTRASTS,
                        "home_news_captured_at_utc",
                        "away_news_captured_at_utc",
                    ),
                ),
                "predictions": store_frame(folder, "predictions", panel),
            },
        }
        write_json(folder / "frozen_2025.json", frozen)
        write_json(path, report)
    load_dated_report(path, data_dir)
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
        "# Dated injury development comparison\n\n2022 initialization; every 2023-2024 game; "
        "Tuesday-locked history and fits. This limited research test controls for missing "
        "reports and does not establish complete rosters or starters.\n\n"
        + "\n".join(markdown_table(summary))
        + f"\n\n2023-only choice for 2024: **{report['following_season_choice']['model']}**. "
        + f"Separate pre-2025 research choice: **{report['frozen_research_choice']['model']}**.\n\n"
        "Counts must improve refresh margin MAE by at least 0.05 against both EPA and flags-only, "
        "with a positive paired lower bound and score/total/season guards. Both-covered results "
        "are secondary; no uncovered games are removed from primary results. Unknown contrasts "
        "use neutral zero with identical availability controls in both context models.\n\n"
        "The added rule and source sample are adaptive development. 2023/2024 were already "
        "examined. No 2025 outcomes or fits enter this command; original model choices and "
        "enriched replay gate remain intact. Full roster/starter timing and historical weather "
        "adapters remain required before that replay. "
        "The PEM-inspired comparison follows completion of the original plan.\n",
        encoding="utf-8",
    )
    return report
