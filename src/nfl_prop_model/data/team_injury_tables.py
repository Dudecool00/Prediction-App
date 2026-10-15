"""Parse dated official injury tables without treating blank statuses as healthy."""

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.team_games import TEAM_ALIASES
from nfl_prop_model.data.team_injury_archive import NICKNAMES, POSITION_GROUPS, _gunzip

GROUPS = {**POSITION_GROUPS, "OL": POSITION_GROUPS["OL"] | {"LT", "RT", "LG", "RG"}}
HEADERS = ["Player", "Position", "Injuries", "Practice Status", "Game Status"]
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}


@dataclass(eq=False)
class Node:
    tag: str
    attrs: dict[str, str]
    children: list["Node | str"] = field(default_factory=list)

    def text(self) -> str:
        return " ".join(
            " ".join(c.text() if isinstance(c, Node) else c for c in self.children).split()
        )

    def find(self, *, tag: str = "", cls: str = "") -> list["Node"]:
        matches = []
        for child in self.children:
            if isinstance(child, Node):
                if (not tag or child.tag == tag) and (
                    not cls or cls in child.attrs.get("class", "").split()
                ):
                    matches.append(child)
                matches.extend(child.find(tag=tag, cls=cls))
        return matches


class TableDocument(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.root = Node("root", {})
        self.stack = [self.root]
        self.count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.count += 1
        if self.count > 30000 or len(self.stack) > 100:
            raise DataQualityError("Official table document exceeds parser bounds")
        node = Node(tag, {k: v or "" for k, v in attrs})
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                self.stack = self.stack[:index]
                break

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def _team(table: Node) -> dict[str, Any]:
    heads = table.find(tag="thead")
    bodies = table.find(tag="tbody")
    if (
        len(heads) != 1
        or len(bodies) != 1
        or [n.text() for n in heads[0].find(tag="th")] != HEADERS
    ):
        raise DataQualityError("Official injury table column contract changed")
    players: set[str] = set()
    names: set[str] = set()
    observed = {"Out": 0, "Questionable": 0, "Doubtful": 0}
    group_out = dict.fromkeys(GROUPS, 0)
    unknown_position = blank = 0
    unsupported = False
    for row in bodies[0].find(tag="tr"):
        cells = row.find(tag="td")
        # Empty/colspan notices provide no proof of a complete final report.
        if len(cells) != 5 or any(c.attrs.get("colspan", "1") != "1" for c in cells):
            unsupported = True
            continue
        name, position, _, _, status = [c.text() for c in cells]
        links = cells[0].find(tag="a")
        slugs = [a.attrs.get("href", "") for a in links]
        if (
            not name
            or len(slugs) != 1
            or not re.fullmatch(r"/players/[a-z0-9-]+/", slugs[0])
            or slugs[0] in players
            or name.casefold() in names
        ):
            raise DataQualityError("Ambiguous or duplicate official injury player identity")
        players.add(slugs[0])
        names.add(name.casefold())
        if not status:
            blank += 1
        elif status not in observed:
            unsupported = True
        else:
            observed[status] += 1
            if status == "Out":
                groups = [g for g, positions in GROUPS.items() if position.upper() in positions]
                if len(groups) == 1:
                    group_out[groups[0]] += 1
                else:
                    unknown_position += 1
    has_designations = bool(sum(observed.values())) and not unsupported
    return {
        "status": "designations_present" if has_designations else "practice_only_or_pending",
        "player_rows": len(players),
        "blank_game_status_rows": blank,
        "unsupported_rows": unsupported,
        "observed_out_count": observed["Out"] if has_designations else None,
        "observed_questionable_count": observed["Questionable"] if has_designations else None,
        "observed_doubtful_count": observed["Doubtful"] if has_designations else None,
        "unknown_out_positions": unknown_position if has_designations else None,
        **{
            g + "_observed_out_count": n if has_designations and not unknown_position else None
            for g, n in group_out.items()
        },
        # Neither a blank cell nor another player's designation proves this team's finality.
        "final_report_verified": False,
    }


def parse_tables(payload: bytes, season: int, week: int) -> dict[str, dict[str, Any]]:
    html = _gunzip(payload) if payload.startswith(b"\x1f\x8b") else payload
    if len(html) > 1000000:
        raise DataQualityError("Official injury table entity exceeds 1 MB")
    parser = TableDocument()
    parser.feed(html.decode("utf-8"))
    parser.close()
    root = parser.root
    title = root.find(tag="title")
    h1 = root.find(tag="h1")
    if (
        len(title) != 1
        or not re.search(rf"Week {week} of the {season} Season\b", title[0].text(), re.I)
        or [n.text() for n in h1].count(f"{season} NFL Injury Report") != 1
        or any(n.text() not in {title[0].text(), f"{season} NFL Injury Report"} for n in h1)
        or f"Injuries - WEEK {week}" not in [n.text() for n in root.find(tag="h2")]
    ):
        raise DataQualityError("Official injury body does not match expected season/week")
    units = root.find(cls="nfl-o-injury-report__unit")
    if not units or len(units) > 16:
        raise DataQualityError("Official injury matchup units missing or ambiguous")
    teams: dict[str, dict[str, Any]] = {}
    for unit in units:
        abbreviations = [
            TEAM_ALIASES.get(n.text(), n.text())
            for n in unit.find(cls="nfl-c-matchup-strip__team-abbreviation")
        ]
        labels = unit.find(cls="d3-o-section-sub-title")
        tables = unit.find(tag="table")
        if (
            len(abbreviations) != 2
            or len(set(abbreviations)) != 2
            or not set(abbreviations) <= set(NICKNAMES.values())
            or len(labels) != 2
            or len(tables) != 2
            or [NICKNAMES.get(n.text().upper()) for n in labels] != abbreviations
        ):
            raise DataQualityError("Official injury table team/matchup attribution is ambiguous")
        # Label/table order must alternate inside the unit; a displaced label cannot rebind rows.
        ordered = unit.find()
        if (
            not ordered.index(labels[0])
            < ordered.index(tables[0])
            < ordered.index(labels[1])
            < ordered.index(tables[1])
        ):
            raise DataQualityError("Official injury table labels do not bind adjacent tables")
        for index, (team, table) in enumerate(zip(abbreviations, tables, strict=True)):
            if team in teams:
                raise DataQualityError("Duplicate official injury team section")
            teams[team] = {"opponent": abbreviations[1 - index], **_team(table)}
    return teams
