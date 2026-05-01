# Weather Bot — Update v4 (Polymarket overhaul)

## What's new

**🎲 35 supported Polymarket cities** (up from 16). Includes Seoul, Shanghai,
Hong Kong, Tokyo, Singapore, Beijing, Shenzhen, Guangzhou, Wuhan, Qingdao,
Taipei, Manila, Jakarta, Busan, São Paulo, Buenos Aires, Madrid, Warsaw,
Moscow, Helsinki, Ankara, Tel Aviv, Cape Town, Wellington, Panama City, and
all the originals.

**🏟️ Resolution stations** for every city. Polymarket settles each market
against a specific station (Denver = Buckley Space Force Base / KBKF, Tokyo
= Haneda / RJTT, Hong Kong = HK Observatory / VHHH, etc.) — we now predict
**at the exact station** in Polymarket mode and footnote it in every block.

**🎯 Two-mode UX** with a new keyboard:
- 🎲 **Polymarket** — pick a city, focused 3-day forecast at the resolution
  station, with live odds. *Predictions guaranteed to match the station the
  market actually settles on.*
- 🌤️ **Forecast** — search any of ~80,000 airports worldwide. If the airport
  is within 80 km of a covered city, the Polymarket section appears inline
  via geographic fallback.

**🌍 City-local "today"** — Tokyo's "today" is computed in JST, not UTC, so
when you ask about Tokyo at 11 PM ET on May 1, you correctly see the Tokyo
market for May 2 (which started 13 hours ago there).

**🟩 New bucket visualization** — visual YES bar with NO % complement:
```
54–55°F  ✅  Trade
🟩🟩🟩⬜⬜⬜⬜⬜⬜⬜  30% YES  (70% NO)
```

**📅 3-day horizon** — both modes now show today + 2 days (was 7).

**Geographic fallback** — KMRY (Monterey) is too far from any covered city,
so Polymarket section silently hides. Watford, UK auto-maps to London market
even though it has no airport ICAO. KBKF (Buckley) explicitly maps to Denver
so it always works regardless of geographic coincidence.

## Files in this zip

| File | Status | Notes |
|---|---|---|
| `polymarket.py` | **Rewrite** | 35 cities, resolution stations, geo fallback, timezone awareness |
| `bot.py` | **Major changes** | New /polymarket flow, new keyboard, new bar viz, 3-day horizon |

## Deploy (same as always)

**Laptop:**
```bash
# Drop the 2 files into your local repo, overwriting old versions, then:
git add bot.py polymarket.py
git commit -m "Polymarket: 35 cities, resolution stations, two-mode UX, bar viz"
git push
```

**VPS (PuTTY):**
```bash
cd ~/weather-bot
git pull
sudo systemctl restart weather-bot
sudo systemctl status weather-bot --no-pager
```

No new dependencies. No DB migration. No `pip install`.

## Test in Telegram

1. **`/start`** — confirm new keyboard shows 🎲 Polymarket and 🌤️ Forecast
2. Tap **🎲 Polymarket** — you'll see a 35-city picker, alphabetical
3. Tap any city — get 3-day forecast at the resolution station with bar viz
4. **`/forecast KBKF`** (Buckley AFB) — Denver Polymarket section now shows
   inline (was hidden before because KBKF wasn't in our list)
5. **`/forecast EDDF`** (Frankfurt) — Polymarket section silently hidden
   (Frankfurt isn't covered)
6. **`/polymarket`** is now a slash command too

## Diagnose if anything goes sideways

```bash
sudo journalctl -u weather-bot -f
```

You'll see lines like:
- `polymarket: matched highest-temperature-in-tokyo-on-may-2-2026 for tokyo/2026-05-02`
- `polymarket: tokyo → 11 buckets`
- `polymarket: geo fallback KCCR → san-francisco` (when geographic lookup kicks in)
- `polymarket: no event for cape-town/2026-05-01 (tried 7 slugs)` (when city has no
  market for that day yet — this is normal for less-active cities)
