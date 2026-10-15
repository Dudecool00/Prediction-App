import base64
import gzip
import hashlib
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import polars as pl
import pytest

from nfl_prop_model.cli import main
from nfl_prop_model.data import team_injury_news as source
from nfl_prop_model.data import team_injury_news_audit as audit
from nfl_prop_model.data import team_injury_news_variants as variants
from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.storage import read_json, sha256_file, write_json
from nfl_prop_model.data.team_injury_history import capture_time, parse_weekly_article

HEADER = ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"]


def article(season=2024, body=None, week=2, published=None):
    published = published or (
        f"{season}-09-11T18:00:00Z" if season == 2024 else f"{season}-09-13T18:00:00Z"
    )
    if body is None:
        body = (
            "<h3>BEARS</h3><li>OUT: LG Test Guard (ankle)</li>"
            "<h3>PACKERS</h3><p>No injury designations</p>"
        )
    return (
        f"<h1>Week {week} injury report for {season} NFL season</h1>"
        "<script type='application/ld+json'>"
        + json.dumps(
            {"@type": "NewsArticle", "datePublished": published, "dateModified": published}
        )
        + "</script>"
        "<div class='nfl-c-article__body'>" + body + "</div>"
    ).encode()


def row(body, season=2024, stamp=None):
    slug = (
        f"week-2-injury-report-for-{season}-nfl-season"
        if season == 2024
        else f"week-2-nfl-injury-report-for-{season}-season"
    )
    return dict(
        zip(
            HEADER,
            [
                "com,nfl)",
                stamp or ("20240913180000" if season == 2024 else "20230915180000"),
                "https://www.nfl.com/news/" + slug,
                "text/html",
                "200",
                base64.b32encode(hashlib.sha1(body).digest()).decode(),
                str(len(body)),
            ],
            strict=True,
        )
    )


def index(rows):
    return json.dumps([HEADER] + [[r[k] for k in HEADER] for r in rows]).encode()


@pytest.fixture
def sources(tmp_path, monkeypatch):
    games = pl.DataFrame(
        [
            {
                "game_id": f"{s}-2-CHI-GB",
                "season": s,
                "week": 2,
                "home_team": "CHI",
                "away_team": "GB",
                "kickoff_utc": datetime(s, 9, d, 17, tzinfo=UTC),
            }
            for s, d in ((2023, 17), (2024, 15))
        ]
    )
    metadata = {"manifest_sha256": "schedule-hash", "artifact_sha256": "artifact-hash"}
    monkeypatch.setattr(source, "load_development_schedule", lambda _: (games, metadata))
    bodies = {s: article(s) for s in source.DEFAULT_NEWS_SEASONS}
    rows = {s: row(bodies[s], s) for s in source.DEFAULT_NEWS_SEASONS}
    requests = []

    def request(url, limit):
        requests.append(url)
        if "/cdx/" in url:
            query = parse_qs(urlsplit(url).query)
            season = int(query["from"][0][:4])
            if "original:.*[?].*" in query.get("filter", []):
                return b"[]", url
            if "matchType" in query and query["url"][0] == source.PREFIXES[1]:
                return b"[]", url
            return index([rows[season]]), url
        season = next(s for s in bodies if str(s) in url)
        return bodies[season], url

    monkeypatch.setattr(source, "_request", request)
    monkeypatch.setattr(variants, "_request", request)
    return tmp_path, games, bodies, rows, requests


def test_season_url_patterns_exclude_queries_wrong_years_and_ambiguous_canonical_urls():
    for season in (2023, 2024):
        record = row(article(season), season)
        assert source.catalog_articles([record], season, source.PREFIXES[0]) == {
            2: record["original"]
        }
    record = row(article())
    with pytest.raises(DataQualityError, match="URL/date"):
        source.catalog_articles(
            [{**record, "original": record["original"] + "?campaign=1"}], 2024, source.PREFIXES[0]
        )
    wrong_year = {**record, "original": record["original"].replace("2024", "2023")}
    assert source.catalog_articles([wrong_year], 2024, source.PREFIXES[0]) == {}
    alternate = {
        **record,
        "original": record["original"].replace("week-2-injury", "week-2-nfl-injury"),
    }
    with pytest.raises(DataQualityError, match="Ambiguous"):
        source.catalog_articles([record, alternate], 2024, source.PREFIXES[0])


def test_cli_reuses_proof_and_audit_keeps_every_game_with_matching_2025_definition(
    sources, monkeypatch
):
    data, games, bodies, rows, requests = sources
    assert main(["team-injury-news-fetch", "--data-dir", str(data)]) == 0
    raw = source.news_path(data, games)
    original = {p: sha256_file(p) for p in raw.parent.rglob("*") if p.is_file()}
    calls = len(requests)
    assert main(["team-injury-news-fetch", "--data-dir", str(data)]) == 0
    assert len(requests) == calls
    assert (
        main(
            [
                "team-injury-news-audit",
                "--data-dir",
                str(data),
                "--report-dir",
                str(data / "report"),
            ]
        )
        == 0
    )
    manifest = next((data / "processed/team_injury_news").glob("*/manifest.json"))
    report = audit.load_news_audit(manifest, data)
    assert report["games"] == 2 and report["records"] == 8 and report["model_fits_performed"] == 0
    assert (
        report["matched_context_comparison_ready"] is False
        and report["enriched_replay_ready"] is False
    )
    weeks = source.load_news(raw, games)
    records = audit.news_records(games, weeks, raw.parent)
    for season in (2023, 2024):
        fields = parse_weekly_article(
            bodies[season],
            2,
            capture_time(rows[season]),
            source.development_cutoffs(games)[(season, 2)][0],
        )
        for record in records.filter(
            (pl.col("season") == season) & (pl.col("horizon") == "pregame_refresh")
        ).to_dicts():
            assert record["status"] == fields[record["team"]]["status"]
            assert all(record[k] == fields[record["team"]][k] for k in audit.COUNTS)
    assert original == {p: sha256_file(p) for p in raw.parent.rglob("*") if p.is_file()}
    assert all(
        c["games_both_teams_formal"] == 1
        for c in report["coverage"]
        if c["horizon"] == "pregame_refresh"
    )
    with pytest.raises(DataQualityError, match="no outcomes"):
        audit.news_records(games.with_columns(pl.lit(99).alias("home_score")), weeks, raw.parent)
    with monkeypatch.context() as changed:
        changed.setattr(audit, "news_source_hash", lambda: "changed")
        with pytest.raises(DataQualityError, match="code/policy"):
            audit.load_news_audit(manifest, data)
    changed_report = read_json(manifest)
    changed_report["coverage"][0]["formal_team_reports"] += 1
    write_json(manifest, changed_report)
    with pytest.raises(DataQualityError, match="differs from preserved"):
        audit.load_news_audit(manifest, data)


def replace_edition(path, weeks, body, *, season=2024, stamp="20240914180000"):
    old = weeks[(season, 2)]["captures"][0]
    receipt = {**old, **row(body, season, stamp), "file": "changed.html.gz", "status": "verified"}
    (path.parent / str(season) / "week-02" / receipt["file"]).write_bytes(gzip.compress(body))
    weeks[(season, 2)]["captures"].append(receipt)


def test_latest_pending_omitted_or_failed_report_never_inherits_older_healthy_claim(sources):
    data, games, _, _, _ = sources
    path = source.fetch_news(data)
    weeks = source.load_news(path, games)
    replace_edition(path, weeks, article(body="<h3>BEARS</h3><p>N/A</p>"))
    frame = audit.news_records(games, weeks, path.parent)
    refresh = frame.filter((pl.col("season") == 2024) & (pl.col("horizon") == "pregame_refresh"))
    assert set(refresh["status"]) == {"partial_or_pending", "unavailable"}
    assert refresh["out_count"].null_count() == 2
    assert set(refresh["reason"].drop_nulls()) == {"team_section_missing"}
    weeks[(2024, 2)]["captures"][-1]["status"] = "unavailable"
    refresh = audit.news_records(games, weeks, path.parent).filter(
        (pl.col("season") == 2024) & (pl.col("horizon") == "pregame_refresh")
    )
    assert set(refresh["reason"]) == {"unverified_edition"}


def test_future_editions_cannot_change_old_predictions_and_exact_cutoffs_are_excluded(sources):
    data, games, _, rows, _ = sources
    path = source.fetch_news(data)
    weeks = source.load_news(path, games)
    before = audit.news_records(games, weeks, path.parent)
    weeks[(2024, 2)]["captures"].append(
        {**weeks[(2024, 2)]["captures"][0], "timestamp": "20240918180000", "status": "unavailable"}
    )
    assert before.equals(audit.news_records(games, weeks, path.parent))
    changed = games.with_columns(
        pl.when(pl.col("season") == 2024)
        .then(pl.lit(capture_time(rows[2024]) + timedelta(hours=1)))
        .otherwise(pl.col("kickoff_utc"))
        .alias("kickoff_utc")
    )
    refresh = audit.news_records(changed, weeks, path.parent).filter(
        (pl.col("season") == 2024) & (pl.col("horizon") == "pregame_refresh")
    )
    assert set(refresh["reason"]) == {"no_earlier_edition"}


@pytest.mark.parametrize("defect", ["wrong_week", "backdated", "foreign_team", "malformed"])
def test_wrong_source_or_malformed_status_cannot_become_a_usable_count(sources, defect):
    data, games, _, _, _ = sources
    path = source.fetch_news(data)
    weeks = source.load_news(path, games)
    body = (
        article(week=3)
        if defect == "wrong_week"
        else article(published="2023-09-11T18:00:00Z")
        if defect == "backdated"
        else article(body="<h3>VIKINGS</h3><p>No injury designations</p>")
        if defect == "foreign_team"
        else article(body="<h3>BEARS</h3><li>OUT: QB One (knee) QB Two (foot)</li>")
    )
    replace_edition(path, weeks, body)
    frame = audit.news_records(games, weeks, path.parent).filter(
        (pl.col("season") == 2024) & (pl.col("horizon") == "pregame_refresh")
    )
    assert frame["out_count"].null_count() == 2
    assert audit.news_summary(frame, ["season", "horizon"])[0]["games_both_teams_formal"] == 0


@pytest.mark.parametrize("defect", ["redirect", "digest", "budget"])
def test_unverified_capture_or_body_budget_stays_unknown(sources, monkeypatch, defect):
    data, games, _, _, _ = sources
    original = source._request

    def request(url, limit):
        body, resolved = original(url, limit)
        if "/cdx/" not in url:
            if defect == "redirect":
                resolved += "?redirect=1"
            elif defect == "digest":
                body += b"changed"
        return body, resolved

    monkeypatch.setattr(source, "_request", request)
    if defect == "budget":
        monkeypatch.setattr(source.gzip, "compress", lambda *args, **kwargs: b"x" * 8000001)
    path = source.fetch_news(data)
    weeks = source.load_news(path, games)
    frame = audit.news_records(games, weeks, path.parent)
    assert frame["out_count"].null_count() == frame.height
    assert set(frame.filter(pl.col("horizon") == "pregame_refresh")["reason"]) == {
        "unverified_edition"
    }


@pytest.mark.parametrize("target", ["body", "index", "week"])
def test_preserved_source_corruption_fails(sources, target):
    data, games, _, _, _ = sources
    path = source.fetch_news(data)
    week_path = path.parent / "2024/week-02/manifest.json"
    report = read_json(week_path)
    file = (
        week_path
        if target == "week"
        else week_path.parent
        / (report["captures"][0]["file"] if target == "body" else report["index"]["file"])
    )
    file.write_bytes(b"bad")
    with pytest.raises(DataQualityError, match="checksum"):
        source.load_news(path, games)


def test_failed_week_index_resumes_and_invalid_seasons_do_not_read_2025(sources, monkeypatch):
    data, games, _, _, _ = sources
    original = source._request

    def request(url, limit):
        if "/cdx/" in url and "matchType" not in url and "2023" in url:
            raise OSError("temporary archive outage")
        return original(url, limit)

    monkeypatch.setattr(source, "_request", request)
    with pytest.raises(DataQualityError, match="Incomplete news archive"):
        source.fetch_news(data)
    path = source.news_path(data, games)
    assert not path.exists() and (path.parent / "2024/week-02/manifest.json").exists()
    monkeypatch.setattr(source, "_request", original)
    assert source.fetch_news(data) == path
    with pytest.raises(ValueError, match="2022, 2023, 2024"):
        source.news_schedule(data, (2025,))
    with pytest.raises(SystemExit) as rejected:
        main(["team-injury-news-fetch", "--data-dir", str(data), "--seasons", "2025"])
    assert rejected.value.code == 1


def test_tracking_urls_require_a_known_season_and_tracking_only_parameters():
    base = row(article())["original"]
    assert (
        variants.tracking_canonical(base + "?utm_source=dlvr.it&utm_medium=twitter", 2024) == base
    )
    for suffix in ("?lang=es", "?campaign=", "?campaign=a&campaign=b", "?season=2023", "#section"):
        assert variants.tracking_canonical(base + suffix, 2024) is None
    assert variants.tracking_canonical(base + "?campaign=a", 2023) is None


@pytest.mark.parametrize("defect", [None, "canonical_identity", "timestamp_tie"])
def test_tracking_editions_have_independent_proof_and_latest_whole_selection(
    sources, monkeypatch, defect
):
    data, games, _, rows, _ = sources
    canonical = source.fetch_news(data)
    unchanged = {p: sha256_file(p) for p in canonical.parent.rglob("*") if p.is_file()}
    base = rows[2024]["original"]
    variant = base + "?utm_source=dlvr.it&utm_medium=twitter"
    identity = base if defect != "canonical_identity" else base.replace("week-2", "week-3")
    body = f'<link rel="canonical" href="{identity}">'.encode() + article(
        body="<h3>BEARS</h3><li>OUT: LG One (ankle), QB Two (foot)</li>"
        "<h3>PACKERS</h3><p>No injury designations</p>"
    )
    stamp = rows[2024]["timestamp"] if defect == "timestamp_tie" else "20240914180000"
    receipt = {**row(body, stamp=stamp), "original": variant}
    original = source._request

    def request(url, limit):
        if "/cdx/" in url:
            query = parse_qs(urlsplit(url).query)
            if "original:.*[?].*" in query.get("filter", []):
                eligible = query["from"] == ["20240801"] and query["url"] == [source.PREFIXES[0]]
                return index([receipt]) if eligible else b"[]", url
            if query["url"] == [variant]:
                return index([receipt]), url
        elif url.endswith(variant):
            return body, url
        return original(url, limit)

    monkeypatch.setattr(source, "_request", request)
    monkeypatch.setattr(variants, "_request", request)
    path = variants.fetch_qualified(data)
    weeks = variants.load_qualified(path, games)
    frame = audit.news_records(games, weeks, path.parent)
    refresh = frame.filter((pl.col("season") == 2024) & (pl.col("horizon") == "pregame_refresh"))
    if defect is None:
        assert refresh.filter(pl.col("team") == "CHI")["out_count"].to_list() == [2]
        assert set(refresh["article_kind"]) == {"tracking_variant"}
        assert set(refresh["archive_url"]) == {f"https://web.archive.org/web/{stamp}id_/{variant}"}
        assert audit.news_summary(refresh, ["season", "horizon"])[0]["games_both_teams_formal"] == 1
    else:
        assert refresh["out_count"].null_count() == 2
        assert set(refresh["reason"]) == {
            "unsupported_edition" if defect == "canonical_identity" else "ambiguous_latest_editions"
        }
    assert unchanged == {p: sha256_file(p) for p in unchanged}
    calls_before = len(read_json(path)["variants"])
    assert calls_before == 1
    source_path = (
        path.parent / "2024/week-02" / read_json(path)["variants"][0]["folder"] / "manifest.json"
    )
    source_path.write_bytes(b"bad")
    with pytest.raises(DataQualityError, match="checksum"):
        variants.load_qualified(path, games)


def test_formal_out_count_without_position_does_not_qualify_paired_position_features(sources):
    data, games, _, _, _ = sources
    path = source.fetch_news(data)
    weeks = source.load_news(path, games)
    replace_edition(
        path,
        weeks,
        article(
            body="<h3>BEARS</h3><li>OUT: Mystery Person (foot)</li>"
            "<h3>PACKERS</h3><p>No injury designations</p>"
        ),
    )
    frame = audit.news_records(games, weeks, path.parent).filter(
        (pl.col("season") == 2024) & (pl.col("horizon") == "pregame_refresh")
    )
    summary = audit.news_summary(frame, ["season", "horizon"])[0]
    assert summary["games_both_teams_formal"] == 1
    assert summary["games_both_teams_position_counts"] == 0
