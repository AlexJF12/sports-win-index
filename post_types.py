#!/usr/bin/env python3
"""
The four post types that have no analysis script of their own.

City of the day, the banner race and the out-of-the-norm hunt each have a
script that writes a folder, and publish_blog.py reads the folder. These four
are small enough to be a function each: they take the scores, return a post in
the shape the publisher files, and draw one chart on the way.

    hotcold      the longest active winning and losing runs, city by city
    h2h          two cities side by side on the year, picked because their
                 teams just met or because they sit together in the standings
    timemachine  this calendar month in an earlier year: who took the banner,
                 and where that city is now
    guess        a season chart with the name taken off, and three clues

Every builder returns None when it has nothing to say, and plan_posts.py moves
on to the next type. Each also takes the posts already published, so it can
avoid the city, pair or year it used last time.

A post carries its own `hook` — the text that goes to Bluesky. It is written
here, next to the numbers, and it is written as something a person would say:
a claim, then a question where there is one worth asking.
"""

import calendar
import logging
import os
import random
from datetime import date, timedelta

import hooks
from banner_race import by_city, month_tallies, ranked
from city_of_the_day import draw, profile, qualifies
from fandom_analysis import (FIRST_YEAR, MONTH_NAMES, display_label, group_games,
                             ordinal, pretty_date, season_series, tally,
                             trailing_streak)

log = logging.getLogger(__name__)

OUT_ROOT = "content"

ACTIVE_DAYS = 7          # a run only counts as active if it was added to this recently
RUN_LOOKBACK = 150       # how far back a run is traced; nothing gets close to it
MIN_RUN = 3              # shorter than this is not a run
RUNS_PER_SIDE = 5
PAIR_COOLDOWN = 45       # days before the same two cities are compared again
GUESS_COOLDOWN = 30
YEAR_COOLDOWN = 8        # time-machine posts before a year comes round again
MIN_BANNER_FIELD = 5     # a month with fewer cities playing is not a race
MIN_BANNER_GAMES = 6


def ymd(day: date) -> str:
    return day.strftime("%Y%m%d")


def signed(n: float) -> str:
    n = 0.0 if abs(n) < 0.05 else n
    return f"{n:+.1f}"


def record(r: dict) -> str:
    return f"{r['w']}-{r['l']}" + (f"-{r['t']}" if r.get("t") else "")


def nicknames(group: dict) -> str:
    return display_label(group).split(": ", 1)[-1]


def image(kind: str, name: str, caption: str, out_root: str = OUT_ROOT) -> dict:
    return {"src": os.path.join(out_root, kind, name), "file": f"{kind}-{name}",
            "caption": caption, "alt": caption}


def out_path(kind: str, name: str, out_root: str = OUT_ROOT) -> str:
    os.makedirs(os.path.join(out_root, kind), exist_ok=True)
    return os.path.join(out_root, kind, name)


def count(posts: list, kind: str, ref: date) -> int:
    """How many posts of this type came before — what turns the wording."""
    return len(earlier(posts, kind, ref))


def earlier(posts: list, kind: str, ref: date, days: int | None = None) -> list:
    """Posts of one kind from before ref, newest first."""
    out = [p for p in posts if p["kind"] == kind and p["date"] < ref.isoformat()
           and (days is None or (ref - date.fromisoformat(p["date"])).days <= days)]
    return sorted(out, key=lambda p: p["date"], reverse=True)


def city_standings(by_team: dict, groups: list, ref: date) -> list:
    """The year so far, one row per city — its best group, best first."""
    rows = []
    for group in groups:
        games = group_games(by_team, group, f"{ref.year}0101", ymd(ref))
        if games:
            rows.append({"group": group, **tally(games)})
    rows.sort(key=lambda r: (-r["weighted"], -r["w"], r["group"]["name"]))
    best = by_city(rows)
    for place, row in enumerate(best, 1):
        row["place"] = place
    return best


# --- hot & cold ---------------------------------------------------------------

def active_runs(by_team: dict, groups: list, ref: date) -> dict:
    """{"W": [...], "L": [...]} — each city's longest active run, longest first.

    A group's run is its teams' results in the order they were played, so a
    four-team city needs every team to keep winning and a one-team city needs
    one. A city with several groups is counted once, by its longest run.
    """
    lo, hi = ymd(ref - timedelta(days=RUN_LOOKBACK)), ymd(ref)
    fresh = ymd(ref - timedelta(days=ACTIVE_DAYS - 1))
    best = {"W": {}, "L": {}}
    for group in sorted(groups, key=lambda g: g["name"]):
        games = group_games(by_team, group, lo, hi)
        run = trailing_streak(games)
        if not run or run["length"] < MIN_RUN or games[-1]["date"] < fresh:
            continue
        tail = games[-run["length"]:]
        by_nick = {}
        for game in tail:
            by_nick[game["nickname"]] = by_nick.get(game["nickname"], 0) + 1
        row = {"city": group["city"], "label": display_label(group),
               "type": run["type"], "length": run["length"],
               "since": tail[0]["date"],
               "weighted": round(sum(g["weighted"] for g in tail), 1),
               "teams": sorted(by_nick.items(), key=lambda kv: -kv[1])}
        held = best[run["type"]].get(group["city"])
        if held is None or row["length"] > held["length"]:
            best[run["type"]][group["city"]] = row
    return {kind: sorted(rows.values(), key=lambda r: (-r["length"], r["city"]))
            for kind, rows in best.items()}


def team_split(row: dict) -> str:
    """"Lakers 6, Ducks 5, Rams 2" — or just the team, when it is one team's run."""
    if len(row["teams"]) == 1:
        return row["teams"][0][0]
    return ", ".join(f"{nick} {n}" for nick, n in row["teams"])


def run_words(row: dict) -> str:
    return f"{row['length']} straight {'wins' if row['type'] == 'W' else 'losses'}"


def hotcold_post(by_team: dict, groups: list, ref: date, posts: list = (),
                 out_root: str = OUT_ROOT, images: bool = True) -> dict | None:
    runs = active_runs(by_team, groups, ref)
    hot, cold = runs["W"][:RUNS_PER_SIDE], runs["L"][:RUNS_PER_SIDE]
    if not hot or not cold:
        return None
    top, bottom = hot[0], cold[0]

    title = (f"{top['city']} has won {top['length']} straight. "
             f"{bottom['city']} has lost {bottom['length']} straight.")
    hook = hooks.hotcold_hook(top, bottom, team_split(top), team_split(bottom),
                              count(posts, "hotcold", ref))
    stats = [
        {"label": "Hottest", "value": f"{top['label']} — {run_words(top)} since "
                                      f"{pretty_date(top['since'])}, {signed(top['weighted'])} weighted"},
        {"label": "Coldest", "value": f"{bottom['label']} — {run_words(bottom)} since "
                                      f"{pretty_date(bottom['since'])}, {signed(bottom['weighted'])} weighted"},
        {"label": "How a run is counted",
         "value": "Every game by every team in the group, in the order they "
                  "were played. One loss by any of them ends it."},
    ]
    table = {"columns": ["Run", "City", "Teams in it", "Since"],
             "rows": [[f"{'W' if r['type'] == 'W' else 'L'}{r['length']}", r["city"],
                       team_split(r), pretty_date(r["since"])] for r in hot + cold]}
    caption = ("The five longest active winning runs and the five longest active "
               "losing runs, one bar per city.")
    if images:
        import render_posts
        render_posts.render_hotcold(hot, cold, ref, out_path("hotcold", "runs.png", out_root))
    return {
        "date": ref.isoformat(), "kind": "hotcold", "title": title,
        "dek": f"The longest active runs in every city, through {ref.strftime('%B %-d, %Y')}",
        "hook": hook,
        "score": hooks.hotcold_score(
            top, bottom, (earlier(posts, "hotcold", ref)[:1] or [{}])[0].get("cities") or []),
        "cities": [top["city"], bottom["city"]],
        "sections": [{"heading": None, "subhead": None, "stats": stats, "table": table,
                      "images": [image("hotcold", "runs.png", caption, out_root)]}],
    }


# --- head to head -------------------------------------------------------------

def team_cities(groups: list) -> dict:
    return {(t["league"], t["abbr"]): g["city"] for g in groups for t in g["teams"]}


def meetings_on(by_team: dict, groups: list, day: date) -> list:
    """Games on one day between teams from two different cities on the map."""
    where, seen, out = team_cities(groups), set(), []
    for (league, abbr), rows in by_team.items():
        for row in rows:
            if row["date"] != ymd(day) or row["game_id"] in seen or not row["winner"]:
                continue
            seen.add(row["game_id"])
            home = where.get((league, row["home_team"]))
            away = where.get((league, row["away_team"]))
            if home and away and home != away:
                out.append({"league": league, "row": row, "cities": (home, away)})
    return out


def nickname_of(groups: list, league: str, abbr: str) -> str:
    for group in groups:
        for team in group["teams"]:
            if (team["league"], team["abbr"]) == (league, abbr):
                return team["nickname"]
    return abbr


def game_line(groups: list, meeting: dict) -> str:
    """"Celtics 112, Lakers 104" — winner first."""
    row, league = meeting["row"], meeting["league"]
    sides = [(row["home_team"], row["home_score"]), (row["away_team"], row["away_score"])]
    sides.sort(key=lambda s: s[0] != row["winner"])
    return ", ".join(f"{nickname_of(groups, league, abbr)} {score}" for abbr, score in sides)


def pick_pair(standings: list, meetings: list, used: set) -> tuple | None:
    """(row_a, row_b, meeting or None), a ahead of b on the year.

    Two cities whose teams met on the reference date come first — the game is
    the reason to compare them — and among those, the two closest together in
    the standings. With no such game, the closest two neighbors.
    """
    by_name = {row["group"]["city"]: row for row in standings}
    options = []
    for meeting in meetings:
        a, b = (by_name.get(c) for c in meeting["cities"])
        # the city is compared as its best group, so the game only counts as
        # the reason when both teams in it belong to the groups on the chart
        row, playing = meeting["row"], set()
        for side in (a, b):
            if side:
                playing |= {t["abbr"] for t in side["group"]["teams"]
                            if t["league"] == meeting["league"]}
        if a and b and {row["home_team"], row["away_team"]} <= playing:
            # stakes first: two cities near the top of the year beat two near
            # the bottom, and a wide gap between them counts against the pair
            options.append((a["place"] + b["place"] + 2 * abs(a["place"] - b["place"]),
                            0, a, b, meeting))
    for a, b in zip(standings, standings[1:]):
        options.append((1000 + abs(a["weighted"] - b["weighted"]), a["place"], a, b, None))
    for _, _, a, b, meeting in sorted(options, key=lambda o: o[:2]):
        if frozenset((a["group"]["city"], b["group"]["city"])) not in used:
            return (a, b, meeting) if a["place"] < b["place"] else (b, a, meeting)
    return None


def meetings_this_year(by_team: dict, a: dict, b: dict, ref: date) -> dict:
    """How often the two groups' teams have played each other this year."""
    theirs = {(t["league"], t["abbr"]) for t in b["teams"]}
    games = [g for g in group_games(by_team, a, f"{ref.year}0101", ymd(ref))
             if (g["league"], g["opponent"]) in theirs]
    return {"games": len(games), "a": sum(g["result"] == "W" for g in games),
            "b": sum(g["result"] == "L" for g in games)}


def h2h_post(by_team: dict, groups: list, ref: date, posts: list = (),
             out_root: str = OUT_ROOT, images: bool = True) -> dict | None:
    standings = city_standings(by_team, groups, ref)
    used = {frozenset(p.get("cities") or []) for p in earlier(posts, "h2h", ref, PAIR_COOLDOWN)}
    picked = pick_pair(standings, meetings_on(by_team, groups, ref), used)
    if not picked:
        return None
    a, b, meeting = picked
    field = len(standings)
    city_a, city_b = a["group"]["city"], b["group"]["city"]
    met = meetings_this_year(by_team, a["group"], b["group"], ref)

    def line(row):
        return (f"{display_label(row['group'])} — {record(row)}, "
                f"{signed(row['weighted'])} weighted, {ordinal(row['place'])} of {field}")

    stats = [{"label": city_a, "value": line(a)}, {"label": city_b, "value": line(b)},
             {"label": "The gap", "value": f"{city_a} by {abs(a['weighted'] - b['weighted']):.1f}"}]
    played = game_line(groups, meeting) if meeting else None
    if played:
        stats.append({"label": f"{ref.strftime('%B %-d')}", "value": played})
    if met["games"]:
        stats.append({"label": f"Meetings in {ref.year}",
                      "value": f"{met['games']} — {city_a} {met['a']}, {city_b} {met['b']}"})
    hook = hooks.h2h_hook(a, b, field, ref.year, played, met, ref.strftime("%b %-d"),
                          count(posts, "h2h", ref))

    series = [{"city": row["group"]["city"], "total": row["weighted"],
               "series": season_series(by_team, row["group"], ref.year, ref)}
              for row in (a, b)]
    caption = ("Both cities' cumulative weighted index this year, every game "
               "since January 1 added up.")
    if images:
        import render_posts
        render_posts.render_h2h(series, ref, out_path("h2h", "year.png", out_root))
    return {
        "date": ref.isoformat(), "kind": "h2h",
        "title": f"{city_a} vs {city_b}: who's had the better {ref.year}?",
        "dek": f"{nicknames(a['group'])} against {nicknames(b['group'])}, "
               f"through {ref.strftime('%B %-d')}",
        "hook": hook, "score": hooks.h2h_score(a, b, field, bool(meeting)),
        "cities": [city_a, city_b],
        "sections": [{"heading": None, "subhead": None, "stats": stats, "table": None,
                      "images": [image("h2h", "year.png", caption, out_root)]}],
    }


# --- time machine -------------------------------------------------------------

def pick_year(tallies: dict, groups: list, ref: date, used: list) -> tuple | None:
    """(year, that month's cities in order) for a year worth going back to.

    Years come up in a date-seeded shuffle, skipping the ones used most
    recently, and skipping any where the month was too thin to be a race
    (a lockout, 2020's spring).
    """
    years = list(range(FIRST_YEAR, ref.year))
    random.Random(f"timemachine-{ref.isoformat()}").shuffle(years)
    skip = set(used[:YEAR_COOLDOWN])
    for pool in ([y for y in years if y not in skip], years):
        for year in pool:
            rows = by_city(ranked(tallies.get(f"{year:04d}{ref.month:02d}", {}), groups))
            if len(rows) >= MIN_BANNER_FIELD and rows[0]["games"] >= MIN_BANNER_GAMES:
                return year, rows
    return None


def top_team(by_team: dict, group: dict, year: int, month: int) -> dict | None:
    """The team that did most of the winning in one month."""
    last = calendar.monthrange(year, month)[1]
    games = group_games(by_team, group, f"{year:04d}{month:02d}01",
                        f"{year:04d}{month:02d}{last:02d}")
    best = None
    for team in group["teams"]:
        mine = [g for g in games if (g["league"], g["abbr"]) == (team["league"], team["abbr"])]
        if mine:
            row = {"nickname": team["nickname"], **tally(mine)}
            if best is None or row["weighted"] > best["weighted"]:
                best = row
    return best


def timemachine_post(by_team: dict, groups: list, ref: date, posts: list = (),
                     out_root: str = OUT_ROOT, images: bool = True) -> dict | None:
    tallies = month_tallies(by_team, groups, ref)
    used = [p.get("year") for p in earlier(posts, "timemachine", ref)]
    picked = pick_year(tallies, groups, ref, used)
    if not picked:
        return None
    year, rows = picked
    month = MONTH_NAMES[ref.month]
    champ, second = rows[0], rows[1]
    city = champ["group"]["city"]
    ago = ref.year - year
    years_ago = "A year ago" if ago == 1 else f"{ago} years ago"

    now_rows = by_city(ranked(tallies.get(f"{ref.year:04d}{ref.month:02d}", {}), groups))
    now = next(((n, r) for n, r in enumerate(now_rows, 1)
                if r["group"]["city"] == city), None)
    star = top_team(by_team, champ["group"], year, ref.month)

    stats = [
        {"label": f"{month} {year} banner",
         "value": f"{display_label(champ['group'])} — {record(champ)}, "
                  f"{signed(champ['weighted'])} weighted"},
        {"label": "Runner-up",
         "value": f"{display_label(second['group'])} — {record(second)}, "
                  f"{signed(second['weighted'])} weighted"},
    ]
    if not (star and len(champ["group"]["teams"]) > 1):
        star = None                # a one-team city carried itself
    if star:
        stats.append({"label": "Who carried it",
                      "value": f"{star['nickname']} — {record(star)}, "
                               f"{signed(star['weighted'])} weighted"})
    if now:
        place, row = now
        stats.append({"label": f"{city} this {month}, through day {ref.day}",
                      "value": f"{ordinal(place)} of {len(now_rows)} — {record(row)}, "
                               f"{signed(row['weighted'])} weighted"})
    standing_now = (now[0], len(now_rows)) if now else None
    hook = hooks.timemachine_hook(city, month, year, years_ago, champ, star,
                                  standing_now, count(posts, "timemachine", ref))

    top = [{"city": r["group"]["city"], "weighted": r["weighted"], "record": record(r)}
           for r in rows[:5]]
    table = {"columns": ["Place", "City", "Teams", "Record", "Weighted"],
             "rows": [[str(n), r["group"]["city"], nicknames(r["group"]), record(r),
                       signed(r["weighted"])] for n, r in enumerate(rows[:5], 1)]}
    caption = f"The top five cities of {month} {year} on the weighted index, the banner winner picked out."
    if images:
        import render_posts
        render_posts.render_timemachine(top, month, year,
                                        out_path("timemachine", "month.png", out_root))
    return {
        "date": ref.isoformat(), "kind": "timemachine",
        "title": f"{month} {year}: the banner went to {city}",
        "dek": f"{years_ago} this month — {nicknames(champ['group'])}, "
               f"{signed(champ['weighted'] - second['weighted'])} clear of "
               f"{second['group']['city']}",
        "hook": hook, "score": hooks.timemachine_score(standing_now),
        "cities": [city], "year": year,
        "sections": [{"heading": None, "subhead": None, "stats": stats, "table": table,
                      "images": [image("timemachine", "month.png", caption, out_root)]}],
    }


# --- guess the city -----------------------------------------------------------

COUNT_WORDS = {1: "One team", 2: "Two teams", 3: "Three teams", 4: "Four teams"}


def guess_post(by_team: dict, groups: list, ref: date, posts: list = (),
               out_root: str = OUT_ROOT, images: bool = True,
               exclude: tuple = ()) -> dict | None:
    # the daily's own draw, with its cooldown fed by the cities this type and
    # city of the day have used lately, so the two never show the same city
    # in the same stretch — and seeded apart from the daily's draw
    history = [{"date": p["date"], "city": city}
               for kind in ("guess", "daily")
               for p in earlier(posts, kind, ref, GUESS_COOLDOWN)
               for city in p.get("cities") or []]
    history += [{"date": (ref - timedelta(days=1)).isoformat(), "city": c} for c in exclude]
    prof = draw(groups, by_team, ref, history, seed=f"guess-{ref.isoformat()}")
    if prof is None:
        return None
    season = prof["season"]
    leagues = "/".join(t["league"].upper() for t in prof["teams"])
    teams = f"{COUNT_WORDS.get(len(prof['teams']), str(len(prof['teams'])) + ' teams')} ({leagues})"

    standings = city_standings(by_team, groups, ref)
    place = 1 + sum(1 for r in standings if r["weighted"] > season["weighted"]
                    and r["group"]["city"] != prof["city"])
    best = max(prof["past_seasons"], key=lambda p: p["final"])
    worst = min(prof["past_seasons"], key=lambda p: p["final"])

    clue_year = (f"{season['w']}-{season['l']} this year, "
                 f"{ordinal(place)} of {len(standings)} cities")
    stats = [
        {"label": "Clue 1", "value": teams},
        {"label": "Clue 2", "value": f"{clue_year} ({signed(season['weighted'])} weighted)"},
        {"label": "Clue 3", "value": f"Best year on record: {best['year']} "
                                     f"({signed(best['final'])}). Worst: {worst['year']} "
                                     f"({signed(worst['final'])})"},
    ]
    hook = hooks.guess_hook([teams, clue_year,
                             f"Best year on record: {best['year']}. Worst: {worst['year']}"],
                            count(posts, "guess", ref))
    caption = ("A mystery city's cumulative weighted index this year, against "
               "its own earlier seasons in gray.")
    if images:
        import render_posts
        render_posts.render_guess(prof, ref, out_path("guess", "season.png", out_root))
    return {
        "date": ref.isoformat(), "kind": "guess",
        "title": f"Guess the city: {teams[0].lower()}{teams[1:]}, "
                 f"{season['w']}-{season['l']} this year",
        "dek": "Three clues and a chart with the name taken off",
        "hook": hook, "score": hooks.GUESS_SCORE, "cities": [prof["city"]],
        "answer": {"label": prof["label"],
                   "detail": f"{season['w']}-{season['l']} in {ref.year}, "
                             f"{signed(season['weighted'])} weighted — "
                             f"{ordinal(place)} of {len(standings)} cities"},
        "sections": [{"heading": None, "subhead": None, "stats": stats, "table": None,
                      "images": [image("guess", "season.png", caption, out_root)]}],
    }


BUILDERS = {"hotcold": hotcold_post, "h2h": h2h_post,
            "timemachine": timemachine_post, "guess": guess_post}
