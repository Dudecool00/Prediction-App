"""Select EPA settings on earlier seasons, then lock them for the following season."""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nfl_prop_model.data.schemas import DataQualityError, require_keys
from nfl_prop_model.data.storage import load_frame, read_json, store_frame, write_json
from nfl_prop_model.data.team_games import load_team_cache
from nfl_prop_model.features import team_tuning as feature_module
from nfl_prop_model.features.team_epa import HORIZONS, TEAM_FEATURES, matchup_features
from nfl_prop_model.features.team_tuning import RatingSettings, tuned_matchup_features
from nfl_prop_model.modeling.reporting import markdown_table
from nfl_prop_model.modeling.team_scores import (
    forecast_rows,
    model_source_hash,
    runtime_versions,
    score_metrics,
    score_pipeline,
    validate_features,
)


@dataclass(frozen=True)
class TuningProfile:
    name: str
    rating: RatingSettings = RatingSettings()
    training_seasons: int | None = None

    def __post_init__(self) -> None:
        if self.training_seasons is not None and not 3 <= self.training_seasons <= 10:
            raise ValueError("Training windows must contain 3-10 NFL seasons including current")

    def policy(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "training_seasons": self.training_seasons,
            "rating": self.rating.policy(),
        }


PROFILES = (
    TuningProfile("ridge_epa_scores"),
    TuningProfile("epa_train_3_seasons", training_seasons=3),
    TuningProfile("epa_train_5_seasons", training_seasons=5),
    TuningProfile("epa_recent_3_games", RatingSettings(recent_games=3)),
    TuningProfile("epa_recent_8_games", RatingSettings(recent_games=8)),
    TuningProfile("epa_decay_065", RatingSettings(recent_decay=0.65)),
    TuningProfile("epa_decay_100", RatingSettings(recent_decay=1.0)),
    TuningProfile("epa_prior_100", RatingSettings(prior_plays=100)),
    TuningProfile("epa_prior_400", RatingSettings(prior_plays=400)),
    TuningProfile("epa_fast_3_seasons", RatingSettings(3, 0.65, 100), 3),
    TuningProfile("epa_slow_5_seasons", RatingSettings(8, 0.95, 400), 5),
)
TUNED_MODEL = "chronologically_tuned_epa_scores"
TUNING_POLICY: dict[str, Any] = {
    "profiles": [profile.policy() for profile in PROFILES],
    "features": list(TEAM_FEATURES),
    "ridge_alpha": 20.0,
    "training_window": "NFL season count includes the current season; available outcomes only",
    "selection": "preceding two completed NFL seasons, early-week margin MAE",
    "selection_lock": "before first early-week forecast in the evaluation season",
    "score_guard": "candidate score MAE cannot exceed control by more than 0.05 points",
    "minimum_validation_gain": 0.05,
    "ties": "margin MAE then fixed profile order; default retained if gain below 0.05",
    "refresh": "same setting chosen on early-week validation; no independent refresh selection",
    "outer_evaluation": "2022-2024 development simulation; 2025 is already accessed",
    "promotion": "no automatic change to published forecasts",
}


def tuning_source_hash() -> str:
    return hashlib.sha256(
        model_source_hash().encode()
        + Path(feature_module.__file__).read_bytes()
        + Path(__file__).read_bytes()
    ).hexdigest()


def validate_profiles(profiles: tuple[TuningProfile, ...]) -> list[str]:
    names = [profile.name for profile in profiles]
    if not profiles or profiles[0] != PROFILES[0] or len(set(names)) != len(names):
        raise ValueError("Unique profiles must begin with the unchanged current EPA control")
    return names


def select_profiles(
    panel: pl.DataFrame,
    locks: dict[int, datetime],
    *,
    profiles: tuple[TuningProfile, ...] = PROFILES,
) -> list[dict[str, Any]]:
    """Outer outcomes never enter selection, even when present in the supplied panel."""
    require_keys(panel, ["game_id", "horizon", "model"], "tuning selection panel")
    names = validate_profiles(profiles)
    selections = []
    for season, lock in sorted(locks.items()):
        validation = panel.filter(
            (pl.col("horizon") == "early_week") & pl.col("season").is_in([season - 2, season - 1])
        )
        if validation.is_empty() or set(validation["season"]) != {season - 2, season - 1}:
            raise DataQualityError("Both earlier validation seasons are required")
        if validation.filter(pl.col("result_available_at_utc") >= lock).height:
            raise DataQualityError("Validation outcomes were unavailable at the selection lock")
        control = validation.filter(pl.col("model") == names[0])
        cohort = set(control["game_id"])
        for name in names:
            if set(validation.filter(pl.col("model") == name)["game_id"]) != cohort:
                raise DataQualityError("Tuning validation cohorts do not match")
        metrics = score_metrics(validation).filter(pl.col("scope") == "overall").to_dicts()
        by_name = {metric["model"]: metric for metric in metrics}
        baseline = by_name[names[0]]
        eligible = [
            name for name in names if by_name[name]["score_mae"] <= baseline["score_mae"] + 0.05
        ]
        chosen = min(eligible, key=lambda name: (by_name[name]["margin_mae"], names.index(name)))
        if baseline["margin_mae"] - by_name[chosen]["margin_mae"] < 0.05:
            chosen = names[0]
        latest = validation["result_available_at_utc"].max()
        if not isinstance(latest, datetime):
            raise DataQualityError("Missing validation availability")
        selections.append(
            {
                "season": season,
                "selected_profile": chosen,
                "lock_utc": lock.isoformat(),
                "validation_seasons": [season - 2, season - 1],
                "validation_games": control.height,
                "latest_validation_result_available_utc": latest.isoformat(),
                "validation_metrics": metrics,
                "selection_gain": baseline["margin_mae"] - by_name[chosen]["margin_mae"],
            }
        )
    return selections


def training_rows(
    features: pl.DataFrame, cutoff: datetime, season: int, profile: TuningProfile
) -> pl.DataFrame:
    available = (
        (pl.col("result_available_at_utc") < cutoff)
        & pl.col("home_score").is_not_null()
        & (pl.col("season") <= season)
    )
    if profile.training_seasons is not None:
        available &= pl.col("season") >= season - profile.training_seasons + 1
    return features.filter(available)


def evaluate_team_tuning(
    teams: pl.DataFrame,
    games: pl.DataFrame,
    *,
    evaluation_seasons: tuple[int, ...] = (2022, 2023, 2024),
    profiles: tuple[TuningProfile, ...] = PROFILES,
) -> tuple[pl.DataFrame, pl.DataFrame, list[dict[str, Any]], list[dict[str, Any]]]:
    if not evaluation_seasons:
        raise ValueError("Specify at least one tuning evaluation season")
    validate_profiles(profiles)
    scoring_seasons = set(evaluation_seasons)
    scoring_seasons.update(year - offset for year in evaluation_seasons for offset in (1, 2))
    records, folds = [], []
    locks: dict[int, datetime] = {}
    selected_games = games.filter(pl.col("season") <= max(evaluation_seasons))
    for horizon in HORIZONS:
        canonical = matchup_features(teams, selected_games, horizon=horizon, schedule_history=games)
        evaluation = canonical.filter(
            pl.col("season").is_in(scoring_seasons) & pl.col("home_score").is_not_null()
        )
        if evaluation.is_empty():
            raise DataQualityError("No completed tuning games")
        if horizon == "early_week":
            for season in evaluation_seasons:
                cutoff = canonical.filter(pl.col("season") == season)["prediction_time_utc"].min()
                if not isinstance(cutoff, datetime):
                    raise DataQualityError("Missing evaluation season")
                locks[season] = cutoff
        matrices: dict[RatingSettings, pl.DataFrame] = {}
        for profile in profiles:
            if profile.rating not in matrices:
                matrices[profile.rating] = tuned_matchup_features(teams, canonical, profile.rating)
                validate_features(matrices[profile.rating])
            features = matrices[profile.rating]
            for group in evaluation.partition_by("prediction_time_utc", maintain_order=True):
                cutoff, season = group["prediction_time_utc"][0], group["season"][0]
                test = features.filter(pl.col("game_id").is_in(group["game_id"].to_list()))
                train = training_rows(features, cutoff, season, profile)
                if train.height < 64:
                    raise DataQualityError(
                        "At least 64 available training games required for tuning"
                    )
                if set(train["game_id"]).intersection(test["game_id"]):
                    raise DataQualityError("Tuning training and test games overlap")
                targets = np.asarray(
                    train.select("home_score", "away_score").to_numpy(), dtype=np.float64
                )
                if not np.isfinite(targets).all():
                    raise DataQualityError("Tuning training scores must be finite")
                model = score_pipeline()
                model.fit(
                    np.asarray(train.select(TEAM_FEATURES).to_numpy(), dtype=np.float64), targets
                )
                values = np.asarray(
                    model.predict(
                        np.asarray(test.select(TEAM_FEATURES).to_numpy(), dtype=np.float64)
                    ),
                    dtype=np.float64,
                )
                result = forecast_rows(test, {profile.name: values}, train)
                availability = dict(
                    zip(test["game_id"], test["result_available_at_utc"], strict=True)
                )
                for record in result:
                    record["result_available_at_utc"] = availability[record["game_id"]]
                records.extend(result)
                latest = train["result_available_at_utc"].max()
                if not isinstance(latest, datetime):
                    raise DataQualityError("Missing tuning training availability")
                folds.append(
                    {
                        "horizon": horizon,
                        "profile": profile.name,
                        "cutoff_utc": cutoff.isoformat(),
                        "training_games": train.height,
                        "test_games": test.height,
                        "latest_training_result_available_utc": latest.isoformat(),
                        "earliest_training_season": train["season"].min(),
                    }
                )
    panel = pl.DataFrame(records, infer_schema_length=None)
    selections = select_profiles(panel, locks, profiles=profiles)
    outer = panel.filter(pl.col("season").is_in(evaluation_seasons))
    control = outer.filter(pl.col("model") == "ridge_epa_scores")
    chosen = pl.concat(
        [
            outer.filter(
                (pl.col("season") == selection["season"])
                & (pl.col("model") == selection["selected_profile"])
            ).with_columns(pl.lit(TUNED_MODEL).alias("model"))
            for selection in selections
        ]
    )
    predictions = pl.concat([control, chosen])
    require_keys(predictions, ["game_id", "horizon", "model"], "outer tuning predictions")
    return predictions, panel, selections, folds


def tuning_comparisons(predictions: pl.DataFrame) -> list[dict[str, Any]]:
    records = []
    for horizon in HORIZONS:
        frame = predictions.filter(pl.col("horizon") == horizon).with_columns(
            (pl.col("predicted_home_margin") - pl.col("target_home_margin")).abs().alias("error")
        )
        control = frame.filter(pl.col("model") == "ridge_epa_scores")
        candidate = frame.filter(pl.col("model") == TUNED_MODEL)
        paired = candidate.join(
            control.select("game_id", pl.col("error").alias("control_error")),
            on="game_id",
            validate="1:1",
        ).with_columns((pl.col("control_error") - pl.col("error")).alias("gain"))
        if paired.height != control.height or paired.height != candidate.height:
            raise DataQualityError("Outer tuning cohorts do not match")
        weeks = (
            paired.group_by("season", "week")
            .agg(pl.col("gain").sum(), pl.len().alias("games"))
            .sort("season", "week")
        )
        if weeks.is_empty():
            raise DataQualityError("No outer tuning weeks")
        rng = np.random.default_rng(20261013)
        indexes = rng.integers(0, weeks.height, size=(5000, weeks.height))
        gains = weeks["gain"].to_numpy()[indexes].sum(axis=1)
        gains /= weeks["games"].to_numpy()[indexes].sum(axis=1)
        lower, upper = np.quantile(gains, [0.025, 0.975])
        records.append(
            {
                "horizon": horizon,
                "games": paired.height,
                "margin_mae_gain": float(paired["gain"].to_numpy().mean()),
                "week_bootstrap_gain_lower": float(lower),
                "week_bootstrap_gain_upper": float(upper),
            }
        )
    return records


def save_team_tuning(
    data_dir: Path, report_dir: Path, *, evaluation_seasons: tuple[int, ...] = (2022, 2023, 2024)
) -> dict[str, Any]:
    teams, games, sources = load_team_cache(data_dir)
    directory = data_dir / "processed" / "team_tuning"
    protocol = {
        "locked_at_utc": datetime.now(UTC).isoformat(),
        "policy": TUNING_POLICY,
        "tuning_source_sha256": tuning_source_hash(),
        "evaluation_seasons": list(evaluation_seasons),
    }
    # Record settings and selection rules before running the comparison.
    write_json(directory / "protocol.json", protocol)
    predictions, panel, selections, folds = evaluate_team_tuning(
        teams, games, evaluation_seasons=evaluation_seasons
    )
    metrics = score_metrics(predictions)
    report: dict[str, Any] = {
        "format_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        **protocol,
        "runtime": runtime_versions(),
        "sources": sources,
        "metrics": metrics.to_dicts(),
        "selections": selections,
        "folds": folds,
        "paired_comparisons": tuning_comparisons(predictions),
        "artifacts": {
            "predictions": store_frame(directory, "predictions", predictions),
            "candidate_panel": store_frame(directory, "candidate_panel", panel),
        },
    }
    write_json(directory / "manifest.json", report)
    write_json(report_dir / "team_tuning.json", report)
    lines = [
        "# Chronological EPA tuning",
        "",
        "Choose settings using only the preceding two seasons. Lock each setting before Week 1, "
        "then refit on available games during the following season. Refresh uses the same choice.",
        "",
        "2022–2024 remain development simulations. 2025 is already accessed. "
        "No automatic forecast promotion occurs.",
        "",
        "## Season selections",
        "",
        *markdown_table(
            pl.DataFrame(
                [
                    {
                        key: selection[key]
                        for key in (
                            "season",
                            "selected_profile",
                            "validation_games",
                            "selection_gain",
                            "lock_utc",
                        )
                    }
                    for selection in selections
                ]
            )
        ),
        "",
        "## Overall",
        "",
        *markdown_table(metrics.filter(pl.col("scope") == "overall").drop("scope", "season")),
        "",
        "## By season",
        "",
        *markdown_table(metrics.filter(pl.col("scope") == "season").drop("scope")),
        "",
        "## Paired outer margin gains",
        "",
        *markdown_table(pl.DataFrame(report["paired_comparisons"])),
        "",
        "Week-block ranges are approximate and do not correct serial dependence or earlier "
        "development decisions. Score error counts both team scores; "
        "margin/total count each game once.",
    ]
    (report_dir / "team_tuning.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def load_team_tuning(data_dir: Path) -> tuple[dict[str, Any], pl.DataFrame]:
    directory = data_dir / "processed" / "team_tuning"
    report = read_json(directory / "manifest.json")
    if report["tuning_source_sha256"] != tuning_source_hash():
        raise DataQualityError("Team tuning changed; run team-tune again")
    return report, load_frame(directory, report["artifacts"]["predictions"])
