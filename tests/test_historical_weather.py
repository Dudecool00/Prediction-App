import copy
import math
import urllib.error
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from nfl_prop_model.data import gfs_archive as gfs
from nfl_prop_model.data import team_historical_weather as weather
from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.storage import read_json, store_frame, write_json

FIXTURE = Path(__file__).parent / "fixtures" / "gfs"
KICKOFF = datetime(2022, 9, 9, tzinfo=UTC)
CUTOFF = datetime(2022, 9, 6, 15, tzinfo=UTC)


@pytest.fixture
def point():
    return (
        (FIXTURE / "point.ascii").read_bytes(),
        (FIXTURE / "selected.das").read_bytes(),
        gfs.forecast_selection(KICKOFF, CUTOFF),
    )


def test_strict_publication_bound_and_valid_time_rounding():
    cutoff = datetime(2022, 9, 6, 12, 10, tzinfo=UTC)
    s = gfs.forecast_selection(KICKOFF, cutoff)
    assert s["run_utc"] == "2022-09-06T00:00:00+00:00"
    s = gfs.forecast_selection(KICKOFF, cutoff + timedelta(microseconds=1))
    assert s["run_utc"] == "2022-09-06T06:00:00+00:00"
    for minute, expected in ((89, 0), (90, 3), (91, 3)):
        s = gfs.forecast_selection(KICKOFF + timedelta(minutes=minute), CUTOFF)
        assert datetime.fromisoformat(s["valid_utc"]).hour == expected
        assert abs(s["valid_offset_minutes"]) <= 90


def test_january_uses_forecast_calendar_year_and_no_analysis():
    s = gfs.forecast_selection(
        datetime(2023, 1, 8, 18, tzinfo=UTC), datetime(2023, 1, 3, 16, tzinfo=UTC)
    )
    assert "/2023/20230103/" in s["url"]
    assert s["lead_hours"] > 0
    with pytest.raises(DataQualityError, match="timezone-aware"):
        gfs.forecast_selection(KICKOFF.replace(tzinfo=None), CUTOFF)
    with pytest.raises(DataQualityError, match="precede"):
        gfs.forecast_selection(KICKOFF, KICKOFF)
    with pytest.raises(DataQualityError, match="3-240h"):
        gfs.forecast_selection(KICKOFF, CUTOFF - timedelta(days=20))


def test_real_point_fixture_conversion_and_units(point):
    payload, metadata, selection = point
    result = gfs.parse_point(payload, metadata, selection, 33.95, -118.34)
    assert result["temperature_c"] == pytest.approx(31.25253)
    assert result["wind_speed_m_s"] == pytest.approx(math.hypot(5.1768236, 0.44539794))
    assert result["precipitation_rate_mm_h"] == 0
    assert (result["grid_latitude"], result["grid_longitude"]) == (34, 241.75)
    rain = payload.replace(b"[0][0], 0.0", b"[0][0], 0.0001")
    assert gfs.parse_point(rain, metadata, selection, 33.95, -118.34)[
        "precipitation_rate_mm_h"
    ] == pytest.approx(0.36)


@pytest.mark.parametrize(
    "old,new",
    [
        (b'"K"', b'"degC"'),
        (b'"m/s"', b'"km/h"'),
        (b'"kg.m-2.s-1"', b'"mm"'),
        (b'"Forecast"', b'"Analysis"'),
        (b'"UnknownStatType--1"', b'"Average"'),
        (b"2022-09-06T06:00:00Z", b"2022-09-06T00:00:00Z"),
        (b'"VAR_0-1-7_L1"', b'"VAR_0-1-8_L1"'),
    ],
)
def test_metadata_changes_fail_closed(point, old, new):
    payload, metadata, selection = point
    with pytest.raises(DataQualityError):
        gfs.parse_point(payload, metadata.replace(old, new), selection, 33.95, -118.34)


@pytest.mark.parametrize(
    "old,new",
    [
        (b"66.0", b"69.0"),
        (b"34.0", b"34.25"),
        (b"241.75", b"241.5"),
        (b"\n2.0\n", b"\n80.0\n"),
        (b"\n10.0\n", b"\n20.0\n"),
        (b"reftime, 0.0", b"reftime, 6.0"),
        (b"f066.grib2", b"f069.grib2"),
        (b"[0][0], 0.0", b"[0][0], -0.001"),
        (b"[0][0][0], 304.40253", b"[0][0][0], 9999"),
    ],
)
def test_wrong_time_cell_height_dataset_or_value_rejected(point, old, new):
    payload, metadata, selection = point
    with pytest.raises(DataQualityError):
        gfs.parse_point(payload.replace(old, new), metadata, selection, 33.95, -118.34)


def test_missing_is_not_zero_and_response_bounds(point):
    payload, metadata, selection = point
    missing = payload.replace(b"5.1768236", b"NaN")
    result = gfs.parse_point(missing, metadata, selection, 33.95, -118.34)
    assert result["wind_speed_m_s"] is None
    assert result["precipitation_rate_mm_h"] == 0
    for bad in (
        payload + b"invalid",
        payload + b"x" * 20_000,
        payload.replace(b"[1][1][1]", b"[2][1][1]"),
    ):
        with pytest.raises(DataQualityError):
            gfs.parse_point(bad, metadata, selection, 33.95, -118.34)


def test_grid_dateline_poles_and_encoded_projection(point):
    assert gfs.grid_point(90, -180) == (0, 720)
    assert gfs.grid_point(-90, 180) == (720, 720)
    assert gfs.grid_point(0, -0.01) == (360, 0)
    with pytest.raises(DataQualityError):
        gfs.grid_point(math.nan, 0)
    assert "%5B0%5D" in gfs.point_url(point[2], 33.95, -118.34)
    assert "[" not in gfs.point_url(point[2], 33.95, -118.34)


def test_all_international_overrides_and_unknown_roof():
    expected = [
        "SAO00",
        "announced-dublin",
        "LON02",
        "LON02",
        "LON00",
        "announced-berlin",
        "announced-madrid",
    ]
    assert [
        weather.venue_key({"game_id": k, "stadium_id": "wrong-home"}) for k in weather.OVERRIDES
    ] == expected
    assert weather.REGISTRY["announced-madrid"][2] is None


def test_receipt_resume_corruption_and_bounded_storage(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(weather, "_download", lambda url, limit: calls.append(url) or b"sample")
    assert weather._fetch(tmp_path, "https://example.test/point", 100) == b"sample"
    assert weather._fetch(tmp_path, "https://example.test/point", 100) == b"sample"
    assert calls == ["https://example.test/point"]
    body = next(tmp_path.glob("*.gz"))
    body.write_bytes(b"corrupt")
    with pytest.raises(DataQualityError, match="checksum"):
        weather._fetch(tmp_path, "https://example.test/point", 100)
    monkeypatch.setattr(weather, "MAX_CACHE_BYTES", 1)
    with pytest.raises(DataQualityError, match="budget"):
        weather._fetch(tmp_path, "https://example.test/second", 100)


def test_failed_sources_explicit_and_retry_preserves_attempt(tmp_path, monkeypatch):
    def unavailable(*args):
        raise urllib.error.URLError("unavailable")

    monkeypatch.setattr(weather, "_download", unavailable)
    url = "https://example.test/missing"
    assert weather._fetch(tmp_path, url, 100) is None
    monkeypatch.setattr(weather, "_download", lambda *args: b"good")
    assert weather._fetch(tmp_path, url, 100) is None
    assert weather._fetch(tmp_path, url, 100, retry_failed=True) == b"good"
    receipt = read_json(weather._receipt_path(tmp_path, url))
    assert receipt["previous_attempts"][0]["status"] == "unavailable"


@pytest.fixture
def canonical(tmp_path):
    rows = []
    for year, count in ((2022, 271), (2023, 272), (2024, 272), (2025, 272)):
        for i in range(count):
            rows.append(
                {
                    "game_id": f"{year}_{i}_A_B",
                    "season": year,
                    "week": i // 16 + 1,
                    "home_team": "B",
                    "away_team": "A",
                    "kickoff_utc": datetime(year, 9, 9, tzinfo=UTC),
                    "home_score": 999,
                    "away_score": 0,
                    "actual_starter": "FORBIDDEN",
                }
            )
    games = pl.DataFrame(rows)
    directory = tmp_path / "processed" / "team_epa"
    write_json(
        directory / "manifest.json",
        {
            "pipeline_source_sha256": weather.team_source_hash(),
            "artifacts": {"games": store_frame(directory, "games", games)},
        },
    )
    directory = tmp_path / "raw" / "team_pregame"
    venues = games.select("game_id", "season", "home_team", "away_team").with_columns(
        pl.lit("DET00").alias("stadium_id")
    )
    write_json(directory / "audit.json", {"venues": store_frame(directory, "venues", venues)})
    return tmp_path, games


def test_schedule_projection_and_full_cohort_guard(canonical):
    data, games = canonical
    loaded, _ = weather.schedule(data)
    assert loaded.height == 1087
    assert not {"home_score", "away_score", "actual_starter"} & set(loaded.columns)
    directory = data / "processed" / "team_epa"
    manifest = read_json(directory / "manifest.json")
    manifest["artifacts"]["games"] = store_frame(directory, "games", games.head(1086))
    write_json(directory / "manifest.json", manifest)
    with pytest.raises(DataQualityError, match="1087"):
        weather.schedule(data)


def test_all_missing_roofs_and_moved_rows_retained(tmp_path, monkeypatch):
    rows = [
        {
            "game_id": game_id,
            "season": 2022,
            "week": 11,
            "home_team": "BUF",
            "away_team": "CLE",
            "kickoff_utc": datetime(2022, 11, 20, 18, tzinfo=UTC),
            "stadium_id": venue,
        }
        for game_id, venue in (
            ("indoor", "DET00"),
            ("roof", "LAX01"),
            ("missing", "BUF00"),
            ("2022_11_CLE_BUF", "BUF00"),
        )
    ]
    monkeypatch.setattr(weather, "_read_receipt", lambda *args: None)
    result = weather.build_rows(
        pl.DataFrame(rows),
        {"points": {"BUF00": {"latitude": 42.7, "longitude": -78.7}}},
        tmp_path,
        offline=True,
    )
    assert len(result) == 8
    assert set(r["status"] for r in result) == {
        "fixed_indoor",
        "roof_unknown",
        "moved_venue_excluded",
        "source_unavailable",
    }
    assert all(r["temperature_c"] is None for r in result)
    assert all(r["exposure"] == 0 for r in result if r["status"] == "fixed_indoor")


def test_offline_audit_rebuilds_and_guards_policy(canonical, monkeypatch):
    data, _ = canonical
    monkeypatch.setattr(weather, "_points", lambda *args, **kwargs: {"points": {}})
    weather.collect_historical_weather(data)
    monkeypatch.setattr(
        weather, "_download", lambda *args: pytest.fail("Offline audit used network")
    )
    report = weather.audit_historical_weather(data, data / "report")
    assert report["records"] == 2174 and report["model_fits"] == 0
    assert report["status"] == {"fixed_indoor": 2174}
    path = data / "raw" / "team_historical_weather" / "manifest.json"
    manifest = read_json(path)
    bad = copy.deepcopy(manifest)
    bad["policy"]["run"] = "latest observed weather"
    write_json(path, bad)
    with pytest.raises(DataQualityError, match="mismatch"):
        weather.audit_historical_weather(data, data / "report")
