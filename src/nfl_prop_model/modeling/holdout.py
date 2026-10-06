"""One recorded, fixed-artifact 2025 diagnostic, with explicit frozen-code access gates."""

import platform
from collections.abc import Iterator
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from xgboost import XGBRegressor

from nfl_prop_model.data.quarterback_games import build_target_table
from nfl_prop_model.data.schemas import DataQualityError, require_keys, validate_sources
from nfl_prop_model.data.storage import load_frame, read_json, sha256_file, store_frame, write_json
from nfl_prop_model.features.quarterback import FEATURE_COLUMNS, add_lagged_features
from nfl_prop_model.modeling.candidate import FEATURES
from nfl_prop_model.modeling.config import COVERAGES, DIAGNOSTIC_THRESHOLDS
from nfl_prop_model.modeling.contracts import validate_model_values
from nfl_prop_model.modeling.freeze import verify_frozen
from nfl_prop_model.modeling.policy import MINIMUM_HISTORY_GAMES
from nfl_prop_model.modeling.reporting import markdown_table
from nfl_prop_model.modeling.uncertainty import ResidualCalibration


def add_holdout_schedule_features(table: pl.DataFrame, schedules: pl.DataFrame) -> pl.DataFrame:
    """Rest resets by season and uses all completed regular-season schedule games."""
    games = schedules.filter(
        (pl.col("game_type") == "REG")
        & pl.col("home_score").is_not_null()
        & pl.col("away_score").is_not_null()
    ).with_columns(
        pl.concat_str(["gameday", "gametime"], separator=" ")
        .str.to_datetime("%Y-%m-%d %H:%M", strict=False)
        .dt.replace_time_zone("America/New_York")
        .dt.convert_time_zone("UTC")
        .alias("kickoff_utc")
    )
    if games.filter(
        pl.col("kickoff_utc").is_null()
        | pl.col("location").is_null()
        | ~pl.col("location").is_in(["Home", "Neutral"])
    ).height:
        raise DataQualityError("Holdout schedule contains invalid time or location")
    sides = pl.concat(
        [
            games.select(
                "game_id",
                "season",
                "kickoff_utc",
                pl.col(f"{side}_team").alias("team"),
                pl.col(f"{other}_team").alias("opponent_team"),
                pl.lit(side == "home").alias("_home"),
                (pl.col("location") == "Neutral").alias("is_neutral_site"),
            )
            for side, other in (("home", "away"), ("away", "home"))
        ]
    ).sort("team", "season", "kickoff_utc", "game_id")
    require_keys(sides, ["team", "kickoff_utc"], "holdout schedule team kickoffs")
    sides = sides.with_columns(
        (
            (
                pl.col("kickoff_utc") - pl.col("kickoff_utc").shift(1).over("team", "season")
            ).dt.total_seconds()
            / 86400
        ).alias("team_rest_days")
    )
    own = sides.select("game_id", "team", "_home", "is_neutral_site", "team_rest_days")
    opponent = sides.select(
        "game_id",
        pl.col("team").alias("opponent_team"),
        pl.col("team_rest_days").alias("opponent_rest_days"),
    )
    result = table.join(own, on=["game_id", "team"], how="left", validate="m:1").join(
        opponent, on=["game_id", "opponent_team"], how="left", validate="m:1"
    )
    if (
        result.filter(
            pl.col("_home").is_null()
            | (pl.col("_home") != pl.col("is_designated_home"))
            | pl.col("is_neutral_site").is_null()
        ).height
        or result.height != table.height
    ):
        raise DataQualityError("Holdout schedule coverage or designated-home identity differs")
    return result.drop("_home").sort("kickoff_utc", "game_id", "player_id")


def build_holdout_features(
    development: pl.DataFrame, stats: pl.DataFrame, schedules: pl.DataFrame
) -> tuple[pl.DataFrame, dict[str, Any]]:
    if set(stats["season"].unique()) != {2025} or set(schedules["season"].unique()) != {2025}:
        raise DataQualityError("Reserved diagnostic sources must contain exactly season 2025")
    targets, exclusions = build_target_table(stats, schedules)
    # Rebuild lags across the original development history and earlier holdout appearances.
    # Current outcomes remain targets; shift-before-rolling never exposes them to their forecast.
    combined = pl.concat([development.select(targets.columns), targets], how="vertical_relaxed")
    lagged = add_lagged_features(combined)
    restored = lagged.filter(pl.col("season") != 2025).select(
        "game_id", "player_id", *FEATURE_COLUMNS, "history_available_at_utc"
    )
    original = development.sort("kickoff_utc", "game_id", "player_id").select(restored.columns)
    if not restored.equals(original, null_equal=True):
        raise DataQualityError("Appending holdout history changed the development features")
    holdout = add_holdout_schedule_features(lagged.filter(pl.col("season") == 2025), schedules)
    validate_model_values(holdout)
    if holdout.filter(pl.col("prediction_time_utc") <= datetime(2025, 2, 1, tzinfo=UTC)).height:
        raise DataQualityError("Holdout prediction times must follow the development cutoff")
    rest = ["team_rest_days", "opponent_rest_days"]
    if holdout.filter(
        pl.any_horizontal((~pl.col(c).is_finite() | (pl.col(c) < 0)).fill_null(False) for c in rest)
    ).height:
        raise DataQualityError("Holdout rest days must be finite nonnegative values or null")
    return holdout, exclusions


def score_holdout(
    features: pl.DataFrame, estimator: XGBRegressor, calibration: ResidualCalibration
) -> dict[str, pl.DataFrame]:
    if set(features["season"].unique()) != {2025}:
        raise DataQualityError("The reserved diagnostic scores only 2025")
    validate_model_values(features)
    prediction = np.asarray(
        estimator.predict(np.asarray(features.select(FEATURES).to_numpy(), dtype=np.float64)),
        dtype=np.float64,
    )
    if not np.isfinite(prediction).all():
        raise DataQualityError("Nonfinite reserved diagnostic predictions")
    points = features.select(
        "game_id",
        "player_id",
        "season",
        "week",
        "kickoff_utc",
        "prediction_time_utc",
        "prior_games_in_sample",
        pl.col("target_passing_yards").alias("actual"),
    ).with_columns(
        pl.Series("prediction", prediction),
        pl.lit("xgb_schedule").alias("model"),
        (pl.col("prior_games_in_sample") >= MINIMUM_HISTORY_GAMES).alias("history_eligible"),
        pl.when(pl.col("prior_games_in_sample") == 0)
        .then(pl.lit("0: no sample history"))
        .when(pl.col("prior_games_in_sample") < 5)
        .then(pl.lit("1-4 prior games"))
        .otherwise(pl.lit("5+ prior games"))
        .alias("history_bucket"),
    )
    intervals = pl.concat(
        [
            points.with_columns(
                pl.lit(c).alias("coverage"),
                (pl.col("prediction") - calibration.radius(c)).alias("lower"),
                (pl.col("prediction") + calibration.radius(c)).alias("upper"),
            )
            for c in COVERAGES
        ]
    )
    probabilities = pl.concat(
        [
            points.with_columns(
                pl.lit(line).alias("line"),
                pl.Series(
                    "probability_over",
                    [calibration.probability_over(float(p), line) for p in prediction],
                ),
                (pl.col("actual") > line).cast(pl.Int64).alias("outcome_over"),
            )
            for line in DIAGNOSTIC_THRESHOLDS
        ]
    )
    return {"predictions": points, "intervals": intervals, "probabilities": probabilities}


def _groups(frame: pl.DataFrame) -> Iterator[pl.DataFrame]:
    for population in ("all recorded appearances", "5+ prior available games"):
        selected = frame if population.startswith("all") else frame.filter("history_eligible")
        if selected.is_empty():
            continue
        for scope, group in (
            ("overall", pl.lit("all")),
            ("history", pl.col("history_bucket")),
            ("week", pl.concat_str("season", "week", separator="-W")),
        ):
            yield selected.with_columns(
                pl.lit(population).alias("population"),
                pl.lit(scope).alias("scope"),
                group.alias("group"),
            )


def summarize_holdout(frames: dict[str, pl.DataFrame]) -> dict[str, list[dict[str, Any]]]:
    counts = [
        pl.len().alias("n"),
        pl.col("player_id").n_unique().alias("distinct_players"),
        pl.col("game_id").n_unique().alias("distinct_games"),
        pl.col("week").n_unique().alias("distinct_weeks"),
    ]
    keys = ["population", "scope", "group"]
    points, intervals, probabilities, reliability = [], [], [], []
    for frame in _groups(frames["predictions"]):
        points.append(
            frame.group_by(keys).agg(
                *counts,
                (pl.col("prediction") - pl.col("actual")).abs().mean().alias("mae"),
                ((pl.col("prediction") - pl.col("actual")) ** 2).mean().sqrt().alias("rmse"),
                (pl.col("prediction") - pl.col("actual")).mean().alias("bias"),
            )
        )
    for frame in _groups(frames["intervals"]):
        intervals.append(
            frame.group_by(*keys, "coverage").agg(
                *counts,
                ((pl.col("actual") >= pl.col("lower")) & (pl.col("actual") <= pl.col("upper")))
                .mean()
                .alias("observed_coverage"),
                (pl.col("upper") - pl.col("lower")).mean().alias("mean_width"),
            )
        )
    for frame in _groups(frames["probabilities"]):
        scored = frame.with_columns(
            ((pl.col("probability_over") - pl.col("outcome_over")) ** 2).alias("brier"),
            (
                -(
                    pl.col("outcome_over") * pl.col("probability_over").log()
                    + (1 - pl.col("outcome_over")) * (1 - pl.col("probability_over")).log()
                )
            ).alias("log_loss"),
            (pl.col("probability_over") * 10).floor().clip(0, 9).cast(pl.Int64).alias("bin"),
        )
        probabilities.append(
            scored.group_by(*keys, "line").agg(*counts, pl.col("brier", "log_loss").mean())
        )
        if frame["scope"][0] == "overall":
            reliability.append(
                scored.group_by("population", "line", "bin").agg(
                    *counts,
                    pl.col("probability_over").mean().alias("mean_predicted_probability"),
                    pl.col("outcome_over").mean().alias("observed_over_rate"),
                )
            )
    return {
        "point_metrics": pl.concat(points).sort(keys).to_dicts(),
        "interval_metrics": pl.concat(intervals).sort(*keys, "coverage").to_dicts(),
        "probability_metrics": pl.concat(probabilities).sort(*keys, "line").to_dicts(),
        "reliability": pl.concat(reliability).sort("population", "line", "bin").to_dicts(),
    }


def _fetch_inputs(directory: Path) -> tuple[pl.DataFrame, pl.DataFrame, dict[str, Any]]:
    path = directory / "manifest.json"
    if path.exists():
        manifest = read_json(path)
        return (
            load_frame(directory, manifest["datasets"]["player_stats"]),
            load_frame(directory, manifest["datasets"]["schedules"]),
            manifest,
        )
    import nflreadpy as nfl
    from nflreadpy.config import update_config

    update_config(cache_mode="memory")
    nfl.clear_cache()
    stats = nfl.load_player_stats([2025], summary_level="week")
    schedules = nfl.load_schedules([2025])
    validate_sources(stats, schedules)
    if set(stats["season"].unique()) != {2025} or set(schedules["season"].unique()) != {2025}:
        raise DataQualityError("Holdout download must contain exactly season 2025")
    manifest = {
        "season": 2025,
        "retrieved_at_utc": datetime.now(UTC).isoformat(),
        "packages": {name: version(name) for name in ("nflreadpy", "polars", "tzdata")},
        "datasets": {
            "player_stats": {
                **store_frame(directory, "player_stats", stats),
                "source": "https://github.com/nflverse/nflverse-data/releases/tag/stats_player",
                "license": "CC-BY-4.0",
            },
            "schedules": {
                **store_frame(directory, "schedules", schedules),
                "source": "https://github.com/nflverse/nflverse-data/releases/tag/schedules",
                "license": "CC-BY-4.0",
            },
        },
    }
    write_json(path, manifest)
    return stats, schedules, manifest


def load_completed_holdout(root: Path, registry: dict[str, Any]) -> dict[str, Any]:
    run = root / f"run-{registry['freeze_sha256']}"
    path = run / "manifest.json"
    if sha256_file(path) != registry["report_sha256"]:
        raise DataQualityError("Completed holdout report checksum mismatch")
    report = read_json(path)
    if report["freeze_sha256"] != registry["freeze_sha256"]:
        raise DataQualityError("Completed holdout report belongs to another freeze")
    for artifact in report["artifacts"].values():
        load_frame(run, artifact)
    return report


def evaluate_holdout(
    frozen_dir: Path, data_dir: Path, *, resume: bool = False
) -> tuple[dict[str, Any], bool]:
    frozen = verify_frozen(frozen_dir)
    root = data_dir / "holdout" / "2025"
    registry_path = root / "access.json"
    root.mkdir(parents=True, exist_ok=True)
    lock = root / "run.lock"
    try:
        handle = lock.open("x", encoding="utf-8")
    except FileExistsError as error:
        raise DataQualityError(
            "Holdout writer is already active; inspect the run lock before retrying"
        ) from error
    with handle:
        handle.write(f"Started {datetime.now(UTC).isoformat()}\n")
    try:
        if registry_path.exists():
            registry = read_json(registry_path)
            if registry["freeze_sha256"] != frozen["freeze_sha256"]:
                raise DataQualityError(
                    "2025 has already been accessed under another freeze; no retuning/retest"
                )
            if registry["status"] == "complete":
                return load_completed_holdout(root, registry), True
            if not resume:
                raise DataQualityError(
                    "An earlier access attempt exists; inspect its audit trail and use --resume"
                )
        else:
            if resume:
                raise DataQualityError("There is no earlier holdout attempt to resume")
            registry = {
                "season": 2025,
                "freeze_sha256": frozen["freeze_sha256"],
                "frozen_at_utc": frozen["frozen_at_utc"],
                "first_access_started_at_utc": datetime.now(UTC).isoformat(),
                "status": "pending",
                "attempts": [],
            }
        # This check runs BEFORE writing the access record, opening cached targets or downloading.
        verify_frozen(frozen_dir, require_current_code=True)
        registry["status"] = "access_started"
        registry["attempts"].append(
            {"started_at_utc": datetime.now(UTC).isoformat(), "status": "started"}
        )
        write_json(registry_path, registry)
        try:
            stats, schedules, inputs = _fetch_inputs(root / "raw")
            prepared = frozen["prepared"]
            development = load_frame(frozen_dir, prepared["artifacts"]["features"])
            features, exclusions = build_holdout_features(development, stats, schedules)
            estimator = XGBRegressor(**prepared["policy"]["parameters"])
            estimator.load_model(frozen_dir / prepared["artifacts"]["model"]["file"])
            pool = load_frame(frozen_dir, prepared["artifacts"]["calibration"])
            calibration = ResidualCalibration(pool["residual"].to_numpy())
            frames = score_holdout(features, estimator, calibration)
            summaries = summarize_holdout(frames)
            run = root / f"run-{frozen['freeze_sha256']}"
            artifacts = {
                name: store_frame(run, name, frame)
                for name, frame in {"features": features, **frames}.items()
            }
            verify_frozen(frozen_dir, require_current_code=True)
            report = {
                "format_version": 1,
                "status": "completed_reserved_diagnostic",
                "generated_at_utc": datetime.now(UTC).isoformat(),
                "freeze_sha256": frozen["freeze_sha256"],
                "frozen_at_utc": frozen["frozen_at_utc"],
                "first_access_started_at_utc": registry["first_access_started_at_utc"],
                "policy_sha256": frozen["policy_sha256"],
                "protocol_sha256": frozen["protocol_sha256"],
                "protocol": frozen["protocol"],
                "evaluation_executable_sha256": frozen["executable_sha256"],
                "model_sha256": prepared["artifacts"]["model"]["sha256"],
                "calibration_sha256": prepared["artifacts"]["calibration"]["sha256"],
                "calibration_rows": pool.height,
                "inputs": inputs,
                "artifacts": artifacts,
                "exclusions": exclusions,
                "production_enabled": False,
                "environment": {
                    "python": platform.python_version(),
                    **{name: version(name) for name in ("polars", "numpy", "xgboost-cpu")},
                },
                "counts": {
                    "qb_games": features.height,
                    "players": features["player_id"].n_unique(),
                    "games": features["game_id"].n_unique(),
                    "weeks": features["week"].n_unique(),
                    "history_eligible_rows": frames["predictions"]
                    .filter("history_eligible")
                    .height,
                    "scored_rows": frames["predictions"].height,
                    "dropped_evaluation_rows": 0,
                },
                **summaries,
            }
            write_json(run / "manifest.json", report)
            registry["status"] = "complete"
            registry["report_sha256"] = sha256_file(run / "manifest.json")
            registry["attempts"][-1].update(
                {"status": "complete", "finished_at_utc": datetime.now(UTC).isoformat()}
            )
            write_json(registry_path, registry)
            return report, False
        except Exception as error:
            registry["status"] = "failed"
            registry["attempts"][-1].update(
                {
                    "status": "failed",
                    "finished_at_utc": datetime.now(UTC).isoformat(),
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            )
            write_json(registry_path, registry)
            raise
    finally:
        lock.unlink(missing_ok=True)


def write_holdout_report(report_dir: Path, report: dict[str, Any]) -> None:
    write_json(report_dir / "holdout.json", report)
    lines = [
        "# Frozen 2025 QB passing-yards holdout diagnostic",
        "",
        f"Completed: {report['generated_at_utc']}",
        "",
        f"Frozen before access: {report['frozen_at_utc']}; "
        f"first access began: {report['first_access_started_at_utc']}.",
        "",
        f"Freeze SHA-256: `{report['freeze_sha256']}`",
        "",
        "One fixed schedule XGBoost estimator and its development residual pool are used for "
        "every 2025 appearance. No refitting, recalibration, tuning or row filtering uses "
        "holdout outcomes. Earlier available outcomes supply strictly lagged inputs only.",
        "",
        "The cohort is conditioned on recorded participation and includes backups, early exits, "
        "zero attempts and sparse history. The five-game subgroup is a history diagnostic. "
        "This does not validate prospective starters, playing time or betting EV. "
        "Production stays disabled; 2025 is now an accessed holdout and cannot be reused "
        "as untouched validation for a revised model.",
        "",
        "## Counts",
        "",
        *markdown_table(pl.DataFrame([report["counts"]])),
        "",
    ]
    for key, title in (
        ("point_metrics", "Point errors (yards)"),
        ("interval_metrics", "Interval coverage and width"),
        ("probability_metrics", "Fixed-threshold probability scores"),
    ):
        frame = (
            pl.DataFrame(report[key]).filter(pl.col("scope") == "overall").drop("scope", "group")
        )
        lines.extend([f"## {title}", "", *markdown_table(frame), ""])
    lines.extend(
        [
            "## Exclusions and provenance",
            "",
            "Complete week/history/reliability diagnostics, exclusions, source URLs/licenses, "
            "retrieval times, input/output hashes, frozen protocol and environment "
            "are in holdout.json. Source retrieval is retrospective; kickoff plus 24 hours "
            "remains an availability assumption. The static model protocol differs from "
            "weekly-refit development research; do not pool their metrics.",
            "",
            f"Model SHA-256: `{report['model_sha256']}`",
            "",
            f"Calibration SHA-256: `{report['calibration_sha256']}`",
            "",
            "Repeated players/shared games/time dependence limit independent-sample claims. "
            "Coverage is empirical, threshold probabilities are diagnostics rather than historical "
            "sportsbook lines, and no ROI or profitability claim is established.",
            "",
            "A completed invocation reuses the saved checksummed report. An interrupted or failed "
            "attempt requires explicit --resume under the same freeze and retains its audit trail.",
            "",
        ]
    )
    (report_dir / "holdout.md").write_text("\n".join(lines), encoding="utf-8")
