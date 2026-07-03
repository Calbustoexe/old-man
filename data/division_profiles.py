"""
Persistance des profils de division, des sessions d'interaction (/division-config)
et de l'historique des grades (vice-capitaine / lieutenant) pour /division-profil.

Persistance : base Turso (libSQL) via l'adaptateur data/db_conn.py.
"""
import json
from data import db_conn as aiosqlite

DB_PATH = None  # non utilisé : la cible réelle vient des variables d'environnement Turso


async def _get_table_columns(db: aiosqlite.Connection, table: str) -> set[str]:
    async with db.execute(f"PRAGMA table_info({table})") as cursor:
        rows = await cursor.fetchall()
    return {row[1] for row in rows}


async def _rename_column(db: aiosqlite.Connection, table: str, old_name: str, new_name: str):
    try:
        await db.execute(f"ALTER TABLE {table} RENAME COLUMN {old_name} TO {new_name}")
    except aiosqlite.OperationalError:
        pass


async def _add_column_if_missing(db: aiosqlite.Connection, table: str, column_definition: str):
    column_name = column_definition.split()[0]
    columns = await _get_table_columns(db, table)
    if column_name not in columns:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column_definition}")


def _normalize_profile_row(profile: aiosqlite.Row | dict | None) -> dict | None:
    if profile is None:
        return None
    data = dict(profile)

    if "pp_url" in data:
        data["pfp_url"] = data.pop("pp_url")
    if "role_color" in data:
        data["color_primary"] = data.pop("role_color")
    if "role_color2" in data:
        data["color_secondary"] = data.pop("role_color2")
    if "badge_icon_url" in data:
        data["badge_source"] = data.pop("badge_icon_url")
    if "setup_complete" in data:
        data["setup_done"] = data.pop("setup_complete")
    if "visibility" not in data:
        data["visibility"] = "public"
    if "color_cooldown_until" not in data:
        data["color_cooldown_until"] = None
    if "badge_cooldown_until" not in data:
        data["badge_cooldown_until"] = None
    return data


async def init_profiles_tables():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS division_profiles (
                number INTEGER PRIMARY KEY,
                custom_name TEXT,
                description TEXT,
                min_age INTEGER,
                reglement TEXT,
                pfp_url TEXT,
                banner_url TEXT,
                color_primary TEXT,
                color_secondary TEXT,
                badge_source TEXT,
                setup_done INTEGER NOT NULL DEFAULT 0,
                color_cooldown_until INTEGER,
                badge_cooldown_until INTEGER,
                visibility TEXT NOT NULL DEFAULT 'public'
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS division_config_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                division_number INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER,
                kind TEXT NOT NULL,
                step TEXT NOT NULL,
                data TEXT NOT NULL DEFAULT '{}',
                history TEXT NOT NULL DEFAULT '[]',
                created_at INTEGER NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS division_grade_history (
                user_id INTEGER NOT NULL,
                division_number INTEGER NOT NULL,
                grade TEXT NOT NULL,
                granted_at INTEGER NOT NULL,
                PRIMARY KEY (user_id, division_number, grade)
            )
            """
        )

        existing = await _get_table_columns(db, "division_profiles")
        if "pp_url" in existing and "pfp_url" not in existing:
            await _rename_column(db, "division_profiles", "pp_url", "pfp_url")
        if "role_color" in existing and "color_primary" not in existing:
            await _rename_column(db, "division_profiles", "role_color", "color_primary")
        if "role_color2" in existing and "color_secondary" not in existing:
            await _rename_column(db, "division_profiles", "role_color2", "color_secondary")
        if "badge_icon_url" in existing and "badge_source" not in existing:
            await _rename_column(db, "division_profiles", "badge_icon_url", "badge_source")
        if "setup_complete" in existing and "setup_done" not in existing:
            await _rename_column(db, "division_profiles", "setup_complete", "setup_done")

        await _add_column_if_missing(db, "division_profiles", "pfp_url TEXT")
        await _add_column_if_missing(db, "division_profiles", "banner_url TEXT")
        await _add_column_if_missing(db, "division_profiles", "color_primary TEXT")
        await _add_column_if_missing(db, "division_profiles", "color_secondary TEXT")
        await _add_column_if_missing(db, "division_profiles", "badge_source TEXT")
        await _add_column_if_missing(db, "division_profiles", "setup_done INTEGER NOT NULL DEFAULT 0")
        await _add_column_if_missing(db, "division_profiles", "color_cooldown_until INTEGER")
        await _add_column_if_missing(db, "division_profiles", "badge_cooldown_until INTEGER")
        await _add_column_if_missing(db, "division_profiles", "visibility TEXT NOT NULL DEFAULT 'public'")
        await db.commit()


async def init_profiles_table():
    await init_profiles_tables()


# ---------------------------------------------------------------------------
# Profils
# ---------------------------------------------------------------------------

async def get_profile(number: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM division_profiles WHERE number = ?", (number,)) as cur:
            profile = await cur.fetchone()
    return _normalize_profile_row(profile)


async def ensure_profile(number: int) -> dict:
    profile = await get_profile(number)
    if profile:
        return profile
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO division_profiles (number) VALUES (?)", (number,))
        await db.commit()
    return await get_profile(number)


async def update_profile_fields(number: int, **fields):
    if not fields:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cols = ", ".join(f"{k} = ?" for k in fields)
        await db.execute(f"UPDATE division_profiles SET {cols} WHERE number = ?", (*fields.values(), number))
        await db.commit()


async def reset_profile(number: int):
    await ensure_profile(number)
    await update_profile_fields(
        number,
        custom_name=None,
        description=None,
        min_age=None,
        reglement=None,
        pfp_url=None,
        banner_url=None,
        color_primary=None,
        color_secondary=None,
        badge_source=None,
        setup_done=0,
        color_cooldown_until=None,
        badge_cooldown_until=None,
        visibility="public",
    )


# ---------------------------------------------------------------------------
# Sessions d'interaction (une seule active par utilisateur)
# ---------------------------------------------------------------------------

async def create_session(division_number: int, user_id: int, channel_id: int, kind: str, step: str, data: dict | None = None) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM division_config_sessions WHERE user_id = ?", (user_id,))
        cursor = await db.execute(
            "INSERT INTO division_config_sessions (division_number, user_id, channel_id, kind, step, data, history, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, '[]', strftime('%s','now'))",
            (division_number, user_id, channel_id, kind, step, json.dumps(data or {})),
        )
        await db.commit()
        return cursor.lastrowid


async def set_session_message(session_id: int, message_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE division_config_sessions SET message_id = ? WHERE id = ?", (message_id, session_id))
        await db.commit()


async def update_session(session_id: int, **fields):
    if not fields:
        return
    if "data" in fields:
        fields["data"] = json.dumps(fields["data"])
    if "history" in fields:
        fields["history"] = json.dumps(fields["history"])
    async with aiosqlite.connect(DB_PATH) as db:
        cols = ", ".join(f"{k} = ?" for k in fields)
        await db.execute(f"UPDATE division_config_sessions SET {cols} WHERE id = ?", (*fields.values(), session_id))
        await db.commit()


async def get_session(session_id: int) -> aiosqlite.Row | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM division_config_sessions WHERE id = ?", (session_id,)) as cur:
            return await cur.fetchone()


async def get_session_by_message(message_id: int) -> aiosqlite.Row | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM division_config_sessions WHERE message_id = ?", (message_id,)) as cur:
            return await cur.fetchone()


async def get_session_by_user_channel(user_id: int, channel_id: int) -> aiosqlite.Row | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM division_config_sessions WHERE user_id = ? AND channel_id = ?", (user_id, channel_id)
        ) as cur:
            return await cur.fetchone()


async def get_all_sessions() -> list[aiosqlite.Row]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM division_config_sessions") as cur:
            return await cur.fetchall()


async def delete_session(session_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM division_config_sessions WHERE id = ?", (session_id,))
        await db.commit()


def session_data(session: aiosqlite.Row) -> dict:
    return json.loads(session["data"])


def session_history(session: aiosqlite.Row) -> list:
    return json.loads(session["history"])


# ---------------------------------------------------------------------------
# Historique des grades (vice-capitaine / lieutenant)
# ---------------------------------------------------------------------------

async def set_grade_grant(user_id: int, division_number: int, grade: str, granted_at: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO division_grade_history (user_id, division_number, grade, granted_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, division_number, grade) DO UPDATE SET granted_at = excluded.granted_at",
            (user_id, division_number, grade, granted_at),
        )
        await db.commit()


async def get_grade_grant(user_id: int, division_number: int, grade: str) -> aiosqlite.Row | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM division_grade_history WHERE user_id = ? AND division_number = ? AND grade = ?",
            (user_id, division_number, grade),
        ) as cur:
            return await cur.fetchone()