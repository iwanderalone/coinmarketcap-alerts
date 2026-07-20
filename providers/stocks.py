import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional

from providers.base import MarketStatus, Quote

log = logging.getLogger(__name__)


def _get_stock_quote_sync(symbol: str) -> Quote:
    import yfinance as yf
    symbol_upper = symbol.strip().upper()
    ticker = yf.Ticker(symbol_upper)
    info = ticker.info or {}
    
    # Extract current price from available fields
    current_price = (
        info.get("regularMarketPrice")
        or info.get("currentPrice")
        or info.get("previousClose")
    )
    
    if current_price is None:
        # Fallback to fast history check
        hist = ticker.history(period="2d")
        if hist.empty:
            raise ValueError(f"Could not fetch stock quote for '{symbol_upper}'")
        current_price = float(hist["Close"].iloc[-1])
        if len(hist) > 1:
            prev_close = float(hist["Close"].iloc[-2])
            change_24h_pct = ((current_price - prev_close) / prev_close) * 100
        else:
            change_24h_pct = 0.0
    else:
        prev_close = info.get("regularMarketPreviousClose") or info.get("previousClose")
        if prev_close and prev_close > 0:
            change_24h_pct = ((current_price - prev_close) / prev_close) * 100
        else:
            change_24h_pct = float(info.get("regularMarketChangePercent") or 0.0)

    name = info.get("shortName") or info.get("longName") or symbol_upper
    
    return Quote(
        symbol=symbol_upper,
        name=name,
        asset_type="stock",
        price=float(current_price),
        change_24h_pct=float(change_24h_pct),
        high_24h=float(info["regularMarketDayHigh"]) if info.get("regularMarketDayHigh") else None,
        low_24h=float(info["regularMarketDayLow"]) if info.get("regularMarketDayLow") else None,
        market_cap=float(info["marketCap"]) if info.get("marketCap") else None,
        volume_24h=float(info["regularMarketVolume"]) if info.get("regularMarketVolume") else None,
        pe_ratio=float(info["trailingPE"]) if info.get("trailingPE") else None,
        fifty_two_week_high=float(info["fiftyTwoWeekHigh"]) if info.get("fiftyTwoWeekHigh") else None,
        fifty_two_week_low=float(info["fiftyTwoWeekLow"]) if info.get("fiftyTwoWeekLow") else None,
    )


async def fetch_stock_quote(symbol: str) -> Quote:
    """Async wrapper for fetching stock quotes via yfinance."""
    return await asyncio.to_thread(_get_stock_quote_sync, symbol)


def _get_market_status_sync() -> MarketStatus:
    import yfinance as yf
    ny_tz = ZoneInfo("America/New_York")
    now_ny = datetime.now(ny_tz)
    weekday = now_ny.weekday()  # 0=Mon, 4=Fri, 5=Sat, 6=Sun
    
    # Regular trading hours: Mon-Fri 09:30 - 16:00 ET
    is_weekday = weekday < 5
    start_time = now_ny.replace(hour=9, minute=30, second=0, microsecond=0)
    end_time = now_ny.replace(hour=16, minute=0, second=0, microsecond=0)
    
    is_open = is_weekday and (start_time <= now_ny <= end_time)
    status_text = "OPEN" if is_open else ("CLOSED (Weekend)" if not is_weekday else "CLOSED")
    
    # Fetch S&P 500 and Nasdaq indices
    sp500_price, sp500_pct = None, None
    nasdaq_price, nasdaq_pct = None, None
    
    try:
        sp = yf.Ticker("^GSPC").info
        sp500_price = sp.get("regularMarketPrice") or sp.get("previousClose")
        prev = sp.get("regularMarketPreviousClose") or sp.get("previousClose")
        if sp500_price and prev:
            sp500_pct = ((sp500_price - prev) / prev) * 100
    except Exception as e:
        log.warning("Could not fetch S&P 500 index: %s", e)

    try:
        ixic = yf.Ticker("^IXIC").info
        nasdaq_price = ixic.get("regularMarketPrice") or ixic.get("previousClose")
        prev = ixic.get("regularMarketPreviousClose") or ixic.get("previousClose")
        if nasdaq_price and prev:
            nasdaq_pct = ((nasdaq_price - prev) / prev) * 100
    except Exception as e:
        log.warning("Could not fetch Nasdaq index: %s", e)

    return MarketStatus(
        is_open=is_open,
        status_text=status_text,
        sp500_price=float(sp500_price) if sp500_price else None,
        sp500_change_pct=float(sp500_pct) if sp500_pct else None,
        nasdaq_price=float(nasdaq_price) if nasdaq_price else None,
        nasdaq_change_pct=float(nasdaq_pct) if nasdaq_pct else None,
    )


async def fetch_market_status() -> MarketStatus:
    """Async wrapper for market status check."""
    return await asyncio.to_thread(_get_market_status_sync)
