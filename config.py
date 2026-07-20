import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


def parse_targets(raw: str) -> list[tuple[int, int | None]]:
    """Parse TARGET_CHAT_ID env var format: CHAT_ID or CHAT_ID:THREAD_ID separated by commas."""
    result = []
    if not raw.strip():
        return result
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":", 1)
        result.append((int(parts[0]), int(parts[1]) if len(parts) == 2 else None))
    return result


@dataclass
class Config:
    telegram_token: str = field(default_factory=lambda: os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    cmc_api_key: str = field(default_factory=lambda: os.environ.get("CMC_API_KEY", ""))
    target_chat_raw: str = field(default_factory=lambda: os.environ.get("TARGET_CHAT_ID", "").strip())
    spike_threshold_pct: float = field(default_factory=lambda: float(os.environ.get("SPIKE_THRESHOLD_PCT", "5.0")))
    spike_cooldown_hours: float = field(default_factory=lambda: float(os.environ.get("SPIKE_COOLDOWN_HOURS", "2.0")))
    hourly_swing_pct: float = field(default_factory=lambda: float(os.environ.get("HOURLY_SWING_PCT", "5.0")))
    hourly_swing_tag: str = field(default_factory=lambda: os.environ.get("HOURLY_SWING_TAG", "").strip())
    
    # AI Settings
    ai_provider: str = field(default_factory=lambda: os.environ.get("AI_PROVIDER", "gemini").lower())
    ai_api_key: str = field(default_factory=lambda: os.environ.get("AI_API_KEY", "").strip())
    ai_model: str = field(default_factory=lambda: os.environ.get("AI_MODEL", "").strip())

    # DB Setting
    db_path: Path = field(default_factory=lambda: Path(os.environ.get("DATABASE_PATH", "investor_bot.db")))

    @property
    def targets(self) -> list[tuple[int, int | None]]:
        return parse_targets(self.target_chat_raw)

    @property
    def target_chat_ids(self) -> set[int]:
        return {chat_id for chat_id, _ in self.targets}


config = Config()
