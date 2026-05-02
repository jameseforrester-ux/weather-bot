# Weather Bot — Update v6 (Looser thresholds + two-tier results)

## What changed

**Just `bot.py`.** No `polymarket.py` change, no new dependencies.

### Threshold tuning
- **Strict tier**: confidence ≥ **70%** AND market YES ≥ **40%** (was 75/40)
- **Honorable mentions tier**: confidence ≥ **60%** AND market YES ≥ **30%** (new)

### Two-tier display
The Opportunities view now shows two sections:
- 🟢 **Strict Picks** — your high-conviction trades, top
- 🟡 **Honorable Mentions** — looser tier, below the strict section

### No cap on results
All qualifying opportunities now appear (was capped at top 5). Each has its
own Details button, numbered sequentially across both tiers.

### Single network pass
Same speed as before — we scan once at the loose threshold, then classify
into tiers locally. No extra API calls.

---

## DEPLOY — STEP BY STEP

### Part A — Laptop (3 min)

**1. Find this update zip and unzip it.**
   You'll get one file: `bot.py`. Just one.

**2. Open your local weather-bot repo folder** on your computer.

**3. Drag `bot.py`** into your repo folder, replacing the old `bot.py`.

**4. Open your weather-bot repo on GitHub** in a browser.

**5. Click "Add file" → "Upload files".**

**6. Drag just `bot.py`** into the upload area. It will say "Replacing existing
   `bot.py`" — that's correct.

**7. Scroll down**, commit message:
```
v6: Two-tier opportunities (strict 70/40 + honorable 60/30), no cap
```
Make sure "Commit directly to main branch" is selected.
Click **Commit changes**.

### Part B — VPS in PuTTY (1 min)

```bash
cd ~/weather-bot
pwd                                        # confirm /root/weather-bot
git pull
sudo systemctl restart weather-bot
sleep 3
sudo systemctl status weather-bot --no-pager
```

You want `Active: active (running)`. Press `q` to exit.

### Part C — Test in Telegram

**1.** Tap 🎯 Opportunities

**2.** You should now see two sections:
```
🟢 Strict Picks — conf ≥70% · market ≥40%
1. NYC · Today  🎯 82%
   55°F → 54–55°F  ▰▰▰▰▱▱▱▱ 45%  edge +17pp

🟡 Honorable Mentions — conf ≥60% · market ≥30%
2. Tokyo · Today  🎯 65%
   ...
3. Paris · Tomorrow  🎯 67%
   ...
```

If a tier has no qualifying picks, it just shows "_none right now_" instead
of being missing — so you always know the scan ran.

---

## Why the looser thresholds help

Old (75/40): only ~1-2 cities qualify on a typical day.

New (70/40 strict + 60/30 honorable): typically 3-8 picks across both tiers.

The **honorable tier** is where most of the real value lives. Those are picks
where either:
- Our model is confident but the crowd is split (market YES 30-40% on our pick) — **the crowd may be missing what 8 NWP models agree on**, OR
- Our confidence is 60-70% (still meaningful) but slightly noisier — **still usable, just position-size accordingly**

You should treat strict picks as "size up" and honorable mentions as
"size down but still consider".

---

## Troubleshooting

**Still only seeing NYC after this update:** That genuinely means most other
cities don't have markets published yet, OR they're in volatile weather. Run
in PuTTY to confirm:
```bash
sudo journalctl -u weather-bot -n 200 --no-pager | grep polymarket
```
Look for `no event for X/Y` lines vs `→ N buckets` lines. If most are
"no event", Polymarket hasn't listed those cities yet.

**Both tiers empty:** Polymarket's daily markets typically go live around
9 AM ET. If you're checking before then, most cities won't have markets yet.
