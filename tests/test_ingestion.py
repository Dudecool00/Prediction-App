import nflreadpy as nfl
import polars as pl
import pytest
from polars.testing import assert_frame_equal

from nfl_prop_model.cli import main
from nfl_prop_model.data.ingest_nfl import fetch_snapshot, load_snapshot, validate_seasons
from nfl_prop_model.data.storage import load_frame, read_json, store_frame


@pytest.fixture
def fake_downloads(sources, monkeypatch):
    stats, schedules = sources
    calls = []

    def player_loader(seasons, summary_level):
        assert seasons == [2023, 2024]
        assert summary_level == "week"
        calls.append("stats")
        return stats

    def schedule_loader(seasons):
        assert seasons == [2023, 2024]
        calls.append("schedules")
        return schedules

    monkeypatch.setattr(nfl, "load_player_stats", player_loader)
    monkeypatch.setattr(nfl, "load_schedules", schedule_loader)
    return calls


def test_fetch_uses_verified_cache_until_refresh(tmp_path, fake_downloads):
    first = fetch_snapshot(tmp_path, [2024, 2023, 2023])
    second = fetch_snapshot(tmp_path, [2023, 2024])
    assert fake_downloads == ["stats", "schedules"]
    assert first.manifest == second.manifest
    assert_frame_equal(first.player_stats, second.player_stats)
    fetch_snapshot(tmp_path, [2023, 2024], refresh=True)
    assert fake_downloads == ["stats", "schedules"] * 2
    assert len(list((tmp_path / "raw" / "2023_2024").glob("manifest-*.json"))) == 2


def test_corrupt_cache_fails_instead_of_silently_refetching(tmp_path, fake_downloads):
    snapshot = fetch_snapshot(tmp_path, [2023, 2024])
    raw = tmp_path / "raw" / "2023_2024"
    path = raw / snapshot.manifest["datasets"]["player_stats"]["file"]
    path.write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="checksum mismatch"):
        load_snapshot(tmp_path, [2023, 2024])
    assert len(fake_downloads) == 2


def test_content_addressed_storage_preserves_earlier_data(tmp_path):
    first = store_frame(tmp_path, "sample", pl.DataFrame({"yards": [100, 200]}))
    second = store_frame(tmp_path, "sample", pl.DataFrame({"yards": [100, 300]}))
    assert first["file"] != second["file"]
    assert load_frame(tmp_path, first)["yards"].to_list() == [100, 200]
    assert load_frame(tmp_path, second)["yards"].to_list() == [100, 300]


def test_cli_builds_offline_and_reproduces_same_table(tmp_path, fake_downloads):
    data = tmp_path / "data"
    report = tmp_path / "report"
    fetch_snapshot(data, [2023, 2024])
    args = [
        "build",
        "--seasons",
        "2023",
        "2024",
        "--data-dir",
        str(data),
        "--report-dir",
        str(report),
    ]
    assert main(args) == 0
    manifest_path = data / "processed" / "2023_2024" / "manifest.json"
    first = read_json(manifest_path)
    assert main(args) == 0
    assert first == read_json(manifest_path)
    audit = read_json(report / "2023_2024" / "audit.json")
    assert audit["counts"]["included_qb_games"] == 14
    assert audit["counts"]["included_qb_games"] == first["table"]["rows"]
    assert audit["model_results"].startswith("Not evaluated")
    assert len(fake_downloads) == 2


def test_holdout_seasons_are_not_requested(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Loader must not run for a reserved season")

    monkeypatch.setattr(nfl, "load_player_stats", forbidden)
    with pytest.raises(ValueError, match="reserved"):
        fetch_snapshot(tmp_path, [2025])
    with pytest.raises(ValueError):
        validate_seasons([])


def test_partial_source_download_is_rejected(tmp_path, sources, monkeypatch):
    stats, schedules = sources
    monkeypatch.setattr(
        nfl, "load_player_stats", lambda *a, **kw: stats.filter(pl.col("season") == 2023)
    )
    monkeypatch.setattr(nfl, "load_schedules", lambda *a, **kw: schedules)
    with pytest.raises(ValueError, match="exactly the requested seasons"):
        fetch_snapshot(tmp_path, [2023, 2024])
    assert not (tmp_path / "raw" / "2023_2024" / "manifest.json").exists()


def test_build_without_cache_returns_actionable_error(tmp_path, capsys):
    with pytest.raises(SystemExit) as error:
        main(["build", "--data-dir", str(tmp_path)])
    assert error.value.code == 1
    assert "manifest.json" in capsys.readouterr().err
