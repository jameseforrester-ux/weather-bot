# Weather Bot — Update v5 (Opportunities + Compact UI)

## What's new

### 🎯 NEW: Opportunities scanner
A new top-level button and `/opportunities` command. It scans **all 35 cities
× today + tomorrow** in the cities' local timezones, filters to the trades
worth your attention, and ranks them.

**Filters** (both must pass):
- Our model confidence ≥ 75%
- The matched bucket has ≥ 40% YES on Polymarket (crowd somewhat agrees)

**Ranking:** combined score = model confidence × edge × model probability.
Surfaces the highest-conviction mispricings, not just the obvious consensus.

**Tap any opportunity** to drill in. You see:
- 💰 Best EV pick (highest expected value per $1 staked)
- 🎯 Matched bucket (what our model directly predicts)
- A third runner-up
- Each with a one-tap **▶ Trade** button straight to Polymarket
- EV math shown: `model%` vs `market%`, edge in pp, $/$ EV

### 📐 Compact bar visualization
Fixes the mobile-readability issue. Was:
```
🟩🟩🟩⬜⬜⬜⬜⬜⬜⬜  30% YES  (70% NO)   ← 3 lines per bucket
```
Now:
```
▰▰▰▱▱▱▱▱▱▱ 54–55°F ✅ · 30%Y 70%N  Trade   ← 1 line per bucket
```

About 60% less vertical space on phones.

## Files

| File | Status |
|---|---|
| `polymarket.py` | Adds `EVPick`, `Opportunity`, `model_yes_prob_for_bucket`, `rank_buckets_by_ev`, `score_opportunity`. Existing functions unchanged. |
| `bot.py` | Adds `/opportunities` command, scanner, detail view, compact bar, new keyboard button. Existing flows unchanged. |

No new dependencies. No DB migration.

---

## STEP-BY-STEP DEPLOY

### Part A — On your laptop (5 min)

**1. Find the zip.**
   Look for `weather-bot-update-v5.zip` (the file I just gave you).
   Right-click → "Extract All" / unzip.

**2. Open your local weather-bot repo folder.**
   This is the folder on your laptop you originally pushed to GitHub. It has
   `bot.py`, `polymarket.py`, etc. in it.

**3. Copy the two new files in, overwriting the old ones.**
   From the unzipped `wb-update-v5/` folder, drag `bot.py` and `polymarket.py`
   into your local repo folder. When asked "Replace existing files?" → **YES**.
   Don't copy `UPDATE.md` (this file).

**4. Open GitHub in your browser.** Go to your weather-bot repo.

**5. Click "Add file" → "Upload files".**

**6. Drag `bot.py` and `polymarket.py`** from your repo folder into the
   upload area. GitHub will show a warning that you're replacing files —
   that's exactly right.

**7. Scroll down**, type a commit message:
   ```
   v5: Opportunities scanner + compact bar
   ```
   Make sure "Commit directly to the main branch" is selected.
   Click the green **Commit changes** button.

**8. Verify on GitHub:** click `polymarket.py`, search (Ctrl+F) for `EVPick`.
   If you find it — upload worked.

### Part B — On the VPS in PuTTY (1 min)

**9. Open PuTTY. Connect.**

**10. Get into the right folder.** ⚠️ This is where things broke before.
```bash
cd ~/weather-bot
pwd
```
`pwd` should print `/root/weather-bot`. If it prints something else, you're
in the wrong folder — re-run `cd ~/weather-bot`.

**11. Pull the new code:**
```bash
git pull
```
You should see lines like `Updating ... Fast-forward ... 2 files changed`.
- If it says **"Already up to date"** → upload didn't save on GitHub. Go back to step 7.
- If it asks for **Username** → re-set the token URL:
  ```bash
  git remote set-url origin https://<YOUR_TOKEN>@github.com/jameseforrester-ux/weather-bot.git
  git pull
  ```

**12. Restart the bot:**
```bash
sudo systemctl restart weather-bot
sleep 3
sudo systemctl status weather-bot --no-pager
```
You want `Active: active (running)`. Press `q` to exit.

**13. (Optional) Watch live logs while you test:**
```bash
sudo journalctl -u weather-bot -f
```
Press Ctrl+C when done. Bot keeps running.

### Part C — Test in Telegram

**14.** `/start` — confirm new keyboard shows 🎯 Opportunities

**15. Tap 🎯 Opportunities** — bot scans 35 markets × 2 days. Takes 5-15
seconds (35 × 2 forecasts + market fetches in parallel).

You'll see one of:
- **A list of top 5** — each has a **📋 Details** button to drill in.
- **"No high-confidence opportunities right now"** — totally normal mid-day
  when forecasts are uncertain. Try again in a few hours, or near a market
  resolution (forecasts firm up).

**16. Tap a Details button.** You see best EV pick, matched bucket,
runner-up, with **▶ Trade** buttons that open Polymarket directly.

---

## Troubleshooting

**Bot fails to start after pull:**
```bash
sudo journalctl -u weather-bot -n 30 --no-pager
```
Paste the last 20 lines if it doesn't start.

**Scan takes forever / times out:**
This is rare but possible if Polymarket's API is slow. The bot will time out
gracefully after ~30s. Try `/opportunities` again.

**Empty results all the time:**
Could legitimately mean no markets currently meet the threshold. To verify
the scanner is working:
- Run `/polymarket` → pick NYC → see if today's markets are listed.
- If yes, the scanner works; the threshold just isn't met right now.
- If no markets show for any city, check Polymarket's website — they may
  not have published today's markets yet (usually they're up by 9am ET).
