import os
import sys
from datetime import datetime, timezone
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["TELEGRAM_BOT_TOKEN"] = "test_token"
os.environ["CMC_API_KEY"] = "test_key"
os.environ["TARGET_CHAT_ID"] = "-1001234567890:42,-1009876543210"
os.environ["DATABASE_PATH"] = ":memory:"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import config, parse_targets
from database import Database, WatchlistItem, PriceAlert
from providers.base import Quote
from services.scheduler import fmt_pct, fmt_price


def test_fmt_price():
    assert fmt_price(2.5) == "$2.50"
    assert fmt_price(1234.5678) == "$1,234.57"
    assert fmt_price(0.0054321) == "$0.005432"


def test_fmt_pct():
    assert "▲" in fmt_pct(5.25)
    assert "5.25" in fmt_pct(5.25)
    assert "▼" in fmt_pct(-12.3)
    assert "12.30" in fmt_pct(-12.3)


def test_parse_targets():
    result = parse_targets("-1001234567890:42,-1009876543210")
    assert len(result) == 2
    assert result[0] == (-1001234567890, 42)
    assert result[1] == (-1009876543210, None)


@pytest.mark.asyncio
async def test_database_watchlist_operations(tmp_path):
    db_file = tmp_path / "test.db"
    test_db = Database(db_file)
    await test_db.init()

    # Add watchlist
    added = await test_db.add_to_watchlist(123, "AAPL", "stock")
    assert added is True

    # Duplicate add
    dup = await test_db.add_to_watchlist(123, "AAPL", "stock")
    assert dup is False

    # Get watchlist
    items = await test_db.get_watchlist(123)
    assert len(items) == 1
    assert items[0].symbol == "AAPL"
    assert items[0].asset_type == "stock"

    # Remove watchlist
    removed = await test_db.remove_from_watchlist(123, "AAPL")
    assert removed is True
    assert len(await test_db.get_watchlist(123)) == 0


@pytest.mark.asyncio
async def test_database_price_alerts(tmp_path):
    db_file = tmp_path / "test_alerts.db"
    test_db = Database(db_file)
    await test_db.init()

    alert = await test_db.add_alert(
        chat_id=123,
        symbol="BTC",
        asset_type="crypto",
        target_price=100000.0,
        direction="above",
    )
    assert alert.id is not None
    assert alert.symbol == "BTC"

    alerts = await test_db.get_alerts_for_chat(123)
    assert len(alerts) == 1

    removed = await test_db.remove_alert_by_target(123, "BTC", 100000.0)
    assert removed is True
    assert len(await test_db.get_alerts_for_chat(123)) == 0


@pytest.mark.asyncio
async def test_database_portfolio(tmp_path):
    db_file = tmp_path / "test_port.db"
    test_db = Database(db_file)
    await test_db.init()

    item = await test_db.add_portfolio_item(
        chat_id=123,
        symbol="AAPL",
        asset_type="stock",
        quantity=10.0,
        buy_price=210.5,
    )
    assert item.symbol == "AAPL"
    assert item.quantity == 10.0

    port = await test_db.get_portfolio(123)
    assert len(port) == 1
    assert port[0].buy_price == 210.5

    removed = await test_db.remove_portfolio_item(123, "AAPL")
    assert removed is True
    assert len(await test_db.get_portfolio(123)) == 0
