# Weather Data Quietly Stopped Updating — The "Switched-Off Fetcher" Problem

**Date discovered:** 2026-05-31
**When it actually broke:** ~2026-04-21 (about six weeks earlier)
**Impact:** No new weather data for ~6 weeks. Everything else looked normal.
**Outcome:** Fixed — weather is flowing again, and the server-rebuild process now
prevents this from happening again. ✅

---

## TL;DR

The part of the system that *fetches* weather had been switched **off**, and
nothing switched it back **on**. So fresh weather quietly stopped arriving on
**April 21** and didn't come back. Nobody noticed for six weeks because the
weather dashboard still showed *a* chart — just an old one — and nothing crashed
or threw an error.

The deeper reason: on this system, programs start out switched **off** by default.
Whenever the server is rebuilt (which happens automatically from time to time), it
forgets which programs were on, and they all revert to off. The rebuild routine
knew to switch the *savers* back on, but not the *fetchers*. So after a rebuild,
the fetcher stayed off forever.

The fix: switch the fetchers back on, and teach the rebuild routine to always
switch fetchers on too — so a future rebuild can't silently break this again.

---

## What you would have noticed

- The weather section of the dashboard was "stuck" — it kept showing data that
  ended on April 21, no matter how much later you looked.
- There were **no error messages, no alerts, no red lights.** That's what made it
  sneaky — a thing that's switched off doesn't complain. It just sits quietly.
- The stocks/financial side looked fine, so it seemed like a weather-only problem.
  (It wasn't entirely — more on that below.)

---

## How the weather pipeline works (in plain terms)

Think of it as two small workers and an inbox:

1. **The Fetcher** — every hour it goes out to a free weather service on the
   internet, grabs the latest forecast for 10 US cities, and drops it into an
   **inbox** (a holding area called Kafka).
2. **The Saver** — it watches the inbox, and whenever something new lands there,
   it picks it up, tidies it, and files it away in our database (Snowflake), which
   is what the dashboard reads from.

The important detail: **the Saver only does anything when something new appears in
the inbox.** It doesn't go fetch weather itself — it just waits for the inbox.

So if the Fetcher is switched off, the inbox stays empty, the Saver has nothing to
do, and the database never gets anything new. From the outside it looks like
"nothing is broken" — because technically nothing *failed*. It just stopped.

That's exactly what happened: **the Saver was on, but the Fetcher was off.**

---

## Root cause

Three facts combined to cause this:

1. **Programs start switched "off" by default here.** When a weather/stocks
   program is first registered, the system marks it as paused (off) until someone
   deliberately turns it on. This is a normal, sensible safety default — you don't
   want a brand-new program firing before you've checked it.

2. **The server gets rebuilt from time to time, and a rebuild erases the
   on/off memory.** This server runs on cheap, interruptible capacity, so every so
   often it's torn down and rebuilt automatically. The list of which programs are
   "on" lives in a small internal database that gets wiped and recreated during a
   rebuild — so after a rebuild, **every program reverts to its default: off.**

3. **The rebuild routine only switched the Savers back on — not the Fetchers.**
   The automated rebuild script had a step that turned the two *Savers* back on,
   but it never listed the two *Fetchers*. So after a rebuild, the Savers came
   back on and the Fetchers stayed off. The last time the weather Fetcher ran was
   around April 21; a rebuild shortly after left it off, and there it stayed.

**The same hidden flaw affected stocks**, too — the stocks Fetcher was also left
off. It just wasn't obvious, because company financial figures only change a
couple of times a year, so stale stock data looks almost identical to fresh stock
data. Weather changes every hour, so the gap showed up clearly there first.

---

## The fix

1. **Updated the rebuild routine so it switches the Fetchers back on** — not just
   the Savers. This was the core fix for the original outage: now a server rebuild
   restores the *whole* pipeline (both Fetchers and Savers), so a rebuild can't
   silently leave the weather feed off again. (The two stuck Fetchers were also
   switched on immediately to restart fetching.)

2. **Planted the inbox bookmark — and taught the Saver to plant its own.** There's
   a subtle catch that surfaced while fixing this: the Saver keeps a "bookmark" of
   how far it has read in the inbox, and a rebuild erases that too. With no
   bookmark, the Saver defaults to "only read things that arrive *after* I start
   looking" — but the Fetcher drops its delivery and *then* nudges the Saver, so
   the Saver starts looking a split-second too late and keeps missing the very
   delivery it was nudged about. Worse, there was a chicken-and-egg gap: the
   rebuild step meant to set the bookmark could only *move an existing* bookmark,
   not *create a missing* one — so on a fresh rebuild the bookmark was never
   created and the Saver could never get going on its own. Two changes fixed this:
   (a) I created the bookmark by hand, once, pointing it at the most recent
   delivery — which is what made the backed-up weather land right away; and (b) I
   changed the Saver's code so that whenever it wakes up and finds no bookmark, it
   creates one itself before doing anything else. From now on the Saver heals
   itself after any rebuild, with no manual step.

3. **Cleared a misleading "error" on a helper program.** While investigating, I
   found the system was flagging an import error on a small helper script used for
   stock-anomaly detection. It turned out to be harmless — that script actually
   runs in a separate environment that *does* have everything it needs, and the
   anomaly results were being produced correctly the whole time. The "error" was
   just the main system trying to read a file that was never meant for it. I told
   the system to skip that file, so the false alarm no longer shows up and can't
   hide a *real* error in future.

---

## What is NOT fixed (on purpose)

The roughly six-week gap (April 22 through late May) stays empty. The weather
service we use only hands out the next 7 days of forecast — it can't give us back
the days we missed. Filling that hole would require pulling from a separate
historical-weather service, which I deliberately left out of this fix. Going
forward, data is complete from the day the Fetcher was switched back on.

---

## Why it went unnoticed for six weeks

- A switched-off program is silent — no crash, no alert.
- The dashboard still drew a weather chart, just an old one, so at a glance it
  looked alive.
- It was only caught while double-checking a new weather-summary feature, when
  someone noticed every city's data ended on the exact same day.

---

## How we prevent it happening again

- The rebuild routine now restores **both** Fetchers and Savers, so a rebuild
  brings the entire pipeline back online by itself.
- The Saver now plants its own inbox bookmark when it finds none, closing the
  chicken-and-egg gap — so even a brand-new Saver after a rebuild bootstraps
  itself instead of silently missing every delivery.
- Lesson for the future: a pipeline that has simply *stopped* is harder to spot
  than one that has *failed*. A freshness check that alerts when the newest
  weather row falls behind "now" would have caught this in a day instead of six
  weeks — worth considering as a follow-up.
