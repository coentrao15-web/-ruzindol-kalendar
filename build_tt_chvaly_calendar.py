#!/usr/bin/env python3
"""Build an auto-updating subscribed calendar for TT.CHVÁLY in Trnava."""

import calendar
import datetime as dt
import html
import json
import re
import sys
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo


SOURCE_URL = "https://ttchvaly.sk/"
TZ = ZoneInfo("Europe/Bratislava")
UTC = dt.timezone.utc
MONTHS_AHEAD = 24
DEFAULT_TIME = dt.time(18, 0)


def get_text(url):
    request = urllib.request.Request(url, headers={
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "sk-SK,sk;q=0.9",
        "User-Agent": "Mozilla/5.0 (compatible; TT-Chvaly-calendar/1.1)",
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


def first_monday(year, month):
    day = 1 + (calendar.MONDAY - dt.date(year, month, 1).weekday()) % 7
    return dt.date(year, month, day)


def add_months(year, month, count):
    value = year * 12 + month - 1 + count
    return value // 12, value % 12 + 1


def clean_text(page):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(page))).strip()


def parse_source():
    page = get_text(SOURCE_URL)
    if len(page) < 500:
        raise RuntimeError("Oficiálna stránka TT.CHVÁLY nevrátila použiteľný obsah.")

    text = clean_text(page)
    schedule_match = re.search(
        r"prv[ýy]\s+pondelok.{0,100}?o\s+(\d{1,2}):(\d{2})",
        text,
        re.I,
    )
    regular_time = DEFAULT_TIME
    if schedule_match:
        regular_time = dt.time(int(schedule_match.group(1)), int(schedule_match.group(2)))

    confirmed = []
    for raw in re.findall(r"<time\b[^>]*\bdatetime=[\"']([^\"']+)[\"'][^>]*>", page, re.I):
        try:
            value = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if value.tzinfo:
                value = value.astimezone(TZ)
            else:
                value = value.replace(tzinfo=TZ)
            if "T" not in raw:
                value = dt.datetime.combine(value.date(), regular_time, TZ)
            confirmed.append(value)
        except ValueError:
            try:
                day = dt.date.fromisoformat(raw[:10])
                confirmed.append(dt.datetime.combine(day, regular_time, TZ))
            except ValueError:
                continue

    unique = {}
    for value in confirmed:
        unique[(value.date(), value.time().replace(tzinfo=None))] = value
    return regular_time, sorted(unique.values())


def build_schedule(today, regular_time, confirmed):
    confirmed_by_month = {}
    for value in confirmed:
        if value.date() >= today:
            confirmed_by_month.setdefault((value.year, value.month), []).append(value)

    events = []
    confirmed_count = 0
    for offset in range(MONTHS_AHEAD):
        year, month = add_months(today.year, today.month, offset)
        month_confirmed = confirmed_by_month.get((year, month), [])
        if month_confirmed:
            for value in month_confirmed:
                events.append((value, True))
                confirmed_count += 1
            continue
        day = first_monday(year, month)
        if day >= today:
            events.append((dt.datetime.combine(day, regular_time, TZ), False))
    return sorted(events, key=lambda item: item[0]), confirmed_count


def build_event(start_local, generated_at, confirmed):
    end_local = start_local + dt.timedelta(hours=2)
    start = start_local.astimezone(UTC)
    end = end_local.astimezone(UTC)
    title = "🙌 TT.CHVÁLY"
    location = "Kostol Najsvätejšej Trojice, Štefánikova 45, Trnava"
    status_note = (
        "Potvrdený termín z oficiálnej stránky."
        if confirmed else
        "Predbežný termín podľa pravidelného harmonogramu; po zverejnení potvrdeného termínu sa automaticky upraví."
    )
    description = (
        "Otvorené modlitbové stretnutie: chvály, slovo a modlitby príhovoru.\n"
        f"{status_note}\n"
        f"Aktuálne informácie: {SOURCE_URL}"
    )
    return [
        "BEGIN:VEVENT",
        f"UID:tt-chvaly-{start_local:%Y%m}@ruzindol-kalendar",
        "DTSTAMP:" + generated_at.strftime("%Y%m%dT%H%M%SZ"),
        "SEQUENCE:" + str(int(generated_at.timestamp())),
        "DTSTART:" + start.strftime("%Y%m%dT%H%M%SZ"),
        "DTEND:" + end.strftime("%Y%m%dT%H%M%SZ"),
        "SUMMARY:" + escape(title),
        "LOCATION:" + escape(location),
        "DESCRIPTION:" + escape(description),
        "URL:" + SOURCE_URL,
        "STATUS:" + ("CONFIRMED" if confirmed else "TENTATIVE"),
        "TRANSP:OPAQUE",
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        "TRIGGER;RELATED=START:-P2D",
        "DESCRIPTION:" + escape(title + " – o 2 dni"),
        "END:VALARM",
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        "TRIGGER;RELATED=START:-PT1H30M",
        "DESCRIPTION:" + escape(title + " – o 1,5 hodiny"),
        "END:VALARM",
        "END:VEVENT",
    ]


def main():
    generated_at = dt.datetime.now(UTC)
    today = generated_at.astimezone(TZ).date()
    regular_time, confirmed = parse_source()
    events, confirmed_count = build_schedule(today, regular_time, confirmed)

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Ruzindol calendar//TT Chvaly Trnava//SK",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:TT.CHVÁLY – Trnava",
        "X-WR-TIMEZONE:Europe/Bratislava",
        "REFRESH-INTERVAL;VALUE=DURATION:PT24H",
        "X-PUBLISHED-TTL:PT24H",
    ]
    for start_local, is_confirmed in events:
        lines.extend(build_event(start_local, generated_at, is_confirmed))
    lines.append("END:VCALENDAR")

    output = Path("public/tt-chvaly.ics")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(("\r\n".join(folded(line) for line in lines) + "\r\n").encode("utf-8"))

    next_event = events[0][0].isoformat() if events else None
    Path("public/tt-chvaly-status.json").write_text(json.dumps({
        "updated_utc": generated_at.isoformat(),
        "events": len(events),
        "confirmed_events_from_official_page": confirmed_count,
        "next_event": next_event,
        "regular_schedule": f"spravidla prvý pondelok v mesiaci o {regular_time:%H:%M}",
        "source_mode": "confirmed dates from official page, regular schedule as fallback",
        "location": "Kostol Najsvätejšej Trojice, Štefánikova 45, Trnava",
        "alerts": ["2 days before", "1 hour 30 minutes before"],
        "source": SOURCE_URL,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"Vygenerovaných {len(events)} termínov TT.CHVÁLY; "
        f"potvrdených zo stránky: {confirmed_count}; najbližší: {next_event}."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("Kalendár TT.CHVÁLY sa neaktualizoval:", exc, file=sys.stderr)
        sys.exit(1)
