"""Hand-authored synthetic fixtures; no network or licensed data in unit tests."""

import polars as pl
import pytest


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
