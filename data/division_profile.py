"""
Cog d'affichage du profil de division (Gotei RP Bleach).

/division-profil : affiche le profil configuré d'une division (la sienne par défaut).
Profil privé -> réservé aux membres de la division.
Le règlement interne (si configuré) n'est visible qu'aux membres, via un bouton éphémère.
"""
import sys
import pathlib
import logging

import discord
from discord import app_commands
from discord.ext import commands

sys.path.append(str(pathlib.Path(__file__).parent.parent))
from data import database as db
from data import division_profiles as pdb
from cogs import divisions as div_mod

logger = logging.getLogger("urahara.division_profile")


def is_division_member(guild: discord.Guild, member: discord.Member, div) -> bool:
    role = guild.get_role(div["role_id"])
    return bool(role and role in member.roles)


async def build_profile_embed(guild: discord.Guild, number: int, profile: dict, div) -> discord.Embed:
    captain, vices, lieutenants, count = await div_mod.get_division_staff(guild, div)
    category = guild.get_channel(div["category_id"])
    created_ts = int(category.created_at.timestamp()) if category else None

    emoji = div["emoji"] or ""
    name = profile.get("custom_name") or f"Division {number}"
    title = f"{emoji} {name}" if emoji else name
    
    embed = discord.Embed(
        title=title, 
        description=profile.get("description") or "*Aucune description.*", 
        color=discord.Color.blurple()
    )
    if profile.get("pfp_url"):
        embed.set_thumbnail(url=profile["pfp_url"])
    if profile.get("banner_url"):
        embed.set_image(url=profile["banner_url"])

    division_role = guild.get_role(div["role_id"])
    embed.add_field(name="🔢 Division", value=str(number), inline=True)
    embed.add_field(name="🎯 Rôle", value=division_role.mention if division_role else "Inconnu", inline=True)
    
    if profile.get("min_age"):
        embed.add_field(name="⏳ Âge minimum", value=str(profile["min_age"]), inline=True)
    
    embed.add_field(name="👑 Capitaine", value=captain.mention if captain else "*Aucun*", inline=True)
    embed.add_field(name="🗝️ Vice-capitaine(s)", value=", ".join(v.mention for v in vices) if vices else "*Aucun*", inline=True)
    embed.add_field(name="⚔️ Lieutenant(s)", value=", ".join(l.mention for l in lieutenants) if lieutenants else "*Aucun*", inline=True)
    
    embed.add_field(name="👥 Membres", value=f"{count}/{div_mod.MAX_DIVISION_MEMBERS}", inline=True)
    if created_ts:
        embed.add_field(name="📅 Créée le", value=f"<t:{created_ts}:D>", inline=True)
    if category:
        embed.add_field(name="📁 Catégorie", value=category.mention if hasattr(category, 'mention') else category.name, inline=True)

    grade_lines = []
    for grade_members, grade_key, grade_label, icon in ((vices, "vice", "Vice-capitaine", "🗝️"), (lieutenants, "lieutenant", "Lieutenant", "⚔️")):
        for m in grade_members:
            grant = await pdb.get_grade_grant(m.id, number, grade_key)
            if grant:
                grade_lines.append(f"{icon} {m.mention} — <t:{grant['granted_at']}:D>")
    if grade_lines:
        embed.add_field(name="📋 Élections", value="\n".join(grade_lines[:10]), inline=False)

    members = [m for m in guild.members if division_role and division_role in m.roles]
    if members:
        member_list = "\n".join(f"• {m.mention}" for m in members[:25])
        if len(members) > 25:
            member_list += f"\n*...et {len(members) - 25} autre(s)*"
        embed.add_field(name="👥 Effectif", value=member_list, inline=False)

    embed.add_field(name="🔐 Visibilité", value="🔒 Privée" if profile.get("visibility") == "private" else "🌐 Publique", inline=True)
    embed.set_footer(text="Urahara • Profil de division")
    return embed


class ReglementView(discord.ui.View):
    def __init__(self, reglement: str):
        super().__init__(timeout=300)
        self.reglement = reglement

    @discord.ui.button(label="📜 Règlement", style=discord.ButtonStyle.secondary)
    async def show(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="📜 Règlement interne", description=self.reglement[:4000], color=discord.Color.blurple())
        await interaction.response.send_message(embed=embed, ephemeral=True)


class DivisionProfile(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        await pdb.init_profiles_tables()

    @app_commands.command(name="division-profil", description="Affiche le profil d'une division (la tienne par défaut).")
    @app_commands.describe(division="Division à afficher (par défaut : la tienne)")
    @app_commands.autocomplete(division=div_mod.division_autocomplete)
    async def division_profil(self, interaction: discord.Interaction, division: int = None):
        guild = interaction.guild
        member = interaction.user
        if guild is None:
            await interaction.response.send_message(embed=div_mod.error_embed("Cette commande ne fonctionne que sur un serveur."), ephemeral=True)
            return

        if division is None:
            roles = div_mod.find_division_roles(member)
            if len(roles) != 1:
                await interaction.response.send_message(embed=div_mod.error_embed("Impossible de déterminer ta division, précise un numéro."), ephemeral=True)
                return
            number = div_mod.extract_division_number(roles[0])
        else:
            number = division

        div = await db.get_division(number)
        if div is None:
            await interaction.response.send_message(embed=div_mod.error_embed(f"La division {number} n'existe pas."), ephemeral=True)
            return

        profile_row = await pdb.get_profile(number)
        profile = dict(profile_row) if profile_row else {}
        member_of_division = is_division_member(guild, member, div)

        if profile.get("visibility", "public") == "private" and not member_of_division:
            # Affiche le titre avec la division, mais message d'erreur dans le corps
            emoji = div["emoji"] or ""
            name = profile.get("custom_name") or f"Division {number}"
            title = f"{emoji} {name}" if emoji else name
            
            embed = discord.Embed(
                title=title,
                description="🔒 Cette division est privée.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)
            return

        embed = await build_profile_embed(guild, number, profile, div)
        view = ReglementView(profile["reglement"]) if member_of_division and profile.get("reglement") else None
        if view is not None:
            await interaction.response.send_message(embed=embed, view=view)
        else:
            await interaction.response.send_message(embed=embed)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.roles == after.roles:
            return
        guild = after.guild
        added = set(after.roles) - set(before.roles)
        vice_role = guild.get_role(div_mod.VICE_ROLE_ID)
        lieutenant_role = guild.get_role(div_mod.LIEUTENANT_ROLE_ID)
        for role, grade in ((vice_role, "vice"), (lieutenant_role, "lieutenant")):
            if role and role in added:
                division_roles = div_mod.find_division_roles(after)
                if len(division_roles) == 1:
                    number = div_mod.extract_division_number(division_roles[0])
                    if number is not None:
                        await pdb.set_grade_grant(after.id, number, grade, int(discord.utils.utcnow().timestamp()))

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        logger.exception("Erreur non gérée sur /division-profil", exc_info=error)
        embed = div_mod.error_embed("Une erreur inattendue est survenue. Réessaie dans un instant.")
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(DivisionProfile(bot))