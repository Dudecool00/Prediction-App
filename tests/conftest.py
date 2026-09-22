"""Hand-authored synthetic fixtures; no network or licensed data in unit tests."""

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from nfl_prop_model.data.storage import store_frame, write_json


@pytest.fixture
def sources() -> tuple[pl.DataFrame, pl.DataFrame]:
    stats = []
    schedules = []
    dates = [
        "2023-11-05",
        "2023-11-12",
        "2023-11-19",
        "2023-11-26",
        "2023-12-03",
        "2024-09-08",
        "2024-09-15",
    ]
    for index, date in enumerate(dates):
        season = int(date[:4])
        week = index + 1 if season == 2023 else index - 4
        game_id = f"{season}_{week:02d}_AWAY_HOME"
        team = "HOME" if index < 5 else "AWAY"  # A changes teams between seasons.
        schedules.append(
            {
                "game_id": game_id,
                "season": season,
                "week": week,
                "game_type": "REG",
                "gameday": date,
                "gametime": "13:00",
                "home_team": "HOME",
                "away_team": "AWAY",
                "home_score": 24,
                "away_score": 17,
                "home_qb_id": "A" if team == "HOME" else "B",
                "away_qb_id": "B" if team == "HOME" else "A",
            }
        )
        for player, yards, player_team in (
            ("A", (index + 1) * 100, team),
            ("B", 50, "AWAY" if team == "HOME" else "HOME"),
        ):
            stats.append(
                {
                    "player_id": player,
                    "player_display_name": f"Player {player}",
                    "position": "QB",
                    "season_type": "REG",
                    "game_id": game_id,
                    "season": season,
                    "week": week,
                    "team": player_team,
                    "opponent_team": "AWAY" if player_team == "HOME" else "HOME",
                    "passing_yards": yards,
                    "attempts": 10 + index,
                    "completions": 8,
                }
            )
    return pl.DataFrame(stats), pl.DataFrame(schedules)


@pytest.fixture
def saved_research(tmp_path):
    data = tmp_path / "data"
    directory = data / "processed" / "research_2022_2024"
    kickoff = datetime(2023, 9, 17, 17, tzinfo=UTC)
    train_cutoff = datetime(2023, 8, 31, 17, tzinfo=UTC)
    evaluation_cutoff = kickoff - timedelta(hours=1)
    point = pl.DataFrame(
        [
            {
                "player_id": "QB",
                "game_id": "2023_02_A_B",
                "model": "xgb_schedule",
                "season": 2023,
                "week": 2,
                "fold": "2023-W02",
                "kickoff_utc": kickoff,
                "prediction_time_utc": evaluation_cutoff,
                "evaluation_cutoff_utc": evaluation_cutoff,
                "training_cutoff_utc": train_cutoff,
                "prediction": 200.0,
                "actual": 350.0,
                "prior_games_in_sample": 5,
                "history_bucket": "5+ prior games",
            }
        ]
    )
    calibration = pl.DataFrame(
        [
            {
                "player_id": f"QB-{index % 2}",
                "game_id": f"cal-{index // 2}",
                "model": "xgb_schedule",
                "fold": "2023-W02",
                "kickoff_utc": train_cutoff + timedelta(days=2, hours=3 * (index // 2)),
                "prediction_time_utc": train_cutoff + timedelta(days=2, hours=3 * (index // 2) - 1),
                "training_cutoff_utc": train_cutoff,
                "evaluation_cutoff_utc": evaluation_cutoff,
                "residual": float(index - 50),
            }
            for index in range(100)
        ]
    )
    identity = pl.DataFrame(
        [
            {
                "player_id": "QB",
                "game_id": "2023_02_A_B",
                "player_display_name": "Example QB",
                "team": "A",
                "opponent_team": "B",
            }
        ]
    )
    coverage = {
        "model": "xgb_schedule",
        "coverage": 0.9,
        "n": 100,
        "observed_coverage": 0.89,
        "history_bucket": "5+ prior games",
    }
    manifest = {
        "format_version": 2,
        "generated_at_utc": "2026-09-18T00:00:00+00:00",
        "research_source_sha256": "test-source-hash",
        "method": {"minimum_calibration_rows": 100},
        "context_sources": {"retrieved_at_utc": "2026-09-15T00:00:00+00:00"},
        "folds": [
            {
                "fold": "2023-W02",
                "calibration_rows": 100,
                "latest_calibration_result_available_utc": (
                    calibration["kickoff_utc"].max() + timedelta(hours=24)
                ).isoformat(),
            }
        ],
        "interval_overall": [coverage],
        "interval_by_history": [coverage],
        "artifacts": {
            name: store_frame(directory, name, frame)
            for name, frame in (
                ("predictions", point),
                ("calibration_residuals", calibration),
                ("features", identity),
            )
        },
    }
    write_json(directory / "manifest.json", manifest)
    return data, directory, manifest
