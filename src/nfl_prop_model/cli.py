"""Reproducible NFL team data, forecasting and comparison commands."""

import argparse
from pathlib import Path

import polars as pl

from nfl_prop_model import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NFL team EPA scores and margins")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    from nfl_prop_model.data.team_context import DEFAULT_CONTEXT_SEASONS
    from nfl_prop_model.data.team_games import DEFAULT_TEAM_SEASONS
    from nfl_prop_model.features.team_epa import HORIZONS

    for name in (
        "team-fetch",
        "team-build",
        "team-evaluate",
        "team-experiment",
        "team-tune",
        "team-forecast",
        "team-context-fetch",
        "team-context-build",
        "team-context-evaluate",
        "team-playbook-fetch",
        "team-playbook-build",
        "team-playbook-evaluate",
    ):
        team_command = commands.add_parser(name, help="Team EPA final-score and margin research")
        team_command.add_argument("--data-dir", type=Path, default=Path("data"))
        if name in {
            "team-fetch",
            "team-build",
            "team-context-fetch",
            "team-context-build",
            "team-playbook-fetch",
            "team-playbook-build",
        }:
            team_command.add_argument(
                "--seasons",
                type=int,
                nargs="+",
                default=list(
                    DEFAULT_CONTEXT_SEASONS
                    if name.startswith("team-context")
                    else DEFAULT_TEAM_SEASONS
                ),
            )
        if name in {"team-fetch", "team-context-fetch", "team-playbook-fetch"}:
            team_command.add_argument("--refresh", action="store_true")
        if name in {
            "team-evaluate",
            "team-experiment",
            "team-tune",
            "team-context-evaluate",
            "team-playbook-evaluate",
        }:
            team_command.add_argument(
                "--evaluation-seasons", type=int, nargs="+", default=[2022, 2023, 2024]
            )
            team_command.add_argument(
                "--report-dir",
                type=Path,
                default=Path(
                    {
                        "team-evaluate": "reports/local/team_scores",
                        "team-experiment": "reports/local/team_experiments",
                        "team-tune": "reports/local/team_tuning",
                        "team-context-evaluate": "reports/local/team_context",
                        "team-playbook-evaluate": "reports/local/team_playbook",
                    }[name]
                ),
            )
        if name == "team-forecast":
            team_command.add_argument("--horizon", choices=HORIZONS, default="early_week")
            team_command.add_argument("--days", type=int, default=7)
    diagnosis = commands.add_parser(
        "team-diagnose", help="Graphical season score/margin diagnostics"
    )
    diagnosis.add_argument("--season", type=int, default=2025)
    diagnosis.add_argument("--data-dir", type=Path, default=Path("data"))
    diagnosis.add_argument("--report-dir", type=Path)
    refinement = commands.add_parser(
        "team-refine", help="Chronological feature and model refinement"
    )
    refinement.add_argument("--data-dir", type=Path, default=Path("data"))
    refinement.add_argument(
        "--evaluation-seasons", type=int, nargs="+", default=[2022, 2023, 2024, 2025]
    )
    refinement.add_argument(
        "--report-dir", type=Path, default=Path("reports/local/team_refinement")
    )
    for name in ("team-pregame-audit", "team-walkforward", "team-replay-check"):
        command = commands.add_parser(
            name, help="Audited weekly context development and replay gate"
        )
        command.add_argument("--data-dir", type=Path, default=Path("data"))
        command.add_argument(
            "--report-dir", type=Path, default=Path("reports/local/team_walkforward")
        )
    args = parser.parse_args(argv)
    try:
        from nfl_prop_model.data.team_games import build_team_cache, fetch_team_sources
        from nfl_prop_model.modeling.team_scores import save_team_evaluation, save_team_forecast

        if args.command == "team-pregame-audit":
            from nfl_prop_model.data.team_pregame import fetch_development_weather

            manifest = fetch_development_weather(args.data_dir)
            print(f"Pregame source audit cached; {manifest['known_temperatures']} forecast values.")
        elif args.command == "team-walkforward":
            from nfl_prop_model.modeling.team_walkforward import (
                check_2025_replay,
                save_walkforward_development,
            )

            report = save_walkforward_development(args.data_dir, args.report_dir)
            status = check_2025_replay(args.data_dir, args.report_dir)
            print(
                f"Frozen model: {report['frozen_choice']['model']}. "
                f"2025 replay: {status['status']}."
            )
            print(f"Report: {args.report_dir / 'findings.md'}")
        elif args.command == "team-replay-check":
            from nfl_prop_model.modeling.team_walkforward import check_2025_replay

            status = check_2025_replay(args.data_dir, args.report_dir)
            print(f"2025 replay: {status['status']}. Reasons: {status['blockers']}")
        elif args.command == "team-refine":
            from nfl_prop_model.modeling.team_refinement import save_refinements

            report = save_refinements(
                args.data_dir,
                args.report_dir,
                evaluation_seasons=tuple(args.evaluation_seasons),
            )
            print(
                f"Locked {len(report['selections'])} refinement choices. "
                f"Report: {args.report_dir / 'team_refinement.md'}"
            )
        elif args.command == "team-diagnose":
            from nfl_prop_model.modeling.team_diagnostics import save_season_diagnostics

            directory = args.report_dir or Path("reports/local") / f"team_diagnostics_{args.season}"
            report = save_season_diagnostics(args.data_dir, directory, season=args.season)
            print(
                f"Diagnosed {args.season} across {len(report['folds'])} cutoffs. "
                f"Charts: {directory / 'diagnostics.html'}"
            )
        elif args.command == "team-playbook-fetch":
            from nfl_prop_model.data.team_playbook import fetch_playbook_sources

            fetch_playbook_sources(args.data_dir, args.seasons, refresh=args.refresh)
        elif args.command == "team-playbook-build":
            from nfl_prop_model.data.team_playbook import build_playbook_cache

            manifest = build_playbook_cache(args.data_dir, args.seasons)
            print(f"Playbook audit: {manifest['audit']}")
        elif args.command == "team-playbook-evaluate":
            from nfl_prop_model.modeling.team_playbook import save_playbook_evaluation

            report = save_playbook_evaluation(
                args.data_dir,
                args.report_dir,
                evaluation_seasons=tuple(args.evaluation_seasons),
            )
            print(
                f"Compared playbook across {len(report['folds'])} cutoffs. "
                f"Report: {args.report_dir / 'team_playbook.md'}"
            )
        elif args.command == "team-context-fetch":
            from nfl_prop_model.data.team_context import fetch_context_sources

            fetch_context_sources(args.data_dir, args.seasons, refresh=args.refresh)
        elif args.command == "team-context-build":
            from nfl_prop_model.data.team_context import build_context_cache

            manifest = build_context_cache(args.data_dir, args.seasons)
            print(f"Context audit: {manifest['audit']}")
        elif args.command == "team-context-evaluate":
            from nfl_prop_model.modeling.team_context import save_context_evaluation

            report = save_context_evaluation(
                args.data_dir,
                args.report_dir,
                evaluation_seasons=tuple(args.evaluation_seasons),
            )
            print(
                f"Compared context across {len(report['folds'])} cutoffs. "
                f"Report: {args.report_dir / 'team_context.md'}"
            )
        elif args.command == "team-fetch":
            fetch_team_sources(args.data_dir, args.seasons, refresh=args.refresh)
        elif args.command == "team-build":
            manifest = build_team_cache(args.data_dir, args.seasons)
            print(f"Team data audit: {manifest['audit']}")
        elif args.command == "team-evaluate":
            report = save_team_evaluation(
                args.data_dir,
                args.report_dir,
                evaluation_seasons=tuple(args.evaluation_seasons),
            )
            print(
                f"Scored {len(report['folds'])} temporal folds. "
                f"Report: {args.report_dir / 'team_scores.md'}"
            )
        elif args.command == "team-experiment":
            from nfl_prop_model.modeling.team_experiments import save_team_experiments

            report = save_team_experiments(
                args.data_dir,
                args.report_dir,
                evaluation_seasons=tuple(args.evaluation_seasons),
            )
            print(
                f"Compared four feature ablations across {len(report['folds'])} cutoffs. "
                f"Report: {args.report_dir / 'team_experiments.md'}"
            )
        elif args.command == "team-tune":
            from nfl_prop_model.modeling.team_tuning import save_team_tuning

            report = save_team_tuning(
                args.data_dir,
                args.report_dir,
                evaluation_seasons=tuple(args.evaluation_seasons),
            )
            print(
                f"Locked {len(report['selections'])} season choices. "
                f"Report: {args.report_dir / 'team_tuning.md'}"
            )
        else:
            path = save_team_forecast(args.data_dir, horizon=args.horizon, days=args.days)
            print(f"Saved team score/margin research forecast: {path}")
        return 0
    except (OSError, ValueError, ConnectionError, pl.exceptions.PolarsError) as error:
        parser.exit(1, f"Error: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
