"""Offline history-support diagnostics from existing chronological forecasts; no refits."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nfl_prop_model.data.schemas import DataQualityError, require_columns, require_keys
from nfl_prop_model.data.storage import load_frame, read_json, sha256_file, write_json
from nfl_prop_model.modeling.config import (
    COVERAGES,
    DIAGNOSTIC_THRESHOLDS,
    TREE_FEATURES,
    TREE_PARAMETERS,
)
from nfl_prop_model.modeling.contracts import validate_model_table
from nfl_prop_model.modeling.policy import (
    MINIMUM_GROUP_RESIDUALS,
    candidate_policy,
    policy_hash,
)
from nfl_prop_model.modeling.reporting import markdown_table
from nfl_prop_model.modeling.uncertainty import ResidualCalibration

MODELS = {"prior_five_mean", "season_to_date_mean", "ridge", *TREE_FEATURES}
BUCKETS = ("0: no sample history", "1-4 prior games", "5+ prior games")
KEYS = ["game_id", "player_id"]
TIMES = ("kickoff_utc", "prediction_time_utc", "evaluation_cutoff_utc", "training_cutoff_utc")


def history_bucket() -> pl.Expr:
    return (
        pl.when(pl.col("prior_games_in_sample") == 0)
        .then(pl.lit(BUCKETS[0]))
        .when(pl.col("prior_games_in_sample") < 5)
        .then(pl.lit(BUCKETS[1]))
        .otherwise(pl.lit(BUCKETS[2]))
        .alias("history_bucket")
    )


def validated_inputs(
    frames: dict[str, pl.DataFrame],
    manifest: dict[str, Any],
) -> tuple[pl.DataFrame, pl.DataFrame]:
    method = manifest.get("method", {})
    if (
        manifest.get("format_version") != 2
        or method.get("reserved_season") != 2025
        or method.get("calibration_weeks") != 6
        or method.get("minimum_calibration_rows") != 100
        or method.get("tree_parameters") != TREE_PARAMETERS
        or method.get("tree_feature_groups")
        != {key: list(value) for key, value in TREE_FEATURES.items()}
        or method.get("nominal_coverages") != list(COVERAGES)
        or method.get("diagnostic_thresholds") != list(DIAGNOSTIC_THRESHOLDS)
    ):
        raise DataQualityError("Audit requires the saved format-2 development research protocol")
    features = frames["features"]
    validate_model_table(features)
    if features.filter(
        (pl.col("prior_games_in_sample") < 0)
        | (pl.col("prior_games_in_sample") != pl.col("prior_games_in_sample").floor())
    ).height:
        raise DataQualityError("History counts must be nonnegative whole numbers")
    points, residuals = frames["predictions"], frames["calibration_residuals"]
    for name, frame, numeric in (
        (
            "predictions",
            points,
            ("season", "week", "actual", "prediction", "prior_games_in_sample"),
        ),
        ("residuals", residuals, ("residual",)),
    ):
        require_columns(frame, name, ("game_id", "player_id", "model", "fold"), numeric)
        require_keys(frame, [*KEYS, "model", *(["fold"] if name == "residuals" else [])], name)
        columns = [*KEYS, "model", "fold", *TIMES, *numeric]
        if (
            frame.is_empty()
            or frame.select(pl.any_horizontal(pl.col(columns).is_null()).any()).item()
        ):
            raise DataQualityError(f"Missing {name} values")
        for field in TIMES:
            dtype = frame.schema[field]
            if not isinstance(dtype, pl.Datetime) or dtype.time_zone != "UTC":
                raise DataQualityError(f"{name}.{field} must be UTC")
        if set(frame["model"].unique()) != MODELS:
            raise DataQualityError(f"{name} must contain the same six research models")
    if set(points["season"].unique()) - {2023, 2024}:
        raise DataQualityError("Evaluation must contain only 2023â€“2024; 2025 is reserved")
    expected = features.filter(pl.col("season").is_in([2023, 2024])).select(KEYS)
    for model in sorted(MODELS):
        selected = points.filter(pl.col("model") == model).select(KEYS)
        if (
            selected.height != expected.height
            or expected.join(selected, on=KEYS, how="anti").height
        ):
            raise DataQualityError("Every model must score every development evaluation row")
    # Derive calibration strata from each calibration game's own pregame history, never
    # from the evaluated player's outcomes or today's current-QB membership.
    reference = features.select(
        *KEYS,
        "season",
        "week",
        "kickoff_utc",
        "prediction_time_utc",
        "prior_games_in_sample",
        pl.col("target_passing_yards").alias("actual"),
    )
    for frame, fields in (
        (
            points,
            [
                "season",
                "week",
                "kickoff_utc",
                "prediction_time_utc",
                "prior_games_in_sample",
                "actual",
            ],
        ),
        (residuals, ["kickoff_utc", "prediction_time_utc"]),
    ):
        joined = frame.join(reference, on=KEYS, how="left", suffix="_source")
        if joined["season" if frame is residuals else "season_source"].null_count():
            raise DataQualityError("Research row has no development feature identity")
        if joined.filter(
            pl.any_horizontal(pl.col(f) != pl.col(f + "_source") for f in fields)
        ).height:
            raise DataQualityError("Research rows disagree with their development feature source")
    points = points.with_columns(history_bucket())
    residuals = residuals.join(
        features.select(*KEYS, "prior_games_in_sample"), on=KEYS
    ).with_columns(history_bucket())
    audits = {fold["fold"]: fold for fold in manifest["folds"]}
    if (
        len(audits) != len(manifest["folds"])
        or set(points["fold"].unique()) != set(audits)
        or set(residuals["fold"].unique()) != set(audits)
    ):
        raise DataQualityError("Research fold identities disagree")
    for fold, audit in audits.items():
        test = points.filter(pl.col("fold") == fold)
        pool = residuals.filter(pl.col("fold") == fold)
        for field in ("training_cutoff_utc", "evaluation_cutoff_utc"):
            cutoff = datetime.fromisoformat(audit[field])
            offset = cutoff.utcoffset()
            if offset is None or offset.total_seconds() != 0:
                raise DataQualityError("Fold audit times must be UTC")
            if (
                test.filter(pl.col(field) != cutoff).height
                or pool.filter(pl.col(field) != cutoff).height
            ):
                raise DataQualityError("Research timestamps disagree with fold audit")
        if test.filter(
            ~(
                (pl.col("training_cutoff_utc") < pl.col("evaluation_cutoff_utc"))
                & (pl.col("evaluation_cutoff_utc") <= pl.col("prediction_time_utc"))
            )
        ).height:
            raise DataQualityError("Evaluation cutoffs violate pregame ordering")
        if pool.filter(
            (pl.col("prediction_time_utc") < pl.col("training_cutoff_utc"))
            | (pl.col("kickoff_utc") + pl.duration(hours=24) >= pl.col("evaluation_cutoff_utc"))
        ).height:
            raise DataQualityError("Calibration includes unavailable results")
        if (
            pool.select("game_id")
            .join(test.select("game_id").unique(), on="game_id", how="semi")
            .height
        ):
            raise DataQualityError("Calibration overlaps an evaluation game")
        members = None
        for model in sorted(MODELS):
            sample = pool.filter(pl.col("model") == model).select(KEYS)
            if sample.height != audit["calibration_rows"] or sample.height < 100:
                raise DataQualityError("Calibration count differs from the saved fold audit")
            if members is not None and members.join(sample, on=KEYS, how="anti").height:
                raise DataQualityError("Models have different calibration cohorts")
            members = sample
        if test.height != audit["test_rows"] * len(MODELS):
            raise DataQualityError("Evaluation count differs from the saved fold audit")
    return points, residuals


def summarize_intervals(frame: pl.DataFrame) -> pl.DataFrame:
    groups = []
    for scope in ("overall", "season"):
        scoped = frame.with_columns(
            pl.lit("all").alias("group")
            if scope == "overall"
            else pl.col("season").cast(pl.String).alias("group")
        )
        groups.append(
            scoped.group_by("model", "history_bucket", "coverage", "group")
            .agg(
                pl.len().alias("n"),
                pl.col("player_id").n_unique().alias("distinct_players"),
                pl.col("game_id").n_unique().alias("distinct_games"),
                pl.col("fold").n_unique().alias("distinct_weeks"),
                pl.col("pooled_hit").mean().alias("pooled_coverage"),
                pl.col("pooled_width").mean().alias("pooled_mean_width"),
                pl.col("matched_hit").count().alias("matched_available_n"),
                pl.col("player_id")
                .filter(pl.col("matched_hit").is_not_null())
                .n_unique()
                .alias("matched_distinct_players"),
                pl.col("game_id")
                .filter(pl.col("matched_hit").is_not_null())
                .n_unique()
                .alias("matched_distinct_games"),
                pl.col("fold")
                .filter(pl.col("matched_hit").is_not_null())
                .n_unique()
                .alias("matched_distinct_weeks"),
                pl.col("matched_hit").mean().alias("matched_coverage"),
                pl.col("matched_width").mean().alias("matched_mean_width"),
                pl.when(pl.col("matched_hit").is_not_null())
                .then(pl.col("pooled_hit"))
                .mean()
                .alias("paired_pooled_coverage"),
                pl.when(pl.col("matched_hit").is_not_null())
                .then(pl.col("pooled_width"))
                .mean()
                .alias("paired_pooled_mean_width"),
            )
            .with_columns(pl.lit(scope).alias("scope"))
        )
    return pl.concat(groups).sort("scope", "group", "model", "coverage", "history_bucket")


def build_calibration_audit(
    frames: dict[str, pl.DataFrame], manifest: dict[str, Any]
) -> dict[str, Any]:
    points, residuals = validated_inputs(frames, manifest)
    intervals, probabilities, support = [], [], []
    for (fold, model), test in points.partition_by("fold", "model", as_dict=True).items():
        sample = residuals.filter((pl.col("fold") == fold) & (pl.col("model") == model))
        pooled = ResidualCalibration(np.asarray(sample["residual"].to_numpy(), dtype=float))
        for bucket in BUCKETS:
            group = sample.filter(pl.col("history_bucket") == bucket)
            matched = (
                ResidualCalibration(np.asarray(group["residual"].to_numpy(), dtype=float))
                if group.height >= MINIMUM_GROUP_RESIDUALS
                else None
            )
            support.append(
                {
                    "fold": fold,
                    "model": model,
                    "history_bucket": bucket,
                    "pooled_n": sample.height,
                    "group_n": group.height,
                    "group_players": group["player_id"].n_unique(),
                    "group_games": group["game_id"].n_unique(),
                    "matched_available": matched is not None,
                }
            )
            for row in test.filter(pl.col("history_bucket") == bucket).iter_rows(named=True):
                identity = {
                    key: row[key] for key in (*KEYS, "model", "fold", "season", "history_bucket")
                }
                error = abs(row["actual"] - row["prediction"])
                for coverage in COVERAGES:
                    radius = pooled.radius(coverage)
                    group_radius = matched.radius(coverage) if matched is not None else None
                    intervals.append(
                        {
                            **identity,
                            "coverage": coverage,
                            "pooled_hit": error <= radius,
                            "pooled_width": 2 * radius,
                            "matched_hit": error <= group_radius
                            if group_radius is not None
                            else None,
                            "matched_width": 2 * group_radius if group_radius is not None else None,
                        }
                    )
                for line in DIAGNOSTIC_THRESHOLDS:
                    probability = pooled.probability_over(row["prediction"], line)
                    outcome = int(row["actual"] > line)
                    probabilities.append(
                        {
                            **identity,
                            "line": line,
                            "probability": probability,
                            "outcome": outcome,
                            "brier": (probability - outcome) ** 2,
                            "log_loss": -(
                                outcome * np.log(probability)
                                + (1 - outcome) * np.log1p(-probability)
                            ),
                            "bin": min(int(probability * 10), 9),
                        }
                    )
    probability_frame = pl.DataFrame(probabilities)
    probability_scores = (
        probability_frame.group_by("model", "history_bucket", "line", "season")
        .agg(
            pl.len().alias("n"),
            pl.col("player_id").n_unique().alias("distinct_players"),
            pl.col("game_id").n_unique().alias("distinct_games"),
            pl.col("fold").n_unique().alias("distinct_weeks"),
            pl.col("brier", "log_loss").mean(),
        )
        .sort("model", "history_bucket", "line", "season")
    )
    reliability = (
        probability_frame.group_by("model", "history_bucket", "line", "bin")
        .agg(
            pl.len().alias("n"),
            pl.col("player_id").n_unique().alias("distinct_players"),
            pl.col("game_id").n_unique().alias("distinct_games"),
            pl.col("fold").n_unique().alias("distinct_weeks"),
            pl.col("probability").mean().alias("mean_probability"),
            pl.col("outcome").mean().alias("observed_over_rate"),
        )
        .sort("model", "history_bucket", "line", "bin")
    )
    policy = candidate_policy()
    return {
        "format_version": 1,
        "scope": "development_calibration_diagnostics",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "policy": policy,
        "policy_sha256": policy_hash(policy),
        "counts": {
            "evaluated_qb_games": points.select(KEYS).unique().height,
            "models": len(MODELS),
            "weekly_folds": points["fold"].n_unique(),
        },
        "interval_metrics": summarize_intervals(pl.DataFrame(intervals)).to_dicts(),
        "probability_scores": probability_scores.to_dicts(),
        "reliability": reliability.to_dicts(),
        "calibration_support": support,
        "limitations": [
            "All rows condition on recorded QB participation, including backups and early exits; "
            "this is not a verified pregame starter cohort.",
            "Counts of rows are not independent sample sizes. Players repeat, QBs share games, "
            "and adjacent six-week calibration windows overlap. "
            "No confidence or conformal guarantee is asserted.",
            "History-matched intervals require 30 same-bucket residuals and are diagnostic only. "
            "Unavailable intervals stay unavailable; paired pooled metrics use "
            "exactly the same available subset.",
            "History buckets use each game's pregame model-sample count, not age, "
            "career experience, today's depth charts, or the current outcome. "
            "Probability lines are fixed diagnostics, not sportsbook lines.",
            "Candidate selection and these checks use development outcomes. The policy is "
            "not frozen, 2025 remains closed, and no production forecast or EV is enabled.",
        ],
    }


def load_calibration_audit(data_dir: Path) -> dict[str, Any]:
    directory = data_dir / "processed" / "research_2022_2024"
    manifest_path = directory / "manifest.json"
    manifest_hash = sha256_file(manifest_path)
    manifest = read_json(manifest_path)
    required = {
        "artifacts",
        "method",
        "folds",
        "historical_source",
        "research_source_sha256",
        "generated_at_utc",
    }
    names = ("features", "predictions", "calibration_residuals")
    if (
        manifest.get("format_version") != 2
        or required - set(manifest)
        or set(names) - set(manifest.get("artifacts", {}))
    ):
        raise DataQualityError(
            "Saved research is incomplete; run nfl-prop research before this audit"
        )
    frames = {name: load_frame(directory, manifest["artifacts"][name]) for name in names}
    report = build_calibration_audit(frames, manifest)
    if sha256_file(manifest_path) != manifest_hash:
        raise DataQualityError("Research manifest changed while reading; retry with a stable cache")
    report["provenance"] = {
        "research_manifest_sha256": manifest_hash,
        "research_source_sha256": manifest["research_source_sha256"],
        "research_generated_at_utc": manifest["generated_at_utc"],
        "historical_source": manifest["historical_source"],
        "artifacts": {name: manifest["artifacts"][name] for name in frames},
        "method": manifest["method"],
    }
    return report


def write_calibration_audit(report_dir: Path, report: dict[str, Any]) -> None:
    write_json(report_dir / "calibration_audit.json", report)
    selected = report["policy"]["model"]
    metrics = pl.DataFrame(report["interval_metrics"]).filter(
        (pl.col("scope") == "overall") & (pl.col("model") == selected) & (pl.col("coverage") == 0.9)
    )
    support = pl.DataFrame(report["calibration_support"]).filter(pl.col("model") == selected)
    support_summary = (
        support.group_by("history_bucket")
        .agg(
            pl.col("group_n").min().alias("min_residuals"),
            pl.col("group_n").median().alias("median_residuals"),
            pl.col("group_n").max().alias("max_residuals"),
            pl.col("matched_available").sum().alias("folds_with_30_residuals"),
        )
        .sort("history_bucket")
    )
    lines = [
        "# Development calibration and candidate policy",
        "",
        f"Generated {report['generated_at_utc']}.",
        "",
        f"Candidate: `{selected}`; status: `{report['policy']['status']}`. "
        "Below five prior model-sample games: research only; abstain prospectively.",
        "",
        "## Candidate's nominal 90% intervals by pregame history",
        "",
        *markdown_table(
            metrics.select(
                "history_bucket",
                "n",
                "distinct_players",
                "distinct_games",
                "distinct_weeks",
                "pooled_coverage",
                "pooled_mean_width",
                "matched_available_n",
                "matched_coverage",
                "paired_pooled_coverage",
                "matched_mean_width",
            )
        ),
        "",
        "Matched coverage and paired pooled coverage use the same available rows. "
        "A missing matched value means insufficient same-history residuals; "
        "it is not zero coverage.",
        "",
        "## Candidate's per-fold calibration support",
        "",
        *markdown_table(support_summary),
        "",
        "Regenerated full JSON contains all six models, 50/80/90% intervals, season splits, "
        "probability "
        "scores and reliability bins by history, source hashes, and the full policy.",
        "",
        "## Limits",
        "",
        *[f"- {limit}" for limit in report["limitations"]],
        "",
        "## Provenance",
        "",
        f"Policy SHA-256: `{report['policy_sha256']}`.",
        "",
        f"Research manifest SHA-256: `{report['provenance']['research_manifest_sha256']}`.",
        "",
        "Derived from [nflverse](https://github.com/nflverse/nflverse-data) data, distributed "
        "under [CC BY 4.0](https://github.com/nflverse/nflverse-data/blob/main/LICENSE.md).",
        "",
    ]
    (report_dir / "calibration_audit.md").write_text("\n".join(lines), encoding="utf-8")
