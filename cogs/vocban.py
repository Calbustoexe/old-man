import time

import discord
from discord.ext import commands

import utils

OWNER_ID = utils.OWNER_ID

_vocban = utils.load_vocban()


def _save():
    utils.save_vocban(_vocban)


def _clean_expired():
    now = time.time()
    changed = False
    for guild_id in list(_vocban):
        for user_id in list(_vocban[guild_id]):
            entry = _vocban[guild_id][user_id]
            if entry["until"] and now >= entry["until"]:
                del _vocban[guild_id][user_id]
                changed = True
        if not _vocban[guild_id]:
            del _vocban[guild_id]
            changed = True
    if changed:
        _save()

class VocBanCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if after.channel is None:
            return
        if before.channel and before.channel.id == after.channel.id:
            return

        guild_id = str(member.guild.id)
        user_id = str(member.id)
        entry = _vocban.get(guild_id, {}).get(user_id)
        if entry is None:
            return

        if entry["until"] and time.time() >= entry["until"]:
            del _vocban[guild_id][user_id]
            if not _vocban[guild_id]:
                del _vocban[guild_id]
            _save()
            return

        exempt = entry["exempt_channel"]
        if exempt is not None and after.channel.id == exempt:
            return

        try:
            await member.move_to(None)
        except (discord.Forbidden, discord.HTTPException):
            pass

    @commands.command(name="banvoc")
    @commands.guild_only()
    async def banvoc(self, ctx: commands.Context, *, args: str = ""):
        if ctx.author.id != OWNER_ID:
            return

        await ctx.message.delete()
        _clean_expired()

        parts = args.split()
        if not parts:
            return await ctx.send(embed=utils.err(
                f"Usage : `{ctx.prefix}banvoc @membre [temps] [salon_excepté]`",
                "❌ Arguments manquants"
            ), delete_after=10)

        member = await utils.find_member(ctx, parts[0])
        if not member:
            return await ctx.send(embed=utils.err(
                f"Membre `{parts[0]}` introuvable.",
                "❌ Introuvable"
            ), delete_after=10)

        if member.bot:
            return await ctx.send(embed=utils.err(
                "Tu ne peux pas bannir un bot des vocs.",
                "❌ Cible invalide"
            ), delete_after=10)

        duration = None
        exempt_channel = None
        idx = 1

        if idx < len(parts):
            dur = utils.parse_duration(parts[idx])
            if dur is not None:
                duration = dur
                idx += 1

        if idx < len(parts):
            chan = await utils.find_channel(ctx, parts[idx])
            if chan and isinstance(chan, discord.VoiceChannel):
                exempt_channel = chan.id

        guild_id = str(ctx.guild.id)
        user_id = str(member.id)

        _vocban.setdefault(guild_id, {})[user_id] = {
            "exempt_channel": exempt_channel,
            "until": time.time() + duration if duration else None,
        }
        _save()

        desc = f"**{member.mention}** est banni de tous les salons vocaux"
        if exempt_channel:
            desc += f"\nsauf <#{exempt_channel}>"
        if duration:
            desc += f"\n⏱️ Durée : {utils.fmt_duration(duration)}"
        else:
            desc += "\n⏱️ Durée : permanente"

        await ctx.send(embed=discord.Embed(
            title="🔇 Ban vocal",
            description=desc,
            color=discord.Color.red(),
        ).set_footer(text=f"Par {ctx.author.display_name}"), delete_after=15)

        if member.voice and member.voice.channel:
            if exempt_channel is None or member.voice.channel.id != exempt_channel:
                try:
                    await member.move_to(None)
                except (discord.Forbidden, discord.HTTPException):
                    pass

    @commands.command(name="unbanvoc")
    @commands.guild_only()
    async def unbanvoc(self, ctx: commands.Context, *, query: str = ""):
        if ctx.author.id != OWNER_ID:
            return

        await ctx.message.delete()

        if not query:
            return await ctx.send(embed=utils.err(
                f"Usage : `{ctx.prefix}unbanvoc @membre`",
                "❌ Arguments manquants"
            ), delete_after=10)

        member = await utils.find_member(ctx, query)
        if not member:
            return await ctx.send(embed=utils.err(
                f"Membre `{query}` introuvable.",
                "❌ Introuvable"
            ), delete_after=10)

        guild_id = str(ctx.guild.id)
        user_id = str(member.id)

        if guild_id not in _vocban or user_id not in _vocban[guild_id]:
            return await ctx.send(embed=utils.info(
                f"{member.mention} n'est pas banni des vocs.",
                "ℹ️ Aucun ban"
            ), delete_after=10)

        del _vocban[guild_id][user_id]
        if not _vocban[guild_id]:
            del _vocban[guild_id]
        _save()

        await ctx.send(embed=discord.Embed(
            title="🔊 Unban vocal",
            description=f"**{member.mention}** peut à nouveau rejoindre les salons vocaux.",
            color=discord.Color.green(),
        ).set_footer(text=f"Par {ctx.author.display_name}"), delete_after=15)


async def setup(bot: commands.Bot):
    await bot.add_cog(VocBanCog(bot))
