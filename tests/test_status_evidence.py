import json
from copy import deepcopy
from datetime import timedelta

import pytest
from test_prospective import AS_OF, buf, save_inputs

from nfl_prop_model.data.prospective import load_feature_report
from nfl_prop_model.data.status_evidence import (
    apply_status_evidence,
    article_text,
    capture_status_evidence,
    primary_url,
    read_status_evidence,
)


def article(*, published=None, quote="Current BUF will start.", context="Bills-Cardinals Week 3"):
    metadata = {
        "@type": "NewsArticle",
        "datePublished": (published or AS_OF - timedelta(hours=1)).isoformat(),
    }
    return (
        f'<html><script type="application/ld+json">{json.dumps(metadata)}</script>'
        f"<h1>{context}</h1><p>{quote}</p></html>"
    ).encode()


def capture(data, monkeypatch, *, raw=None, **kwargs):
    import nfl_prop_model.data.status_evidence as module

    save_inputs(data)
    row = buf(load_feature_report(data, as_of=AS_OF))
    monkeypatch.setattr(module, "fetch_article", lambda url, team: (raw or article(), url))
    return capture_status_evidence(
        data,
        row["game_id"],
        row["espn_id"],
        kind=kwargs.pop("kind", "starter"),
        claim=kwargs.pop("claim", "confirmed"),
        source_url="https://www.buffalobills.com/news/current",
        quote=kwargs.pop("quote", "Current BUF will start."),
        context_quotes=["Bills-Cardinals", "Week 3"],
        now=AS_OF,
        **kwargs,
    )


def test_archive_is_checked_offline_and_never_enables_forecasts(tmp_path, monkeypatch):
    directory = capture(tmp_path, monkeypatch)
    records = read_status_evidence(tmp_path)
    assert records[0]["published_at_utc"] == (AS_OF - timedelta(hours=1)).isoformat()
    report = load_feature_report(tmp_path, as_of=AS_OF)
    row = buf(report)
    assert row["primary_status"]["starter"] == "confirmed"
    assert not row["forecast_available"]
    assert "primary_status_claims_not_independently_adjudicated" in row["forecast_blockers"]
    assert (directory / "article.html").read_bytes() == article()


@pytest.mark.parametrize(
    "url",
    [
        "http://www.buffalobills.com/news/a",
        "https://localhost/a",
        "https://buffalobills.com.evil.test/a",
        "https://user:pass@www.buffalobills.com/a",
        "https://www.buffalobills.com:444/a",
        "https://www.buccaneers.com/news/a",
    ],
)
def test_primary_host_allowlist(url):
    with pytest.raises(ValueError, match="HTTPS"):
        primary_url(url, "BUF")


@pytest.mark.parametrize(
    "raw",
    [
        b"<p>Missing publication date</p>",
        b'<meta property="article:published_time" content="2026-10-06T10:00:00">',
        article() + b'<meta property="article:published_time" content="2026-10-06T09:00:00Z">',
    ],
)
def test_missing_ambiguous_or_unzoned_dates_fail(raw):
    with pytest.raises(ValueError):
        article_text(raw)


@pytest.mark.parametrize(
    "raw",
    [
        article(published=AS_OF + timedelta(minutes=1)),
        article(published=AS_OF - timedelta(days=8)),
        article(quote="A different QB will start."),
        article(context="Bills-Cardinals Week 2"),
    ],
)
def test_future_old_wrong_player_or_wrong_game_excerpts_fail(tmp_path, monkeypatch, raw):
    with pytest.raises(ValueError):
        capture(tmp_path, monkeypatch, raw=raw)
    assert not list((tmp_path / "status_evidence").glob("evidence-*"))


def test_html_corruption_fails_even_with_unchanged_annotation(tmp_path, monkeypatch):
    directory = capture(tmp_path, monkeypatch)
    (directory / "article.html").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        read_status_evidence(tmp_path)


def test_expiry_context_and_conflicting_claims_block(tmp_path, monkeypatch):
    capture(tmp_path, monkeypatch)
    records = read_status_evidence(tmp_path)
    report = load_feature_report(tmp_path, as_of=AS_OF + timedelta(hours=25))
    assert buf(report)["primary_status"]["starter"] == "stale_or_changed"
    report = load_feature_report(tmp_path, as_of=AS_OF)
    changed = deepcopy(records[0])
    changed["claim"] = "not_starter"
    apply_status_evidence(report, [records[0], changed])
    assert buf(report)["primary_status"]["starter"] == "conflict"
    report = load_feature_report(tmp_path, as_of=AS_OF)
    buf(report)["depth_rank"] += 1
    apply_status_evidence(report, records)
    assert buf(report)["primary_status"]["starter"] == "stale_or_changed"


def test_ruled_out_is_distinct_from_gameday_inactive(tmp_path, monkeypatch):
    capture(tmp_path, monkeypatch, kind="availability", claim="ruled_out")
    report = load_feature_report(tmp_path, as_of=AS_OF)
    assert buf(report)["primary_status"]["availability"] == "ruled_out"
    assert not buf(report)["active_status_reviewed"]
    assert not buf(report)["forecast_available"]


def test_redirect_to_unapproved_host_is_rejected_before_request(monkeypatch):
    from urllib.error import HTTPError

    import nfl_prop_model.data.status_evidence as module

    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append(request.full_url)
            raise HTTPError(
                request.full_url, 302, "Redirect", {"Location": "https://evil.test/a"}, None
            )

    monkeypatch.setattr(module, "build_opener", lambda handler: Opener())
    with pytest.raises(ValueError, match="HTTPS"):
        module.fetch_article("https://www.buffalobills.com/news/a", "BUF")
    assert calls == ["https://www.buffalobills.com/news/a"]


def test_future_capture_does_not_appear_in_earlier_report(tmp_path, monkeypatch):
    capture(tmp_path, monkeypatch)
    records = read_status_evidence(tmp_path)
    report = load_feature_report(tmp_path, as_of=AS_OF - timedelta(minutes=1))
    assert buf(report)["primary_status"]["records"] == []
    assert buf(report)["primary_status"]["starter"] == "unknown"
    assert records[0]["retrieved_at_utc"] == AS_OF.isoformat()
