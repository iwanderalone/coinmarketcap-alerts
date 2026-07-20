# market-investor-bot (Investor Multi-Asset Telegram Bot)

An all-in-one Telegram Bot designed for investors to track **US Stocks** and **Cryptocurrencies**, manage favorites watchlists, receive **US Market Open / Closing alerts**, monitor price spikes, and run **AI-powered fundamental research**.

---

## 🌟 Key Features

- **Multi-Asset Real-Time Quotes**: Fetch live price, 24h change, market cap, and volume for any US Stock (`AAPL`, `NVDA`, `TSLA`) or Crypto (`BTC`, `ETH`, `TON`, `SOL`).
- **Favorites Watchlist**: Add/remove favorite stocks and crypto (`/fav add stock AAPL`, `/fav add crypto BTC`). Get on-demand performance updates (`/watchlist`).
- **US Market Open & Close Alerts**: Automatic notifications when NYSE & NASDAQ open (09:30 AM ET) and close (04:00 PM ET) with market index recaps (S&P 500, Nasdaq).
- **Drastic Price Movement Alerts**: Instant alerts for sharp price surges or drops across watchlisted assets.
- **Price Target Alerts**: Set custom threshold alerts (`/alert NVDA 140`).
- **AI Investor Research Agent (`/research <symbol>`)**: Connect your AI API key (Google Gemini, OpenAI, or Anthropic) to generate automated investor research briefings (executive summary, fundamental drivers, price action, risks).
- **SQLite Persistence**: Watchlists and target alerts are persisted asynchronously in SQLite (`investor_bot.db`).

---

## 🛠️ Commands

| Command | Usage Example | Description |
|---------|---------------|-------------|
| `/p` or `/price` | `/p AAPL` or `/p BTC` | Real-time price quote & market stats |
| `/fav` | `/fav add stock AAPL`<br>`/fav add crypto BTC`<br>`/fav list`<br>`/fav del AAPL` | Manage favorite stocks & crypto |
| `/watchlist` | `/watchlist` | Live 24h performance summary of your favorites |
| `/market` | `/market` | Status of US Market (Open/Closed) & major indices |
| `/alert` | `/alert NVDA 145` | Set target price alert |
| `/alerts` | `/alerts` | List all active price alerts |
| `/delalert` | `/delalert NVDA 145` | Delete a price alert |
| `/research` | `/research TSLA` | AI-generated investor research briefing |
| `/about` | `/about` | Display full command list and bot configuration |
| `/chatid` | `/chatid` | Inspect current Chat ID & topic thread ID |

---

## ⚡ Quick Setup

### 1. Clone & Configure
```bash
git clone https://github.com/iwanderalone/market-investor-bot.git
cd market-investor-bot
cp .env.example .env
```

Edit `.env`:
```env
TELEGRAM_BOT_TOKEN=7123456789:AAF...your_token...
TARGET_CHAT_ID=-1001234567890

# Optional: Add AI Key for /research <symbol>
AI_PROVIDER=gemini
AI_API_KEY=your_gemini_or_openai_api_key
```

### 2. Run directly with Python
```bash
pip install -r requirements.txt
python bot.py
```

### 3. Run with Docker Compose
```bash
touch investor_bot.db
docker compose up -d
```

---

## 🧪 Running Tests

```bash
pip install pytest pytest-asyncio
pytest tests/ -v
```

---

## 📁 Project Structure

```
market-investor-bot/
├── config.py             # Configuration & environment variables
├── database.py           # Async SQLite database & migrations
├── providers/            # Data providers
│   ├── base.py           # Unified data schemas (Quote, MarketStatus)
│   ├── crypto.py         # CoinMarketCap & CoinGecko integration
│   ├── stocks.py         # Yahoo Finance integration & market hours check
│   └── ai_research.py    # Gemini, OpenAI & Anthropic research provider
├── services/             # Core business logic
│   ├── market_service.py # Unified quote router & auto-detector
│   └── scheduler.py      # Background jobs (Market open/close, spikes, digests)
├── bot.py                # Main Telegram bot handlers & entrypoint
├── requirements.txt      # Python dependencies
├── Dockerfile            # Container image definition
└── docker-compose.yml    # Docker Compose deployment
```
