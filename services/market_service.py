import logging
from typing import Optional
from providers.base import Quote
from providers.crypto import fetch_crypto_quote
from providers.stocks import fetch_stock_quote

log = logging.getLogger(__name__)

# Known crypto symbols for auto-detection
KNOWN_CRYPTO_SYMBOLS = {
    "BTC", "ETH", "TON", "SOL", "XRP", "ADA", "DOGE", "BNB", "AVAX",
    "DOT", "LINK", "NEAR", "SUI", "SHIB", "PEPE", "UNI", "LTC", "APT"
}


async def fetch_quote(symbol: str, asset_type: Optional[str] = None) -> Quote:
    """Fetch quote for any symbol. Auto-detects crypto vs stock if asset_type is not provided."""
    symbol_upper = symbol.strip().upper()

    if asset_type == "crypto" or (not asset_type and symbol_upper in KNOWN_CRYPTO_SYMBOLS):
        try:
            return await fetch_crypto_quote(symbol_upper)
        except Exception as exc:
            if not asset_type:
                # Fallback to stock search if auto-detect failed
                log.info("Crypto lookup failed for %s (%s), trying as stock...", symbol_upper, exc)
                return await fetch_stock_quote(symbol_upper)
            raise exc

    # Default to stock lookup first, with crypto fallback
    try:
        return await fetch_stock_quote(symbol_upper)
    except Exception as stock_exc:
        if not asset_type:
            log.info("Stock lookup failed for %s (%s), trying as crypto...", symbol_upper, stock_exc)
            return await fetch_crypto_quote(symbol_upper)
        raise stock_exc
