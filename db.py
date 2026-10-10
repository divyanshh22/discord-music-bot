"""PostgreSQL storage for CASE. Everything here is optional. Without a DATABASE_URL the bot runs exactly like before and /history simply reports that no databas..."""

from __future__ import annotations

import logging

import asyncpg

log = logging.getLogger("case")

_pool: asyncpg.Pool | None = None


SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS play_history (
        id           BIGSERIAL PRIMARY KEY,
        guild_id     BIGINT  NOT NULL,
        channel_id   BIGINT,
        title        TEXT    NOT NULL,
        url          TEXT,
        requested_by TEXT    NOT NULL DEFAULT 'someone',
        duration     INTEGER,
        played_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS play_history_guild_time
        ON play_history (guild_id, played_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS liked_tracks (
        id         BIGSERIAL PRIMARY KEY,
        guild_id   BIGINT  NOT NULL,
        user_id    BIGINT  NOT NULL,
        identity   TEXT    NOT NULL,
        title      TEXT    NOT NULL,
        url        TEXT,
        artist     TEXT,
        duration   INTEGER,
        added_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (guild_id, user_id, identity)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS liked_tracks_guild_user
        ON liked_tracks (guild_id, user_id, added_at DESC)
    """,
)


def normalise_dsn(dsn: str) -> str:
    """asyncpg only speaks postgresql:// but Render hands out postgres://."""
    if dsn.startswith("postgres://"):
        return "postgresql://" + dsn[len("postgres://"):]
    return dsn


async def connect(dsn: str | None) -> bool:
    """Open the pool and create the tables."""
    global _pool

    if not dsn:
        log.info("DATABASE_URL not set - /history will be unavailable")
        return False

    try:

        _pool = await asyncpg.create_pool(
            normalise_dsn(dsn), min_size=1, max_size=5, timeout=10
        )
        async with _pool.acquire() as conn:
            for statement in SCHEMA:
                await conn.execute(statement)
    except Exception:
        log.exception("could not reach PostgreSQL - continuing without a database")
        _pool = None
        return False

    log.info("database ready")
    return True


async def close() -> None:
    """Drop the pool. Safe to call when there was never one."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def available() -> bool:
    return _pool is not None


async def log_play(
    guild_id: int,
    title: str,
    *,
    channel_id: int | None = None,
    url: str | None = None,
    requested_by: str = "someone",
    duration: int | None = None,
) -> None:
    """Remember one track that played. Swallows its own errors. Losing a history row is never worth interrupting playback for, so any failure here is logged and for..."""
    if _pool is None:
        return

    try:
        await _pool.execute(
            """
            INSERT INTO play_history (guild_id, channel_id, title, url, requested_by, duration)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            guild_id,
            channel_id,
            title,
            url,
            requested_by,
            duration,
        )
    except Exception:
        log.warning("could not record %r in play history", title, exc_info=True)


async def recent_plays(guild_id: int, limit: int = 10) -> list[asyncpg.Record]:
    """Newest-first history for one server. Empty list when no database."""
    if _pool is None:
        return []

    try:
        return await _pool.fetch(
            """
            SELECT title, requested_by, duration, played_at
            FROM play_history
            WHERE guild_id = $1
            ORDER BY played_at DESC
            LIMIT $2
            """,
            guild_id,
            limit,
        )
    except Exception:
        log.warning("could not read play history", exc_info=True)
        return []


async def save_like(
    guild_id: int,
    user_id: int,
    identity: str,
    *,
    title: str,
    url: str | None = None,
    artist: str | None = None,
    duration: int | None = None,
) -> None:
    """Remember (or refresh) a liked track for one user in one server."""
    if _pool is None:
        return

    try:
        await _pool.execute(
            """
            INSERT INTO liked_tracks (guild_id, user_id, identity, title, url, artist, duration)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (guild_id, user_id, identity) DO UPDATE
            SET title = EXCLUDED.title,
                url = EXCLUDED.url,
                artist = EXCLUDED.artist,
                duration = EXCLUDED.duration
            """,
            guild_id,
            user_id,
            identity,
            title,
            url,
            artist,
            duration,
        )
    except Exception:
        log.warning("could not save like %r", title, exc_info=True)


async def remove_like(guild_id: int, user_id: int, identity: str) -> None:
    """Forget a liked track for one user in one server."""
    if _pool is None:
        return

    try:
        await _pool.execute(
            "DELETE FROM liked_tracks WHERE guild_id = $1 AND user_id = $2 AND identity = $3",
            guild_id,
            user_id,
            identity,
        )
    except Exception:
        log.warning("could not remove like %r", identity, exc_info=True)


async def user_likes(
    guild_id: int, user_id: int, limit: int = 100
) -> list[asyncpg.Record]:
    """Newest-first liked tracks for one user. Empty list when no database."""
    if _pool is None:
        return []

    try:
        return await _pool.fetch(
            """
            SELECT identity, title, url, artist, duration, added_at
            FROM liked_tracks
            WHERE guild_id = $1 AND user_id = $2
            ORDER BY added_at DESC
            LIMIT $3
            """,
            guild_id,
            user_id,
            limit,
        )
    except Exception:
        log.warning("could not read liked tracks", exc_info=True)
        return []


async def guild_likes(guild_id: int, limit: int = 1000) -> list[asyncpg.Record]:
    """Every liked track a server knows about (used for the shared card heart)."""
    if _pool is None:
        return []

    try:
        return await _pool.fetch(
            """
            SELECT identity, user_id, title, url, artist, duration, added_at
            FROM liked_tracks
            WHERE guild_id = $1
            LIMIT $2
            """,
            guild_id,
            limit,
        )
    except Exception:
        log.warning("could not read server likes", exc_info=True)
        return []
