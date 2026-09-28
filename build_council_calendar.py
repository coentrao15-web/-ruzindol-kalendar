#!/usr/bin/env python3
"""Build a subscribed calendar for Ružindol municipal council meetings."""

import datetime as dt
import hashlib
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import uuid
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo


TERMS_URL = "https://www.ruzindol.sk/samosprava/terminy-zasadnutia-oz/"
BOARD_URL = (
    "https://www.ruzindol.sk/seniori/obecny-urad/zverejnovanie/"
    "uradna-tabula/"
)
BASE_URL = "https://www.ruzindol.sk"
TZ = ZoneInfo("Europe/Bratislava")
UTC = dt.timezone.utc
ESTIMATED_DURATION = dt.timedelta(hours=3)
ALERTS = ("-P1D", "-PT1H")


def get_text(url):
    request = urllib.request.Request(url, headers={
        "Accept": "text/html,application/xhtml+xml",
        "User-Agent": "Ruzindol-council-calendar/1.1 (public municipal data)",
    })
    last_error = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=35) as response:
                return response.read().decode("utf-8")
        except Exception as exc:  # Keep the previously published feed on failure.
            last_error = exc
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise last_error


class RegionTextParser(HTMLParser):
    """Extract visible text from the first div carrying the requested class."""

    def __init__(self, wanted_class):
        super().__init__()
        self.wanted_class = wanted_class
        self.depth = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        classes = dict(attrs).get("class", "").split()
        if not self.depth and tag == "div" and self.wanted_class in classes:
            self.depth = 1
        elif self.depth:
            self.depth += 1
        if self.depth and tag in {"br", "li", "p", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self.depth:
            if tag in {"li", "p", "h1", "h2", "h3"}:
                self.parts.append("\n")
            self.depth -= 1

    def handle_data(self, data):
        if self.depth:
            self.parts.append(data)

    def text(self):
        return html.unescape("".join(self.parts))


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.current_href = None
        self.current_text = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.current_href = dict(attrs).get("href")
            self.current_text = []

    def handle_data(self, data):
        if self.current_href is not None:
            self.current_text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.current_href is not None:
            self.links.append((self.current_href, html.unescape(" ".join(self.current_text))))
            self.current_href = None


class AllTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif not self.hidden and tag in {"br", "li", "p", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1
        elif not self.hidden and tag in {"li", "p", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)

    def text(self):
        return html.unescape("".join(self.parts))


DATE_TIME = re.compile(
    r"(?<!\d)(\d{1,2})\s*\.\s*(\d{1,2})\s*\.\s*(20\d{2})"
    r".{0,100}?(?:o|v\s*čase)?\s*(\d{1,2})\s*:\s*(\d{2})\s*(?:h|hod)?",
    re.IGNORECASE | re.DOTALL,
)


def normalize(value):
    return re.sub(r"\s+", " ", value).strip()


def extract_date_time(text):
    found = []
    for match in DATE_TIME.finditer(text):
        day, month, year, hour, minute = map(int, match.groups())
        try:
            local = dt.datetime(year, month, day, hour, minute, tzinfo=TZ)
        except ValueError:
            continue
        found.append(local)
    return found


def planned_meetings():
    source = get_text(TERMS_URL)
    parser = RegionTextParser("editor_content")
    parser.feed(source)
    dates = extract_date_time(parser.text())
    return {date: TERMS_URL for date in dates}


def invitation_meetings():
    board = get_text(BOARD_URL)
    parser = LinkParser()
    parser.feed(board)
    output = {}
    for href, label in parser.links:
        label_key = normalize(label).lower()
        if "pozvánka" not in label_key or not any(word in label_key for word in ("oz", "zastupiteľ")):
            continue
        url = urllib.parse.urljoin(BASE_URL, href)
        page = get_text(url)
        text_parser = AllTextParser()
        text_parser.feed(page)
        for date in extract_date_time(text_parser.text()):
            output[date] = url
    return output


def escape(value):
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")


def folded(line):
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


def event(start, source_url, generated_at):
    start_utc = start.astimezone(UTC)
    end_utc = (start + ESTIMATED_DURATION).astimezone(UTC)
    key = start.strftime("%Y%m%d-%H%M")
    title = "🏛️ Obecné zastupiteľstvo Ružindol"
    description = "Oficiálny termín obce Ružindol. Predpokladané trvanie 3 hodiny. Aktuálne informácie: " + source_url
    lines = [
        "BEGIN:VEVENT",
        "UID:oz-" + key + "@ruzindol.sk",
        "DTSTAMP:" + generated_at.strftime("%Y%m%dT%H%M%SZ"),
        "DTSTART:" + start_utc.strftime("%Y%m%dT%H%M%SZ"),
        "DTEND:" + end_utc.strftime("%Y%m%dT%H%M%SZ"),
        "SUMMARY:" + escape(title),
        "LOCATION:" + escape("Zasadačka Obecného úradu Ružindol"),
        "DESCRIPTION:" + escape(description),
        "URL:" + source_url,
        "STATUS:CONFIRMED",
        "TRANSP:OPAQUE",
    ]
    default_alarm_uid = str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"https://ruzindol.sk/calendar/council/{key}/apple-default-none",
    )).upper()
    lines += [
        "BEGIN:VALARM",
        "UID:" + default_alarm_uid,
        "X-WR-ALARMUID:" + default_alarm_uid,
        "TRIGGER;VALUE=DATE-TIME:19760401T005545Z",
        "X-APPLE-DEFAULT-ALARM:TRUE",
        "ACTION:NONE",
        "END:VALARM",
    ]
    for trigger in ALERTS:
        alarm_uid = str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"https://ruzindol.sk/calendar/council/{key}/{trigger}",
        )).upper()
        lines += [
            "BEGIN:VALARM",
            "UID:" + alarm_uid,
            "X-WR-ALARMUID:" + alarm_uid,
            "ACTION:DISPLAY",
            "TRIGGER;RELATED=START:" + trigger,
            "DESCRIPTION:" + escape(title),
            "END:VALARM",
        ]
    lines.append("END:VEVENT")
    return lines


def main():
    now = dt.datetime.now(UTC)
    cutoff = now.astimezone(TZ) - dt.timedelta(days=7)
    planned = planned_meetings()
    invitations = invitation_meetings()
    # An official invitation is newer and more precise than the annual plan.
    meetings = {date: url for date, url in planned.items() if date >= cutoff}
    meetings.update({date: url for date, url in invitations.items() if date >= cutoff})
    if not meetings:
        raise RuntimeError("Obecná stránka nevrátila žiadny aktuálny ani budúci termín.")

    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "PRODID:-//Obec Ruzindol//Municipal council calendar//SK",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        "X-WR-CALNAME:Obecné zastupiteľstvo Ružindol",
        "X-WR-TIMEZONE:Europe/Bratislava",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H",
    ]
    for start, source_url in sorted(meetings.items()):
        lines += event(start, source_url, now)
    lines.append("END:VCALENDAR")

    output = Path("public/ruzindol-zastupitelstvo.ics")
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = ("\r\n".join(folded(line) for line in lines) + "\r\n").encode("utf-8")
    output.write_bytes(payload)
    Path("public/zastupitelstvo-status.json").write_text(json.dumps({
        "updated_utc": now.isoformat(),
        "meetings": len(meetings),
        "planned_found": len(planned),
        "invitations_found": len(invitations),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "sources": [TERMS_URL, BOARD_URL],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Vygenerované zasadnutia:", len(meetings), sorted(d.isoformat() for d in meetings))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("Kalendár zastupiteľstva sa neaktualizoval:", exc, file=sys.stderr)
        sys.exit(1)
