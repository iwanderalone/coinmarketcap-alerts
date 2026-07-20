import logging
from typing import Optional
import httpx
from config import config
from providers.base import Quote

log = logging.getLogger(__name__)

CMC_QUOTES_SYMBOL_URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
CG_BASE = "https://api.coingecko.com/api/v3"

# Symbol map for common cryptos to CG ids if needed
CRYPTO_CG_ID_MAP = {
    "TON": "the-open-network",
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "XRP": "ripple",
    "ADA": "cardano",
    "DOGE": "dogecoin",
    "BNB": "binancecoin",
    "AVAX": "avalanche-2",
    "DOT": "polkadot",
    "LINK": "chainlink",
    "NEAR": "near",
    "SUI": "sui",
}


async def fetch_crypto_quote(symbol: str) -> Quote:
    """Fetch cryptocurrency quote using CMC API (if configured) or CoinGecko fallback."""
    symbol_upper = symbol.strip().upper()
    
    # Try CoinMarketCap first if key exists
    if config.cmc_api_key:
        try:
            return await _fetch_cmc_by_symbol(symbol_upper)
        except Exception as exc:
            log.warning("CMC fetch failed for %s (%s). Falling back to CoinGecko...", symbol_upper, exc)

    # CoinGecko fallback
    return await _fetch_coingecko_by_symbol(symbol_upper)


async def _fetch_cmc_by_symbol(symbol: str) -> Quote:
    headers = {"X-CMC_PRO_API_KEY": config.cmc_api_key, "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            CMC_QUOTES_SYMBOL_URL, headers=headers, params={"symbol": symbol, "convert": "USD"}
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        if symbol not in data:
            raise ValueError(f"Symbol {symbol} not found in CoinMarketCap response")
        
        coin_info = data[symbol]
        if isinstance(coin_info, list):
            coin_info = coin_info[0]
            
        quote_usd = coin_info["quote"]["USD"]
        return Quote(
            symbol=symbol,
            name=coin_info.get("name", symbol),
            asset_type="crypto",
            price=float(quote_usd["price"]),
            change_24h_pct=float(quote_usd.get("percent_change_24h") or 0.0),
            change_1h_pct=float(quote_usd.get("percent_change_1h") or 0.0) if quote_usd.get("percent_change_1h") is not None else None,
            market_cap=float(quote_usd.get("market_cap") or 0.0),
            volume_24h=float(quote_usd.get("volume_24h") or 0.0),
        )


async def _fetch_coingecko_by_symbol(symbol: str) -> Quote:
    cg_id = CRYPTO_CG_ID_MAP.get(symbol, symbol.lower())
    async with httpx.AsyncClient(timeout=12) as client:
        # Get market data
        resp = await client.get(
            f"{CG_BASE}/coins/{cg_id}",
            params={
                "localization": "false",
                "tickers": "false",
                "market_data": "true",
                "community_data": "false",
                "developer_data": "false",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        md = data["market_data"]
        price = md["current_price"]["usd"]
        pct_24h = md.get("price_change_percentage_24h") or 0.0
        pct_1h = md.get("price_change_percentage_1h_in_currency", {}).get("usd")
        
        return Quote(
            symbol=symbol,
            name=data.get("name", symbol),
            asset_type="crypto",
            price=float(price),
            change_24h_pct=float(pct_24h),
            change_1h_pct=float(pct_1h) if pct_1h is not None else None,
            high_24h=float(md["high_24h"]["usd"]) if md.get("high_24h") and md["high_24h"].get("usd") else None,
            low_24h=float(md["low_24h"]["usd"]) if md.get("low_24h") and md["low_24h"].get("usd") else None,
            market_cap=float(md["market_cap"]["usd"]) if md.get("market_cap") and md["market_cap"].get("usd") else None,
            volume_24h=float(md["total_volume"]["usd"]) if md.get("total_volume") and md["total_volume"].get("usd") else None,
        )
