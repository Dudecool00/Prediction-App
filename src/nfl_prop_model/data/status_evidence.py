"""Archive primary articles for explicit pregame annotations; read them offline."""

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from nfl_prop_model.data.status_reviews import (
    CONTEXT_FIELDS,
    MAX_AGES,
    checksum,
    review_context,
    timestamp,
)
from nfl_prop_model.data.storage import read_json, write_json

# Official club websites linked from https://www.nfl.com/teams/.
CLUBS = {
    "ARI": ("azcardinals.com", "Cardinals"),
    "ATL": ("atlantafalcons.com", "Falcons"),
    "BAL": ("baltimoreravens.com", "Ravens"),
    "BUF": ("buffalobills.com", "Bills"),
    "CAR": ("panthers.com", "Panthers"),
    "CHI": ("chicagobears.com", "Bears"),
    "CIN": ("bengals.com", "Bengals"),
    "CLE": ("clevelandbrowns.com", "Browns"),
    "DAL": ("dallascowboys.com", "Cowboys"),
    "DEN": ("denverbroncos.com", "Broncos"),
    "DET": ("detroitlions.com", "Lions"),
    "GB": ("packers.com", "Packers"),
    "HOU": ("houstontexans.com", "Texans"),
    "IND": ("colts.com", "Colts"),
    "JAX": ("jaguars.com", "Jaguars"),
    "KC": ("chiefs.com", "Chiefs"),
    "LA": ("therams.com", "Rams"),
    "LAC": ("chargers.com", "Chargers"),
    "LV": ("raiders.com", "Raiders"),
    "MIA": ("miamidolphins.com", "Dolphins"),
    "MIN": ("vikings.com", "Vikings"),
    "NE": ("patriots.com", "Patriots"),
    "NO": ("neworleanssaints.com", "Saints"),
    "NYG": ("giants.com", "Giants"),
    "NYJ": ("newyorkjets.com", "Jets"),
    "PHI": ("philadelphiaeagles.com", "Eagles"),
    "PIT": ("steelers.com", "Steelers"),
    "SEA": ("seahawks.com", "Seahawks"),
    "SF": ("49ers.com", "49ers"),
    "TB": ("buccaneers.com", "Buccaneers"),
    "TEN": ("tennesseetitans.com", "Titans"),
    "WAS": ("commanders.com", "Commanders"),
}
CLAIMS = {
    "starter": {"confirmed", "not_starter"},
    "availability": {"active", "inactive", "ruled_out"},
}
MAX_BYTES = 2_000_000


def primary_url(url: str, team: str) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").removeprefix("www.")
    if (
        parsed.scheme != "https"
        or host not in {"nfl.com", CLUBS[team][0]}
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
        or any(char.isspace() for char in url)
    ):
        raise ValueError("Use an HTTPS article on NFL.com or the candidate's official club website")
    return url


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def fetch_article(url: str, team: str) -> tuple[bytes, str]:
    """Validate every redirect before following it; never fetch arbitrary remote URLs."""
    opener = build_opener(NoRedirect())
    for _ in range(6):
        primary_url(url, team)
        try:
            with opener.open(
                Request(url, headers={"User-Agent": "QBResearch/0.1"}), timeout=30
            ) as response:
                if response.headers.get_content_type() != "text/html":
                    raise ValueError("Status evidence must be an HTML article")
                raw = response.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise ValueError("Status article exceeds the archive size limit")
                return raw, url
        except HTTPError as error:
            if error.code not in {301, 302, 303, 307, 308} or not error.headers.get("Location"):
                raise
            url = urljoin(url, error.headers["Location"])
    raise ValueError("Too many article redirects")


class ArticleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.published: list[str] = []
        self.scripts: list[str] = []
        self.script: list[str] | None = None
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        fields = dict(attrs)
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag == "script" and fields.get("type") == "application/ld+json":
            self.script = []
        if tag == "meta" and fields.get("property") == "article:published_time":
            self.published.append(fields.get("content") or "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.script is not None:
            self.scripts.append("".join(self.script))
            self.script = None
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data: str) -> None:
        if self.script is not None:
            self.script.append(data)
        if not self.hidden:
            self.text.append(data)


def article_text(raw: bytes) -> tuple[str, datetime]:
    parser = ArticleParser()
    parser.feed(raw.decode("utf-8-sig"))

    def dates(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                dates(item)
        elif isinstance(value, dict):
            types = value.get("@type", [])
            types = [types] if isinstance(types, str) else types
            if set(types) & {"NewsArticle", "Article"} and value.get("datePublished"):
                parser.published.append(value["datePublished"])
            if "@graph" in value:
                dates(value["@graph"])

    for script in parser.scripts:
        dates(json.loads(script))
    published = {timestamp(value) for value in parser.published}
    if len(published) != 1:
        raise ValueError("Article needs one unambiguous, timezone-aware publication timestamp")
    return " ".join(" ".join(parser.text).split()), published.pop()


def validate_evidence(record: dict[str, Any], raw: bytes) -> None:
    context = record["context"]
    if (
        record["format_version"] != 1
        or record["scope"] != "primary_article_status_annotation"
        or not re.fullmatch(r"[a-f0-9]{32}", record["record_id"])
        or context["season"] != 2026
        or set(context) != set(CONTEXT_FIELDS)
        or record["claim"] not in CLAIMS.get(record["kind"], set())
        or not context["gsis_id"]
    ):
        raise ValueError("Invalid primary status annotation")
    primary_url(record["source_url"], context["team"])
    primary_url(record["final_url"], context["team"])
    if hashlib.sha256(raw).hexdigest() != record["source_sha256"]:
        raise ValueError("Archived article checksum mismatch")
    text, published = article_text(raw)
    retrieved = timestamp(record["retrieved_at_utc"])
    cutoff = timestamp(context["kickoff_utc"]) - timedelta(hours=1)
    if (
        published.isoformat() != record["published_at_utc"]
        or not cutoff - timedelta(days=7) <= published <= retrieved < cutoff
        or timestamp(context["source_snapshot_utc"]) > retrieved
        or timestamp(record["sources"]["retrieved_at_utc"]) > retrieved
    ):
        raise ValueError(
            "Evidence must be published recently and archived before the forecast cutoff"
        )
    excerpts = [record["quote"], *record["context_quotes"]]
    if (
        not record["quote"]
        or not record["context_quotes"]
        or sum(len(value.split()) for value in excerpts) > 25
        or any(" ".join(value.split()) not in text for value in excerpts)
        or record["player_name"] not in record["quote"]
    ):
        raise ValueError(
            "Use matching short excerpts, including the candidate's full name (25 words total)"
        )
    game_text = re.sub(r"[^a-z0-9]+", " ", " ".join(record["context_quotes"]).lower())
    required = [
        CLUBS[context["team"]][1],
        CLUBS[context["opponent_team"]][1],
        f"Week {context['week']}",
    ]
    if any(value.lower() not in game_text for value in required):
        raise ValueError("Context excerpts must identify both clubs and the scheduled week")


def capture_status_evidence(
    data_dir: Path,
    game_id: str,
    espn_id: str,
    *,
    kind: str,
    claim: str,
    source_url: str,
    quote: str,
    context_quotes: list[str],
    now: datetime | None = None,
) -> Path:
    from nfl_prop_model.data.upcoming import load_upcoming_report

    report = load_upcoming_report(data_dir, as_of=now or datetime.now(UTC), days=28)
    matches = [
        row
        for row in report["candidates"]
        if row["game_id"] == game_id and row["espn_id"] == espn_id
    ]
    if len(matches) != 1 or not matches[0]["sources_fresh"]:
        raise ValueError("Select a unique QB in fresh upcoming sources")
    row = matches[0]
    raw, final_url = fetch_article(source_url, row["team"])
    _, published = article_text(raw)
    record = {
        "format_version": 1,
        "scope": "primary_article_status_annotation",
        "record_id": uuid4().hex,
        "context": review_context(row),
        "sources": report["sources"],
        "player_name": row["player_name"],
        "kind": kind,
        "claim": claim,
        "source_url": source_url,
        "final_url": final_url,
        "quote": " ".join(quote.split()),
        "context_quotes": [" ".join(value.split()) for value in context_quotes],
        "published_at_utc": published.isoformat(),
        "retrieved_at_utc": (now or datetime.now(UTC)).isoformat(),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "method": "Reviewer annotation; exact excerpts, publication time and archived bytes "
        "checked. No semantic classifier or participation proof.",
    }
    validate_evidence(record, raw)
    directory = data_dir / "status_evidence" / f"evidence-{record['record_id']}"
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "article.html").write_bytes(raw)
    write_json(directory / "record.json", {"payload": record, "sha256": checksum(record)})
    return directory


def read_status_evidence(data_dir: Path) -> list[dict[str, Any]]:
    records = []
    for directory in sorted((data_dir / "status_evidence").glob("evidence-*")):
        envelope = read_json(directory / "record.json")
        record = envelope["payload"]
        if (
            directory.name != f"evidence-{record['record_id']}"
            or directory.is_symlink()
            or (directory / "article.html").is_symlink()
            or (directory / "record.json").is_symlink()
            or envelope["sha256"] != checksum(record)
        ):
            raise ValueError("Primary status archive identity/checksum mismatch")
        validate_evidence(record, (directory / "article.html").read_bytes())
        records.append(record)
    return records


def apply_status_evidence(report: dict[str, Any], records: list[dict[str, Any]]) -> None:
    """Expose checked archives separately from manual claims; all forecasts stay off."""
    as_of = timestamp(report["as_of_utc"])
    for row in report["candidates"]:
        status: dict[str, Any] = {"starter": "unknown", "availability": "unknown", "records": []}
        for kind, age in MAX_AGES.items():
            matches = [
                record
                for record in records
                if record["kind"] == kind
                and record["context"]["game_id"] == row["game_id"]
                and record["context"]["espn_id"] == row["espn_id"]
                and timestamp(record["retrieved_at_utc"]) <= as_of
            ]
            status["records"].extend(matches)
            fresh = [
                record
                for record in matches
                if record["context"] == review_context(row)
                and as_of - timestamp(record["published_at_utc"]) <= age
                and as_of - timestamp(record["retrieved_at_utc"]) <= age
                and row["sources_fresh"]
            ]
            claims = {record["claim"] for record in fresh}
            status[kind] = (
                next(iter(claims))
                if len(claims) == 1
                else "conflict"
                if claims
                else "stale_or_changed"
                if matches
                else "unknown"
            )
            manual = row["status_review"][kind]
            if claims and manual not in {"unknown", "stale", status[kind]}:
                status[kind] = "conflict"
        row["primary_status"] = status
    starters: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in report["candidates"]:
        if row["primary_status"]["starter"] == "confirmed":
            starters.setdefault((row["game_id"], row["team"]), []).append(row)
    for rows in starters.values():
        if len(rows) > 1:
            for row in rows:
                row["primary_status"]["starter"] = "conflict"
    for row in report["candidates"]:
        status = row["primary_status"]
        if status["availability"] in {"inactive", "ruled_out"} and status["starter"] == "confirmed":
            status["starter"] = "conflict"
        for kind in MAX_AGES:
            if status[kind] != "unknown":
                row["forecast_blockers"].append(f"primary_{kind}_{status[kind]}")
        if status["records"]:
            row["forecast_blockers"].append("primary_status_claims_not_independently_adjudicated")
        row["review_reasons"] = row["forecast_blockers"]
        row["forecast_available"] = False
    report["counts"]["primary_article_candidate_rows"] = sum(
        bool(row["primary_status"]["records"]) for row in report["candidates"]
    )
    report["primary_evidence_policy"] = {
        "method": "Archived primary articles with exact excerpts and publication metadata; "
        "reviewer interprets claims",
        "expiry_hours": {"starter": 24, "availability": 6},
        "ruled_out": "Injury designation; distinct from an official gameday inactive list",
        "activation": "Never inferred from chart rank, practice participation "
        "or missing injury designation",
        "forecast_gate": "Independent claim adjudication and participation validation "
        "still required",
    }
