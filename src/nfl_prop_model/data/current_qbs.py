"""Compare current ESPN-derived depth charts with history without filtering history."""

import argparse
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

import polars as pl

from nfl_prop_model.data.schemas import DataQualityError, require_keys
from nfl_prop_model.data.storage import load_frame, read_json, store_frame, write_json

NFL_TEAMS = set(
    "ARI ATL BAL BUF CAR CHI CIN CLE DAL DEN DET GB HOU IND JAX KC LA LAC LV MIA MIN "
    "NE NO NYG NYJ PHI PIT SEA SF TB TEN WAS".split()
)
SOURCE = "https://github.com/nflverse/nflverse-data/releases/tag/depth_charts"
UPDATER = "https://github.com/nflverse/nflverse-rosters/blob/main/exec/update-depth-charts.R"


def latest_quarterbacks(
    depth: pl.DataFrame, as_of: datetime, *, expected_teams: set[str] = NFL_TEAMS
) -> pl.DataFrame:
    """Select a team's latest recorded chart before selecting its QB positions."""
    if as_of.tzinfo is None:
        raise ValueError("as_of must include a time zone")
    columns = {"dt", "team", "player_name", "espn_id", "gsis_id", "pos_abb", "pos_rank"}
    if missing := columns - set(depth.columns):
        raise DataQualityError(f"Depth chart missing columns: {sorted(missing)}")
    if depth.select(pl.any_horizontal(pl.col("dt", "team").is_null()).any()).item():
        raise DataQualityError("Depth charts have missing team or snapshot timestamps")
    parsed = depth.with_columns(pl.col("dt").str.to_datetime(time_zone="UTC").alias("chart_time"))
    available = parsed.filter(pl.col("chart_time") <= as_of)
    latest = available.filter(pl.col("chart_time") == pl.col("chart_time").max().over("team"))
    if set(latest["team"].unique().to_list()) != expected_teams:
        raise DataQualityError("Latest depth-chart coverage does not match the expected NFL teams")
    qbs = latest.filter(pl.col("pos_abb") == "QB").select(
        "team",
        "player_name",
        "espn_id",
        "gsis_id",
        "pos_rank",
        pl.col("dt").alias("source_snapshot_utc"),
    )
    if set(qbs["team"].unique().to_list()) != expected_teams:
        raise DataQualityError("A latest team chart has no QB rows; do not reuse an older QB list")
    require_keys(qbs, ["espn_id"], "current QB ESPN identities")
    require_keys(qbs.filter(pl.col("gsis_id").is_not_null()), ["gsis_id"], "current QB GSIS IDs")
    if qbs["pos_rank"].null_count() or qbs.filter(pl.col("pos_rank") < 1).height:
        raise DataQualityError("Missing or invalid QB depth rank")
    return qbs.sort("team", "pos_rank")


def cross_reference(
    history: pl.DataFrame, current: pl.DataFrame
) -> tuple[pl.DataFrame, pl.DataFrame, dict[str, Any]]:
    """Join by stable GSIS ID. Absence is never converted into a retirement label."""
    if history.is_empty():
        raise DataQualityError("Cross-reference requires a nonempty historical sample")
    players = (
        history.sort("kickoff_utc")
        .group_by("player_id")
        .agg(pl.col("player_display_name").last(), pl.len().alias("historical_game_rows"))
    )
    matched = (
        players.join(
            current.filter(pl.col("gsis_id").is_not_null()).select(
                "gsis_id", "team", "pos_rank", "source_snapshot_utc"
            ),
            left_on="player_id",
            right_on="gsis_id",
            how="left",
            validate="1:1",
        )
        .with_columns(
            pl.when(pl.col("team").is_not_null())
            .then(pl.lit("listed_current_qb"))
            .otherwise(pl.lit("not_matched_current_qb"))
            .alias("chart_match_status")
        )
        .sort("player_display_name")
    )
    candidates = (
        current.join(
            players.select("player_id", "historical_game_rows"),
            left_on="gsis_id",
            right_on="player_id",
            how="left",
            validate="m:1",
        )
        .with_columns(
            pl.when(pl.col("gsis_id").is_null())
            .then(pl.lit("missing_gsis_mapping"))
            .when(pl.col("historical_game_rows").is_null())
            .then(pl.lit("no_2022_2024_sample_history"))
            .otherwise(pl.lit("matched_history"))
            .alias("history_match_status")
        )
        .sort("team", "pos_rank")
    )
    absent = matched.filter(pl.col("chart_match_status") == "not_matched_current_qb")
    excluded = absent["historical_game_rows"].sum()
    summary = {
        "historical_qbs": players.height,
        "historical_game_rows": history.height,
        "current_chart_teams": current["team"].n_unique(),
        "current_chart_qbs": current.height,
        "historical_qbs_matched": matched.height - absent.height,
        "historical_qbs_not_matched": absent.height,
        "historical_rows_discarded_if_filtered": excluded,
        "fraction_discarded_if_filtered": excluded / history.height,
        "current_qbs_without_sample_history": candidates.filter(
            pl.col("history_match_status") == "no_2022_2024_sample_history"
        ).height,
        "current_qbs_missing_gsis_mapping": current["gsis_id"].null_count(),
        "historical_rows_deleted": 0,
    }
    return matched, candidates, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--report-dir", type=Path, default=Path("reports/current_qbs_2026"))
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args(argv)
    if args.season != 2026:
        parser.error("This audit compares the 2026 charts with the 2022-2024 development sample")
    raw = args.data_dir / "raw" / "current_depth_2026"
    if args.refresh or not (raw / "manifest.json").exists():
        import nflreadpy as nfl
        from nflreadpy.config import update_config

        update_config(cache_mode="memory")
        nfl.clear_cache()
        depth = nfl.load_depth_charts([2026])
        retrieved = datetime.now(UTC)
        latest_quarterbacks(depth, retrieved)
        if (raw / "manifest.json").exists():
            previous = read_json(raw / "manifest.json")
            previous_time = datetime.fromisoformat(previous["retrieved_at_utc"])
            write_json(
                raw / f"manifest-{previous_time.strftime('%Y%m%dT%H%M%S%fZ')}.json", previous
            )
        manifest = {
            **store_frame(raw, "depth_charts", depth),
            "retrieved_at_utc": retrieved.isoformat(),
            "nflreadpy": version("nflreadpy"),
            "loader": "nflreadpy.load_depth_charts([2026])",
            "source": SOURCE,
            "license": "CC-BY-4.0 (nflverse distribution)",
        }
        write_json(raw / f"manifest-{retrieved.strftime('%Y%m%dT%H%M%S%fZ')}.json", manifest)
        write_json(raw / "manifest.json", manifest)
    manifest = read_json(raw / "manifest.json")
    depth = load_frame(raw, manifest)
    # Anchor the audit to what was available when this cache was retrieved.
    as_of = datetime.fromisoformat(manifest["retrieved_at_utc"])
    current = latest_quarterbacks(depth, as_of)
    directory = args.data_dir / "processed" / "2022_2023_2024"
    historical_manifest = read_json(directory / "manifest.json")
    historical = load_frame(directory, historical_manifest["table"])
    matched, candidates, summary = cross_reference(historical, current)
    args.report_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        args.report_dir / "cross_reference.json",
        {
            "summary": summary,
            "depth_manifest": {**manifest, "source": SOURCE, "upstream_loader": UPDATER},
            "historical_table_sha256": historical_manifest["table"]["sha256"],
            "current_chart_qbs": candidates.to_dicts(),
            "historical_qbs": matched.to_dicts(),
            "policy": "Prospective review only. Not a training feature or retirement registry.",
        },
    )
    lines = [
        "# Current QB cross-reference: 2026",
        "",
        f"Cache retrieved: {as_of.isoformat()}.",
        "",
        f"Source snapshot(s): {', '.join(current['source_snapshot_utc'].unique().sort())}.",
        "",
        "ESPN-derived depth charts distributed by "
        f"[nflverse]({SOURCE}), loaded through nflreadpy. "
        f"[Upstream ESPN loader]({UPDATER}). "
        "[Field definitions](https://nflreadr.nflverse.com/articles/dictionary_depth_charts.html).",
        "",
        "## Summary",
        "",
        "| Measure | Value |",
        "| --- | ---: |",
    ]
    labels = {
        "historical_qbs": "Historical QBs (2022-2024)",
        "historical_game_rows": "Historical QB-game rows",
        "current_chart_teams": "Teams covered",
        "current_chart_qbs": "Current chart QBs",
        "historical_qbs_matched": "Historical QBs matched to current charts",
        "historical_qbs_not_matched": "Historical QBs not matched",
        "historical_rows_discarded_if_filtered": "Historical rows lost if filtered",
        "fraction_discarded_if_filtered": "Share of historical rows lost if filtered",
        "current_qbs_without_sample_history": "Current QBs without sample history",
        "current_qbs_missing_gsis_mapping": "Current QBs without GSIS mapping",
        "historical_rows_deleted": "Historical rows actually deleted",
    }
    for key, value in summary.items():
        display = f"{value:.1%}" if key == "fraction_discarded_if_filtered" else str(value)
        lines.append(f"| {labels[key]} | {display} |")
    lines.extend(
        [
            "",
            "## Recommendation",
            "",
            "Keep historical rows for training. Restrict the upcoming-game player selector using "
            "a fresh depth-chart/roster check, "
            "with confirmed starter and game status checked separately. "
            "Selecting historical rows by 2026 membership would use future survival information to "
            "choose the backtest cohort and discard valid injury/backup/retirement-era games.",
            "",
            "Not matched means not found at QB by GSIS ID in this snapshot; it does not establish "
            "retirement. Free agency, injured reserve, practice squads, chart omissions, position "
            "changes, and ID issues all require separate evidence. ESPN-listed rank 1 is not a "
            "guaranteed starter. No historical rows were deleted and no model was changed.",
            "",
            "Charts are selected per team from the latest recorded snapshot, not by each player's "
            "latest appearance. The latter would mistakenly keep departed players. This report "
            "is frozen at its source timestamp; refresh before using it for a future game.",
            "",
            f"There are {summary['current_qbs_without_sample_history']} current QBs with no "
            f"matching 2022-2024 history and {summary['current_qbs_missing_gsis_mapping']} "
            "without a GSIS mapping. Do not exclude newcomers simply because no history matches.",
            "",
            "Current 2026 charts cannot establish who started a particular 2022-2024 game; "
            "the earlier 37 historical starter discrepancies still need contemporaneous sources.",
            "",
            "## Historical players not matched to a current QB chart",
            "",
            "Retirement status is unverified for this list.",
            "",
            "| Player | GSIS ID | Historical game rows |",
            "| --- | --- | ---: |",
        ]
    )
    for row in matched.filter(pl.col("chart_match_status") == "not_matched_current_qb").to_dicts():
        lines.append(
            f"| {row['player_display_name']} | {row['player_id']} | {row['historical_game_rows']} |"
        )
    lines.extend(
        [
            "",
            "## Current QB chart entries",
            "",
            "| Team | Rank | Player | Historical rows | Match status |",
            "| --- | ---: | --- | ---: | --- |",
        ]
    )
    for row in candidates.to_dicts():
        games = row["historical_game_rows"]
        game_text = games if games is not None else "unmatched"
        lines.append(
            f"| {row['team']} | {row['pos_rank']} | {row['player_name']} | "
            f"{game_text} | {row['history_match_status']} |"
        )
    lines.extend(
        [
            "",
            "Attribution: ESPN and nflverse contributors. "
            "[nflverse distribution license]"
            "(https://github.com/nflverse/nflverse-data/blob/main/LICENSE.md). "
            "This report filters, groups, and joins their data; "
            "no ESPN article text is reproduced.",
            "",
        ]
    )
    (args.report_dir / "cross_reference.md").write_text("\n".join(lines), encoding="utf-8")
    print(summary)
    print(f"Report: {args.report_dir / 'cross_reference.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
