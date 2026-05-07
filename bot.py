import json
import logging
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, filters

load_dotenv()

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CMC_API_KEY = os.environ["CMC_API_KEY"]

CMC_QUOTES_URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
CG_BASE = "https://api.coingecko.com/api/v3"
TON_CMC_ID = 11419
TON_CG_ID = "the-open-network"
SPIKE_THRESHOLD_PCT = 10.0
ALERT_COOLDOWN_HOURS = 2

# Hourly swing alert — compares our captured price snapshots each hour
_swing_pct_raw = os.environ.get("HOURLY_SWING_PCT", "10").strip()
HOURLY_SWING_PCT: float = float(_swing_pct_raw) if _swing_pct_raw else 10.0
HOURLY_SWING_TAG: str = os.environ.get("HOURLY_SWING_TAG", "").strip()  # e.g. @username or @user1,@user2

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Target chat parsing
# Supports comma-separated entries, each as CHAT_ID or CHAT_ID:THREAD_ID
# e.g. TARGET_CHAT_ID=-1001234567890:42,-1009876543210
# ---------------------------------------------------------------------------

def _parse_targets(raw: str) -> list[tuple[int, int | None]]:
    result = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":", 1)
        result.append((int(parts[0]), int(parts[1]) if len(parts) == 2 else None))
    return result


_target_raw = os.environ.get("TARGET_CHAT_ID", "").strip()
TARGETS: list[tuple[int, int | None]] = _parse_targets(_target_raw) if _target_raw else []
TARGET_CHAT_IDS: set[int] = {chat_id for chat_id, _ in TARGETS}

# Commands restricted to configured chats only.
# If no target is set yet, allow any non-private chat (useful during initial setup).
if TARGET_CHAT_IDS:
    CHAT_FILTER = filters.Chat(chat_id=list(TARGET_CHAT_IDS))
else:
    CHAT_FILTER = ~filters.ChatType.PRIVATE


# ---------------------------------------------------------------------------
# Price alerts — persistent, stored in alerts.json
# ---------------------------------------------------------------------------

ALERTS_FILE = Path("alerts.json")


@dataclass
class PriceAlert:
    target: float
    direction: str  # "above" | "below"


def _load_alerts() -> list[PriceAlert]:
    if not ALERTS_FILE.exists():
        return []
    try:
        return [PriceAlert(**a) for a in json.loads(ALERTS_FILE.read_text())]
    except Exception as exc:
        log.warning("Could not load alerts.json: %s", exc)
        return []


def _save_alerts(alerts: list[PriceAlert]) -> None:
    try:
        ALERTS_FILE.write_text(json.dumps([asdict(a) for a in alerts], indent=2))
    except Exception as exc:
        log.warning("Could not save alerts.json: %s", exc)


_price_alerts: list[PriceAlert] = _load_alerts()


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_alert_cooldown_until: datetime | None = None
_last_hourly_price: float | None = None  # price captured at last hourly tick


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

async def fetch_ton_cmc() -> dict:
    """Current USD quote from CoinMarketCap."""
    headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY, "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            CMC_QUOTES_URL, headers=headers, params={"id": TON_CMC_ID, "convert": "USD"}
        )
        resp.raise_for_status()
    return resp.json()["data"][str(TON_CMC_ID)]["quote"]["USD"]


async def fetch_ton_cg_coin() -> dict:
    """Full coin data from CoinGecko (includes ATH, % from ATH, etc.)."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{CG_BASE}/coins/{TON_CG_ID}",
            params={
                "localization": "false",
                "tickers": "false",
                "market_data": "true",
                "community_data": "false",
                "developer_data": "false",
            },
        )
        resp.raise_for_status()
    return resp.json()


async def fetch_ton_cg_high(days: int) -> float:
    """Highest price over the last N days from CoinGecko (free, no key needed)."""
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"{CG_BASE}/coins/{TON_CG_ID}/market_chart",
            params={"vs_currency": "usd", "days": days},
        )
        resp.raise_for_status()
    prices = resp.json()["prices"]  # [[timestamp_ms, price], ...]
    return max(p[1] for p in prices)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def fmt_price(price: float) -> str:
    return f"${price:,.4f}"


def fmt_pct(pct: float) -> str:
    arrow = "▲" if pct >= 0 else "▼"
    return f"{arrow} {abs(pct):.2f}%"


def fmt_date(iso: str) -> str:
    return iso[:10]


# ---------------------------------------------------------------------------
# Broadcast
# ---------------------------------------------------------------------------

async def broadcast(app: Application, text: str) -> None:
    for chat_id, thread_id in TARGETS:
        try:
            await app.bot.send_message(
                chat_id=chat_id,
                message_thread_id=thread_id,
                text=text,
                parse_mode="HTML",
            )
        except Exception as exc:
            log.warning("Send failed → chat %s thread %s: %s", chat_id, thread_id, exc)


# ---------------------------------------------------------------------------
# Startup health checks
# ---------------------------------------------------------------------------

async def startup_checks(app: Application) -> None:
    errors: list[str] = []

    try:
        me = await app.bot.get_me()
        log.info("✓ Telegram bot: @%s", me.username)
    except Exception as exc:
        errors.append(f"Telegram token invalid: {exc}")

    try:
        await fetch_ton_cmc()
        log.info("✓ CoinMarketCap API key: valid")
    except Exception as exc:
        errors.append(f"CoinMarketCap API error: {exc}")

    for chat_id, thread_id in TARGETS:
        try:
            chat = await app.bot.get_chat(chat_id)
            label = f"{chat.title or chat_id}" + (f" (topic {thread_id})" if thread_id else "")
            log.info("✓ Target chat accessible: %s", label)
        except Exception as exc:
            errors.append(f"Cannot access chat {chat_id}: {exc}")

    if not TARGETS:
        log.warning("⚠ TARGET_CHAT_ID not set — bot will not post automatically")

    if errors:
        for e in errors:
            log.error("✗ %s", e)
        sys.exit(1)

    log.info("All checks passed. Bot is ready.")


# ---------------------------------------------------------------------------
# Scheduled jobs
# ---------------------------------------------------------------------------

async def _check_price_alerts(app: Application, price: float) -> None:
    """Fire and remove any price alerts that the current price has crossed."""
    global _price_alerts
    if not _price_alerts:
        return

    triggered = [
        a for a in _price_alerts
        if (a.direction == "above" and price >= a.target)
        or (a.direction == "below" and price <= a.target)
    ]
    if not triggered:
        return

    _price_alerts = [a for a in _price_alerts if a not in triggered]
    _save_alerts(_price_alerts)

    tag = f"\n{HOURLY_SWING_TAG}" if HOURLY_SWING_TAG else ""
    for alert in triggered:
        emoji = "🎯"
        label = "reached" if alert.direction == "above" else "dropped to"
        await broadcast(
            app,
            f"{emoji} <b>TON Price Alert</b>\n"
            f"Target {label}: <b>{fmt_price(alert.target)}</b>\n"
            f"Current price: <b>{fmt_price(price)}</b>"
            f"{tag}",
        )
        log.info("Price alert fired: target %s (%s), current %s", alert.target, alert.direction, price)


async def job_spike_check(app: Application) -> None:
    global _alert_cooldown_until
    if not TARGETS:
        return
    try:
        q = await fetch_ton_cmc()
    except Exception as exc:
        log.error("CMC fetch error: %s", exc)
        return

    price = q["price"]
    pct_1h = q["percent_change_1h"]
    now = datetime.now(timezone.utc)

    # Check user-set price alerts
    await _check_price_alerts(app, price)

    # Check ±10% spike
    if abs(pct_1h) < SPIKE_THRESHOLD_PCT:
        return
    if _alert_cooldown_until and now < _alert_cooldown_until:
        log.info("Spike %.2f%% detected — cooldown active until %s", pct_1h, _alert_cooldown_until)
        return

    direction = "surged" if pct_1h > 0 else "dropped"
    emoji = "🚀" if pct_1h > 0 else "🔻"
    await broadcast(
        app,
        f"{emoji} <b>TON Price Alert</b>\n"
        f"Toncoin has <b>{direction} {fmt_pct(pct_1h)}</b> in the last hour!\n"
        f"Current price: <b>{fmt_price(price)}</b>",
    )
    _alert_cooldown_until = now + timedelta(hours=ALERT_COOLDOWN_HOURS)
    log.info("Spike alert sent (%.2f%%). Cooldown until %s", pct_1h, _alert_cooldown_until)


async def job_hourly_update(app: Application) -> None:
    global _last_hourly_price
    if not TARGETS:
        return
    try:
        q = await fetch_ton_cmc()
    except Exception as exc:
        log.error("CMC fetch error: %s", exc)
        return

    price = q["price"]
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")

    # Check swing against our own last captured price
    swing_line = ""
    tag_line = ""
    if _last_hourly_price is not None:
        swing_pct = (price - _last_hourly_price) / _last_hourly_price * 100
        swing_line = f"\nHourly swing: {fmt_pct(swing_pct)}"
        if abs(swing_pct) >= HOURLY_SWING_PCT and HOURLY_SWING_TAG:
            tag_line = f"\n{HOURLY_SWING_TAG}"
            log.info("Hourly swing %.2f%% exceeded threshold — tagging %s", swing_pct, HOURLY_SWING_TAG)

    _last_hourly_price = price

    await broadcast(
        app,
        f"⏰ <b>TON Hourly Update</b> — {now}\n"
        f"Price: <b>{fmt_price(price)}</b>\n"
        f"1h change: {fmt_pct(q['percent_change_1h'])}\n"
        f"24h change: {fmt_pct(q['percent_change_24h'])}"
        f"{swing_line}"
        f"{tag_line}",
    )


async def job_daily_summary(app: Application) -> None:
    if not TARGETS:
        return
    try:
        q = await fetch_ton_cmc()
    except Exception as exc:
        log.error("CMC fetch error: %s", exc)
        return
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    await broadcast(
        app,
        f"📊 <b>TON Daily Summary</b> — {today}\n"
        f"Price: <b>{fmt_price(q['price'])}</b>\n"
        f"24h change: {fmt_pct(q['percent_change_24h'])}\n"
        f"7d change: {fmt_pct(q['percent_change_7d'])}\n"
        f"Market cap: ${q['market_cap']:,.0f}",
    )


# ---------------------------------------------------------------------------
# Command handlers (all restricted to CHAT_FILTER except /chatid)
# ---------------------------------------------------------------------------

async def cmd_current(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        q = await fetch_ton_cmc()
    except Exception as exc:
        await update.message.reply_text(f"⚠️ Error: {exc}")
        return
    await update.message.reply_text(
        f"💎 <b>Toncoin (TON)</b>\n"
        f"Price: <b>{fmt_price(q['price'])}</b>\n"
        f"1h: {fmt_pct(q['percent_change_1h'])}\n"
        f"24h: {fmt_pct(q['percent_change_24h'])}\n"
        f"7d: {fmt_pct(q['percent_change_7d'])}\n"
        f"Market cap: ${q['market_cap']:,.0f}",
        parse_mode="HTML",
    )


async def cmd_swing(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        q = await fetch_ton_cmc()
    except Exception as exc:
        await update.message.reply_text(f"⚠️ Error: {exc}")
        return
    price = q["price"]
    pct_24h = q["percent_change_24h"]
    open_price = price / (1 + pct_24h / 100)
    emoji = "📈" if pct_24h >= 0 else "📉"
    await update.message.reply_text(
        f"{emoji} <b>TON 24h Swing</b>\n"
        f"Open (24h ago): {fmt_price(open_price)}\n"
        f"Now: <b>{fmt_price(price)}</b>\n"
        f"Change: <b>{fmt_pct(pct_24h)}</b>",
        parse_mode="HTML",
    )


async def cmd_top1m(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text("⏳ Fetching 30-day high…")
    try:
        high = await fetch_ton_cg_high(30)
    except Exception as exc:
        await msg.edit_text(f"⚠️ Error: {exc}")
        return
    await msg.edit_text(
        f"📅 <b>TON — 30-Day High</b>\n"
        f"Highest price in last 30 days: <b>{fmt_price(high)}</b>",
        parse_mode="HTML",
    )


async def cmd_top1y(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text("⏳ Fetching 1-year high…")
    try:
        high = await fetch_ton_cg_high(365)
    except Exception as exc:
        await msg.edit_text(f"⚠️ Error: {exc}")
        return
    await msg.edit_text(
        f"📆 <b>TON — 1-Year High</b>\n"
        f"Highest price in last 365 days: <b>{fmt_price(high)}</b>",
        parse_mode="HTML",
    )


async def cmd_ath(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text("⏳ Fetching all-time high…")
    try:
        data = await fetch_ton_cg_coin()
        md = data["market_data"]
        ath = md["ath"]["usd"]
        ath_date = fmt_date(md["ath_date"]["usd"])
        pct_from_ath = md["ath_change_percentage"]["usd"]
    except Exception as exc:
        await msg.edit_text(f"⚠️ Error: {exc}")
        return
    await msg.edit_text(
        f"🏆 <b>TON All-Time High</b>\n"
        f"ATH: <b>{fmt_price(ath)}</b>\n"
        f"Date: {ath_date}\n"
        f"Current vs ATH: {fmt_pct(pct_from_ath)}",
        parse_mode="HTML",
    )


async def cmd_alert(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args:
        await update.message.reply_text("Usage: /alert <price>  e.g. /alert 2.88")
        return
    try:
        target = float(ctx.args[0].replace(",", "."))
    except ValueError:
        await update.message.reply_text("⚠️ Invalid price. Example: /alert 2.88")
        return

    try:
        q = await fetch_ton_cmc()
    except Exception as exc:
        await update.message.reply_text(f"⚠️ Could not fetch current price: {exc}")
        return

    current = q["price"]
    if abs(current - target) < 0.00001:
        await update.message.reply_text("⚠️ Target price is the same as the current price.")
        return

    direction = "above" if target > current else "below"

    # Replace any existing alert at the same target price
    _price_alerts[:] = [a for a in _price_alerts if a.target != target]
    _price_alerts.append(PriceAlert(target=target, direction=direction))
    _save_alerts(_price_alerts)

    label = "rises to" if direction == "above" else "drops to"
    await update.message.reply_text(
        f"🎯 Alert set: notify when TON {label} <b>{fmt_price(target)}</b>\n"
        f"Current price: {fmt_price(current)}",
        parse_mode="HTML",
    )


async def cmd_alerts(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _price_alerts:
        await update.message.reply_text("No active price alerts. Set one with /alert <price>")
        return
    lines = []
    for i, a in enumerate(_price_alerts, 1):
        label = "≥" if a.direction == "above" else "≤"
        lines.append(f"{i}. {label} <b>{fmt_price(a.target)}</b>")
    await update.message.reply_text(
        f"🎯 <b>Active price alerts ({len(_price_alerts)})</b>\n"
        + "\n".join(lines)
        + "\n\nRemove one with /delalert <price>",
        parse_mode="HTML",
    )


async def cmd_delalert(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args:
        await update.message.reply_text("Usage: /delalert <price>  e.g. /delalert 2.88")
        return
    try:
        target = float(ctx.args[0].replace(",", "."))
    except ValueError:
        await update.message.reply_text("⚠️ Invalid price. Example: /delalert 2.88")
        return

    before = len(_price_alerts)
    _price_alerts[:] = [a for a in _price_alerts if a.target != target]
    if len(_price_alerts) == before:
        await update.message.reply_text(f"No alert found at {fmt_price(target)}.")
        return

    _save_alerts(_price_alerts)
    await update.message.reply_text(f"✅ Alert at {fmt_price(target)} removed.")


ABOUT_TEXT = (
    "🤖 <b>Toncoin (TON) Alert Bot</b>\n"
    "Monitors Toncoin via CoinMarketCap and posts updates to this channel.\n\n"
    "<b>Price commands</b>\n"
    "/current — current price with 1h, 24h and 7d change\n"
    "/swing — 24h price swing: where it opened vs now\n"
    "/top1m — highest TON price in the last 30 days\n"
    "/top1y — highest TON price in the last 365 days\n"
    "/ath — all-time high price and the date it hit\n\n"
    "<b>Price alerts</b>\n"
    "/alert 2.88 — notify when TON reaches $2.88 (direction auto-detected)\n"
    "/alerts — list all active price alerts\n"
    "/delalert 2.88 — remove the alert at $2.88\n\n"
    "<b>Other</b>\n"
    "/about — show this message\n"
    "/chatid — show this chat's ID and topic thread ID\n\n"
    "<b>Automatic updates</b>\n"
    "• Hourly price update — tags you if price swung more than the configured % since last hour\n"
    "• Daily summary every midnight UTC\n"
    "• Spike alert if TON moves ±10% within 1 hour (2h cooldown)\n"
    "• Price alert check every 5 minutes\n\n"
    "Data: CoinMarketCap (real-time) · CoinGecko (historical)"
)


async def cmd_about(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(ABOUT_TEXT, parse_mode="HTML")


async def cmd_chatid(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Unrestricted — useful during initial setup to find a chat/topic ID."""
    chat = update.effective_chat
    thread_id = update.message.message_thread_id
    target_value = f"{chat.id}:{thread_id}" if thread_id else str(chat.id)
    await update.message.reply_text(
        f"<b>Chat info</b>\n"
        f"ID: <code>{chat.id}</code>\n"
        f"Type: {chat.type}\n"
        f"Title: {chat.title or '—'}\n"
        + (f"Topic thread ID: <code>{thread_id}</code>\n" if thread_id else "")
        + f"\nSet in <code>TARGET_CHAT_ID</code>:\n<code>{target_value}</code>",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(startup_checks)
        .build()
    )

    # Channel-only commands
    for name, handler in [
        ("current", cmd_current),
        ("swing", cmd_swing),
        ("top1m", cmd_top1m),
        ("top1y", cmd_top1y),
        ("ath", cmd_ath),
        ("alert", cmd_alert),
        ("alerts", cmd_alerts),
        ("delalert", cmd_delalert),
        ("about", cmd_about),
    ]:
        app.add_handler(CommandHandler(name, handler, filters=CHAT_FILTER))

    # Setup helper — works everywhere
    app.add_handler(CommandHandler("chatid", cmd_chatid))

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(job_spike_check, "interval", minutes=5, args=[app])
    scheduler.add_job(job_hourly_update, "cron", minute=0, args=[app])
    scheduler.add_job(job_daily_summary, "cron", hour=0, minute=0, args=[app])
    scheduler.start()

    log.info("Bot started. Targets: %s", TARGETS or "none configured")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
