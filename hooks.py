#!/usr/bin/env python3
"""
What each post says on Bluesky.

Two rules, both learned from reading the account's own timeline back.

Plain language. The site's index is "weighted", and a stranger scrolling past
has never heard of it, so nothing here says "+34.8 weighted". A hook is made
of things every fan already reads: a record, a streak, a place in a ranking,
a score.

No line is the same every time. Each type has several ways to open and several
questions to close on, and `n` — how many posts of the type have gone out
before this one — walks through them in turn. The opening and the question
step at different rates, so the pairs don't repeat in lockstep either, and
replaying a date gives the same words because `n` is counted from the posts
before it.

Every function returns the text above the link, short enough that
share_bluesky.compose() never has to trim it (tests/test_hooks.py holds each
variant to that).
"""

from fandom_analysis import MONTH_NAMES, ordinal, standing


def turn(options: list, n: int, stride: int = 1) -> str:
    """The n-th option, going round. A stride that shares no factor with the
    number of options still visits every one."""
    return options[(n * stride) % len(options)]


def rec(row: dict) -> str:
    return f"{row['w']}-{row['l']}" + (f"-{row['t']}" if row.get("t") else "")


def lines(*parts: str) -> str:
    return "\n".join(part for part in parts if part)


def nicknames(label: str) -> str:
    """"Boston: Red Sox/Celtics" -> "Red Sox, Celtics"."""
    return label.split(": ", 1)[-1].replace("/", ", ")


# --- city of the day ----------------------------------------------------------

DAILY_OPEN = [
    "City of the day: {city} ({teams})",
    "Today's city: {city}. {teams}.",
    "{city}, you're up. ({teams})",
    "Checking in on {city}: {teams}",
    "Spotlight on {city} ({teams})",
]
MONTH_NEAR_EDGE = 3     # top three or bottom three of its own record

DAILY_ASK = [
    "{city} fans: does that sound right?",
    "Better or worse than you expected?",
    "What's the mood in {city}?",
    "Where does this year end up?",
    "Did anyone see this coming?",
]


def daily_hook(facts: dict, n: int = 0) -> str:
    city, season, month, recent = (facts["city"], facts["season"], facts["month"],
                                   facts["recent"])
    detail = f"{rec(season)} in {facts['year']}."
    if month:
        so_far = " so far" if month["in_progress"] else ""
        detail += f" {month['name']}{so_far}: {rec(month)}"
        # where the month ranks is only worth the words near either end
        if min(month["place"] - 1, month["worse"]) < MONTH_NEAR_EDGE:
            detail += (f", {standing(month['place'] - 1, month['worse'], month['field'])}"
                       f" on record")
        detail += "."
    elif recent["games"]:
        detail += f" Last 30 days: {rec(recent)}."
    return lines(turn(DAILY_OPEN, n).format(city=city, teams=nicknames(facts["label"])),
                 detail, turn(DAILY_ASK, n, 2).format(city=city))


def daily_score(facts: dict) -> float:
    """How much of a story an ordinary city's morning is: a month at the very
    top or bottom of its own record, a long run, or just a check-in."""
    month, run = facts["month"], facts["recent"]["longest"]
    if month and month["field"] >= 5 and (month["place"] == 1 or month["worse"] == 0):
        return 0.75
    if run and run["length"] >= 6:
        return 0.6
    return 0.5


# --- record watch -------------------------------------------------------------

RECORD_ASK = {
    "hot": ["How long does it last?", "Real, or just a hot stretch?",
            "Who saw this coming?", "Is anyone stopping them?", "Too good to last?"],
    "cold": ["How much worse can it get?", "Where's the bottom?",
             "When does it turn around?", "Rock bottom, or not yet?",
             "What has to change?"],
}


def driver(finding: dict) -> dict | None:
    """The team doing most of it this week."""
    playing = [t for t in finding["per_team"] if t["week"]["games"]]
    return max(playing, key=lambda t: abs(t["week"]["weighted"])) if playing else None


def record_hook(finding: dict, n: int = 0) -> str:
    kind, month = finding["kind"], finding["month_totals"]
    lead = driver(finding)
    led = (f" The {lead['nickname']} are {rec(lead['week'])} this week."
           if lead and len(finding["per_team"]) > 1 else "")
    if kind == "streak":
        run = finding["streak"]
        detail = ("No run this long since records began in 2010."
                  if run["record"] else f"Their longest before this one: {run['prior_best']}.")
        runs = [t for t in finding["per_team"]
                if t["streak"] and t["streak"]["type"] == run["type"]
                and t["streak"]["length"] >= 2]
        if len(finding["per_team"]) > 1 and runs:
            detail += " " + ", ".join(
                f"{t['nickname']} {t['streak']['type']}{t['streak']['length']}"
                for t in runs) + "."
    elif kind == "year":
        totals = finding["year"]["totals"]
        detail = f"{rec(totals)} on the year."
        if month["games"]:
            detail += f" This month: {rec(month)}."
    elif kind == "month":
        detail = f"{rec(month)} this month.{led}"
    elif kind == "turnaround":
        detail = f"{rec(month)} on the month now.{led}"
    else:
        climb = finding["climb"]
        detail = (f"{ordinal(climb['from'])} to {ordinal(climb['to'])} of "
                  f"{climb['field']} in seven days.{led}")
    return lines(finding["headline"], detail,
                 turn(RECORD_ASK[finding["direction"]], n))


def record_score(finding: dict) -> float:
    """News value, which is not the detector's own score: that one measures
    how far from the group's norm a number sits, and a 3-0 week after an 0-4
    start is a long way from the norm and not much of a story."""
    kind = finding["kind"]
    if kind == "streak":
        run = finding["streak"]
        return 1.0 if run["record"] else min(0.9, 0.4 + 0.04 * run["length"])
    if kind == "month":
        if finding.get("rank") != 1:
            return 0.5
        return 0.95 if finding.get("basis") == "all" else 0.8
    if kind == "year":
        # true every day for months once it is true at all, so it sits under
        # the things that happened this week
        return 0.7 if finding["year"]["rank"] == 1 else 0.55
    if kind == "turnaround":
        return 0.3 + min(0.3, finding["month_totals"]["games"] / 40)
    return 0.3


# --- hot & cold ---------------------------------------------------------------

HOTCOLD_ASK = [
    "Which run ends first?",
    "Who blinks first?",
    "Which streak is still going a week from now?",
    "Which fans are enjoying this more? (Easy one.)",
    "Either of these reaching {target}?",
]


def run_line(icon: str, row: dict, split: str) -> str:
    word = "wins" if row["type"] == "W" else "losses"
    return f"{icon} {row['city']}: {row['length']} straight {word} ({split})"


def hotcold_hook(hot: dict, cold: dict, hot_split: str, cold_split: str,
                 n: int = 0) -> str:
    target = max(hot["length"], cold["length"]) + 3
    return lines(run_line("🔥", hot, hot_split), run_line("🧊", cold, cold_split),
                 turn(HOTCOLD_ASK, n).format(target=target))


def hotcold_score(hot: dict, cold: dict, last_cities: list = ()) -> float:
    """The longer the run the bigger the story — and a smaller one each time
    it is the same two cities as the last hot & cold post."""
    longest = max(hot["length"], cold["length"])
    score = max(0.2, min(1.0, 0.2 + 0.07 * (longest - 4)))
    repeats = len({hot["city"], cold["city"]} & set(last_cities))
    return round(score * (1, 0.8, 0.6)[repeats], 3)


# --- head to head -------------------------------------------------------------

H2H_OPEN = [
    "{a} or {b}: who's had the better {year}?",
    "{a} vs {b}. Whose {year} would you rather have?",
    "Pick one: {a}'s {year} or {b}'s?",
    "{a} fans, {b} fans: who's winning {year}?",
    "Settle it: {a} or {b} in {year}?",
]


def h2h_hook(a: dict, b: dict, field: int, year: int, game: str | None,
             met: dict, day: str, n: int = 0) -> str:
    city_a, city_b = a["group"]["city"], b["group"]["city"]
    standing_line = (f"{city_a}: {rec(a)} ({ordinal(a['place'])} of {field} cities) · "
                     f"{city_b}: {rec(b)} ({ordinal(b['place'])})")
    if game:
        last = f"{day}: {game}."
    elif met["games"]:
        last = f"Head to head this year: {city_a} {met['a']}, {city_b} {met['b']}."
    else:
        last = ""
    return lines(turn(H2H_OPEN, n).format(a=city_a, b=city_b, year=year),
                 standing_line, last)


def h2h_score(a: dict, b: dict, field: int, game: bool) -> float:
    """Two cities near the top are a rivalry; two near the bottom are a
    shrug. A game between them the night before is the reason to post."""
    top = 1 - ((a["place"] + b["place"]) / 2 - 1) / max(field - 1, 1)
    return round(0.3 + (0.15 if game else 0) + 0.45 * top, 3)


# --- time machine -------------------------------------------------------------

TIME_OPEN = [
    "{ago} this month: {city} took the {month} {year} banner, {record}{star}.",
    "Throwback: {month} {year} belonged to {city}. {record}{star}.",
    "Rewind to {month} {year}. {city} went {record}{star} and took the banner.",
    "{month} {year}: nobody was better than {city}. {record}{star}.",
    "Remember {month} {year}? {city} went {record}{star}.",
]
TIME_ASK = [
    "Remember that month?",
    "Feels like a long time ago, doesn't it?",
    "Who was on those teams?",
    "Better then, or better now?",
    "Where were you for that one?",
]


def timemachine_hook(city: str, month: str, year: int, ago: str, champ: dict,
                     star: dict | None, now: tuple | None, n: int = 0) -> str:
    # a team that played every game of it would only say the record twice
    carried = (f" ({star['nickname']} went {rec(star)})"
               if star and rec(star) != rec(champ) else "")
    opener = turn(TIME_OPEN, n).format(ago=ago, city=city, month=month, year=year,
                                       record=rec(champ), star=carried)
    today = (f"This {month} they're {ordinal(now[0])} of {now[1]}." if now else "")
    return lines(opener, today, turn(TIME_ASK, n, 2))


def timemachine_score(now: tuple | None) -> float:
    """Evergreen, unless the city has since fallen a long way or is still on
    top — then the old month says something about this one."""
    if now and (now[0] <= 3 or now[0] > now[1] * 2 / 3):
        return 0.6
    return 0.45


# --- guess the city -----------------------------------------------------------

GUESS_OPEN = ["Guess the city 🕵️", "Name that city 🕵️", "Whose season is this? 🕵️",
              "Mystery city time 🕵️", "Can you name the city? 🕵️"]
GUESS_ASK = [
    "Reply with your guess. Answer in a couple of hours.",
    "Guesses below. We'll reveal it in a reply.",
    "Think you know? Reply before we reveal it.",
    "Drop your guess. The answer goes up later today.",
    "First right answer gets the bragging rights.",
]
GUESS_SCORE = 0.5


def guess_hook(clues: list, n: int = 0) -> str:
    return lines(turn(GUESS_OPEN, n), *[f"• {clue}" for clue in clues],
                 turn(GUESS_ASK, n, 2))


# --- the banner race ----------------------------------------------------------

BANNER_OPEN = [
    "{month} banner race, day {day}: {city} leads.",
    "{city} is on top of the {month} banner race with {left} to go.",
    "Day {day} of {month}, and it's {city} out in front.",
    "{month} banner watch: {city} sets the pace.",
    "{left} left in {month}. {city} has the lead.",
]
BANNER_ASK = [
    "Can anyone catch them?",
    "Is this one already over?",
    "Who finishes on top?",
    "Does {chaser} have a run in them?",
    "Locked up, or still a race?",
]
BANNER_WON = [
    "{city} wins the {month} {year} banner 🏆",
    "The {month} {year} banner goes to {city} 🏆",
    "{month} {year} belongs to {city} 🏆",
]
BANNER_NEXT = ["Who takes {next}?", "Who's your pick for {next}?",
               "{next} starts now. Who's got it?"]


def banner_hook(data: dict, n: int = 0) -> str:
    leader, chaser = data["leader"], data.get("runner_up")
    city, month = leader["city"], data["month_name"]
    board = f"{city} {rec(leader)} this month"
    if chaser:
        board += f" · {chaser['city']} {rec(chaser)}"
    if data["final"]:
        return lines(
            turn(BANNER_WON, n).format(city=city, month=month, year=data["year"]),
            f"{board}. Banner #{data['banner_number']} for {city} since {data['since']}.",
            turn(BANNER_NEXT, n).format(next=MONTH_NAMES[data["month"] % 12 + 1]))
    to_play = data["days"] - data["day"]
    left = f"{to_play} day{'' if to_play == 1 else 's'}"
    asks = BANNER_ASK if chaser else [a for a in BANNER_ASK if "{chaser}" not in a]
    return lines(
        turn(BANNER_OPEN, n).format(month=month, day=data["day"], city=city, left=left),
        board + ".",
        turn(asks, n, 2).format(chaser=chaser["city"] if chaser else ""))
