"""Local NFL team score forecasts and chronological model comparisons."""

import os
from pathlib import Path

import polars as pl
import streamlit as st

from nfl_prop_model.team_app import team_backtest_page, team_scores_page


def main() -> None:
    st.set_page_config(page_title="Team EPA | Prediction App", page_icon="🏈", layout="wide")
    data_dir = Path(os.environ.get("NFL_PROP_DATA_DIR", "data"))
    st.sidebar.title("NFL Team Research")
    st.sidebar.caption("EPA · final scores · margins")
    page = st.sidebar.radio("Workspace", ["Team scores", "Team backtest"], key="page")
    st.caption("RESEARCH PREVIEW")
    try:
        if page == "Team scores":
            team_scores_page(data_dir)
        else:
            team_backtest_page(data_dir)
    except (OSError, ValueError, pl.exceptions.PolarsError) as error:
        st.error(str(error))
        st.info("Prepare this page's local cache from the repository terminal:")
        st.code("nfl-prop team-fetch\nnfl-prop team-build\nnfl-prop team-evaluate", language="text")
    st.divider()
    st.caption("Margins equal the difference between the displayed projected scores.")
