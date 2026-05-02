# Weather Bot — Update v7 (Two bug fixes)

## What this fixes

### 🐛 Bug 1: "No longer an opportunity" on every honorable mention

When you tapped Details on an honorable-tier pick, the bot re-fetched it
using the **strict** thresholds (70/40), which the honorable pick was never
designed to clear. Result: every honorable-mention click told you the
opportunity had "shifted" when nothing had actually changed.

**Fix:** Detail re-fetch now uses the **honorable** thresholds (60/30), so
any opportunity from either tier round-trips cleanly. If it genuinely fails
even the loose threshold, the error message now says exactly which threshold
was missed (confidence vs market vs market unpublished) and gives you a
"View Polymarket" button to investigate manually.

### 🐛 Bug 2: Confusing summary line numbers

The summary showed the **best EV pick's** price (e.g. `18%`) on the same
line as our predicted temperature, making it look like the pick had only
18% market support — even though the **matched bucket** (which is what the
filter actually qualifies on) might have had 33%.

**Fix:** Summary now renders 3 lines per opportunity, separating the two:

Before (one confusing line):
```
1. Tokyo · Today  🎯 72%
   22°C → 22°C  ▰▱▱▱▱▱▱▱ 18%  edge +13pp
                ↑ this looked like 18% < 30% honorable threshold violation
```

After (clear separation):
```
1. Tokyo · Today  🎯 72% model
   🎯 22°C → 21°C  ▰▰▰▱▱▱▱▱ 33% market   ← matched bucket (filter qualifier)
   💰 Best EV: 22°C  ▰▱▱▱▱▱▱▱ 18% · edge +13pp   ← best EV pick (recommendation)
```

Now you can see at a glance:
- 🎯 line = the bucket that qualifies the trade for the tier
- 💰 line = the bucket we actually recommend you trade

Sometimes they're the same bucket — in that case the 💰 line collapses to
`💰 Best EV: same bucket · edge +Xpp`.

## File

Only `bot.py`. No `polymarket.py` change. No new deps.

## Deploy

### Laptop
1. Unzip → drag `bot.py` into your local repo, replacing the old one.
2. GitHub: Add file → Upload files → drop `bot.py` → commit message:
   ```
   v7: Fix honorable-mention re-scan + clarify summary line
   ```
   → Commit changes.

### PuTTY
```bash
cd ~/weather-bot
git pull
sudo systemctl restart weather-bot
sleep 3
sudo systemctl status weather-bot --no-pager
```

### Test
1. `/opportunities` → tap any honorable-mention Details button → it should
   now load successfully (was always failing before).
2. The summary lines should now show two distinct rows per opportunity (the
   🎯 matched bucket and the 💰 best EV pick).
