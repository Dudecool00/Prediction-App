"""Reproducible commands for source ingestion and offline table construction."""

import argparse
import hashlib
import platform
from importlib.metadata import version
from pathlib import Path

import polars as pl

from nfl_prop_model import __version__
from nfl_prop_model.data.audit import write_audit
from nfl_prop_model.data.ingest_nfl import (
    DEFAULT_SEASONS,
    fetch_snapshot,
    load_snapshot,
    season_key,
)
from nfl_prop_model.data.quarterback_games import build_target_table
from nfl_prop_model.data.storage import store_frame, write_json
from nfl_prop_model.features.quarterback import FEATURE_COLUMNS, add_lagged_features


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NFL quarterback data foundation (Milestones 0–1)")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("fetch", "build"):
        command = commands.add_parser(name)
        command.add_argument("--seasons", type=int, nargs="+", default=list(DEFAULT_SEASONS))
        command.add_argument("--data-dir", type=Path, default=Path("data"))
        if name == "fetch":
            command.add_argument("--refresh", action="store_true", help="Retrieve a new snapshot")
        else:
            command.add_argument("--report-dir", type=Path, default=Path("reports/local"))
    args = parser.parse_args(argv)
    try:
        key = season_key(args.seasons)
        if args.command == "fetch":
            snapshot = fetch_snapshot(args.data_dir, args.seasons, refresh=args.refresh)
            print(
                f"Cached {snapshot.player_stats.height:,} player-stat rows and "
                f"{snapshot.schedules.height:,} schedule rows."
            )
            print(f"Retrieved: {snapshot.manifest['retrieved_at_utc']}")
            print(f"Manifest: {args.data_dir / 'raw' / key / 'manifest.json'}")
        else:
            snapshot = load_snapshot(args.data_dir, args.seasons)
            table, counts = build_target_table(snapshot.player_stats, snapshot.schedules)
            features = add_lagged_features(table)
            directory = args.data_dir / "processed" / key
            metadata = store_frame(directory, "quarterback_games", features)
            source_hash = hashlib.sha256()
            package_root = Path(__file__).parent
            for path in sorted(package_root.rglob("*.py")):
                source_hash.update(path.relative_to(package_root).as_posix().encode())
                source_hash.update(path.read_bytes())
            metadata["pipeline_source_sha256"] = source_hash.hexdigest()
            metadata["build_environment"] = {
                "python": platform.python_version(),
                "polars": version("polars"),
                "tzdata": version("tzdata"),
            }
            write_json(
                directory / "manifest.json",
                {
                    "pipeline_version": __version__,
                    "feature_columns": list(FEATURE_COLUMNS),
                    "raw_manifest": snapshot.manifest,
                    "table": metadata,
                },
            )
            report_dir = args.report_dir / key
            write_audit(report_dir, snapshot, features, counts, metadata)
            print(f"Built {features.height:,} quarterback-game rows.")
            print(f"Table: {directory / metadata['file']}")
            print(f"Report: {report_dir / 'audit.md'}")
    except (OSError, ValueError, ConnectionError, pl.exceptions.PolarsError) as error:
        parser.exit(1, f"Error: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
