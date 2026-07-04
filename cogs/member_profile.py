"""
Member profile display cog.
Shows member profiles with division info, grades, and custom details.
"""

import discord
from discord.ext import commands
from discord import app_commands
import re
import os
import asyncio

from data import member_profiles as mdb
from data import database as db

# Load role IDs from env
CAPTAIN_ROLE_ID = int(os.getenv("ROLE_CAPTAIN_ID", 0))
VICE_ROLE_ID = int(os.getenv("ROLE_VICE_ID", 0))
LIEUTENANT_ROLE_ID = int(os.getenv("ROLE_LIEUTENANT_ID", 0))


DIVISION_ROLE_PATTERN = re.compile(
    r"^(?:division\s*(\d{1,2})\s*(?:e|er|ère|eme|ème)?|(\d{1,2})\s*(?:e|er|ère|eme|ème)?\s*division)$",
    re.IGNORECASE,
)


def extract_division_number(role: discord.Role) -> int | None:
    """Extract division number from role name."""
    match = DIVISION_ROLE_PATTERN.match(role.name.strip())
    if not match:
        return None
    num = match.group(1) or match.group(2)
    try:
        return int(num)
    except (ValueError, TypeError):
        return None


async def get_division_staff(guild: discord.Guild, div):
    """Get captain, vices, lieutenants from division."""
    captain = None
    vices = []
    lieutenants = []
    count = 0
    
    if not div or not div["role_id"]:
        return captain, vices, lieutenants, count
    
    division_role = guild.get_role(div["role_id"])
    if not division_role:
        return captain, vices, lieutenants, count
    
    # Get captain
    captain_id = div.get("captain_id")
    if captain_id:
        captain = guild.get_member(captain_id)
    
    # Get vice role members
    vice_role = guild.get_role(VICE_ROLE_ID)
    if vice_role:
        for member in guild.members:
            if vice_role in member.roles and division_role in member.roles:
                vices.append(member)
    
    # Get lieutenant role members
    lieutenant_role = guild.get_role(LIEUTENANT_ROLE_ID)
    if lieutenant_role:
        for member in guild.members:
            if lieutenant_role in member.roles and division_role in member.roles:
                lieutenants.append(member)
    
    # Count members with division role
    count = sum(1 for m in guild.members if division_role in m.roles)
    
    return captain, vices, lieutenants, count


# ============================================================================
# VIEWS
# ============================================================================

class MemberProfile(commands.Cog):
    """Cog pour l'affichage des profils des membres."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="profil", description="Affiche le profil d'un membre")
    @app_commands.describe(member="Membre dont afficher le profil (optionnel, toi par défaut)")
    async def profil(self, interaction: discord.Interaction, member: discord.Member = None):
        """Display a member's profile with division info and custom details."""
        await interaction.response.defer()
        
        member = member or interaction.user
        
        # Prevent bots from having profiles
        if member.bot:
            embed = discord.Embed(
                title="❌ Erreur",
                description="Les bots n'ont pas de profil RP.",
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed)
            return
        
        profile = await mdb.get_profile(interaction.guild_id, member.id)
        
        if not profile:
            profile = await mdb.ensure_profile(interaction.guild_id, member.id)
        
        # Check visibility
        is_owner = member.id == interaction.user.id
        is_admin = interaction.user.guild_permissions.administrator
        
        if profile["visibility"] == "private" and not is_owner and not is_admin:
            embed = discord.Embed(
                title=f"👤 {member.display_name}",
                description="🔒 Ce profil est privé.",
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed)
            return
        
        # Build and send profile
        embed = await self._build_profile_embed(member, profile)
        await interaction.followup.send(embed=embed)

    async def _build_profile_embed(self, member: discord.Member, profile: dict) -> discord.Embed:
        """Build member RP profile embed."""
        # Get member's division if any
        division_info = None
        division_number = None
        grade_text = None
        
        # Find division roles for this member
        for role in member.roles:
            match = DIVISION_ROLE_PATTERN.match(role.name.strip())
            if match:
                try:
                    div_number = extract_division_number(role)
                    if div_number is None:
                        continue
                    
                    div = await db.get_division(div_number)
                    
                    if div:
                        division_info = div
                        division_number = div_number
                        
                        # Detect grade by checking if member has grade roles
                        captain_role = member.guild.get_role(CAPTAIN_ROLE_ID)
                        vice_role = member.guild.get_role(VICE_ROLE_ID)
                        lieutenant_role = member.guild.get_role(LIEUTENANT_ROLE_ID)
                        
                        if captain_role and captain_role in member.roles:
                            grade_text = captain_role.mention
                        elif vice_role and vice_role in member.roles:
                            grade_text = vice_role.mention
                        elif lieutenant_role and lieutenant_role in member.roles:
                            grade_text = lieutenant_role.mention
                        else:
                            grade_text = "Membre"
                        
                        break  # Primary division found
                except (discord.HTTPException, KeyError, TypeError):
                    continue
        
        # Get profile picture (custom or Discord default)
        pfp = profile["pfp_url"] or (member.avatar.url if member.avatar else member.default_avatar.url)
        
        # Get banner (custom > Discord banner if Nitro > none)
        banner = None
        if profile["banner_url"]:
            banner = profile["banner_url"]
        else:
            # Try to get Discord banner
            try:
                full_member = await member.guild.fetch_member(member.id)
                if full_member.banner:
                    banner = full_member.banner.url
            except discord.HTTPException:
                pass
        
        # Get color from profile or default
        color = discord.Color.blurple()
        if profile["color_primary"]:
            try:
                color = discord.Color(int(profile["color_primary"], 16))
            except (ValueError, TypeError):
                pass
        
        # Build embed with RP profile info - well organized
        embed = discord.Embed(
            title=f"👤 {member.display_name}",
            description=profile["description"] or "*Pas de description RP.*",
            color=color,
        )
        
        # Set profile picture as thumbnail
        embed.set_thumbnail(url=pfp)
        
        # === DIVISION INFO SECTION ===
        if division_info:
            embed.add_field(
                name="",
                value="━━━━━━━━ 📋 **DIVISION** ━━━━━━━━",
                inline=False
            )
            embed.add_field(name="📍 Division", value=f"Division {division_number}", inline=True)
            embed.add_field(name="⭐ Grade", value=grade_text or "Membre", inline=True)
            embed.add_field(name="", value="", inline=False)  # Spacing
        else:
            embed.add_field(name="📍 Division", value="Aucune", inline=False)
            embed.add_field(name="", value="", inline=False)  # Spacing
        
        # Set banner as image at bottom (landscape format)
        if banner:
            embed.set_image(url=banner)
        
        # Footer with visibility status
        visibility_text = "🔒 Privée" if profile["visibility"] == "private" else "🌐 Publique"
        embed.set_footer(text=f"Urahara • Profil RP • {visibility_text}")
        return embed


async def setup(bot: commands.Bot):
    await bot.add_cog(MemberProfile(bot))