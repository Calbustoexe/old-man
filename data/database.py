"""
Gestion de la base de données pour Urahara.
Toutes les tables et fonctions d'accès aux données du bot passent par ici.

Persistance : la base tourne sur Turso (libSQL hébergé) via l'adaptateur
`data/db_conn.py`, qui reproduit l'API aiosqlite utilisée ci-dessous.
Voir data/db_conn.py pour la configuration (TURSO_DATABASE_URL / TURSO_AUTH_TOKEN).
"""
from data import db_conn as aiosqlite

DB_PATH = None  # non utilisé : la cible réelle vient des variables d'environnement Turso

# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS division_roles (
                number INTEGER PRIMARY KEY,
                role_id INTEGER NOT NULL UNIQUE
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS divisions (
                number INTEGER PRIMARY KEY,
                role_id INTEGER NOT NULL,
                category_id INTEGER NOT NULL,
                emoji TEXT,
                captain_id INTEGER,
                general_channel_id INTEGER,
                announce_channel_id INTEGER,
                entrants_channel_id INTEGER,
                sortants_channel_id INTEGER
            )
            """
        )
        try:
            await db.execute("ALTER TABLE divisions ADD COLUMN invite_channel_id INTEGER")
        except aiosqlite.OperationalError:
            pass

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS division_invites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                division_number INTEGER NOT NULL,
                inviter_id INTEGER NOT NULL,
                invitee_id INTEGER NOT NULL,
                message_id INTEGER,
                channel_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL
            )
            """
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS division_blocks (
                user_id INTEGER NOT NULL,
                division_number INTEGER NOT NULL,
                PRIMARY KEY (user_id, division_number)
            )
            """
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS captain_sanctions (
                user_id INTEGER PRIMARY KEY,
                no_create_until INTEGER,
                no_join_until INTEGER
            )
            """
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS division_leaves (
                user_id INTEGER NOT NULL,
                division_number INTEGER NOT NULL,
                no_rejoin_until INTEGER NOT NULL,
                PRIMARY KEY (user_id, division_number)
            )
            """
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS panel_messages (
                channel_id INTEGER NOT NULL,
                message_id INTEGER PRIMARY KEY
            )
            """
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                division_number INTEGER NOT NULL,
                applicant_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at INTEGER NOT NULL
            )
            """
        )

        # Bannissements de division (expulser/postuler/inviter bloqués)
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS division_bans (
                user_id INTEGER NOT NULL,
                division_number INTEGER NOT NULL,
                banned_until INTEGER,
                PRIMARY KEY (user_id, division_number)
            )
            """
        )

        # Mutes de division (écriture coupée dans toute la catégorie)
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS division_mutes (
                user_id INTEGER NOT NULL,
                division_number INTEGER NOT NULL,
                muted_until INTEGER,
                PRIMARY KEY (user_id, division_number)
            )
            """
        )

        # Cooldown 24h après une expulsion (postuler / être invité)
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS division_expulsions (
                user_id INTEGER NOT NULL,
                division_number INTEGER NOT NULL,
                until_ts INTEGER NOT NULL,
                PRIMARY KEY (user_id, division_number)
            )
            """
        )

        # Mandats de grade : protège un membre fraîchement promu contre un dérank
        # pendant 3 jours, uniquement à la toute première promotion à ce grade.
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS grade_mandates (
                user_id INTEGER NOT NULL,
                division_number INTEGER NOT NULL,
                grade TEXT NOT NULL,
                protected_until INTEGER NOT NULL,
                PRIMARY KEY (user_id, division_number, grade)
            )
            """
        )

        # Configuration sessions (assistant pas-à-pas, table legacy conservée)
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS config_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                division_number INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER,
                step TEXT NOT NULL,
                data TEXT NOT NULL DEFAULT '{}',
                history TEXT NOT NULL DEFAULT '[]',
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL
            )
            """
        )

        await db.commit()


# ---------------------------------------------------------------------------
# Mandats de grade
# ---------------------------------------------------------------------------

async def has_grade_mandate_record(user_id: int, division_number: int, grade: str) -> bool:
    """True si ce membre a déjà eu un mandat pour ce grade dans cette division
    (peu importe qu'il soit encore actif) : sert à savoir si c'est une 1ère promotion."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM grade_mandates WHERE user_id = ? AND division_number = ? AND grade = ?",
            (user_id, division_number, grade),
        ) as cursor:
            return await cursor.fetchone() is not None


async def set_grade_mandate(user_id: int, division_number: int, grade: str, protected_until: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO grade_mandates (user_id, division_number, grade, protected_until) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, division_number, grade) DO NOTHING",
            (user_id, division_number, grade, protected_until),
        )
        await db.commit()


async def get_active_grade_mandate(user_id: int, division_number: int, grade: str) -> aiosqlite.Row | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM grade_mandates WHERE user_id = ? AND division_number = ? AND grade = ?",
            (user_id, division_number, grade),
        ) as cursor:
            return await cursor.fetchone()


async def clear_grade_mandate(user_id: int, division_number: int, grade: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM grade_mandates WHERE user_id = ? AND division_number = ? AND grade = ?",
            (user_id, division_number, grade),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Division roles (config staff)
# ---------------------------------------------------------------------------

async def register_division_role(number: int, role_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO division_roles (number, role_id) VALUES (?, ?) "
            "ON CONFLICT(number) DO UPDATE SET role_id = excluded.role_id",
            (number, role_id),
        )
        await db.commit()


async def get_division_role_config(number: int) -> aiosqlite.Row | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM division_roles WHERE number = ?", (number,)) as cursor:
            return await cursor.fetchone()


async def get_all_division_role_configs() -> list[aiosqlite.Row]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM division_roles ORDER BY number") as cursor:
            return await cursor.fetchall()


async def find_registered_matches(role_ids: list[int]) -> list[aiosqlite.Row]:
    if not role_ids:
        return []
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        placeholders = ",".join("?" for _ in role_ids)
        async with db.execute(
            f"SELECT * FROM division_roles WHERE role_id IN ({placeholders})", role_ids
        ) as cursor:
            return await cursor.fetchall()


# ---------------------------------------------------------------------------
# Divisions - lecture
# ---------------------------------------------------------------------------

async def get_division(number: int) -> aiosqlite.Row | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM divisions WHERE number = ?", (number,)) as cursor:
            return await cursor.fetchone()


async def get_division_by_role(role_id: int) -> aiosqlite.Row | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM divisions WHERE role_id = ?", (role_id,)) as cursor:
            return await cursor.fetchone()


async def get_all_divisions() -> list[aiosqlite.Row]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM divisions ORDER BY number") as cursor:
            return await cursor.fetchall()


# ---------------------------------------------------------------------------
# Divisions - écriture
# ---------------------------------------------------------------------------

async def create_division(
    number: int, role_id: int, category_id: int, emoji: str | None, captain_id: int,
    general_channel_id: int, announce_channel_id: int, entrants_channel_id: int,
    sortants_channel_id: int, invite_channel_id: int | None = None,
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO divisions (
                number, role_id, category_id, emoji, captain_id,
                general_channel_id, announce_channel_id,
                entrants_channel_id, sortants_channel_id, invite_channel_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (number, role_id, category_id, emoji, captain_id, general_channel_id,
             announce_channel_id, entrants_channel_id, sortants_channel_id, invite_channel_id),
        )
        await db.commit()


async def delete_division(number: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM divisions WHERE number = ?", (number,))
        await db.commit()


async def set_division_invite_channel(number: int, channel_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE divisions SET invite_channel_id = ? WHERE number = ?", (channel_id, number))
        await db.commit()


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------

async def create_invite(division_number: int, inviter_id: int, invitee_id: int, channel_id: int, expires_at: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        created_at = expires_at - 180
        cursor = await db.execute(
            "INSERT INTO division_invites "
            "(division_number, inviter_id, invitee_id, channel_id, status, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
            (division_number, inviter_id, invitee_id, channel_id, created_at, expires_at),
        )
        await db.commit()
        return cursor.lastrowid


async def set_invite_message(invite_id: int, message_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE division_invites SET message_id = ? WHERE id = ?", (message_id, invite_id))
        await db.commit()


async def update_invite_status(invite_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE division_invites SET status = ? WHERE id = ?", (status, invite_id))
        await db.commit()


async def get_invite(invite_id: int) -> aiosqlite.Row | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM division_invites WHERE id = ?", (invite_id,)) as cursor:
            return await cursor.fetchone()


async def get_pending_invite(division_number: int, invitee_id: int) -> aiosqlite.Row | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM division_invites WHERE division_number = ? AND invitee_id = ? AND status = 'pending'",
            (division_number, invitee_id),
        ) as cursor:
            return await cursor.fetchone()


async def get_pending_invites() -> list[aiosqlite.Row]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM division_invites WHERE status = 'pending'") as cursor:
            return await cursor.fetchall()


async def get_expired_pending_invites(now_ts: int) -> list[aiosqlite.Row]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM division_invites WHERE status = 'pending' AND expires_at <= ?", (now_ts,)
        ) as cursor:
            return await cursor.fetchall()


# ---------------------------------------------------------------------------
# Blocages
# ---------------------------------------------------------------------------

async def block_division(user_id: int, division_number: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO division_blocks (user_id, division_number) VALUES (?, ?)",
            (user_id, division_number),
        )
        await db.commit()


async def unblock_division(user_id: int, division_number: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM division_blocks WHERE user_id = ? AND division_number = ?", (user_id, division_number)
        )
        await db.commit()


async def is_division_blocked(user_id: int, division_number: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM division_blocks WHERE user_id = ? AND division_number = ?", (user_id, division_number)
        ) as cursor:
            return await cursor.fetchone() is not None


# ---------------------------------------------------------------------------
# Sanctions capitaine / candidature générale
# ---------------------------------------------------------------------------

async def set_sanction(user_id: int, no_create_until: int | None = None, no_join_until: int | None = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO captain_sanctions (user_id, no_create_until, no_join_until) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "no_create_until = COALESCE(excluded.no_create_until, captain_sanctions.no_create_until), "
            "no_join_until = COALESCE(excluded.no_join_until, captain_sanctions.no_join_until)",
            (user_id, no_create_until, no_join_until),
        )
        await db.commit()


async def get_sanction(user_id: int) -> aiosqlite.Row | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM captain_sanctions WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchone()


async def clear_sanction(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM captain_sanctions WHERE user_id = ?", (user_id,))
        await db.commit()


# ---------------------------------------------------------------------------
# Cooldown de départ (division-quitter) - spécifique à une division
# ---------------------------------------------------------------------------

async def set_leave_cooldown(user_id: int, division_number: int, until_ts: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO division_leaves (user_id, division_number, no_rejoin_until) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, division_number) DO UPDATE SET no_rejoin_until = excluded.no_rejoin_until",
            (user_id, division_number, until_ts),
        )
        await db.commit()


async def get_leave_cooldown(user_id: int, division_number: int) -> aiosqlite.Row | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM division_leaves WHERE user_id = ? AND division_number = ?", (user_id, division_number)
        ) as cursor:
            return await cursor.fetchone()


async def clear_all_leave_cooldowns_for_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM division_leaves WHERE user_id = ?", (user_id,))
        await db.commit()


# ---------------------------------------------------------------------------
# Bans de division
# ---------------------------------------------------------------------------

async def set_division_ban(user_id: int, division_number: int, until_ts: int | None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO division_bans (user_id, division_number, banned_until) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, division_number) DO UPDATE SET banned_until = excluded.banned_until",
            (user_id, division_number, until_ts),
        )
        await db.commit()


async def get_division_ban(user_id: int, division_number: int) -> aiosqlite.Row | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM division_bans WHERE user_id = ? AND division_number = ?", (user_id, division_number)
        ) as cursor:
            return await cursor.fetchone()


async def clear_division_ban(user_id: int, division_number: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM division_bans WHERE user_id = ? AND division_number = ?", (user_id, division_number)
        )
        await db.commit()


async def clear_all_bans_for_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM division_bans WHERE user_id = ?", (user_id,))
        await db.commit()


async def get_expired_bans(now_ts: int) -> list[aiosqlite.Row]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM division_bans WHERE banned_until IS NOT NULL AND banned_until <= ?", (now_ts,)
        ) as cursor:
            return await cursor.fetchall()


# ---------------------------------------------------------------------------
# Mutes de division
# ---------------------------------------------------------------------------

async def set_division_mute(user_id: int, division_number: int, until_ts: int | None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO division_mutes (user_id, division_number, muted_until) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, division_number) DO UPDATE SET muted_until = excluded.muted_until",
            (user_id, division_number, until_ts),
        )
        await db.commit()


async def get_division_mute(user_id: int, division_number: int) -> aiosqlite.Row | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM division_mutes WHERE user_id = ? AND division_number = ?", (user_id, division_number)
        ) as cursor:
            return await cursor.fetchone()


async def clear_division_mute(user_id: int, division_number: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM division_mutes WHERE user_id = ? AND division_number = ?", (user_id, division_number)
        )
        await db.commit()


async def get_all_mutes_for_user(user_id: int) -> list[aiosqlite.Row]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM division_mutes WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchall()


async def clear_all_mutes_for_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM division_mutes WHERE user_id = ?", (user_id,))
        await db.commit()


async def get_expired_mutes(now_ts: int) -> list[aiosqlite.Row]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM division_mutes WHERE muted_until IS NOT NULL AND muted_until <= ?", (now_ts,)
        ) as cursor:
            return await cursor.fetchall()


# ---------------------------------------------------------------------------
# Expulsions (cooldown 24h : postuler / être invité)
# ---------------------------------------------------------------------------

async def set_division_expulsion(user_id: int, division_number: int, until_ts: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO division_expulsions (user_id, division_number, until_ts) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, division_number) DO UPDATE SET until_ts = excluded.until_ts",
            (user_id, division_number, until_ts),
        )
        await db.commit()


async def get_division_expulsion(user_id: int, division_number: int) -> aiosqlite.Row | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM division_expulsions WHERE user_id = ? AND division_number = ?", (user_id, division_number)
        ) as cursor:
            return await cursor.fetchone()


async def clear_all_expulsions_for_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM division_expulsions WHERE user_id = ?", (user_id,))
        await db.commit()


# ---------------------------------------------------------------------------
# Panel de candidature
# ---------------------------------------------------------------------------

async def add_panel_message(channel_id: int, message_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO panel_messages (channel_id, message_id) VALUES (?, ?)", (channel_id, message_id)
        )
        await db.commit()


async def get_panel_messages() -> list[aiosqlite.Row]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM panel_messages") as cursor:
            return await cursor.fetchall()


async def remove_panel_message(message_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM panel_messages WHERE message_id = ?", (message_id,))
        await db.commit()


# ---------------------------------------------------------------------------
# Tickets de candidature
# ---------------------------------------------------------------------------

async def create_ticket(division_number: int, applicant_id: int, channel_id: int, created_at: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO tickets (division_number, applicant_id, channel_id, status, created_at) "
            "VALUES (?, ?, ?, 'pending', ?)",
            (division_number, applicant_id, channel_id, created_at),
        )
        await db.commit()
        return cursor.lastrowid


async def set_ticket_message(ticket_id: int, message_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE tickets SET message_id = ? WHERE id = ?", (message_id, ticket_id))
        await db.commit()


async def update_ticket_status(ticket_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE tickets SET status = ? WHERE id = ?", (status, ticket_id))
        await db.commit()


async def get_ticket(ticket_id: int) -> aiosqlite.Row | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)) as cursor:
            return await cursor.fetchone()


async def get_ticket_by_channel(channel_id: int) -> aiosqlite.Row | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM tickets WHERE channel_id = ?", (channel_id,)) as cursor:
            return await cursor.fetchone()


async def get_open_ticket(division_number: int, applicant_id: int) -> aiosqlite.Row | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM tickets WHERE division_number = ? AND applicant_id = ? AND status = 'pending'",
            (division_number, applicant_id),
        ) as cursor:
            return await cursor.fetchone()


async def get_pending_tickets() -> list[aiosqlite.Row]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM tickets WHERE status = 'pending'") as cursor:
            return await cursor.fetchall()


# ---------------------------------------------------------------------------
# Configuration sessions (assistant pas-à-pas)
# ---------------------------------------------------------------------------

async def create_config_session(division_number: int, user_id: int, channel_id: int, step: str, data: dict) -> int:
    """Crée une nouvelle session de configuration."""
    import json
    import time
    async with aiosqlite.connect(DB_PATH) as db:
        now = int(time.time())
        expires_at = now + 3600  # 1 heure d'expiration
        cursor = await db.execute(
            """
            INSERT INTO config_sessions (division_number, user_id, channel_id, step, data, history, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (division_number, user_id, channel_id, step, json.dumps(data), json.dumps([]), now, expires_at),
        )
        await db.commit()
        return cursor.lastrowid


async def get_config_session(session_id: int) -> dict | None:
    """Récupère une session de configuration par ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM config_sessions WHERE id = ?", (session_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_config_session_by_message(message_id: int) -> dict | None:
    """Récupère une session de configuration par message ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM config_sessions WHERE message_id = ?", (message_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_config_session_awaiting_image(channel_id: int, user_id: int) -> dict | None:
    """Récupère une session en attente d'image (pp ou banner)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM config_sessions WHERE channel_id = ? AND user_id = ? AND step IN ('pp', 'banner', 'badge_image')",
            (channel_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def update_config_session(session_id: int, step: str = None, data: dict = None, history: list = None):
    """Met à jour une session de configuration."""
    import json
    async with aiosqlite.connect(DB_PATH) as db:
        updates = {}
        if step is not None:
            updates["step"] = step
        if data is not None:
            updates["data"] = json.dumps(data)
        if history is not None:
            updates["history"] = json.dumps(history)
        
        if not updates:
            return
        
        cols = ", ".join(f"{k} = ?" for k in updates.keys())
        await db.execute(
            f"UPDATE config_sessions SET {cols} WHERE id = ?",
            (*updates.values(), session_id),
        )
        await db.commit()


async def set_config_session_message(session_id: int, message_id: int):
    """Définit le message ID d'une session de configuration."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE config_sessions SET message_id = ? WHERE id = ?",
            (message_id, session_id),
        )
        await db.commit()


async def delete_config_session(session_id: int):
    """Supprime une session de configuration."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM config_sessions WHERE id = ?", (session_id,))
        await db.commit()


async def cleanup_expired_config_sessions():
    """Nettoie les sessions expirées."""
    import time
    async with aiosqlite.connect(DB_PATH) as db:
        now = int(time.time())
        await db.execute("DELETE FROM config_sessions WHERE expires_at <= ?", (now,))
        await db.commit()