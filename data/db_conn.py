"""
Adaptateur de compatibilité aiosqlite -> Turso (libSQL).

Objectif : garder EXACTEMENT la même façon d'écrire le code partout ailleurs
(`async with aiosqlite.connect(...) as db: ...`, `db.row_factory = aiosqlite.Row`,
`await db.execute(...)`, `cursor.fetchone()`, `cursor.fetchall()`, `db.commit()`)
en tapant en réalité sur une base Turso distante via `libsql_client`.

Utilisation dans les fichiers data (database.py, division_profiles.py, member_profiles.py) :

    from data import db_conn as aiosqlite
    DB_PATH = None  # plus utilisé, l'URL/token viennent des variables d'environnement

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM table WHERE x = ?", (val,)) as cursor:
            row = await cursor.fetchone()

Tout le reste du code n'a pas besoin de changer.

Configuration (variables d'environnement) :
    TURSO_DATABASE_URL   ex: libsql://yamamotobot-tonusername.turso.io
    TURSO_AUTH_TOKEN     le token généré via `turso db tokens create yamamotobot`

Si ces variables ne sont pas définies, on retombe automatiquement sur un fichier
SQLite local (utile pour développer/tester en local sans dépendre de Turso).
"""
from __future__ import annotations

import os
import pathlib
import sqlite3
from typing import Any, Iterable, Sequence

import libsql_client

# Alias pour coller à l'API aiosqlite (aiosqlite.Row == sqlite3.Row côté usage: accès par nom de colonne)
Row = sqlite3.Row

def _normalize_url(url: str) -> str:
    """Force le protocole HTTP (au lieu de libsql:// -> WebSocket) car le handshake
    WebSocket échoue dans certains environnements conteneurisés (ex: Railway),
    avec une erreur `WSServerHandshakeError: 400, Invalid response status`.
    Le mode HTTP est parfaitement supporté par Turso et évite ce problème."""
    if url.startswith("libsql://"):
        return "https://" + url[len("libsql://"):]
    return url


TURSO_DATABASE_URL = os.getenv("TURSO_DATABASE_URL")
if TURSO_DATABASE_URL:
    TURSO_DATABASE_URL = _normalize_url(TURSO_DATABASE_URL)
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN")

# Fallback local (dev sans Turso) : fichier sqlite classique à côté de ce module.
_LOCAL_FALLBACK_PATH = pathlib.Path(__file__).parent / "urahara.db"


class OperationalError(Exception):
    """Miroir de aiosqlite.OperationalError, levée notamment sur un ALTER TABLE
    qui ajoute une colonne déjà existante (pattern utilisé dans database.py)."""
    pass


def _to_libsql_url(local_path: pathlib.Path) -> str:
    return f"file:{local_path}"


class _Cursor:
    """Reproduit l'API d'un curseur aiosqlite : usage en `async with` + fetchone/fetchall,
    et lecture directe `await db.execute(...)` sans fetch (juste pour writes)."""

    def __init__(self, result_set: libsql_client.ResultSet, row_factory):
        self._rows = list(result_set.rows) if result_set is not None else []
        self._columns = list(result_set.columns) if result_set is not None else []
        self.lastrowid = None  # rempli explicitement par _ExecuteAwaitable._run après un INSERT
        self._row_factory = row_factory
        self._index = 0

    def _wrap(self, raw_row):
        if raw_row is None:
            return None
        if self._row_factory is Row:
            # Construit un sqlite3.Row-like : on simule via un dict-accessible objet.
            return _RowProxy(self._columns, raw_row)
        return tuple(raw_row)

    async def fetchone(self):
        if self._index >= len(self._rows):
            return None
        raw = self._rows[self._index]
        self._index += 1
        return self._wrap(raw)

    async def fetchall(self):
        remaining = self._rows[self._index:]
        self._index = len(self._rows)
        return [self._wrap(r) for r in remaining]

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        row = await self.fetchone()
        if row is None:
            raise StopAsyncIteration
        return row


class _RowProxy:
    """Objet accessible par index ET par nom de colonne, comme sqlite3.Row,
    et convertible en dict via dict(row)."""

    def __init__(self, columns: list[str], values: Sequence[Any]):
        self._columns = columns
        self._values = list(values)

    def __getitem__(self, key):
        if isinstance(key, str):
            return self._values[self._columns.index(key)]
        return self._values[key]

    def keys(self):
        return list(self._columns)

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __repr__(self):
        return f"<Row {dict(zip(self._columns, self._values))}>"


class _ExecuteAwaitable:
    """Résultat de db.execute(sql, params) : utilisable comme
    `cursor = await db.execute(...)` OU `async with db.execute(...) as cursor:`,
    exactement comme aiosqlite."""

    def __init__(self, client: libsql_client.Client, row_factory, sql: str, params: list):
        self._client = client
        self._row_factory = row_factory
        self._sql = sql
        self._params = params
        self._cursor: _Cursor | None = None

    async def _run(self) -> _Cursor:
        if self._cursor is not None:
            return self._cursor

        stripped = self._sql.lstrip().upper()
        needs_rowid = stripped.startswith("INSERT")

        try:
            if needs_rowid:
                # CRITIQUE : en mode HTTP, deux appels execute() séparés peuvent atterrir
                # sur des connexions logiques différentes, et last_insert_rowid() renverrait
                # alors une valeur fausse ou nulle. batch() garantit que l'INSERT et le
                # SELECT last_insert_rowid() s'exécutent sur LA MÊME connexion logique.
                statements = [
                    libsql_client.Statement(self._sql, self._params),
                    libsql_client.Statement("SELECT last_insert_rowid()"),
                ]
                results = await self._client.batch(statements)
                result, id_result = results[0], results[1]
                lastrowid = id_result.rows[0][0] if id_result.rows else None
            else:
                result = await self._client.execute(self._sql, self._params)
                lastrowid = None
        except libsql_client.LibsqlError as e:
            msg = str(e)
            # ALTER TABLE ... ADD COLUMN sur une colonne déjà existante -> comportement
            # attendu comme aiosqlite.OperationalError (voir try/except dans database.py)
            if "duplicate column name" in msg.lower() or "already exists" in msg.lower():
                raise OperationalError(msg) from e
            raise
        except KeyError as e:
            # En mode HTTP, libsql_client attend une clé "result" dans la réponse JSON.
            # Quand la requête échoue côté serveur (ex: ALTER TABLE ADD COLUMN sur une
            # colonne déjà existante), l'API renvoie une erreur sans cette clé, et
            # libsql_client lève un KeyError('result') au lieu d'une LibsqlError propre.
            # On le traite comme une OperationalError générique pour laisser le code
            # appelant (try/except aiosqlite.OperationalError dans database.py) gérer
            # ce cas normalement, plutôt que de faire planter tout le bot au démarrage.
            raise OperationalError(f"Requête échouée côté serveur ({e})") from e

        self._cursor = _Cursor(result, self._row_factory)
        if needs_rowid:
            self._cursor.lastrowid = lastrowid
        return self._cursor

    def __await__(self):
        return self._run().__await__()

    async def __aenter__(self) -> _Cursor:
        return await self._run()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Connection:
    def __init__(self, client: libsql_client.Client):
        self._client = client
        self.row_factory = None  # None = tuples bruts, Row = accès par nom

    def execute(self, sql: str, parameters: Iterable[Any] | None = None):
        # IMPORTANT: pas de `async def` ici. On retourne un objet qui est à la fois
        # awaitable (pour `cursor = await db.execute(...)`) et un async context manager
        # (pour `async with db.execute(...) as cursor:`), comme le fait aiosqlite.
        return _ExecuteAwaitable(self._client, self.row_factory, sql, list(parameters) if parameters else [])

    async def executescript(self, sql: str):
        statements = [s.strip() for s in sql.split(";") if s.strip()]
        for stmt in statements:
            await self.execute(stmt)

    async def commit(self):
        # Turso/libsql en mode HTTP committe chaque requête individuellement,
        # il n'y a pas de transaction implicite à valider ici.
        pass

    async def close(self):
        # Ne rien faire ici : le client est partagé (singleton géré par
        # _ConnectContextManager) et ne doit pas être fermé à chaque `async with`.
        # Voir close_shared_client() pour la fermeture réelle au shutdown.
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _ConnectContextManager:
    """Permet `async with connect(...) as db:` en réutilisant un client Turso
    UNIQUE et persistant pour toute la durée de vie du process, au lieu d'en
    recréer un (avec handshake TLS/HTTP complet) à chaque requête.

    Avant ce changement, chaque `db.get_xxx()` payait le coût plein d'une
    nouvelle connexion HTTP à Turso. Une commande enchaînant 5-10 requêtes
    (ex: /division-inviter) pouvait alors dépasser les 3 secondes que Discord
    accorde avant d'invalider le token d'interaction -> `Unknown interaction`
    sur le tout premier `response.defer()`."""

    _shared_client: libsql_client.Client | None = None

    async def __aenter__(self) -> _Connection:
        cls = type(self)
        if cls._shared_client is None:
            if TURSO_DATABASE_URL and TURSO_AUTH_TOKEN:
                cls._shared_client = libsql_client.create_client(
                    url=TURSO_DATABASE_URL,
                    auth_token=TURSO_AUTH_TOKEN,
                )
            else:
                # Fallback local pour développement sans variables Turso définies.
                cls._shared_client = libsql_client.create_client(url=_to_libsql_url(_LOCAL_FALLBACK_PATH))
        return _Connection(cls._shared_client)

    async def __aexit__(self, exc_type, exc, tb):
        # Le client est partagé et persistant : on ne le ferme JAMAIS ici,
        # seulement via close_shared_client() au shutdown du bot.
        return False

    def __await__(self):
        # Permet aussi `db = await connect(...)` si jamais utilisé ainsi ailleurs.
        async def _get():
            return await self.__aenter__()
        return _get().__await__()


async def close_shared_client():
    """À appeler explicitement à l'arrêt propre du bot (facultatif : sinon le
    process se termine et la connexion HTTP sous-jacente est simplement coupée)."""
    if _ConnectContextManager._shared_client is not None:
        await _ConnectContextManager._shared_client.close()
        _ConnectContextManager._shared_client = None


def connect(_db_path_ignored=None) -> _ConnectContextManager:
    """Signature compatible avec aiosqlite.connect(DB_PATH).
    Le paramètre est ignoré : la cible réelle vient de TURSO_DATABASE_URL / TURSO_AUTH_TOKEN,
    avec fallback sur un fichier local si ces variables ne sont pas définies.
    Le client sous-jacent est un singleton partagé (voir _ConnectContextManager).
    """
    return _ConnectContextManager()


# Alias pour compat avec les annotations de type existantes (ex: "aiosqlite.Connection")
Connection = _Connection
Cursor = _Cursor