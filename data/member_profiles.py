"""
Member profile persistence layer.
Handles creation, retrieval, updating, and deletion of member profiles.
"""

import aiosqlite
import time
from pathlib import Path


DB_PATH = Path(__file__).parent / "urahara.db"


async def _get_table_columns(table_name: str) -> list[str]:
    """Get list of column names for a table."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(f"PRAGMA table_info({table_name})")
        rows = await cursor.fetchall()
        return [row[1] for row in rows]


async def ensure_member_profile_table():
    """Ensure member_profiles table exists with proper schema."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS member_profiles (
                member_id INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                pfp_url TEXT,
                banner_url TEXT,
                description TEXT DEFAULT '',
                color_primary TEXT,
                color_secondary TEXT,
                visibility TEXT DEFAULT 'public',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)
        # Sessions persistantes pour /config-profil : survit à un redémarrage du bot,
        # une seule session active par utilisateur (comme division_config_sessions).
        await db.execute("""
            CREATE TABLE IF NOT EXISTS member_config_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER,
                panel_message_id INTEGER,
                step TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
        """)
        await db.commit()


# ---------------------------------------------------------------------------
# Sessions de configuration (une seule active par utilisateur)
# ---------------------------------------------------------------------------

async def create_config_session(user_id: int, guild_id: int, channel_id: int, step: str, panel_message_id: int | None = None) -> int:
    """Crée une session (supprime l'éventuelle session précédente du même utilisateur)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM member_config_sessions WHERE user_id = ? AND guild_id = ?", (user_id, guild_id))
        cursor = await db.execute(
            "INSERT INTO member_config_sessions (user_id, guild_id, channel_id, panel_message_id, step, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, guild_id, channel_id, panel_message_id, step, int(time.time())),
        )
        await db.commit()
        return cursor.lastrowid


async def set_config_session_message(session_id: int, message_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE member_config_sessions SET message_id = ? WHERE id = ?", (message_id, session_id))
        await db.commit()


async def get_config_session_by_user_channel(user_id: int, channel_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM member_config_sessions WHERE user_id = ? AND channel_id = ?", (user_id, channel_id)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_all_config_sessions() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM member_config_sessions")
        return [dict(r) for r in await cursor.fetchall()]


async def delete_config_session(session_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM member_config_sessions WHERE id = ?", (session_id,))
        await db.commit()


async def _normalize_profile_row(row) -> dict:
    """Normalize member profile row to dict with proper field names."""
    if row is None:
        return None
    return {
        "member_id": row[0],
        "guild_id": row[1],
        "pfp_url": row[2],
        "banner_url": row[3],
        "description": row[4],
        "color_primary": row[5],
        "color_secondary": row[6],
        "visibility": row[7],
        "created_at": row[8],
        "updated_at": row[9],
    }


async def ensure_profile(guild_id: int, member_id: int) -> dict:
    """Ensure a member profile exists; create if not."""
    existing = await get_profile(guild_id, member_id)
    if existing:
        return existing
    
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO member_profiles 
               (member_id, guild_id, description, visibility, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (member_id, guild_id, "", "public", now, now),
        )
        await db.commit()
    
    return await get_profile(guild_id, member_id)


async def get_profile(guild_id: int, member_id: int) -> dict | None:
    """Get member profile."""
    await ensure_member_profile_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM member_profiles WHERE member_id = ? AND guild_id = ?",
            (member_id, guild_id),
        )
        row = await cursor.fetchone()
    return await _normalize_profile_row(row) if row else None


async def update_profile(guild_id: int, member_id: int, **kwargs) -> dict:
    """Update member profile fields."""
    profile = await ensure_profile(guild_id, member_id)
    
    now = int(time.time())
    kwargs["updated_at"] = now
    
    set_clause = ", ".join(f"{k} = ?" for k in kwargs.keys())
    values = list(kwargs.values()) + [member_id, guild_id]
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE member_profiles SET {set_clause} WHERE member_id = ? AND guild_id = ?",
            values,
        )
        await db.commit()
    
    return await get_profile(guild_id, member_id)


async def update_pfp(guild_id: int, member_id: int, url: str | None):
    """Update member profile picture."""
    return await update_profile(guild_id, member_id, pfp_url=url)


async def update_banner(guild_id: int, member_id: int, url: str | None):
    """Update member profile banner."""
    return await update_profile(guild_id, member_id, banner_url=url)


async def update_description(guild_id: int, member_id: int, description: str):
    """Update member profile description."""
    return await update_profile(guild_id, member_id, description=description)


async def update_color(guild_id: int, member_id: int, primary: str, secondary: str | None = None):
    """Update member profile colors."""
    return await update_profile(guild_id, member_id, color_primary=primary, color_secondary=secondary)


async def update_visibility(guild_id: int, member_id: int, visibility: str):
    """Update member profile visibility (public/private)."""
    return await update_profile(guild_id, member_id, visibility=visibility)


async def reset_profile(guild_id: int, member_id: int):
    """Reset member profile to defaults."""
    now = int(time.time())
    return await update_profile(
        guild_id, 
        member_id, 
        pfp_url=None,
        banner_url=None,
        description="",
        color_primary=None,
        color_secondary=None,
        visibility="public",
    )