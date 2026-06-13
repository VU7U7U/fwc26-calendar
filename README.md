# FIFA World Cup 2026 — Calendar Subscription 🏆

A **self-updating calendar feed** for the [FIFA World Cup 2026](https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026) (Canada · Mexico · USA). Subscribe once and all **104 matches** appear in your calendar app, each shown automatically in **your own local time zone**.

Knockout fixtures (Round of 32 through the Final) are included from day one. Their participants start as placeholders such as *"Winner Group A"* or *"Winner Match 89"* and are **replaced automatically with the real teams** as they qualify — you don't need to do anything.

---

## 📅 Subscribe (the important part)

Use this feed URL:

```
https://raw.githubusercontent.com/VU7U7U/fwc26-calendar/main/world_cup_2026.ics
```

> ℹ️ **Subscribe, don't import.** *Importing* the `.ics` adds a one-time snapshot that never updates. *Subscribing* to the URL lets your app re-fetch it, so knockout teams and any schedule changes flow in automatically.

### Google Calendar (desktop)
1. Open [Google Calendar](https://calendar.google.com).
2. In the left sidebar, next to **Other calendars**, click **+** → **From URL**.
3. Paste the feed URL above and click **Add calendar**.
4. The matches appear within a few minutes. Google re-checks the feed periodically (typically every several hours to a day).

### Apple Calendar (iPhone / iPad)
1. **Settings** → **Calendar** → **Accounts** → **Add Account** → **Other**.
2. Tap **Add Subscribed Calendar**.
3. Paste the feed URL and tap **Next** → **Save**.
4. Tip: under the account's settings you can set **Fetch New Data** to refresh as often as hourly.

### Apple Calendar (macOS)
1. In the **Calendar** app: **File** → **New Calendar Subscription…**
2. Paste the feed URL and click **Subscribe**.
3. Set **Auto-refresh** to **Every hour** (or **Every day**) and click **OK**.

### Outlook (outlook.com / Microsoft 365 web)
1. Open [Outlook Calendar](https://outlook.live.com/calendar).
2. **Add calendar** → **Subscribe from web**.
3. Paste the feed URL, give it a name, and click **Import**/**Save**.

### Other apps
Any calendar that supports **iCal / ICS / "subscribe by URL"** (Fantastical, Thunderbird, Proton Calendar, etc.) works — just paste the same URL.

---

## 🔔 Optional: 15-minute match reminders

If you'd like a notification **15 minutes before each match**, subscribe to the **alarms** feed instead of (or in addition to) the one above:

```
https://raw.githubusercontent.com/VU7U7U/fwc26-calendar/main/world_cup_2026_alarms.ics
```

It contains the exact same 104 matches and auto-updates the same way — the only difference is a built-in reminder before kickoff. The two feeds are independent, so the regular feed stays completely quiet; pick whichever you prefer.

**Reminders depend on your calendar app**, and not all of them honour alarms on a *subscribed* feed:

| App | 15-minute reminder on the alarms feed |
| --- | --- |
| Apple Calendar (iOS/macOS) | ✅ Works. When subscribing, leave **"Remove Alarms" off** in the subscription dialog. |
| Google Calendar | ❌ Google ignores alarms embedded in subscribed (URL) calendars. |
| Outlook | ⚠️ Inconsistent; reminders on subscribed internet calendars often don't fire. |

This is a limitation of the calendar providers, not the feed. If you're on Google or Outlook and want a reminder for a specific match, the simplest route is to open that single event and add your own reminder, or set a phone alarm.

> ℹ️ Subscribing to **both** feeds will show every match twice (once from each calendar). Most people pick just one.

---

## ⏰ A note on update timing

This repository **regenerates the feed every hour** via GitHub Actions and commits it whenever something changes. However, **how quickly changes reach your calendar depends on your app**, not on this repo:

| App | Typical refresh of subscribed feeds |
| --- | --- |
| Apple Calendar | Configurable — as often as **hourly** |
| Outlook | A few hours |
| Google Calendar | Several hours up to ~24h (not user-configurable) |

So a newly-decided knockout matchup may take a little while to appear, especially on Google. This is a limitation of the calendar providers' polling intervals.

---

## 🌍 What's in the feed

- All **104 matches**, 11 June – 19 July 2026.
- Each event includes the **stage** (Group A–L, Round of 32, Round of 16, Quarter-final, Semi-final, Third-place Play-off, Final), the **match number**, and the **stadium, host city and country**.
- Event titles look like:
  - `[Group A] Mexico vs South Africa (Match 1)`
  - `[Round of 32] Winner Group A vs Runner-up Group B (Match 73)`
  - `[Final] Winner Match 101 vs Winner Match 102 (Match 104)`
- All kickoff times are stored in **UTC**; your app converts them to local time.
- Everything is in **English** for universal use.

---

## ⚙️ How it works

- [`scripts/generate_calendar.py`](scripts/generate_calendar.py) fetches the official **FIFA data API** (`api.fifa.com`, competition `17`, season `285023`) and writes an [RFC 5545](https://datatracker.ietf.org/doc/html/rfc5545) `.ics` file.
- Knockout placeholders from the API (`1A`, `2B`, `3ABCDF`, `W74`, `RU101`, …) are expanded into readable labels and overwritten with real team names once FIFA publishes them.
- [`.github/workflows/update-calendar.yml`](.github/workflows/update-calendar.yml) runs the script hourly and commits the feeds when they change. It generates both `world_cup_2026.ics` and `world_cup_2026_alarms.ics`. Each event keeps a **stable UID** (tied to the FIFA match ID), so updates modify existing events in your calendar instead of creating duplicates.
- **Safe updates:** before writing, the generator validates its output (parses, checks all 104 matches are present). If the API ever returns a short, empty, or malformed response, the run aborts **without touching the existing feed**, so a bad fetch can't corrupt a calendar people already rely on. A failed run opens a GitHub issue so it's noticed.

### Run it yourself
```bash
pip install -r requirements.txt
python scripts/generate_calendar.py -o world_cup_2026.ics                 # quiet feed
python scripts/generate_calendar.py --with-alarms -o world_cup_2026_alarms.ics  # with 15-min reminders
```

---

## Disclaimer

This is an **unofficial**, community-maintained feed. Match data comes from FIFA's public API and may change. Not affiliated with or endorsed by FIFA. Trademarks belong to their respective owners.
