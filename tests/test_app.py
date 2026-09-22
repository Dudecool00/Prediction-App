from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest


def app_test():
    return AppTest.from_file(Path(__file__).parents[1] / "app.py", default_timeout=20)


def test_missing_cache_has_setup_instructions(tmp_path, monkeypatch):
    monkeypatch.setenv("NFL_PROP_DATA_DIR", str(tmp_path))
    app = app_test().run()
    assert not app.exception
    assert app.error
    assert "nfl-prop research" in app.code[0].value


def test_manual_comparison_validation_and_append_only_save(saved_research, monkeypatch):
    data, _, _ = saved_research
    monkeypatch.setenv("NFL_PROP_DATA_DIR", str(data))
    app = app_test().run()
    assert not app.exception and not app.error
    assert app.metric[0].value == "200.0 yd"
    app.button[0].click().run()
    assert not app.exception
    assert app.button(key="save_snapshot")
    app.button(key="save_snapshot").click().run()
    first = {p.name: p.read_bytes() for p in (data / "journal").glob("*.json")}
    assert len(first) == 1
    app.button(key="save_snapshot").click().run()
    assert len(list((data / "journal").glob("*.json"))) == 2
    assert all((data / "journal" / name).read_bytes() == content for name, content in first.items())
    app.text_input(key="over_odds").set_value("0")
    app.button[0].click().run()
    assert app.error and not app.exception
    assert "quote" not in app.session_state
    app.radio(key="page").set_value("Saved snapshots").run()
    assert not app.exception
    assert app.selectbox(key="saved_record")


def test_changing_game_clears_previous_market(saved_research, monkeypatch):
    import polars as pl

    from nfl_prop_model.data.storage import load_frame, store_frame, write_json

    data, directory, manifest = saved_research
    for name in ("predictions", "features"):
        frame = load_frame(directory, manifest["artifacts"][name])
        duplicate = frame.with_columns(pl.lit("2023_02_C_D").alias("game_id"))
        if name == "features":
            duplicate = duplicate.with_columns(
                pl.lit("C").alias("team"), pl.lit("D").alias("opponent_team")
            )
        manifest["artifacts"][name] = store_frame(directory, name, pl.concat([frame, duplicate]))
    write_json(directory / "manifest.json", manifest)
    monkeypatch.setenv("NFL_PROP_DATA_DIR", str(data))
    app = app_test().run()
    app.button[0].click().run()
    assert "quote" in app.session_state
    app.selectbox(key="game").set_value("2023_02_C_D").run()
    assert not app.exception and not app.error
    assert "quote" not in app.session_state
