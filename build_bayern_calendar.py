#!/usr/bin/env python3
"""Build an auto-updating FC Bayern München men's calendar with SK/CZ TV listings."""

import concurrent.futures
import datetime as dt
import html
import json
import re
import sys
import unicodedata
import urllib.request
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo


TEAM_ID = 2672
FIXTURES_URL = f"https://www.sofascore.com/api/v1/team/{TEAM_ID}/events/next/{{page}}"
OFFICIAL_URL = "https://fcbayern.com/en/matches/profis"
TV_BASE = "https://tv-program.sk"
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
    events = []
    for page in range(5):
        payload = json.loads(get_text(FIXTURES_URL.format(page=page), "application/json"))
        batch = payload.get("events")
        if not isinstance(batch, list):
            raise RuntimeError("Zdroj nevrátil zoznam zápasov Bayernu.")
        events.extend(batch)
        if not payload.get("hasNextPage"):
            break
    if len(events) < 10:
        raise RuntimeError("Zdroj nevrátil úplný zoznam zápasov Bayernu.")
    return events


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


def channels_for(event, start, broadcasts):
    home = normal(event.get("homeTeam", {}).get("name", ""))
    away = normal(event.get("awayTeam", {}).get("name", ""))
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
    return sorted(found)


def match_url(event):
    return f"https://www.sofascore.com/{event.get('slug', 'football-match')}/{event.get('customId', '')}#id:{event['id']}"


def build_event(event, broadcasts, generated_at):
    start = dt.datetime.fromtimestamp(event["startTimestamp"], UTC)
    home = html.unescape(event["homeTeam"]["name"])
    away = html.unescape(event["awayTeam"]["name"])
    competition = html.unescape(event.get("tournament", {}).get("name", "Futbal"))
    title = f"⚽ {home} – {away} ({competition})"
    event_uid = f"bayern-men-{event['id']}-v1@ruzindol-kalendar"
    channels = channels_for(event, start, broadcasts)
    tv = ", ".join(channels) if channels else "zatiaľ nepotvrdený – doplní sa automaticky"
    url = match_url(event)
    local_start = start.astimezone(TZ)
    # Several distant Bundesliga fixtures initially use a provisional 11:00 local slot.
    provisional = competition == "Bundesliga" and local_start.hour == 11 and (start - generated_at).days > 35
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
        "SEQUENCE:1",
        "DTSTART:" + start.strftime("%Y%m%dT%H%M%SZ"),
        "DTEND:" + (start + dt.timedelta(hours=2)).strftime("%Y%m%dT%H%M%SZ"),
        "SUMMARY:" + escape(title),
        "DESCRIPTION:" + escape(description),
        "URL:" + url,
        "TRANSP:OPAQUE",
        "STATUS:CONFIRMED",
    ]
    alarm_uid = stable_uuid(event_uid + ":-PT5M")
    lines += [
        "BEGIN:VALARM",
        "UID:" + alarm_uid,
        "X-WR-ALARMUID:" + alarm_uid,
        "ACTION:DISPLAY",
        "TRIGGER;RELATED=START:-PT5M",
        "DESCRIPTION:" + escape(title),
        "END:VALARM",
        "END:VEVENT",
    ]
    return start, lines, bool(channels), provisional


def main():
    generated_at = dt.datetime.now(UTC)
    fixtures = fetch_fixtures()
    broadcasts = fetch_tv_program()
    events = []
    tv_confirmed = provisional_count = 0
    for fixture in fixtures:
        if fixture.get("status", {}).get("type") != "notstarted":
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
        "fixtures_source": FIXTURES_URL.format(page=0),
        "tv_source": TV_BASE,
        "tv_channels_checked": list(TV_CHANNELS.values()),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Vygenerované: {len(events)} zápasov; TV potvrdená pri {tv_confirmed}; orientačný čas pri {provisional_count}.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("Kalendár Bayernu sa neaktualizoval:", exc, file=sys.stderr)
        sys.exit(1)
