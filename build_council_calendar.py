#!/usr/bin/env python3
"""Build a subscribed calendar for Ružindol municipal council meetings."""

import datetime as dt
import concurrent.futures
import hashlib
import html
import io
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

from pypdf import PdfReader


TERMS_URL = "https://www.ruzindol.sk/samosprava/terminy-zasadnutia-oz/"
BOARD_URL = (
    "https://www.ruzindol.sk/seniori/obecny-urad/zverejnovanie/"
    "uradna-tabula/"
)
BASE_URL = "https://www.ruzindol.sk"
DOCUMENTS_URL = BASE_URL + "/obecny-urad/dokumenty/"
NEWS_URL = BASE_URL + "/obcan/aktuality/"
OUTPUT = Path("public/ruzindol-zastupitelstvo.ics")
STATUS_OUTPUT = Path("public/zastupitelstvo-status.json")
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
    parser = LinkParser()
    parser.feed(get_text(BOARD_URL))
    parser.feed(get_text(NEWS_URL))
    output = {}
    locations = {}
    visited = set()
    for href, label in parser.links:
        label_key = normalize(label).lower()
        if "pozvánka" not in label_key or not any(word in label_key for word in ("oz", "zastupiteľ")):
            continue
        url = urllib.parse.urljoin(BASE_URL, href)
        if url in visited:
            continue
        visited.add(url)
        page = get_text(url)
        text_parser = AllTextParser()
        text_parser.feed(page)
        text = text_parser.text()
        for date in extract_date_time(text):
            output[date] = url
            if re.search(r"v\s+sále\s+Kultúrneho\s+domu\s+Ružindol", text, re.I):
                locations[date] = "Sála Kultúrneho domu Ružindol"
    return output, locations


MINUTES_DATE = re.compile(
    r"konan[éýeá]ho\s+dňa\s+(\d{1,2})\s*\.\s*(\d{1,2})\s*\.\s*(20\d{2})",
    re.IGNORECASE,
)


def minutes_date(text):
    """Use the meeting date in the heading, never dates of referenced meetings."""
    match = MINUTES_DATE.search(normalize(text[:2500]))
    if not match:
        return None
    day, month, year = map(int, match.groups())
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def fetch_minutes_document(item):
    url, label = item
    request = urllib.request.Request(url, headers={"User-Agent": "Ruzindol-council-calendar/1.2"})
    with urllib.request.urlopen(request, timeout=35) as response:
        data = response.read(15 * 1024 * 1024 + 1)
    if len(data) > 15 * 1024 * 1024 or not data.startswith(b"%PDF"):
        raise ValueError("Odkaz nevrátil použiteľnú PDF zápisnicu.")
    document = PdfReader(io.BytesIO(data))
    day = minutes_date(document.pages[0].extract_text() or "")
    if day is None:
        raise ValueError("Dátum zasadnutia sa z nadpisu PDF nedá bezpečne určiť.")
    return day, {"url": url, "title": normalize(label)}


def published_minutes(now):
    """Preserve verified links if a PDF is temporarily unavailable or scanned."""
    output = {}
    if STATUS_OUTPUT.exists():
        previous = json.loads(STATUS_OUTPUT.read_text(encoding="utf-8"))
        for day, item in previous.get("minutes", {}).items():
            if urllib.parse.urlparse(item.get("url", "")).hostname == "www.ruzindol.sk":
                output[dt.date.fromisoformat(day)] = item
    parser = LinkParser()
    parser.feed(get_text(DOCUMENTS_URL))
    candidates = {}
    years = {str(now.year), str(now.year - 1)}
    for href, label in parser.links:
        key = normalize(label).lower()
        # The municipal archive also contains the misspelling "Zápsnica".
        if not re.search(r"z[áa]p(?:is|s)nica", key) or not re.search(r"\boz\b|zastupiteľstva", key.replace("_", " ")):
            continue
        if not years.intersection(re.findall(r"20\d{2}", key)):
            continue
        url = urllib.parse.urljoin(BASE_URL, href)
        if urllib.parse.urlparse(url).hostname == "www.ruzindol.sk":
            candidates[url] = label
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch_minutes_document, item): item for item in candidates.items()}
        for future in concurrent.futures.as_completed(futures):
            try:
                day, item = future.result()
                output[day] = item
            except Exception as exc:
                print(f"Zápisnica sa zatiaľ nedá overiť ({futures[future][1]}): {exc}", file=sys.stderr)
    return output


def previous_meetings():
    """Retain already published past meetings so minutes can be added later."""
    if not OUTPUT.exists():
        return {}, {}
    content = re.sub(r"\r?\n[ \t]", "", OUTPUT.read_text(encoding="utf-8"))
    meetings, locations = {}, {}
    for block in content.split("BEGIN:VEVENT")[1:]:
        start = re.search(r"^DTSTART:(\d{8}T\d{6}Z)$", block, re.M)
        source = re.search(r"^URL:(https://www\.ruzindol\.sk/[^\n]+)$", block, re.M)
        location = re.search(r"^LOCATION:([^\n]+)$", block, re.M)
        if start and source:
            date = dt.datetime.strptime(start.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).astimezone(TZ)
            meetings[date] = source.group(1)
            if location:
                locations[date] = location.group(1)
    return meetings, locations


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


def event(start, source_url, generated_at, minutes=None, location=None):
    start_utc = start.astimezone(UTC)
    end_utc = (start + ESTIMATED_DURATION).astimezone(UTC)
    key = start.strftime("%Y%m%d-%H%M")
    title = "🏛️ Obecné zastupiteľstvo Ružindol"
    description = "Oficiálny termín obce Ružindol. Predpokladané trvanie 3 hodiny. Aktuálne informácie: " + source_url
    if minutes:
        description += "\n\nZápisnica z tohto zasadnutia: " + minutes["url"]
    else:
        description += "\n\nZápisnica: po zverejnení obcou sa odkaz doplní automaticky."
    lines = [
        "BEGIN:VEVENT",
        "UID:oz-" + key + "@ruzindol.sk",
        "DTSTAMP:" + generated_at.strftime("%Y%m%dT%H%M%SZ"),
        "SEQUENCE:" + str(int(generated_at.timestamp())),
        "DTSTART:" + start_utc.strftime("%Y%m%dT%H%M%SZ"),
        "DTEND:" + end_utc.strftime("%Y%m%dT%H%M%SZ"),
        "SUMMARY:" + escape(title),
        "LOCATION:" + escape(location or "Zasadačka Obecného úradu Ružindol"),
        "DESCRIPTION:" + escape(description),
        "URL:" + source_url,
        "STATUS:CONFIRMED",
        "TRANSP:OPAQUE",
    ]
    if minutes:
        lines.append("ATTACH;FMTTYPE=application/pdf:" + minutes["url"])
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
    planned = planned_meetings()
    invitations, invitation_locations = invitation_meetings()
    minutes = published_minutes(now)
    meetings, locations = previous_meetings()
    locations.update(invitation_locations)
    # An official invitation is newer and more precise than the annual plan.
    # Backfill the current year; keep historical events once they were published.
    meetings = {date: url for date, url in meetings.items() if date < now.astimezone(TZ)}
    meetings.update({date: url for date, url in planned.items() if date.year >= now.year})
    for date, url in invitations.items():
        if date.year >= now.year:
            for old in list(meetings):
                if old.date() == date.date():
                    del meetings[old]
            meetings[date] = url
    if not planned and not invitations:
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
        lines += event(start, source_url, now, minutes.get(start.date()), locations.get(start))
    lines.append("END:VCALENDAR")

    output = OUTPUT
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = ("\r\n".join(folded(line) for line in lines) + "\r\n").encode("utf-8")
    output.write_bytes(payload)
    STATUS_OUTPUT.write_text(json.dumps({
        "updated_utc": now.isoformat(),
        "meetings": len(meetings),
        "planned_found": len(planned),
        "invitations_found": len(invitations),
        "meetings_with_minutes": sum(start.date() in minutes for start in meetings),
        "minutes": {day.isoformat(): item for day, item in sorted(minutes.items())},
        "sha256": hashlib.sha256(payload).hexdigest(),
        "sources": [TERMS_URL, BOARD_URL, NEWS_URL, DOCUMENTS_URL],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Vygenerované zasadnutia:", len(meetings), sorted(d.isoformat() for d in meetings))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("Kalendár zastupiteľstva sa neaktualizoval:", exc, file=sys.stderr)
        sys.exit(1)

