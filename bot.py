"""Telegram Weather Prediction Bot — main entry point.

Features:
- Search a city to see nearby airports as tappable buttons.
- Get a 7-day max-temp forecast for any ICAO/IATA airport, derived from a
  weighted ensemble of 8 leading numerical weather prediction models.
- Per-day confidence score, green-flag for high-confidence predictions, and
  a probability for each integer temperature.
- Track airports for ≥2°F / ≥1°C change alerts on the predicted max.
- Bottom-left commands menu + persistent reply keyboard for fast access.
"""
import asyncio
import logging
import re
from typing import Optional

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    MenuButtonCommands,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from airports import AirportDatabase, ensure_airport_data, geocode_city
from config import (
    AIRPORTS_CSV,
    ALERT_THRESHOLD_C,
    ALERT_THRESHOLD_F,
    BOT_TOKEN,
    DB_PATH,
    LOG_LEVEL,
    TRACKING_INTERVAL_MINUTES,
)
from tracking import TrackingDB
from weather import (
    DayForecast,
    MODEL_DISPLAY,
    fetch_current_observation,
    fetch_ensemble_forecast,
)

# ─────────────────────────── setup ────────────────────────────────
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("weather-bot")

airports_db = AirportDatabase()
tracking_db = TrackingDB(DB_PATH)


# ─────────────────────────── messages ─────────────────────────────
WELCOME = (
    "🌤️ *Weather Prediction Bot* 🌤️\n\n"
    "I provide highly accurate temperature forecasts using a weighted "
    "*ensemble of the world's best NWP models*:\n"
    "🇪🇺 ECMWF IFS  ·  🇪🇺 ECMWF AIFS (AI)  ·  🇬🇧 UK Met Office\n"
    "🇩🇪 DWD ICON  ·  🇺🇸 NOAA GFS  ·  🇯🇵 JMA  ·  🇫🇷 Météo-France  ·  🇨🇦 GEM\n\n"
    "✨ *What I can do:*\n"
    "🔍 Search a city → see nearby airports\n"
    "✈️ Forecast by ICAO / IATA code\n"
    "🎯 Predictions calibrated to ±2° with a confidence score\n"
    "📊 Probability for each integer temperature\n"
    "🔔 Track airports → alert if forecast shifts ≥2°F (≥1°C)\n"
    "🌡️ Temperatures in both °F and °C\n\n"
    "Tap *Menu* (bottom-left) for quick access. "
    "Or just type a city or airport code!"
)


HELP = (
    "*🆘 Help & Methodology*\n\n"
    "*Commands*\n"
    "/start — Welcome message\n"
    "/search `<city>` — Find nearby airports\n"
    "/forecast `<code>` — Forecast for an airport\n"
    "/track `<code>` — Track for change alerts\n"
    "/untrack `<code>` — Stop tracking\n"
    "/list — Your tracked airports\n"
    "/help — This help\n\n"
    "*Examples*\n"
    "`/search New York` · `/forecast KJFK` · `/forecast LAX` · `/track EGLL`\n"
    "_Tip:_ you can also just type a city name or airport code directly.\n\n"
    "*Methodology*\n"
    "The forecast combines 8 numerical weather prediction models:\n"
    "• ECMWF IFS (highest weight — global skill leader)\n"
    "• ECMWF AIFS (ECMWF's AI model — peer to WeatherNext)\n"
    "• UK Met Office, DWD ICON, NOAA GFS, JMA, Météo-France, GEM\n\n"
    "We compute a weighted ensemble mean and use the inter-model standard "
    "deviation as a calibrated uncertainty estimate. A Gaussian over that "
    "uncertainty gives a probability for each whole-number temperature.\n\n"
    "🟢 = High confidence (low spread between models — usually within ±2°)\n"
    "🟡 = Medium confidence\n"
    "🔴 = Low confidence (large model disagreement)\n\n"
    "Current conditions come from the airport's *METAR* weather station "
    "where available; otherwise from Open-Meteo's nearest grid cell."
)


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🔍 Search City"), KeyboardButton("✈️ Forecast")],
            [KeyboardButton("📋 My Tracked"), KeyboardButton("❓ Help")],
        ],
        resize_keyboard=True,
    )


# ─────────────────────────── command handlers ─────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        WELCOME, parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard()
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP, parse_mode=ParseMode.MARKDOWN)


async def cmd_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args:
        await update.effective_message.reply_text(
            "🔍 Send me a city name.\n\nExample: `/search Tokyo`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    await do_city_search(update, " ".join(ctx.args))


async def cmd_forecast(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args:
        await update.effective_message.reply_text(
            "✈️ Send me an airport code.\n\nExample: `/forecast KJFK`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    await send_forecast(update, ctx.args[0])


async def cmd_track(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args:
        await update.effective_message.reply_text(
            "🔔 Specify an airport.\n\nExample: `/track KJFK`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    await track_airport(update, ctx.args[0])


async def cmd_untrack(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args:
        await update.effective_message.reply_text(
            "🔕 Specify an airport.\n\nExample: `/untrack KJFK`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    code = ctx.args[0].upper().strip()
    user_id = update.effective_user.id
    if tracking_db.remove(user_id, code):
        await update.effective_message.reply_text(
            f"✅ No longer tracking *{code}*.", parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.effective_message.reply_text(
            f"ℹ️ You weren't tracking *{code}*.", parse_mode=ParseMode.MARKDOWN
        )


async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    rows = tracking_db.list_user(user_id)
    if not rows:
        await update.effective_message.reply_text(
            "📋 You aren't tracking any airports yet.\n"
            "Use `/track <code>` or tap *Track* on a forecast.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    lines = ["📋 *Your Tracked Airports*\n"]
    keyboard = []
    for code, last_c, last_f, last_check in rows:
        airport = airports_db.lookup(code)
        name = airport.name if airport else code
        line = f"\n{airport.type_emoji if airport else '✈️'} *{code}* — {_md_safe(name[:40])}"
        if last_f is not None and last_c is not None:
            line += f"\n   _Last forecast: {int(round(last_f))}°F / {int(round(last_c))}°C_"
        lines.append(line)
        keyboard.append(
            [
                InlineKeyboardButton(f"📊 {code}", callback_data=f"fc:{code}"),
                InlineKeyboardButton("🔕 Untrack", callback_data=f"untrack:{code}"),
            ]
        )

    await update.effective_message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ─────────────────────────── core flows ───────────────────────────
async def do_city_search(update: Update, city: str) -> None:
    msg = await update.effective_message.reply_text(
        f"🔍 Searching for *{_md_safe(city)}*…", parse_mode=ParseMode.MARKDOWN
    )
    try:
        results = await geocode_city(city, count=3)
    except Exception as e:
        log.exception("geocode failed")
        await msg.edit_text(f"❌ Search failed: {e}")
        return

    if not results:
        await msg.edit_text(
            f"❌ No locations found for *{_md_safe(city)}*.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    top = results[0]
    lat, lon = top["latitude"], top["longitude"]
    place = top["name"]
    region = top.get("admin1") or ""
    country = top.get("country_code") or ""
    label = ", ".join(p for p in (place, region, country) if p)

    nearby = airports_db.search_near(lat, lon, radius_km=200, limit=8)
    if not nearby:
        await msg.edit_text(
            f"📍 Found *{_md_safe(label)}* but no airports within 200 km.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    text = (
        f"📍 *{_md_safe(label)}*\n"
        f"_({top['latitude']:.2f}, {top['longitude']:.2f})_\n\n"
        f"✈️ *Airports within 200 km* — tap one for a forecast:"
    )
    rows = []
    for ap in nearby:
        code = ap.iata or ap.icao
        btn_label = f"{ap.type_emoji} {code} — {ap.name[:32]}"
        rows.append([InlineKeyboardButton(btn_label, callback_data=f"fc:{ap.icao}")])

    await msg.edit_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def send_forecast(update: Update, code: str) -> None:
    code = code.upper().strip()
    airport = airports_db.lookup(code)
    if not airport:
        await update.effective_message.reply_text(
            f"❌ Airport not found: `{_md_safe(code)}`\n"
            "Try an ICAO (4-letter, e.g. `KJFK`) or IATA (3-letter, e.g. `LAX`) code.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    msg = await update.effective_message.reply_text(
        f"⏳ Fetching ensemble forecast for *{airport.icao}*…\n"
        f"_combining {len(MODEL_DISPLAY)} models…_",
        parse_mode=ParseMode.MARKDOWN,
    )

    forecasts, current = await asyncio.gather(
        fetch_ensemble_forecast(airport.lat, airport.lon, days=7),
        fetch_current_observation(airport.icao, airport.lat, airport.lon),
        return_exceptions=True,
    )
    if isinstance(forecasts, Exception):
        log.exception("forecast fetch failed", exc_info=forecasts)
        await msg.edit_text(f"❌ Forecast failed: {forecasts}")
        return
    if isinstance(current, Exception):
        current = None
    if not forecasts:
        await msg.edit_text("❌ No forecast data available for this location.")
        return

    text = format_forecast(airport, forecasts, current)
    keyboard = [
        [
            InlineKeyboardButton("🔔 Track", callback_data=f"track:{airport.icao}"),
            InlineKeyboardButton("🔄 Refresh", callback_data=f"fc:{airport.icao}"),
            InlineKeyboardButton("🧠 Models", callback_data=f"models:{airport.icao}"),
        ]
    ]
    await msg.edit_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def show_models_breakdown(update: Update, code: str) -> None:
    code = code.upper().strip()
    airport = airports_db.lookup(code)
    if not airport:
        return
    forecasts = await fetch_ensemble_forecast(airport.lat, airport.lon, days=2)
    if not forecasts:
        await update.effective_message.reply_text("❌ No data.")
        return
    today = forecasts[0]
    lines = [
        f"🧠 *Per-model breakdown for {airport.icao}* — Today",
        "",
        f"Ensemble mean: *{today.predicted_max_f}°F / {today.predicted_max_c}°C*",
        f"Spread (σ): {today.std_c:.2f}°C  ·  Confidence: *{int(today.confidence*100)}%*",
        "",
        "*Individual model max-temp predictions:*",
    ]
    for m, v in sorted(
        today.model_values_c.items(), key=lambda x: -x[1] if x[1] else 0
    ):
        f_val = v * 9 / 5 + 32
        lines.append(
            f"• {MODEL_DISPLAY.get(m, m)}: {int(round(f_val))}°F / {int(round(v))}°C"
        )
    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.MARKDOWN
    )


async def track_airport(update: Update, code: str) -> None:
    code = code.upper().strip()
    airport = airports_db.lookup(code)
    if not airport:
        await update.effective_message.reply_text(
            f"❌ Airport not found: `{_md_safe(code)}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    if tracking_db.add(user_id, chat_id, airport.icao):
        await update.effective_message.reply_text(
            f"✅ Now tracking *{airport.icao}* — {_md_safe(airport.name)}\n\n"
            f"You'll get an alert if the predicted max temp shifts by "
            f"≥{int(ALERT_THRESHOLD_F)}°F ({ALERT_THRESHOLD_C:g}°C). "
            f"Checks run every {TRACKING_INTERVAL_MINUTES} min.",
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        await update.effective_message.reply_text(
            f"ℹ️ Already tracking *{airport.icao}*.",
            parse_mode=ParseMode.MARKDOWN,
        )


# ─────────────────────────── formatting ──────────────────────────
def _md_safe(s: str) -> str:
    """Escape characters that break Telegram's legacy Markdown."""
    if not s:
        return ""
    for ch in ("*", "_", "`", "["):
        s = s.replace(ch, " ")
    return s


def _flag(f: DayForecast) -> str:
    if f.high_confidence:
        return "🟢"
    if f.confidence_level == "MEDIUM":
        return "🟡"
    return "🔴"


def format_forecast(airport, forecasts, current) -> str:
    parts = []
    code_str = f"*{airport.icao}*"
    if airport.iata:
        code_str += f" / *{airport.iata}*"
    parts.append(f"{airport.type_emoji} {code_str}")
    parts.append(f"📍 {_md_safe(airport.name)}")
    parts.append(f"🌍 {_md_safe(airport.city)}, {airport.country}")

    # Current observation block
    if current and current.temp_c is not None:
        c = int(round(current.temp_c))
        f_v = int(round(current.temp_f))
        line = f"\n📡 *Current ({current.source})*: {f_v}°F / {c}°C"
        if current.wind_kt is not None:
            wd = current.wind_dir if current.wind_dir is not None else "—"
            line += f"  ·  💨 {int(round(current.wind_kt))} kt @ {wd}°"
        parts.append(line)

    parts.append(
        "\n🧠 *Ensemble forecast* — 8 NWP models, ECMWF-weighted"
    )
    parts.append("─" * 26)

    for i, fc in enumerate(forecasts):
        if i == 0:
            day_label = "📅 *Today*"
        elif i == 1:
            day_label = "📅 *Tomorrow*"
        else:
            day_label = f"📅 *{fc.date.strftime('%a')}*"
        date_label = fc.date.strftime("%b %d")

        flag = _flag(fc)
        parts.append(f"\n{day_label} _{date_label}_")
        parts.append(
            f"🌡️ Max: *{fc.predicted_max_f}°F / {fc.predicted_max_c}°C*  {flag}"
        )

        # Range (mean ± σ rounded)
        lo_c = int(round(fc.ensemble_mean_c - fc.std_c))
        hi_c = int(round(fc.ensemble_mean_c + fc.std_c))
        lo_f = int(round(lo_c * 9 / 5 + 32))
        hi_f = int(round(hi_c * 9 / 5 + 32))
        parts.append(
            f"   📉 Range ±1σ: {lo_f}–{hi_f}°F ({lo_c}–{hi_c}°C)"
        )

        parts.append(
            f"   🎯 Confidence: *{int(fc.confidence * 100)}%* "
            f"({fc.confidence_level}) · σ {fc.std_c:.1f}°C"
        )

        # Top 3 probabilities (Fahrenheit)
        top = sorted(fc.probability_f.items(), key=lambda x: -x[1])[:3]
        prob_str = " · ".join(f"{t}°F: *{int(p * 100)}%*" for t, p in top)
        parts.append(f"   📊 {prob_str}")

    parts.append("\n" + "─" * 26)
    parts.append("🟢 high confidence  ·  🟡 medium  ·  🔴 low")
    parts.append("_Predictions are whole-number °F and °C, ensemble-calibrated to ±2°._")
    return "\n".join(parts)


# ─────────────────────────── handlers ─────────────────────────────
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    if data.startswith("fc:"):
        await send_forecast(update, data[3:])
    elif data.startswith("track:"):
        await track_airport(update, data[6:])
    elif data.startswith("untrack:"):
        code = data[8:]
        if tracking_db.remove(update.effective_user.id, code):
            await q.message.reply_text(
                f"✅ No longer tracking *{code}*.",
                parse_mode=ParseMode.MARKDOWN,
            )
    elif data.startswith("models:"):
        await show_models_breakdown(update, data[7:])


_AIRPORT_CODE_RE = re.compile(r"^[A-Za-z]{3,4}$")


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    txt = (update.effective_message.text or "").strip()
    if not txt:
        return

    # Reply-keyboard buttons
    if txt == "🔍 Search City":
        await update.effective_message.reply_text(
            "🔍 Send me a city name — just type it.\n\nExample: *London*",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    if txt == "✈️ Forecast":
        await update.effective_message.reply_text(
            "✈️ Send me an airport code — just type it.\n\nExample: *KJFK*  or  *LAX*",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    if txt == "📋 My Tracked":
        await cmd_list(update, ctx)
        return
    if txt == "❓ Help":
        await cmd_help(update, ctx)
        return

    # 3- or 4-letter code that maps to a known airport → forecast
    if _AIRPORT_CODE_RE.match(txt) and airports_db.lookup(txt):
        await send_forecast(update, txt)
        return

    # Anything else → city search
    await do_city_search(update, txt)


# ─────────────────────────── tracking job ─────────────────────────
async def tracking_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    rows = tracking_db.list_all()
    if not rows:
        return
    log.info("tracking_job: checking %d entries", len(rows))
    for row_id, user_id, chat_id, code, last_c, last_f in rows:
        airport = airports_db.lookup(code)
        if not airport:
            continue
        try:
            forecasts = await fetch_ensemble_forecast(airport.lat, airport.lon, days=1)
        except Exception:
            log.exception("forecast fetch failed for %s", code)
            continue
        if not forecasts:
            continue
        today = forecasts[0]
        new_c = today.predicted_max_c
        new_f = today.predicted_max_f

        if last_c is not None and last_f is not None:
            d_c = abs(new_c - last_c)
            d_f = abs(new_f - last_f)
            if d_c >= ALERT_THRESHOLD_C or d_f >= ALERT_THRESHOLD_F:
                arrow = "📈" if new_c > last_c else "📉"
                flag = _flag(today)
                msg = (
                    f"🔔 *Forecast Alert: {code}*\n"
                    f"📍 {_md_safe(airport.name)}\n\n"
                    f"{arrow} Today's predicted max changed:\n"
                    f"   Old: {int(round(last_f))}°F / {int(round(last_c))}°C\n"
                    f"   New: *{new_f}°F / {new_c}°C* {flag}\n"
                    f"   Δ: {int(new_f - last_f):+d}°F / {int(new_c - last_c):+d}°C\n\n"
                    f"Confidence: *{int(today.confidence * 100)}%* "
                    f"({today.confidence_level})"
                )
                try:
                    await ctx.bot.send_message(
                        chat_id, msg, parse_mode=ParseMode.MARKDOWN
                    )
                except Exception:
                    log.exception("failed to send alert to chat %s", chat_id)

        tracking_db.update_last(row_id, new_c, new_f)


# ─────────────────────────── lifecycle ────────────────────────────
async def post_init(app: Application) -> None:
    commands = [
        BotCommand("start", "🌟 Welcome & menu"),
        BotCommand("search", "🔍 Search city for airports"),
        BotCommand("forecast", "✈️ Forecast by airport code"),
        BotCommand("track", "🔔 Track for change alerts"),
        BotCommand("untrack", "🔕 Stop tracking"),
        BotCommand("list", "📋 Your tracked airports"),
        BotCommand("help", "❓ Help & methodology"),
    ]
    await app.bot.set_my_commands(commands)
    await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    log.info("Commands menu registered (bottom-left button)")


def main() -> None:
    log.info("Loading airport database…")
    ensure_airport_data(AIRPORTS_CSV)
    airports_db.load_from_csv(AIRPORTS_CSV)
    log.info("Loaded %d airports", len(airports_db.airports))

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("forecast", cmd_forecast))
    app.add_handler(CommandHandler("track", cmd_track))
    app.add_handler(CommandHandler("untrack", cmd_untrack))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    if app.job_queue is not None:
        app.job_queue.run_repeating(
            tracking_job,
            interval=TRACKING_INTERVAL_MINUTES * 60,
            first=60,
            name="tracking_job",
        )
        log.info("Tracking job scheduled every %d min", TRACKING_INTERVAL_MINUTES)
    else:
        log.warning(
            "job_queue not available — install python-telegram-bot[job-queue]"
        )

    log.info("Starting bot — long polling")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
