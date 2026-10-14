"""Independent richer play snapshots, retaining the canonical EPA source and cohort."""

import hashlib
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

import polars as pl

from nfl_prop_model.data.schemas import DataQualityError, require_keys
from nfl_prop_model.data.storage import load_frame, read_json, store_frame, write_json
from nfl_prop_model.data.team_games import (
    PBP_COLUMNS,
    load_team_cache,
    normalize_teams,
    team_seasons,
    team_source_hash,
)

EXTRA_COLUMNS = ("yards_gained", "sack", "interception", "fumble_lost")
PLAY_METRICS = ("success", "explosive_dropback", "explosive_rush", "sack", "turnover")


def playbook_source_hash() -> str:
    paths = [
        Path(__file__),
        Path(__file__).parents[1] / "features" / "team_playbook.py",
        Path(__file__).parents[1] / "features" / "team_experiments.py",
    ]
    digest = hashlib.sha256(team_source_hash().encode())
    for path in paths:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def enrich_plays(original: pl.DataFrame, expanded: pl.DataFrame) -> pl.DataFrame:
    """Join extra measurements to the exact original plays; never replace stored EPA."""
    if missing := set(PBP_COLUMNS + EXTRA_COLUMNS) - set(expanded.columns):
        raise DataQualityError(f"Expanded play source missing columns: {sorted(missing)}")
    regular = normalize_teams(
        expanded.filter(pl.col("season_type") == "REG"),
        ["home_team", "away_team", "posteam", "defteam"],
    )
    base = normalize_teams(
        original.filter(pl.col("season_type") == "REG"),
        ["home_team", "away_team", "posteam", "defteam"],
    )
    require_keys(regular, ["game_id", "play_id"], "expanded plays")
    require_keys(base, ["game_id", "play_id"], "canonical plays")
    if base.join(regular, on=["game_id", "play_id"], how="anti").height:
        raise DataQualityError("Expanded source is missing canonical play IDs")
    identities = ("season", "week", "home_team", "away_team", "posteam", "defteam")
    joined = base.join(
        regular.select("game_id", "play_id", *identities, *EXTRA_COLUMNS),
        on=["game_id", "play_id"],
        validate="1:1",
        suffix="_new",
        maintain_order="left",
    )
    if joined.filter(
        pl.any_horizontal(~pl.col(name).eq_missing(pl.col(name + "_new")) for name in identities)
    ).height:
        raise DataQualityError("Expanded play identities disagree with canonical snapshot")
    return joined.select(*PBP_COLUMNS, *EXTRA_COLUMNS)


def aggregate_playbook(
    pbp: pl.DataFrame, teams: pl.DataFrame
) -> tuple[pl.DataFrame, dict[str, Any]]:
    if missing := set(PBP_COLUMNS + EXTRA_COLUMNS) - set(pbp.columns):
        raise DataQualityError(f"Playbook missing columns: {sorted(missing)}")
    require_keys(pbp, ["game_id", "play_id"], "playbook plays")
    plays = normalize_teams(pbp, ["home_team", "away_team", "posteam", "defteam"])
    eligible = plays.filter(
        (pl.col("season_type") == "REG")
        & ((pl.col("pass") == 1) | (pl.col("rush") == 1)).fill_null(False)
        & (pl.col("play_type") != "no_play").fill_null(False)
        & (pl.col("qb_kneel") != 1).fill_null(True)
        & (pl.col("qb_spike") != 1).fill_null(True)
        & (pl.col("two_point_attempt") != 1).fill_null(True)
        & pl.col("epa").is_finite().fill_null(False)
    ).join(
        teams.select("game_id", pl.col("team").alias("posteam")),
        on=["game_id", "posteam"],
        how="inner",
        validate="m:1",
    )
    for flag in ("sack", "interception", "fumble_lost"):
        if eligible.filter(pl.col(flag).is_not_null() & ~pl.col(flag).is_in([0, 1])).height:
            raise DataQualityError(f"Invalid {flag} flag on eligible play")
    dropback = pl.col("qb_dropback") == 1
    rush = (pl.col("rush") == 1) & ~dropback
    yards_known = pl.col("yards_gained").is_finite().fill_null(False)
    turn_known = pl.col("interception").is_not_null() & pl.col("fumble_lost").is_not_null()
    definitions = {
        "success": (pl.lit(True), pl.col("epa") > 0),
        "explosive_dropback": (dropback & yards_known, pl.col("yards_gained") >= 20),
        "explosive_rush": (rush & yards_known, pl.col("yards_gained") >= 10),
        "sack": (dropback & pl.col("sack").is_not_null(), pl.col("sack") == 1),
        "turnover": (turn_known, (pl.col("interception") == 1) | (pl.col("fumble_lost") == 1)),
    }
    totals = (
        eligible.group_by("game_id", "posteam")
        .agg(
            pl.len().alias("observed_plays"),
            pl.col("epa").sum().alias("observed_epa"),
            *[
                expression.alias(f"off_{metric}_{suffix}")
                for metric, (known, event) in definitions.items()
                for suffix, expression in (
                    ("count", (known & event).fill_null(False).sum()),
                    ("exposure", known.sum() if metric != "success" else pl.len()),
                )
            ],
        )
        .rename({"posteam": "team"})
    )
    result = teams.join(totals, on=["game_id", "team"], how="left", validate="1:1")
    if result.filter(
        pl.col("observed_plays").is_null()
        | (pl.col("observed_plays") != pl.col("off_plays"))
        | ((pl.col("observed_epa") - pl.col("off_epa_sum")).abs() > 1e-8)
    ).height:
        raise DataQualityError("Expanded play counts/EPA disagree with canonical team games")
    # Defensive measurements mirror the opposing offense on the same eligible plays.
    defensive = totals.select("game_id", "team", pl.col("^off_.*$")).rename(
        {
            "team": "opponent_team",
            **{
                name: name.replace("off_", "def_", 1)
                for name in totals.columns
                if name.startswith("off_")
            },
        }
    )
    result = result.join(defensive, on=["game_id", "opponent_team"], validate="1:1").drop(
        "observed_plays", "observed_epa"
    )
    audit = {
        "team_games": result.height,
        "eligible_plays": eligible.height,
        "missing_yards": eligible.filter(~yards_known).height,
        "missing_sack_on_dropback": eligible.filter(dropback & pl.col("sack").is_null()).height,
        "missing_turnover_flags": eligible.filter(~turn_known).height,
        "canonical_epa_preserved": True,
    }
    return result, audit


def fetch_playbook_sources(data_dir: Path, seasons: list[int], *, refresh: bool = False) -> None:
    import nflreadpy as nfl
    from nflreadpy.config import update_config

    update_config(cache_mode="off")
    for season in team_seasons(seasons):
        directory = data_dir / "raw" / "team_playbook" / str(season)
        canonical_dir = data_dir / "raw" / "team_epa" / str(season)
        canonical = read_json(canonical_dir / "manifest.json")
        original = load_frame(canonical_dir, canonical["datasets"]["pbp"])
        if (directory / "manifest.json").exists() and not refresh:
            manifest = read_json(directory / "manifest.json")
            if manifest["canonical_pbp_sha256"] != canonical["datasets"]["pbp"]["sha256"]:
                raise DataQualityError("Canonical play source changed; fetch playbook --refresh")
            load_frame(directory, manifest["dataset"])
            print(f"Verified expanded plays: {season}", flush=True)
            continue
        print(f"Downloading expanded plays: {season}", flush=True)
        expanded = nfl.load_pbp(season)
        enriched = enrich_plays(original, expanded)
        manifest = {
            "season": season,
            "retrieved_at_utc": datetime.now(UTC).isoformat(),
            "nflreadpy_version": version("nflreadpy"),
            "canonical_pbp_sha256": canonical["datasets"]["pbp"]["sha256"],
            "canonical_source": canonical,
            "source": f"https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.parquet",
            "observed_source_schema": {key: str(value) for key, value in expanded.schema.items()},
            "dataset": store_frame(directory, "plays", enriched),
            "license": "CC-BY-4.0",
        }
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        write_json(directory / f"manifest-{stamp}.json", manifest)
        write_json(directory / "manifest.json", manifest)


def build_playbook_cache(data_dir: Path, seasons: list[int]) -> dict[str, Any]:
    years = team_seasons(seasons)
    teams, _, canonical = load_team_cache(data_dir)
    frames, sources = [], []
    for year in years:
        directory = data_dir / "raw" / "team_playbook" / str(year)
        source = read_json(directory / "manifest.json")
        if source["season"] != year:
            raise DataQualityError("Playbook season mismatch")
        sources.append(source)
        frames.append(load_frame(directory, source["dataset"]))
    expanded, audit = aggregate_playbook(
        pl.concat(frames, how="vertical_relaxed"), teams.filter(pl.col("season").is_in(years))
    )
    directory = data_dir / "processed" / "team_playbook"
    manifest = {
        "format_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "seasons": years,
        "sources": sources,
        "canonical_sources": canonical,
        "audit": audit,
        "playbook_source_sha256": playbook_source_hash(),
        "artifact": store_frame(directory, "team_plays", expanded),
    }
    write_json(directory / "manifest.json", manifest)
    return manifest


def load_playbook_cache(data_dir: Path) -> tuple[pl.DataFrame, dict[str, Any]]:
    directory = data_dir / "processed" / "team_playbook"
    manifest = read_json(directory / "manifest.json")
    if manifest["playbook_source_sha256"] != playbook_source_hash():
        raise DataQualityError("Playbook source changed; run team-playbook-build again")
    return load_frame(directory, manifest["artifact"]), manifest
