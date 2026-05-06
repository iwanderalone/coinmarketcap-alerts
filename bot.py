import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CMC_API_KEY = os.environ["CMC_API_KEY"]
# Optional fixed target — supports plain CHAT_ID or CHAT_ID:THREAD_ID for topics
_target_raw = os.environ.get("TARGET_CHAT_ID", "").strip()
if _target_raw:
    _parts = _target_raw.split(":", 1)
    TARGET_CHAT_ID: int | None = int(_parts[0])
    TARGET_THREAD_ID: int | None = int(_parts[1]) if len(_parts) == 2 else None
else:
    TARGET_CHAT_ID = None
    TARGET_THREAD_ID = None

CMC_URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
TON_ID = 11419  # CoinMarketCap ID for Toncoin
SPIKE_THRESHOLD_PCT = 10.0
ALERT_COOLDOWN_HOURS = 2

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# --- shared state ---
subscribers: set[int] = set()
_alert_cooldown_until: datetime | None = None  # suppresses repeat spike alerts


async def fetch_ton() -> dict:
    """Fetch the current USD quote for Toncoin from CoinMarketCap."""
    headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY, "Accept": "application/json"}
    params = {"id": TON_ID, "convert": "USD"}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(CMC_URL, headers=headers, params=params)
        resp.raise_for_status()
    return resp.json()["data"][str(TON_ID)]["quote"]["USD"]


def fmt_price(price: float) -> str:
    return f"${price:,.4f}"


def fmt_pct(pct: float) -> str:
    arrow = "▲" if pct >= 0 else "▼"
    return f"{arrow} {abs(pct):.2f}%"


async def broadcast(app: Application, text: str) -> None:
    # Fixed target — may include a topic thread ID
    if TARGET_CHAT_ID is not None:
        try:
            await app.bot.send_message(
                chat_id=TARGET_CHAT_ID,
                message_thread_id=TARGET_THREAD_ID,
                text=text,
                parse_mode="HTML",
            )
        except Exception as exc:
            log.warning("Failed to send to target %s (thread %s): %s", TARGET_CHAT_ID, TARGET_THREAD_ID, exc)

    # Individual /start subscribers — no thread context
    for chat_id in list(subscribers):
        if chat_id == TARGET_CHAT_ID:
            continue  # already sent above
        try:
            await app.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
        except Exception as exc:
            log.warning("Failed to send to %s: %s", chat_id, exc)


# --- scheduled jobs ---

async def job_spike_check(app: Application) -> None:
    """Runs every 5 minutes. Alerts if 1h price change exceeds ±10%."""
    global _alert_cooldown_until

    if not subscribers and TARGET_CHAT_ID is None:
        return

    try:
        q = await fetch_ton()
    except Exception as exc:
        log.error("CMC fetch error: %s", exc)
        return

    pct_1h = q["percent_change_1h"]
    now = datetime.now(timezone.utc)

    if abs(pct_1h) < SPIKE_THRESHOLD_PCT:
        return

    if _alert_cooldown_until and now < _alert_cooldown_until:
        log.info("Spike detected (%.2f%%) but cooldown active until %s", pct_1h, _alert_cooldown_until)
        return

    direction = "surged" if pct_1h > 0 else "dropped"
    emoji = "🚀" if pct_1h > 0 else "🔻"
    await broadcast(
        app,
        f"{emoji} <b>TON Price Alert</b>\n"
        f"Toncoin has <b>{direction} {fmt_pct(pct_1h)}</b> in the last hour!\n"
        f"Current price: <b>{fmt_price(q['price'])}</b>",
    )

    _alert_cooldown_until = now + timedelta(hours=ALERT_COOLDOWN_HOURS)
    log.info("Spike alert sent (%.2f%%). Next alert allowed after %s", pct_1h, _alert_cooldown_until)


async def job_hourly_update(app: Application) -> None:
    """Sends a price update every hour."""
    if not subscribers and TARGET_CHAT_ID is None:
        return

    try:
        q = await fetch_ton()
    except Exception as exc:
        log.error("CMC fetch error: %s", exc)
        return

    now = datetime.now(timezone.utc).strftime("%H:%M UTC")
    await broadcast(
        app,
        f"⏰ <b>TON Hourly Update</b> — {now}\n"
        f"Price: <b>{fmt_price(q['price'])}</b>\n"
        f"1h change: {fmt_pct(q['percent_change_1h'])}\n"
        f"24h change: {fmt_pct(q['percent_change_24h'])}",
    )


async def job_daily_summary(app: Application) -> None:
    """Sends a full daily summary at midnight UTC."""
    if not subscribers and TARGET_CHAT_ID is None:
        return

    try:
        q = await fetch_ton()
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


# --- command handlers ---

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    subscribers.add(update.effective_chat.id)
    await update.message.reply_text(
        "✅ <b>Subscribed to Toncoin (TON) alerts!</b>\n\n"
        "You'll receive:\n"
        "• Hourly price updates (top of each hour)\n"
        "• Daily summary at midnight UTC\n"
        "• Instant alert if TON moves ±10% within an hour\n\n"
        "Commands:\n"
        "/price — current price snapshot\n"
        "/status — subscription status\n"
        "/stop — unsubscribe",
        parse_mode="HTML",
    )


async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    subscribers.discard(update.effective_chat.id)
    await update.message.reply_text("❌ Unsubscribed. Send /start to resubscribe.")


async def cmd_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        q = await fetch_ton()
    except Exception as exc:
        await update.message.reply_text(f"⚠️ Failed to fetch price: {exc}")
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


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    subbed = update.effective_chat.id in subscribers
    if TARGET_CHAT_ID and TARGET_THREAD_ID:
        target_line = f"Fixed target: <code>{TARGET_CHAT_ID}</code> (topic <code>{TARGET_THREAD_ID}</code>)"
    elif TARGET_CHAT_ID:
        target_line = f"Fixed target: <code>{TARGET_CHAT_ID}</code>"
    else:
        target_line = "No fixed target set"
    await update.message.reply_text(
        f"{'✅ You are subscribed.' if subbed else '❌ Not subscribed — send /start.'}\n"
        f"Individual subscribers: {len(subscribers)}\n"
        f"{target_line}",
        parse_mode="HTML",
    )


async def cmd_chatid(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    thread_id = update.message.message_thread_id
    target_value = f"{chat.id}:{thread_id}" if thread_id else str(chat.id)
    await update.message.reply_text(
        f"<b>Chat info</b>\n"
        f"ID: <code>{chat.id}</code>\n"
        f"Type: {chat.type}\n"
        f"Title: {chat.title or '—'}\n"
        + (f"Topic thread ID: <code>{thread_id}</code>\n" if thread_id else "")
        + f"\nUse in <code>TARGET_CHAT_ID</code>:\n<code>{target_value}</code>",
        parse_mode="HTML",
    )


# --- entrypoint ---

def main() -> None:
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("price", cmd_price))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("chatid", cmd_chatid))

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(job_spike_check, "interval", minutes=5, args=[app])
    scheduler.add_job(job_hourly_update, "cron", minute=0, args=[app])
    scheduler.add_job(job_daily_summary, "cron", hour=0, minute=0, args=[app])
    scheduler.start()

    log.info("Bot started.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
