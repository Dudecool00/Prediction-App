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
    calibration_audit = commands.add_parser(
        "calibration-audit", help="Audit saved development calibration by pregame QB history"
    )
    calibration_audit.add_argument("--data-dir", type=Path, default=Path("data"))
    calibration_audit.add_argument(
        "--report-dir", type=Path, default=Path("reports/local/calibration")
    )
    candidate = commands.add_parser(
        "prepare-candidate", help="Save a reproducible development candidate; 2025 stays closed"
    )
    candidate.add_argument("--data-dir", type=Path, default=Path("data"))
    candidate.add_argument("--output-dir", type=Path, default=Path("models/candidates"))
    candidate.add_argument("--report-dir", type=Path, default=Path("reports/local/candidate"))
    verification = commands.add_parser("verify-candidate", help="Verify a saved candidate offline")
    verification.add_argument("--bundle", type=Path, required=True)
    freeze = commands.add_parser(
        "freeze-candidate", help="Freeze exact artifacts/code for one 2025 diagnostic"
    )
    freeze.add_argument("--bundle", type=Path, required=True)
    freeze.add_argument("--output-dir", type=Path, default=Path("models/frozen"))
    holdout = commands.add_parser(
        "evaluate-holdout", help="Evaluate 2025 once under an exact frozen candidate"
    )
    holdout.add_argument("--frozen", type=Path, required=True)
    holdout.add_argument("--data-dir", type=Path, default=Path("data"))
    holdout.add_argument("--report-dir", type=Path, default=Path("reports/local/holdout"))
    holdout.add_argument(
        "--resume",
        action="store_true",
        help="Resume an audited failure with the same frozen code/model",
    )
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
    feature_command = commands.add_parser(
        "current-features", help="Audit observed 2026 features; no forecasts or model fitting"
    )
    feature_command.add_argument("--data-dir", type=Path, default=Path("data"))
    feature_command.add_argument(
        "--report-dir", type=Path, default=Path("reports/local/current_features")
    )
    feature_command.add_argument("--days", type=int, default=14)
    feature_command.add_argument(
        "--refresh", action="store_true", help="Refresh 2026 stats/schedules/charts"
    )
    status_capture = commands.add_parser(
        "capture-status", help="Archive an official article for a pregame status annotation"
    )
    status_capture.add_argument("--data-dir", type=Path, default=Path("data"))
    status_capture.add_argument("--game-id", required=True)
    status_capture.add_argument("--espn-id", required=True)
    status_capture.add_argument("--kind", choices=("starter", "availability"), required=True)
    status_capture.add_argument("--claim", required=True)
    status_capture.add_argument("--source-url", required=True)
    status_capture.add_argument("--quote", required=True)
    status_capture.add_argument("--context-quote", action="append", required=True)
    enrollment = commands.add_parser(
        "register-participation", help="Enroll every snapshot candidate before game cutoffs"
    )
    enrollment.add_argument("--data-dir", type=Path, default=Path("data"))
    enrollment.add_argument("--snapshot", type=Path, required=True)
    participation = commands.add_parser(
        "participation-audit", help="Audit an immutable candidate registry offline"
    )
    participation.add_argument("--data-dir", type=Path, default=Path("data"))
    participation.add_argument("--registry", type=Path, required=True)
    participation.add_argument(
        "--report-dir", type=Path, default=Path("reports/local/participation")
    )
    reconciliation = commands.add_parser(
        "participation-reconcile",
        help="Reconcile enrolled candidates with completed-game ESPN rosters",
    )
    reconciliation.add_argument("--data-dir", type=Path, default=Path("data"))
    reconciliation.add_argument("--registry", type=Path, required=True)
    reconciliation.add_argument(
        "--report-dir", type=Path, default=Path("reports/local/participation_reconciliation")
    )
    reconciliation.add_argument(
        "--refresh",
        action="store_true",
        help="Refresh 2026 stats/schedules and eligible postgame roster evidence",
    )
    starters = commands.add_parser(
        "starter-audit",
        help="Reconcile flagged 2022–2024 starter labels against ESPN event rosters",
    )
    starters.add_argument("--data-dir", type=Path, default=Path("data"))
    starters.add_argument("--report-dir", type=Path, default=Path("reports/local/starters"))
    starters.add_argument(
        "--refresh", action="store_true", help="Refresh historical starter evidence"
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
        if args.command == "participation-reconcile":
            from nfl_prop_model.data.prospective import fetch_current_stats
            from nfl_prop_model.data.roster_reconciliation import (
                fetch_roster_evidence,
                reconcile_rosters,
                write_reconciliation_report,
            )
            from nfl_prop_model.data.upcoming import fetch_upcoming

            if args.refresh:
                fetch_upcoming(args.data_dir, refresh=True)
                fetch_current_stats(args.data_dir, refresh=True)
                fetch_roster_evidence(args.data_dir, args.registry)
            report = reconcile_rosters(args.data_dir, args.registry)
            write_reconciliation_report(args.report_dir, report)
            print(
                f"{report['corroborated_rows']} of {report['candidate_count']} enrolled "
                f"candidates corroborated: {report['roster_counts']}"
            )
            print("No forecasts enabled; incomplete or conflicting evidence stays unresolved.")
            print(f"Report: {args.report_dir / 'reconciliation.md'}")
            return 0
        if args.command == "capture-status":
            from nfl_prop_model.data.status_evidence import capture_status_evidence

            saved = capture_status_evidence(
                args.data_dir,
                args.game_id,
                args.espn_id,
                kind=args.kind,
                claim=args.claim,
                source_url=args.source_url,
                quote=args.quote,
                context_quotes=args.context_quote,
            )
            print(f"Archived primary status annotation: {saved}")
            print(
                "Article bytes/excerpts/date checked; claim adjudication remains open. "
                "No forecasts enabled."
            )
            return 0
        if args.command == "register-participation":
            from nfl_prop_model.data.participation import register_candidates

            saved = register_candidates(args.data_dir, args.snapshot)
            print(f"Registered all snapshot candidates: {saved}")
            print("Outcomes remain pending; no forecasts enabled.")
            return 0
        if args.command == "participation-audit":
            from nfl_prop_model.data.participation import (
                audit_participation,
                export_enrollment,
                write_participation_report,
            )

            report = audit_participation(args.data_dir, args.registry)
            write_participation_report(args.report_dir, report)
            export_enrollment(args.registry, args.report_dir / "enrollment.json")
            print(f"Audited {report['candidate_count']} enrolled candidates: {report['counts']}")
            print(f"Report: {args.report_dir / 'participation.md'}")
            return 0
        if args.command == "current-features":
            from nfl_prop_model.data.prospective import (
                fetch_current_stats,
                load_feature_report,
                save_feature_snapshot,
                write_feature_report,
            )
            from nfl_prop_model.data.upcoming import fetch_upcoming

            if not 1 <= args.days <= 28:
                raise ValueError("Upcoming horizon must be 1–28 days")
            fetch_upcoming(args.data_dir, refresh=args.refresh)
            fetch_current_stats(args.data_dir, refresh=args.refresh)
            report = load_feature_report(args.data_dir, days=args.days)
            saved = save_feature_snapshot(args.data_dir, report)
            write_feature_report(args.report_dir, report)
            print(
                f"{report['counts']['features_ready_rows']} candidates pass feature checks. "
                "No forecasts generated."
            )
            print(f"Snapshot: {saved}")
            print(f"Report: {args.report_dir / 'current_features.md'}")
            return 0
        if args.command == "freeze-candidate":
            from nfl_prop_model.modeling.freeze import freeze_candidate

            frozen = freeze_candidate(args.bundle, args.output_dir)
            print(f"Frozen diagnostic candidate: {frozen}")
            print("No holdout outcomes accessed by freezing; production stays disabled.")
            return 0
        if args.command == "evaluate-holdout":
            from nfl_prop_model.modeling.holdout import evaluate_holdout, write_holdout_report

            report, reused = evaluate_holdout(args.frozen, args.data_dir, resume=args.resume)
            write_holdout_report(args.report_dir, report)
            print(
                f"{'Reused saved' if reused else 'Completed'} 2025 diagnostic: "
                f"{report['counts']['qb_games']} QB-games."
            )
            print(
                "2025 is now accessed; no retuning/retest as an untouched holdout. "
                "Production stays disabled."
            )
            print(f"Report: {args.report_dir / 'holdout.md'}")
            return 0
        if args.command == "prepare-candidate":
            from nfl_prop_model.modeling.candidate import write_candidate

            bundle = write_candidate(args.data_dir, args.output_dir, args.report_dir)
            print(f"Prepared and verified candidate: {bundle}")
            print("Candidate remains unfrozen; 2025 access is closed; production is disabled.")
            print(f"Report: {args.report_dir / 'candidate.md'}")
            return 0
        if args.command == "verify-candidate":
            from nfl_prop_model.modeling.candidate import verify_candidate

            report = verify_candidate(args.bundle)
            print(f"Verified {report['counts']['calibration_rows']} saved calibration predictions.")
            print("Candidate remains unfrozen; 2025 access is closed; production is disabled.")
            return 0
        if args.command == "calibration-audit":
            from nfl_prop_model.modeling.calibration_audit import (
                load_calibration_audit,
                write_calibration_audit,
            )

            report = load_calibration_audit(args.data_dir)
            write_calibration_audit(args.report_dir, report)
            print(
                f"Audited {report['counts']['evaluated_qb_games']} development QB-games. "
                "Candidate policy remains unfrozen; 2025 is closed."
            )
            print(f"Report: {args.report_dir / 'calibration_audit.md'}")
            return 0
        if args.command == "starter-audit":
            from nfl_prop_model.data.starter_audit import (
                fetch_starter_evidence,
                load_starter_audit,
                write_starter_audit,
            )

            fetch_starter_evidence(args.data_dir, refresh=args.refresh)
            report = load_starter_audit(args.data_dir)
            write_starter_audit(args.report_dir, report)
            print(
                f"{report['counts']['corrected_schedule_labels']} starter labels reconciled; "
                f"{report['counts']['needs_review']} need review. Historical data unchanged."
            )
            print(f"Report: {args.report_dir / 'starter_audit.md'}")
            return 0
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
