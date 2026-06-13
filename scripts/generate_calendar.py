#!/usr/bin/env python3
"""
Generate an iCalendar (.ics) feed for the FIFA World Cup 2026.

Data source: the official FIFA data API (api.fifa.com), competition 17,
season 285023. The API returns all 104 matches. Knockout matches whose
participants are not yet decided are exposed via placeholder tokens
(e.g. "1A", "2B", "3ABCDF", "W74", "RU101"); these are rendered as
human-readable labels and are automatically replaced by real team names
once FIFA fills them in. Re-running this script therefore keeps the feed
current with no manual edits.

Output: world_cup_2026.ics (RFC 5545), all times in UTC so every calendar
client converts them to the subscriber's local time zone automatically.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

# --- FIFA API constants -----------------------------------------------------
API_BASE = "https://api.fifa.com/api/v3"
ID_COMPETITION = "17"        # FIFA World Cup (men's)
ID_SEASON = "285023"         # FIFA World Cup 2026
LANGUAGE = "en"
MATCH_COUNT = 200            # > 104, fetches the whole tournament in one call

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Calendar identity
CAL_PRODID = "-//fwc26-calendar//FIFA World Cup 2026//EN"
CAL_NAME = "FIFA World Cup 2026"
CAL_DESC = (
    "All 104 matches of the FIFA World Cup 2026 (Canada, Mexico & USA). "
    "Knockout fixtures update automatically as teams qualify. "
    "Unofficial feed sourced from the FIFA data API."
)
# UID namespace so events are stable across regenerations
UID_DOMAIN = "fwc26-calendar.github"

# Default assumed match duration (kickoff -> end), used for DTEND.
MATCH_DURATION = timedelta(hours=2)


# --- Data fetching ----------------------------------------------------------
def fetch_matches(session: requests.Session) -> list[dict]:
    """Return the list of all match objects from the FIFA calendar endpoint."""
    url = f"{API_BASE}/calendar/matches"
    params = {
        "idCompetition": ID_COMPETITION,
        "idSeason": ID_SEASON,
        "language": LANGUAGE,
        "count": MATCH_COUNT,
    }
    resp = session.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    results = data.get("Results")
    if not results:
        raise RuntimeError("FIFA API returned no matches (empty 'Results').")
    return results


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


def build_event(match: dict, dtstamp: datetime) -> list[str]:
    """Build the VEVENT content lines for a single match."""
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
    lines.append("END:VEVENT")
    return lines


def build_calendar(matches: list[dict]) -> str:
    """Assemble the full VCALENDAR text."""
    dtstamp = datetime.now(timezone.utc)

    head = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{CAL_PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{ics_escape(CAL_NAME)}",
        f"NAME:{ics_escape(CAL_NAME)}",
        f"X-WR-CALDESC:{ics_escape(CAL_DESC)}",
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
        body.extend(build_event(match, dtstamp))

    tail = ["END:VCALENDAR"]

    raw_lines = head + body + tail
    folded = [fold_line(line) for line in raw_lines]
    # RFC 5545 requires CRLF line endings, file terminated by CRLF.
    return "\r\n".join(folded) + "\r\n"


# --- Main -------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-o", "--output",
        default="world_cup_2026.ics",
        help="Output .ics path (default: world_cup_2026.ics)",
    )
    args = parser.parse_args()

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    try:
        matches = fetch_matches(session)
    except Exception as exc:  # noqa: BLE001 - surface any fetch/parse failure
        print(f"ERROR: could not fetch matches: {exc}", file=sys.stderr)
        return 1

    ics_text = build_calendar(matches)

    out_path = Path(args.output)
    out_path.write_text(ics_text, encoding="utf-8", newline="")

    # Diagnostics for the Action log.
    decided = sum(
        1 for m in matches
        if (m.get("Home") and m["Home"].get("TeamName"))
        and (m.get("Away") and m["Away"].get("TeamName"))
    )
    digest = hashlib.sha256(ics_text.encode("utf-8")).hexdigest()[:12]
    print(f"Wrote {out_path} | {len(matches)} matches "
          f"| {decided} with both teams decided | sha256:{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
