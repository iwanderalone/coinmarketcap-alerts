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


db = Database()
