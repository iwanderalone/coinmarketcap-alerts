import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import aiosqlite
from config import config

log = logging.getLogger(__name__)


@dataclass
class WatchlistItem:
    chat_id: int
    symbol: str
    asset_type: str  # "crypto" or "stock"
    added_at: str


@dataclass
class PriceAlert:
    id: Optional[int]
    chat_id: int
    symbol: str
    asset_type: str  # "crypto" or "stock"
    target_price: float
    direction: str  # "above" or "below"
    created_at: str


@dataclass
class PortfolioItem:
    chat_id: int
    symbol: str
    asset_type: str
    quantity: float
    buy_price: float
    added_at: str


class Database:
    def __init__(self, db_path: Path = config.db_path):
        self.db_path = db_path

    async def init(self) -> None:
        """Initialize database tables and run migrations."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS watchlists (
                    chat_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    asset_type TEXT NOT NULL,
                    added_at TEXT NOT NULL,
                    PRIMARY KEY (chat_id, symbol)
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS price_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    asset_type TEXT NOT NULL,
                    target_price REAL NOT NULL,
                    direction TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS portfolios (
                    chat_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    asset_type TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    buy_price REAL NOT NULL,
                    added_at TEXT NOT NULL,
                    PRIMARY KEY (chat_id, symbol)
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS ibkr_configs (
                    chat_id INTEGER PRIMARY KEY,
                    token TEXT NOT NULL,
                    query_id TEXT NOT NULL,
                    last_synced_at TEXT
                )
            """)
            await db.commit()

        # Migrate legacy alerts.json if exists
        await self._migrate_legacy_alerts()

    async def _migrate_legacy_alerts(self) -> None:
        legacy_file = Path("alerts.json")
        if not legacy_file.exists():
            return
        try:
            content = legacy_file.read_text().strip()
            if not content:
                return
            data = json.loads(content)
            if data and isinstance(data, list):
                log.info("Migrating legacy alerts.json (%d alerts)...", len(data))
                now_str = datetime.now(timezone.utc).isoformat()
                # Legacy alerts were for TON
                for item in data:
                    chat_id = config.targets[0][0] if config.targets else 0
                    await self.add_alert(
                        chat_id=chat_id,
                        symbol="TON",
                        asset_type="crypto",
                        target_price=float(item["target"]),
                        direction=item["direction"],
                    )
            # Backup legacy file
            legacy_file.rename("alerts.json.bak")
            log.info("Legacy alerts.json successfully migrated and backed up.")
        except Exception as exc:
            log.warning("Could not migrate legacy alerts.json: %s", exc)

    # -------------------------------------------------------------------
    # Watchlist Methods
    # -------------------------------------------------------------------

    async def add_to_watchlist(self, chat_id: int, symbol: str, asset_type: str) -> bool:
        symbol_upper = symbol.strip().upper()
        now_str = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            try:
                await db.execute(
                    "INSERT INTO watchlists (chat_id, symbol, asset_type, added_at) VALUES (?, ?, ?, ?)",
                    (chat_id, symbol_upper, asset_type, now_str),
                )
                await db.commit()
                return True
            except aiosqlite.IntegrityError:
                return False  # Already exists

    async def remove_from_watchlist(self, chat_id: int, symbol: str) -> bool:
        symbol_upper = symbol.strip().upper()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM watchlists WHERE chat_id = ? AND symbol = ?",
                (chat_id, symbol_upper),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def get_watchlist(self, chat_id: int) -> List[WatchlistItem]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT chat_id, symbol, asset_type, added_at FROM watchlists WHERE chat_id = ? ORDER BY asset_type, symbol",
                (chat_id,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [WatchlistItem(**dict(row)) for row in rows]

    async def get_all_watchlists(self) -> List[WatchlistItem]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT chat_id, symbol, asset_type, added_at FROM watchlists") as cursor:
                rows = await cursor.fetchall()
                return [WatchlistItem(**dict(row)) for row in rows]

    # -------------------------------------------------------------------
    # Price Alert Methods
    # -------------------------------------------------------------------

    async def add_alert(self, chat_id: int, symbol: str, asset_type: str, target_price: float, direction: str) -> PriceAlert:
        symbol_upper = symbol.strip().upper()
        now_str = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "INSERT INTO price_alerts (chat_id, symbol, asset_type, target_price, direction, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (chat_id, symbol_upper, asset_type, target_price, direction, now_str),
            )
            await db.commit()
            alert_id = cursor.lastrowid
            return PriceAlert(
                id=alert_id,
                chat_id=chat_id,
                symbol=symbol_upper,
                asset_type=asset_type,
                target_price=target_price,
                direction=direction,
                created_at=now_str,
            )

    async def remove_alert(self, alert_id: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM price_alerts WHERE id = ?", (alert_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def remove_alert_by_target(self, chat_id: int, symbol: str, target_price: float) -> bool:
        symbol_upper = symbol.strip().upper()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM price_alerts WHERE chat_id = ? AND symbol = ? AND ABS(target_price - ?) < 0.0001",
                (chat_id, symbol_upper, target_price),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def get_alerts_for_chat(self, chat_id: int) -> List[PriceAlert]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT id, chat_id, symbol, asset_type, target_price, direction, created_at FROM price_alerts WHERE chat_id = ?",
                (chat_id,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [PriceAlert(**dict(row)) for row in rows]

    async def get_all_alerts(self) -> List[PriceAlert]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT id, chat_id, symbol, asset_type, target_price, direction, created_at FROM price_alerts") as cursor:
                rows = await cursor.fetchall()
                return [PriceAlert(**dict(row)) for row in rows]

    # -------------------------------------------------------------------
    # Portfolio Methods
    # -------------------------------------------------------------------

    async def add_portfolio_item(self, chat_id: int, symbol: str, asset_type: str, quantity: float, buy_price: float) -> PortfolioItem:
        symbol_upper = symbol.strip().upper()
        now_str = datetime.now(timezone.utc).isoformat()
        
        async with aiosqlite.connect(self.db_path) as db_conn:
            db_conn.row_factory = aiosqlite.Row
            # Check for existing holding
            async with db_conn.execute(
                "SELECT quantity, buy_price FROM portfolios WHERE chat_id = ? AND symbol = ?",
                (chat_id, symbol_upper),
            ) as cursor:
                existing = await cursor.fetchone()

            if existing:
                old_qty = float(existing["quantity"])
                old_price = float(existing["buy_price"])
                total_qty = old_qty + quantity
                
                if total_qty > 0:
                    avg_price = ((old_qty * old_price) + (quantity * buy_price)) / total_qty
                else:
                    avg_price = 0.0
                
                await db_conn.execute(
                    "UPDATE portfolios SET quantity = ?, buy_price = ?, added_at = ? WHERE chat_id = ? AND symbol = ?",
                    (total_qty, avg_price, now_str, chat_id, symbol_upper),
                )
                final_qty = total_qty
                final_price = avg_price
            else:
                await db_conn.execute(
                    "INSERT INTO portfolios (chat_id, symbol, asset_type, quantity, buy_price, added_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (chat_id, symbol_upper, asset_type, quantity, buy_price, now_str),
                )
                final_qty = quantity
                final_price = buy_price
                
            await db_conn.commit()
            
            return PortfolioItem(
                chat_id=chat_id,
                symbol=symbol_upper,
                asset_type=asset_type,
                quantity=final_qty,
                buy_price=final_price,
                added_at=now_str,
            )

    async def remove_portfolio_item(self, chat_id: int, symbol: str) -> bool:
        symbol_upper = symbol.strip().upper()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM portfolios WHERE chat_id = ? AND symbol = ?",
                (chat_id, symbol_upper),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def get_portfolio(self, chat_id: int) -> List[PortfolioItem]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT chat_id, symbol, asset_type, quantity, buy_price, added_at FROM portfolios WHERE chat_id = ?",
                (chat_id,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [PortfolioItem(**dict(row)) for row in rows]

    # -------------------------------------------------------------------
    # Interactive Brokers (IBKR) Methods
    # -------------------------------------------------------------------

    async def get_ibkr_config(self, chat_id: int) -> Optional[dict]:
        """Get IBKR config for chat. Falls back to global env variables if configured."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT token, query_id, last_synced_at FROM ibkr_configs WHERE chat_id = ?",
                (chat_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return dict(row)

        # Fallback to .env config if available
        if config.ibkr_flex_token and config.ibkr_flex_query_id:
            return {
                "token": config.ibkr_flex_token,
                "query_id": config.ibkr_flex_query_id,
                "last_synced_at": None,
                "is_env": True,
            }
        return None

    async def set_ibkr_config(self, chat_id: int, token: str, query_id: str) -> None:
        """Store or update IBKR credentials for chat."""
        token_clean = token.strip()
        query_id_clean = query_id.strip()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO ibkr_configs (chat_id, token, query_id, last_synced_at)
                VALUES (?, ?, ?, NULL)
                ON CONFLICT(chat_id) DO UPDATE SET
                    token = excluded.token,
                    query_id = excluded.query_id
                """,
                (chat_id, token_clean, query_id_clean),
            )
            await db.commit()

    async def remove_ibkr_config(self, chat_id: int) -> bool:
        """Remove IBKR config for chat."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM ibkr_configs WHERE chat_id = ?", (chat_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def update_ibkr_last_synced(self, chat_id: int, timestamp: Optional[str] = None) -> None:
        """Update last_synced_at timestamp."""
        now_str = timestamp or datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE ibkr_configs SET last_synced_at = ? WHERE chat_id = ?",
                (now_str, chat_id),
            )
            await db.commit()

    async def sync_ibkr_portfolio(self, chat_id: int, positions: list, replace_stocks: bool = True) -> int:
        """
        Synchronize positions from Interactive Brokers into portfolios table.
        If replace_stocks=True, existing stock positions for this chat are replaced
        while preserving any crypto holdings.
        """
        now_str = datetime.now(timezone.utc).isoformat()
        count = 0
        async with aiosqlite.connect(self.db_path) as db:
            if replace_stocks:
                await db.execute("DELETE FROM portfolios WHERE chat_id = ? AND asset_type = 'stock'", (chat_id,))

            for pos in positions:
                sym = pos.symbol.strip().upper()
                qty = float(pos.quantity)
                cost = float(pos.cost_price)
                asset_type = getattr(pos, "asset_type", "stock")

                await db.execute(
                    """
                    INSERT INTO portfolios (chat_id, symbol, asset_type, quantity, buy_price, added_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(chat_id, symbol) DO UPDATE SET
                        quantity = excluded.quantity,
                        buy_price = excluded.buy_price,
                        added_at = excluded.added_at
                    """,
                    (chat_id, sym, asset_type, qty, cost, now_str),
                )
                count += 1

            # Update last_synced_at in ibkr_configs if record exists
            await db.execute(
                "UPDATE ibkr_configs SET last_synced_at = ? WHERE chat_id = ?",
                (now_str, chat_id),
            )
            await db.commit()

        return count


db = Database()
