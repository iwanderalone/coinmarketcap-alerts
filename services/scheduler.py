import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Tuple
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.ext import Application

from config import config
from database import db
from providers.stocks import fetch_market_status
from services.market_service import fetch_quote

log = logging.getLogger(__name__)

# Cooldown tracking: (chat_id, symbol) -> cooldown_until datetime
_spike_cooldowns: Dict[Tuple[int, str], datetime] = {}


def fmt_price(price: float) -> str:
    if price < 0.01:
        return f"${price:,.6f}"
    elif price < 1.0:
        return f"${price:,.4f}"
    else:
        return f"${price:,.2f}"


def fmt_pct(pct: float) -> str:
    arrow = "▲" if pct >= 0 else "▼"
    return f"{arrow} {abs(pct):.2f}%"


async def broadcast_to_targets(app: Application, text: str, target_chat_id: int = None) -> None:
    targets = config.targets
    for chat_id, thread_id in targets:
        if target_chat_id is not None and chat_id != target_chat_id:
            continue
        try:
            await app.bot.send_message(
                chat_id=chat_id,
                message_thread_id=thread_id,
                text=text,
                parse_mode="HTML",
            )
        except Exception as exc:
            log.warning("Broadcast failed → chat %s thread %s: %s", chat_id, thread_id, exc)


# ---------------------------------------------------------------------------
# Background Jobs
# ---------------------------------------------------------------------------

async def job_check_price_alerts_and_spikes(app: Application) -> None:
    """Check target price alerts and drastic price spikes across watchlists."""
    global _spike_cooldowns
    now = datetime.now(timezone.utc)

    # 1. Process custom target price alerts
    alerts = await db.get_all_alerts()
    for alert in alerts:
        try:
            quote = await fetch_quote(alert.symbol, alert.asset_type)
            triggered = False
            if alert.direction == "above" and quote.price >= alert.target_price:
                triggered = True
            elif alert.direction == "below" and quote.price <= alert.target_price:
                triggered = True

            if triggered:
                await db.remove_alert(alert.id)
                emoji = "🎯"
                direction_label = "reached" if alert.direction == "above" else "dropped to"
                tag = f"\n{config.hourly_swing_tag}" if config.hourly_swing_tag else ""
                
                text = (
                    f"{emoji} <b>{quote.name} ({quote.symbol}) Price Alert</b>\n"
                    f"Target {direction_label}: <b>{fmt_price(alert.target_price)}</b>\n"
                    f"Current price: <b>{fmt_price(quote.price)}</b>"
                    f"{tag}"
                )
                await broadcast_to_targets(app, text, target_chat_id=alert.chat_id)
                log.info("Alert fired for %s in chat %s", alert.symbol, alert.chat_id)
        except Exception as exc:
            log.warning("Failed to evaluate price alert for %s: %s", alert.symbol, exc)

    # 2. Process drastic movement spikes for watchlisted symbols
    watch_items = await db.get_all_watchlists()
    for item in watch_items:
        cooldown_key = (item.chat_id, item.symbol)
        cooldown_until = _spike_cooldowns.get(cooldown_key)
        if cooldown_until and now < cooldown_until:
            continue

        try:
            quote = await fetch_quote(item.symbol, item.asset_type)
            change = quote.change_24h_pct
            
            # Use lower threshold for stocks (5%) vs crypto (10%)
            threshold = config.spike_threshold_pct if item.asset_type == "stock" else max(config.spike_threshold_pct, 8.0)
            
            if abs(change) >= threshold:
                direction = "surged" if change > 0 else "dropped"
                emoji = "🚀" if change > 0 else "🔻"
                tag = f"\n{config.hourly_swing_tag}" if config.hourly_swing_tag else ""
                
                text = (
                    f"{emoji} <b>Drastic Movement Alert: {quote.symbol}</b>\n"
                    f"<b>{quote.name}</b> has <b>{direction} {fmt_pct(change)}</b> in the last 24h!\n"
                    f"Current Price: <b>{fmt_price(quote.price)}</b>"
                    f"{tag}"
                )
                await broadcast_to_targets(app, text, target_chat_id=item.chat_id)
                _spike_cooldowns[cooldown_key] = now + timedelta(hours=config.spike_cooldown_hours)
                log.info("Spike alert sent for %s (%.2f%%)", item.symbol, change)
        except Exception as exc:
            log.warning("Failed spike check for %s: %s", item.symbol, exc)


async def job_market_open_alert(app: Application) -> None:
    """Notify when US Stock Market opens (Mon-Fri 09:30 ET)."""
    try:
        status = await fetch_market_status()
        text = (
            f"🔔 <b>US Stock Market Open!</b>\n"
            f"NYSE & NASDAQ are now open for trading.\n"
        )
        if status.sp500_price:
            text += f"• S&P 500: <b>{status.sp500_price:,.2f}</b> ({fmt_pct(status.sp500_change_pct or 0.0)})\n"
        if status.nasdaq_price:
            text += f"• Nasdaq: <b>{status.nasdaq_price:,.2f}</b> ({fmt_pct(status.nasdaq_change_pct or 0.0)})\n"
        
        await broadcast_to_targets(app, text)
        log.info("US Market Open alert broadcasted.")
    except Exception as exc:
        log.error("Failed US market open alert: %s", exc)


async def job_market_close_alert(app: Application) -> None:
    """Notify when US Stock Market closes (Mon-Fri 16:00 ET)."""
    try:
        status = await fetch_market_status()
        text = (
            f"🔔 <b>US Stock Market Closed!</b>\n"
            f"Trading has concluded for today.\n"
        )
        if status.sp500_price:
            text += f"• S&P 500: <b>{status.sp500_price:,.2f}</b> ({fmt_pct(status.sp500_change_pct or 0.0)})\n"
        if status.nasdaq_price:
            text += f"• Nasdaq: <b>{status.nasdaq_price:,.2f}</b> ({fmt_pct(status.nasdaq_change_pct or 0.0)})\n"

        await broadcast_to_targets(app, text)
        log.info("US Market Close alert broadcasted.")
    except Exception as exc:
        log.error("Failed US market close alert: %s", exc)


async def job_daily_summary(app: Application) -> None:
    """Daily watchlist movement summary."""
    for chat_id, thread_id in config.targets:
        try:
            items = await db.get_watchlist(chat_id)
            if not items:
                continue

            lines = []
            for item in items:
                try:
                    q = await fetch_quote(item.symbol, item.asset_type)
                    icon = "📈" if q.change_24h_pct >= 0 else "📉"
                    lines.append(f"{icon} <b>{q.symbol}</b> ({q.asset_type.upper()}): {fmt_price(q.price)} ({fmt_pct(q.change_24h_pct)})")
                except Exception as exc:
                    lines.append(f"⚠️ <b>{item.symbol}</b>: Error fetching data ({exc})")

            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            text = (
                f"📊 <b>Daily Investor Digest</b> — {today}\n"
                f"Here is the daily performance for your watchlist:\n\n"
                + "\n".join(lines)
            )
            await app.bot.send_message(chat_id=chat_id, message_thread_id=thread_id, text=text, parse_mode="HTML")
        except Exception as exc:
            log.error("Failed to send daily summary for chat %s: %s", chat_id, exc)


# ---------------------------------------------------------------------------
# Setup Scheduler
# ---------------------------------------------------------------------------

def start_scheduler(app: Application) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")

    # 1. Periodic spike and price alerts check every 5 mins
    scheduler.add_job(job_check_price_alerts_and_spikes, "interval", minutes=5, args=[app])

    # 2. Daily summary digest at midnight UTC
    scheduler.add_job(job_daily_summary, "cron", hour=0, minute=0, args=[app])

    # 3. US Market Open (Mon-Fri 09:30 ET)
    scheduler.add_job(
        job_market_open_alert,
        "cron",
        day_of_week="mon-fri",
        hour=9,
        minute=30,
        timezone="America/New_York",
        args=[app],
    )

    # 4. US Market Close (Mon-Fri 16:00 ET)
    scheduler.add_job(
        job_market_close_alert,
        "cron",
        day_of_week="mon-fri",
        hour=16,
        minute=0,
        timezone="America/New_York",
        args=[app],
    )

    scheduler.start()
    log.info("AsyncIOScheduler started with US Market Open/Close and Watchlist jobs.")
    return scheduler
