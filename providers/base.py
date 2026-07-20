from dataclasses import dataclass
from typing import Optional


@dataclass
class Quote:
    symbol: str
    name: str
    asset_type: str  # "stock" or "crypto"
    price: float
    change_24h_pct: float
    change_1h_pct: Optional[float] = None
    high_24h: Optional[float] = None
    low_24h: Optional[float] = None
    market_cap: Optional[float] = None
    volume_24h: Optional[float] = None
    pe_ratio: Optional[float] = None  # Stocks
    fifty_two_week_high: Optional[float] = None  # Stocks
    fifty_two_week_low: Optional[float] = None  # Stocks
    currency: str = "USD"


@dataclass
class MarketStatus:
    is_open: bool
    status_text: str
    sp500_price: Optional[float] = None
    sp500_change_pct: Optional[float] = None
    nasdaq_price: Optional[float] = None
    nasdaq_change_pct: Optional[float] = None
