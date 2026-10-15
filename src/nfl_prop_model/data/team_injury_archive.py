"""Small, digest-verified archive pilot for dated 2025 Week 1 injury designations."""

import base64
import gzip
import hashlib
import io
import json
import re
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.storage import read_json, sha256_file, write_json
from nfl_prop_model.data.team_depth import aware

ARTICLE = (
    "https://www.nfl.com/news/nfl-week-1-injury-report-player-statuses-for-all-16-games-2025-season"
)
INJURY_POLICY = {
    "scope": "2025 Week 1 source pilot only, not season-wide injury coverage",
    "availability": "verified archive capture strictly before cutoff; "
    "never article's first publication date",
    "integrity": "raw id_ body SHA1/base32 must match the CDX digest and resolved capture URL",
    "selection": "latest whole archived edition; same week/team; no carry across a removed section",
    "freshness": "edition at most 72h old; older or missing editions unknown",
    "status": "formal Out/Questionable/Doubtful designations; "
    "not practice participation or complete IR roster",
    "identity": "NFL player slug or document name only; no fabricated GSIS or ESPN crosswalk",
    "classification": "unknown positions invalidate group counts; never guessed from names",
    "storage": "small compressed original bodies plus index/digests; no live fallback or grids",
}
NICKNAMES = dict(
    zip(
        (
            "CARDINALS",
            "FALCONS",
            "RAVENS",
            "BILLS",
            "PANTHERS",
            "BEARS",
            "BENGALS",
            "BROWNS",
            "COWBOYS",
            "BRONCOS",
            "LIONS",
            "PACKERS",
            "TEXANS",
            "COLTS",
            "JAGUARS",
            "CHIEFS",
            "RAMS",
            "CHARGERS",
            "RAIDERS",
            "DOLPHINS",
            "VIKINGS",
            "PATRIOTS",
            "SAINTS",
            "GIANTS",
            "JETS",
            "EAGLES",
            "STEELERS",
            "SEAHAWKS",
            "49ERS",
            "BUCCANEERS",
            "TITANS",
            "COMMANDERS",
        ),
        (
            "ARI",
            "ATL",
            "BAL",
            "BUF",
            "CAR",
            "CHI",
            "CIN",
            "CLE",
            "DAL",
            "DEN",
            "DET",
            "GB",
            "HOU",
            "IND",
            "JAX",
            "KC",
            "LA",
            "LAC",
            "LV",
            "MIA",
            "MIN",
            "NE",
            "NO",
            "NYG",
            "NYJ",
            "PHI",
            "PIT",
            "SEA",
            "SF",
            "TB",
            "TEN",
            "WAS",
        ),
        strict=True,
    )
)
POSITION_GROUPS = {
    "QB": {"QB"},
    "OL": {"C", "G", "T", "OL", "OG", "OT"},
    "skill": {"WR", "TE", "RB", "FB"},
    "defense": {"DL", "DE", "DT", "NT", "LB", "ILB", "OLB", "MLB", "CB", "DB", "S", "FS", "SS"},
    "special": {"K", "P", "LS"},
}
POSITIONS = set().union(*POSITION_GROUPS.values())


def _get(url: str, limit: int) -> tuple[bytes, str]:
    request = urllib.request.Request(
        url, headers={"User-Agent": "Prediction-App-research", "Accept-Encoding": "identity"}
    )
    with urllib.request.urlopen(request, timeout=35) as response:
        payload = response.read(limit + 1)
        resolved = response.url
    if len(payload) > limit:
        raise DataQualityError("Archive response exceeds the declared size limit")
    return payload, resolved


def _clean(value: str) -> str:
    return " ".join(value.replace("\ufeff", "").replace("\u200b", "").split())


def _gunzip(payload: bytes) -> bytes:
    with gzip.GzipFile(fileobj=io.BytesIO(payload)) as handle:
        unpacked = handle.read(1000001)
    if len(unpacked) > 1000000:
        raise DataQualityError("Decoded archive entity exceeds 1 MB limit")
    return unpacked


class ArticleParts(HTMLParser):
    """Capture headings/list paragraphs only inside the article, excluding navigation."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.div_depth = 0
        self.block: dict[str, Any] | None = None
        self.parts: list[dict[str, Any]] = []
        self.title = ""
        self.in_title = False
        self.in_metadata = False
        self.script = ""
        self.metadata: dict[str, Any] = {}
        self.article_found = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        properties = dict(attrs)
        if tag == "h1":
            self.in_title = True
        if tag == "script" and properties.get("type") == "application/ld+json":
            self.in_metadata, self.script = True, ""
        if tag == "div":
            if self.div_depth:
                self.div_depth += 1
            elif "nfl-c-article__body" in (properties.get("class") or "").split():
                self.div_depth, self.article_found = 1, True
        if (
            self.div_depth
            and tag == "div"
            and "nfl-o-ranked-item" in (properties.get("class") or "").split()
        ):
            self.parts.append({"tag": "matchup", "text": "", "links": {}})
        if self.div_depth and tag in {"h2", "h3", "li", "p"}:
            if self.block is not None:
                raise DataQualityError("Nested injury text blocks require a parser audit")
            self.block = {"tag": tag, "text": "", "links": {}}
        if self.block is not None and tag == "a" and "/players/" in (properties.get("href") or ""):
            slug = (properties["href"] or "").split("/players/", 1)[1].strip("/").split("/", 1)[0]
            self.block["links"][slug] = ""
            self.block["active_link"] = slug

    def handle_endtag(self, tag: str) -> None:
        if tag == "h1":
            self.in_title = False
        if tag == "script" and self.in_metadata:
            self.in_metadata = False
            value = json.loads(self.script)
            if isinstance(value, dict) and value.get("@type") == "NewsArticle":
                self.metadata = value
        if self.block is not None and tag == "a":
            self.block.pop("active_link", None)
        if self.block is not None and tag == self.block["tag"]:
            self.block["text"] = _clean(self.block["text"])
            self.parts.append(self.block)
            self.block = None
        if tag == "div" and self.div_depth:
            self.div_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title += data
        if self.in_metadata:
            self.script += data
        if self.block is not None:
            self.block["text"] += data
            if slug := self.block.get("active_link"):
                self.block["links"][slug] += data


def _segments(text: str) -> list[str]:
    parts, start, nesting = [], 0, 0
    for index, character in enumerate(text):
        nesting += 1 if character == "(" else -1 if character == ")" else 0
        if nesting < 0:
            raise DataQualityError("Unbalanced injury annotation")
        if character == "," and nesting == 0:
            parts.append(text[start:index].strip())
            start = index + 1
    if nesting:
        raise DataQualityError("Unbalanced injury annotation")
    return parts + [text[start:].strip()]


def parse_injury_article(payload: bytes, captured: datetime) -> dict[str, dict[str, Any]]:
    parser = ArticleParts()
    # Verify the original HTTP entity before decoding; some captures retain gzip.
    html = _gunzip(payload) if payload.startswith(b"\x1f\x8b") else payload
    parser.feed(html.decode("utf-8"))
    if not parser.article_found or not re.search(r"Week\s+1.*injury report", parser.title, re.I):
        raise DataQualityError("Archived response is not the declared Week 1 injury article")
    if not {"dateModified", "datePublished"} <= parser.metadata.keys():
        raise DataQualityError("Injury article publication/modification metadata missing")
    modified = aware(datetime.fromisoformat(parser.metadata["dateModified"].replace("Z", "+00:00")))
    published = aware(
        datetime.fromisoformat(parser.metadata["datePublished"].replace("Z", "+00:00"))
    )
    if published.year != 2025 or not published <= modified <= aware(captured):
        raise DataQualityError("Archived article metadata conflicts with capture time")
    teams: dict[str, dict[str, Any]] = {}
    team = None
    for part in parser.parts:
        text = part["text"]
        if part["tag"] in {"h2", "matchup"}:
            team = None
            if "ALREADY COMPLETED" in text.upper():
                break
        if part["tag"] == "h3":
            team = NICKNAMES.get(text.upper())
            if team is None or team in teams:
                raise DataQualityError("Unknown or repeated injury team heading")
            teams[team] = {"players": {}, "no_designations": False, "warnings": []}
        if team is None or part["tag"] not in {"li", "p"} or not text:
            continue
        if text.rstrip(".").lower() == "no injury designations":
            teams[team]["no_designations"] = True
            continue
        status = re.match(r"^(OUT|QUESTIONABLE|DOUBTFUL)\s*:\s*(.*)$", text, re.I)
        if status is None:
            teams[team]["warnings"].append("unclassified note")
            continue
        for segment in _segments(status[2]):
            name = segment.split("(", 1)[0].strip()
            first, _, rest = name.partition(" ")
            position = first if first in POSITIONS else None
            name = rest if position else name
            if not name:
                raise DataQualityError("Empty injury designation")
            slugs = [
                slug
                for slug, label in part["links"].items()
                if _clean(label).casefold() == name.casefold()
            ]
            identity = "slug:" + slugs[0] if len(slugs) == 1 else "name:" + name.casefold()
            if identity in teams[team]["players"]:
                raise DataQualityError("Duplicate player injury designation within an edition")
            teams[team]["players"][identity] = {"status": status[1].title(), "position": position}
    if not teams:
        raise DataQualityError("Archived article contains no auditable team sections")
    result = {}
    for team, values in teams.items():
        players = list(values["players"].values())
        if values["no_designations"] and players:
            raise DataQualityError("Empty injury designation claim conflicts with listed players")
        state = (
            "no_designations"
            if values["no_designations"] and not values["warnings"]
            else "designations"
            if players and not values["warnings"]
            else "partial_or_pending"
        )
        out = [p for p in players if p["status"] == "Out"]
        classified = all(p["position"] is not None for p in out)
        result[team] = {
            "report_state": state,
            "parsed_players": len(players),
            "parsed_out_players": len(out),
            "unknown_position_out_players": sum(p["position"] is None for p in out),
            "warnings": len(values["warnings"]),
            "out_count": len(out) if state != "partial_or_pending" else None,
            **{
                name + "_out_count": sum(p["position"] in positions for p in out)
                if classified and state != "partial_or_pending"
                else None
                for name, positions in POSITION_GROUPS.items()
            },
        }
    return result


def fetch_injury_pilot(data_dir: Path) -> Path:
    directory = data_dir / "raw" / "team_availability" / "injury_week1_2025"
    path = directory / "manifest.json"
    if path.exists():
        load_injury_pilot(path)
        return path
    query = urllib.parse.urlencode(
        {
            "url": ARTICLE,
            "output": "json",
            "filter": "statuscode:200",
            "collapse": "digest",
            "from": "20250901",
            "to": "20250909",
        }
    )
    url = "https://web.archive.org/cdx/search/cdx?" + query
    payload, _ = _get(url, 50000)
    index = json.loads(payload)
    captures = [dict(zip(index[0], row, strict=True)) for row in index[1:]] if index else []
    if len(captures) > 20:
        raise DataQualityError("Week 1 archive probe exceeds 20-version bound")
    records = []
    index_digest = hashlib.sha256(payload).hexdigest()
    index_file = directory / ("index-" + index_digest + ".json")
    directory.mkdir(parents=True, exist_ok=True)
    if index_file.exists() and index_file.read_bytes() != payload:
        raise DataQualityError("Archive index snapshot changed")
    index_file.write_bytes(payload)
    for capture in captures:
        stamp = capture["timestamp"]
        captured = datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
        if (
            capture["original"] != ARTICLE
            or capture["mimetype"] != "text/html"
            or capture["statuscode"] != "200"
            or not "20250901" <= stamp[:8] <= "20250909"
        ):
            raise DataQualityError("Archive index URL/type changed")
        archive_url = f"https://web.archive.org/web/{stamp}id_/{ARTICLE}"
        body, resolved = _get(archive_url, 1000000)
        digest = base64.b32encode(hashlib.sha1(body).digest()).decode()
        if resolved != archive_url or digest != capture["digest"]:
            raise DataQualityError(
                "Archive redirected to a different version or body digest mismatched"
            )
        parse_injury_article(body, captured)
        compressed = gzip.compress(body, mtime=0)
        file = directory / (stamp + "-" + digest + ".html.gz")
        file.parent.mkdir(parents=True, exist_ok=True)
        if file.exists():
            if _gunzip(file.read_bytes()) != body:
                raise DataQualityError("Archive capture file changed")
        else:
            file.write_bytes(compressed)
        records.append(
            {
                "captured_at_utc": captured.isoformat(),
                "archive_url": archive_url,
                "original_url": ARTICLE,
                "cdx_sha1_base32": digest,
                "body_sha256": hashlib.sha256(body).hexdigest(),
                "file": file.name,
                "compressed_sha256": sha256_file(file),
                "bytes": file.stat().st_size,
            }
        )
        print(f"Verified injury archive capture {stamp}", flush=True)
    write_json(
        path,
        {
            "format_version": 1,
            "retrieved_at_utc": datetime.now(UTC).isoformat(),
            "index_url": url,
            "index_sha256": index_digest,
            "index_file": index_file.name,
            "captures": records,
        },
    )
    return path


def load_injury_pilot(path: Path) -> list[tuple[datetime, dict[str, dict[str, Any]], str]]:
    manifest = read_json(path)
    if manifest["format_version"] != 1:
        raise DataQualityError("Unsupported injury archive format")
    index_path = path.parent / manifest["index_file"]
    if manifest["index_file"] != "index-" + manifest["index_sha256"] + ".json":
        raise DataQualityError("Unexpected archive index filename")
    if sha256_file(index_path) != manifest["index_sha256"]:
        raise DataQualityError("Injury archive index checksum mismatch")
    index = json.loads(index_path.read_bytes())
    tuples = {
        (r["timestamp"], r["original"], r["digest"])
        for row in index[1:]
        if (r := dict(zip(index[0], row, strict=True)))["statuscode"] == "200"
    }
    result = []
    for record in manifest["captures"]:
        captured = aware(datetime.fromisoformat(record["captured_at_utc"]))
        stamp = captured.strftime("%Y%m%d%H%M%S")
        if record["file"] != stamp + "-" + record["cdx_sha1_base32"] + ".html.gz":
            raise DataQualityError("Unexpected archive capture filename/time")
        file = path.parent / record["file"]
        if sha256_file(file) != record["compressed_sha256"]:
            raise DataQualityError("Compressed injury capture checksum mismatch")
        body = _gunzip(file.read_bytes())
        if (
            hashlib.sha256(body).hexdigest() != record["body_sha256"]
            or base64.b32encode(hashlib.sha1(body).digest()).decode() != record["cdx_sha1_base32"]
        ):
            raise DataQualityError("Injury archive body digest mismatch")
        if (stamp, record["original_url"], record["cdx_sha1_base32"]) not in tuples:
            raise DataQualityError("Injury receipt time/digest disagrees with preserved index")
        if (
            record["original_url"] != ARTICLE
            or record["archive_url"] != f"https://web.archive.org/web/{stamp}id_/{ARTICLE}"
        ):
            raise DataQualityError("Injury archive receipt URL/time mismatch")
        result.append((captured, parse_injury_article(body, captured), record["archive_url"]))
    if len({r[0] for r in result}) != len(result):
        raise DataQualityError("Duplicate injury archive capture timestamp")
    return sorted(result, key=lambda value: value[0])


def injury_as_of(
    captures: list[tuple[datetime, dict[str, dict[str, Any]], str]],
    team: str,
    season: int,
    week: int,
    cutoff: datetime,
) -> dict[str, Any]:
    cutoff = aware(cutoff)
    eligible = [r for r in captures if r[0] < cutoff]
    missing = {
        "status": "unavailable",
        "captured_at_utc": None,
        "archive_url": None,
        "out_count": None,
        "reason": "pilot does not cover this season/week"
        if season != 2025 or week != 1
        else "no earlier archive edition",
    }
    if season != 2025 or week != 1 or not eligible:
        return missing
    captured, reports, url = eligible[-1]
    if (cutoff - captured).total_seconds() > 72 * 3600 or team not in reports:
        return {
            **missing,
            "captured_at_utc": captured,
            "archive_url": url,
            "reason": "stale edition or missing team section",
        }
    report = reports[team]
    return {
        "status": report["report_state"],
        "captured_at_utc": captured,
        "archive_url": url,
        "reason": None,
        **report,
    }
