"""
Module utilitaire partagé par les cogs "outils" du bot (mod, tools, nickfix,
vocban, vocprotect, ownerban, restrinct_help).

Contient :
- Embeds standardisés (err / info)
- Résolution de membres et salons par mention / ID / pseudo partiel
- Parsing / formatage de durées
- Persistance JSON simple (settings, warns, nickfix, vocban, vocprotect, ownerban)
- Vérification du propriétaire du bot (owner only)
"""
from __future__ import annotations

import json
import pathlib
import re
import uuid
from typing import Optional

import discord
from discord.ext import commands

# ─────────────────────────────────────────────────────────────────────────────
# Owner du bot — seul Fabrice peut utiliser les commandes réservées.
# ─────────────────────────────────────────────────────────────────────────────

OWNER_ID = 1458068003970093260


def is_owner(user_id: int) -> bool:
    """Vrai si l'utilisateur est le propriétaire du bot."""
    return user_id == OWNER_ID


def owner_only():
    """Check de commande : silencieux (aucune réponse) si ce n'est pas l'owner.

    À utiliser avec @commands.check(utils.owner_only()) sur les commandes qui
    doivent être strictement réservées à Fabrice. En cas de refus, le check
    lève CheckFailure mais aucun message n'est renvoyé (voir les gestionnaires
    d'erreurs de cog, qui ignorent silencieusement ce cas).
    """
    async def predicate(ctx: commands.Context) -> bool:
        return is_owner(ctx.author.id)
    return commands.check(predicate)


async def silent_check_failure(ctx: commands.Context, error: Exception) -> bool:
    """Retourne True si l'erreur correspond à un check owner_only échoué,
    auquel cas l'appelant doit avaler l'erreur sans répondre."""
    return isinstance(error, commands.CheckFailure) and not isinstance(
        error, (commands.MissingPermissions, commands.BotMissingPermissions)
    )


# ─────────────────────────────────────────────────────────────────────────────
# Stockage JSON — un fichier par domaine, dans data/storage/
# ─────────────────────────────────────────────────────────────────────────────

_STORAGE_DIR = pathlib.Path(__file__).parent / "data" / "storage"
_STORAGE_DIR.mkdir(parents=True, exist_ok=True)


def _load_json(filename: str, default):
    path = _STORAGE_DIR / filename
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def _save_json(filename: str, data) -> None:
    path = _STORAGE_DIR / filename
    tmp_path = path.with_suffix(f".tmp-{uuid.uuid4().hex}")
    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def load_tools_settings() -> dict:
    return _load_json("tools_settings.json", {})


def save_tools_settings(data: dict) -> None:
    _save_json("tools_settings.json", data)


def load_vocban() -> dict:
    return _load_json("vocban.json", {})


def save_vocban(data: dict) -> None:
    _save_json("vocban.json", data)


def load_vocprotect() -> dict:
    return _load_json("vocprotect.json", {})


def save_vocprotect(data: dict) -> None:
    _save_json("vocprotect.json", data)


def load_ownerban() -> dict:
    return _load_json("ownerban.json", {})


def save_ownerban(data: dict) -> None:
    _save_json("ownerban.json", data)


def _load_nickfix() -> dict:
    return _load_json("nickfix.json", {})


def _save_nickfix(data: dict) -> None:
    _save_json("nickfix.json", data)


# ─────────────────────────────────────────────────────────────────────────────
# Warns — persistance par serveur / membre
# ─────────────────────────────────────────────────────────────────────────────

def _load_warns() -> dict:
    return _load_json("warns.json", {})


def _save_warns(data: dict) -> None:
    _save_json("warns.json", data)


def add_warn(guild_id: int, user_id: int, moderator_id: int, reason: str) -> int:
    """Ajoute un warn et retourne son numéro (id) au sein du membre."""
    from datetime import datetime, timezone

    data = _load_warns()
    gid, uid = str(guild_id), str(user_id)
    data.setdefault(gid, {}).setdefault(uid, [])
    existing_ids = [w["id"] for w in data[gid][uid]]
    new_id = (max(existing_ids) + 1) if existing_ids else 1
    data[gid][uid].append({
        "id": new_id,
        "by": str(moderator_id),
        "reason": reason,
        "at": datetime.now(timezone.utc).isoformat(),
    })
    _save_warns(data)
    return new_id


def get_warns(guild_id: int, user_id: int) -> list[dict]:
    data = _load_warns()
    return data.get(str(guild_id), {}).get(str(user_id), [])


def get_all_warned(guild_id: int) -> dict[str, list[dict]]:
    data = _load_warns()
    return {uid: warns for uid, warns in data.get(str(guild_id), {}).items() if warns}


def del_warn(guild_id: int, user_id: int, warn_id: int) -> bool:
    data = _load_warns()
    gid, uid = str(guild_id), str(user_id)
    warns = data.get(gid, {}).get(uid, [])
    for i, w in enumerate(warns):
        if w["id"] == warn_id:
            warns.pop(i)
            _save_warns(data)
            return True
    return False


def clear_warns(guild_id: int, user_id: int) -> int:
    data = _load_warns()
    gid, uid = str(guild_id), str(user_id)
    warns = data.get(gid, {}).get(uid, [])
    count = len(warns)
    if count:
        data[gid][uid] = []
        _save_warns(data)
    return count


# ─────────────────────────────────────────────────────────────────────────────
# NickFix — pseudos fixés
# ─────────────────────────────────────────────────────────────────────────────

def get_fixed_nick(guild_id: int, user_id: int) -> Optional[str]:
    data = _load_nickfix()
    return data.get(str(guild_id), {}).get(str(user_id))


def set_fixed_nick(guild_id: int, user_id: int, nick: str) -> None:
    data = _load_nickfix()
    data.setdefault(str(guild_id), {})[str(user_id)] = nick
    _save_nickfix(data)


def unset_fixed_nick(guild_id: int, user_id: int) -> bool:
    data = _load_nickfix()
    gid, uid = str(guild_id), str(user_id)
    if gid in data and uid in data[gid]:
        del data[gid][uid]
        if not data[gid]:
            del data[gid]
        _save_nickfix(data)
        return True
    return False


def get_all_fixed(guild_id: int) -> dict[str, str]:
    data = _load_nickfix()
    return data.get(str(guild_id), {})


# ─────────────────────────────────────────────────────────────────────────────
# Embeds standardisés
# ─────────────────────────────────────────────────────────────────────────────

def err(description: str, title: str = "❌ Erreur") -> discord.Embed:
    return discord.Embed(title=title, description=description, color=discord.Color.red())


def info(description: str, title: str = "ℹ️ Info") -> discord.Embed:
    return discord.Embed(title=title, description=description, color=discord.Color.blurple())


# ─────────────────────────────────────────────────────────────────────────────
# Résolution de membres / salons
# ─────────────────────────────────────────────────────────────────────────────

_MENTION_RE = re.compile(r"^<@!?(\d+)>$")
_CHANNEL_MENTION_RE = re.compile(r"^<#(\d+)>$")


async def find_member(ctx: commands.Context, query: str) -> Optional[discord.Member]:
    """Résout un membre à partir d'une mention, d'un ID, ou d'un pseudo (exact
    puis partiel, insensible à la casse). Retourne None si rien ne correspond."""
    if not query or ctx.guild is None:
        return None

    query = query.strip()

    m = _MENTION_RE.match(query)
    if m:
        return ctx.guild.get_member(int(m.group(1)))

    if query.isdigit():
        member = ctx.guild.get_member(int(query))
        if member:
            return member
        try:
            return await ctx.guild.fetch_member(int(query))
        except (discord.NotFound, discord.HTTPException):
            return None

    ql = query.lower()

    for member in ctx.guild.members:
        if str(member).lower() == ql or member.name.lower() == ql:
            return member
    for member in ctx.guild.members:
        if member.nick and member.nick.lower() == ql:
            return member

    for member in ctx.guild.members:
        if ql in member.name.lower() or (member.nick and ql in member.nick.lower()):
            return member

    return None


async def find_channel(ctx: commands.Context, query: str) -> Optional[discord.abc.GuildChannel]:
    """Résout un salon à partir d'une mention, d'un ID, ou d'un nom partiel."""
    if not query or ctx.guild is None:
        return None

    query = query.strip()

    m = _CHANNEL_MENTION_RE.match(query)
    if m:
        return ctx.guild.get_channel(int(m.group(1)))

    if query.isdigit():
        return ctx.guild.get_channel(int(query))

    ql = query.lower().lstrip("#")
    for channel in ctx.guild.channels:
        if channel.name.lower() == ql:
            return channel
    for channel in ctx.guild.channels:
        if ql in channel.name.lower():
            return channel

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Durées
# ─────────────────────────────────────────────────────────────────────────────

_DURATION_RE = re.compile(r"(\d+)\s*(s|sec|secs|m|min|mins|h|hr|hrs|d|j|jour|jours|w|sem|semaine|semaines)", re.IGNORECASE)

_UNIT_SECONDS = {
    "s": 1, "sec": 1, "secs": 1,
    "m": 60, "min": 60, "mins": 60,
    "h": 3600, "hr": 3600, "hrs": 3600,
    "d": 86400, "j": 86400, "jour": 86400, "jours": 86400,
    "w": 604800, "sem": 604800, "semaine": 604800, "semaines": 604800,
}


def parse_duration(text: str) -> Optional[int]:
    """Parse une durée type '10m', '2h', '1j', '1d2h30m' -> secondes.
    Retourne None si aucun format valide n'est reconnu."""
    if not text:
        return None
    text = text.strip().lower()

    matches = _DURATION_RE.findall(text)
    if not matches:
        return None

    total = 0
    for amount, unit in matches:
        total += int(amount) * _UNIT_SECONDS[unit]
    return total if total > 0 else None


def fmt_duration(seconds: int) -> str:
    """Formate un nombre de secondes en texte lisible (ex: '2h 30m')."""
    if seconds <= 0:
        return "0s"

    units = [
        ("j", 86400),
        ("h", 3600),
        ("m", 60),
        ("s", 1),
    ]
    parts = []
    remaining = int(seconds)
    for label, size in units:
        value, remaining = divmod(remaining, size)
        if value:
            parts.append(f"{value}{label}")
    return " ".join(parts) if parts else "0s"
