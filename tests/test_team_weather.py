import copy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import polars as pl
import pytest

from nfl_prop_model.cli import main
from nfl_prop_model.data import team_weather as weather
from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.storage import read_json, sha256_file, store_frame, write_json


@pytest.fixture
def live_weather(monkeypatch, tmp_path):
    now = datetime(2026, 10, 13, 16, tzinfo=UTC)
    kickoff = datetime(2026, 10, 16, 0, 15, tzinfo=UTC)
    clock = [now]
    game = {
        "game_id": "2026_06_SEA_DEN",
        "season": 2026,
        "week": 6,
        "home_team": "DEN",
        "away_team": "SEA",
        "kickoff_utc": kickoff,
        "espn": "123",
        "stadium_id": "denver",
        "stadium": "Denver stadium",
    }
    hourly = {
        "time": [t.strftime("%Y-%m-%dT%H:00") for t in weather.game_hours(kickoff)],
        **{field: [10.0 if field == "temperature_2m" else 1.0] * 5 for field in weather.FIELDS},
    }
    response = {
        "utc_offset_seconds": 0,
        "hourly_units": {"time": "iso8601", **weather.UNITS},
        "hourly": hourly,
    }
    requests = []
    monkeypatch.setattr(weather, "_now", lambda: clock[0])
    monkeypatch.setattr(
        weather, "_fresh_slate", lambda *args: (pl.DataFrame([game]), {"test": True})
    )
    monkeypatch.setattr(
        weather, "venue_point", lambda *args: {"latitude": 39.74, "longitude": -105.02}
    )

    def fake_json(url):
        requests.append(url)
        return copy.deepcopy(response)

    monkeypatch.setattr(weather, "_json", fake_json)
    return tmp_path, clock, game, response, requests


def test_run_cycle_and_chicago_date_guard(live_weather, monkeypatch):
    data, clock, _, _, requests = live_weather
    assert weather.pinned_run(datetime(2026, 10, 14, 0, 9, tzinfo=UTC)) == datetime(
        2026, 10, 13, 12, tzinfo=UTC
    )
    assert weather.pinned_run(datetime(2026, 10, 14, 0, 10, tzinfo=UTC)) == datetime(
        2026, 10, 13, 18, tzinfo=UTC
    )
    with pytest.raises(DataQualityError, match="timezone-aware"):
        weather.pinned_run(datetime(2026, 10, 14))
    clock[0] = datetime(2026, 10, 14, 0, 10, tzinfo=UTC)  # October 13 in Chicago.
    weather.collect_weather(data, target=date(2026, 10, 13))
    requests.clear()
    monkeypatch.setattr(
        weather, "_fresh_slate", lambda *args: pytest.fail("Past request fetched live data")
    )
    with pytest.raises(DataQualityError, match="cannot be backdated"):
        weather.collect_weather(data, target=date(2025, 9, 7))
    assert not requests


def test_compact_versions_and_strict_as_of_receipts(live_weather):
    data, clock, game, response, requests = live_weather
    first = weather.collect_weather(data)
    original = first.read_bytes()
    frame = weather.load_weather_capture(first)[1]
    assert frame.height == 5
    assert frame["valid_time_utc"].to_list() == weather.game_hours(game["kickoff_utc"])
    query = parse_qs(urlparse(requests[0]).query)
    assert query["models"] == ["gfs_global"] and query["run"] == ["2026-10-13T06:00"]
    assert "start_hour" not in query and "start_date" not in query
    assert "hourly" not in read_json(first)  # No full point response retained.
    cutoff = clock[0]
    assert weather.weather_as_of(data, game["game_id"], game["kickoff_utc"], cutoff)[0] is None
    clock[0] += timedelta(minutes=1)
    response["hourly"]["temperature_2m"] = [20.0] * 5
    second = weather.collect_weather(data, horizon="pregame_refresh")
    third = weather.collect_weather(data, horizon="pregame_refresh")
    assert len({first, second, third}) == 3 and first.read_bytes() == original
    selected, receipt = weather.weather_as_of(
        data, game["game_id"], game["kickoff_utc"], cutoff + timedelta(seconds=30)
    )
    assert selected["temperature_2m"].to_list() == [10.0] * 5
    assert receipt["manifest"] == str(first)
    selected, _ = weather.weather_as_of(
        data, game["game_id"], game["kickoff_utc"], clock[0] + timedelta(seconds=1)
    )
    assert selected["temperature_2m"].to_list() == [20.0] * 5
    assert (
        weather.weather_as_of(
            data, game["game_id"], game["kickoff_utc"] + timedelta(hours=1), clock[0]
        )[0]
        is None
    )


@pytest.mark.parametrize("defect", ["units", "duplicate", "missing", "negative", "nan", "late"])
def test_invalid_provider_values_are_preserved_as_unavailable(live_weather, defect):
    data, clock, game, response, _ = live_weather
    if defect == "units":
        response["hourly_units"]["wind_speed_10m"] = "km/h"
    elif defect == "duplicate":
        response["hourly"]["time"][1] = response["hourly"]["time"][0]
    elif defect == "missing":
        response["hourly"]["time"][0] = "2026-10-15T23:00"
    elif defect in {"negative", "nan"}:
        response["hourly"]["wind_speed_10m"][0] = -1 if defect == "negative" else float("nan")
    else:
        clock[0] = game["kickoff_utc"]
    path = weather.collect_weather(data)
    manifest, frame = weather.load_weather_capture(path)
    assert frame.is_empty() and manifest["games"][0]["status"] == "unavailable"
    assert manifest["games"][0]["reason"]


def test_unknown_measurements_remain_null_and_metadata_cannot_backdate(live_weather):
    data, _, _, response, _ = live_weather
    response["hourly"]["wind_speed_10m"][0] = None
    path = weather.collect_weather(data)
    report, frame = weather.load_weather_capture(path)
    assert frame["wind_speed_10m"][0] is None and report["games"][0]["status"] == "partial"
    report["games"][0]["captured_at_utc"] = "2026-10-13T15:59:00+00:00"
    write_json(path, report)
    with pytest.raises(DataQualityError, match="timing"):
        weather.load_weather_capture(path)
    report["games"][0]["captured_at_utc"] = "2026-10-13T16:00:01+00:00"
    report["created_at_utc"] = "2026-10-13T16:01:00+00:00"
    write_json(path, report)
    with pytest.raises(DataQualityError, match="metadata/frame"):
        weather.load_weather_capture(path)


def test_corrupt_weather_artifact_fails_visibly(live_weather):
    data, _, _, _, _ = live_weather
    path = weather.collect_weather(data)
    report = read_json(path)
    (path.parent / report["artifact"]["file"]).write_bytes(b"broken")
    with pytest.raises(ValueError, match="checksum"):
        weather.load_weather_capture(path)


def test_venue_validation_handles_neutral_site_and_state_ambiguity(tmp_path, monkeypatch):
    kickoff = datetime(2026, 10, 18, 13, 30, tzinfo=UTC)
    game = {"espn": "123", "kickoff_utc": kickoff, "home_team": "JAX", "away_team": "HOU"}
    venue = {"fullName": "Wembley Stadium", "address": {"city": "London", "country": "England"}}
    summary = {
        "header": {
            "competitions": [
                {
                    "date": kickoff.isoformat(),
                    "competitors": [
                        {"homeAway": "home", "team": {"abbreviation": "JAX"}},
                        {"homeAway": "away", "team": {"abbreviation": "HOU"}},
                    ],
                }
            ]
        },
        "gameInfo": {"venue": venue},
    }
    result = {
        "results": [
            {
                "name": "London",
                "country_code": "GB",
                "latitude": 51.51,
                "longitude": -0.12,
                "population": 1000,
            }
        ]
    }
    requests = []

    def source(url):
        requests.append(url)
        return summary if "espn.com" in url else result

    monkeypatch.setattr(weather, "_json", source)
    point = weather.venue_point(tmp_path, game)
    assert point["country"] == "GB" and point["city"] == "London"
    assert "countryCode=GB" in requests[-1]
    requests.clear()
    weather.venue_point(tmp_path, game)
    assert len(requests) == 1  # Verify live venue again, reuse geocoding.
    summary["header"]["competitions"][0]["date"] = (kickoff + timedelta(hours=1)).isoformat()
    with pytest.raises(DataQualityError, match="disagrees"):
        weather.venue_point(tmp_path, game)
    summary["header"]["competitions"][0]["date"] = kickoff.isoformat()
    venue["address"] = {"city": "Glendale", "state": "AZ", "country": "USA"}
    result["results"] = [
        {
            "name": "Glendale",
            "country_code": "US",
            "latitude": 34.1,
            "longitude": -118.2,
            "population": 999999,
            "admin1": "California",
        },
        {
            "name": "Glendale",
            "country_code": "US",
            "latitude": 33.53,
            "longitude": -112.19,
            "population": 1,
            "admin1": "Arizona",
        },
    ]
    assert weather.venue_point(tmp_path, game)["latitude"] == 33.53
    game["away_team"] = "WAS"
    summary["header"]["competitions"][0]["competitors"][1]["team"]["abbreviation"] = "WSH"
    assert weather.venue_point(tmp_path, game)["country"] == "US"


def test_live_window_and_january_season_follow_schedule(monkeypatch):
    now = datetime(2027, 1, 2, 0, tzinfo=UTC)
    rows = []
    for day, time, game_id in (
        ("2027-01-01", "19:30", "too-close"),
        ("2027-01-01", "22:00", "evening"),
        ("2027-01-03", "13:00", "sunday"),
    ):
        rows.append(
            {
                "game_id": game_id,
                "season": 2026,
                "week": 18,
                "game_type": "REG",
                "gameday": day,
                "gametime": time,
                "home_team": "DEN",
                "away_team": "SEA",
                "home_score": None,
                "away_score": None,
                "location": "Home",
                "espn": "123",
                "stadium_id": "denver",
                "stadium": "Denver stadium",
            }
        )
    monkeypatch.setattr(weather, "_download", lambda url: pl.DataFrame(rows).write_csv().encode())
    monkeypatch.setattr(weather, "_now", lambda: now)
    games, _ = weather._fresh_slate(now, 7, "pregame_refresh", date(2027, 1, 1))
    assert games["game_id"].to_list() == ["evening"] and games["season"].to_list() == [2026]
    games, _ = weather._fresh_slate(now, 7, "early_week", None)
    assert set(games["game_id"]) == {"evening", "sunday"}


def test_historical_lookup_never_fetches_and_uses_horizon_cutoff(monkeypatch, tmp_path):
    kickoff = datetime(2025, 9, 8, 0, 20, tzinfo=UTC)  # September 7 Chicago.
    games = pl.DataFrame(
        [{"game_id": "2025_01_BAL_BUF", "season": 2025, "week": 1, "kickoff_utc": kickoff}]
    )
    monkeypatch.setattr(weather, "load_team_cache", lambda data: (pl.DataFrame(), games, {}))
    monkeypatch.setattr(
        weather, "_json", lambda url: pytest.fail("Historical lookup fetched live weather")
    )
    early = weather.lookup_weather_date(tmp_path, date(2025, 9, 7), "early_week")[0]
    refresh = weather.lookup_weather_date(tmp_path, date(2025, 9, 7), "pregame_refresh")[0]
    assert early["status"] == refresh["status"] == "unavailable"
    assert datetime.fromisoformat(early["cutoff_utc"]) == datetime(2025, 9, 2, 15, tzinfo=UTC)
    assert datetime.fromisoformat(refresh["cutoff_utc"]) == kickoff - timedelta(hours=1)
    assert main(["team-weather-lookup", "--data-dir", str(tmp_path), "--date", "2025-09-07"]) == 0


def test_forecast_cli_links_context_without_changing_score_manifest(live_weather, monkeypatch):
    from nfl_prop_model.modeling import team_scores

    data, clock, game, _, _ = live_weather
    directory = data / "journal" / "team_scores" / "fixture"
    path = directory / "manifest.json"
    calls = []

    def score_forecast(data_dir, *, horizon, days):
        calls.append((data_dir, horizon, days))
        assert list((data / "journal" / "team_weather").glob("snapshot-*/manifest.json"))
        write_json(
            path,
            {
                "as_of_utc": (clock[0] + timedelta(seconds=1)).isoformat(),
                "horizon": horizon,
                "artifacts": {
                    "features": store_frame(
                        directory,
                        "features",
                        pl.DataFrame(
                            [{"game_id": game["game_id"], "kickoff_utc": game["kickoff_utc"]}]
                        ),
                    )
                },
            },
        )
        return path

    monkeypatch.setattr(team_scores, "save_team_forecast", score_forecast)
    assert main(["team-forecast", "--data-dir", str(data), "--days", "7"]) == 0
    sidecar = read_json(path.parent / "weather.json")
    assert calls == [(data, "early_week", 7)]
    assert sidecar["forecast_manifest_sha256"] == sha256_file(path)
    original = path.read_bytes()
    captured = Path(sidecar["weather_manifest"])
    with pytest.raises(DataQualityError, match="already has"):
        weather.link_weather_forecast(captured, path)
    assert path.read_bytes() == original
    report = read_json(path)
    report["horizon"] = "pregame_refresh"
    write_json(path, report)
    with pytest.raises(DataQualityError, match="horizons differ"):
        weather.link_weather_forecast(captured, path)


def test_context_link_rejects_changed_or_extra_score_games(live_weather):
    data, clock, game, _, _ = live_weather
    captured = weather.collect_weather(data)
    directory = data / "journal" / "team_scores" / "fixture"
    path = directory / "manifest.json"
    for game_id, kickoff, error in (
        ("wrong", game["kickoff_utc"], "cohorts differ"),
        (game["game_id"], game["kickoff_utc"] + timedelta(hours=1), "kickoffs differ"),
    ):
        write_json(
            path,
            {
                "as_of_utc": (clock[0] + timedelta(seconds=1)).isoformat(),
                "horizon": "early_week",
                "artifacts": {
                    "features": store_frame(
                        directory,
                        "features",
                        pl.DataFrame([{"game_id": game_id, "kickoff_utc": kickoff}]),
                    )
                },
            },
        )
        with pytest.raises(DataQualityError, match=error):
            weather.link_weather_forecast(captured, path)
    write_json(
        path,
        {
            "as_of_utc": clock[0].isoformat(),
            "horizon": "early_week",
            "artifacts": {
                "features": store_frame(
                    directory,
                    "features",
                    pl.DataFrame(
                        [{"game_id": game["game_id"], "kickoff_utc": game["kickoff_utc"]}]
                    ),
                )
            },
        },
    )
    with pytest.raises(DataQualityError, match="Weather was late"):
        weather.link_weather_forecast(captured, path)
