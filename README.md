# coinmarketcap-alerts

A Telegram bot that monitors **Toncoin (TON)** and posts price updates, historical highs, and spike alerts to configured channels or group topics.

## Features

- **Hourly price updates** — sent at the top of every hour
- **Daily summary** — posted at midnight UTC with 24h and 7d change
- **Spike alerts** — instant alert if TON moves ±10% within an hour (2-hour cooldown)
- **On-demand commands** — current price, 24h swing, 30-day/1-year highs, all-time high
- **Channel-only** — commands only work in configured chats; the bot ignores private messages
- **Multi-target** — broadcast to multiple channels or group topics simultaneously
- **Startup health checks** — validates API keys and chat access before the bot starts

## Requirements

- Python 3.13+
- CoinMarketCap API key (free basic tier works for real-time data)
- Telegram bot token from [@BotFather](https://t.me/BotFather)
- Docker + Docker Compose (for containerised deployment)

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/iwanderalone/coinmarketcap-alerts.git
cd coinmarketcap-alerts
```

### 2. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env`:

```env
TELEGRAM_BOT_TOKEN=7123456789:AAF...your_token...
CMC_API_KEY=your_coinmarketcap_api_key
TARGET_CHAT_ID=-1001234567890
```

See the [Target Chat ID](#target-chat-id) section below for format details.

### 3. Run with Docker Compose

```bash
docker compose up -d
```

**Useful commands:**

```bash
docker compose logs -f     # live logs
docker compose stop        # stop
docker compose down        # stop and remove container
docker compose restart     # restart after .env changes
```

### 4. Run with Python directly

```bash
pip install -r requirements.txt
python bot.py
```

## Target Chat ID

Add the bot to your group or channel, then send `/chatid` inside it — the bot replies with the exact value to paste into `TARGET_CHAT_ID`.

| Format | Example | Use case |
|--------|---------|----------|
| Plain group or channel | `-1001234567890` | Post to the whole chat |
| Specific topic | `-1001234567890:123` | Post to one topic thread |
| Multiple targets | `-1001234567890:123,-1009876543210` | Post to several chats |

> For **channels**, the bot must be added as an **Admin** with "Post Messages" permission.  
> For **groups with topics**, run `/chatid` inside the specific topic to get the thread ID.

## Startup Health Checks

On startup the bot validates:

1. **Telegram token** — calls `getMe` to confirm the token is valid
2. **CoinMarketCap API key** — makes a live request and checks for a valid response
3. **Target chats** — calls `getChat` on each configured chat ID to confirm the bot has access

If any check fails, the bot logs the error and exits immediately rather than running silently broken.

## Commands

All commands work only in configured target chats. Private messages to the bot are ignored.

| Command | Description |
|---------|-------------|
| `/current` | Current price with 1h, 24h, and 7d change plus market cap |
| `/swing` | 24h price swing — where TON opened 24h ago vs now |
| `/top1m` | Highest TON price in the last 30 days |
| `/top1y` | Highest TON price in the last 365 days |
| `/ath` | All-time high price, the date it hit, and % below ATH now |
| `/about` | Bot description and full command list |
| `/chatid` | Show the current chat's ID and topic thread ID (works everywhere — useful for setup) |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | Token from @BotFather |
| `CMC_API_KEY` | Yes | CoinMarketCap API key |
| `TARGET_CHAT_ID` | Yes | One or more chat targets (see format above) |

## Data Sources

| Data | Source | Notes |
|------|--------|-------|
| Real-time price, 1h/24h/7d change, market cap | CoinMarketCap | Requires API key |
| 30-day high, 1-year high, all-time high | CoinGecko | Free, no key needed |

CoinMarketCap free tier allows ~333 calls/day. The bot uses ~288 (5-minute spike checks) leaving ~45 for on-demand commands.

## Project Structure

```
coinmarketcap-alerts/
├── bot.py              # Main bot logic
├── requirements.txt    # Python dependencies
├── Dockerfile          # Container image definition
├── docker-compose.yml  # Compose service definition
├── .env.example        # Environment variable template
├── .env.test           # Placeholder env for tests
└── tests/
    └── test_bot.py     # Unit tests
```

## Running Tests

```bash
pip install pytest pytest-asyncio
pytest tests/ -v
```

## How It Works

- **Spike check** runs every 5 minutes and reads `percent_change_1h` from CoinMarketCap — a server-side rolling window, so no local history is needed
- **Hourly update** fires via APScheduler cron at `minute=0`
- **Daily summary** fires at `hour=0, minute=0` UTC
- **Historical commands** (`/top1m`, `/top1y`, `/ath`) fetch from CoinGecko's free API on demand
- All jobs and commands target the `TARGETS` list parsed from `TARGET_CHAT_ID` at startup
