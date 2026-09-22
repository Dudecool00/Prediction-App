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
from nfl_prop_model.markets.odds import ManualMarket, parse_american


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="NFL quarterback forecasts and manual-market research"
    )
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
    evaluation = commands.add_parser("evaluate", help="Weekly baseline evaluation on 2023-2024")
    evaluation.add_argument("--data-dir", type=Path, default=Path("data"))
    evaluation.add_argument("--report-dir", type=Path, default=Path("reports/local/baselines"))
    research = commands.add_parser(
        "research", help="Trees and chronological calibration on 2023-2024"
    )
    research.add_argument("--data-dir", type=Path, default=Path("data"))
    research.add_argument("--report-dir", type=Path, default=Path("reports/local/research"))
    settlement = commands.add_parser(
        "settlement-audit", help="Audit rounded probabilities and integer pushes"
    )
    settlement.add_argument("--data-dir", type=Path, default=Path("data"))
    settlement.add_argument("--report-dir", type=Path, default=Path("reports/local/settlement"))
    upcoming = commands.add_parser("upcoming", help="2026 scheduled games and current QB readiness")
    upcoming.add_argument("--data-dir", type=Path, default=Path("data"))
    upcoming.add_argument("--report-dir", type=Path, default=Path("reports/local/upcoming"))
    upcoming.add_argument("--days", type=int, default=14)
    upcoming.add_argument(
        "--refresh", action="store_true", help="Refresh schedules and depth charts"
    )
    quote_command = commands.add_parser(
        "quote", help="Compare manual odds with a saved historical forecast"
    )
    quote_command.add_argument("--data-dir", type=Path, default=Path("data"))
    quote_command.add_argument("--report-dir", type=Path, default=Path("reports/local/quote"))
    quote_command.add_argument("--player-id", required=True, help="Stable GSIS player ID")
    quote_command.add_argument("--game-id", required=True, help="Saved 2023–2024 game ID")
    quote_command.add_argument(
        "--model",
        required=True,
        choices=(
            "prior_five_mean",
            "season_to_date_mean",
            "ridge",
            "xgb_qb",
            "xgb_schedule",
            "xgb_context",
        ),
    )
    quote_command.add_argument("--line", required=True, type=float)
    quote_command.add_argument("--over-odds", required=True, type=parse_american)
    quote_command.add_argument("--under-odds", required=True, type=parse_american)
    quote_command.add_argument("--sportsbook")
    quote_command.add_argument("--game-spread", type=float, help="Recorded note; not a predictor")
    quote_command.add_argument("--game-total", type=float, help="Recorded note; not a predictor")
    args = parser.parse_args(argv)
    try:
        if args.command == "upcoming":
            from nfl_prop_model.data.upcoming import (
                fetch_upcoming,
                load_upcoming_report,
                write_upcoming_report,
            )

            if not 1 <= args.days <= 28:
                raise ValueError("Upcoming horizon must be 1–28 days")
            fetch_upcoming(args.data_dir, refresh=args.refresh)
            report = load_upcoming_report(args.data_dir, days=args.days)
            write_upcoming_report(args.report_dir, report)
            print(
                f"{report['counts']['upcoming_games']} upcoming games; "
                f"{report['counts']['candidate_rows']} QB candidates. No forecasts generated."
            )
            print(f"Report: {args.report_dir / 'upcoming.md'}")
            return 0
        if args.command == "settlement-audit":
            from nfl_prop_model.markets.audit import write_settlement_audit

            write_settlement_audit(args.data_dir, args.report_dir)
            print(f"Report: {args.report_dir / 'settlement_audit.md'}")
            return 0
        if args.command == "quote":
            from nfl_prop_model.markets.quote import (
                create_quote,
                load_historical_forecast,
                quote_markdown,
                write_quote,
            )

            market = ManualMarket(
                args.line,
                args.over_odds,
                args.under_odds,
                args.sportsbook,
                args.game_spread,
                args.game_total,
            )
            forecast = load_historical_forecast(
                args.data_dir, args.player_id, args.game_id, args.model
            )
            quote = create_quote(forecast, market)
            write_quote(args.report_dir, quote)
            print(quote_markdown(quote))
            print(f"Report: {args.report_dir / 'quote.md'}")
            return 0
        if args.command == "research":
            from nfl_prop_model.modeling.research_report import write_research

            report = write_research(args.data_dir, args.report_dir)
            print(
                f"Evaluated {report['counts']['evaluated_qb_games']:,} QB-games with calibration."
            )
            print(f"Report: {args.report_dir / 'research.md'}")
            return 0
        if args.command == "evaluate":
            from nfl_prop_model.modeling.report import write_evaluation

            report = write_evaluation(args.data_dir, args.report_dir)
            print(
                f"Evaluated {report['counts']['evaluated_qb_games']:,} QB-games across "
                f"{report['counts']['folds']} weekly folds."
            )
            print(f"Report: {args.report_dir / 'evaluation.md'}")
            return 0
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
