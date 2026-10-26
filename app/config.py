"""Runtime configuration, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _ids(raw: str) -> set[int]:
    try:
        return {int(x) for x in raw.split(",") if x.strip()}
    except ValueError as e:
        raise SystemExit(f"ALLOWED_USER_IDS must be comma-separated integers: {e}") from e


@dataclass
class Settings:
    anthropic_api_key: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))
    model: str = field(default_factory=lambda: os.environ.get("MODEL", "claude-sonnet-5-5"))
    telegram_token: str = field(default_factory=lambda: os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    allowed_user_ids: set[int] = field(
        default_factory=lambda: _ids(os.environ.get("ALLOWED_USER_IDS", ""))
    )
    database_url: str = field(default_factory=lambda: os.environ.get("DATABASE_URL", ""))
    timezone: str = field(default_factory=lambda: os.environ.get("TIMEZONE", "Asia/Kolkata"))
    history_turns: int = 20
    max_tool_rounds: int = 8

    def validate(self) -> None:
        missing = [
            name
            for name, val in (
                ("ANTHROPIC_API_KEY", self.anthropic_api_key),
                ("TELEGRAM_BOT_TOKEN", self.telegram_token),
            )
            if not val
        ]
        if missing:
            raise SystemExit(f"Missing required environment variables: {', '.join(missing)}")
        if not self.allowed_user_ids:
            raise SystemExit("Set ALLOWED_USER_IDS, otherwise anyone could read your mail.")


def load() -> Settings:
    return Settings()
