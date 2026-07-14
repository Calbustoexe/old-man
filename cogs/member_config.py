"""
Cog de configuration du profil membre (Gotei RP Bleach).

/config-profil : configure son propre profil (description, images, couleur, visibilité).
Toutes les réponses en salon (texte/image) sont capturées via on_message et stockées
dans une session persistante en base : un redémarrage du bot ne casse ni le panel
ouvert, ni une saisie en attente (couleur, pfp, bannière, description).
"""
import logging

import discord
from discord import app_commands, ui
from discord.ext import commands

from data import member_profiles as mdb
import utils
from cogs.division_config import resolve_image_url

logger = logging.getLogger("urahara.member_config")

FIELD_LABELS = {
    "description": "la description",
    "pfp": "la photo de profil",
    "banner": "la bannière",
    "color": "la couleur",
}


def error_embed(description: str) -> discord.Embed:
    return discord.Embed(description=f"❌ {description}", color=discord.Color.red())


async def safe_delete(message: discord.Message):
    try:
        await message.delete()
    except discord.HTTPException:
        pass


async def safe_notice(channel: discord.abc.Messageable, text: str):
    try:
        await channel.send(embed=error_embed(text), delete_after=6)
    except discord.HTTPException:
        pass


# ---------------------------------------------------------------------------
# Embeds
# ---------------------------------------------------------------------------

def _color_display(profile: dict) -> str:
    if not profile["color_primary"]:
        return "Non définie"
    if profile["color_secondary"]:
        return f"#{profile['color_primary']} → #{profile['color_secondary']}"
    return f"#{profile['color_primary']}"


def summary_embed(member: discord.Member, profile: dict) -> discord.Embed:
    embed = discord.Embed(
        title=f"⚙️ Profil — {member.display_name}",
        description=profile["description"] or "*Aucune description.*",
        color=discord.Color.blurple(),
    )
    pfp = profile["pfp_url"] or (member.avatar.url if member.avatar else member.default_avatar.url)
    embed.set_thumbnail(url=pfp)
    if profile["banner_url"]:
        embed.set_image(url=profile["banner_url"])
    embed.add_field(name="🎨 Couleur", value=_color_display(profile), inline=True)
    embed.add_field(name="📸 Photo", value="✅ Définie" if profile["pfp_url"] else "❌ Par défaut", inline=True)
    embed.add_field(name="📄 Bannière", value="✅ Définie" if profile["banner_url"] else "❌ Aucune", inline=True)
    embed.add_field(name="🔐 Visibilité", value="🔒 Privée" if profile["visibility"] == "private" else "🌐 Publique", inline=True)
    embed.set_footer(text="Urahara • Panel de configuration")
    return embed


def infos_embed(member: discord.Member, profile: dict) -> discord.Embed:
    embed = discord.Embed(title="📝 Infos générales", color=discord.Color.blurple())
    embed.add_field(name="📌 Pseudo", value=member.display_name, inline=False)
    embed.add_field(name="📄 Description", value=profile["description"] or "*Non définie*", inline=False)
    return embed


def images_embed(profile: dict) -> discord.Embed:
    embed = discord.Embed(title="🖼️ Images", color=discord.Color.blurple())
    embed.add_field(name="Photo de profil", value="Définie ✅" if profile["pfp_url"] else "Non définie", inline=True)
    embed.add_field(name="Bannière", value="Définie ✅" if profile["banner_url"] else "Non définie", inline=True)
    if profile["pfp_url"]:
        embed.set_thumbnail(url=profile["pfp_url"])
    if profile["banner_url"]:
        embed.set_image(url=profile["banner_url"])
    return embed


def advanced_embed(profile: dict) -> discord.Embed:
    embed = discord.Embed(title="🎨 Configuration avancée", color=discord.Color.blurple())
    embed.add_field(name="🎨 Couleur du profil", value=_color_display(profile), inline=True)
    embed.add_field(name="🔐 Visibilité", value="🔒 Privée" if profile["visibility"] == "private" else "🌐 Publique", inline=True)
    return embed


def edit_prompt_embed(step: str) -> discord.Embed:
    embed = discord.Embed(
        title="📝 Modification",
        description=f"✏️ Écris ta réponse dans ce salon pour **{FIELD_LABELS[step]}** (ou `skip`/`-` pour vider ce champ).",
        color=discord.Color.orange(),
    )
    embed.set_footer(text="En attente de ta réponse...")
    return embed


# ---------------------------------------------------------------------------
# Helpers de session
# ---------------------------------------------------------------------------

async def fetch_session_message(guild: discord.Guild, session: dict) -> discord.Message | None:
    channel = guild.get_channel(session["channel_id"])
    if channel is None or session["message_id"] is None:
        return None
    try:
        return await channel.fetch_message(session["message_id"])
    except discord.HTTPException:
        return None


# ---------------------------------------------------------------------------
# Vues persistantes (custom_id fixes, aucun état volatile en constructeur)
# ---------------------------------------------------------------------------

class MainConfigView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Infos", style=discord.ButtonStyle.blurple, emoji="📝", custom_id="memcfg_main_infos")
    async def infos(self, interaction: discord.Interaction, button: ui.Button):
        profile = await mdb.ensure_profile(interaction.guild_id, interaction.user.id)
        await interaction.response.edit_message(embed=infos_embed(interaction.user, profile), view=InfosCategoryView())

    @ui.button(label="Images", style=discord.ButtonStyle.blurple, emoji="🖼️", custom_id="memcfg_main_images")
    async def images(self, interaction: discord.Interaction, button: ui.Button):
        profile = await mdb.ensure_profile(interaction.guild_id, interaction.user.id)
        await interaction.response.edit_message(embed=images_embed(profile), view=ImagesCategoryView())

    @ui.button(label="Avancé", style=discord.ButtonStyle.blurple, emoji="🎨", custom_id="memcfg_main_advanced")
    async def advanced(self, interaction: discord.Interaction, button: ui.Button):
        profile = await mdb.ensure_profile(interaction.guild_id, interaction.user.id)
        await interaction.response.edit_message(embed=advanced_embed(profile), view=AdvancedCategoryView())


class InfosCategoryView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Modifier description", style=discord.ButtonStyle.blurple, emoji="📝", custom_id="memcfg_infos_edit")
    async def edit_description(self, interaction: discord.Interaction, button: ui.Button):
        await start_edit(interaction, "description")

    @ui.button(label="Retour", style=discord.ButtonStyle.secondary, emoji="⬅️", custom_id="memcfg_infos_back")
    async def back(self, interaction: discord.Interaction, button: ui.Button):
        await back_to_main(interaction)


class ImagesCategoryView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="PP", style=discord.ButtonStyle.blurple, emoji="📸", custom_id="memcfg_images_pp")
    async def edit_pfp(self, interaction: discord.Interaction, button: ui.Button):
        await start_edit(interaction, "pfp")

    @ui.button(label="Bannière", style=discord.ButtonStyle.blurple, emoji="🖼️", custom_id="memcfg_images_banner")
    async def edit_banner(self, interaction: discord.Interaction, button: ui.Button):
        await start_edit(interaction, "banner")

    @ui.button(label="Retirer PP", style=discord.ButtonStyle.red, emoji="❌", custom_id="memcfg_images_reset_pp", row=1)
    async def reset_pfp(self, interaction: discord.Interaction, button: ui.Button):
        await mdb.update_pfp(interaction.guild_id, interaction.user.id, None)
        profile = await mdb.get_profile(interaction.guild_id, interaction.user.id)
        await interaction.response.edit_message(embed=images_embed(profile), view=ImagesCategoryView())

    @ui.button(label="Retirer bannière", style=discord.ButtonStyle.red, emoji="❌", custom_id="memcfg_images_reset_banner", row=1)
    async def reset_banner(self, interaction: discord.Interaction, button: ui.Button):
        await mdb.update_banner(interaction.guild_id, interaction.user.id, None)
        profile = await mdb.get_profile(interaction.guild_id, interaction.user.id)
        await interaction.response.edit_message(embed=images_embed(profile), view=ImagesCategoryView())

    @ui.button(label="Retour", style=discord.ButtonStyle.secondary, emoji="⬅️", custom_id="memcfg_images_back", row=1)
    async def back(self, interaction: discord.Interaction, button: ui.Button):
        await back_to_main(interaction)


class AdvancedCategoryView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Couleur", style=discord.ButtonStyle.blurple, emoji="🎨", custom_id="memcfg_adv_color")
    async def edit_color(self, interaction: discord.Interaction, button: ui.Button):
        await start_edit(interaction, "color")

    @ui.button(label="Visibilité", style=discord.ButtonStyle.blurple, emoji="🔐", custom_id="memcfg_adv_visibility")
    async def toggle_visibility(self, interaction: discord.Interaction, button: ui.Button):
        profile = await mdb.get_profile(interaction.guild_id, interaction.user.id)
        new_visibility = "private" if profile["visibility"] != "private" else "public"
        await mdb.update_visibility(interaction.guild_id, interaction.user.id, new_visibility)
        profile = await mdb.get_profile(interaction.guild_id, interaction.user.id)
        await interaction.response.edit_message(embed=advanced_embed(profile), view=AdvancedCategoryView())

    @ui.button(label="Réinitialiser", style=discord.ButtonStyle.red, emoji="♻️", custom_id="memcfg_adv_reset")
    async def reset_profile(self, interaction: discord.Interaction, button: ui.Button):
        await mdb.reset_profile(interaction.guild_id, interaction.user.id)
        profile = await mdb.get_profile(interaction.guild_id, interaction.user.id)
        await interaction.response.edit_message(embed=advanced_embed(profile), view=AdvancedCategoryView())

    @ui.button(label="Retour", style=discord.ButtonStyle.secondary, emoji="⬅️", custom_id="memcfg_adv_back")
    async def back(self, interaction: discord.Interaction, button: ui.Button):
        await back_to_main(interaction)


class AwaitCancelView(ui.View):
    """Vue affichée pendant qu'on attend une réponse en salon (custom_id fixe -> persistante)."""

    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Annuler", style=discord.ButtonStyle.danger, custom_id="memcfg_await_cancel")
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        session = await mdb.get_config_session_by_user_channel(interaction.user.id, interaction.channel_id)
        if session is None or interaction.user.id != session["user_id"]:
            await interaction.response.send_message(embed=error_embed("Session introuvable ou expirée."), ephemeral=True)
            return
        await mdb.delete_config_session(session["id"])
        profile = await mdb.get_profile(interaction.guild_id, interaction.user.id)
        step = session["step"]
        if step == "description":
            await interaction.response.edit_message(embed=infos_embed(interaction.user, profile), view=InfosCategoryView())
        elif step in ("pfp", "banner"):
            await interaction.response.edit_message(embed=images_embed(profile), view=ImagesCategoryView())
        else:
            await interaction.response.edit_message(embed=advanced_embed(profile), view=AdvancedCategoryView())


# ---------------------------------------------------------------------------
# Navigation / démarrage de saisie
# ---------------------------------------------------------------------------

async def back_to_main(interaction: discord.Interaction):
    profile = await mdb.ensure_profile(interaction.guild_id, interaction.user.id)
    await interaction.response.edit_message(embed=summary_embed(interaction.user, profile), view=MainConfigView())


async def start_edit(interaction: discord.Interaction, step: str):
    session_id = await mdb.create_config_session(interaction.user.id, interaction.guild_id, interaction.channel_id, step)
    await interaction.response.edit_message(embed=edit_prompt_embed(step), view=AwaitCancelView())
    message = await interaction.original_response()
    await mdb.set_config_session_message(session_id, message.id)


# ---------------------------------------------------------------------------
# Traitement des réponses en salon
# ---------------------------------------------------------------------------

async def handle_session_message(message: discord.Message, session: dict):
    step = session["step"]
    raw = message.content.strip()
    guild_id, user_id = session["guild_id"], session["user_id"]

    if step == "description":
        value = "" if raw.lower() in ("skip", "-") else raw[:500]
        await safe_delete(message)
        await mdb.update_description(guild_id, user_id, value)
        await mdb.delete_config_session(session["id"])
        profile = await mdb.get_profile(guild_id, user_id)
        target = await fetch_session_message(message.guild, session)
        if target:
            member = message.guild.get_member(user_id) or message.author
            await target.edit(embed=infos_embed(member, profile), view=InfosCategoryView())
        return

    if step in ("pfp", "banner"):
        if raw.lower() in ("skip", "-"):
            url, valid = None, True
        elif message.attachments:
            url = await utils.persist_image(message.guild, message.attachments[0])
            valid = url is not None
        elif raw.startswith(("http://", "https://")):
            resolved = await resolve_image_url(raw)
            if resolved is None:
                url, valid = None, False
            else:
                url = await utils.persist_image_from_url(message.guild, resolved)
                valid = True
        else:
            url, valid = None, False

        await safe_delete(message)
        if not valid:
            await safe_notice(message.channel, "Lien non reconnu comme image/GIF valide (essaie un lien direct .gif/.png/.mp4, ou envoie le fichier en pièce jointe).")
            return

        if step == "pfp":
            await mdb.update_pfp(guild_id, user_id, url)
        else:
            await mdb.update_banner(guild_id, user_id, url)
        await mdb.delete_config_session(session["id"])
        profile = await mdb.get_profile(guild_id, user_id)
        target = await fetch_session_message(message.guild, session)
        if target:
            await target.edit(embed=images_embed(profile), view=ImagesCategoryView())
        return

    if step == "color":
        if raw.lower() in ("skip", "-"):
            await safe_delete(message)
            await mdb.delete_config_session(session["id"])
            profile = await mdb.get_profile(guild_id, user_id)
            target = await fetch_session_message(message.guild, session)
            if target:
                await target.edit(embed=advanced_embed(profile), view=AdvancedCategoryView())
            return

        hex_color = raw.lstrip("#")
        valid = len(hex_color) == 6 and all(c in "0123456789abcdefABCDEF" for c in hex_color)
        await safe_delete(message)
        if not valid:
            await safe_notice(message.channel, "Code hexadécimal invalide (format : `ff0000`).")
            return

        await mdb.update_color(guild_id, user_id, hex_color.lower())
        await mdb.delete_config_session(session["id"])
        profile = await mdb.get_profile(guild_id, user_id)
        target = await fetch_session_message(message.guild, session)
        if target:
            await target.edit(embed=advanced_embed(profile), view=AdvancedCategoryView())


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class MemberConfig(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        await mdb.ensure_member_profile_table()
        # Vues persistantes réutilisables : un seul enregistrement générique suffit,
        # discord.py route par custom_id quel que soit le message concerné.
        self.bot.add_view(MainConfigView())
        self.bot.add_view(InfosCategoryView())
        self.bot.add_view(ImagesCategoryView())
        self.bot.add_view(AdvancedCategoryView())
        self.bot.add_view(AwaitCancelView())

    @app_commands.command(name="config-profil", description="Configure ton profil de membre.")
    async def config_profil(self, interaction: discord.Interaction):
        profile = await mdb.ensure_profile(interaction.guild_id, interaction.user.id)
        await interaction.response.send_message(embed=summary_embed(interaction.user, profile), view=MainConfigView())

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        session = await mdb.get_config_session_by_user_channel(message.author.id, message.channel.id)
        if session is None:
            return
        await handle_session_message(message, session)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        logger.exception("Erreur non gérée sur /config-profil", exc_info=error)
        embed = error_embed("Une erreur inattendue est survenue. Réessaie dans un instant.")
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(MemberConfig(bot))