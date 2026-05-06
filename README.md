# coinmarketcap-alerts

A Telegram bot that monitors **Toncoin (TON)** via the CoinMarketCap API and sends price alerts to a group, channel, or private chat.

## Features

- **Hourly price updates** — sent at the top of every hour
- **Daily summary** — posted at midnight UTC with 24h and 7d change
- **Spike alerts** — instant notification if TON moves ±10% within an hour (2-hour cooldown between alerts)
- **Fixed target chat** — configure a group or channel ID so the bot posts automatically without anyone running `/start`

## Requirements

- Python 3.13+
- CoinMarketCap API key (free tier works)
- Telegram bot token from [@BotFather](https://t.me/BotFather)
- Docker (optional, for containerised deployment)

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

> **Finding your chat/topic ID:** Add the bot to your group/channel, then send `/chatid` — the bot replies with the exact value to paste into `TARGET_CHAT_ID`, including the topic thread ID if you run it inside a topic.  
> For channels, the bot must be an **Admin** with "Post Messages" permission.

**Posting to a specific topic (forum group):**

```env
# Whole group
TARGET_CHAT_ID=-1001234567890

# Specific topic inside a group
TARGET_CHAT_ID=-1001234567890:123
```

Run `/chatid` inside the topic to get the ready-to-paste value.

### 3. Run with Python

```bash
pip install -r requirements.txt
python bot.py
```

### 4. Run with Docker Compose

```bash
docker compose up -d
```

**Useful commands:**

```bash
docker compose logs -f    # live logs
docker compose stop       # stop
docker compose down       # stop and remove container
docker compose restart    # restart after config changes
```

## Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Subscribe to all alerts |
| `/stop` | Unsubscribe |
| `/price` | Current price snapshot |
| `/status` | Show subscription status and fixed target |
| `/chatid` | Show the current chat's ID (useful for config) |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | Token from @BotFather |
| `CMC_API_KEY` | Yes | CoinMarketCap API key |
| `TARGET_CHAT_ID` | No | Chat/group/channel ID, optionally with topic: `-1001234567890` or `-1001234567890:123` |

## Project Structure

```
coinmarketcap-alerts/
├── bot.py              # Main bot logic
├── requirements.txt    # Python dependencies
├── Dockerfile          # Container image definition
├── docker-compose.yml  # Compose service definition
├── .env.example        # Environment variable template
└── tests/
    └── test_bot.py     # Unit tests
```

## How It Works

- **Spike check** runs every 5 minutes using CoinMarketCap's `percent_change_1h` field (a server-side rolling window — no local history needed)
- **Hourly update** fires via APScheduler cron at `minute=0`
- **Daily summary** fires at `hour=0, minute=0` UTC
- All scheduled jobs broadcast to both `TARGET_CHAT_ID` (env) and any individual `/start` subscribers

## CoinMarketCap API Usage

The bot uses the `/v1/cryptocurrency/quotes/latest` endpoint.  
At 5-minute polling: ~288 calls/day — well within the free tier (333 calls/day).
