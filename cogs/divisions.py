"""
Cog de gestion des divisions du Gotei (RP Bleach).

/create-division        : matérialise la division (catégorie + salons) d'un capitaine.
/division-inviter        : invite un membre à rejoindre une division (capitaine/vice).
/division-quitter        : quitte sa division actuelle (confirmation requise).
/division-expulser       : expulse un membre de la division (capitaine).
/division-bannir         : bannit un membre d'une division, durée optionnelle (capitaine/vice).
/division-deban          : lève un bannissement (capitaine/vice).
/division-mute           : coupe l'écriture d'un membre dans la division (capitaine/vice).
/division-demute         : lève un mute (capitaine/vice).
/bloquer-division         : bloque les invitations d'une division pour soi-même.
/division-debloquer       : débloque une division précédemment bloquée.
/division-dissoudre       : dissout entièrement une division (Administrateur).
/sanction-retirer         : lève toutes les sanctions/timeouts de division d'un membre (Administrateur).
d!tagall                 : réapplique le tag『ɗivN』à tous les membres d'une division qui l'ont perdu.
"""
import os
import re
import sys
import asyncio
import pathlib
import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks
import emoji as emoji_lib

sys.path.append(str(pathlib.Path(__file__).parent.parent))
from data import database as db
from data import division_profiles as pdb

logger = logging.getLogger("urahara.divisions")

def _load_role_id(env_name: str) -> int:
    """Lit un ID de rôle depuis l'environnement sans jamais planter l'import du cog.

    Avant : int(os.getenv("ROLE_X_ID")) levait une exception si la variable était
    absente/mal définie sur Railway, ce qui faisait planter le chargement de tout
    ce module -> divisions.py ne se chargeait plus -> division_config.py (qui
    l'importe via "from cogs import divisions as div_mod") plantait en cascade.
    Résultat observé : "le rôle capitaine n'existe pas" / division non reconnue,
    alors que le vrai souci était un cog jamais chargé, pas un rôle manquant.
    """
    raw = os.getenv(env_name)
    if raw is None or not raw.strip():
        logger.error(
            "Variable d'environnement %s absente ou vide : le rôle correspondant "
            "ne sera pas détecté tant qu'elle n'est pas définie sur Railway.",
            env_name,
        )
        return 0
    try:
        return int(raw.strip())
    except ValueError:
        logger.error(
            "Variable d'environnement %s invalide (%r) : elle doit contenir "
            "uniquement l'ID numérique du rôle.",
            env_name, raw,
        )
        return 0


CAPTAIN_ROLE_ID = _load_role_id("ROLE_CAPTAIN_ID")
VICE_ROLE_ID = _load_role_id("ROLE_VICE_ID")
LIEUTENANT_ROLE_ID = _load_role_id("ROLE_LIEUTENANT_ID")

MAX_DIVISION_MEMBERS = 15
LEAVE_REJOIN_COOLDOWN = 3 * 86400
LEAVE_APPLY_COOLDOWN = 86400
EXPULSION_COOLDOWN = 86400
GRADE_MANDATE_DURATION = 3 * 86400
GRADE_CHOICES = {"vice-capitaine": "vice", "lieutenant": "lieutenant"}

DIVISION_ROLE_PATTERN = re.compile(
    r"^(?:division\s*(\d{1,2})\s*(?:e|er|ère|eme|ème)?|(\d{1,2})\s*(?:e|er|ère|eme|ème)?\s*division)$",
    re.IGNORECASE,
)

CUSTOM_EMOJI_PATTERN = re.compile(r"^<a?:\w+:\d+>$")

SUPERSCRIPT_MAP = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
TAG_PATTERN = re.compile(r"^(?:『ɗiv[⁰¹²³⁴⁵⁶⁷⁸⁹]+』|\[Div[⁰¹²³⁴⁵⁶⁷⁸⁹]+\])\s*")

DURATION_PATTERN = re.compile(r"^(\d+)\s*(m|h|j)$", re.IGNORECASE)
DURATION_UNITS = {"m": 60, "h": 3600, "j": 86400}


def find_division_roles(member: discord.Member) -> list[discord.Role]:
    matches = []
    for role in member.roles:
        if DIVISION_ROLE_PATTERN.match(role.name.strip()):
            matches.append(role)
    return matches


def extract_division_number(role: discord.Role) -> int | None:
    match = DIVISION_ROLE_PATTERN.match(role.name.strip())
    if not match:
        return None
    return int(match.group(1) or match.group(2))


def has_role(member: discord.Member, role_id: int) -> bool:
    return any(role.id == role_id for role in member.roles)


def is_valid_standard_emoji(text: str) -> bool:
    if CUSTOM_EMOJI_PATTERN.match(text):
        return False
    return emoji_lib.is_emoji(text)


def parse_duration(raw: str) -> int | None:
    match = DURATION_PATTERN.match(raw.strip())
    if not match:
        return None
    value, unit = int(match.group(1)), match.group(2).lower()
    if value <= 0:
        return None
    return value * DURATION_UNITS[unit]


def error_embed(description: str) -> discord.Embed:
    return discord.Embed(description=f"❌ {description}", color=discord.Color.red())


def info_embed(description: str) -> discord.Embed:
    return discord.Embed(description=description, color=discord.Color.orange())


def success_embed(title: str, description: str = "") -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=discord.Color.green())
    embed.set_footer(text="Urahara • Gestion RP")
    return embed


def division_tag(number: int) -> str:
    return f"『ɗiv{str(number).translate(SUPERSCRIPT_MAP)}』"


async def set_division_tag(member: discord.Member, number: int):
    tag = division_tag(number)
    base = TAG_PATTERN.sub("", member.display_name)
    if len(base) > 32 - len(tag) - 1:
        base = base[: 32 - len(tag) - 1]
    try:
        await member.edit(nick=f"{tag} {base}")
    except discord.Forbidden:
        pass


async def clear_division_tag(member: discord.Member):
    base = TAG_PATTERN.sub("", member.display_name)
    if base != member.display_name:
        try:
            await member.edit(nick=base or None)
        except discord.Forbidden:
            pass


def get_grade_roles(guild: discord.Guild, member: discord.Member) -> list[discord.Role]:
    """Rôles de grade (vice-capitaine / lieutenant) actuellement portés par le membre."""
    roles = []
    for role_id in (VICE_ROLE_ID, LIEUTENANT_ROLE_ID):
        role = guild.get_role(role_id)
        if role and role in member.roles:
            roles.append(role)
    return roles


def get_grade_holder(guild: discord.Guild, division_role: discord.Role, grade_role_id: int) -> discord.Member | None:
    """Le membre qui porte actuellement ce grade dans cette division (unicité stricte : 1 max)."""
    grade_role = guild.get_role(grade_role_id)
    if grade_role is None:
        return None
    return next((m for m in division_role.members if grade_role in m.roles), None)


async def get_division_staff(guild: discord.Guild, div: "db.aiosqlite.Row"):
    """Retourne (capitaine, [vices], [lieutenants], effectif) pour une division."""
    division_role = guild.get_role(div["role_id"])
    vice_role = guild.get_role(VICE_ROLE_ID)
    lieutenant_role = guild.get_role(LIEUTENANT_ROLE_ID)
    captain = guild.get_member(div["captain_id"]) if div["captain_id"] else None

    members = [m for m in guild.members if division_role and division_role in m.roles]
    vices = [m for m in members if vice_role and vice_role in m.roles]
    lieutenants = [m for m in members if lieutenant_role and lieutenant_role in m.roles]
    return captain, vices, lieutenants, len(members)


async def resolve_staff_division(guild: discord.Guild, author: discord.Member):
    """(div, division_role, division_number) de la division dont l'auteur est membre, sinon None."""
    division_roles = find_division_roles(author)
    if len(division_roles) != 1:
        return None
    division_role = division_roles[0]
    division_number = extract_division_number(division_role)
    div = await db.get_division(division_number)
    if div is None:
        return None
    return div, division_role, division_number


def can_target_for_mute(guild: discord.Guild, author: discord.Member, target: discord.Member, is_captain: bool) -> bool:
    vice_role = guild.get_role(VICE_ROLE_ID)
    lieutenant_role = guild.get_role(LIEUTENANT_ROLE_ID)
    captain_role = guild.get_role(CAPTAIN_ROLE_ID)
    if is_captain:
        return not (vice_role and vice_role in target.roles)
    if lieutenant_role and lieutenant_role in target.roles:
        return False
    if captain_role and captain_role in target.roles:
        return False
    return True


def parse_staff_args(args: tuple) -> tuple[int | None, int | None]:
    """Extrait (numéro_division, durée_en_secondes) d'arguments libres type '3', '7j'."""
    division_number = None
    duration_seconds = None
    for arg in args:
        if arg.isdigit() and division_number is None:
            division_number = int(arg)
        else:
            seconds = parse_duration(arg)
            if seconds is not None:
                duration_seconds = seconds
    return division_number, duration_seconds


def resolve_division_number(membre: discord.Member, division_number: int | None) -> int | None:
    if division_number is not None:
        return division_number
    roles = find_division_roles(membre)
    return extract_division_number(roles[0]) if len(roles) == 1 else None


async def strip_from_division(guild: discord.Guild, member: discord.Member, division_role: discord.Role, reason: str, farewell: str | None = None):
    grade_roles = get_grade_roles(guild, member)
    try:
        await member.remove_roles(division_role, *grade_roles, reason=reason)
    except discord.HTTPException:
        pass
    await clear_division_tag(member)
    if farewell is not None:
        div = await db.get_division_by_role(division_role.id)
        if div:
            await send_farewell(guild, div, member, farewell)


async def send_welcome(guild: discord.Guild, div: "db.aiosqlite.Row", member: discord.Member, method: str):
    """Message de bienvenue dans le salon entrants de la division."""
    channel = guild.get_channel(div["entrants_channel_id"]) if div["entrants_channel_id"] else None
    if channel is None:
        return
    embed = discord.Embed(
        description=f"📥 {member.mention} vient de rejoindre la **division {div['number']}** {method}.",
        color=discord.Color.green(),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="Urahara • Bienvenue")
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        pass


async def send_farewell(guild: discord.Guild, div: "db.aiosqlite.Row", member: discord.Member, reason: str):
    """Message d'au revoir dans le salon sortants de la division."""
    channel = guild.get_channel(div["sortants_channel_id"]) if div["sortants_channel_id"] else None
    if channel is None:
        return
    embed = discord.Embed(
        description=f"📤 {member.mention} {reason} la **division {div['number']}**.",
        color=discord.Color.red(),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="Urahara • Au revoir")
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        pass


async def refresh_panels(client: discord.Client, guild: discord.Guild):
    cog = client.get_cog("Tickets")
    if cog:
        await cog.refresh_panels(guild)


# ---------------------------------------------------------------------------
# Rafraîchissement automatique et "débounced" des panels
#
# De nombreux événements changent l'effectif d'une division (accepter/refuser
# une invitation, quitter, expulsion, ban, dissolution, acceptation de ticket,
# promotion...). Plutôt que d'ajouter un appel à refresh_panels() après chacun
# de ces (nombreux) points, on centralise ça via un listener on_member_update
# qui détecte tout changement de rôle de division/grade et planifie un seul
# refresh (avec un léger délai pour regrouper plusieurs changements rapprochés,
# par ex. add_roles + remove_roles lors d'un remplacement de division).
# ---------------------------------------------------------------------------

_pending_refresh_tasks: dict[int, asyncio.Task] = {}
_REFRESH_DEBOUNCE_SECONDS = 2.0


def schedule_panels_refresh(client: discord.Client, guild: discord.Guild):
    """Planifie un refresh_panels() pour cette guild, en regroupant les appels
    rapprochés (debounce). Sûr à appeler plusieurs fois de suite."""
    existing = _pending_refresh_tasks.get(guild.id)
    if existing and not existing.done():
        existing.cancel()

    async def _run():
        try:
            await asyncio.sleep(_REFRESH_DEBOUNCE_SECONDS)
            await refresh_panels(client, guild)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Erreur lors du refresh automatique des panels pour %s", guild.id)
        finally:
            if _pending_refresh_tasks.get(guild.id) is asyncio.current_task():
                _pending_refresh_tasks.pop(guild.id, None)

    _pending_refresh_tasks[guild.id] = asyncio.create_task(_run())


def build_base_overwrites(guild: discord.Guild, division_role: discord.Role) -> dict:
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        division_role: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    for role_id in (CAPTAIN_ROLE_ID, VICE_ROLE_ID, LIEUTENANT_ROLE_ID):
        role = guild.get_role(role_id)
        if role:
            overwrites[role] = discord.PermissionOverwrite(view_channel=False)
    return overwrites


async def get_or_create_invite_channel(
    guild: discord.Guild, division_row: "db.aiosqlite.Row", division_role: discord.Role
) -> discord.TextChannel:
    if division_row["invite_channel_id"]:
        channel = guild.get_channel(division_row["invite_channel_id"])
        if channel:
            return channel
    category = guild.get_channel(division_row["category_id"])
    channel = await guild.create_text_channel(
        "📨・invitations", category=category, overwrites=build_base_overwrites(guild, division_role)
    )
    await db.set_division_invite_channel(division_row["number"], channel.id)
    return channel


async def division_autocomplete(interaction: discord.Interaction, current: str):
    divisions = await db.get_all_divisions()
    return [
        app_commands.Choice(name=f"Division {d['number']}", value=d["number"])
        for d in divisions if current in str(d["number"])
    ][:25]


class DivisionEmojiModal(discord.ui.Modal, title="Création de division"):
    emoji_input = discord.ui.TextInput(
        label="Emoji de la division (facultatif)",
        placeholder="🔥  —  laisse vide pour ne pas en mettre",
        required=False,
        max_length=8,
    )

    def __init__(self, division_role: discord.Role, division_number: int, error_notice: str | None = None):
        super().__init__()
        self.division_role = division_role
        self.division_number = division_number
        if error_notice:
            self.emoji_input.label = f"⚠️ {error_notice} — Réessaie"

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.emoji_input.value.strip()

        if raw and not is_valid_standard_emoji(raw):
            reason = (
                "Emoji personnalisé du serveur non autorisé"
                if CUSTOM_EMOJI_PATTERN.match(raw)
                else "Ce n'est pas un emoji standard valide"
            )
            await interaction.response.send_modal(
                DivisionEmojiModal(self.division_role, self.division_number, error_notice=reason)
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        chosen_emoji = raw or None

        try:
            await build_division(interaction, self.division_role, self.division_number, chosen_emoji)
        except Exception:
            logger.exception("Erreur inattendue lors de la création de la division %s", self.division_number)
            await interaction.followup.send(
                embed=error_embed("Une erreur inattendue est survenue pendant la création. Préviens un développeur si ça persiste."),
                ephemeral=True,
            )

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.exception("Erreur dans le modal de création de division", exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send(embed=error_embed("Une erreur est survenue avec le formulaire. Réessaie."), ephemeral=True)
        else:
            await interaction.response.send_message(embed=error_embed("Une erreur est survenue avec le formulaire. Réessaie."), ephemeral=True)


COMMUNITY_CATEGORY_ID = 1476261628663042281


async def compute_category_position(guild: discord.Guild, division_number: int) -> int | None:
    """Calcule la position d'insertion pour que les catégories de division restent
    triées par numéro croissant, juste au-dessus de la catégorie Espace Communautaire.
    Retourne None si la catégorie de référence est introuvable (on ne touche à rien)."""
    community_category = guild.get_channel(COMMUNITY_CATEGORY_ID)
    if community_category is None:
        return None

    all_divisions = await db.get_all_divisions()
    existing_categories = []
    for d in all_divisions:
        if d["number"] == division_number:
            continue
        cat = guild.get_channel(d["category_id"])
        if cat is not None:
            existing_categories.append((d["number"], cat))

    # Parmi les divisions déjà créées avec un numéro inférieur, on se place juste après
    # la plus grande ; sinon on se place directement devant Espace Communautaire.
    lower_categories = sorted((n, c) for n, c in existing_categories if n < division_number)
    if lower_categories:
        return lower_categories[-1][1].position + 1
    return community_category.position


async def build_division(
    interaction: discord.Interaction, division_role: discord.Role, division_number: int, chosen_emoji: str | None
):
    guild = interaction.guild
    member = interaction.user

    progress_message = await interaction.followup.send(embed=info_embed("⏳ Création des salons en cours..."), ephemeral=True)

    category_name = (
        f"{chosen_emoji}・division・{division_number}" if chosen_emoji else f"・division・{division_number}"
    )

    captain_role = guild.get_role(CAPTAIN_ROLE_ID)
    vice_role = guild.get_role(VICE_ROLE_ID)

    base_overwrites = build_base_overwrites(guild, division_role)

    try:
        category = await guild.create_category(name=category_name, overwrites=base_overwrites)

        target_position = await compute_category_position(guild, division_number)
        if target_position is not None:
            try:
                await category.edit(position=target_position)
            except discord.HTTPException:
                logger.warning("Impossible de repositionner la catégorie de la division %s.", division_number)

        general_channel = await guild.create_text_channel("💬・general", category=category)

        announce_overwrites = dict(base_overwrites)
        announce_overwrites[division_role] = discord.PermissionOverwrite(view_channel=True, send_messages=False)
        announce_channel = await guild.create_text_channel("📢・annonces", category=category, overwrites=announce_overwrites)
        if captain_role:
            await announce_channel.set_permissions(captain_role, send_messages=True)
        if vice_role:
            await announce_channel.set_permissions(vice_role, send_messages=True)

        entrants_channel = await guild.create_text_channel("📥・entrants", category=category)
        sortants_channel = await guild.create_text_channel("📤・sortants", category=category)
        invite_channel = await guild.create_text_channel("📨・invitations", category=category, overwrites=base_overwrites)

    except discord.Forbidden:
        await progress_message.edit(
            embed=error_embed(
                "Je n'ai pas la permission de créer des catégories/salons ici. "
                "Vérifie mes permissions (Gérer les salons, Gérer les rôles) et ma position dans la hiérarchie des rôles."
            )
        )
        return
    except discord.HTTPException as e:
        await progress_message.edit(embed=error_embed(f"Erreur Discord lors de la création : `{e}`"))
        return

    await db.create_division(
        number=division_number, role_id=division_role.id, category_id=category.id, emoji=chosen_emoji,
        captain_id=member.id, general_channel_id=general_channel.id, announce_channel_id=announce_channel.id,
        entrants_channel_id=entrants_channel.id, sortants_channel_id=sortants_channel.id,
        invite_channel_id=invite_channel.id,
    )

    await set_division_tag(member, division_number)
    await refresh_panels(interaction.client, guild)

    created_at = discord.utils.utcnow()
    timestamp = int(created_at.timestamp())

    summary_embed = discord.Embed(
        title=f"{(chosen_emoji + ' ') if chosen_emoji else ''}Division {division_number} créée",
        description="Cette division a été officiellement mise en place.",
        color=discord.Color.blurple(),
    )
    summary_embed.add_field(name="Capitaine", value=member.mention, inline=True)
    summary_embed.add_field(name="Créée le", value=f"<t:{timestamp}:F>", inline=True)
    summary_embed.add_field(name="Rôle", value=division_role.mention, inline=True)
    summary_embed.add_field(name="Emoji", value=chosen_emoji or "Aucun", inline=True)
    summary_embed.add_field(name="Catégorie", value=category.name, inline=True)
    summary_embed.set_footer(text="Urahara • Gestion RP")

    try:
        summary_message = await announce_channel.send(content=member.mention, embed=summary_embed)
        await summary_message.pin(reason=f"Création de la division {division_number}")
    except discord.Forbidden:
        logger.warning("Impossible d'envoyer/épingler le résumé dans le salon annonces de la division %s (permissions).", division_number)
    except discord.HTTPException:
        logger.exception("Erreur lors de l'envoi/épinglage du résumé de la division %s", division_number)

    final_embed = success_embed("Division mise en place", f"La **division {division_number}** est prête.")
    final_embed.add_field(name="Catégorie", value=category.name, inline=False)
    final_embed.add_field(
        name="Salons",
        value=(
            f"{general_channel.mention} {announce_channel.mention} "
            f"{entrants_channel.mention} {sortants_channel.mention} {invite_channel.mention}"
        ),
        inline=False,
    )
    await progress_message.edit(embed=final_embed)


# ---------------------------------------------------------------------------
# Invitations - Views
# ---------------------------------------------------------------------------

class InviteResponseView(discord.ui.View):
    def __init__(self, invite_id: int):
        super().__init__(timeout=None)
        self.invite_id = invite_id
        accept = discord.ui.Button(label="Accepter", style=discord.ButtonStyle.success, custom_id=f"div_invite_accept:{invite_id}")
        decline = discord.ui.Button(label="Refuser", style=discord.ButtonStyle.danger, custom_id=f"div_invite_decline:{invite_id}")
        block = discord.ui.Button(label="Bloquer", style=discord.ButtonStyle.secondary, custom_id=f"div_invite_block:{invite_id}")
        accept.callback = self._make_callback("accepted")
        decline.callback = self._make_callback("declined")
        block.callback = self._make_callback("blocked")
        self.add_item(accept)
        self.add_item(decline)
        self.add_item(block)

    def _make_callback(self, action: str):
        async def callback(interaction: discord.Interaction):
            await handle_invite_response(interaction, self.invite_id, action)
        return callback


class CaptainCancelView(discord.ui.View):
    def __init__(self, invite_id: int):
        super().__init__(timeout=None)
        self.invite_id = invite_id
        cancel = discord.ui.Button(label="Annuler l'invitation", style=discord.ButtonStyle.danger, custom_id=f"div_invite_cancel:{invite_id}")
        cancel.callback = self._callback
        self.add_item(cancel)

    async def _callback(self, interaction: discord.Interaction):
        await handle_invite_cancel(interaction, self.invite_id)


class StaffDivisionResetView(discord.ui.View):
    def __init__(self, division_number: int, author_id: int):
        super().__init__(timeout=300)
        self.division_number = division_number
        self.author_id = author_id

    @discord.ui.button(label="✅ Confirmer", style=discord.ButtonStyle.danger, custom_id="div_staff_reset_confirm")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(embed=error_embed("Seule la personne qui a lancé cette action peut confirmer."), ephemeral=True)
            return
        await pdb.reset_profile(self.division_number)
        await interaction.response.edit_message(
            embed=success_embed("Profil réinitialisé", f"Le profil de la division {self.division_number} a été remis à zéro."),
            view=None,
        )

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary, custom_id="div_staff_reset_cancel")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(embed=error_embed("Seule la personne qui a lancé cette action peut annuler."), ephemeral=True)
            return
        await interaction.response.edit_message(embed=info_embed("Réinitialisation annulée."), view=None)


async def handle_invite_response(interaction: discord.Interaction, invite_id: int, action: str):
    invite = await db.get_invite(invite_id)
    if invite is None or invite["status"] != "pending":
        await interaction.response.send_message(embed=error_embed("Cette invitation n'est plus valide."), ephemeral=True)
        return
    if interaction.user.id != invite["invitee_id"]:
        await interaction.response.send_message(embed=error_embed("Cette invitation ne t'est pas destinée."), ephemeral=True)
        return

    guild = interaction.guild
    member = interaction.user
    division_number = invite["division_number"]
    invite_channel = guild.get_channel(invite["channel_id"])

    if action == "accepted":
        div = await db.get_division(division_number)
        if div is None:
            await interaction.response.send_message(embed=error_embed("Cette division n'existe plus."), ephemeral=True)
            return
        now = int(discord.utils.utcnow().timestamp())
        if find_division_roles(member):
            await interaction.response.send_message(embed=error_embed("Tu fais déjà partie d'une division."), ephemeral=True)
            return
        sanction = await db.get_sanction(member.id)
        if sanction and sanction["no_join_until"] and sanction["no_join_until"] > now:
            await interaction.response.send_message(
                embed=error_embed(f"Tu ne peux pas rejoindre de division avant <t:{sanction['no_join_until']}:F>."), ephemeral=True
            )
            return
        leave_cd = await db.get_leave_cooldown(member.id, division_number)
        if leave_cd and leave_cd["no_rejoin_until"] > now:
            await interaction.response.send_message(
                embed=error_embed(f"Tu ne peux pas rejoindre cette division avant <t:{leave_cd['no_rejoin_until']}:F>."), ephemeral=True
            )
            return
        ban = await db.get_division_ban(member.id, division_number)
        if ban and (ban["banned_until"] is None or ban["banned_until"] > now):
            await interaction.response.send_message(embed=error_embed("Tu es banni de cette division."), ephemeral=True)
            return
        division_role = guild.get_role(div["role_id"])
        await interaction.response.defer()
        try:
            await member.add_roles(division_role, reason="Invitation de division acceptée")
        except discord.HTTPException:
            await interaction.followup.send(embed=error_embed("Impossible de t'ajouter le rôle."), ephemeral=True)
            return
        await set_division_tag(member, division_number)
        await send_welcome(guild, div, member, "par invitation")
        result_text = f"✅ {member.mention} a **accepté** l'invitation et rejoint la division {division_number}."
        result_color = discord.Color.green()
    elif action == "blocked":
        await interaction.response.defer()
        await db.block_division(member.id, division_number)
        result_text = f"🚫 {member.mention} a **bloqué** les invitations de cette division."
        result_color = discord.Color.greyple()
    else:
        await interaction.response.defer()
        result_text = f"❌ {member.mention} a **refusé** l'invitation."
        result_color = discord.Color.red()

    if invite_channel:
        try:
            await invite_channel.set_permissions(member, overwrite=None)
        except discord.HTTPException:
            pass

    await db.update_invite_status(invite_id, action)
    closed_embed = discord.Embed(description=result_text, color=result_color)
    closed_embed.set_footer(text="Urahara • Invitation de division")
    await interaction.edit_original_response(embed=closed_embed, view=None)


async def handle_invite_cancel(interaction: discord.Interaction, invite_id: int):
    invite = await db.get_invite(invite_id)
    if invite is None or invite["status"] != "pending":
        await interaction.response.edit_message(embed=error_embed("Cette invitation a déjà été traitée."), view=None)
        return
    if interaction.user.id != invite["inviter_id"]:
        await interaction.response.send_message(embed=error_embed("Tu n'es pas à l'origine de cette invitation."), ephemeral=True)
        return

    guild = interaction.guild
    await interaction.response.defer()
    await db.update_invite_status(invite_id, "cancelled")

    channel = guild.get_channel(invite["channel_id"])
    if channel:
        member = guild.get_member(invite["invitee_id"])
        if member:
            try:
                await channel.set_permissions(member, overwrite=None)
            except discord.HTTPException:
                pass
        if invite["message_id"]:
            try:
                message = await channel.fetch_message(invite["message_id"])
                await message.edit(
                    embed=discord.Embed(description="⚪ Invitation **annulée** par le capitaine.", color=discord.Color.greyple()),
                    view=None,
                )
            except discord.HTTPException:
                pass

    await interaction.edit_original_response(embed=success_embed("Invitation annulée", "L'invitation a bien été annulée."), view=None)


# ---------------------------------------------------------------------------
# Départ volontaire (division-quitter)
# ---------------------------------------------------------------------------

async def perform_leave(guild: discord.Guild, member: discord.Member, division_role: discord.Role, division_number: int):
    await strip_from_division(guild, member, division_role, "Départ volontaire de division", farewell="a quitté")

    now = int(discord.utils.utcnow().timestamp())
    await db.set_leave_cooldown(member.id, division_number, now + LEAVE_REJOIN_COOLDOWN)
    await db.set_sanction(member.id, no_join_until=now + LEAVE_APPLY_COOLDOWN)


class ConfirmLeaveView(discord.ui.View):
    def __init__(self, author_id: int, division_role_id: int, division_number: int):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.division_role_id = division_role_id
        self.division_number = division_number

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(embed=error_embed("Seul l'auteur de la commande peut confirmer."), ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirmer le départ", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        division_role = guild.get_role(self.division_role_id)
        if division_role is None or division_role not in interaction.user.roles:
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(embed=error_embed("Tu n'es déjà plus dans cette division."), view=self)
            self.stop()
            return

        await interaction.response.defer()
        await perform_leave(guild, interaction.user, division_role, self.division_number)
        for item in self.children:
            item.disabled = True
        embed = success_embed(
            "Division quittée",
            f"Tu as quitté la **division {self.division_number}**.\n"
            f"Tu ne pourras pas la rejoindre avant 3 jours, ni postuler ailleurs avant 24h.",
        )
        await interaction.edit_original_response(embed=embed, view=self)
        self.stop()

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(embed=info_embed("Départ annulé."), view=self)
        self.stop()


# ---------------------------------------------------------------------------
# Dissolution
# ---------------------------------------------------------------------------

async def perform_dissolution(guild: discord.Guild, number: int, apply_sanction: bool = True, client: discord.Client | None = None):
    div = await db.get_division(number)
    if div is None:
        return False, "Division introuvable (déjà dissoute ?)."

    for key in ("general_channel_id", "announce_channel_id", "entrants_channel_id", "sortants_channel_id", "invite_channel_id"):
        cid = div[key]
        if cid:
            ch = guild.get_channel(cid)
            if ch:
                try:
                    await ch.delete(reason=f"Dissolution division {number}")
                except discord.HTTPException:
                    pass

    category = guild.get_channel(div["category_id"])
    if category:
        try:
            await category.delete(reason=f"Dissolution division {number}")
        except discord.HTTPException:
            pass

    division_role = guild.get_role(div["role_id"])
    captain_role = guild.get_role(CAPTAIN_ROLE_ID)
    captain_id = div["captain_id"]

    if division_role:
        # Les membres admin conservent leur rôle/tag de division : seuls les non-admins sont nettoyés.
        for member in [m for m in guild.members if division_role in m.roles]:
            if member.guild_permissions.administrator:
                continue
            try:
                await member.remove_roles(division_role, reason=f"Dissolution division {number}")
            except discord.HTTPException:
                pass
            await clear_division_tag(member)

    if captain_id:
        captain = guild.get_member(captain_id)
        if captain and not captain.guild_permissions.administrator:
            if captain_role and captain_role in captain.roles:
                try:
                    await captain.remove_roles(captain_role, reason=f"Dissolution division {number}")
                except discord.HTTPException:
                    pass
            if apply_sanction:
                now = int(discord.utils.utcnow().timestamp())
                await db.set_sanction(captain_id, no_create_until=now + 7 * 86400, no_join_until=now + 86400)

    # Réinitialiser le profil de la division
    await pdb.reset_profile(number)
    
    await db.delete_division(number)
    if client:
        await refresh_panels(client, guild)
    return True, None


class ConfirmPromoteView(discord.ui.View):
    def __init__(self, author_id: int, member_id: int, division_number: int, grade_key: str, grade_name: str):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.member_id = member_id
        self.division_number = division_number
        self.grade_key = grade_key
        self.grade_name = grade_name

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(embed=error_embed("Seul l'auteur de la commande peut confirmer."), ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirmer la promotion", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        guild = interaction.guild
        member = guild.get_member(self.member_id)
        div = await db.get_division(self.division_number)
        for item in self.children:
            item.disabled = True

        if member is None or div is None:
            await interaction.edit_original_response(embed=error_embed("Membre ou division introuvable."), view=self)
            self.stop()
            return

        division_role = guild.get_role(div["role_id"])
        grade_role_id = VICE_ROLE_ID if self.grade_key == "vice" else LIEUTENANT_ROLE_ID
        grade_role = guild.get_role(grade_role_id)

        current_holder = get_grade_holder(guild, division_role, grade_role_id) if division_role else None
        if current_holder is not None:
            await interaction.edit_original_response(
                embed=error_embed(f"{current_holder.mention} occupe déjà ce grade entre-temps."), view=self
            )
            self.stop()
            return

        try:
            await member.add_roles(grade_role, reason=f"Promotion {self.grade_name} par {interaction.user}")
        except discord.HTTPException:
            await interaction.edit_original_response(embed=error_embed("Impossible d'attribuer le rôle de grade."), view=self)
            self.stop()
            return

        # Mandat de 3 jours : uniquement lors de la toute première promotion à ce grade.
        already_had_mandate = await db.has_grade_mandate_record(member.id, self.division_number, self.grade_key)
        if not already_had_mandate:
            protected_until = int(discord.utils.utcnow().timestamp()) + GRADE_MANDATE_DURATION
            await db.set_grade_mandate(member.id, self.division_number, self.grade_key, protected_until)

        announce_channel = guild.get_channel(div["announce_channel_id"]) if div["announce_channel_id"] else None
        if announce_channel:
            try:
                await announce_channel.send(embed=discord.Embed(
                    description=f"📈 {member.mention} a été promu **{self.grade_name}** de la division {self.division_number} par {interaction.user.mention} !",
                    color=discord.Color.green(),
                ))
            except discord.HTTPException:
                pass

        embed = success_embed("Membre promu", f"{member.mention} est maintenant **{self.grade_name}** de la division {self.division_number}.")
        await interaction.edit_original_response(embed=embed, view=self)
        self.stop()

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(embed=info_embed("Promotion annulée."), view=self)
        self.stop()


class ConfirmDissolveView(discord.ui.View):
    def __init__(self, author_id: int, division_number: int, apply_sanction: bool):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.division_number = division_number
        self.apply_sanction = apply_sanction

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(embed=error_embed("Seul l'auteur de la commande peut confirmer."), ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirmer la dissolution", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        ok, error = await perform_dissolution(interaction.guild, self.division_number, self.apply_sanction, interaction.client)
        for item in self.children:
            item.disabled = True
        embed = (
            success_embed("Division dissoute", f"La division {self.division_number} a été entièrement dissoute.")
            if ok else error_embed(error)
        )
        await interaction.edit_original_response(embed=embed, view=self)
        self.stop()

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(embed=info_embed("Dissolution annulée."), view=self)
        self.stop()


class Divisions(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # {(guild_id, user_id): timestamp} — dernière correction auto de tag
        # effectuée par le bot lui-même, pour ne pas re-déclencher en boucle
        # notre propre on_member_update (member.edit(nick=...) ci-dessous émet
        # exactement le même event que si le membre s'était renommé).
        self._tag_fix_timestamps: dict[tuple[int, int], float] = {}
        # Évite de relancer le scan complet à chaque reconnexion on_ready
        # (Railway peut redéclencher on_ready après une coupure réseau sans
        # que ce soit un vrai redémarrage du process).
        self._startup_tag_scan_done = False

    async def cog_load(self):
        self.expire_invites.start()
        self.check_expirations.start()
        for invite in await db.get_pending_invites():
            self.bot.add_view(InviteResponseView(invite["id"]))
            self.bot.add_view(CaptainCancelView(invite["id"]))

    def cog_unload(self):
        self.expire_invites.cancel()
        self.check_expirations.cancel()

    def _mark_tag_fix(self, guild_id: int, user_id: int):
        self._tag_fix_timestamps[(guild_id, user_id)] = discord.utils.utcnow().timestamp()

    def _tag_fix_cooldown_active(self, guild_id: int, user_id: int) -> bool:
        key = (guild_id, user_id)
        last = self._tag_fix_timestamps.get(key, 0.0)
        return (discord.utils.utcnow().timestamp() - last) < 2.0

    async def _reapply_tag_if_needed(self, member: discord.Member) -> str:
        """Si le membre porte un rôle de division, s'assure que le tag『ɗivN』
        est toujours présent en tête de son pseudo actuel. Ne touche à rien
        d'autre : le reste du pseudo choisi par le membre est conservé tel quel.
        Pensé pour être appelé à chaque renommage, autant de fois que
        nécessaire — le tag doit être perpétuellement présent, sans que le
        membre puisse s'en débarrasser en se renommant.

        Retourne un statut pour permettre aux appelants (scan de démarrage,
        commande d!tagall) de faire un rapport :
          "fixed"     : le tag manquait, il vient d'être réappliqué.
          "ok"        : rien à faire, le tag était déjà là.
          "no_division": le membre n'a pas de rôle de division.
          "forbidden" : permission Discord manquante (rôle du bot trop bas...).
          "error"     : autre erreur HTTP.
        """
        roles = find_division_roles(member)
        if not roles:
            return "no_division"
        number = extract_division_number(roles[0])
        if number is None:
            return "no_division"

        expected_tag = division_tag(number)
        current_name = member.display_name

        # Le tag est déjà là, en tête : rien à faire.
        if current_name.startswith(expected_tag):
            return "ok"

        base = TAG_PATTERN.sub("", current_name).strip()
        new_nick = f"{expected_tag} {base}".strip() if base else expected_tag
        if len(new_nick) > 32:
            overflow = len(new_nick) - 32
            base = base[: max(0, len(base) - overflow)].rstrip()
            new_nick = f"{expected_tag} {base}".strip() if base else expected_tag

        if new_nick == member.nick:
            return "ok"

        try:
            self._mark_tag_fix(member.guild.id, member.id)
            await member.edit(nick=new_nick, reason="Réapplication automatique du tag de division")
            return "fixed"
        except discord.Forbidden:
            logger.warning(
                "[DivisionTag] Impossible de réappliquer le tag de division pour %s (%d) — "
                "permission manquante (rôle du bot trop bas ou membre non modifiable).",
                member, member.id,
            )
            return "forbidden"
        except discord.HTTPException as exc:
            logger.error("[DivisionTag] Erreur HTTP en réappliquant le tag pour %s (%d) : %s", member, member.id, exc)
            return "error"

    async def _scan_and_fix_all_tags(self, guild: discord.Guild) -> dict[str, int]:
        """Parcourt tous les membres du serveur et réapplique le tag de division
        à quiconque en a un rôle mais pas le tag en tête de pseudo. Utilisé au
        démarrage du bot (rattrapage automatique après une mise à jour) et par
        la commande d!tagall (rattrapage manuel à la demande)."""
        counters = {"fixed": 0, "ok": 0, "no_division": 0, "forbidden": 0, "error": 0}
        for member in guild.members:
            if member.bot:
                continue
            status = await self._reapply_tag_if_needed(member)
            counters[status] = counters.get(status, 0) + 1
            if status == "fixed":
                # Petite pause pour rester loin des rate limits Discord sur
                # les gros serveurs (member.edit est un appel API à part entière).
                await asyncio.sleep(0.3)
        return counters

    @commands.Cog.listener()
    async def on_ready(self):
        """Rattrapage automatique au démarrage : après un déploiement/commit,
        on scanne tous les serveurs pour redonner le tag『ɗivN』à quiconque a
        une division mais dont le pseudo a été changé pendant que le bot était
        hors ligne (ou avant que ce système n'existe). Une seule fois par
        session — on_ready peut se redéclencher après une reconnexion réseau
        sans que ce soit un vrai redémarrage."""
        if self._startup_tag_scan_done:
            return
        self._startup_tag_scan_done = True

        for guild in self.bot.guilds:
            try:
                counters = await self._scan_and_fix_all_tags(guild)
            except Exception as exc:
                logger.error("[DivisionTag] Scan de démarrage échoué pour '%s' : %s", guild.name, exc)
                continue
            if counters["fixed"] or counters["forbidden"] or counters["error"]:
                logger.info(
                    "[DivisionTag] Scan de démarrage sur '%s' : %d tag(s) réappliqué(s), "
                    "%d déjà en ordre, %d ignoré(s) (permission), %d erreur(s).",
                    guild.name, counters["fixed"], counters["ok"], counters["forbidden"], counters["error"],
                )

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """Filet de sécurité global : dès qu'un membre gagne ou perd un rôle de
        division (ou un rôle de grade vice/lieutenant), on planifie un refresh
        des panels. Ça couvre TOUTES les sources de changement (invitation
        acceptée, départ, expulsion, ban, ticket accepté, promotion, action
        manuelle d'un admin sur les rôles...) sans avoir à multiplier les
        appels explicites partout dans le code.

        On surveille aussi les changements de pseudo : un membre en division
        qui se renomme et fait disparaître son tag『ɗivN』se le voit réappliqué
        immédiatement devant son nouveau pseudo, sans rien changer d'autre.
        """
        if before.nick != after.nick:
            if not self._tag_fix_cooldown_active(after.guild.id, after.id):
                await self._reapply_tag_if_needed(after)

        if before.roles == after.roles:
            return

        before_ids = {r.id for r in before.roles}
        after_ids = {r.id for r in after.roles}
        changed_ids = before_ids ^ after_ids
        if not changed_ids:
            return

        watched_ids = {VICE_ROLE_ID, LIEUTENANT_ROLE_ID}
        relevant = False
        for role in after.guild.roles:
            if role.id in changed_ids and (role.id in watched_ids or DIVISION_ROLE_PATTERN.match(role.name.strip())):
                relevant = True
                break
        if relevant:
            schedule_panels_refresh(self.bot, after.guild)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Si un membre d'une division quitte le serveur, son rôle disparaît
        avec lui : on_member_update ne se déclenche pas dans ce cas précis,
        donc on rafraîchit explicitement les panels ici aussi."""
        if find_division_roles(member):
            schedule_panels_refresh(self.bot, member.guild)

    @tasks.loop(seconds=30)
    async def expire_invites(self):
        now = int(discord.utils.utcnow().timestamp())
        for invite in await db.get_expired_pending_invites(now):
            # On marque le statut en DB EN PREMIER : même si tout ce qui suit
            # (fetch du salon/message Discord) échoue, l'invitation ne sera
            # plus jamais reproposée ni considérée "pending" au prochain tour
            # de boucle ou au prochain reboot. La donnée n'est jamais perdue.
            await db.update_invite_status(invite["id"], "expired")

            channel = self.bot.get_channel(invite["channel_id"])
            if channel is None:
                # Le cache interne peut ne pas encore contenir ce salon juste
                # après un reboot/reconnexion : on retente un fetch réseau
                # explicite avant d'abandonner, pour ne pas laisser l'embed
                # public figé sur "en attente" alors que l'invitation a bien
                # expiré côté données.
                try:
                    channel = await self.bot.fetch_channel(invite["channel_id"])
                except discord.HTTPException:
                    logger.warning(
                        "Invitation %s expirée mais salon %s introuvable (probablement supprimé).",
                        invite["id"], invite["channel_id"],
                    )
                    continue

            member = channel.guild.get_member(invite["invitee_id"])
            if member:
                try:
                    await channel.set_permissions(member, overwrite=None)
                except discord.HTTPException:
                    pass
            if invite["message_id"]:
                try:
                    message = await channel.fetch_message(invite["message_id"])
                    await message.edit(
                        embed=discord.Embed(description="⏰ Cette invitation a **expiré**.", color=discord.Color.greyple()),
                        view=None,
                    )
                except discord.NotFound:
                    # Message déjà supprimé manuellement : rien à mettre à jour, ce n'est pas une erreur.
                    pass
                except discord.HTTPException:
                    logger.warning("Impossible de mettre à jour l'embed d'expiration pour l'invitation %s.", invite["id"])

    @expire_invites.before_loop
    async def before_expire_invites(self):
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=60)
    async def check_expirations(self):
        now = int(discord.utils.utcnow().timestamp())
        for mute in await db.get_expired_mutes(now):
            await db.clear_division_mute(mute["user_id"], mute["division_number"])
            div = await db.get_division(mute["division_number"])
            if div is None:
                continue
            for guild in self.bot.guilds:
                member = guild.get_member(mute["user_id"])
                category = guild.get_channel(div["category_id"])
                if member and category:
                    try:
                        await category.set_permissions(member, overwrite=None, reason="Mute division expiré")
                    except discord.HTTPException:
                        pass

    @check_expirations.before_loop
    async def before_check_expirations(self):
        await self.bot.wait_until_ready()

    # -----------------------------------------------------------------
    # /create-division
    # -----------------------------------------------------------------

    @app_commands.command(name="create-division", description="Crée la catégorie et les salons de ta division (rôle Xe division requis).")
    async def create_division(self, interaction: discord.Interaction):
        member = interaction.user
        guild = interaction.guild

        if guild is None:
            await interaction.response.send_message(embed=error_embed("Cette commande ne fonctionne qu'sur un serveur."), ephemeral=True)
            return

        captain_role = guild.get_role(CAPTAIN_ROLE_ID)
        if captain_role is None:
            await interaction.response.send_message(
                embed=error_embed("Le rôle Capitaine n'existe pas sur ce serveur. Vérifie l'ID du rôle dans ton .env."),
                ephemeral=True,
            )
            return
        if not has_role(member, CAPTAIN_ROLE_ID):
            await interaction.response.send_message(embed=error_embed("Tu dois avoir le rôle **Capitaine** pour utiliser cette commande."), ephemeral=True)
            return

        sanction = await db.get_sanction(member.id)
        now = int(discord.utils.utcnow().timestamp())
        if sanction and sanction["no_create_until"] and sanction["no_create_until"] > now:
            await interaction.response.send_message(embed=error_embed(f"Tu ne peux pas créer de division avant <t:{sanction['no_create_until']}:F>."), ephemeral=True)
            return

        division_roles = find_division_roles(member)

        if len(division_roles) == 0:
            await interaction.response.send_message(
                embed=error_embed("Tu n'es dans aucune division actif."),
                ephemeral=True,
            )
            return

        if len(division_roles) > 1:
            roles_list = ", ".join(r.mention for r in division_roles)
            await interaction.response.send_message(
                embed=error_embed(
                    f"Tu as **plusieurs** rôles de division en même temps, c'est ambigu : {roles_list}\n"
                    "Un membre ne doit avoir qu'un seul rôle de division. Corrige ça avant de réessayer."
                ),
                ephemeral=True,
            )
            return

        division_role = division_roles[0]
        division_number = extract_division_number(division_role)

        existing = await db.get_division(division_number)
        if existing is not None:
            await interaction.response.send_message(embed=error_embed(f"La division **{division_number}** existe déjà sur le serveur."), ephemeral=True)
            return

        try:
            await interaction.response.send_modal(DivisionEmojiModal(division_role=division_role, division_number=division_number))
        except discord.NotFound:
            if interaction.response.is_done():
                await interaction.followup.send(
                    embed=error_embed("Impossible d'ouvrir le formulaire, l'interaction a expiré. Réessaie."),
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    embed=error_embed("Impossible d'ouvrir le formulaire, l'interaction a expiré. Réessaie."),
                    ephemeral=True,
                )
        except discord.HTTPException as exc:
            await interaction.response.send_message(
                embed=error_embed("Une erreur Discord est survenue. Réessaie dans un instant."),
                ephemeral=True,
            )

    # -----------------------------------------------------------------
    # /division-inviter
    # -----------------------------------------------------------------

    @app_commands.command(name="division-inviter", description="Invite un membre à rejoindre ta division.")
    @app_commands.describe(membre="Le membre à inviter")
    async def division_inviter(self, interaction: discord.Interaction, membre: discord.Member):
        guild = interaction.guild
        author = interaction.user

        if membre.bot:
            await interaction.response.send_message(embed=error_embed("Impossible d'inviter un bot."), ephemeral=True)
            return
        if membre.id == author.id:
            await interaction.response.send_message(embed=error_embed("Tu ne peux pas t'inviter toi-même."), ephemeral=True)
            return

        division_roles = find_division_roles(author)
        if len(division_roles) != 1:
            await interaction.response.send_message(embed=error_embed("Impossible de déterminer ta division (rôle manquant ou ambigu)."), ephemeral=True)
            return
        division_role = division_roles[0]
        division_number = extract_division_number(division_role)
        div = await db.get_division(division_number)
        if div is None:
            await interaction.response.send_message(embed=error_embed("Ta division n'a pas encore été créée."), ephemeral=True)
            return

        vice_role = guild.get_role(VICE_ROLE_ID)
        is_captain = author.id == div["captain_id"]
        is_vice = vice_role is not None and vice_role in author.roles
        if not (is_captain or is_vice):
            await interaction.response.send_message(embed=error_embed("Seuls le capitaine et le vice-capitaine peuvent inviter."), ephemeral=True)
            return

        if division_role in membre.roles:
            await interaction.response.send_message(embed=error_embed(f"{membre.mention} est déjà dans cette division."), ephemeral=True)
            return
        if find_division_roles(membre):
            await interaction.response.send_message(embed=error_embed(f"{membre.mention} fait déjà partie d'une autre division."), ephemeral=True)
            return

        # On defer dès que les vérifications rapides (en mémoire, sans DB) sont
        # passées : tout ce qui suit enchaîne plusieurs requêtes réseau vers
        # Turso et peut dépasser les 3s de budget avant expiration du token.
        await interaction.response.defer(ephemeral=True, thinking=True)

        if await db.is_division_blocked(membre.id, division_number):
            await interaction.followup.send(embed=error_embed(f"{membre.mention} a bloqué les invitations de cette division."), ephemeral=True)
            return
        if await db.get_pending_invite(division_number, membre.id):
            await interaction.followup.send(embed=error_embed(f"Une invitation est déjà en attente pour {membre.mention}."), ephemeral=True)
            return

        now = int(discord.utils.utcnow().timestamp())
        ban = await db.get_division_ban(membre.id, division_number)
        if ban and (ban["banned_until"] is None or ban["banned_until"] > now):
            await interaction.followup.send(embed=error_embed(f"{membre.mention} est banni de cette division."), ephemeral=True)
            return
        expulsion = await db.get_division_expulsion(membre.id, division_number)
        if expulsion and expulsion["until_ts"] > now:
            await interaction.followup.send(
                embed=error_embed(f"{membre.mention} a été expulsé récemment, impossible de l'inviter avant <t:{expulsion['until_ts']}:F>."), ephemeral=True
            )
            return
        _, _, _, current_count = await get_division_staff(guild, div)
        if current_count >= MAX_DIVISION_MEMBERS:
            await interaction.followup.send(
                embed=error_embed(f"La division {division_number} est complète ({MAX_DIVISION_MEMBERS}/{MAX_DIVISION_MEMBERS})."), ephemeral=True
            )
            return

        # Un capitaine/vice non-admin ne peut pas contourner les cooldowns de départ.
        if not author.guild_permissions.administrator:
            sanction = await db.get_sanction(membre.id)
            if sanction and sanction["no_join_until"] and sanction["no_join_until"] > now:
                await interaction.followup.send(
                    embed=error_embed(f"{membre.mention} ne peut pas rejoindre de division avant <t:{sanction['no_join_until']}:F>."), ephemeral=True
                )
                return
            leave_cd = await db.get_leave_cooldown(membre.id, division_number)
            if leave_cd and leave_cd["no_rejoin_until"] > now:
                await interaction.followup.send(
                    embed=error_embed(f"{membre.mention} ne peut pas rejoindre cette division avant <t:{leave_cd['no_rejoin_until']}:F>."), ephemeral=True
                )
                return

        invite_channel = await get_or_create_invite_channel(guild, div, division_role)
        try:
            await invite_channel.set_permissions(membre, view_channel=True, send_messages=True, reason="Invitation division")
        except discord.Forbidden:
            await interaction.followup.send(embed=error_embed("Je n'ai pas la permission de gérer ce salon."), ephemeral=True)
            return

        expires_at = int(discord.utils.utcnow().timestamp()) + 180
        invite_id = await db.create_invite(division_number, author.id, membre.id, invite_channel.id, expires_at)

        invite_embed = discord.Embed(
            title=f"Invitation • Division {division_number}",
            description=f"{membre.mention}, {author.mention} t'invite à rejoindre la **division {division_number}**.",
            color=discord.Color.blurple(),
        )
        invite_embed.add_field(name="Expire", value=f"<t:{expires_at}:R>", inline=True)
        invite_embed.set_footer(text="Urahara • Invitation de division")

        response_view = InviteResponseView(invite_id)
        invite_message = await invite_channel.send(content=membre.mention, embed=invite_embed, view=response_view)
        self.bot.add_view(response_view, message_id=invite_message.id)
        await db.set_invite_message(invite_id, invite_message.id)

        cancel_view = CaptainCancelView(invite_id)
        self.bot.add_view(cancel_view)
        await interaction.followup.send(
            embed=success_embed("Invitation envoyée", f"{membre.mention} a été invité à rejoindre la division {division_number}."),
            view=cancel_view, ephemeral=True,
        )

    # -----------------------------------------------------------------
    # /division-quitter
    # -----------------------------------------------------------------

    @app_commands.command(name="division-quitter", description="Quitte ta division actuelle.")
    async def division_quitter(self, interaction: discord.Interaction):
        member = interaction.user
        division_roles = find_division_roles(member)
        if len(division_roles) != 1:
            await interaction.response.send_message(embed=error_embed("Impossible de déterminer ta division (rôle manquant ou ambigu)."), ephemeral=True)
            return

        division_role = division_roles[0]
        division_number = extract_division_number(division_role)

        warning = discord.Embed(
            title="⚠️ Quitter la division",
            description=(
                f"Tu es sur le point de quitter la **division {division_number}**.\n\n"
                "Conséquences :\n"
                "• Retrait immédiat du rôle et du tag de division\n"
                "• Retrait de ton grade (vice-capitaine/lieutenant) si tu en as un\n"
                "• Impossible de rejoindre **cette division** avant **3 jours**\n"
                "• Impossible de postuler à **une autre division** avant **24h**"
            ),
            color=discord.Color.red(),
        )
        await interaction.response.send_message(
            embed=warning, view=ConfirmLeaveView(member.id, division_role.id, division_number), ephemeral=True
        )

    # -----------------------------------------------------------------
    # /division-expulser
    # -----------------------------------------------------------------

    @app_commands.command(name="division-expulser", description="Expulse un membre de ta division (capitaine uniquement).")
    @app_commands.describe(membre="Le membre à expulser")
    async def division_expulser(self, interaction: discord.Interaction, membre: discord.Member):
        guild = interaction.guild
        author = interaction.user

        resolved = await resolve_staff_division(guild, author)
        if resolved is None:
            await interaction.response.send_message(embed=error_embed("Impossible de déterminer ta division."), ephemeral=True)
            return
        div, division_role, division_number = resolved

        if author.id != div["captain_id"]:
            await interaction.response.send_message(embed=error_embed("Seul le capitaine peut expulser un membre."), ephemeral=True)
            return
        if membre.id == author.id:
            await interaction.response.send_message(embed=error_embed("Tu ne peux pas t'expulser toi-même."), ephemeral=True)
            return
        if division_role not in membre.roles:
            await interaction.response.send_message(embed=error_embed(f"{membre.mention} n'est pas dans ta division."), ephemeral=True)
            return

        await interaction.response.defer()
        await strip_from_division(guild, membre, division_role, f"Expulsion par {author}", farewell="a été expulsé de")
        now = int(discord.utils.utcnow().timestamp())
        await db.set_division_expulsion(membre.id, division_number, now + EXPULSION_COOLDOWN)

        await interaction.followup.send(
            embed=success_embed(
                "Membre expulsé",
                f"{membre.mention} a été expulsé de la division {division_number}.\n"
                "Il ne pourra pas postuler ni être réinvité avant 24h.",
            ),
        )

    # -----------------------------------------------------------------
    # /division-bannir  /  /division-deban
    # -----------------------------------------------------------------

    @app_commands.command(name="division-bannir", description="Bannit un membre de ta division (capitaine/vice).")
    @app_commands.describe(membre="Le membre à bannir", duree="Durée optionnelle (ex: 30m, 12h, 7j). Laisse vide pour un ban permanent.")
    async def division_bannir(self, interaction: discord.Interaction, membre: discord.Member, duree: str = None):
        guild = interaction.guild
        author = interaction.user

        resolved = await resolve_staff_division(guild, author)
        if resolved is None:
            await interaction.response.send_message(embed=error_embed("Impossible de déterminer ta division."), ephemeral=True)
            return
        div, division_role, division_number = resolved

        vice_role = guild.get_role(VICE_ROLE_ID)
        is_captain = author.id == div["captain_id"]
        is_vice = vice_role is not None and vice_role in author.roles
        if not (is_captain or is_vice):
            await interaction.response.send_message(embed=error_embed("Seuls le capitaine et le vice-capitaine peuvent bannir."), ephemeral=True)
            return
        if membre.id == author.id:
            await interaction.response.send_message(embed=error_embed("Tu ne peux pas te bannir toi-même."), ephemeral=True)
            return

        until_ts = None
        if duree:
            seconds = parse_duration(duree)
            if seconds is None:
                await interaction.response.send_message(
                    embed=error_embed("Format de durée invalide. Utilise un nombre suivi de m, h ou j (ex: 30m, 12h, 7j)."), ephemeral=True
                )
                return
            until_ts = int(discord.utils.utcnow().timestamp()) + seconds

        await interaction.response.defer()
        if division_role in membre.roles:
            await strip_from_division(guild, membre, division_role, f"Bannissement par {author}", farewell="a été banni de")

        await db.set_division_ban(membre.id, division_number, until_ts)
        duration_txt = f"jusqu'à <t:{until_ts}:F>" if until_ts else "de façon permanente (jusqu'à levée manuelle)"
        await interaction.followup.send(
            embed=success_embed("Membre banni", f"{membre.mention} est banni de la division {division_number} {duration_txt}."),
        )

    @app_commands.command(name="division-deban", description="Lève le bannissement d'un membre (capitaine/vice).")
    @app_commands.describe(membre="Le membre à débannir")
    async def division_deban(self, interaction: discord.Interaction, membre: discord.Member):
        guild = interaction.guild
        author = interaction.user

        resolved = await resolve_staff_division(guild, author)
        if resolved is None:
            await interaction.response.send_message(embed=error_embed("Impossible de déterminer ta division."), ephemeral=True)
            return
        div, division_role, division_number = resolved

        vice_role = guild.get_role(VICE_ROLE_ID)
        if not (author.id == div["captain_id"] or (vice_role and vice_role in author.roles)):
            await interaction.response.send_message(embed=error_embed("Seuls le capitaine et le vice-capitaine peuvent débannir."), ephemeral=True)
            return

        ban = await db.get_division_ban(membre.id, division_number)
        if ban is None:
            await interaction.response.send_message(embed=error_embed(f"{membre.mention} n'est pas banni de cette division."), ephemeral=True)
            return

        await db.clear_division_ban(membre.id, division_number)
        await interaction.response.send_message(
            embed=success_embed("Bannissement levé", f"{membre.mention} peut à nouveau postuler à la division {division_number}."),
        )

    # -----------------------------------------------------------------
    # /division-mute  /  /division-demute
    # -----------------------------------------------------------------

    @app_commands.command(name="division-mute", description="Coupe l'écriture d'un membre dans ta division (capitaine/vice).")
    @app_commands.describe(membre="Le membre à muter", duree="Durée optionnelle (ex: 30m, 12h, 7j). Laisse vide pour un mute jusqu'à levée manuelle.")
    async def division_mute(self, interaction: discord.Interaction, membre: discord.Member, duree: str = None):
        guild = interaction.guild
        author = interaction.user

        resolved = await resolve_staff_division(guild, author)
        if resolved is None:
            await interaction.response.send_message(embed=error_embed("Impossible de déterminer ta division."), ephemeral=True)
            return
        div, division_role, division_number = resolved

        vice_role = guild.get_role(VICE_ROLE_ID)
        is_captain = author.id == div["captain_id"]
        is_vice = vice_role is not None and vice_role in author.roles
        if not (is_captain or is_vice):
            await interaction.response.send_message(embed=error_embed("Seuls le capitaine et le vice-capitaine peuvent muter."), ephemeral=True)
            return
        if membre.id == author.id:
            await interaction.response.send_message(embed=error_embed("Tu ne peux pas te muter toi-même."), ephemeral=True)
            return
        if division_role not in membre.roles:
            await interaction.response.send_message(embed=error_embed(f"{membre.mention} n'est pas dans ta division."), ephemeral=True)
            return
        if not can_target_for_mute(guild, author, membre, is_captain):
            await interaction.response.send_message(embed=error_embed("Tu n'as pas l'autorité pour muter ce membre."), ephemeral=True)
            return

        until_ts = None
        if duree:
            seconds = parse_duration(duree)
            if seconds is None:
                await interaction.response.send_message(
                    embed=error_embed("Format de durée invalide. Utilise un nombre suivi de m, h ou j (ex: 30m, 12h, 7j)."), ephemeral=True
                )
                return
            until_ts = int(discord.utils.utcnow().timestamp()) + seconds

        category = guild.get_channel(div["category_id"])
        if category:
            try:
                await category.set_permissions(membre, send_messages=False, reason=f"Mute division par {author}")
            except discord.HTTPException:
                pass

        await db.set_division_mute(membre.id, division_number, until_ts)
        duration_txt = f"jusqu'à <t:{until_ts}:F>" if until_ts else "jusqu'à levée manuelle"
        await interaction.response.send_message(
            embed=success_embed("Membre muté", f"{membre.mention} ne peut plus écrire dans la division {duration_txt}."), ephemeral=True
        )

    @app_commands.command(name="division-demute", description="Lève le mute d'un membre (capitaine/vice).")
    @app_commands.describe(membre="Le membre à démuter")
    async def division_demute(self, interaction: discord.Interaction, membre: discord.Member):
        guild = interaction.guild
        author = interaction.user

        resolved = await resolve_staff_division(guild, author)
        if resolved is None:
            await interaction.response.send_message(embed=error_embed("Impossible de déterminer ta division."), ephemeral=True)
            return
        div, division_role, division_number = resolved

        vice_role = guild.get_role(VICE_ROLE_ID)
        if not (author.id == div["captain_id"] or (vice_role and vice_role in author.roles)):
            await interaction.response.send_message(embed=error_embed("Seuls le capitaine et le vice-capitaine peuvent démuter."), ephemeral=True)
            return

        mute = await db.get_division_mute(membre.id, division_number)
        if mute is None:
            await interaction.response.send_message(embed=error_embed(f"{membre.mention} n'est pas muté dans cette division."), ephemeral=True)
            return

        category = guild.get_channel(div["category_id"])
        if category:
            try:
                await category.set_permissions(membre, overwrite=None, reason=f"Démute par {author}")
            except discord.HTTPException:
                pass

        await db.clear_division_mute(membre.id, division_number)
        await interaction.response.send_message(
            embed=success_embed("Membre démuté", f"{membre.mention} peut de nouveau écrire dans la division {division_number}."), ephemeral=True
        )

    # -----------------------------------------------------------------
    # /promouvoir  /  /demouvoir
    # -----------------------------------------------------------------

    @app_commands.command(name="promouvoir", description="Promeut un membre de ta division à un grade (capitaine).")
    @app_commands.describe(membre="Le membre à promouvoir", grade="Le grade à attribuer")
    @app_commands.choices(grade=[
        app_commands.Choice(name="Vice-capitaine", value="vice"),
        app_commands.Choice(name="Lieutenant", value="lieutenant"),
    ])
    async def promouvoir(self, interaction: discord.Interaction, membre: discord.Member, grade: app_commands.Choice[str]):
        guild = interaction.guild
        author = interaction.user

        resolved = await resolve_staff_division(guild, author)
        if resolved is None:
            await interaction.response.send_message(embed=error_embed("Impossible de déterminer ta division."), ephemeral=True)
            return
        div, division_role, division_number = resolved

        if author.id != div["captain_id"]:
            await interaction.response.send_message(embed=error_embed("Seul le capitaine peut promouvoir un membre."), ephemeral=True)
            return
        if membre.id == author.id:
            await interaction.response.send_message(embed=error_embed("Tu ne peux pas te promouvoir toi-même."), ephemeral=True)
            return
        if division_role not in membre.roles:
            await interaction.response.send_message(embed=error_embed(f"{membre.mention} n'est pas dans ta division."), ephemeral=True)
            return

        grade_role_id = VICE_ROLE_ID if grade.value == "vice" else LIEUTENANT_ROLE_ID
        grade_role = guild.get_role(grade_role_id)
        if grade_role is None:
            await interaction.response.send_message(embed=error_embed(f"Le rôle {grade.name} est introuvable sur ce serveur."), ephemeral=True)
            return
        if grade_role in membre.roles:
            await interaction.response.send_message(embed=error_embed(f"{membre.mention} est déjà {grade.name.lower()} de cette division."), ephemeral=True)
            return

        current_holder = get_grade_holder(guild, division_role, grade_role_id)
        if current_holder is not None:
            await interaction.response.send_message(
                embed=error_embed(f"{current_holder.mention} est déjà {grade.name.lower()} de cette division. Démouvoir-le d'abord avec `/demouvoir`."),
                ephemeral=True,
            )
            return

        warning = discord.Embed(
            title="⚠️ Confirmation de promotion",
            description=(
                f"Tu es sur le point de promouvoir {membre.mention} au grade de **{grade.name}** "
                f"dans la division {division_number}.\n\n"
                "Une annonce sera faite dans le salon annonces de la division."
            ),
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(embed=warning, view=ConfirmPromoteView(author.id, membre.id, division_number, grade.value, grade.name), ephemeral=True)

    @app_commands.command(name="demouvoir", description="Retire le grade d'un membre de ta division (capitaine).")
    @app_commands.describe(membre="Le membre à démouvoir")
    async def demouvoir(self, interaction: discord.Interaction, membre: discord.Member):
        guild = interaction.guild
        author = interaction.user

        resolved = await resolve_staff_division(guild, author)
        if resolved is None:
            await interaction.response.send_message(embed=error_embed("Impossible de déterminer ta division."), ephemeral=True)
            return
        div, division_role, division_number = resolved

        if author.id != div["captain_id"]:
            await interaction.response.send_message(embed=error_embed("Seul le capitaine peut démouvoir un membre."), ephemeral=True)
            return

        grade_roles = get_grade_roles(guild, membre)
        if not grade_roles:
            await interaction.response.send_message(embed=error_embed(f"{membre.mention} n'a aucun grade dans cette division."), ephemeral=True)
            return

        is_admin = author.guild_permissions.administrator
        if not is_admin:
            now = int(discord.utils.utcnow().timestamp())
            for role in grade_roles:
                grade_key = "vice" if role.id == VICE_ROLE_ID else "lieutenant"
                mandate = await db.get_active_grade_mandate(membre.id, division_number, grade_key)
                if mandate and mandate["protected_until"] > now:
                    await interaction.response.send_message(
                        embed=error_embed(f"{membre.mention} est protégé par son mandat jusqu'à <t:{mandate['protected_until']}:F>."),
                        ephemeral=True,
                    )
                    return

        await interaction.response.defer(ephemeral=True)
        try:
            await membre.remove_roles(*grade_roles, reason=f"Démotion par {author}")
        except discord.HTTPException:
            await interaction.followup.send(embed=error_embed("Impossible de retirer le(s) rôle(s) de grade."), ephemeral=True)
            return

        for role in grade_roles:
            grade_key = "vice" if role.id == VICE_ROLE_ID else "lieutenant"
            await db.clear_grade_mandate(membre.id, division_number, grade_key)

        announce_channel = guild.get_channel(div["announce_channel_id"]) if div["announce_channel_id"] else None
        if announce_channel:
            try:
                await announce_channel.send(embed=discord.Embed(
                    description=f"📉 {membre.mention} a été rétrogradé au rang de membre par {author.mention}.",
                    color=discord.Color.orange(),
                ))
            except discord.HTTPException:
                pass

        await interaction.followup.send(
            embed=success_embed("Membre dému", f"{membre.mention} a perdu son/ses grade(s) dans la division {division_number}."), ephemeral=True
        )

    # -----------------------------------------------------------------
    # /bloquer-division  /  /division-debloquer
    # -----------------------------------------------------------------

    @app_commands.command(name="bloquer-division", description="Bloque les invitations d'une division.")
    @app_commands.describe(division="Numéro de la division à bloquer")
    @app_commands.autocomplete(division=division_autocomplete)
    async def bloquer_division(self, interaction: discord.Interaction, division: int):
        if await db.get_division(division) is None:
            await interaction.response.send_message(embed=error_embed(f"La division {division} n'existe pas."), ephemeral=True)
            return
        if await db.is_division_blocked(interaction.user.id, division):
            await interaction.response.send_message(embed=error_embed("Tu bloques déjà cette division."), ephemeral=True)
            return
        await db.block_division(interaction.user.id, division)
        await interaction.response.send_message(embed=success_embed("Division bloquée", f"Tu ne recevras plus d'invitations de la division {division}."), ephemeral=True)

    @app_commands.command(name="division-debloquer", description="Débloque une division précédemment bloquée.")
    @app_commands.describe(division="Numéro de la division à débloquer")
    @app_commands.autocomplete(division=division_autocomplete)
    async def division_debloquer(self, interaction: discord.Interaction, division: int):
        if not await db.is_division_blocked(interaction.user.id, division):
            await interaction.response.send_message(embed=error_embed("Tu ne bloques pas cette division."), ephemeral=True)
            return
        await db.unblock_division(interaction.user.id, division)
        await interaction.response.send_message(embed=success_embed("Division débloquée", f"La division {division} peut à nouveau t'inviter."), ephemeral=True)

    # -----------------------------------------------------------------
    # /division-dissoudre
    # -----------------------------------------------------------------

    @app_commands.command(name="division-dissoudre", description="Dissout complètement une division (Administrateur uniquement).")
    @app_commands.describe(division="Numéro de la division à dissoudre")
    @app_commands.autocomplete(division=division_autocomplete)
    async def division_dissoudre(self, interaction: discord.Interaction, division: int):
        div = await db.get_division(division)
        if div is None:
            await interaction.response.send_message(embed=error_embed(f"La division {division} n'existe pas."), ephemeral=True)
            return

        is_admin = interaction.user.guild_permissions.administrator
        is_own_captain = interaction.user.id == div["captain_id"]

        if not is_admin and not is_own_captain:
            await interaction.response.send_message(embed=error_embed("Tu ne peux dissoudre que ta propre division."), ephemeral=True)
            return

        apply_sanction = not is_admin

        sanction_lines = (
            "• Bloquer la création/promotion capitaine du capitaine pendant **7 jours**\n"
            "• Bloquer l'entrée en division du capitaine pendant **24h**\n\n"
            if apply_sanction else "\n"
        )
        admin_note = "\nLes membres administrateurs conservent leur rôle de division.\n" if is_admin else ""
        warning = discord.Embed(
            title="⚠️ Dissolution de division",
            description=(
                f"Tu es sur le point de **dissoudre définitivement la division {division}**.\n\n"
                "Cette action va :\n"
                "• Supprimer tous les salons et la catégorie\n"
                "• Retirer le rôle de division aux membres non-administrateurs\n"
                "• Retirer le rôle **Capitaine** au capitaine actuel (si non-admin)\n"
                f"{sanction_lines}{admin_note}"
                "**Action irréversible.**"
            ),
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=warning, view=ConfirmDissolveView(interaction.user.id, division, apply_sanction), ephemeral=True)

    # -----------------------------------------------------------------
    # /sanction-retirer
    # -----------------------------------------------------------------

    @app_commands.command(name="sanction-retirer", description="Retire toutes les sanctions/timeouts de division d'un membre (Administrateur).")
    @app_commands.describe(membre="Le membre dont on retire les sanctions")
    async def sanction_retirer(self, interaction: discord.Interaction, membre: discord.Member):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(embed=error_embed("Réservé aux administrateurs."), ephemeral=True)
            return

        guild = interaction.guild
        sanction = await db.get_sanction(membre.id)
        mutes = await db.get_all_mutes_for_user(membre.id)

        # Lever les mutes actifs côté Discord avant de nettoyer la base.
        for mute in mutes:
            div = await db.get_division(mute["division_number"])
            if div is None:
                continue
            category = guild.get_channel(div["category_id"])
            member = guild.get_member(membre.id)
            if category and member:
                try:
                    await category.set_permissions(member, overwrite=None, reason="Sanction retirée par un administrateur")
                except discord.HTTPException:
                    pass

        await db.clear_sanction(membre.id)
        await db.clear_all_leave_cooldowns_for_user(membre.id)
        await db.clear_all_bans_for_user(membre.id)
        await db.clear_all_mutes_for_user(membre.id)
        await db.clear_all_expulsions_for_user(membre.id)

        await interaction.response.send_message(
            embed=success_embed(
                "Sanctions retirées",
                f"Toutes les restrictions de division de {membre.mention} ont été levées "
                "(attentes de départ, bans, mutes, expulsions).",
            ),
            ephemeral=True,
        )

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        logger.exception("Erreur non gérée sur une commande de Divisions", exc_info=error)
        embed = error_embed("Une erreur inattendue est survenue. Réessaie dans un instant.")
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    # -----------------------------------------------------------------
    # Commandes staff (préfixe, permission "Gérer les messages" minimum)
    # -----------------------------------------------------------------

    @commands.command(name="tagall")
    @commands.has_permissions(manage_messages=True)
    async def tagall(self, ctx: commands.Context):
        """d!tagall — réapplique le tag『ɗivN』à tous les membres d'une division
        qui ne l'ont plus dans leur pseudo (rattrapage manuel, ex : après une
        mise à jour, ou pour vérifier ponctuellement l'état du serveur)."""
        notice = await ctx.send(embed=discord.Embed(
            description=f"🔄 Vérification des pseudos en cours sur **{ctx.guild.member_count}** membres...",
            color=discord.Color.blurple(),
        ))

        counters = await self._scan_and_fix_all_tags(ctx.guild)

        embed = discord.Embed(title="🏷️ Rattrapage des tags de division", color=discord.Color.green())
        embed.add_field(name="✅ Tag réappliqué", value=str(counters["fixed"]), inline=True)
        embed.add_field(name="✔️ Déjà en ordre", value=str(counters["ok"]), inline=True)
        embed.add_field(name="🚫 Sans division", value=str(counters["no_division"]), inline=True)
        if counters["forbidden"]:
            embed.add_field(
                name="⚠️ Ignoré (permission)",
                value=f"{counters['forbidden']} membre(s) — rôle du bot trop bas ou non modifiable.",
                inline=False,
            )
        if counters["error"]:
            embed.add_field(name="❌ Erreur", value=str(counters["error"]), inline=False)
        embed.set_footer(text=f"Lancé par {ctx.author.display_name}")

        await notice.edit(embed=embed)

    @commands.command(name="skick")
    @commands.has_permissions(manage_messages=True)
    async def skick(self, ctx: commands.Context, membre: discord.Member, *args: str):
        """d!skick @membre [division] — expulse un membre de sa division."""
        guild = ctx.guild
        division_number, _ = parse_staff_args(args)
        division_number = resolve_division_number(membre, division_number)
        if division_number is None:
            await ctx.send(embed=error_embed("Précise le numéro de division : `d!skick @membre 3`."))
            return
        div = await db.get_division(division_number)
        if div is None:
            await ctx.send(embed=error_embed(f"La division {division_number} n'existe pas."))
            return
        division_role = guild.get_role(div["role_id"])
        if division_role is None or division_role not in membre.roles:
            await ctx.send(embed=error_embed(f"{membre.mention} n'est pas dans la division {division_number}."))
            return

        await strip_from_division(guild, membre, division_role, f"Kick staff par {ctx.author}", farewell="a été expulsé de")
        now = int(discord.utils.utcnow().timestamp())
        await db.set_division_expulsion(membre.id, division_number, now + EXPULSION_COOLDOWN)
        await ctx.send(embed=success_embed("Membre expulsé (staff)", f"{membre.mention} a été retiré de la division {division_number}."))

    @commands.command(name="sban")
    @commands.has_permissions(manage_messages=True)
    async def sban(self, ctx: commands.Context, membre: discord.Member, *args: str):
        """d!sban @membre [division] [durée] — bannit un membre d'une division."""
        guild = ctx.guild
        division_number, duration_seconds = parse_staff_args(args)
        division_number = resolve_division_number(membre, division_number)
        if division_number is None:
            await ctx.send(embed=error_embed("Précise le numéro de division : `d!sban @membre 3 7j`."))
            return
        div = await db.get_division(division_number)
        if div is None:
            await ctx.send(embed=error_embed(f"La division {division_number} n'existe pas."))
            return

        division_role = guild.get_role(div["role_id"])
        if division_role and division_role in membre.roles:
            await strip_from_division(guild, membre, division_role, f"Ban staff par {ctx.author}", farewell="a été banni de")

        until_ts = int(discord.utils.utcnow().timestamp()) + duration_seconds if duration_seconds else None
        await db.set_division_ban(membre.id, division_number, until_ts)
        duration_txt = f"jusqu'à <t:{until_ts}:F>" if until_ts else "de façon permanente"
        await ctx.send(embed=success_embed("Membre banni (staff)", f"{membre.mention} est banni de la division {division_number} {duration_txt}."))

    @commands.command(name="sreset")
    @commands.has_permissions(manage_messages=True)
    async def sreset(self, ctx: commands.Context, division_number: int):
        """d!sreset <division> — réinitialise toutes les infos de la division demandée."""
        div = await db.get_division(division_number)
        if div is None:
            await ctx.send(embed=error_embed(f"La division {division_number} n'existe pas."))
            return
        embed = discord.Embed(
            title="⚠️ Confirmation de réinitialisation",
            description=(
                f"Tu t'apprêtes à réinitialiser **toutes les informations de la division {division_number}**.\n"
                "Cela remettra à zéro le profil de division, les images, la visibilité et la configuration avancée."
            ),
            color=discord.Color.orange(),
        )
        await ctx.send(embed=embed, view=StaffDivisionResetView(division_number, ctx.author.id))

    @commands.command(name="sunban")
    @commands.has_permissions(manage_messages=True)
    async def sunban(self, ctx: commands.Context, membre: discord.Member, *args: str):
        """d!sunban @membre [division] — lève un bannissement de division."""
        division_number, _ = parse_staff_args(args)
        division_number = resolve_division_number(membre, division_number)
        if division_number is None:
            await ctx.send(embed=error_embed("Précise le numéro de division : `d!sunban @membre 3`."))
            return
        ban = await db.get_division_ban(membre.id, division_number)
        if ban is None:
            await ctx.send(embed=error_embed(f"{membre.mention} n'est pas banni de la division {division_number}."))
            return
        await db.clear_division_ban(membre.id, division_number)
        await ctx.send(embed=success_embed("Bannissement levé (staff)", f"{membre.mention} peut de nouveau rejoindre la division {division_number}."))

    @commands.command(name="smute")
    @commands.has_permissions(manage_messages=True)
    async def smute(self, ctx: commands.Context, membre: discord.Member, *args: str):
        """d!smute @membre [division] [durée] — coupe l'écriture d'un membre dans sa division."""
        guild = ctx.guild
        division_number, duration_seconds = parse_staff_args(args)
        division_number = resolve_division_number(membre, division_number)
        if division_number is None:
            await ctx.send(embed=error_embed("Précise le numéro de division : `d!smute @membre 3 12h`."))
            return
        div = await db.get_division(division_number)
        if div is None:
            await ctx.send(embed=error_embed(f"La division {division_number} n'existe pas."))
            return

        category = guild.get_channel(div["category_id"])
        if category:
            try:
                await category.set_permissions(membre, send_messages=False, reason=f"Mute staff par {ctx.author}")
            except discord.HTTPException:
                pass

        until_ts = int(discord.utils.utcnow().timestamp()) + duration_seconds if duration_seconds else None
        await db.set_division_mute(membre.id, division_number, until_ts)
        duration_txt = f"jusqu'à <t:{until_ts}:F>" if until_ts else "jusqu'à levée manuelle"
        await ctx.send(embed=success_embed("Membre muté (staff)", f"{membre.mention} ne peut plus écrire dans la division {division_number} {duration_txt}."))

    @commands.command(name="sunmute")
    @commands.has_permissions(manage_messages=True)
    async def sunmute(self, ctx: commands.Context, membre: discord.Member, *args: str):
        """d!sunmute @membre [division] — lève un mute de division."""
        guild = ctx.guild
        division_number, _ = parse_staff_args(args)
        division_number = resolve_division_number(membre, division_number)
        if division_number is None:
            await ctx.send(embed=error_embed("Précise le numéro de division : `d!sunmute @membre 3`."))
            return
        mute = await db.get_division_mute(membre.id, division_number)
        if mute is None:
            await ctx.send(embed=error_embed(f"{membre.mention} n'est pas muté dans la division {division_number}."))
            return

        div = await db.get_division(division_number)
        category = guild.get_channel(div["category_id"]) if div else None
        if category:
            try:
                await category.set_permissions(membre, overwrite=None, reason=f"Démute staff par {ctx.author}")
            except discord.HTTPException:
                pass

        await db.clear_division_mute(membre.id, division_number)
        await ctx.send(embed=success_embed("Membre démuté (staff)", f"{membre.mention} peut de nouveau écrire dans la division {division_number}."))

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(embed=error_embed("Tu n'as pas la permission pour cette action."))
            return
        if isinstance(error, commands.MemberNotFound):
            await ctx.send(embed=error_embed("Membre introuvable."))
            return
        if isinstance(error, commands.CommandNotFound):
            return
        logger.exception("Erreur non gérée sur une commande staff de Divisions", exc_info=error)


async def setup(bot: commands.Bot):
    await bot.add_cog(Divisions(bot))