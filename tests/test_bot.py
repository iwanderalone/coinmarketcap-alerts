import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test_token")
os.environ.setdefault("CMC_API_KEY", "test_key")
os.environ["TARGET_CHAT_ID"] = "-1001234567890:42"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bot


# --- fmt helpers ---

def test_fmt_price_formats_correctly():
    assert bot.fmt_price(2.5) == "$2.5000"
    assert bot.fmt_price(1234.5678) == "$1,234.5678"


def test_fmt_pct_positive():
    result = bot.fmt_pct(5.25)
    assert "▲" in result
    assert "5.25" in result


def test_fmt_pct_negative():
    result = bot.fmt_pct(-12.3)
    assert "▼" in result
    assert "12.30" in result


# --- TARGET_CHAT_ID / TARGET_THREAD_ID parsing ---

def test_target_chat_id_parsed():
    assert bot.TARGET_CHAT_ID == -1001234567890


def test_target_thread_id_parsed():
    assert bot.TARGET_THREAD_ID == 42


# --- broadcast sends to topic thread ---

@pytest.mark.asyncio
async def test_broadcast_uses_thread_id_for_fixed_target():
    bot.subscribers = set()

    mock_app = MagicMock()
    mock_app.bot.send_message = AsyncMock()

    await bot.broadcast(mock_app, "hello")

    mock_app.bot.send_message.assert_called_once_with(
        chat_id=-1001234567890,
        message_thread_id=42,
        text="hello",
        parse_mode="HTML",
    )


@pytest.mark.asyncio
async def test_broadcast_skips_duplicate_when_subscriber_equals_target():
    bot.subscribers = {bot.TARGET_CHAT_ID}

    mock_app = MagicMock()
    mock_app.bot.send_message = AsyncMock()

    await bot.broadcast(mock_app, "hello")

    # Target already sent via the fixed-target path; subscriber skip prevents double-send
    assert mock_app.bot.send_message.call_count == 1


@pytest.mark.asyncio
async def test_broadcast_sends_to_subscribers_without_thread_id():
    bot.subscribers = {-9999}

    mock_app = MagicMock()
    mock_app.bot.send_message = AsyncMock()

    await bot.broadcast(mock_app, "hello")

    calls = mock_app.bot.send_message.call_args_list
    # Target call (with thread id) + subscriber call (without)
    assert len(calls) == 2
    subscriber_call = next(c for c in calls if c.kwargs["chat_id"] == -9999)
    assert "message_thread_id" not in subscriber_call.kwargs


# --- spike check ---

@pytest.mark.asyncio
async def test_spike_check_sends_alert_when_threshold_exceeded():
    bot._alert_cooldown_until = None
    bot.subscribers = set()

    mock_quote = {
        "price": 3.50,
        "percent_change_1h": -11.5,
        "percent_change_24h": -15.0,
        "percent_change_7d": -20.0,
        "market_cap": 12_000_000_000,
    }

    mock_app = MagicMock()
    mock_app.bot.send_message = AsyncMock()

    with patch("bot.fetch_ton", AsyncMock(return_value=mock_quote)):
        await bot.job_spike_check(mock_app)

    mock_app.bot.send_message.assert_called_once()
    call_text = mock_app.bot.send_message.call_args.kwargs["text"]
    assert "dropped" in call_text
    assert "11.50%" in call_text


@pytest.mark.asyncio
async def test_spike_check_respects_cooldown():
    bot._alert_cooldown_until = datetime.now(timezone.utc) + timedelta(hours=1)
    bot.subscribers = set()

    mock_app = MagicMock()
    mock_app.bot.send_message = AsyncMock()

    mock_quote = {
        "price": 3.50,
        "percent_change_1h": -15.0,
        "percent_change_24h": -20.0,
        "percent_change_7d": -25.0,
        "market_cap": 12_000_000_000,
    }

    with patch("bot.fetch_ton", AsyncMock(return_value=mock_quote)):
        await bot.job_spike_check(mock_app)

    mock_app.bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_spike_check_no_alert_below_threshold():
    bot._alert_cooldown_until = None
    bot.subscribers = set()

    mock_quote = {
        "price": 3.50,
        "percent_change_1h": -5.0,
        "percent_change_24h": -8.0,
        "percent_change_7d": -10.0,
        "market_cap": 12_000_000_000,
    }

    mock_app = MagicMock()
    mock_app.bot.send_message = AsyncMock()

    with patch("bot.fetch_ton", AsyncMock(return_value=mock_quote)):
        await bot.job_spike_check(mock_app)

    mock_app.bot.send_message.assert_not_called()
