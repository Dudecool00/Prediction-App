"""Human-readable and machine-readable data audits, without model claims."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from nfl_prop_model import __version__
from nfl_prop_model.data.ingest_nfl import Snapshot
from nfl_prop_model.data.storage import write_json
from nfl_prop_model.features.quarterback import FEATURE_COLUMNS


def missingness(frame: pl.DataFrame) -> dict[str, dict[str, int | float]]:
    return {
        column: {
            "null_count": frame[column].null_count(),
            "null_fraction": frame[column].null_count() / frame.height if frame.height else 0.0,
        }
        for column in frame.columns
    }


def write_audit(
    directory: Path,
    snapshot: Snapshot,
    table: pl.DataFrame,
    counts: dict[str, Any],
    processed_metadata: dict[str, Any],
) -> dict[str, Any]:
    by_season = (
        table.group_by("season")
        .agg(
            pl.len().alias("qb_games"),
            pl.col("player_id").n_unique().alias("quarterbacks"),
            (pl.col("prior_games_in_sample") == 0).sum().alias("no_prior_history"),
            (pl.col("prior_games_in_sample") < 5).sum().alias("fewer_than_five_prior_games"),
            (pl.col("observed_attempts") == 0).sum().alias("zero_attempts"),
            (~pl.col("schedule_reported_starter")).sum().alias("recorded_nonstarters"),
        )
        .sort("season")
        .to_dicts()
    )
    last_kickoff = table["kickoff_utc"].max()
    if not isinstance(last_kickoff, datetime):
        raise ValueError("Audit requires a nonempty table with datetime kickoffs.")
    audit: dict[str, Any] = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "pipeline_version": __version__,
        "retrieved_at_utc": snapshot.manifest["retrieved_at_utc"],
        "seasons": snapshot.manifest["seasons"],
        "last_included_kickoff_utc": last_kickoff.isoformat(),
        "counts": counts,
        "by_season": by_season,
        "table_missingness": missingness(table),
        "source_missingness": {
            "player_stats": missingness(snapshot.player_stats),
            "schedules": missingness(snapshot.schedules),
        },
        "feature_columns": list(FEATURE_COLUMNS),
        "raw_manifest": snapshot.manifest,
        "processed_metadata": processed_metadata,
        "model_results": "Not evaluated: Milestones 0 and 1 only.",
    }
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "audit.json", audit)
    lines = [
        "# Milestone 1 data-quality report",
        "",
        f"Seasons: {', '.join(map(str, audit['seasons']))}. Regular-season QB rows only.",
        "",
        f"Source retrieved: {audit['retrieved_at_utc']}. This is a retrieval time, "
        "not the source's last-update time.",
        "",
        f"Pipeline version: {__version__}. Last included kickoff: "
        f"{audit['last_included_kickoff_utc']}.",
        "",
        "## Inclusion and integrity",
        "",
        "| Check | Count |",
        "| --- | ---: |",
    ]
    lines.extend(f"| {key} | {value} |" for key, value in counts.items() if isinstance(value, int))
    lines.extend(
        [
            "",
            "Excluded counts are sequential and sum with included rows to the source player count.",
            "Duplicate QB/schedule keys, unmatched QB game IDs, invalid matchups, "
            "and missing kickoff times "
            "stop the build; they are never silently deduplicated or guessed.",
            "",
            "## Sample by season",
            "",
            "| Season | QB games | QBs | No history | <5 prior games | Zero attempts |"
            " Unlisted as starter |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in by_season:
        lines.append(
            f"| {row['season']} | {row['qb_games']} | {row['quarterbacks']} | "
            f"{row['no_prior_history']} | {row['fewer_than_five_prior_games']} | "
            f"{row['zero_attempts']} | {row['recorded_nonstarters']} |"
        )
    lines.extend(
        ["", "## Feature missingness", "", "| Feature | Nulls | Percent |", "| --- | ---: | ---: |"]
    )
    for column in FEATURE_COLUMNS:
        value = audit["table_missingness"][column]
        lines.append(f"| {column} | {value['null_count']} | {value['null_fraction']:.1%} |")
    missing_starters = counts["scheduled_starters_without_target_rows"]
    lines.extend(
        [
            "",
            "## Scheduled starters without a target row",
            "",
            f"Count: {len(missing_starters)}. Missing statistics are never replaced with zero.",
            "",
        ]
    )
    lines.extend(
        f"- {row['game_id']}: {row['player_id']} ({row['team']})" for row in missing_starters
    )
    lines.extend(
        [
            "",
            "## Interpretation and limitations",
            "",
            "- This cohort consists of QBs with a statistics row, including backups, "
            "zero attempts, "
            "and early exits. Inactive players absent from this source are not reconstructed.",
            "- Schedule-reported starter status is an unverified audit label, "
            "not a timestamped pregame feature or a filter. Mismatches need reconciliation "
            "before starter-specific evaluation; these counts do not prove who actually started.",
            "- Lagged history uses prior regular-season QB records across teams and seasons. "
            "Season means reset each season. Playoff appearances are excluded from history.",
            "- Cold starts stay null; counts measure history inside the downloaded sample, "
            "not career experience. A later training fold must fit any imputation.",
            "- Prediction time is reconstructed as kickoff minus one hour. Prior statistics are "
            "assumed available 24 hours after their kickoff; "
            "this does not recover later corrections.",
            "- Today's historical files contain revisions. Original forecast, injury, starter, "
            "and schedule snapshots are unavailable in this milestone.",
            "- Cancelled games absent from both sources cannot be counted as exclusions. "
            "Games present without final scores are excluded and counted.",
            "- Weather, opponent features, market data, probabilities, EV, and model evaluation "
            "are deferred. No model-versus-baseline or profitability result exists yet.",
            "- 2025 is reserved for later holdout work; stored development tables exclude it. "
            "nflreadpy fetches a complete schedules file internally before filtering seasons.",
            "",
            "Full schemas and source missingness: [source_schema.md](source_schema.md). "
            "Hashes and full audit: [audit.json](audit.json).",
            "",
            "Data attribution: nflverse contributors and Lee Sharpe, "
            "[nflverse-data](https://github.com/nflverse/nflverse-data), "
            "[CC BY 4.0](https://github.com/nflverse/nflverse-data/blob/main/LICENSE.md). "
            "This report summarizes and transforms their data.",
            "",
        ]
    )
    (directory / "audit.md").write_text("\n".join(lines), encoding="utf-8")
    schema_lines = [
        "# Observed source schemas",
        "",
        "Generated from actual cached data. "
        "All source columns are shown; only the documented allowlist is modeled.",
        "",
    ]
    for name, frame in (("player_stats", snapshot.player_stats), ("schedules", snapshot.schedules)):
        schema_lines.extend(
            [
                f"## {name}",
                "",
                f"{frame.height:,} rows; {frame.width} columns.",
                "",
                "| Column | Polars dtype | Nulls |",
                "| --- | --- | ---: |",
            ]
        )
        schema_lines.extend(
            f"| {column} | {dtype} | {frame[column].null_count()} |"
            for column, dtype in frame.schema.items()
        )
        schema_lines.append("")
    (directory / "source_schema.md").write_text("\n".join(schema_lines), encoding="utf-8")
    return audit
