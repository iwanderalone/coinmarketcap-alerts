import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ["TELEGRAM_BOT_TOKEN"] = "test_token"
os.environ["CMC_API_KEY"] = "test_key"
os.environ["TARGET_CHAT_ID"] = "-1001234567890:42,-1009876543210"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bot


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def test_fmt_price():
    assert bot.fmt_price(2.5) == "$2.5000"
    assert bot.fmt_price(1234.5678) == "$1,234.5678"


def test_fmt_pct_positive():
    assert "▲" in bot.fmt_pct(5.25)
    assert "5.25" in bot.fmt_pct(5.25)


def test_fmt_pct_negative():
    assert "▼" in bot.fmt_pct(-12.3)
    assert "12.30" in bot.fmt_pct(-12.3)


# ---------------------------------------------------------------------------
# Target parsing
# ---------------------------------------------------------------------------

def test_targets_parsed():
    assert (-1001234567890, 42) in bot.TARGETS
    assert (-1009876543210, None) in bot.TARGETS


def test_target_chat_ids():
    assert -1001234567890 in bot.TARGET_CHAT_IDS
    assert -1009876543210 in bot.TARGET_CHAT_IDS


def test_parse_targets_empty():
    assert bot._parse_targets("") == []


def test_parse_targets_no_thread():
    result = bot._parse_targets("-100999")
    assert result == [(-100999, None)]


def test_parse_targets_with_thread():
    result = bot._parse_targets("-100999:7")
    assert result == [(-100999, 7)]


# ---------------------------------------------------------------------------
# Broadcast
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_broadcast_sends_to_all_targets():
    mock_app = MagicMock()
    mock_app.bot.send_message = AsyncMock()

    await bot.broadcast(mock_app, "hello")

    calls = mock_app.bot.send_message.call_args_list
    assert len(calls) == 2

    chat_ids_sent = {c.kwargs["chat_id"] for c in calls}
    assert chat_ids_sent == {-1001234567890, -1009876543210}


@pytest.mark.asyncio
async def test_broadcast_passes_thread_id_where_set():
    mock_app = MagicMock()
    mock_app.bot.send_message = AsyncMock()

    await bot.broadcast(mock_app, "hello")

    calls = {c.kwargs["chat_id"]: c.kwargs for c in mock_app.bot.send_message.call_args_list}
    assert calls[-1001234567890]["message_thread_id"] == 42
    assert calls[-1009876543210]["message_thread_id"] is None


# ---------------------------------------------------------------------------
# Spike check
# ---------------------------------------------------------------------------

MOCK_QUOTE = {
    "price": 3.50,
    "percent_change_1h": -11.5,
    "percent_change_24h": -15.0,
    "percent_change_7d": -20.0,
    "market_cap": 12_000_000_000,
}


@pytest.mark.asyncio
async def test_spike_check_fires_alert():
    bot._alert_cooldown_until = None
    mock_app = MagicMock()
    mock_app.bot.send_message = AsyncMock()

    with patch("bot.fetch_ton_cmc", AsyncMock(return_value=MOCK_QUOTE)):
        await bot.job_spike_check(mock_app)

    assert mock_app.bot.send_message.called
    text = mock_app.bot.send_message.call_args_list[0].kwargs["text"]
    assert "dropped" in text


@pytest.mark.asyncio
async def test_spike_check_respects_cooldown():
    bot._alert_cooldown_until = datetime.now(timezone.utc) + timedelta(hours=1)
    mock_app = MagicMock()
    mock_app.bot.send_message = AsyncMock()

    with patch("bot.fetch_ton_cmc", AsyncMock(return_value=MOCK_QUOTE)):
        await bot.job_spike_check(mock_app)

    mock_app.bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_spike_check_no_alert_below_threshold():
    bot._alert_cooldown_until = None
    mock_app = MagicMock()
    mock_app.bot.send_message = AsyncMock()

    safe_quote = {**MOCK_QUOTE, "percent_change_1h": -4.9}
    with patch("bot.fetch_ton_cmc", AsyncMock(return_value=safe_quote)):
        await bot.job_spike_check(mock_app)

    mock_app.bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_spike_check_sets_cooldown_after_alert():
    bot._alert_cooldown_until = None
    mock_app = MagicMock()
    mock_app.bot.send_message = AsyncMock()

    with patch("bot.fetch_ton_cmc", AsyncMock(return_value=MOCK_QUOTE)):
        await bot.job_spike_check(mock_app)

    assert bot._alert_cooldown_until is not None
    assert bot._alert_cooldown_until > datetime.now(timezone.utc)
