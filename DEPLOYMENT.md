# Deployment & Architecture Guide — market-investor-bot

## 1. Общие сведения и назначение
**Investor Multi-Asset Telegram Bot** (`@coinmc_alert_bot`) — бот для комплексного отслеживания портфеля инвестора, котировок акций США (NYSE / NASDAQ) и криптовалют (CoinGecko, CMC, TON/GRAM), автоматических алертов на открытие/закрытие торгов с привязкой ко времени Абу-Даби (GST), алертов на ценовые спайки и AI-аналитики.

---

## 2. Инфраструктура и сервер (Production)

- **Хост**: `hz-hmg` (Hetzner Cloud VPS)
- **IP-адрес**: `178.104.57.233`
- **Пользователь**: `root`
- **SSH-доступ**: через ключ `~/.ssh/` (алиас `hz-hmg` в `~/.ssh/config`)
- **Рабочая директория на сервере**: `/opt/coinmarket-alerts`
- **Репозиторий GitHub**: `git@github.com:iwanderalone/coinmarketcap-alerts.git` (ветка `master`)

### Структура на сервере:
```
/opt/coinmarket-alerts/
├── .env                  # Секреты (TELEGRAM_BOT_TOKEN, TARGET_CHAT_ID, AI_API_KEY, etc.)
├── investor_bot.db       # Основная база данных SQLite (монтируется как том в контейнер)
├── docker-compose.yml    # Описание сервиса
└── Dockerfile            # Сборка контейнера на базе python:3.13-slim
```

### Стандартный деплой обновлений в одну команду:
```bash
ssh hz-hmg "cd /opt/coinmarket-alerts && git pull origin master && docker compose up -d --build"
```

### Полезные команды для обслуживания на сервере:
```bash
# Проверить статус контейнера
ssh hz-hmg "cd /opt/coinmarket-alerts && docker compose ps"

# Просмотр последних 50 строк логов
ssh hz-hmg "cd /opt/coinmarket-alerts && docker compose logs -n 50 -f"

# Перезапуск без пересборки
ssh hz-hmg "cd /opt/coinmarket-alerts && docker compose restart"
```

---

## 3. Последние ключевые изменения

### А. Интеграция с Interactive Brokers (IBKR Flex Web Service)
- **Модуль**: `providers/ibkr.py`
- **Протокол**: Официальный двухэтапный REST API Flex Web Service v3:
  - Шаг 1: `SendRequest` на `https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/SendRequest`
  - Шаг 2: Опрос `GetStatement` на `https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/GetStatement`
  - *Важно*: старый домен `gdcdp.interactivebrokers.com` отключен IBKR, используется актуальный `ndcdyn`.
- **Парсер XML**:
  - Извлекает `<OpenPosition>` (акции, ETF, крипто).
  - Нормализует тикеры (например, `BRK B` $\rightarrow$ `BRK-B`).
  - Агрегирует несколько лотов по одному активу с расчётом средневзвешенной цены входа (`weighted average cost basis`).
- **Синхронизация с БД (`database.py`)**:
  - Таблица `ibkr_configs` хранит токен, query_id и дату последней синхронизации для каждого чата.
  - Метод `sync_ibkr_portfolio(chat_id, positions, replace_stocks=True)`: атомарно обновляет акции из брокера, **не затрагивая крипто-позиции** пользователя, добавленные вручную (например, GRAM/TON).

### Б. Поддержка GRAM (Toncoin) в экосистеме Telegram Wallet
- **Модули**: `providers/crypto.py` и `services/market_service.py`
- Добавлен маппинг `"GRAM": "the-open-network"` для CoinGecko API.
- `"GRAM"` добавлен в список `KNOWN_CRYPTO_SYMBOLS`, исключая ошибочное распознавание тикера как OTC-акции США.
- Поддерживается живая цена, PnL, суточный объем и AI-анализ по тикеру `GRAM`.

### В. Автоматические отчеты по сессиям США (Время Абу-Даби • GST, UTC+4)
- **Модуль**: `services/scheduler.py`
- **Расписание**:
  - **Market Open**: Пн–Пт в `09:30 America/New_York` (17:30 летом / 18:30 зимой по Абу-Даби).
  - **Market Close**: Пн–Пт в `16:00 America/New_York` (00:00 полночь летом / 01:00 зимой по Абу-Даби).
- **Логика**:
  - Перед отправкой уведомления бот автоматически связывается с IBKR и актуализирует позиции.
  - Запрашивает статус основных индексов (S&P 500, Nasdaq).
  - Рассчитывает текущий портфель (акции + крипта) и отправляет единый сводный брифинг в чат.

### Г. Интерактивный UI с кнопками (без ручного ввода команд)
- **Модуль**: `bot.py`
- **Постоянная клавиатура (ReplyKeyboardMarkup)**: закреплена внизу экрана над полем ввода сообщения:
  - `[ 💼 Мой портфель ]`
  - `[ 🔄 Синхронизация IBKR ]`
  - `[ 🏛️ Рынок США ]`
  - `[ ⭐ Избранное ]`
  - `[ 📊 Дневной отчет ]`
  - `[ ❓ Меню и команды ]`
- **Инлайн-дашборд (`/menu` или `/start`)**: открывает интерактивные кнопки под сообщением.
- **Официальное меню Telegram (`set_my_commands`)**: зарегистрировано в BotFather API на русском языке с эмодзи.

---

## 4. Особенности работы с портфелем

### Живые цены vs. Отчёты брокера:
- **Цены и PnL**: при вызове `/port` или нажатии кнопки `[💼 Refresh]` бот запрашивает секундные рыночные котировки с бирж США через Yahoo Finance и CoinGecko в режиме реального времени.
- **Состав портфеля из IBKR**: обновляется раз в сутки по результатам закрытия дня (End-of-Day). Сделки, совершенные прямо внутри текущего дня, попадают в официальный стейтмент IBKR ночью после закрытия торгов.

### Расчёт PnL и цена покупки:
- `PnL = (Текущая цена × Количество) - (Цена покупки × Количество)`
- Если актив добавлен с ценой $0.00, вся его рыночная стоимость считается как 100% прибыль.
- Чтобы сбросить PnL актива в $0 (или указать реальную себестоимость):
  ```text
  /port del СИМВОЛ
  /port add crypto СИМВОЛ КОЛИЧЕСТВО ЦЕНА_ПОКУПКИ
  ```
