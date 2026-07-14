"""
Cog de configuration de profil de division (Gotei RP Bleach).

/division-config : configure le profil complet d'une division (capitaine uniquement).
Toutes les réponses (texte ou image) se donnent en écrivant dans le salon ; les messages
du capitaine sont supprimés après traitement pour garder le salon propre. Aucun modal.
Première utilisation -> assistant pas-à-pas (questions simples, preview uniquement à la fin,
au même format que /division-profil). Utilisations suivantes -> panel de modification.
Tout est piloté par une table de sessions persistante : un redémarrage du bot ne casse
ni un assistant en cours, ni une confirmation de couleur/badge en attente.
"""
import re
import sys
import time
import base64
import io
import pathlib
import logging

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

sys.path.append(str(pathlib.Path(__file__).parent.parent))
from data import database as db
from data import division_profiles as pdb
from data import division_profile as profile_mod
from cogs import divisions as div_mod
import utils

logger = logging.getLogger("urahara.division_config")

STYLE_COOLDOWN = 7 * 86400
URL_PATTERN = re.compile(r"^https?://\S+$")
HEX_PATTERN = re.compile(r"^#?[0-9A-Fa-f]{6}$")
CUSTOM_EMOJI_ID_PATTERN = re.compile(r"^<a?:\w+:(\d+)>$")
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".apng")
DIRECT_IMAGE_HOSTS = ("cdn.discordapp.com", "media.discordapp.net", "media.tenor.com", "i.giphy.com", "media.giphy.com")
OG_IMAGE_PATTERN = re.compile(r'property=["\']og:image(?::secure_url)?["\']\s+content=["\']([^"\']+)["\']', re.I)
OG_IMAGE_PATTERN_ALT = re.compile(r'content=["\']([^"\']+)["\']\s+property=["\']og:image["\']', re.I)

WIZARD_STEPS = ["name", "description", "min_age", "reglement", "pp", "banner", "preview"]
FIELD_TYPE = {"name": "text", "description": "text", "min_age": "int", "reglement": "text", "pp": "image", "banner": "image"}
FIELD_TO_COLUMN = {"name": "custom_name", "description": "description", "min_age": "min_age", "reglement": "reglement", "pp": "pfp_url", "banner": "banner_url"}
FIELD_CATEGORY = {"name": "infos", "description": "infos", "min_age": "infos", "reglement": "infos", "pp": "images", "banner": "images"}
MAX_LEN = {"name": 100, "description": 1000, "reglement": 1500}
STEP_QUESTIONS = {
    "name": ("Nom personnalisé", "Écris le nom personnalisé de la division, ou `skip` pour garder « Division N »."),
    "description": ("Description", "Écris une description pour la division, ou `skip`."),
    "min_age": ("Âge minimum", "Écris un nombre (âge minimum requis), ou `skip` pour aucune restriction."),
    "reglement": ("Règlement interne", "Écris le règlement interne, ou `skip` (modifiable plus tard)."),
    "pp": ("Photo de profil", "Envoie une image en pièce jointe, ou un lien (GIF Discord/Tenor/Giphy supporté), ou `skip`."),
    "banner": ("Bannière", "Envoie une image en format paysage (pièce jointe ou lien), ou `skip`."),
}
FIELD_LABELS = {"name": "le nom", "description": "la description", "min_age": "l'âge minimum", "reglement": "le règlement", "pp": "la photo de profil", "banner": "la bannière"}


# ---------------------------------------------------------------------------
# Helpers génériques
# ---------------------------------------------------------------------------

async def resolve_captain_context(interaction: discord.Interaction):
    """(div, division_role, division_number) si l'auteur est capitaine d'une division active, sinon None (message déjà envoyé)."""
    guild, author = interaction.guild, interaction.user
    captain_role = guild.get_role(div_mod.CAPTAIN_ROLE_ID)
    if captain_role is None:
        await interaction.response.send_message(
            embed=div_mod.error_embed("Le rôle Capitaine est introuvable sur ce serveur. Vérifie l'ID du rôle dans ton .env."),
            ephemeral=True,
        )
        return None
    if not any(role.id == div_mod.CAPTAIN_ROLE_ID for role in author.roles):
        await interaction.response.send_message(embed=div_mod.error_embed("Tu dois être Capitaine pour configurer une division."), ephemeral=True)
        return None

    roles = div_mod.find_division_roles(author)
    if len(roles) != 1:
        await interaction.response.send_message(
            embed=div_mod.error_embed(
                "Impossible de déterminer ta division. Vérifie que tu portes bien un seul rôle de division valide (ex : `Division 1`, `1ère division`)."
            ),
            ephemeral=True,
        )
        return None

    number = div_mod.extract_division_number(roles[0])
    if number is None:
        await interaction.response.send_message(embed=div_mod.error_embed("Impossible de lire le numéro de ta division."), ephemeral=True)
        return None

    div = await db.get_division(number)
    if div is None:
        await interaction.response.send_message(embed=div_mod.error_embed("Cette division n'existe pas encore dans la base de données."), ephemeral=True)
        return None
    if div["captain_id"] != author.id:
        await interaction.response.send_message(embed=div_mod.error_embed("Seul le capitaine officiel de cette division peut la configurer."), ephemeral=True)
        return None

    return div, roles[0], number


async def get_session_for_interaction(interaction: discord.Interaction):
    if interaction.message is None:
        await interaction.response.send_message(embed=div_mod.error_embed("Session introuvable."), ephemeral=True)
        return None
    session = await pdb.get_session_by_message(interaction.message.id)
    if session is None:
        await interaction.response.send_message(embed=div_mod.error_embed("Cette session a expiré ou a déjà été traitée."), ephemeral=True)
        return None
    if interaction.user.id != session["user_id"]:
        await interaction.response.send_message(embed=div_mod.error_embed("Seul le capitaine à l'origine peut interagir ici."), ephemeral=True)
        return None
    return session


async def fetch_session_message(guild: discord.Guild, session) -> discord.Message | None:
    channel = guild.get_channel(session["channel_id"])
    if channel is None or session["message_id"] is None:
        return None
    try:
        return await channel.fetch_message(session["message_id"])
    except discord.HTTPException:
        return None


async def fetch_bytes(url: str) -> bytes | None:
    try:
        async with aiohttp.ClientSession() as http:
            async with http.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None
                return await resp.read()
    except aiohttp.ClientError:
        return None


async def fetch_text(url: str) -> str | None:
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; UraharaBot/1.0)"}
        async with aiohttp.ClientSession() as http:
            async with http.get(url, timeout=aiohttp.ClientTimeout(total=8), headers=headers) as resp:
                if resp.status != 200:
                    return None
                return await resp.text()
    except aiohttp.ClientError:
        return None


async def resolve_image_url(raw: str) -> str:
    """Résout un lien de page (giphy.com/gifs/..., tenor.com/view/...) vers l'URL directe du média via og:image."""
    lower = raw.split("?")[0].lower()
    if lower.endswith(IMAGE_EXTENSIONS) or any(host in raw.lower() for host in DIRECT_IMAGE_HOSTS):
        return raw
    html = await fetch_text(raw)
    if html is None:
        return raw
    match = OG_IMAGE_PATTERN.search(html) or OG_IMAGE_PATTERN_ALT.search(html)
    return match.group(1) if match else raw


async def safe_delete(message: discord.Message):
    try:
        await message.delete()
    except discord.HTTPException:
        pass


async def safe_notice(channel: discord.abc.Messageable, text: str):
    try:
        await channel.send(embed=div_mod.error_embed(text), delete_after=6)
    except discord.HTTPException:
        pass


# ---------------------------------------------------------------------------
# Embeds
# ---------------------------------------------------------------------------

def _color_display(profile) -> str:
    if not profile["color_primary"]:
        return "Non définie"
    if profile["color_secondary"]:
        return f"#{profile['color_primary']} → #{profile['color_secondary']}"
    return f"#{profile['color_primary']}"


def wizard_step_embed(number: int, step: str) -> discord.Embed:
    title, question = STEP_QUESTIONS[step]
    idx = WIZARD_STEPS.index(step)
    embed = discord.Embed(
        title=f"⚙️ Configuration • Division {number}",
        description=f"**Étape {idx + 1}/{len(WIZARD_STEPS) - 1} — {title}**\n\n{question}",
        color=discord.Color.blurple(),
    )
    embed.set_footer(text="Urahara • Écris ta réponse dans ce salon")
    return embed


async def wizard_preview_embed(guild: discord.Guild, number: int, div, data: dict) -> discord.Embed:
    preview_profile = {
        "custom_name": data.get("name"), "description": data.get("description"), "min_age": data.get("min_age"),
        "pfp_url": data.get("pp"), "banner_url": data.get("banner"), "reglement": data.get("reglement"),
        "visibility": "public",
    }
    embed = await profile_mod.build_profile_embed(guild, number, preview_profile, div)
    embed.title = f"👁️ Aperçu — {embed.title}"
    embed.set_footer(text="Vérifie les informations puis valide.")
    return embed


def summary_embed(number: int, profile) -> discord.Embed:
    name = profile["custom_name"] or f"Division {number}"
    embed = discord.Embed(title=f"⚙️ Profil — {name}", description=profile["description"] or "*Aucune description.*", color=discord.Color.blurple())
    embed.add_field(name="⏳ Âge minimum", value=str(profile["min_age"]) if profile["min_age"] else "Aucune restriction", inline=True)
    embed.add_field(name="📋 Règlement", value="✅ Défini" if profile["reglement"] else "❌ Non défini", inline=True)
    embed.add_field(name="🎨 Couleur du rôle", value=_color_display(profile), inline=True)
    embed.add_field(name="🏅 Badge du rôle", value=profile["badge_source"] or "Aucun", inline=True)
    embed.add_field(name="🔐 Visibilité", value="🔒 Privée" if profile["visibility"] == "private" else "🌐 Publique", inline=True)
    if profile["pfp_url"]:
        embed.set_thumbnail(url=profile["pfp_url"])
    if profile["banner_url"]:
        embed.set_image(url=profile["banner_url"])
    embed.set_footer(text="Urahara • Panel de configuration")
    return embed


def infos_embed(number: int, profile) -> discord.Embed:
    embed = discord.Embed(title="📝 Infos générales", color=discord.Color.blurple())
    embed.add_field(name="📌 Nom", value=profile["custom_name"] or f"Division {number}", inline=False)
    embed.add_field(name="📄 Description", value=profile["description"] or "*Non définie*", inline=False)
    embed.add_field(name="⏳ Âge minimum", value=str(profile["min_age"]) if profile["min_age"] else "Aucune restriction", inline=True)
    embed.add_field(name="📋 Règlement", value="✅ Défini" if profile["reglement"] else "❌ Non défini", inline=True)
    return embed


def images_embed(profile) -> discord.Embed:
    embed = discord.Embed(title="🖼️ Images", color=discord.Color.blurple())
    embed.add_field(name="Photo de profil", value="Définie ✅" if profile["pfp_url"] else "Non définie", inline=True)
    embed.add_field(name="Bannière", value="Définie ✅" if profile["banner_url"] else "Non définie", inline=True)
    if profile["pfp_url"]:
        embed.set_thumbnail(url=profile["pfp_url"])
    if profile["banner_url"]:
        embed.set_image(url=profile["banner_url"])
    return embed


def advanced_embed(guild: discord.Guild, profile) -> discord.Embed:
    embed = discord.Embed(title="🎨 Configuration avancée", color=discord.Color.blurple())
    embed.add_field(name="🎨 Couleur du rôle", value=_color_display(profile), inline=True)
    embed.add_field(name="🏅 Badge du rôle", value=profile["badge_source"] or "Aucun", inline=True)
    embed.add_field(name="🔐 Visibilité", value="🔒 Privée" if profile["visibility"] == "private" else "🌐 Publique", inline=True)
    now = int(time.time())
    if profile["color_cooldown_until"] and profile["color_cooldown_until"] > now:
        embed.add_field(name="⏱️ Cooldown couleur", value=f"<t:{profile['color_cooldown_until']}:R>", inline=False)
    if profile["badge_cooldown_until"] and profile["badge_cooldown_until"] > now:
        embed.add_field(name="⏱️ Cooldown badge", value=f"<t:{profile['badge_cooldown_until']}:R>", inline=False)
    embed.add_field(name="🌈 Dégradé de rôle", value="✅ Disponible" if "ENHANCED_ROLE_COLORS" in guild.features else "❌ Non débloqué", inline=True)
    embed.add_field(name="🖼️ Icônes de rôle", value="✅ Disponible" if "ROLE_ICONS" in guild.features else "❌ Non débloqué", inline=True)
    return embed


def edit_prompt_embed(field_key: str) -> discord.Embed:
    embed = discord.Embed(title="📝 Modification", description=f"✏️ Écris ta réponse dans ce salon pour **{FIELD_LABELS[field_key]}** (ou `skip`/`-` pour vider ce champ).", color=discord.Color.orange())
    embed.set_footer(text="En attente de ta réponse...")
    return embed


def color_confirm_embed(hex1: str, hex2: str | None) -> discord.Embed:
    embed = discord.Embed(title="🎨 Aperçu couleur", color=discord.Color(int(hex1, 16)))
    embed.description = f"🌈 Dégradé `#{hex1}` → `#{hex2}`" if hex2 else f"🎨 Couleur unie `#{hex1}`"
    embed.set_footer(text="Confirme pour appliquer")
    return embed


def badge_confirm_embed(data: dict) -> tuple[discord.Embed, list[discord.File]]:
    embed = discord.Embed(title="🏅 Aperçu badge", description="Confirme pour appliquer ce badge au rôle.", color=discord.Color.blurple())
    files = []
    if data["is_image"]:
        raw = base64.b64decode(data["value"])
        files.append(discord.File(io.BytesIO(raw), filename="badge.png"))
        embed.set_thumbnail(url="attachment://badge.png")
    else:
        embed.description += f"\n🎭 Emoji : {data['value']}"
    embed.set_footer(text="Confirme pour appliquer")
    return embed, files


# ---------------------------------------------------------------------------
# Vues persistantes réutilisables
# ---------------------------------------------------------------------------

def build_await_view() -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    cancel = discord.ui.Button(label="Annuler", style=discord.ButtonStyle.danger, custom_id="divcfg_await_cancel")
    cancel.callback = await_cancel_callback
    view.add_item(cancel)
    return view


def build_confirm_view() -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    apply_btn = discord.ui.Button(label="✅ Confirmer", style=discord.ButtonStyle.success, custom_id="divcfg_confirm_apply")
    apply_btn.callback = confirm_apply_callback
    cancel_btn = discord.ui.Button(label="Annuler", style=discord.ButtonStyle.danger, custom_id="divcfg_confirm_cancel")
    cancel_btn.callback = await_cancel_callback
    view.add_item(apply_btn)
    view.add_item(cancel_btn)
    return view


def reset_confirm_embed(number: int) -> discord.Embed:
    return discord.Embed(
        title="⚠️ Réinitialiser le profil",
        description=(
            f"Tu es sur le point de réinitialiser **toutes les informations de la division {number}**.\n"
            "Cela remettra à zéro les champs du profil, les images, la visibilité et les données avancées."
        ),
        color=discord.Color.red(),
    )


class ResetProfileConfirmView(discord.ui.View):
    def __init__(self, number: int):
        super().__init__(timeout=300)
        self.number = number

    @discord.ui.button(label="⚠️ Réinitialiser", style=discord.ButtonStyle.danger, custom_id="divcfg_reset_confirm")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        ctx = await resolve_captain_context(interaction)
        if ctx is None:
            return
        _, _, number = ctx
        await pdb.reset_profile(number)
        profile = await pdb.get_profile(number)
        embed = div_mod.success_embed("Profil réinitialisé", f"Le profil de la division {number} a été remis à zéro.")
        await interaction.response.edit_message(embed=embed, view=MainConfigView())

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary, custom_id="divcfg_reset_cancel")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        ctx = await resolve_captain_context(interaction)
        if ctx is None:
            return
        _, _, number = ctx
        profile = await pdb.get_profile(number)
        await interaction.response.edit_message(embed=advanced_embed(interaction.guild, profile), view=AdvancedCategoryView(dict(profile)))


def build_wizard_view(step: str, history: list) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    back = discord.ui.Button(label="◀ Retour", style=discord.ButtonStyle.secondary, disabled=(len(history) == 0), custom_id="divcfg_wiz_back")
    back.callback = wizard_back_cb
    view.add_item(back)
    if step == "preview":
        validate = discord.ui.Button(label="✅ Valider", style=discord.ButtonStyle.success, custom_id="divcfg_wiz_validate")
        validate.callback = wizard_validate_cb
        view.add_item(validate)
    else:
        skip = discord.ui.Button(label="Passer ▶", style=discord.ButtonStyle.secondary, custom_id="divcfg_wiz_skip")
        skip.callback = wizard_skip_cb
        view.add_item(skip)
    cancel = discord.ui.Button(label="Annuler", style=discord.ButtonStyle.danger, custom_id="divcfg_wiz_cancel")
    cancel.callback = wizard_cancel_cb
    view.add_item(cancel)
    return view


async def render_wizard_step(interaction: discord.Interaction, session):
    step = session["step"]
    view = build_wizard_view(step, pdb.session_history(session))
    if step == "preview":
        div = await db.get_division(session["division_number"])
        embed = await wizard_preview_embed(interaction.guild, session["division_number"], div, pdb.session_data(session))
    else:
        embed = wizard_step_embed(session["division_number"], step)
    await interaction.response.edit_message(embed=embed, view=view)


async def render_wizard_step_message(message: discord.Message, session):
    step = session["step"]
    view = build_wizard_view(step, pdb.session_history(session))
    if step == "preview":
        div = await db.get_division(session["division_number"])
        embed = await wizard_preview_embed(message.guild, session["division_number"], div, pdb.session_data(session))
    else:
        embed = wizard_step_embed(session["division_number"], step)
    await message.edit(embed=embed, view=view)


# ---------------------------------------------------------------------------
# Callbacks - assistant
# ---------------------------------------------------------------------------

async def wizard_back_cb(interaction: discord.Interaction):
    session = await get_session_for_interaction(interaction)
    if session is None:
        return
    history = pdb.session_history(session)
    if not history:
        await interaction.response.send_message(embed=div_mod.info_embed("Tu es déjà à la première question."), ephemeral=True)
        return
    prev_step = history.pop()
    await pdb.update_session(session["id"], step=prev_step, history=history)
    session = await pdb.get_session(session["id"])
    await render_wizard_step(interaction, session)


async def wizard_skip_cb(interaction: discord.Interaction):
    session = await get_session_for_interaction(interaction)
    if session is None:
        return
    step = session["step"]
    if step == "preview":
        await interaction.response.send_message(embed=div_mod.info_embed("Rien à passer ici."), ephemeral=True)
        return
    history = pdb.session_history(session)
    history.append(step)
    next_step = WIZARD_STEPS[WIZARD_STEPS.index(step) + 1]
    await pdb.update_session(session["id"], step=next_step, history=history)
    session = await pdb.get_session(session["id"])
    await render_wizard_step(interaction, session)


async def wizard_cancel_cb(interaction: discord.Interaction):
    session = await get_session_for_interaction(interaction)
    if session is None:
        return
    await pdb.delete_session(session["id"])
    await interaction.response.edit_message(embed=div_mod.info_embed("Configuration annulée. Aucune modification enregistrée."), view=None)


async def wizard_validate_cb(interaction: discord.Interaction):
    session = await get_session_for_interaction(interaction)
    if session is None:
        return
    if session["step"] != "preview":
        await interaction.response.send_message(embed=div_mod.info_embed("Termine les étapes précédentes d'abord."), ephemeral=True)
        return
    data = pdb.session_data(session)
    number = session["division_number"]
    await pdb.ensure_profile(number)
    fields = {FIELD_TO_COLUMN[k]: v for k, v in data.items() if k in FIELD_TO_COLUMN}
    fields["setup_done"] = 1
    await pdb.update_profile_fields(number, **fields)
    await pdb.delete_session(session["id"])
    embed = div_mod.success_embed("Profil enregistré", f"Le profil de la **division {number}** a été configuré.\nRelance `/division-config` pour le gérer (couleur, badge, visibilité...).")
    await interaction.response.edit_message(embed=embed, view=None)


async def handle_wizard_message(message: discord.Message, session):
    step = session["step"]
    if step == "preview":
        return
    raw = message.content.strip()
    kind = FIELD_TYPE[step]
    value = None
    valid = True

    if raw.lower() in ("skip", "passer", "-"):
        value = None
    elif kind == "text":
        valid = bool(raw)
        value = raw[:MAX_LEN.get(step, 1000)] if valid else None
    elif kind == "int":
        valid = raw.isdigit() and int(raw) > 0
        value = int(raw) if valid else None
    elif kind == "image":
        if message.attachments:
            value = await utils.persist_image(message.guild, message.attachments[0])
            if value is None:
                valid = False
        elif URL_PATTERN.match(raw):
            resolved = await resolve_image_url(raw)
            value = await utils.persist_image_from_url(message.guild, resolved)
        else:
            valid = False

    await safe_delete(message)
    if not valid:
        await safe_notice(message.channel, "Réponse invalide pour cette étape (ou erreur d'enregistrement de l'image, réessaie).")
        return

    data = pdb.session_data(session)
    data[step] = value
    history = pdb.session_history(session)
    history.append(step)
    next_step = WIZARD_STEPS[WIZARD_STEPS.index(step) + 1]
    await pdb.update_session(session["id"], step=next_step, data=data, history=history)
    session = await pdb.get_session(session["id"])
    target = await fetch_session_message(message.guild, session)
    if target:
        await render_wizard_step_message(target, session)


async def start_wizard(interaction: discord.Interaction, division_number: int):
    session_id = await pdb.create_session(division_number, interaction.user.id, interaction.channel_id, "wizard", WIZARD_STEPS[0])
    embed = wizard_step_embed(division_number, WIZARD_STEPS[0])
    view = build_wizard_view(WIZARD_STEPS[0], [])
    await interaction.response.send_message(embed=embed, view=view)
    message = await interaction.original_response()
    await pdb.set_session_message(session_id, message.id)


# ---------------------------------------------------------------------------
# Callbacks - panel (infos / images / avancé)
# ---------------------------------------------------------------------------

async def back_to_main(interaction: discord.Interaction):
    ctx = await resolve_captain_context(interaction)
    if ctx is None:
        return
    _, _, number = ctx
    profile = await pdb.get_profile(number)
    await interaction.response.edit_message(embed=summary_embed(number, profile), view=MainConfigView())


async def start_edit(interaction: discord.Interaction, field_key: str):
    ctx = await resolve_captain_context(interaction)
    if ctx is None:
        return
    _, _, number = ctx
    session_id = await pdb.create_session(number, interaction.user.id, interaction.channel_id, "edit", field_key)
    await interaction.response.edit_message(embed=edit_prompt_embed(field_key), view=build_await_view())
    message = await interaction.original_response()
    await pdb.set_session_message(session_id, message.id)


async def handle_edit_message(message: discord.Message, session):
    step = session["step"]
    number = session["division_number"]
    raw = message.content.strip()
    kind = FIELD_TYPE[step]
    value = None
    valid = True

    if raw.lower() in ("skip", "-", "vider"):
        value = None
    elif kind == "text":
        valid = bool(raw)
        value = raw[:MAX_LEN.get(step, 1000)] if valid else None
    elif kind == "int":
        valid = raw.isdigit() and int(raw) > 0
        value = int(raw) if valid else None
    elif kind == "image":
        if message.attachments:
            value = await utils.persist_image(message.guild, message.attachments[0])
            if value is None:
                valid = False
        elif URL_PATTERN.match(raw):
            resolved = await resolve_image_url(raw)
            value = await utils.persist_image_from_url(message.guild, resolved)
        else:
            valid = False

    await safe_delete(message)
    if not valid:
        await safe_notice(message.channel, "Réponse invalide pour ce champ (ou erreur d'enregistrement de l'image, réessaie).")
        return

    column = FIELD_TO_COLUMN[step]
    await pdb.update_profile_fields(number, **{column: value})
    await pdb.delete_session(session["id"])
    profile = await pdb.get_profile(number)
    target = await fetch_session_message(message.guild, session)
    if target is None:
        return
    if FIELD_CATEGORY[step] == "infos":
        await target.edit(embed=infos_embed(number, profile), view=InfosCategoryView())
    else:
        await target.edit(embed=images_embed(profile), view=ImagesCategoryView())


class MainConfigView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📝 Infos", style=discord.ButtonStyle.primary, custom_id="divcfg_main_infos")
    async def infos(self, interaction: discord.Interaction, button: discord.ui.Button):
        ctx = await resolve_captain_context(interaction)
        if ctx is None:
            return
        _, _, number = ctx
        profile = await pdb.get_profile(number)
        await interaction.response.edit_message(embed=infos_embed(number, profile), view=InfosCategoryView())

    @discord.ui.button(label="🖼️ Images", style=discord.ButtonStyle.primary, custom_id="divcfg_main_images")
    async def images(self, interaction: discord.Interaction, button: discord.ui.Button):
        ctx = await resolve_captain_context(interaction)
        if ctx is None:
            return
        _, _, number = ctx
        profile = await pdb.get_profile(number)
        await interaction.response.edit_message(embed=images_embed(profile), view=ImagesCategoryView())

    @discord.ui.button(label="🎨 Avancé", style=discord.ButtonStyle.primary, custom_id="divcfg_main_advanced")
    async def advanced(self, interaction: discord.Interaction, button: discord.ui.Button):
        ctx = await resolve_captain_context(interaction)
        if ctx is None:
            return
        _, _, number = ctx
        profile = await pdb.get_profile(number)
        await interaction.response.edit_message(embed=advanced_embed(interaction.guild, profile), view=AdvancedCategoryView(dict(profile)))


class InfosCategoryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Nom", style=discord.ButtonStyle.secondary, custom_id="divcfg_infos_name")
    async def name(self, interaction: discord.Interaction, button: discord.ui.Button):
        await start_edit(interaction, "name")

    @discord.ui.button(label="Description", style=discord.ButtonStyle.secondary, custom_id="divcfg_infos_desc")
    async def description(self, interaction: discord.Interaction, button: discord.ui.Button):
        await start_edit(interaction, "description")

    @discord.ui.button(label="Âge minimum", style=discord.ButtonStyle.secondary, custom_id="divcfg_infos_age")
    async def age(self, interaction: discord.Interaction, button: discord.ui.Button):
        await start_edit(interaction, "min_age")

    @discord.ui.button(label="Règlement", style=discord.ButtonStyle.secondary, custom_id="divcfg_infos_reglement")
    async def reglement(self, interaction: discord.Interaction, button: discord.ui.Button):
        await start_edit(interaction, "reglement")

    @discord.ui.button(label="◀ Retour", style=discord.ButtonStyle.secondary, custom_id="divcfg_infos_back", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await back_to_main(interaction)


class ImagesCategoryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Photo de profil", style=discord.ButtonStyle.secondary, custom_id="divcfg_images_pp")
    async def pp(self, interaction: discord.Interaction, button: discord.ui.Button):
        await start_edit(interaction, "pp")

    @discord.ui.button(label="Bannière", style=discord.ButtonStyle.secondary, custom_id="divcfg_images_banner")
    async def banner(self, interaction: discord.Interaction, button: discord.ui.Button):
        await start_edit(interaction, "banner")

    @discord.ui.button(label="◀ Retour", style=discord.ButtonStyle.secondary, custom_id="divcfg_images_back", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await back_to_main(interaction)


class AdvancedCategoryView(discord.ui.View):
    def __init__(self, profile: dict | None = None):
        super().__init__(timeout=None)
        profile = profile or {}
        now = int(time.time())
        color_locked = bool(profile.get("color_cooldown_until")) and profile["color_cooldown_until"] > now
        badge_locked = bool(profile.get("badge_cooldown_until")) and profile["badge_cooldown_until"] > now
        is_private = profile.get("visibility") == "private"

        color_btn = discord.ui.Button(label="🎨 Couleur du rôle", style=discord.ButtonStyle.secondary, custom_id="divcfg_adv_color", disabled=color_locked)
        color_btn.callback = self._color
        badge_btn = discord.ui.Button(label="🏅 Badge du rôle", style=discord.ButtonStyle.secondary, custom_id="divcfg_adv_badge", disabled=badge_locked)
        badge_btn.callback = self._badge
        visibility_btn = discord.ui.Button(
            label="🔒 Privé" if is_private else "🌐 Public",
            style=discord.ButtonStyle.danger if is_private else discord.ButtonStyle.success,
            custom_id="divcfg_adv_visibility",
        )
        visibility_btn.callback = self._visibility
        back_btn = discord.ui.Button(label="◀ Retour", style=discord.ButtonStyle.secondary, custom_id="divcfg_adv_back", row=1)
        back_btn.callback = self._back

        self.add_item(color_btn)
        self.add_item(badge_btn)
        self.add_item(visibility_btn)
        self.add_item(back_btn)
        reset_btn = discord.ui.Button(label="♻ Réinitialiser", style=discord.ButtonStyle.danger, custom_id="divcfg_adv_reset", row=1)
        reset_btn.callback = self._reset
        self.add_item(reset_btn)

    async def _color(self, interaction: discord.Interaction):
        ctx = await resolve_captain_context(interaction)
        if ctx is None:
            return
        _, _, number = ctx
        profile = await pdb.get_profile(number)
        now = int(time.time())
        if profile["color_cooldown_until"] and profile["color_cooldown_until"] > now:
            await interaction.response.send_message(embed=div_mod.error_embed(f"Modifiable à nouveau <t:{profile['color_cooldown_until']}:R>."), ephemeral=True)
            return
        gradient_ok = "ENHANCED_ROLE_COLORS" in interaction.guild.features
        hint = "Écris une couleur hex (`FF5733`)" + (" ou deux séparées par un espace pour un dégradé (`FF5733 33C1FF`)." if gradient_ok else ".")
        session_id = await pdb.create_session(number, interaction.user.id, interaction.channel_id, "color", "await")
        await interaction.response.edit_message(embed=discord.Embed(description=hint, color=discord.Color.orange()), view=build_await_view())
        message = await interaction.original_response()
        await pdb.set_session_message(session_id, message.id)

    async def _badge(self, interaction: discord.Interaction):
        ctx = await resolve_captain_context(interaction)
        if ctx is None:
            return
        _, _, number = ctx
        profile = await pdb.get_profile(number)
        now = int(time.time())
        if profile["badge_cooldown_until"] and profile["badge_cooldown_until"] > now:
            await interaction.response.send_message(embed=div_mod.error_embed(f"Modifiable à nouveau <t:{profile['badge_cooldown_until']}:R>."), ephemeral=True)
            return
        if "ROLE_ICONS" not in interaction.guild.features:
            await interaction.response.send_message(embed=div_mod.error_embed("Ce serveur n'a pas débloqué les icônes de rôle (boost niveau 2 requis)."), ephemeral=True)
            return
        session_id = await pdb.create_session(number, interaction.user.id, interaction.channel_id, "badge", "await")
        embed = discord.Embed(description="Envoie un **emoji** ou une **image en pièce jointe** dans ce salon.", color=discord.Color.orange())
        await interaction.response.edit_message(embed=embed, view=build_await_view())
        message = await interaction.original_response()
        await pdb.set_session_message(session_id, message.id)

    async def _visibility(self, interaction: discord.Interaction):
        ctx = await resolve_captain_context(interaction)
        if ctx is None:
            return
        _, _, number = ctx
        profile = await pdb.get_profile(number)
        new_visibility = "private" if profile["visibility"] != "private" else "public"
        await pdb.update_profile_fields(number, visibility=new_visibility)
        profile = await pdb.get_profile(number)
        await interaction.response.edit_message(embed=advanced_embed(interaction.guild, profile), view=AdvancedCategoryView(dict(profile)))

    async def _reset(self, interaction: discord.Interaction):
        ctx = await resolve_captain_context(interaction)
        if ctx is None:
            return
        _, _, number = ctx
        await interaction.response.edit_message(embed=reset_confirm_embed(number), view=ResetProfileConfirmView(number))

    async def _back(self, interaction: discord.Interaction):
        await back_to_main(interaction)


# ---------------------------------------------------------------------------
# Couleur / badge - capture chat + confirmation
# ---------------------------------------------------------------------------

async def await_cancel_callback(interaction: discord.Interaction):
    session = await get_session_for_interaction(interaction)
    if session is None:
        return
    kind, number = session["kind"], session["division_number"]
    await pdb.delete_session(session["id"])
    if kind == "edit":
        profile = await pdb.get_profile(number)
        if FIELD_CATEGORY[session["step"]] == "infos":
            await interaction.response.edit_message(embed=infos_embed(number, profile), view=InfosCategoryView())
        else:
            await interaction.response.edit_message(embed=images_embed(profile), view=ImagesCategoryView())
    else:
        profile = await pdb.get_profile(number)
        await interaction.response.edit_message(embed=advanced_embed(interaction.guild, profile), view=AdvancedCategoryView(dict(profile)))


async def confirm_apply_callback(interaction: discord.Interaction):
    session = await get_session_for_interaction(interaction)
    if session is None:
        return
    if session["kind"] == "color":
        await apply_color(interaction, session)
    elif session["kind"] == "badge":
        await apply_badge(interaction, session)


async def apply_color(interaction: discord.Interaction, session):
    data = pdb.session_data(session)
    number = session["division_number"]
    div = await db.get_division(number)
    role = interaction.guild.get_role(div["role_id"]) if div else None
    if role is None:
        await pdb.delete_session(session["id"])
        await interaction.response.send_message(embed=div_mod.error_embed("Rôle de division introuvable."), ephemeral=True)
        return

    await interaction.response.defer()
    hex1, hex2 = data["hex1"], data.get("hex2")
    primary_colour = discord.Colour(int(hex1, 16))
    try:
        if hex2:
            await role.edit(colour=primary_colour, secondary_colour=discord.Colour(int(hex2, 16)), reason=f"Config couleur par {interaction.user}")
        else:
            await role.edit(colour=primary_colour, reason=f"Config couleur par {interaction.user}")
    except discord.Forbidden:
        await interaction.followup.send(embed=div_mod.error_embed("Permissions insuffisantes pour modifier ce rôle."), ephemeral=True)
        return
    except (discord.HTTPException, TypeError):
        try:
            await role.edit(colour=primary_colour, reason=f"Config couleur par {interaction.user} (dégradé indisponible)")
            hex2 = None
        except discord.HTTPException:
            await interaction.followup.send(embed=div_mod.error_embed("Discord a refusé cette couleur."), ephemeral=True)
            return

    until = int(time.time()) + STYLE_COOLDOWN
    await pdb.update_profile_fields(number, color_primary=hex1, color_secondary=hex2, color_cooldown_until=until)
    await pdb.delete_session(session["id"])
    profile = await pdb.get_profile(number)
    embed = advanced_embed(interaction.guild, profile)
    embed.add_field(name="✅ Résultat", value=f"Couleur appliquée. Prochaine modification <t:{until}:R>.", inline=False)
    await interaction.edit_original_response(embed=embed, view=AdvancedCategoryView(dict(profile)))


async def apply_badge(interaction: discord.Interaction, session):
    data = pdb.session_data(session)
    number = session["division_number"]
    div = await db.get_division(number)
    role = interaction.guild.get_role(div["role_id"]) if div else None
    if role is None:
        await pdb.delete_session(session["id"])
        await interaction.response.send_message(embed=div_mod.error_embed("Rôle de division introuvable."), ephemeral=True)
        return

    await interaction.response.defer()
    icon_value = base64.b64decode(data["value"]) if data["is_image"] else data["value"]
    try:
        await role.edit(display_icon=icon_value, reason=f"Config badge par {interaction.user}")
    except discord.Forbidden:
        await interaction.followup.send(embed=div_mod.error_embed("Permissions insuffisantes pour modifier ce rôle."), ephemeral=True)
        return
    except discord.HTTPException as e:
        await interaction.followup.send(embed=div_mod.error_embed(f"Discord a refusé ce badge : `{e}`"), ephemeral=True)
        return

    until = int(time.time()) + STYLE_COOLDOWN
    await pdb.update_profile_fields(number, badge_source=data["label"], badge_cooldown_until=until)
    await pdb.delete_session(session["id"])
    profile = await pdb.get_profile(number)
    embed = advanced_embed(interaction.guild, profile)
    embed.add_field(name="✅ Résultat", value=f"Badge appliqué. Prochaine modification <t:{until}:R>.", inline=False)
    await interaction.edit_original_response(embed=embed, view=AdvancedCategoryView(dict(profile)))


async def handle_color_message(message: discord.Message, session):
    guild = message.guild
    gradient_ok = "ENHANCED_ROLE_COLORS" in guild.features
    parts = message.content.strip().split()
    valid = bool(parts) and HEX_PATTERN.match(parts[0]) and (
        len(parts) == 1 or (gradient_ok and len(parts) == 2 and HEX_PATTERN.match(parts[1]))
    )
    await safe_delete(message)
    if not valid:
        hint = "Format attendu : `FF5733` ou `FF5733 33C1FF` (dégradé)." if gradient_ok else "Format attendu : `FF5733`."
        await safe_notice(message.channel, hint)
        return

    hex1 = parts[0].lstrip("#").upper()
    hex2 = parts[1].lstrip("#").upper() if len(parts) == 2 else None
    await pdb.update_session(session["id"], step="confirm", data={"hex1": hex1, "hex2": hex2})
    session = await pdb.get_session(session["id"])
    target = await fetch_session_message(guild, session)
    if target:
        await target.edit(embed=color_confirm_embed(hex1, hex2), view=build_confirm_view())


async def handle_badge_message(message: discord.Message, session):
    raw = message.content.strip()
    is_image = False
    value = None
    label = raw[:100] if raw else (message.attachments[0].filename if message.attachments else "badge")

    if message.attachments:
        fetched = await fetch_bytes(message.attachments[0].url)
        if fetched:
            is_image, value = True, base64.b64encode(fetched).decode()
    elif CUSTOM_EMOJI_ID_PATTERN.match(raw):
        m = CUSTOM_EMOJI_ID_PATTERN.match(raw)
        ext = "gif" if raw.startswith("<a:") else "png"
        fetched = await fetch_bytes(f"https://cdn.discordapp.com/emojis/{m.group(1)}.{ext}")
        if fetched:
            is_image, value = True, base64.b64encode(fetched).decode()
    elif div_mod.is_valid_standard_emoji(raw):
        is_image, value = False, raw

    await safe_delete(message)
    if value is None:
        await safe_notice(message.channel, "Envoie un emoji valide ou une image en pièce jointe.")
        return

    await pdb.update_session(session["id"], step="confirm", data={"is_image": is_image, "value": value, "label": label})
    session = await pdb.get_session(session["id"])
    target = await fetch_session_message(message.guild, session)
    if target:
        embed, files = badge_confirm_embed(pdb.session_data(session))
        await target.edit(embed=embed, view=build_confirm_view(), attachments=files)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class DivisionConfig(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        await pdb.init_profiles_tables()
        self.bot.add_view(MainConfigView())
        self.bot.add_view(InfosCategoryView())
        self.bot.add_view(ImagesCategoryView())
        self.bot.add_view(AdvancedCategoryView())
        for session in await pdb.get_all_sessions():
            if not session["message_id"]:
                continue
            if session["kind"] == "wizard":
                view = build_wizard_view(session["step"], pdb.session_history(session))
            elif session["kind"] in ("color", "badge") and session["step"] == "confirm":
                view = build_confirm_view()
            elif session["kind"] in ("edit", "color", "badge"):
                view = build_await_view()
            else:
                continue
            self.bot.add_view(view, message_id=session["message_id"])

    @app_commands.command(name="division-config", description="Configure le profil de ta division (capitaine uniquement).")
    async def division_config(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message(embed=div_mod.error_embed("Cette commande ne fonctionne que sur un serveur."), ephemeral=True)
            return
        ctx = await resolve_captain_context(interaction)
        if ctx is None:
            return
        _, _, number = ctx
        profile = await pdb.ensure_profile(number)
        if not profile["setup_done"]:
            await start_wizard(interaction, number)
        else:
            await interaction.response.send_message(embed=summary_embed(number, profile), view=MainConfigView())

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        session = await pdb.get_session_by_user_channel(message.author.id, message.channel.id)
        if session is None:
            return

        if session["kind"] == "wizard":
            await handle_wizard_message(message, session)
        elif session["kind"] == "edit":
            await handle_edit_message(message, session)
        elif session["kind"] == "color" and session["step"] == "await":
            await handle_color_message(message, session)
        elif session["kind"] == "badge" and session["step"] == "await":
            await handle_badge_message(message, session)
        else:
            await safe_delete(message)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        logger.exception("Erreur non gérée sur /division-config", exc_info=error)
        embed = div_mod.error_embed("Une erreur inattendue est survenue. Réessaie dans un instant.")
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(DivisionConfig(bot))