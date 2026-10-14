"""Past primary-passer proxies and timestamp-gated, partial injury observations."""

from bisect import bisect_left
from collections import defaultdict
from datetime import datetime
from typing import Any

import numpy as np
import polars as pl

from nfl_prop_model.features.team_epa import TeamHistory

QB_FEATURES = tuple(
    f"{side}_{name}"
    for side in ("home", "away")
    for name in (
        "qb_epa_prior",
        "qb_history_games",
        "qb_primary_share_last",
        "qb_changed_last_game",
    )
)
INJURY_FEATURES = tuple(
    f"{side}_{name}"
    for side in ("home", "away")
    for name in (
        "qb_reported_out",
        "qb_reported_uncertain",
        "ol_reported_out",
        "skill_reported_out",
        "defense_reported_out",
        "players_reported_rest_out",
    )
)
INTERACTION_FEATURES = tuple(f"{side}_qb_out_epa" for side in ("home", "away"))
CONTEXT_FEATURE_POLICY = {
    "primary_passer": "most attempts+sacks in last available team game; ties/absence unknown",
    "qb_skill": "five available passer games, 0.85 decay, 100 plays at past league passing EPA",
    "qb_passing_plays": "attempts plus sacks; no claim of scramble-inclusive dropback EPA",
    "injuries": "same season/week/team records with modification+24h strictly before cutoff",
    "coverage": "partial observed reports; no eligible row is unknown, never a healthy roster",
    "qb_status": "reported status of last primary passer; no current-game starter labels",
    "season_start": "primary passer proxy unknown until a current-season team game is available",
    "rest": "Out plus an explicit Rest reason only; coaching announcements not reconstructed",
    "limitation": "modification time is not verified original publication; source revisions remain",
}
OL = {"C", "G", "T", "OL", "OG", "OT"}
SKILL = {"WR", "TE", "RB", "FB"}
DEFENSE = {"DE", "DT", "NT", "LB", "ILB", "OLB", "MLB", "CB", "DB", "S", "FS", "SS"}


class ContextHistory:
    def __init__(self, passers: pl.DataFrame, reports: pl.DataFrame, teams: pl.DataFrame) -> None:
        self.team_history = TeamHistory(teams)
        self.by_game: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        self.by_player: dict[str, list[dict[str, Any]]] = defaultdict(list)
        rows = passers.sort("result_available_at_utc", "game_id", "player_id").to_dicts()
        for row in rows:
            self.by_game[(row["game_id"], row["team"])].append(row)
            self.by_player[row["player_id"]].append(row)
        self.player_times = {
            player: [row["result_available_at_utc"] for row in history]
            for player, history in self.by_player.items()
        }
        self.times = [row["result_available_at_utc"] for row in rows]
        known = [row["passing_epa"] is not None and row["passing_plays"] > 0 for row in rows]
        self.epa_prefix = np.concatenate(
            (
                [0.0],
                np.cumsum(
                    [
                        row["passing_epa"] if valid else 0.0
                        for row, valid in zip(rows, known, strict=True)
                    ]
                ),
            )
        )
        self.plays_prefix = np.concatenate(
            (
                [0.0],
                np.cumsum(
                    [
                        row["passing_plays"] if valid else 0.0
                        for row, valid in zip(rows, known, strict=True)
                    ]
                ),
            )
        )
        self.reports: dict[tuple[int, int, str], list[dict[str, Any]]] = defaultdict(list)
        for report in reports.sort("available_at_utc").to_dicts():
            self.reports[(report["season"], report["week"], report["team"])].append(report)

    def primary(
        self, game: dict[str, Any] | None, team: str, cutoff: datetime
    ) -> tuple[str | None, float | None]:
        candidates = self.by_game.get((game["game_id"], team), []) if game else []
        candidates = [row for row in candidates if row["result_available_at_utc"] < cutoff]
        total = sum(row["passing_plays"] for row in candidates)
        if not total:
            return None, None
        best = max(row["passing_plays"] for row in candidates)
        winners = [row for row in candidates if row["passing_plays"] == best]
        return (winners[0]["player_id"], best / total) if len(winners) == 1 else (None, None)

    def features(self, team: str, season: int, week: int, cutoff: datetime) -> dict[str, Any]:
        past = self.team_history.available(team, cutoff)
        last = past[-1] if past and past[-1]["season"] == season else None
        player, share = self.primary(last, team, cutoff)
        previous, _ = self.primary(past[-2] if len(past) >= 2 else None, team, cutoff)
        count = bisect_left(self.player_times.get(player or "", []), cutoff)
        career = self.by_player.get(player or "", [])[:count]
        recent = [
            row for row in career if row["passing_epa"] is not None and row["passing_plays"] > 0
        ][-5:]
        index = bisect_left(self.times, cutoff)
        league = (
            self.epa_prefix[index] / self.plays_prefix[index] if self.plays_prefix[index] else 0.0
        )
        weights = [0.85 ** (len(recent) - i - 1) for i in range(len(recent))]
        plays = sum(
            row["passing_plays"] * weight for row, weight in zip(recent, weights, strict=True)
        )
        rate = (
            sum(row["passing_epa"] * weight for row, weight in zip(recent, weights, strict=True))
            + 100 * league
        ) / (plays + 100)
        result: dict[str, Any] = {
            "last_primary_passer_id": player,
            "qb_epa_prior": float(rate) if player else None,
            "qb_history_games": len(career) if player else None,
            "qb_primary_share_last": share,
            "qb_changed_last_game": float(player != previous) if player and previous else None,
            "qb_history_available_at_utc": past[-1]["result_available_at_utc"] if past else None,
        }
        observed = [
            row
            for row in self.reports.get((season, week, team), [])
            if row["available_at_utc"] < cutoff
        ]
        latest = {row["gsis_id"]: row for row in observed}
        result["injury_observed_players"] = len(latest)
        result["injury_history_available_at_utc"] = max(
            (row["available_at_utc"] for row in latest.values()), default=None
        )
        if not latest:
            result.update(
                {
                    name.removeprefix("home_"): None
                    for name in INJURY_FEATURES
                    if name.startswith("home_")
                }
            )
        else:
            current = latest.get(player or "", {})
            known_status = player is not None and current.get("report_status") != "Note"
            out = [row for row in latest.values() if row["report_status"] == "Out"]
            result.update(
                {
                    "qb_reported_out": float(current.get("report_status") == "Out")
                    if known_status
                    else None,
                    "qb_reported_uncertain": float(
                        current.get("report_status") in {"Questionable", "Doubtful"}
                    )
                    if known_status
                    else None,
                    "ol_reported_out": sum(row["position"] in OL for row in out),
                    "skill_reported_out": sum(row["position"] in SKILL for row in out),
                    "defense_reported_out": sum(row["position"] in DEFENSE for row in out),
                    "players_reported_rest_out": sum(
                        "rest" in (row["report_primary_injury"] or "").lower() for row in out
                    ),
                }
            )
        result["qb_out_epa"] = (
            result["qb_epa_prior"] * result["qb_reported_out"]
            if result["qb_epa_prior"] is not None and result["qb_reported_out"] is not None
            else None
        )
        return result


def add_context_features(
    passers: pl.DataFrame, reports: pl.DataFrame, teams: pl.DataFrame, canonical: pl.DataFrame
) -> pl.DataFrame:
    history = ContextHistory(passers, reports, teams)
    records = []
    for row in canonical.to_dicts():
        record: dict[str, Any] = {"game_id": row["game_id"]}
        for side in ("home", "away"):
            extra = history.features(
                row[f"{side}_team"], row["season"], row["week"], row["prediction_time_utc"]
            )
            record.update({f"{side}_{key}": value for key, value in extra.items()})
        records.append(record)
    additions = pl.DataFrame(records, infer_schema_length=None).with_columns(
        pl.col(QB_FEATURES + INJURY_FEATURES + INTERACTION_FEATURES).cast(pl.Float64),
        pl.col("home_last_primary_passer_id", "away_last_primary_passer_id").cast(pl.String),
        pl.col(
            [
                f"{side}_{kind}_history_available_at_utc"
                for side in ("home", "away")
                for kind in ("qb", "injury")
            ]
        ).cast(pl.Datetime("us", "UTC")),
    )
    return canonical.join(
        additions, on="game_id", how="left", validate="1:1", maintain_order="left"
    )
