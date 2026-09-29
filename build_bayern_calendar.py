#!/usr/bin/env python3
"""Build an auto-updating FC Bayern München men's calendar with SK/CZ TV listings."""

import concurrent.futures
import datetime as dt
import html
import json
import re
import subprocess
import sys
import unicodedata
import urllib.request
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo


TEAM_ID = "132"
ESPN_LEAGUES = {
    "ger.1": "Bundesliga",
    "uefa.champions": "UEFA Champions League",
    "ger.dfb_pokal": "DFB-Pokal",
}
FIXTURES_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard?dates={year}&limit=1000"
OFFICIAL_URL = "https://fcbayern.com/en/matches/profis"
TV_BASE = "https://tv-program.sk"
ONEPLAY_PROGRAM = "https://www.oneplaysport.cz/program?date={date}"
TZ = ZoneInfo("Europe/Bratislava")
UTC = dt.timezone.utc

# Slovak and Czech stations which regularly carry European football.
TV_CHANNELS = {
    "nova-sport-1": "Nova Sport 1",
    "nova-sport-2": "Nova Sport 2",
    "nova-sport-3": "Nova Sport 3",
    "nova-sport-4": "Nova Sport 4",
    "nova-sport-5": "Nova Sport 5",
    "nova-sport-6": "Nova Sport 6",
    "sport1-hd": "Sport 1",
    "sport2-hd": "Sport 2",
    "premier-sport-1": "Premier Sport 1",
    "premier-sport-2": "Premier Sport 2",
    "premier-sport-3": "Premier Sport 3",
    "premier-sport-4": "Premier Sport 4",
    "ct-sport": "ČT sport",
    "joj-sport": "JOJ Šport",
    "joj-sport-2-sk": "JOJ Šport 2",
    "stvr-sport-rtvs-sport": "STVR Šport",
    "canal-sport": "CANAL+ Sport",
}


def get_text(url, accept="text/html,application/xhtml+xml,application/json"):
    request = urllib.request.Request(url, headers={
        "Accept": accept,
        "Accept-Language": "sk-SK,sk;q=0.9,cs;q=0.8,en;q=0.7",
        "User-Agent": "Mozilla/5.0 (compatible; Bayern-calendar/1.0; public calendar feed)",
    })
    with urllib.request.urlopen(request, timeout=35) as response:
        return response.read().decode("utf-8", "replace")


def escape(value):
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")


def folded(line):
    parts, current = [], ""
    for char in line:
        if len((current + char).encode("utf-8")) > 75:
            parts.append(current)
            current = " " + char
        else:
            current += char
    parts.append(current)
    return "\r\n".join(parts)


def stable_uuid(value):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, value)).upper()


def normal(value):
    value = html.unescape(str(value)).lower()
    value = "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def fetch_fixtures():
    events = {}
    now = dt.datetime.now(UTC)
    for league, competition_name in ESPN_LEAGUES.items():
        for year in sorted({now.year, now.year + 1}):
            url = FIXTURES_URL.format(league=league, year=year)
            payload = json.loads(get_text(url, "application/json"))
            batch = payload.get("events")
            if not isinstance(batch, list):
                raise RuntimeError("Zdroj nevrátil zoznam zápasov Bayernu.")
            for event in batch:
                contest = (event.get("competitions") or [{}])[0]
                team_ids = {str(item.get("team", {}).get("id", "")) for item in contest.get("competitors", [])}
                if TEAM_ID not in team_ids:
                    continue
                event["_competition_name"] = competition_name
                events[str(event["id"])] = event
    if len(events) < 10:
        raise RuntimeError("Zdroj nevrátil úplný zoznam zápasov Bayernu.")
    return list(events.values())


def parse_tv_page(slug, channel):
    try:
        page = get_text(f"{TV_BASE}/{slug}/")
    except Exception as exc:
        print(f"TV program {channel} nie je dostupný: {exc}", file=sys.stderr)
        return []
    broadcasts = []
    for raw in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page, re.I | re.S):
        try:
            item = json.loads(html.unescape(raw).strip())
        except (json.JSONDecodeError, TypeError):
            continue
        items = item if isinstance(item, list) else [item]
        for entry in items:
            if not isinstance(entry, dict) or entry.get("@type") != "BroadcastEvent":
                continue
            try:
                start = dt.datetime.fromisoformat(entry["startDate"]).replace(tzinfo=TZ).astimezone(UTC)
            except (KeyError, ValueError):
                continue
            text = " ".join(filter(None, [entry.get("name"), entry.get("description")]))
            broadcasts.append({"channel": channel, "start": start, "text": text})
    return broadcasts


def fetch_tv_program():
    broadcasts = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(parse_tv_page, slug, channel) for slug, channel in TV_CHANNELS.items()]
        for future in concurrent.futures.as_completed(futures):
            broadcasts.extend(future.result())
    return broadcasts


def get_oneplay_page(day):
    """Oneplay rejects urllib in some environments, while its public HTML works with curl."""
    url = ONEPLAY_PROGRAM.format(date=day.isoformat())
    result = subprocess.run([
        "curl", "-fsSL", "--compressed", "--max-time", "35",
        "-A", "Mozilla/5.0 (compatible; Bayern-calendar/1.0; public calendar feed)",
        "-H", "Accept: text/html,application/xhtml+xml", url,
    ], capture_output=True, text=True, timeout=40, check=False)
    if result.returncode:
        print(f"Oneplay program {day} nie je dostupný: {result.stderr.strip()}", file=sys.stderr)
        return ""
    return result.stdout


def parse_oneplay_day(day):
    page = get_oneplay_page(day)
    broadcasts = []
    for block in re.split(r'<div class="channel">', page, flags=re.I)[1:]:
        channel_match = re.search(r'<div class="mobile-channel">.*?alt="([^"]+)"', block, re.I | re.S)
        if not channel_match:
            continue
        channel = html.unescape(channel_match.group(1)).strip()
        if not (channel.startswith("Oneplay Sport ") or channel.startswith("Nova Sport ")):
            continue
        for start_text, name in re.findall(
            r'<a[^>]*data-start="(\d{2}:\d{2})"[^>]*class="program-item"[^>]*>.*?'
            r'<span class="name">(.*?)</span>', block, re.I | re.S
        ):
            try:
                hour, minute = map(int, start_text.split(":"))
                start = dt.datetime.combine(day, dt.time(hour % 24, minute), TZ).astimezone(UTC)
            except ValueError:
                continue
            broadcasts.append({
                "channel": channel,
                "start": start,
                "text": re.sub(r"<[^>]+>", " ", html.unescape(name)),
            })
    return broadcasts


def fetch_oneplay_program(fixtures, now):
    days = set()
    for event in fixtures:
        try:
            start = dt.datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if now <= start <= now + dt.timedelta(days=21):
            days.add(start.astimezone(TZ).date())
    broadcasts = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for batch in pool.map(parse_oneplay_day, sorted(days)):
            broadcasts.extend(batch)
    return broadcasts


def channels_for(home_name, away_name, start, broadcasts):
    home = normal(home_name)
    away = normal(away_name)
    opponent = away if "bayern" in home else home
    opponent_words = [w for w in opponent.split() if len(w) >= 5 and w not in {"munchen", "muenchen", "football"}]
    found = set()
    for broadcast in broadcasts:
        if abs((broadcast["start"] - start).total_seconds()) > 3 * 3600:
            continue
        text = normal(broadcast["text"])
        mentions_bayern = "bayern" in text
        mentions_opponent = any(word in text for word in opponent_words)
        if mentions_bayern or mentions_opponent:
            found.add(broadcast["channel"])
    channels = sorted(found)
    if any(channel.startswith("Nova Sport ") for channel in channels):
        channels += ["Oneplay (CZ)", "Voyo Maximum (SK)"]
    return channels


def match_url(event):
    for link in event.get("links", []):
        if link.get("href"):
            return link["href"]
    return OFFICIAL_URL


def build_event(event, broadcasts, generated_at):
    contest = (event.get("competitions") or [{}])[0]
    start = dt.datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
    competitors = {item.get("homeAway"): item.get("team", {}) for item in contest.get("competitors", [])}
    home = html.unescape(competitors.get("home", {}).get("displayName", "Domáci"))
    away = html.unescape(competitors.get("away", {}).get("displayName", "Hostia"))
    competition = html.unescape(event.get("_competition_name", "Futbal"))
    base_title = f"⚽ {home} – {away}"
    event_uid = f"bayern-men-{event['id']}-v1@ruzindol-kalendar"
    channels = channels_for(home, away, start, broadcasts)
    tv = ", ".join(channels) if channels else "zatiaľ nepotvrdený – doplní sa automaticky"
    tv_title = ", ".join(channels) if channels else "TV zatiaľ nepotvrdená"
    title = f"{base_title} | 📺 {tv_title}"
    url = match_url(event)
    provisional = not bool(contest.get("timeValid", True))
    time_note = " Termín je zatiaľ orientačný a po potvrdení sa automaticky upraví." if provisional else ""
    description = (
        f"Súťaž: {competition}\n"
        f"TV prenos (SK/CZ): {tv}.\n"
        f"TV program: {TV_BASE}/\n"
        f"Aktuálny termín: {url}.{time_note}"
    )
    lines = [
        "BEGIN:VEVENT",
        "UID:" + event_uid,
        "DTSTAMP:" + generated_at.strftime("%Y%m%dT%H%M%SZ"),
        "SEQUENCE:" + str(int(generated_at.timestamp())),
        "DTSTART:" + start.strftime("%Y%m%dT%H%M%SZ"),
        "DTEND:" + (start + dt.timedelta(hours=2)).strftime("%Y%m%dT%H%M%SZ"),
        "SUMMARY:" + escape(title),
        "DESCRIPTION:" + escape(description),
        "URL:" + url,
        "LOCATION:" + escape(contest.get("venue", {}).get("fullName", "")),
        "TRANSP:OPAQUE",
        "STATUS:CONFIRMED",
    ]
    # iPhone applies the subscribed calendar's default five-minute alert.
    # Do not add another VALARM here, otherwise iOS displays two identical alerts.
    lines.append("END:VEVENT")
    return start, lines, bool(channels), provisional


def main():
    generated_at = dt.datetime.now(UTC)
    fixtures = fetch_fixtures()
    broadcasts = fetch_tv_program()
    broadcasts.extend(fetch_oneplay_program(fixtures, generated_at))
    events = []
    tv_confirmed = provisional_count = 0
    for fixture in fixtures:
        contest = (fixture.get("competitions") or [{}])[0]
        state = contest.get("status", {}).get("type", {}).get("state")
        if state == "post":
            continue
        item = build_event(fixture, broadcasts, generated_at)
        start, lines, has_tv, provisional = item
        if start < generated_at - dt.timedelta(days=1):
            continue
        events.append((start, lines))
        tv_confirmed += int(has_tv)
        provisional_count += int(provisional)
    if len(events) < 10:
        raise RuntimeError("Po filtrovaní zostalo príliš málo budúcich zápasov.")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Ruzindol calendar//FC Bayern Muenchen men//SK",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:FC Bayern München – muži",
        "X-WR-TIMEZONE:Europe/Bratislava",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        "X-PUBLISHED-TTL:PT6H",
    ]
    for _, event_lines in sorted(events, key=lambda item: item[0]):
        lines += event_lines
    lines.append("END:VCALENDAR")
    output = Path("public/bayern-muzi.ics")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(("\r\n".join(folded(line) for line in lines) + "\r\n").encode("utf-8"))
    Path("public/bayern-status.json").write_text(json.dumps({
        "updated_utc": generated_at.isoformat(),
        "future_matches": len(events),
        "matches_with_confirmed_tv_channel": tv_confirmed,
        "provisional_kickoff_times": provisional_count,
        "reminder": "5 minutes before",
        "fixtures_source": "ESPN scoreboards: " + ", ".join(ESPN_LEAGUES),
        "tv_source": TV_BASE,
        "oneplay_source": ONEPLAY_PROGRAM.format(date="YYYY-MM-DD"),
        "tv_channels_checked": list(TV_CHANNELS.values()),
        "czech_streaming_checked": [
            "Oneplay Sport 1", "Oneplay Sport 2", "Oneplay Sport 3", "Oneplay Sport 4",
            "Nova Sport 1", "Nova Sport 2", "Nova Sport 3", "Nova Sport 4", "Nova Sport 5", "Nova Sport 6",
        ],
        "slovak_streaming_note": "Nova Sport 1-6 are available through Voyo Maximum in Slovakia",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Vygenerované: {len(events)} zápasov; TV potvrdená pri {tv_confirmed}; orientačný čas pri {provisional_count}.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("Kalendár Bayernu sa neaktualizoval:", exc, file=sys.stderr)
        sys.exit(1)
