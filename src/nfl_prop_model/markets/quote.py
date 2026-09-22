"""Connect a manual market to a verified historical out-of-sample forecast."""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nfl_prop_model.data.schemas import DataQualityError, require_keys
from nfl_prop_model.data.storage import load_frame, read_json, write_json
from nfl_prop_model.markets.distribution import DISTRIBUTION_VERSION, passing_yard_probabilities
from nfl_prop_model.markets.odds import ManualMarket, evaluate_market
from nfl_prop_model.modeling.uncertainty import ResidualCalibration


@dataclass(frozen=True)
class HistoricalForecast:
    point: dict[str, Any]
    identity: dict[str, Any]
    calibration: ResidualCalibration
    manifest: dict[str, Any]


def load_historical_forecast(
    data_dir: Path, player_id: str, game_id: str, model: str
) -> HistoricalForecast:
    directory = data_dir / "processed" / "research_2022_2024"
    manifest = read_json(directory / "manifest.json")
    if manifest.get("format_version") != 2 or "calibration_residuals" not in manifest["artifacts"]:
        raise ValueError("Saved research lacks reusable calibration errors; run nfl-prop research")
    predictions = load_frame(directory, manifest["artifacts"]["predictions"])
    selected = predictions.filter(
        (pl.col("player_id") == player_id)
        & (pl.col("game_id") == game_id)
        & (pl.col("model") == model)
    )
    if selected.height != 1:
        raise DataQualityError("Choose exactly one saved historical player/game/model forecast")
    # The selected game's actual outcome is never used to price the manual market.
    point = selected.drop("actual").row(0, named=True)
    if point["season"] not in (2023, 2024):
        raise DataQualityError("Manual research quotes only support 2023–2024 evaluation games")
    for field in (
        "kickoff_utc",
        "prediction_time_utc",
        "training_cutoff_utc",
        "evaluation_cutoff_utc",
    ):
        value = point[field]
        if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
            raise DataQualityError("Forecast times must be UTC timestamps")
    if not (
        point["training_cutoff_utc"]
        < point["evaluation_cutoff_utc"]
        <= point["prediction_time_utc"]
        == point["kickoff_utc"] - timedelta(hours=1)
    ):
        raise DataQualityError("Forecast cutoffs violate the pregame contract")
    records = load_frame(directory, manifest["artifacts"]["calibration_residuals"]).filter(
        (pl.col("fold") == point["fold"]) & (pl.col("model") == model)
    )
    require_keys(records, ["game_id", "player_id"], "saved calibration")
    audits = [fold for fold in manifest["folds"] if fold["fold"] == point["fold"]]
    if len(audits) != 1 or records.height != audits[0]["calibration_rows"]:
        raise DataQualityError("Calibration sample does not match the saved fold audit")
    if records.height < manifest["method"]["minimum_calibration_rows"]:
        raise DataQualityError("Calibration sample is too small")
    if records.select(pl.any_horizontal(pl.all().is_null()).any()).item():
        raise DataQualityError("Calibration records contain missing values")
    if records.filter(
        (pl.col("game_id") == game_id)
        | (pl.col("training_cutoff_utc") != point["training_cutoff_utc"])
        | (pl.col("evaluation_cutoff_utc") != point["evaluation_cutoff_utc"])
        | (pl.col("prediction_time_utc") < point["training_cutoff_utc"])
        | (pl.col("prediction_time_utc") != pl.col("kickoff_utc") - pl.duration(hours=1))
        | (pl.col("kickoff_utc") + pl.duration(hours=24) >= point["evaluation_cutoff_utc"])
    ).height:
        raise DataQualityError("Calibration records violate training/evaluation separation")
    calibration = ResidualCalibration(np.asarray(records["residual"].to_numpy(), dtype=np.float64))
    features = load_frame(directory, manifest["artifacts"]["features"])
    identity = features.filter(
        (pl.col("player_id") == player_id) & (pl.col("game_id") == game_id)
    ).select("player_display_name", "team", "opponent_team")
    if identity.height != 1:
        raise DataQualityError("Forecast player/game identity is missing or ambiguous")
    return HistoricalForecast(point, identity.row(0, named=True), calibration, manifest)


def create_quote(forecast: HistoricalForecast, market: ManualMarket) -> dict[str, Any]:
    point, manifest = forecast.point, forecast.manifest
    prediction = float(point["prediction"])
    probabilities = passing_yard_probabilities(prediction, forecast.calibration, market.line)
    values = evaluate_market(market, probabilities)
    radius = forecast.calibration.radius(0.9)
    fold = next(row for row in manifest["folds"] if row["fold"] == point["fold"])
    return {
        "format_version": 1,
        "scope": "historical_research_demonstration",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "statistic": "passing_yards",
        "player": {"player_id": point["player_id"], **forecast.identity},
        "forecast": {
            key: value.isoformat() if isinstance(value, datetime) else value
            for key, value in point.items()
        },
        "market": asdict(market),
        "probabilities": asdict(probabilities),
        "sides": {name: asdict(value) for name, value in values.items()},
        "uncertainty": {
            "nominal_coverage": 0.9,
            "lower": prediction - radius,
            "upper": prediction + radius,
            "calibration_rows": len(forecast.calibration.residuals),
            "observed_coverage_overall": [
                row
                for row in manifest["interval_overall"]
                if row["model"] == point["model"] and row["coverage"] == 0.9
            ],
            "observed_coverage_history_group": [
                row
                for row in manifest["interval_by_history"]
                if row["model"] == point["model"]
                and row["coverage"] == 0.9
                and row["history_bucket"] == point["history_bucket"]
            ],
        },
        "provenance": {
            "distribution_version": DISTRIBUTION_VERSION,
            "model_code_sha256": manifest["research_source_sha256"],
            "research_generated_at_utc": manifest["generated_at_utc"],
            "source_retrieved_at_utc": manifest["context_sources"]["retrieved_at_utc"],
            "latest_calibration_result_available_utc": fold[
                "latest_calibration_result_available_utc"
            ],
            "artifacts": {
                key: manifest["artifacts"][key]
                for key in ("predictions", "calibration_residuals", "features")
            },
        },
        "limitations": [
            "Historical demonstration with manually entered hypothetical prices; "
            "not a live quote or betting backtest.",
            "Probabilities condition on recorded participation; starter labels, injuries, "
            "and weather remain unresolved.",
            "Outcome probabilities round point-plus-residual samples to whole yards; "
            "push estimates are unvalidated and can be zero.",
            "Interval coverage is empirical, not a guarantee for this player; "
            "small-history groups can under-cover.",
            "EV assumes this market has action, standard win payouts, and full refunds "
            "on pushes; other void rules are not modeled.",
            "Optional game spread/total are recorded notes, not model inputs. "
            "Prices and lines never alter the forecast.",
            "Model estimates may be wrong and may lose money; "
            "no recommended stake or profitability claim.",
        ],
    }


def quote_markdown(quote: dict[str, Any]) -> str:
    forecast, uncertainty = quote["forecast"], quote["uncertainty"]
    lines = [
        "# Manual passing-yards comparison",
        "",
        "**Historical research demonstration — manually entered hypothetical prices.**",
        "",
        f"{quote['player']['player_display_name']} · {forecast['game_id']} · {forecast['model']}",
        "",
        f"Projection: **{forecast['prediction']:.2f} yards**. Nominal 90% interval: "
        f"**{uncertainty['lower']:.2f} to {uncertainty['upper']:.2f} yards**. "
        f"Calibration sample: {uncertainty['calibration_rows']} QB-games.",
        "",
        f"Manual line: **{quote['market']['line']:g} yards**. "
        f"Estimated push probability: **{quote['probabilities']['push']:.2%}**.",
        "",
        "| Side | Price | Model win, no push | Break-even, no push | "
        "Edge (pp) | Fair American | Estimated EV |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for side, value in quote["sides"].items():
        probability = value["conditional_win_probability"]
        probability_text = f"{probability:.2%}" if probability is not None else "N/A"
        edge = value["probability_edge_percentage_points"]
        edge_text = f"{edge:+.2f}" if edge is not None else "N/A"
        price = value["fair_american_odds"]
        price_text = f"{price:+.1f}" if price is not None else "No finite American price"
        lines.append(
            f"| {side.title()} | {value['american_odds']:+d} | {probability_text} | "
            f"{value['break_even_conditional_probability']:.2%} | {edge_text} | "
            f"{price_text} | {value['estimated_ev_percent']:+.2f}% |"
        )
    lines += [
        "",
        "Probability edge is in percentage points; EV is expected profit per dollar staked. "
        "Win and break-even probabilities in the table exclude pushes. Full unconditional "
        "win/loss/push probabilities and unrounded calculations are in the JSON report.",
        "",
        "## Coverage evidence",
        "",
    ]
    for label, key in (
        ("Overall", "observed_coverage_overall"),
        (forecast["history_bucket"], "observed_coverage_history_group"),
    ):
        for row in uncertainty[key]:
            lines.append(
                f"- {label}: nominal 90%, observed {row['observed_coverage']:.2%}, n={row['n']}."
            )
    provenance = quote["provenance"]
    lines += [
        "",
        "## Timestamps and model",
        "",
        f"- Conceptual prediction time: {forecast['prediction_time_utc']}.",
        f"- Model training cutoff: {forecast['training_cutoff_utc']}.",
        f"- Calibration cutoff: {forecast['evaluation_cutoff_utc']}.",
        "- Latest calibration result available: "
        f"{provenance['latest_calibration_result_available_utc']}.",
        f"- Source snapshot retrieved: {provenance['source_retrieved_at_utc']}.",
        f"- Research generated: {provenance['research_generated_at_utc']}.",
        f"- Manual comparison generated: {quote['generated_at_utc']}.",
        f"- Model source SHA-256: `{provenance['model_code_sha256']}`.",
        f"- Probability method: `{provenance['distribution_version']}`.",
        "",
        "These historical prediction times do not mean forecasts were actually issued then. "
        "Snapshot retrieval time is not proof that later source revisions were known pregame.",
        "",
        "## Limits",
        "",
        *[f"- {item}" for item in quote["limitations"]],
        "",
    ]
    return "\n".join(lines)


def write_quote(report_dir: Path, quote: dict[str, Any]) -> None:
    write_json(report_dir / "quote.json", quote)
    (report_dir / "quote.md").write_text(quote_markdown(quote), encoding="utf-8")
