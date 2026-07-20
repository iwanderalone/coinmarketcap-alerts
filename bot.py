import logging
import sys
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, filters

from config import config
from database import db
from providers.ai_research import generate_ai_research
from providers.news import fetch_news_items, generate_ai_news_summary
from providers.stocks import fetch_market_status
from services.market_service import fetch_quote
from services.scheduler import fmt_pct, fmt_price, start_scheduler

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# Configure chat filter
if config.target_chat_ids:
    CHAT_FILTER = filters.Chat(chat_id=list(config.target_chat_ids))
else:
    CHAT_FILTER = ~filters.ChatType.PRIVATE


def get_quote_keyboard(symbol: str, asset_type: str) -> InlineKeyboardMarkup:
    """Generate interactive buttons for quote response."""
    keyboard = [
        [
            InlineKeyboardButton("🤖 AI Research", callback_data=f"res:{symbol}"),
            InlineKeyboardButton("📰 News", callback_data=f"news:{asset_type}:{symbol}"),
        ],
        [
            InlineKeyboardButton("⭐ +Favorite", callback_data=f"fav:{asset_type}:{symbol}"),
            InlineKeyboardButton("🎯 +Alert", callback_data=f"alert_prompt:{symbol}"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


# ---------------------------------------------------------------------------
# Command Handlers
# ---------------------------------------------------------------------------

async def cmd_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Get price quote for any crypto or stock symbol. Usage: /p AAPL or /p BTC"""
    if not ctx.args:
        await update.message.reply_text("Usage: /p <symbol>  (e.g., /p AAPL or /p BTC)")
        return

    symbol = ctx.args[0].strip().upper()
    try:
        quote = await fetch_quote(symbol)
        mcap_str = f"${quote.market_cap:,.0f}" if quote.market_cap else "N/A"
        vol_str = f"${quote.volume_24h:,.0f}" if quote.volume_24h else "N/A"

        text = (
            f"📊 <b>{quote.name} ({quote.symbol})</b> [{quote.asset_type.upper()}]\n"
            f"Price: <b>{fmt_price(quote.price)}</b>\n"
            f"24h Change: <b>{fmt_pct(quote.change_24h_pct)}</b>\n"
            f"Market Cap: {mcap_str}\n"
            f"24h Volume: {vol_str}"
        )
        if quote.pe_ratio:
            text += f"\nP/E Ratio: {quote.pe_ratio:.2f}"
        if quote.fifty_two_week_high and quote.fifty_two_week_low:
            text += f"\n52w Range: {fmt_price(quote.fifty_two_week_low)} - {fmt_price(quote.fifty_two_week_high)}"

        reply_markup = get_quote_keyboard(quote.symbol, quote.asset_type)
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
    except Exception as exc:
        await update.message.reply_text(f"⚠️ Could not fetch quote for '{symbol}': {exc}")


async def cmd_fav(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Manage favorite assets. Usage: /fav add <stock|crypto> <symbol>, /fav del <symbol>, /fav list"""
    chat_id = update.effective_chat.id
    if not ctx.args:
        await update.message.reply_text(
            "<b>Favorites Commands:</b>\n"
            "• <code>/fav add stock AAPL</code> — Add US stock\n"
            "• <code>/fav add crypto BTC</code> — Add crypto\n"
            "• <code>/fav del AAPL</code> — Remove item\n"
            "• <code>/fav list</code> — View favorites",
            parse_mode="HTML",
        )
        return

    subcmd = ctx.args[0].lower()

    if subcmd == "add":
        if len(ctx.args) < 3:
            await update.message.reply_text("Usage: /fav add <stock|crypto> <symbol>")
            return
        asset_type = ctx.args[1].lower()
        symbol = ctx.args[2].strip().upper()

        if asset_type not in ("stock", "crypto"):
            await update.message.reply_text("⚠️ Asset type must be 'stock' or 'crypto'.")
            return

        try:
            quote = await fetch_quote(symbol, asset_type=asset_type)
        except Exception as exc:
            await update.message.reply_text(f"⚠️ Invalid symbol or data fetch error: {exc}")
            return

        success = await db.add_to_watchlist(chat_id, quote.symbol, asset_type)
        if success:
            await update.message.reply_text(
                f"✅ Added <b>{quote.name} ({quote.symbol})</b> [{asset_type.upper()}] to your favorites!",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(f"⚠️ <b>{quote.symbol}</b> is already in your favorites.", parse_mode="HTML")

    elif subcmd in ("del", "remove", "delete"):
        if len(ctx.args) < 2:
            await update.message.reply_text("Usage: /fav del <symbol>")
            return
        symbol = ctx.args[1].strip().upper()
        removed = await db.remove_from_watchlist(chat_id, symbol)
        if removed:
            await update.message.reply_text(f"✅ Removed <b>{symbol}</b> from favorites.", parse_mode="HTML")
        else:
            await update.message.reply_text(f"⚠️ Symbol <b>{symbol}</b> was not found in your favorites.", parse_mode="HTML")

    elif subcmd == "list":
        items = await db.get_watchlist(chat_id)
        if not items:
            await update.message.reply_text("Your favorites list is currently empty. Add items with <code>/fav add <stock|crypto> <symbol></code>", parse_mode="HTML")
            return
        lines = []
        for i, item in enumerate(items, 1):
            lines.append(f"{i}. <b>{item.symbol}</b> [{item.asset_type.upper()}]")
        await update.message.reply_text("⭐ <b>Your Favorites Watchlist:</b>\n" + "\n".join(lines), parse_mode="HTML")
    else:
        await update.message.reply_text("Unknown subcommand. Use <code>/fav</code> for usage info.", parse_mode="HTML")


async def cmd_watchlist(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show prices and 24h performance for all items in chat's favorites."""
    chat_id = update.effective_chat.id
    items = await db.get_watchlist(chat_id)
    if not items:
        await update.message.reply_text("No favorites set yet. Add with <code>/fav add stock AAPL</code> or <code>/fav add crypto BTC</code>.", parse_mode="HTML")
        return

    msg = await update.message.reply_text("⏳ Fetching live watchlist performance…")
    lines = []
    for item in items:
        try:
            q = await fetch_quote(item.symbol, item.asset_type)
            icon = "📈" if q.change_24h_pct >= 0 else "📉"
            lines.append(f"{icon} <b>{q.symbol}</b> ({q.asset_type.upper()}): <b>{fmt_price(q.price)}</b> ({fmt_pct(q.change_24h_pct)})")
        except Exception as exc:
            lines.append(f"⚠️ <b>{item.symbol}</b>: Fetch error ({exc})")

    await msg.edit_text("⭐ <b>Live Watchlist Summary:</b>\n\n" + "\n".join(lines), parse_mode="HTML")


async def cmd_portfolio(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Manage investor portfolio holdings. Usage: /port or /port add <stock|crypto> <symbol> <qty> <buy_price>"""
    chat_id = update.effective_chat.id

    if ctx.args:
        subcmd = ctx.args[0].lower()
        if subcmd == "add":
            if len(ctx.args) < 5:
                await update.message.reply_text("Usage: /port add <stock|crypto> <symbol> <quantity> <buy_price>\nExample: /port add stock AAPL 10 210.50")
                return
            asset_type = ctx.args[1].lower()
            symbol = ctx.args[2].strip().upper()
            try:
                qty = float(ctx.args[3].replace(",", "."))
                buy_price = float(ctx.args[4].replace(",", "."))
            except ValueError:
                await update.message.reply_text("⚠️ Invalid quantity or buy price.")
                return

            if asset_type not in ("stock", "crypto"):
                await update.message.reply_text("⚠️ Asset type must be 'stock' or 'crypto'.")
                return

            try:
                q = await fetch_quote(symbol, asset_type)
                symbol = q.symbol
            except Exception as exc:
                await update.message.reply_text(f"⚠️ Could not verify symbol '{symbol}': {exc}")
                return

            await db.add_portfolio_item(chat_id, symbol, asset_type, qty, buy_price)
            await update.message.reply_text(
                f"💼 Portfolio Updated!\nAdded <b>{qty} {symbol}</b> @ <b>{fmt_price(buy_price)}</b>",
                parse_mode="HTML",
            )
            return

        elif subcmd in ("del", "remove"):
            if len(ctx.args) < 2:
                await update.message.reply_text("Usage: /port del <symbol>")
                return
            symbol = ctx.args[1].strip().upper()
            removed = await db.remove_portfolio_item(chat_id, symbol)
            if removed:
                await update.message.reply_text(f"✅ Removed <b>{symbol}</b> from portfolio.", parse_mode="HTML")
            else:
                await update.message.reply_text(f"⚠️ <b>{symbol}</b> not found in portfolio.", parse_mode="HTML")
            return

    # View Portfolio
    items = await db.get_portfolio(chat_id)
    if not items:
        await update.message.reply_text(
            "💼 <b>Your Portfolio is empty.</b>\n"
            "Add positions using:\n"
            "<code>/port add stock AAPL 10 210.50</code>\n"
            "<code>/port add crypto BTC 0.5 65000</code>",
            parse_mode="HTML",
        )
        return

    msg = await update.message.reply_text("⏳ Calculating portfolio performance & PnL…")
    total_val = 0.0
    total_cost = 0.0
    lines = []

    for item in items:
        try:
            q = await fetch_quote(item.symbol, item.asset_type)
            curr_val = q.price * item.quantity
            cost_basis = item.buy_price * item.quantity
            pnl = curr_val - cost_basis
            pnl_pct = ((curr_val - cost_basis) / cost_basis * 100) if cost_basis > 0 else 0.0

            total_val += curr_val
            total_cost += cost_basis

            pnl_icon = "🟢" if pnl >= 0 else "🔴"
            lines.append(
                f"{pnl_icon} <b>{item.symbol}</b> ({item.quantity:g} shares/coins)\n"
                f"   Value: <b>{fmt_price(curr_val)}</b> | PnL: <b>{pnl:+.2f} ({pnl_pct:+.2f}%)</b>\n"
                f"   Avg Cost: {fmt_price(item.buy_price)} → Now: {fmt_price(q.price)}"
            )
        except Exception as exc:
            lines.append(f"⚠️ <b>{item.symbol}</b>: Error fetching data ({exc})")

    total_pnl = total_val - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0.0
    overall_icon = "🟢" if total_pnl >= 0 else "🔴"

    header = (
        f"💼 <b>Investor Portfolio Summary</b>\n"
        f"Total Value: <b>{fmt_price(total_val)}</b>\n"
        f"Total Cost: {fmt_price(total_cost)}\n"
        f"Total PnL: {overall_icon} <b>${total_pnl:+,.2f} ({total_pnl_pct:+.2f}%)</b>\n\n"
        f"<b>Holdings Breakdown:</b>\n"
    )

    await msg.edit_text(header + "\n\n".join(lines), parse_mode="HTML")


async def cmd_news(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Fetch financial news & AI summary. Usage: /news <symbol>"""
    if not ctx.args:
        await update.message.reply_text("Usage: /news <symbol>  (e.g., /news AAPL or /news BTC)")
        return

    symbol = ctx.args[0].strip().upper()
    msg = await update.message.reply_text(f"📰 <i>Fetching news & generating AI summary for <b>{symbol}</b>…</i>", parse_mode="HTML")

    try:
        quote = await fetch_quote(symbol)
        news_items = await fetch_news_items(quote.symbol, quote.asset_type)
        summary = await generate_ai_news_summary(quote, news_items)
        await msg.edit_text(summary, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as exc:
        await msg.edit_text(f"⚠️ Error fetching news for {symbol}: {exc}")


async def cmd_alert(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Set price alert. Usage: /alert <symbol> <price>"""
    if len(ctx.args) < 2:
        await update.message.reply_text("Usage: /alert <symbol> <target_price>  (e.g., /alert AAPL 230 or /alert BTC 95000)")
        return

    symbol = ctx.args[0].strip().upper()
    try:
        target_price = float(ctx.args[1].replace(",", "."))
    except ValueError:
        await update.message.reply_text("⚠️ Invalid price. Example: /alert AAPL 230")
        return

    try:
        quote = await fetch_quote(symbol)
    except Exception as exc:
        await update.message.reply_text(f"⚠️ Could not fetch current price for {symbol}: {exc}")
        return

    current = quote.price
    if abs(current - target_price) < 0.0001:
        await update.message.reply_text("⚠️ Target price is the same as current price.")
        return

    direction = "above" if target_price > current else "below"
    chat_id = update.effective_chat.id
    
    await db.add_alert(
        chat_id=chat_id,
        symbol=quote.symbol,
        asset_type=quote.asset_type,
        target_price=target_price,
        direction=direction,
    )

    label = "rises to" if direction == "above" else "drops to"
    await update.message.reply_text(
        f"🎯 Alert set: notify when <b>{quote.symbol}</b> {label} <b>{fmt_price(target_price)}</b>\n"
        f"Current price: {fmt_price(current)}",
        parse_mode="HTML",
    )


async def cmd_alerts(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """List active price alerts."""
    chat_id = update.effective_chat.id
    alerts = await db.get_alerts_for_chat(chat_id)
    if not alerts:
        await update.message.reply_text("No active price alerts. Set one with /alert <symbol> <price>")
        return

    lines = []
    for i, a in enumerate(alerts, 1):
        label = "≥" if a.direction == "above" else "≤"
        lines.append(f"{i}. <b>{a.symbol}</b> [{a.asset_type.upper()}] {label} <b>{fmt_price(a.target_price)}</b>")

    await update.message.reply_text(
        f"🎯 <b>Active Price Alerts ({len(alerts)})</b>\n"
        + "\n".join(lines)
        + "\n\nRemove with /delalert <symbol> <price>",
        parse_mode="HTML",
    )


async def cmd_delalert(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove price alert. Usage: /delalert <symbol> <price>"""
    if len(ctx.args) < 2:
        await update.message.reply_text("Usage: /delalert <symbol> <price>")
        return

    symbol = ctx.args[0].strip().upper()
    try:
        target_price = float(ctx.args[1].replace(",", "."))
    except ValueError:
        await update.message.reply_text("⚠️ Invalid price.")
        return

    chat_id = update.effective_chat.id
    removed = await db.remove_alert_by_target(chat_id, symbol, target_price)
    if removed:
        await update.message.reply_text(f"✅ Alert for <b>{symbol}</b> at {fmt_price(target_price)} removed.", parse_mode="HTML")
    else:
        await update.message.reply_text(f"No alert found for <b>{symbol}</b> at {fmt_price(target_price)}.", parse_mode="HTML")


async def cmd_research(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate AI research briefing for a stock or crypto. Usage: /research <symbol>"""
    if not ctx.args:
        await update.message.reply_text("Usage: /research <symbol>  (e.g., /research NVDA or /research ETH)")
        return

    symbol = ctx.args[0].strip().upper()
    msg = await update.message.reply_text(f"🤖 <i>Generating AI Research Briefing for <b>{symbol}</b>…</i>", parse_mode="HTML")

    try:
        quote = await fetch_quote(symbol)
        research_text = await generate_ai_research(quote)
        await msg.edit_text(research_text, parse_mode="HTML")
    except Exception as exc:
        await msg.edit_text(f"⚠️ Error generating AI research: {exc}")


async def cmd_market(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Check US stock market status (Open/Closed) and major index trends."""
    try:
        status = await fetch_market_status()
        status_emoji = "🟢" if status.is_open else "🔴"
        
        sp_line = f"• S&P 500: <b>{status.sp500_price:,.2f}</b> ({fmt_pct(status.sp500_change_pct or 0.0)})" if status.sp500_price else ""
        nasdaq_line = f"• Nasdaq: <b>{status.nasdaq_price:,.2f}</b> ({fmt_pct(status.nasdaq_change_pct or 0.0)})" if status.nasdaq_price else ""
        
        text = (
            f"🏛️ <b>US Stock Market Status</b>\n"
            f"Status: {status_emoji} <b>{status.status_text}</b>\n\n"
            f"<b>Major Indices:</b>\n"
            f"{sp_line}\n"
            f"{nasdaq_line}"
        )
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception as exc:
        await update.message.reply_text(f"⚠️ Error checking market status: {exc}")


async def cmd_daily(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Trigger daily performance digest on demand."""
    from services.scheduler import job_daily_summary
    await update.message.reply_text("⏳ Generating daily digest…")
    await job_daily_summary(ctx.application)


async def handle_callback_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle interactive button clicks."""
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = update.effective_chat.id

    parts = data.split(":")
    action = parts[0]

    if action == "res":
        symbol = parts[1]
        await query.message.reply_text(f"🤖 <i>Generating AI Research Briefing for <b>{symbol}</b>…</i>", parse_mode="HTML")
        try:
            quote = await fetch_quote(symbol)
            research_text = await generate_ai_research(quote)
            await query.message.reply_text(research_text, parse_mode="HTML")
        except Exception as exc:
            await query.message.reply_text(f"⚠️ Error generating research for {symbol}: {exc}")

    elif action == "news":
        asset_type = parts[1]
        symbol = parts[2]
        await query.message.reply_text(f"📰 <i>Fetching news for <b>{symbol}</b>…</i>", parse_mode="HTML")
        try:
            quote = await fetch_quote(symbol, asset_type)
            news_items = await fetch_news_items(symbol, asset_type)
            summary = await generate_ai_news_summary(quote, news_items)
            await query.message.reply_text(summary, parse_mode="HTML", disable_web_page_preview=True)
        except Exception as exc:
            await query.message.reply_text(f"⚠️ Error fetching news for {symbol}: {exc}")

    elif action == "fav":
        asset_type = parts[1]
        symbol = parts[2]
        added = await db.add_to_watchlist(chat_id, symbol, asset_type)
        if added:
            await query.message.reply_text(f"⭐ Added <b>{symbol}</b> [{asset_type.upper()}] to your favorites!", parse_mode="HTML")
        else:
            await query.message.reply_text(f"⚠️ <b>{symbol}</b> is already in your favorites.", parse_mode="HTML")

    elif action == "alert_prompt":
        symbol = parts[1]
        await query.message.reply_text(
            f"🎯 <b>Set Alert for {symbol}</b>\n"
            f"Type: <code>/alert {symbol} &lt;target_price&gt;</code>\n"
            f"Example: <code>/alert {symbol} 250</code>",
            parse_mode="HTML",
        )


async def cmd_about(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show bot features and user command guide."""
    about_text = (
        "🤖 <b>Investor Multi-Asset Telegram Bot</b>\n"
        "Monitors US Stocks and Cryptocurrencies with Portfolio Tracking, News Intelligence, and AI Research.\n\n"
        "<b>Market & Prices</b>\n"
        "• <code>/p AAPL</code> or <code>/p BTC</code> — Live price quote with interactive buttons\n"
        "• <code>/market</code> — US stock market status & indices\n"
        "• <code>/watchlist</code> — Live updates for your favorites\n"
        "• <code>/news NVDA</code> — Financial news & AI news summary\n\n"
        "<b>Portfolio Tracker</b>\n"
        "• <code>/port</code> — View portfolio value, PnL & holdings breakdown\n"
        "• <code>/port add stock AAPL 10 210.50</code> — Add/update holding\n"
        "• <code>/port del AAPL</code> — Remove holding\n\n"
        "<b>Favorites Watchlist</b>\n"
        "• <code>/fav add stock AAPL</code> — Add US stock to favorites\n"
        "• <code>/fav add crypto BTC</code> — Add crypto to favorites\n"
        "• <code>/fav del AAPL</code> — Remove from favorites\n"
        "• <code>/fav list</code> — View favorites list\n\n"
        "<b>Price Alerts</b>\n"
        "• <code>/alert NVDA 140</code> — Alert when NVDA reaches $140\n"
        "• <code>/alerts</code> — List active price alerts\n"
        "• <code>/delalert NVDA 140</code> — Delete price alert\n\n"
        "<b>AI Investor Research</b>\n"
        "• <code>/research TSLA</code> — Generate AI research report\n\n"
        "<b>Automated Notifications</b>\n"
        "• US Market Open Alert (09:30 AM ET, Mon-Fri)\n"
        "• US Market Close Alert (04:00 PM ET, Mon-Fri)\n"
        "• Drastic Price Movement Alerts (Spikes/Drops)\n"
        "• Daily Digest Summary (Midnight UTC)"
    )
    await update.message.reply_text(about_text, parse_mode="HTML")


async def cmd_chatid(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Setup helper command to inspect Chat and Thread ID."""
    chat = update.effective_chat
    thread_id = update.message.message_thread_id
    target_value = f"{chat.id}:{thread_id}" if thread_id else str(chat.id)
    await update.message.reply_text(
        f"<b>Chat Info</b>\n"
        f"ID: <code>{chat.id}</code>\n"
        f"Type: {chat.type}\n"
        f"Title: {chat.title or '—'}\n"
        + (f"Topic Thread ID: <code>{thread_id}</code>\n" if thread_id else "")
        + f"\nAdd to <code>TARGET_CHAT_ID</code>:\n<code>{target_value}</code>",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# Startup & Main Entrypoint
# ---------------------------------------------------------------------------

async def post_init(app: Application) -> None:
    """Initialize DB and run startup health checks."""
    await db.init()
    log.info("✓ SQLite Database initialized at %s", config.db_path)

    try:
        me = await app.bot.get_me()
        log.info("✓ Telegram Bot: @%s", me.username)
    except Exception as exc:
        log.error("✗ Telegram Bot Token error: %s", exc)
        sys.exit(1)

    log.info("Investor Bot successfully initialized!")


def main() -> None:
    if not config.telegram_token:
        log.error("TELEGRAM_BOT_TOKEN environment variable is missing!")
        sys.exit(1)

    app = (
        Application.builder()
        .token(config.telegram_token)
        .post_init(post_init)
        .build()
    )

    # Command Handlers
    commands = [
        ("p", cmd_price),
        ("price", cmd_price),
        ("fav", cmd_fav),
        ("watchlist", cmd_watchlist),
        ("port", cmd_portfolio),
        ("portfolio", cmd_portfolio),
        ("news", cmd_news),
        ("alert", cmd_alert),
        ("alerts", cmd_alerts),
        ("delalert", cmd_delalert),
        ("research", cmd_research),
        ("market", cmd_market),
        ("daily", cmd_daily),
        ("about", cmd_about),
    ]

    for name, handler in commands:
        app.add_handler(CommandHandler(name, handler, filters=CHAT_FILTER))

    # Helper command & Callback Query Handler for Interactive Buttons
    app.add_handler(CommandHandler("chatid", cmd_chatid))
    app.add_handler(CallbackQueryHandler(handle_callback_query))

    # Start Background Scheduler
    start_scheduler(app)

    log.info("Starting Investor Telegram Bot with Portfolio, News & Interactive Buttons...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
