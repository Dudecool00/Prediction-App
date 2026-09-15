"""Fetch through nflreadpy once; builds thereafter can run without the network."""

import platform
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

import polars as pl

from nfl_prop_model.data.storage import load_frame, read_json, store_frame, write_json

DEFAULT_SEASONS = (2022, 2023, 2024)
RESERVED_HOLDOUT_SEASON = 2025


def validate_seasons(seasons: list[int]) -> list[int]:
    if not seasons or any(year < 1999 or year >= RESERVED_HOLDOUT_SEASON for year in seasons):
        raise ValueError(
            "Use development seasons 1999–2024; 2025 onward is reserved for later work."
        )
    return sorted(set(seasons))


def season_key(seasons: list[int]) -> str:
    return "_".join(str(year) for year in validate_seasons(seasons))


@dataclass(frozen=True)
class Snapshot:
    player_stats: pl.DataFrame
    schedules: pl.DataFrame
    manifest: dict[str, Any]


def load_snapshot(data_dir: Path, seasons: list[int]) -> Snapshot:
    directory = data_dir / "raw" / season_key(seasons)
    manifest = read_json(directory / "manifest.json")
    if manifest["seasons"] != validate_seasons(seasons):
        raise ValueError("Cached manifest seasons do not match the requested seasons.")
    return Snapshot(
        load_frame(directory, manifest["datasets"]["player_stats"]),
        load_frame(directory, manifest["datasets"]["schedules"]),
        manifest,
    )


def fetch_snapshot(data_dir: Path, seasons: list[int], *, refresh: bool = False) -> Snapshot:
    seasons = validate_seasons(seasons)
    directory = data_dir / "raw" / season_key(seasons)
    if (directory / "manifest.json").exists() and not refresh:
        return load_snapshot(data_dir, seasons)

    # Import only on a cache miss: cached fetch/build never needs a downloader.
    import nflreadpy as nfl
    from nflreadpy.config import update_config

    update_config(cache_mode="memory")
    nfl.clear_cache()
    stats = nfl.load_player_stats(seasons, summary_level="week")
    schedules = nfl.load_schedules(seasons)
    for name, frame in (("player_stats", stats), ("schedules", schedules)):
        if frame.is_empty() or "season" not in frame.columns:
            raise ValueError(f"{name}: source returned an empty table or no season column.")
        if sorted(frame["season"].unique().to_list()) != seasons:
            raise ValueError(f"{name}: source did not return exactly the requested seasons.")

    manifest: dict[str, Any] = {
        "format_version": 1,
        "retrieved_at_utc": datetime.now(UTC).isoformat(),
        "seasons": seasons,
        "python_version": platform.python_version(),
        "packages": {name: version(name) for name in ("nflreadpy", "polars", "tzdata")},
        "datasets": {
            "player_stats": {
                **store_frame(directory, "player_stats", stats),
                "loader": "nflreadpy.load_player_stats(seasons, summary_level='week')",
                "source": "https://github.com/nflverse/nflverse-data/releases/tag/stats_player",
                "license": "CC-BY-4.0",
            },
            "schedules": {
                **store_frame(directory, "schedules", schedules),
                "loader": "nflreadpy.load_schedules(seasons)",
                "source": "https://github.com/nflverse/nflverse-data/releases/tag/schedules",
                "license": "CC-BY-4.0",
            },
        },
    }
    # Keep historical manifests as well as the current pointer on refresh.
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    write_json(directory / f"manifest-{stamp}.json", manifest)
    write_json(directory / "manifest.json", manifest)
    return Snapshot(stats, schedules, manifest)
