"""Team-first score and margin research pages for the existing Streamlit app."""

from pathlib import Path
from typing import Any

import polars as pl
import streamlit as st

from nfl_prop_model.data.storage import load_frame, read_json
from nfl_prop_model.modeling.team_scores import load_team_evaluation

HORIZON_LABELS = {"early_week": "Early week", "pregame_refresh": "Pre-kickoff refresh"}
MODEL_LABELS = {
    "league_scores": "League scoring baseline",
    "prior_team_scores": "Prior team scoring baseline",
    "ridge_epa_scores": "EPA score model",
}


def score_table(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.select(
        pl.col("week").alias("Week"),
        pl.col("away_team").alias("Away"),
        pl.col("predicted_away_score").alias("Away score"),
        pl.col("home_team").alias("Home"),
        pl.col("predicted_home_score").alias("Home score"),
        pl.col("predicted_away_margin").alias("Away margin"),
        pl.col("predicted_home_margin").alias("Home margin"),
        pl.col("predicted_total").alias("Total"),
    )


def show_score_detail(predictions: pl.DataFrame, *, key: str) -> dict[str, Any]:
    labels = {
        row["game_id"]: f"{row['away_team']} at {row['home_team']} · Week {row['week']}"
        for row in predictions.to_dicts()
    }
    game_id = st.selectbox(
        "Matchup", list(labels), format_func=labels.__getitem__, key=f"{key}_game"
    )
    row: dict[str, Any] = predictions.filter(pl.col("game_id") == game_id).row(0, named=True)
    away, home, total = st.columns(3)
    away.metric(
        row["away_team"],
        f"{row['predicted_away_score']:.1f}",
        f"{row['predicted_away_margin']:+.1f} margin",
    )
    home.metric(
        row["home_team"],
        f"{row['predicted_home_score']:.1f}",
        f"{row['predicted_home_margin']:+.1f} margin",
    )
    total.metric("Projected total", f"{row['predicted_total']:.1f}")
    st.caption(
        "Scores are projected averages. The margin is the difference between the displayed scores."
    )
    return row


def team_scores_page(data_dir: Path) -> None:
    st.title("Weekly team scores")
    st.write("Projected final scores and signed margins from prior offensive and defensive EPA.")
    paths = sorted(
        (data_dir / "journal" / "team_scores").glob("snapshot-*/manifest.json"), reverse=True
    )
    if not paths:
        st.info("Prepare the team data, then save an early-week forecast to populate the slate.")
        st.code(
            "nfl-prop team-fetch\nnfl-prop team-build\nnfl-prop team-evaluate\n"
            "# Include the current season when preparing upcoming forecasts.\n"
            "nfl-prop team-forecast",
            language="text",
        )
        return
    manifests = {str(path): read_json(path) for path in paths}
    selected = st.selectbox(
        "Forecast version",
        list(manifests),
        key="team_snapshot",
        format_func=lambda value: (
            f"{HORIZON_LABELS[manifests[value]['horizon']]} · {manifests[value]['as_of_utc']}"
        ),
    )
    report = manifests[selected]
    directory = Path(selected).parent
    predictions = load_frame(directory, report["artifacts"]["predictions"])
    st.caption(
        f"Forecast saved: {report['as_of_utc']} · {report['training_games']:,} earlier games"
    )
    st.dataframe(score_table(predictions), hide_index=True, width="stretch")
    row = show_score_detail(predictions, key="team_live")
    features = load_frame(directory, report["artifacts"]["features"])
    feature = features.filter(pl.col("game_id") == row["game_id"]).row(0, named=True)
    st.dataframe(
        pl.DataFrame(
            [
                {
                    "Team": row[f"{side}_team"],
                    "Recent offense EPA/play": feature[f"{side}_off_epa_recent"],
                    "Season offense EPA/play": feature[f"{side}_off_epa_season"],
                    "Recent defense EPA/play allowed": feature[f"{side}_def_epa_recent"],
                    "Season defense EPA/play allowed": feature[f"{side}_def_epa_season"],
                }
                for side in ("away", "home")
            ]
        ),
        hide_index=True,
        width="stretch",
    )
    st.caption("Higher offensive EPA is better; lower defensive EPA allowed is better.")
    st.info("EPA research forecast. QB, injury and weather adjustments and intervals are pending.")


def team_backtest_page(data_dir: Path) -> None:
    st.title("Team score and margin backtest")
    report, predictions = load_team_evaluation(data_dir)
    st.caption(
        "Historical simulation. Early-week and pre-kickoff forecasts are evaluated separately."
    )
    horizon = st.selectbox(
        "Forecast timing",
        list(HORIZON_LABELS),
        format_func=HORIZON_LABELS.__getitem__,
        key="team_horizon",
    )
    metrics = pl.DataFrame(report["metrics"]).filter(
        (pl.col("scope") == "overall") & (pl.col("horizon") == horizon)
    )
    st.dataframe(
        metrics.select(
            pl.col("model").replace(MODEL_LABELS).alias("Model"),
            pl.col("games").alias("Games"),
            pl.col("score_mae").round(2).alias("Score MAE"),
            pl.col("margin_mae").round(2).alias("Margin MAE"),
            pl.col("margin_rmse").round(2).alias("Margin RMSE"),
            pl.col("total_mae").round(2).alias("Total MAE"),
        ),
        hide_index=True,
        width="stretch",
    )
    season = st.selectbox(
        "Season", sorted(predictions["season"].unique().to_list()), key="team_season"
    )
    model = st.selectbox(
        "Model", list(MODEL_LABELS), index=2, format_func=MODEL_LABELS.__getitem__, key="team_model"
    )
    rows = predictions.filter(
        (pl.col("horizon") == horizon) & (pl.col("season") == season) & (pl.col("model") == model)
    )
    week = st.selectbox("Week", sorted(rows["week"].unique().to_list()), key="team_week")
    rows = rows.filter(pl.col("week") == week)
    st.dataframe(score_table(rows), hide_index=True, width="stretch")
    row = show_score_detail(rows, key="team_history")
    st.write(
        f"Actual final: {row['away_team']} {row['away_score']:.0f} – "
        f"{row['home_team']} {row['home_score']:.0f}; "
        f"home margin {row['target_home_margin']:+.0f}."
    )
    st.caption(
        "MAE is average absolute error in points. Score MAE includes both team scores; "
        "margin MAE counts each game once. "
        "Historical EPA revisions are not reconstructed. 2025 has already been accessed."
    )
