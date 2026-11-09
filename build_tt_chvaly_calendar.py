#!/usr/bin/env python3
"""Build a subscribed calendar for the regular TT.CHVÁLY meetings in Trnava."""

import calendar
import datetime as dt
import html
import json
import re
import sys
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo


SOURCE_URL = "https://www.ttchvaly.sk/"
TZ = ZoneInfo("Europe/Bratislava")
UTC = dt.timezone.utc
MONTHS_AHEAD = 24


def get_text(url):
    request = urllib.request.Request(url, headers={
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "sk-SK,sk;q=0.9",
        "User-Agent": "Mozilla/5.0 (compatible; TT-Chvaly-calendar/1.0)",
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


def validate_source():
    page = get_text(SOURCE_URL)
    if len(page) < 500:
        raise RuntimeError("Oficiálna stránka TT.CHVÁLY nevrátila použiteľný obsah.")


def build_event(day, generated_at):
    start_local = dt.datetime.combine(day, dt.time(19, 0), TZ)
    end_local = start_local + dt.timedelta(hours=2)
    start = start_local.astimezone(UTC)
    end = end_local.astimezone(UTC)
    title = "🙌 TT.CHVÁLY"
    location = "Jezuitský kostol Najsvätejšej Trojice, Štefánikova 45, Trnava"
    description = (
        "Otvorené modlitbové stretnutie: chvály, slovo a modlitby príhovoru.\n"
        "Pravidelný termín: prvý pondelok v mesiaci o 19:00.\n"
        f"Aktuálne informácie: {SOURCE_URL}"
    )
    return [
        "BEGIN:VEVENT",
        f"UID:tt-chvaly-{day:%Y%m%d}@ruzindol-kalendar",
        "DTSTAMP:" + generated_at.strftime("%Y%m%dT%H%M%SZ"),
        "DTSTART:" + start.strftime("%Y%m%dT%H%M%SZ"),
        "DTEND:" + end.strftime("%Y%m%dT%H%M%SZ"),
        "SUMMARY:" + escape(title),
        "LOCATION:" + escape(location),
        "DESCRIPTION:" + escape(description),
        "URL:" + SOURCE_URL,
        "STATUS:CONFIRMED",
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
    validate_source()
    generated_at = dt.datetime.now(UTC)
    today = generated_at.astimezone(TZ).date()
    days = []
    for offset in range(MONTHS_AHEAD):
        year, month = add_months(today.year, today.month, offset)
        day = first_monday(year, month)
        if day >= today:
            days.append(day)

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
    for day in days:
        lines.extend(build_event(day, generated_at))
    lines.append("END:VCALENDAR")

    output = Path("public/tt-chvaly.ics")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(("\r\n".join(folded(line) for line in lines) + "\r\n").encode("utf-8"))

    Path("public/tt-chvaly-status.json").write_text(json.dumps({
        "updated_utc": generated_at.isoformat(),
        "events": len(days),
        "next_event": days[0].isoformat() + "T19:00:00+01:00" if days else None,
        "schedule": "prvý pondelok v mesiaci o 19:00",
        "location": "Jezuitský kostol Najsvätejšej Trojice, Trnava",
        "alerts": ["2 days before", "1 hour 30 minutes before"],
        "source": SOURCE_URL,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Vygenerovaných {len(days)} termínov TT.CHVÁLY; najbližší: {days[0] if days else 'žiadny'}.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("Kalendár TT.CHVÁLY sa neaktualizoval:", exc, file=sys.stderr)
        sys.exit(1)
