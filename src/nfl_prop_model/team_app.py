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
EXPERIMENT_LABELS = {
    **MODEL_LABELS,
    "ridge_epa_scoring_scores": "EPA + scoring history",
    "ridge_epa_scoring_volume_scores": "EPA + scoring + play volume",
    "ridge_opponent_epa_scores": "EPA + opponent adjustment",
    "ridge_epa_scoring_volume_opponent_scores": "EPA + scoring + volume + opponents",
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
    with st.expander("Feature experiments"):
        team_experiments_panel(data_dir, horizon)
    with st.expander("Chronological tuning"):
        team_tuning_panel(data_dir, horizon)
    with st.expander("QB and injury context"):
        team_context_panel(data_dir, horizon)
    with st.expander("Play-by-play and schedule strength"):
        team_playbook_panel(data_dir, horizon)


def team_playbook_panel(data_dir: Path, horizon: str) -> None:
    from nfl_prop_model.data.schemas import DataQualityError
    from nfl_prop_model.modeling.team_playbook import load_playbook_evaluation

    try:
        report, predictions = load_playbook_evaluation(data_dir)
    except (FileNotFoundError, DataQualityError, ValueError) as exc:
        st.info(f"Prepare the comparison with nfl-prop team-playbook-evaluate. {exc}")
        return
    labels = {
        **MODEL_LABELS,
        "ridge_epa_play_scores": "EPA + play tendencies",
        "ridge_epa_schedule_scores": "EPA + opponent strength + schedule",
        "ridge_epa_play_schedule_scores": "EPA + plays + opponent strength + schedule",
    }
    metrics = pl.DataFrame(report["metrics"]).filter(pl.col("horizon") == horizon)
    st.dataframe(
        metrics.select(
            pl.col("model").replace(labels).alias("Model"),
            pl.col("season").cast(pl.String).fill_null("All").alias("Season"),
            pl.col("games").alias("Games"),
            pl.col("margin_mae").round(3).alias("Margin MAE"),
            pl.col("score_mae").round(3).alias("Score MAE"),
            pl.col("total_mae").round(3).alias("Total MAE"),
        ),
        hide_index=True,
        width="stretch",
    )
    model = st.selectbox(
        "Play and schedule model",
        list(labels),
        index=5,
        format_func=labels.__getitem__,
        key="team_playbook_model",
    )
    season = st.selectbox(
        "Play and schedule season",
        sorted(predictions["season"].unique().to_list()),
        key="team_playbook_season",
    )
    rows = predictions.filter(
        (pl.col("horizon") == horizon) & (pl.col("model") == model) & (pl.col("season") == season)
    )
    week = st.selectbox(
        "Play and schedule week", sorted(rows["week"].unique().to_list()), key="team_playbook_week"
    )
    rows = rows.filter(pl.col("week") == week)
    st.dataframe(score_table(rows), hide_index=True, width="stretch")
    row = show_score_detail(rows, key="team_playbook_history")
    features = load_frame(
        data_dir / "processed" / "team_playbook_scores", report["artifacts"]["features"]
    )
    feature = features.filter(
        (pl.col("game_id") == row["game_id"]) & (pl.col("horizon") == horizon)
    ).row(0, named=True)
    st.dataframe(
        pl.DataFrame(
            [
                {
                    "Team": row[f"{side}_team"],
                    "Offense strength EPA/play": feature[f"{side}_off_strength"],
                    "Defense strength EPA/play allowed": feature[f"{side}_def_strength"],
                    "Faced offense EPA/play (season)": feature[f"{side}_sos_off_season"],
                    "Faced defense EPA/play allowed (season)": feature[f"{side}_sos_def_season"],
                    "Prior offensive success rate": feature[f"{side}_off_success_prior"],
                    "Prior offensive sack rate": feature[f"{side}_off_sack_prior"],
                    "Prior offensive turnover rate": feature[f"{side}_off_turnover_prior"],
                }
                for side in ("away", "home")
            ]
        ),
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "Prior success, explosive plays, sacks and turnovers. Schedule strength uses "
        "previously faced opponents rated at the forecast cutoff. Higher opposing offense "
        "EPA is harder; lower opposing defense EPA allowed is harder. "
        "Development comparison; published forecasts retain current EPA."
    )


def team_experiments_panel(data_dir: Path, horizon: str) -> None:
    from nfl_prop_model.data.schemas import DataQualityError
    from nfl_prop_model.modeling.team_experiments import load_team_experiments

    try:
        report, _ = load_team_experiments(data_dir)
    except (FileNotFoundError, DataQualityError, ValueError) as exc:
        st.info(f"Prepare the comparison with nfl-prop team-experiment. {exc}")
        return
    st.write(
        "Fixed feature comparisons on the same games and forecast cutoffs. "
        "MAE is average absolute error in points; lower is better."
    )
    metrics = pl.DataFrame(report["metrics"]).filter(
        (pl.col("scope") == "overall") & (pl.col("horizon") == horizon)
    )
    st.dataframe(
        metrics.sort("margin_mae").select(
            pl.col("model").replace(EXPERIMENT_LABELS).alias("Model"),
            pl.col("games").alias("Games"),
            pl.col("margin_mae").round(3).alias("Margin MAE"),
            pl.col("score_mae").round(3).alias("Score MAE"),
            pl.col("total_mae").round(3).alias("Total MAE"),
        ),
        hide_index=True,
        width="stretch",
    )
    seasonal = pl.DataFrame(report["metrics"]).filter(
        (pl.col("scope") == "season") & (pl.col("horizon") == horizon)
    )
    st.dataframe(
        seasonal.select(
            pl.col("model").replace(EXPERIMENT_LABELS).alias("Model"),
            pl.col("season").alias("Season"),
            pl.col("margin_mae").round(3).alias("Margin MAE"),
            pl.col("score_mae").round(3).alias("Score MAE"),
        ),
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "Development comparison only; published forecasts retain the original EPA model. "
        "Choosing a candidate from these results requires later prospective validation."
    )


def team_tuning_panel(data_dir: Path, horizon: str) -> None:
    from nfl_prop_model.data.schemas import DataQualityError
    from nfl_prop_model.modeling.team_tuning import TUNED_MODEL, load_team_tuning

    try:
        report, _ = load_team_tuning(data_dir)
    except (FileNotFoundError, DataQualityError, ValueError) as exc:
        st.info(f"Prepare the comparison with nfl-prop team-tune. {exc}")
        return
    st.write(
        "Each season's setting is selected using the preceding two seasons, "
        "then held fixed for that season. Both forecast timings use the same choice."
    )
    metrics = pl.DataFrame(report["metrics"]).filter(pl.col("horizon") == horizon)
    st.dataframe(
        metrics.select(
            pl.col("model")
            .replace(
                {"ridge_epa_scores": "Current EPA", TUNED_MODEL: "Selected on earlier seasons"}
            )
            .alias("Model"),
            pl.col("season").cast(pl.String).fill_null("All").alias("Season"),
            pl.col("games").alias("Games"),
            pl.col("margin_mae").round(3).alias("Margin MAE"),
            pl.col("score_mae").round(3).alias("Score MAE"),
            pl.col("total_mae").round(3).alias("Total MAE"),
        ),
        hide_index=True,
        width="stretch",
    )
    profiles = {profile["name"]: profile for profile in report["policy"]["profiles"]}
    selections = []
    for selection in report["selections"]:
        profile = profiles[selection["selected_profile"]]
        selections.append(
            {
                "Forecast season": selection["season"],
                "Validation seasons": "–".join(
                    str(year) for year in selection["validation_seasons"]
                ),
                "Training seasons": str(profile["training_seasons"] or "All"),
                "Recent games": profile["rating"]["recent_games"],
                "Recent decay": profile["rating"]["recent_decay"],
                "Prior equivalent plays": profile["rating"]["prior_plays"],
            }
        )
    st.dataframe(pl.DataFrame(selections), hide_index=True, width="stretch")
    st.caption(
        "Development simulation with settings locked before Week 1. "
        "Published forecasts retain the current model pending assessment. "
        "2025 is already accessed."
    )


def team_context_panel(data_dir: Path, horizon: str) -> None:
    from nfl_prop_model.data.schemas import DataQualityError
    from nfl_prop_model.modeling.team_context import load_context_evaluation

    try:
        report, _ = load_context_evaluation(data_dir)
    except (FileNotFoundError, DataQualityError, ValueError) as exc:
        st.info(f"Prepare the comparison with nfl-prop team-context-evaluate. {exc}")
        return
    labels = {
        **MODEL_LABELS,
        "ridge_epa_qb_history_scores": "EPA + prior QB history",
        "ridge_epa_injury_scores": "EPA + injury observations",
        "ridge_epa_qb_injury_scores": "EPA + QB history + injuries",
    }
    metrics = pl.DataFrame(report["metrics"]).filter(pl.col("horizon") == horizon)
    st.dataframe(
        metrics.select(
            pl.col("model").replace(labels).alias("Model"),
            pl.col("season").cast(pl.String).fill_null("All").alias("Season"),
            pl.col("games").alias("Games"),
            pl.col("margin_mae").round(3).alias("Margin MAE"),
            pl.col("score_mae").round(3).alias("Score MAE"),
            pl.col("total_mae").round(3).alias("Total MAE"),
        ),
        hide_index=True,
        width="stretch",
    )
    coverage = next(row for row in report["coverage"] if row["horizon"] == horizon)
    observed = coverage["home_observed_report_games"] + coverage["away_observed_report_games"]
    known = coverage["home_qb_known_games"] + coverage["away_qb_known_games"]
    st.write(
        f"Prior primary passer known in {known}/{2 * coverage['games']} team slots; "
        f"partial injury observations in {observed}/{2 * coverage['games']} slots."
    )
    st.caption(
        "Prior primary passer is a proxy, with no current-game starter labels. Injury rows "
        "require modification plus 24 hours before the forecast. Missing observations are unknown. "
        "Rest flags use explicit report reasons; coaching announcements remain outside this data. "
        "Historical publication/revisions are not reconstructed. "
        "Published forecasts retain current EPA."
    )
