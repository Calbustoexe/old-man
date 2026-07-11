"""
Stockage clé-valeur générique sur Turso, utilisé pour remplacer les anciens
fichiers JSON locaux (data/storage/*.json, giveaways_data.json) qui étaient
perdus à chaque redéploiement Railway (disque éphémère, pas de volume monté).

Chaque "domaine" (permissions, fun_settings, vocban, vocprotect, ownerban,
tools_settings, warns, giveaways...) est stocké comme un blob JSON sous une
clé (namespace, key) dans une seule table `kv_store`. Ça permet de migrer
toute la persistance existante vers Turso sans changer la structure de
données manipulée par chaque cog (toujours des dicts Python identiques).
"""
from __future__ import annotations

import json

from data import db_conn as aiosqlite

DB_PATH = None  # non utilisé : la cible réelle vient des variables d'environnement Turso


async def init_kv_table():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS kv_store (
                namespace TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY (namespace, key)
            )
            """
        )


async def kv_get(namespace: str, key: str, default):
    """Retourne le dict/valeur JSON stocké, ou `default` si absent/corrompu."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT value FROM kv_store WHERE namespace = ? AND key = ?",
            (namespace, key),
        ) as cursor:
            row = await cursor.fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return default


async def kv_set(namespace: str, key: str, value) -> None:
    payload = json.dumps(value, ensure_ascii=False)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO kv_store (namespace, key, value) VALUES (?, ?, ?)
            ON CONFLICT (namespace, key) DO UPDATE SET value = excluded.value
            """,
            (namespace, key, payload),
        )