#!/usr/bin/env python3
"""
Generate iCalendar (.ics) feed(s) for the FIFA World Cup 2026.

Data source: the official FIFA data API (api.fifa.com), competition 17,
season 285023. The API returns all 104 matches. Knockout matches whose
participants are not yet decided are exposed via placeholder tokens
(e.g. "1A", "2B", "3ABCDF", "W74", "RU101"); these are rendered as
human-readable labels and are automatically replaced by real team names
once FIFA fills them in. Re-running this script therefore keeps the feed
current with no manual edits.

Two variants can be produced from the exact same data:
  * default            -> a quiet feed with no alarms (the original feed).
  * --with-alarms      -> identical events plus a reminder before kickoff.

Robustness:
  * The fetch is retried on transient errors.
  * The generated text is validated before it is written. If validation
    fails (e.g. the API returned a short or malformed payload), the script
    exits non-zero and DOES NOT touch the existing .ics file, so a bad run
    can never overwrite a known-good feed that subscribers already use.

Output times are in UTC so every calendar client converts them to the
subscriber's local time zone automatically.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

# --- FIFA API constants -----------------------------------------------------
API_BASE = "https://api.fifa.com/api/v3"
ID_COMPETITION = "17"        # FIFA World Cup (men's)
ID_SEASON = "285023"         # FIFA World Cup 2026
LANGUAGE = "en"
MATCH_COUNT = 200            # > 104, fetches the whole tournament in one call

# The 2026 tournament is a fixed 104-match event. If the API ever returns
# fewer parsable matches than this, we treat the run as failed rather than
# publishing a truncated calendar over the good one.
EXPECTED_MIN_MATCHES = 104

FETCH_ATTEMPTS = 4           # transient-error retries for the API call
FETCH_BACKOFF_SECONDS = 3    # base backoff between attempts

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Calendar identity (default / quiet feed) -- unchanged from the original.
CAL_PRODID = "-//fwc26-calendar//FIFA World Cup 2026//EN"
CAL_NAME = "FIFA World Cup 2026"
CAL_DESC = (
    "All 104 matches of the FIFA World Cup 2026 (Canada, Mexico & USA). "
    "Knockout fixtures update automatically as teams qualify. "
    "Unofficial feed sourced from the FIFA data API."
)

# Calendar identity (optional alarms feed) -- a distinct name so the two
# subscriptions are easy to tell apart inside a calendar app.
CAL_NAME_ALARMS = "FIFA World Cup 2026 (match alerts)"
CAL_DESC_ALARMS = (
    "All 104 matches of the FIFA World Cup 2026 (Canada, Mexico & USA), "
    "each with a reminder before kickoff. Knockout fixtures update "
    "automatically as teams qualify. Reminders fire in Apple Calendar; "
    "Google and Outlook ignore alarms on subscribed feeds. "
    "Unofficial feed sourced from the FIFA data API."
)

# UID namespace so events are stable across regenerations
UID_DOMAIN = "fwc26-calendar.github"

# Default assumed match duration (kickoff -> end), used for DTEND.
MATCH_DURATION = timedelta(hours=2)


# --- Data fetching ----------------------------------------------------------
def fetch_matches(session: requests.Session) -> list[dict]:
    """Return all match objects from the FIFA calendar endpoint.

    Retries a few times on transient network/5xx errors before giving up.
    """
    url = f"{API_BASE}/calendar/matches"
    params = {
        "idCompetition": ID_COMPETITION,
        "idSeason": ID_SEASON,
        "language": LANGUAGE,
        "count": MATCH_COUNT,
    }

    last_error: Exception | None = None
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        try:
            resp = session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("Results")
            if not results:
                raise RuntimeError("FIFA API returned no matches (empty 'Results').")
            return results
        except Exception as exc:  # noqa: BLE001 - retry any transient failure
            last_error = exc
            if attempt < FETCH_ATTEMPTS:
                wait = FETCH_BACKOFF_SECONDS * attempt
                print(f"WARN: fetch attempt {attempt}/{FETCH_ATTEMPTS} failed "
                      f"({exc}); retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
    raise RuntimeError(f"all {FETCH_ATTEMPTS} fetch attempts failed: {last_error}")


# --- Field helpers ----------------------------------------------------------
def localized(value, default: str = "") -> str:
    """Extract a description from FIFA's localized-list fields.

    Many fields look like: [{"Locale": "en-GB", "Description": "Group A"}].
    """
    if isinstance(value, list) and value:
        # Prefer an English locale, fall back to the first entry.
        for item in value:
            loc = (item.get("Locale") or "").lower()
            if loc.startswith("en"):
                return item.get("Description") or default
        return value[0].get("Description") or default
    if isinstance(value, str):
        return value
    return default


# Group-stage finishing-position prefixes.
_POSITION_WORDS = {
    "1": "Winner Group",
    "2": "Runner-up Group",
}


def expand_placeholder(token: str) -> str:
    """Turn a FIFA placeholder token into a human-readable label.

    Examples:
        "1A"      -> "Winner Group A"
        "2B"      -> "Runner-up Group B"
        "3ABCDF"  -> "3rd-place Group A/B/C/D/F"
        "W74"     -> "Winner Match 74"
        "RU101"   -> "Runner-up Match 101"
    """
    if not token:
        return "TBD"
    token = token.strip()

    # Winner of a specific match, e.g. W74
    if token.upper().startswith("W") and token[1:].isdigit():
        return f"Winner Match {token[1:]}"

    # Runner-up of a specific match, e.g. RU101 (used by the 3rd-place play-off)
    if token.upper().startswith("RU") and token[2:].isdigit():
        return f"Runner-up Match {token[2:]}"

    # Loser of a specific match, e.g. L101 (defensive; not seen in 2026 data)
    if token.upper().startswith("L") and token[1:].isdigit():
        return f"Loser Match {token[1:]}"

    # Group finishing positions, e.g. 1A, 2B, 3ABCDF
    if token and token[0] in ("1", "2", "3"):
        pos, groups = token[0], token[1:]
        if pos == "3":
            # Best third-placed team from one of several groups.
            letters = "/".join(list(groups))
            return f"3rd-place Group {letters}"
        word = _POSITION_WORDS.get(pos, f"Position {pos}")
        return f"{word} {groups}"

    # Unknown format: return as-is so nothing is silently dropped.
    return token


def side_label(side: dict | None, placeholder: str | None) -> str:
    """Best available name for one side of a match.

    Uses the real team name when present, otherwise the expanded placeholder.
    """
    if side:
        name = localized(side.get("TeamName"))
        if name:
            return name
    return expand_placeholder(placeholder or "")


def parse_utc(date_str: str) -> datetime:
    """Parse an ISO8601 'Z' timestamp into an aware UTC datetime."""
    # FIFA uses e.g. "2026-06-11T19:00:00Z"
    dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc)


# --- ICS construction -------------------------------------------------------
def ics_escape(text: str) -> str:
    """Escape a value per RFC 5545 (commas, semicolons, backslashes, newlines)."""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def fold_line(line: str) -> str:
    """Fold a content line to <=75 octets per RFC 5545 (continuation = space)."""
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    out = []
    chunk = b""
    for ch in line:
        ch_bytes = ch.encode("utf-8")
        # First line limit 75, continuation lines limit 74 (leading space counts).
        limit = 75 if not out else 74
        if len(chunk) + len(ch_bytes) > limit:
            out.append(chunk.decode("utf-8"))
            chunk = ch_bytes
        else:
            chunk += ch_bytes
    out.append(chunk.decode("utf-8"))
    return "\r\n ".join(out)


def fmt_dt(dt: datetime) -> str:
    """Format a UTC datetime as an RFC 5545 UTC timestamp."""
    return dt.strftime("%Y%m%dT%H%M%SZ")


def build_event(match: dict, dtstamp: datetime,
                alarm_minutes: int | None = None) -> list[str]:
    """Build the VEVENT content lines for a single match.

    When ``alarm_minutes`` is None the output is byte-for-byte identical to
    the original quiet feed. When set, a single DISPLAY VALARM that triggers
    ``alarm_minutes`` before kickoff is appended inside the VEVENT.
    """
    number = match.get("MatchNumber")
    stage = localized(match.get("StageName"))
    group = localized(match.get("GroupName"))

    home = side_label(match.get("Home"), match.get("PlaceHolderA"))
    away = side_label(match.get("Away"), match.get("PlaceHolderB"))

    stadium = match.get("Stadium") or {}
    venue = localized(stadium.get("Name"))
    city = localized(stadium.get("CityName"))
    country = stadium.get("IdCountry") or ""

    start = parse_utc(match["Date"])
    end = start + MATCH_DURATION

    # Stable UID derived from the immutable FIFA match id.
    id_match = match.get("IdMatch") or f"num{number}"
    uid = f"fwc26-{id_match}@{UID_DOMAIN}"

    # Title: "Mexico vs South Africa" or "Winner Group A vs Runner-up Group B"
    summary = f"{home} vs {away}"

    # Stage tag for the title prefix.
    if stage == "First Stage" and group:
        stage_tag = group                      # e.g. "Group A"
    elif stage == "Play-off for third place":
        stage_tag = "Third-place Play-off"
    else:
        stage_tag = stage                      # e.g. "Round of 32", "Final"

    title = f"[{stage_tag}] {summary}"
    if number:
        title += f" (Match {number})"

    # Location: "Stadium, City, COUNTRY"
    loc_parts = [p for p in (venue, city, country) if p]
    location = ", ".join(loc_parts)

    # Human-readable description.
    desc_lines = [f"FIFA World Cup 2026 — Match {number}" if number
                  else "FIFA World Cup 2026"]
    desc_lines.append(f"Stage: {stage}" + (f" ({group})" if group else ""))
    desc_lines.append(f"{home} vs {away}")
    if location:
        desc_lines.append(f"Venue: {location}")
    desc_lines.append(
        "Kickoff time shown in your local time zone. "
        "Knockout participants update automatically as teams qualify."
    )
    description = "\n".join(desc_lines)

    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{fmt_dt(dtstamp)}",
        f"DTSTART:{fmt_dt(start)}",
        f"DTEND:{fmt_dt(end)}",
        f"SUMMARY:{ics_escape(title)}",
    ]
    if location:
        lines.append(f"LOCATION:{ics_escape(location)}")
    lines.append(f"DESCRIPTION:{ics_escape(description)}")

    # Categories help some clients colour-code by stage.
    lines.append(f"CATEGORIES:{ics_escape(stage)}")
    lines.append("STATUS:CONFIRMED")
    lines.append("TRANSP:OPAQUE")

    # Optional reminder before kickoff (alarms feed only).
    if alarm_minutes is not None:
        alarm_text = f"{summary} kicks off in {alarm_minutes} minutes"
        lines.extend([
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{ics_escape(alarm_text)}",
            f"TRIGGER:-PT{alarm_minutes}M",
            "END:VALARM",
        ])

    lines.append("END:VEVENT")
    return lines


def build_calendar(matches: list[dict],
                   alarm_minutes: int | None = None,
                   cal_name: str = CAL_NAME,
                   cal_desc: str = CAL_DESC) -> str:
    """Assemble the full VCALENDAR text.

    With default arguments this reproduces the original quiet feed exactly.
    """
    dtstamp = datetime.now(timezone.utc)

    head = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{CAL_PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{ics_escape(cal_name)}",
        f"NAME:{ics_escape(cal_name)}",
        f"X-WR-CALDESC:{ics_escape(cal_desc)}",
        "X-WR-TIMEZONE:UTC",
        # Hint to clients (esp. Google) on how often to re-poll.
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
    ]

    body: list[str] = []
    # Sort by match number for a stable, readable file.
    for match in sorted(matches, key=lambda m: (m.get("MatchNumber") or 0)):
        if not match.get("Date"):
            continue
        body.extend(build_event(match, dtstamp, alarm_minutes=alarm_minutes))

    tail = ["END:VCALENDAR"]

    raw_lines = head + body + tail
    folded = [fold_line(line) for line in raw_lines]
    # RFC 5545 requires CRLF line endings, file terminated by CRLF.
    return "\r\n".join(folded) + "\r\n"


# --- Validation -------------------------------------------------------------
def validate_calendar_text(text: str, expected_min: int) -> tuple[bool, str]:
    """Lightweight, dependency-free sanity check of generated ICS text.

    Returns (ok, reason). On failure the caller must NOT overwrite the
    existing feed, so a bad payload can never replace a good one.
    """
    if not text or not text.strip():
        return False, "output is empty"

    lines = text.split("\r\n")
    if lines[0] != "BEGIN:VCALENDAR":
        return False, "missing BEGIN:VCALENDAR header"
    if "END:VCALENDAR" not in lines:
        return False, "missing END:VCALENDAR footer"

    begin_ev = sum(1 for ln in lines if ln == "BEGIN:VEVENT")
    end_ev = sum(1 for ln in lines if ln == "END:VEVENT")
    if begin_ev != end_ev:
        return False, f"unbalanced VEVENT blocks ({begin_ev} begin, {end_ev} end)"
    if begin_ev < expected_min:
        return False, f"only {begin_ev} events, expected at least {expected_min}"

    begin_al = sum(1 for ln in lines if ln == "BEGIN:VALARM")
    end_al = sum(1 for ln in lines if ln == "END:VALARM")
    if begin_al != end_al:
        return False, f"unbalanced VALARM blocks ({begin_al} begin, {end_al} end)"

    # Each event must carry the fields clients rely on.
    in_event = False
    have = set()
    required = {"UID", "DTSTART", "SUMMARY"}
    for ln in lines:
        if ln == "BEGIN:VEVENT":
            in_event = True
            have = set()
        elif ln == "END:VEVENT":
            missing = required - have
            if missing:
                return False, f"an event is missing required field(s): {sorted(missing)}"
            in_event = False
        elif in_event:
            if ln.startswith("UID:"):
                have.add("UID")
            elif ln.startswith("DTSTART"):
                have.add("DTSTART")
            elif ln.startswith("SUMMARY"):
                have.add("SUMMARY")

    return True, ""


def atomic_write(path: Path, text: str) -> None:
    """Write text to a temp file in the same dir, then atomically replace.

    Guarantees the destination is either the old file or the fully-written
    new file -- never a half-written one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".ics-tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp_name, path)  # atomic on the same filesystem
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


# --- Main -------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-o", "--output",
        default="world_cup_2026.ics",
        help="Output .ics path (default: world_cup_2026.ics)",
    )
    parser.add_argument(
        "--with-alarms", action="store_true",
        help="Include a reminder before each match (alarms feed variant).",
    )
    parser.add_argument(
        "--alarm-minutes", type=int, default=15,
        help="Minutes before kickoff for the reminder (default: 15).",
    )
    parser.add_argument(
        "--expected-min", type=int, default=EXPECTED_MIN_MATCHES,
        help="Refuse to write if fewer than this many events are produced.",
    )
    args = parser.parse_args()

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    # 1) Fetch (with retries). Any failure here leaves the existing file alone.
    try:
        matches = fetch_matches(session)
    except Exception as exc:  # noqa: BLE001 - surface any fetch/parse failure
        print(f"ERROR: could not fetch matches: {exc}", file=sys.stderr)
        print("Existing calendar left unchanged.", file=sys.stderr)
        return 1

    # 2) Build the requested variant.
    if args.with_alarms:
        ics_text = build_calendar(
            matches,
            alarm_minutes=args.alarm_minutes,
            cal_name=CAL_NAME_ALARMS,
            cal_desc=CAL_DESC_ALARMS,
        )
    else:
        ics_text = build_calendar(matches)

    # 3) Validate BEFORE writing. A bad payload must never replace a good feed.
    ok, reason = validate_calendar_text(ics_text, args.expected_min)
    if not ok:
        print(f"ERROR: generated calendar failed validation: {reason}",
              file=sys.stderr)
        print("Existing calendar left unchanged.", file=sys.stderr)
        return 1

    # 4) Atomic write only after validation passes.
    out_path = Path(args.output)
    atomic_write(out_path, ics_text)

    # Diagnostics for the Action log.
    decided = sum(
        1 for m in matches
        if (m.get("Home") and m["Home"].get("TeamName"))
        and (m.get("Away") and m["Away"].get("TeamName"))
    )
    digest = hashlib.sha256(ics_text.encode("utf-8")).hexdigest()[:12]
    variant = f"alarms(-PT{args.alarm_minutes}M)" if args.with_alarms else "plain"
    print(f"Wrote {out_path} [{variant}] | {len(matches)} matches "
          f"| {decided} with both teams decided | sha256:{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
