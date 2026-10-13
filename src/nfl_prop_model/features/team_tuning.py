"""Configurable past-only EPA windows, isolated from the published feature policy."""

from dataclasses import asdict, dataclass

import polars as pl

from nfl_prop_model.features.team_epa import RATE_GROUPS, TeamHistory


@dataclass(frozen=True)
class RatingSettings:
    recent_games: int = 5
    recent_decay: float = 0.85
    prior_plays: float = 200.0

    def __post_init__(self) -> None:
        if not 1 <= self.recent_games <= 20:
            raise ValueError("Recent history must contain 1-20 games")
        if not 0 < self.recent_decay <= 1 or not 0 < self.prior_plays <= 2000:
            raise ValueError("Use positive decay at most 1 and 1-2000 prior pseudo-plays")

    def policy(self) -> dict[str, int | float]:
        return asdict(self)


def tuned_matchup_features(
    teams: pl.DataFrame, canonical: pl.DataFrame, settings: RatingSettings
) -> pl.DataFrame:
    """Replace only EPA rates; retain identical timing, identities, rest and targets."""
    if settings == RatingSettings():
        return canonical.clone()
    history = TeamHistory(teams)
    records = []
    for row in canonical.to_dicts():
        record = dict(row)
        cutoff, season = row["prediction_time_utc"], row["season"]
        for side in ("home", "away"):
            past = history.available(row[f"{side}_team"], cutoff)
            current = [game for game in past if game["season"] == season]
            previous = [game for game in past if game["season"] == season - 1]
            recent = past[-settings.recent_games :]
            weights = [settings.recent_decay ** (len(recent) - i - 1) for i in range(len(recent))]
            for group in RATE_GROUPS:
                prior_plays = sum(game[group + "plays"] for game in previous)
                prior = (
                    sum(game[group + "epa_sum"] for game in previous) / prior_plays
                    if prior_plays
                    else history.league_rate(group, cutoff)
                )
                plays = sum(game[group + "plays"] for game in current)
                record[f"{side}_{group}epa_season"] = (
                    sum(game[group + "epa_sum"] for game in current) + settings.prior_plays * prior
                ) / (plays + settings.prior_plays)
                recent_plays = sum(
                    game[group + "plays"] * weight
                    for game, weight in zip(recent, weights, strict=True)
                )
                record[f"{side}_{group}epa_recent"] = (
                    sum(
                        game[group + "epa_sum"] * weight
                        for game, weight in zip(recent, weights, strict=True)
                    )
                    / recent_plays
                    if recent_plays
                    else prior
                )
        records.append(record)
    return pl.DataFrame(records, schema=canonical.schema)
