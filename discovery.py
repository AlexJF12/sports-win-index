#!/usr/bin/env python3
"""
What makes a share findable: hashtags, and one team called by its full name.

Almost nobody sees a small account's posts in the Following feed. Its posts
reach strangers through custom feeds, and most sports feeds on Bluesky pick
posts by one of two things: a hashtag (#nba, #NHL, a team tag) or a keyword,
usually the league or a team's full name ("Los Angeles Kings"). A post that
says "Dodgers/Lakers/Kings/Rams" matches neither, so it reaches nobody.

So each share gets:

  inline tags   up to three, at the foot of the post: the lead team, then the
                leagues in season among the post's teams. Each one carries a
                tag facet; without it, "#NBA" posted through the API is plain
                text and no feed sees it.
  hidden tags   the post record's `tags` field (up to eight, never shown): the
                cities and the other teams. Cheap, and searchable.
  a full name   the first team whose full name fits into the text without
                reading oddly ("the Lakers" -> "the Los Angeles Lakers",
                "Seattle: Mariners/..." -> "Seattle Mariners/..."). Once, not
                for every team: a post is for people, not for keyword feeds.

The teams come from the post's own words, matched against city_groups.json:
a city named in the post (or listed as one of its cities), then that city's
nicknames where they appear. A nickname two of the post's cities share
("Cardinals" in a St. Louis-Phoenix head to head) is left out rather than
guessed.

A guess-the-city post gets league tags only. Its city is the answer, and a
#Mariners under the clues would give it away.
"""

import json
import os
import re
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
GROUPS_PATH = os.path.join(HERE, "city_groups.json")
TEAMS_PATH = os.path.join(HERE, "teams.json")

INLINE_MAX = 3     # more than this reads as spam, and feeds don't need it
HIDDEN_MAX = 8     # the lexicon's cap on a post's `tags`

# Months each league is playing, regular season through finals. A league out of
# season gets no tag: #MLB on a November post is a baseball tag on a post about
# hockey and football.
SEASON = {
    "mlb": {3, 4, 5, 6, 7, 8, 9, 10},
    "nba": {10, 11, 12, 1, 2, 3, 4, 5, 6},
    "nhl": {10, 11, 12, 1, 2, 3, 4, 5, 6},
    "nfl": {9, 10, 11, 12, 1, 2},
}
LEAGUE_ORDER = ["nfl", "nba", "nhl", "mlb"]

_cache = {}


def load(groups_path: str = GROUPS_PATH, teams_path: str = TEAMS_PATH) -> dict:
    """Every city's teams: {city: [{league, nickname, full}, ...]}."""
    if (groups_path, teams_path) in _cache:
        return _cache[(groups_path, teams_path)]
    with open(groups_path) as f:
        groups = json.load(f)
    with open(teams_path) as f:
        names = json.load(f)
    cities = {}
    for group in groups:
        teams = cities.setdefault(group["city"], [])
        for team in group["teams"]:
            entry = {"city": group["city"], "league": team["league"],
                     "nickname": team["nickname"],
                     "full": names.get(team["league"], {}).get(team["abbr"])
                     or f"{group['city']} {team['nickname']}"}
            if entry not in teams:
                teams.append(entry)
    _cache[(groups_path, teams_path)] = cities
    return cities


def word(name: str) -> re.Pattern:
    return re.compile(rf"(?<![\w#]){re.escape(name)}(?!\w)")


def tag_of(name: str) -> str:
    """"Trail Blazers" -> "TrailBlazers", "St. Louis" -> "StLouis"."""
    return re.sub(r"[^0-9A-Za-z]", "", name)


def in_season(league: str, day: date) -> bool:
    return day.month in SEASON.get(league, set())


def teams_in(text: str, cities: dict, also: list = ()) -> list:
    """The teams a text talks about, in the order it first names them."""
    named = [city for city in cities if word(city).search(text)]
    named += [city for city in also if city in cities and city not in named]
    candidates = [team for city in named for team in cities[city]]
    found = []
    for team in candidates:
        m = word(team["nickname"]).search(text)
        if not m:
            continue
        if any(other["nickname"] == team["nickname"] and other["full"] != team["full"]
               for other in candidates):
            continue                       # two of the post's teams answer to it
        if all(f["full"] != team["full"] for _, f in found):
            found.append((m.start(), team))
    return [team for _, team in sorted(found, key=lambda pair: pair[0])]


def leagues_in(text: str) -> list:
    """League names spelled out in the text: "Two teams (NBA/NHL)"."""
    return [lg for lg in LEAGUE_ORDER if word(lg.upper()).search(text)]


def unique(tags: list) -> list:
    seen, out = set(), []
    for tag in tags:
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            out.append(tag)
    return out


def full_name(text: str, team: dict) -> str | None:
    """The text with this team called by its full name once, or None when it
    wouldn't read naturally anywhere."""
    nick, full, city = team["nickname"], team["full"], team["city"]
    if full == nick:
        return None
    if word(full).search(text):
        return text
    # "the Lakers are 5-0" -> "the Los Angeles Lakers are 5-0"
    m = re.search(rf"\b([Tt]he) {re.escape(nick)}(?!\w)", text)
    if m:
        return f"{text[:m.start()]}{m.group(1)} {full}{text[m.end():]}"
    # "Seattle: Mariners/Kraken" -> "Seattle Mariners/Kraken", only where the
    # full name is the city and the nickname (not "Raleigh: Hurricanes")
    if full == f"{city} {nick}":
        m = re.search(rf"(?<!\w){re.escape(city)}: {re.escape(nick)}(?!\w)", text)
        if m:
            return f"{text[:m.start()]}{full}{text[m.end():]}"
    # "(Lakers 6, Ducks 5)" -> "(Los Angeles Lakers 6, Ducks 5)", unless the
    # city is already right before the bracket
    m = re.search(rf"\({re.escape(nick)}(?!\w)", text)
    if m and not text[:m.start()].rstrip().endswith(city):
        return f"{text[:m.start()]}({full}{text[m.end():]}"
    return None


def discover(post: dict, text: str, cities: dict | None = None) -> dict:
    """What to add to a share whose text is `text`:
    {"inline": [...], "hidden": [...], "named": text-with-a-full-name or None}.
    Tags come without the "#"."""
    cities = load() if cities is None else cities
    day = date.fromisoformat(post["date"])

    if post.get("kind") == "guess":
        leagues = [lg for lg in leagues_in(text) if in_season(lg, day)]
        return {"inline": [lg.upper() for lg in leagues][:INLINE_MAX],
                "hidden": [], "named": None}

    source = "\n".join(part for part in (text, post.get("title"), post.get("dek"))
                       if part)
    teams = teams_in(source, cities, post.get("cities") or [])
    playing = [t for t in teams if in_season(t["league"], day)]
    lead = (playing or teams or [None])[0]

    named_cities = [c for c in cities if word(c).search(source)]
    named_cities += [c for c in post.get("cities") or [] if c in cities
                     and c not in named_cities]
    named_cities.sort(key=lambda c: (m.start() if (m := word(c).search(text)) else len(text)))

    if lead:
        leagues = list(dict.fromkeys(t["league"] for t in playing))   # in order named
        first = [tag_of(lead["nickname"])]
    elif named_cities:
        # a post about a whole city ("Los Angeles leads the banner race") is
        # about every team it has: the city, then the leagues in season there
        city = named_cities[0]
        leagues = [lg for lg in LEAGUE_ORDER if in_season(lg, day)
                   and any(t["league"] == lg for t in cities[city])]
        first = [tag_of(city)]
    else:
        leagues, first = [], []
    inline = unique(first + [lg.upper() for lg in leagues])[:INLINE_MAX]

    taken = {tag.lower() for tag in inline}
    hidden = [tag for tag in unique([tag_of(c) for c in named_cities]
                                    + [tag_of(t["nickname"]) for t in teams])
              if tag.lower() not in taken][:HIDDEN_MAX]

    named = None
    for team in ([lead] if lead else []) + [t for t in playing + teams if t is not lead]:
        named = full_name(text, team)
        if named is not None:
            break
    return {"inline": inline, "hidden": hidden,
            "named": named if named != text else None}
