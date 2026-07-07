import asyncio
import time

import discord
from discord.ext import commands

import utils

OWNER_ID = utils.OWNER_ID
PROTECTION_TIMEOUT = 180  # 3 minutes in seconds

_vocprotect = utils.load_vocprotect()
_timers = {}


def _save():
    utils.save_vocprotect(_vocprotect)


def _get_protection_key(guild_id: int, channel_id: int) -> str:
    return f"{guild_id}_{channel_id}"


def _is_protected(guild_id: int, channel_id: int) -> dict | None:
    key = _get_protection_key(guild_id, channel_id)
    return _vocprotect.get(key)


def _is_whitelisted(user_id: int, protection: dict) -> bool:
    return str(user_id) in protection["whitelist"]


def _is_blacklisted(user_id: int, protection: dict) -> bool:
    return str(user_id) in protection.get("blacklist", [])


class VocProtectCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        guild_id = member.guild.id
        
        # Check if protector left their protected channel
        if before.channel and not after.channel:
            protection = _is_protected(guild_id, before.channel.id)
            if protection and protection["protector_id"] == member.id:
                # Start the 3-minute timer
                key = _get_protection_key(guild_id, before.channel.id)
                if key in _timers:
                    _timers[key].cancel()
                _timers[key] = asyncio.create_task(self._protection_timer(guild_id, before.channel.id, member.id))
        
        # Check if protector rejoined their protected channel
        if after.channel and not before.channel:
            protection = _is_protected(guild_id, after.channel.id)
            if protection and protection["protector_id"] == member.id:
                key = _get_protection_key(guild_id, after.channel.id)
                if key in _timers:
                    _timers[key].cancel()
                    del _timers[key]
        
        # Check if someone is trying to join a protected channel
        if after.channel and (not before.channel or before.channel.id != after.channel.id):
            protection = _is_protected(guild_id, after.channel.id)
            if protection:
                # Kick if blacklisted (blacklist overrides everything)
                if _is_blacklisted(member.id, protection):
                    try:
                        await member.move_to(None)
                    except (discord.Forbidden, discord.HTTPException):
                        pass
                    return
                # Allow if whitelisted or already in channel
                if _is_whitelisted(member.id, protection):
                    return
                # Allow if they're the protector
                if protection["protector_id"] == member.id:
                    return
                # Allow if they were already in the channel (just moved within it)
                if before.channel and before.channel.id == after.channel.id:
                    return
                
                # Kick them
                try:
                    await member.move_to(None)
                except (discord.Forbidden, discord.HTTPException):
                    pass

    async def _protection_timer(self, guild_id: int, channel_id: int, protector_id: int):
        """Wait 3 minutes after protector leaves, then remove protection."""
        await asyncio.sleep(PROTECTION_TIMEOUT)
        
        key = _get_protection_key(guild_id, channel_id)
        protection = _vocprotect.get(key)
        
        # Check if protection still exists and protector is still not in channel
        if protection and protection["protector_id"] == protector_id:
            guild = self.bot.get_guild(guild_id)
            if guild:
                channel = guild.get_channel(channel_id)
                if channel:
                    # Check if protector is back in the channel
                    protector = guild.get_member(protector_id)
                    if protector and protector.voice and protector.voice.channel.id == channel_id:
                        return  # Protector is back, don't remove protection
            
            # Remove protection
            del _vocprotect[key]
            _save()
            if key in _timers:
                del _timers[key]

    @commands.command(name="pv")
    @commands.guild_only()
    async def protect_voc(self, ctx: commands.Context, *, args: str = ""):
        """Protect current voice channel. Usage: d!pv [@user1 @user2 ...]"""
        if ctx.author.id != OWNER_ID:
            return
        await ctx.message.delete()

        # Check if user is in a voice channel
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send(embed=utils.err(
                "Tu dois être dans un salon vocal pour le protéger.",
                "❌ Pas dans un salon"
            ), delete_after=10)
        
        channel = ctx.author.voice.channel
        
        # Parse optional whitelist members
        whitelist = []
        parts = args.split()
        for part in parts:
            member = await utils.find_member(ctx, part)
            if member:
                whitelist.append(str(member.id))
        
        # Add current members in the channel to whitelist
        if channel.members:
            for member in channel.members:
                if str(member.id) not in whitelist:
                    whitelist.append(str(member.id))
        
        # Remove any existing protection for this channel
        key = _get_protection_key(ctx.guild.id, channel.id)
        if key in _vocprotect:
            old_protection = _vocprotect[key]
            if old_protection["protector_id"] != ctx.author.id:
                return await ctx.send(embed=utils.err(
                    "Ce salon est déjà protégé par quelqu'un d'autre.",
                    "❌ Déjà protégé"
                ), delete_after=10)
        
        # Create protection
        _vocprotect[key] = {
            "protector_id": ctx.author.id,
            "whitelist": whitelist,
            "blacklist": [],
            "created_at": time.time()
        }
        _save()
        
        # Cancel any existing timer
        if key in _timers:
            _timers[key].cancel()
            del _timers[key]
        
        desc = f"**<#{channel.id}>** est maintenant protégé.\n"
        desc += f"🛡️ Protecteur : {ctx.author.mention}\n"
        desc += f"👥 Whitelist : {len(whitelist)} membre(s)"
        
        await ctx.send(embed=discord.Embed(
            title="🔒 Protection vocale",
            description=desc,
            color=discord.Color.green(),
        ).set_footer(text=f"Par {ctx.author.display_name}"), delete_after=15)

    @commands.command(name="pvw")
    @commands.guild_only()
    async def protect_voc_whitelist(self, ctx: commands.Context, *, args: str = ""):
        """Add users to the whitelist of the currently protected voice channel. Usage: d!pvw @user1 @user2 ..."""
        if ctx.author.id != OWNER_ID:
            return
        await ctx.message.delete()

        if not args:
            return await ctx.send(embed=utils.err(
                f"Usage : `{ctx.prefix}pvw @membre1 @membre2 ...`",
                "❌ Arguments manquants"
            ), delete_after=10)
        
        # Find which channel the user is protecting
        user_protection = None
        protection_key = None
        for key, protection in _vocprotect.items():
            if protection["protector_id"] == ctx.author.id:
                guild_id, channel_id = map(int, key.split("_"))
                if guild_id == ctx.guild.id:
                    user_protection = protection
                    protection_key = key
                    break
        
        if not user_protection:
            return await ctx.send(embed=utils.err(
                "Tu ne protèges aucun salon vocal.",
                "❌ Aucune protection"
            ), delete_after=10)
        
        # Parse members to add
        parts = args.split()
        added = []
        for part in parts:
            member = await utils.find_member(ctx, part)
            if member and str(member.id) not in user_protection["whitelist"]:
                user_protection["whitelist"].append(str(member.id))
                added.append(member.mention)
        
        if not added:
            return await ctx.send(embed=utils.info(
                "Aucun nouveau membre ajouté à la whitelist (déjà présent ou introuvable).",
                "ℹ️ Rien à ajouter"
            ), delete_after=10)
        
        _save()
        
        channel_id = int(protection_key.split("_")[1])
        desc = f"**{len(added)} membre(s)** ajouté(s) à la whitelist de <#{channel_id}>.\n"
        desc += f"👥 {', '.join(added)}"
        
        await ctx.send(embed=discord.Embed(
            title="✅ Whitelist mise à jour",
            description=desc,
            color=discord.Color.green(),
        ).set_footer(text=f"Par {ctx.author.display_name}"), delete_after=15)

    @commands.command(name="pvb")
    @commands.guild_only()
    async def protect_voc_blacklist(self, ctx: commands.Context, *, args: str = ""):
        """Add users to the blacklist of the currently protected voice channel. Usage: d!pvb @user1 @user2 ..."""
        if ctx.author.id != OWNER_ID:
            return
        await ctx.message.delete()

        if not args:
            return await ctx.send(embed=utils.err(
                f"Usage : `{ctx.prefix}pvb @membre1 @membre2 ...`",
                "❌ Arguments manquants"
            ), delete_after=10)
        
        # Find which channel the user is protecting
        user_protection = None
        protection_key = None
        for key, protection in _vocprotect.items():
            if protection["protector_id"] == ctx.author.id:
                guild_id, channel_id = map(int, key.split("_"))
                if guild_id == ctx.guild.id:
                    user_protection = protection
                    protection_key = key
                    break
        
        if not user_protection:
            return await ctx.send(embed=utils.err(
                "Tu ne protèges aucun salon vocal.",
                "❌ Aucune protection"
            ), delete_after=10)
        
        # Initialize blacklist if it doesn't exist (for backwards compatibility)
        if "blacklist" not in user_protection:
            user_protection["blacklist"] = []
        
        # Parse members to blacklist
        parts = args.split()
        added = []
        kicked = []
        for part in parts:
            member = await utils.find_member(ctx, part)
            if member:
                user_id_str = str(member.id)
                # Add to blacklist if not already there
                if user_id_str not in user_protection["blacklist"]:
                    user_protection["blacklist"].append(user_id_str)
                    added.append(member.mention)
                # Remove from whitelist if present
                if user_id_str in user_protection["whitelist"]:
                    user_protection["whitelist"].remove(user_id_str)
                # Kick from channel if currently in it
                channel_id = int(protection_key.split("_")[1])
                channel = ctx.guild.get_channel(channel_id)
                if channel and member.voice and member.voice.channel.id == channel_id:
                    try:
                        await member.move_to(None)
                        kicked.append(member.mention)
                    except (discord.Forbidden, discord.HTTPException):
                        pass
        
        if not added:
            return await ctx.send(embed=utils.info(
                "Aucun nouveau membre ajouté à la blacklist (déjà présent ou introuvable).",
                "ℹ️ Rien à ajouter"
            ), delete_after=10)
        
        _save()
        
        channel_id = int(protection_key.split("_")[1])
        desc = f"**{len(added)} membre(s)** ajouté(s) à la blacklist de <#{channel_id}>.\n"
        desc += f"🚫 {', '.join(added)}"
        if kicked:
            desc += f"\n👢 Expulsé(s) du salon : {', '.join(kicked)}"
        
        await ctx.send(embed=discord.Embed(
            title="🚫 Blacklist mise à jour",
            description=desc,
            color=discord.Color.red(),
        ).set_footer(text=f"Par {ctx.author.display_name}"), delete_after=15)

    @commands.command(name="unpv")
    @commands.guild_only()
    async def unprotect_voc(self, ctx: commands.Context, *, args: str = ""):
        """Remove protection from current voice channel. Usage: d!unpv"""
        if ctx.author.id != OWNER_ID:
            return
        await ctx.message.delete()

        # Check if user is in a voice channel
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send(embed=utils.err(
                "Tu dois être dans un salon vocal pour retirer sa protection.",
                "❌ Pas dans un salon"
            ), delete_after=10)
        
        channel = ctx.author.voice.channel
        
        key = _get_protection_key(ctx.guild.id, channel.id)
        if key not in _vocprotect:
            return await ctx.send(embed=utils.info(
                f"Ce salon n'est pas protégé.",
                "ℹ️ Pas protégé"
            ), delete_after=10)

        del _vocprotect[key]
        _save()
        
        if key in _timers:
            _timers[key].cancel()
            del _timers[key]
        
        await ctx.send(embed=discord.Embed(
            title="🔓 Protection retirée",
            description=f"**<#{channel.id}>** n'est plus protégé.",
            color=discord.Color.green(),
        ).set_footer(text=f"Par {ctx.author.display_name}"), delete_after=15)


async def setup(bot: commands.Bot):
    await bot.add_cog(VocProtectCog(bot))
