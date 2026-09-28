#!/usr/bin/env python3
"""Build a subscribed iPhone calendar from Sportnet's public fixture API."""

import datetime as dt
import html
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo


CLUB_URL = "https://sportnet.sme.sk/futbalnet/k/osk-ruzindol/"
CLUB_APP_SPACE = "tj-druzstevnik-ruzindol.futbalnet.sk"
API = "https://sutaze.api.sportnet.online/api/v2/futbalnet/matches"
MATCH_URL = "https://sportnet.sme.sk/futbalnet/z/{}/zapas/{}/"
TZ = ZoneInfo("Europe/Bratislava")
UTC = dt.timezone.utc
# Includes the interval, not just the time the ball is in play. End is an estimate.
DURATIONS = {"Dospelí": 105, "U17": 95, "U15": 85, "U09": 65}
SLUGS = {"dospeli-m-a": "Dospelí", "u17-m-a": "U17",
         "u15-m-a": "U15", "u09-m-a": "U09"}


def get_text(url):
    request = urllib.request.Request(url, headers={
        "Accept": "application/json, text/html;q=0.9",
        "User-Agent": "OSK-Ruzindol-calendar/1.0 (public fixture feed)",
    })
    with urllib.request.urlopen(request, timeout=35) as response:
        return response.read().decode("utf-8")


def discover_teams():
    club = get_text(CLUB_URL)
    slugs = set(re.findall(r'/futbalnet/k/osk-ruzindol/tim/([^/"?]+)/program/', club))
    if not set(SLUGS).issubset(slugs):
        raise RuntimeError("Na stránke klubu chýba očakávaný tím; zachovávam starý kalendár.")
    teams = {}
    for slug in sorted(slugs):
        page = get_text(CLUB_URL + "tim/" + slug + "/program/")
        # Next.js contains the public getTeamProgram query and its current season team ID.
        ids = re.findall(r'teamId\\\":\\\"([0-9a-f]{24})', page)
        if not ids:
            raise RuntimeError("Chýba ID tímu " + slug)
        teams[slug] = (ids[-1], SLUGS.get(slug, slug.upper()))
    return teams


def fetch_matches(team_id, from_date):
    offset = 0
    output = []
    while True:
        params = {
            "playerAppSpace": CLUB_APP_SPACE, "teamId": team_id,
            "withDate": "true", "closed": "false", "sorter": "dateFromAsc",
            "dateFrom": from_date, "offset": offset, "limit": 100,
        }
        url = API + "?" + urllib.parse.urlencode(params)
        payload = json.loads(get_text(url))
        if not isinstance(payload.get("matches"), list):
            raise RuntimeError("Futbalnet nevrátil zoznam zápasov.")
        output.extend(payload["matches"])
        next_offset = payload.get("nextOffset")
        if next_offset is None:
            return output
        if not isinstance(next_offset, int) or next_offset <= offset:
            raise RuntimeError("Chyba stránkovania zápasov.")
        offset = next_offset


def escape(value):
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")


def folded(line):
    """Fold long iCalendar lines at no more than 75 UTF-8 octets."""
    parts = []
    current = ""
    for char in line:
        if len((current + char).encode("utf-8")) > 75:
            parts.append(current)
            current = " " + char
        else:
            current += char
    parts.append(current)
    return "\r\n".join(parts)


def event(match, category, generated_at):
    home = match.get("homeTeam") or {}
    away = match.get("awayTeam") or {}
    if not (home.get("name") and away.get("name") and match.get("startDate")):
        return None
    if any("nez. družstvo" in team.get("name", "").lower() for team in (home, away)):
        return None
    start_utc = dt.datetime.fromisoformat(match["startDate"].replace("Z", "+00:00"))
    start = start_utc.astimezone(TZ)
    end = start + dt.timedelta(minutes=DURATIONS.get(category, 105))
    match_id = match["_id"]
    app_space = (match.get("appSpace") or "obfz-trnava").lower()
    app_space = re.sub(r"[^a-z0-9-]", "-", app_space)
    url = MATCH_URL.format(app_space, match_id)
    title = f"⚽ {html.unescape(home['name'])} – {html.unescape(away['name'])} ({category})"
    description = f"Predpokladaný koniec zápasu. Aktuálny termín: {url}"
    lines = [
        "BEGIN:VEVENT",
        "UID:" + match_id + "@ruzindol.futbalnet",
        "DTSTAMP:" + generated_at.strftime("%Y%m%dT%H%M%SZ"),
        # UTC avoids needing a VTIMEZONE block; iPhone displays the local time.
        "DTSTART:" + start_utc.strftime("%Y%m%dT%H%M%SZ"),
        "DTEND:" + end.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ"),
        "SUMMARY:" + escape(title),
        "DESCRIPTION:" + escape(description),
        "URL:" + url,
        "TRANSP:OPAQUE",
    ]
    # Relative alerts follow the match if its start time changes.
    for trigger in ("-P1D", "-PT2H", "-PT15M"):
        lines += [
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            "TRIGGER;RELATED=START:" + trigger,
            "DESCRIPTION:" + escape(title),
            "END:VALARM",
        ]
    lines.append("END:VEVENT")
    return start_utc, lines


def main():
    now = dt.datetime.now(UTC)
    # Keep recently rescheduled, still-open fixtures visible while Sportnet updates them.
    from_date = (now - dt.timedelta(days=7)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    teams = discover_teams()
    unique = {}
    counts = {}
    for slug, (team_id, category) in teams.items():
        matches = fetch_matches(team_id, from_date)
        counts[slug] = len(matches)
        for match in matches:
            item = event(match, category, now)
            if item:
                unique[match["_id"]] = item
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//OSK Ruzindol//Sportnet calendar//SK",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:OŠK Ružindol – zápasy",
        "X-WR-TIMEZONE:Europe/Bratislava", "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        "X-PUBLISHED-TTL:PT6H",
    ]
    for _, entry in sorted(unique.values(), key=lambda item: item[0]):
        lines += entry
    lines.append("END:VCALENDAR")
    output = Path("public/ruzindol.ics")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(("\r\n".join(folded(line) for line in lines) + "\r\n").encode("utf-8"))
    Path("public/status.json").write_text(json.dumps({
        "updated_utc": now.isoformat(), "teams": counts,
        "matches": len(unique), "source": CLUB_URL,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Vygenerované:", len(unique), "zápasov,", len(teams), "tímov:", counts)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("Kalendár sa neaktualizoval:", exc, file=sys.stderr)
        sys.exit(1)
