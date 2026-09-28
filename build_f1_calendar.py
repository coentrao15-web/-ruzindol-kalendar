#!/usr/bin/env python3
"""Build an auto-updating Formula 1 WebCal feed for Apple Calendar."""

import datetime as dt
import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path


UTC = dt.timezone.utc
API = "https://api.jolpi.ca/ergast/f1/{year}.json"
OFFICIAL = "https://www.formula1.com/en/racing/{year}"
YEARS = (2026, 2027)

SESSION_NAMES = {
    "FirstPractice": ("Tréning 1", 60, "🏎️"),
    "SecondPractice": ("Tréning 2", 60, "🏎️"),
    "ThirdPractice": ("Tréning 3", 60, "🏎️"),
    "SprintQualifying": ("Šprintová kvalifikácia", 45, "⏱️"),
    "SprintShootout": ("Šprintová kvalifikácia", 45, "⏱️"),
    "Sprint": ("Šprint", 60, "🏁"),
    "Qualifying": ("Kvalifikácia", 60, "⏱️"),
    "Race": ("Preteky", 120, "🏁"),
}
SESSION_ORDER = list(SESSION_NAMES)

# Formula 1 announced these 2027 race-weekend dates. They are used only while
# exact session times remain unpublished. Timed API data automatically replaces
# the matching placeholder as soon as it becomes available.
OFFICIAL_2027_WEEKENDS = [
    (1, "Veľká cena Bahrajnu", "2027-03-12", "2027-03-14"),
    (2, "Veľká cena Saudskej Arábie", "2027-03-19", "2027-03-21"),
    (3, "Veľká cena Austrálie", "2027-04-02", "2027-04-04"),
    (4, "Veľká cena Japonska", "2027-04-09", "2027-04-11"),
    (5, "Veľká cena Číny", "2027-04-16", "2027-04-18"),
    (6, "Veľká cena Miami", "2027-04-30", "2027-05-02"),
    (7, "Veľká cena Kanady", "2027-05-21", "2027-05-23"),
    (8, "Veľká cena Monaka", "2027-06-04", "2027-06-06"),
    (9, "Veľká cena Portugalska", "2027-06-18", "2027-06-20"),
    (10, "Veľká cena Veľkej Británie", "2027-07-02", "2027-07-04"),
    (11, "Veľká cena Rakúska", "2027-07-09", "2027-07-11"),
    (12, "Veľká cena Belgicka", "2027-07-23", "2027-07-25"),
    (13, "Veľká cena Maďarska", "2027-07-30", "2027-08-01"),
    (14, "Veľká cena Talianska", "2027-09-03", "2027-09-05"),
    (15, "Veľká cena Španielska", "2027-09-10", "2027-09-12"),
    (16, "Veľká cena Azerbajdžanu", "2027-09-24", "2027-09-26"),
    (17, "Veľká cena Turecka", "2027-10-01", "2027-10-03"),
    (18, "Veľká cena Singapuru", "2027-10-08", "2027-10-10"),
    (19, "Veľká cena USA", "2027-10-22", "2027-10-24"),
    (20, "Veľká cena Mexika", "2027-10-29", "2027-10-31"),
    (21, "Veľká cena Brazílie", "2027-11-05", "2027-11-07"),
    (22, "Veľká cena Las Vegas", "2027-11-18", "2027-11-20"),
    (23, "Veľká cena Kataru", "2027-12-03", "2027-12-05"),
    (24, "Veľká cena Abú Zabí", "2027-12-10", "2027-12-12"),
]


def get_json(url):
    request = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "Ruzindol-F1-calendar/1.0 (public calendar feed)",
    })
    with urllib.request.urlopen(request, timeout=40) as response:
        return json.loads(response.read().decode("utf-8"))


def escape(value):
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")


def folded(line):
    """Fold iCalendar lines at no more than 75 UTF-8 octets."""
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


def alarm_lines(event_uid, title):
    lines = []
    # Apple Calendar sometimes adds its subscribed-calendar default reminder.
    # This explicit NONE alarm suppresses it, leaving exactly our two reminders.
    default_uid = stable_uuid(event_uid + ":apple-default-none")
    lines += [
        "BEGIN:VALARM",
        "UID:" + default_uid,
        "X-WR-ALARMUID:" + default_uid,
        "TRIGGER;VALUE=DATE-TIME:19760401T005545Z",
        "X-APPLE-DEFAULT-ALARM:TRUE",
        "ACTION:NONE",
        "END:VALARM",
    ]
    for label, trigger in (("1 hodinu", "-PT1H"), ("5 minút", "-PT5M")):
        alarm_uid = stable_uuid(event_uid + ":" + trigger)
        lines += [
            "BEGIN:VALARM",
            "UID:" + alarm_uid,
            "X-WR-ALARMUID:" + alarm_uid,
            "ACTION:DISPLAY",
            "TRIGGER;RELATED=START:" + trigger,
            "DESCRIPTION:" + escape(f"{title} – začína o {label}"),
            "END:VALARM",
        ]
    return lines


def parse_start(data):
    date, time = data.get("date"), data.get("time")
    if not date or not time:
        return None
    return dt.datetime.fromisoformat(date + "T" + time.replace("Z", "+00:00")).astimezone(UTC)


def timed_event(year, race, session_key, generated_at):
    data = race if session_key == "Race" else race.get(session_key)
    if not isinstance(data, dict):
        return None
    start = parse_start(data)
    if start is None:
        return None
    name, duration, icon = SESSION_NAMES[session_key]
    round_number = int(race["round"])
    grand_prix = race["raceName"]
    title = f"{icon} F1 – {name} – {grand_prix}"
    event_uid = f"f1-{year}-r{round_number:02d}-{session_key.lower()}@ruzindol-kalendar"
    circuit = race.get("Circuit") or {}
    location_data = circuit.get("Location") or {}
    location = ", ".join(filter(None, [
        circuit.get("circuitName"), location_data.get("locality"), location_data.get("country")
    ]))
    official_url = OFFICIAL.format(year=year)
    lines = [
        "BEGIN:VEVENT",
        "UID:" + event_uid,
        "DTSTAMP:" + generated_at.strftime("%Y%m%dT%H%M%SZ"),
        "DTSTART:" + start.strftime("%Y%m%dT%H%M%SZ"),
        "DTEND:" + (start + dt.timedelta(minutes=duration)).strftime("%Y%m%dT%H%M%SZ"),
        "SUMMARY:" + escape(title),
        "DESCRIPTION:" + escape("Aktuálny harmonogram Formuly 1. Čas sa automaticky upraví pri zmene programu. " + official_url),
        "URL:" + official_url,
        "TRANSP:OPAQUE",
        "STATUS:CONFIRMED",
    ]
    if location:
        lines.append("LOCATION:" + escape(location))
    lines += alarm_lines(event_uid, title)
    lines.append("END:VEVENT")
    return start, round_number, lines


def placeholder_event(round_number, name, start_text, end_text, generated_at):
    start = dt.date.fromisoformat(start_text)
    end_exclusive = dt.date.fromisoformat(end_text) + dt.timedelta(days=1)
    event_uid = f"f1-2027-r{round_number:02d}-weekend@ruzindol-kalendar"
    official_url = OFFICIAL.format(year=2027)
    lines = [
        "BEGIN:VEVENT",
        "UID:" + event_uid,
        "DTSTAMP:" + generated_at.strftime("%Y%m%dT%H%M%SZ"),
        "DTSTART;VALUE=DATE:" + start.strftime("%Y%m%d"),
        "DTEND;VALUE=DATE:" + end_exclusive.strftime("%Y%m%d"),
        "SUMMARY:" + escape(f"📅 F1 2027 – {name} (časy ešte nie sú potvrdené)"),
        "DESCRIPTION:" + escape("Oficiálny termín pretekového víkendu. Presné časy tréningov, kvalifikácie a pretekov ešte Formula 1 nezverejnila. Po zverejnení ich tento kalendár automaticky doplní. " + official_url),
        "URL:" + official_url,
        "TRANSP:TRANSPARENT",
        "STATUS:TENTATIVE",
        "END:VEVENT",
    ]
    return dt.datetime.combine(start, dt.time(), UTC), lines


def fetch_races(year):
    try:
        payload = get_json(API.format(year=year))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        if year == 2027:
            print(f"Rok {year} zatiaľ nemá časový rozpis: {exc}", file=sys.stderr)
            return []
        raise
    races = payload.get("MRData", {}).get("RaceTable", {}).get("Races", [])
    if not isinstance(races, list):
        raise RuntimeError(f"Zdroj pre rok {year} nevrátil zoznam pretekov.")
    return races


def main():
    generated_at = dt.datetime.now(UTC)
    events = []
    timed_rounds = {year: set() for year in YEARS}
    race_counts, session_counts = {}, {}

    for year in YEARS:
        races = fetch_races(year)
        race_counts[str(year)] = len(races)
        session_count = 0
        for race in races:
            for session_key in SESSION_ORDER:
                item = timed_event(year, race, session_key, generated_at)
                if item:
                    start, round_number, lines = item
                    events.append((start, lines))
                    timed_rounds[year].add(round_number)
                    session_count += 1
        session_counts[str(year)] = session_count

    if race_counts.get("2026", 0) < 20 or session_counts.get("2026", 0) < 60:
        raise RuntimeError("Časový zdroj pre rok 2026 je neúplný; starý kalendár zostáva zachovaný.")

    placeholders = 0
    for round_number, name, start, end in OFFICIAL_2027_WEEKENDS:
        if round_number not in timed_rounds[2027]:
            events.append(placeholder_event(round_number, name, start, end, generated_at))
            placeholders += 1

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Ruzindol calendar//Formula 1 schedule//SK",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Formula 1 – tréningy, kvalifikácie a preteky",
        "X-WR-TIMEZONE:Europe/Bratislava",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        "X-PUBLISHED-TTL:PT6H",
    ]
    for _, event_lines in sorted(events, key=lambda item: item[0]):
        lines += event_lines
    lines.append("END:VCALENDAR")

    output = Path("public/formula1.ics")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(("\r\n".join(folded(line) for line in lines) + "\r\n").encode("utf-8"))
    Path("public/formula1-status.json").write_text(json.dumps({
        "updated_utc": generated_at.isoformat(),
        "years": list(YEARS),
        "races_from_timed_source": race_counts,
        "timed_sessions": session_counts,
        "2027_weekend_placeholders": placeholders,
        "reminders": ["1 hour before", "5 minutes before"],
        "timed_source": API,
        "official_calendar": OFFICIAL,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Vygenerované: {len(events)} udalostí; {placeholders} dočasných víkendov 2027.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("Kalendár F1 sa neaktualizoval:", exc, file=sys.stderr)
        sys.exit(1)
