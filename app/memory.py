"""Persistent memory: short-term chat history and long-term facts.

``PostgresStore`` is the production backend. ``InMemoryStore`` implements the
same interface for tests and for running without a database.

Both stores are synchronous. Callers on the event loop should go through
``asyncio.to_thread`` so a slow query never blocks other chats.
"""

from __future__ import annotations

import logging
from typing import Protocol

log = logging.getLogger(__name__)


class Store(Protocol):
    def add_exchange(self, chat_id: int, user_text: str, assistant_text: str) -> None: ...
    def recent_messages(self, chat_id: int, limit: int) -> list[dict[str, str]]: ...
    def remember(self, chat_id: int, key: str, value: str) -> None: ...
    def forget(self, chat_id: int, key: str) -> bool: ...
    def facts(self, chat_id: int) -> dict[str, str]: ...
    def close(self) -> None: ...


class InMemoryStore:
    def __init__(self) -> None:
        self._msgs: dict[int, list[dict[str, str]]] = {}
        self._facts: dict[int, dict[str, str]] = {}

    def add_exchange(self, chat_id: int, user_text: str, assistant_text: str) -> None:
        msgs = self._msgs.setdefault(chat_id, [])
        msgs.append({"role": "user", "content": user_text})
        msgs.append({"role": "assistant", "content": assistant_text})

    def recent_messages(self, chat_id: int, limit: int) -> list[dict[str, str]]:
        return list(self._msgs.get(chat_id, [])[-limit:])

    def remember(self, chat_id: int, key: str, value: str) -> None:
        self._facts.setdefault(chat_id, {})[key] = value

    def forget(self, chat_id: int, key: str) -> bool:
        return self._facts.get(chat_id, {}).pop(key, None) is not None

    def facts(self, chat_id: int) -> dict[str, str]:
        return dict(self._facts.get(chat_id, {}))

    def close(self) -> None:
        pass


SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id         BIGSERIAL PRIMARY KEY,
    chat_id    BIGINT      NOT NULL,
    role       TEXT        NOT NULL CHECK (role IN ('user', 'assistant')),
    content    TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS messages_chat_idx ON messages (chat_id, id DESC);

CREATE TABLE IF NOT EXISTS facts (
    chat_id    BIGINT      NOT NULL,
    key        TEXT        NOT NULL,
    value      TEXT        NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (chat_id, key)
);
"""


class PostgresStore:
    """Postgres-backed store using a small connection pool (thread-safe)."""

    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 4) -> None:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        self._pool = ConnectionPool(
            dsn,
            min_size=min_size,
            max_size=max_size,
            kwargs={"row_factory": dict_row},
            open=True,
        )
        self._pool.wait(timeout=15)
        with self._pool.connection() as conn:
            conn.execute(SCHEMA)
        log.info("Postgres store ready")

    def add_exchange(self, chat_id: int, user_text: str, assistant_text: str) -> None:
        # One transaction so a user message is never stored without its reply.
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO messages (chat_id, role, content) "
                "VALUES (%s, 'user', %s), (%s, 'assistant', %s)",
                (chat_id, user_text, chat_id, assistant_text),
            )

    def recent_messages(self, chat_id: int, limit: int) -> list[dict[str, str]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE chat_id = %s ORDER BY id DESC LIMIT %s",
                (chat_id, limit),
            ).fetchall()
        return list(reversed(rows))

    def remember(self, chat_id: int, key: str, value: str) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                """INSERT INTO facts (chat_id, key, value) VALUES (%s, %s, %s)
                   ON CONFLICT (chat_id, key)
                   DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
                (chat_id, key, value),
            )

    def forget(self, chat_id: int, key: str) -> bool:
        with self._pool.connection() as conn:
            cur = conn.execute("DELETE FROM facts WHERE chat_id = %s AND key = %s", (chat_id, key))
            return cur.rowcount > 0

    def facts(self, chat_id: int) -> dict[str, str]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT key, value FROM facts WHERE chat_id = %s ORDER BY key", (chat_id,)
            ).fetchall()
        return {r["key"]: r["value"] for r in rows}

    def close(self) -> None:
        self._pool.close()


def make_store(database_url: str) -> Store:
    if database_url:
        return PostgresStore(database_url)
    log.warning("DATABASE_URL not set: using in-memory store, nothing will persist")
    return InMemoryStore()
