import discord
from discord.ext import commands
from datetime import timedelta, datetime, timezone

import utils


class ModCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _hierarchy_error(self, ctx: commands.Context, member: discord.Member) -> str | None:
        if member.id == ctx.author.id:
            return "Tu ne peux pas te cibler toi-même."
        if member.id == ctx.guild.owner_id:
            return "Impossible d'agir sur le proprio du serveur."
        if ctx.author.id != ctx.guild.owner_id and member.top_role >= ctx.author.top_role:
            return "Ce membre a un rôle égal ou supérieur au tien."
        if member.top_role >= ctx.guild.me.top_role:
            return "Ce membre a un rôle trop élevé pour que je puisse agir dessus."
        return None

    # ── KICK ─────────────────────────────────────────────────────────────────

    @commands.command(name="kick")
    @commands.has_permissions(kick_members=True)
    @commands.guild_only()
    async def kick(self, ctx: commands.Context, *, query: str):
        await ctx.message.delete()
        parts = query.split(None, 1)
        member = await utils.find_member(ctx, parts[0])
        reason = parts[1] if len(parts) > 1 else None

        if not member:
            return await ctx.send(embed=utils.err(
                f"Impossible de trouver `{parts[0]}`.\nEssaie avec sa mention, son ID ou une partie de son pseudo.",
                "❌ Membre introuvable"
            ))

        if msg := self._hierarchy_error(ctx, member):
            return await ctx.send(embed=utils.err(msg))

        dm_sent = False
        try:
            dm_embed = discord.Embed(
                title="👢 Tu as été expulsé",
                description=f"Tu as été expulsé du serveur **{ctx.guild.name}**.",
                color=discord.Color.orange(),
            )
            dm_embed.add_field(name="Raison", value=reason or "Aucune raison fournie")
            dm_embed.set_footer(text=f"Par {ctx.author}")
            await member.send(embed=dm_embed)
            dm_sent = True
        except (discord.Forbidden, discord.HTTPException):
            pass

        try:
            await member.kick(reason=f"{ctx.author}: {reason or 'Aucune raison'}")
        except discord.Forbidden:
            return await ctx.send(embed=utils.err("Je n'ai pas la permission d'expulser ce membre."))

        embed = discord.Embed(
            title="👢 Membre expulsé",
            description=f"**{member}** a bien été expulsé.",
            color=discord.Color.orange(),
        )
        if reason:
            embed.add_field(name="Raison", value=reason, inline=False)
        embed.add_field(name="Notifié en DM", value="✅ Oui" if dm_sent else "❌ DMs fermés", inline=True)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Par {ctx.author.display_name}")
        await ctx.send(embed=embed)

    @kick.error
    async def kick_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(embed=utils.err("Il te faut la permission **Expulser des membres** pour ça."))
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=utils.err(f"Usage : `{ctx.prefix}kick <membre> [raison]`", "❌ Arguments manquants"))

    # ── BAN ──────────────────────────────────────────────────────────────────

    @commands.command(name="ban")
    @commands.has_permissions(ban_members=True)
    @commands.guild_only()
    async def ban(self, ctx: commands.Context, *, query: str):
        await ctx.message.delete()
        parts = query.split(None, 1)
        member = await utils.find_member(ctx, parts[0])
        reason = parts[1] if len(parts) > 1 else None

        if not member:
            return await ctx.send(embed=utils.err(
                f"Impossible de trouver `{parts[0]}`.",
                "❌ Membre introuvable"
            ))

        if msg := self._hierarchy_error(ctx, member):
            return await ctx.send(embed=utils.err(msg))

        dm_sent = False
        try:
            dm_embed = discord.Embed(
                title="🔨 Tu as été banni",
                description=f"Tu as été banni du serveur **{ctx.guild.name}**.",
                color=discord.Color.red(),
            )
            dm_embed.add_field(name="Raison", value=reason or "Aucune raison fournie")
            dm_embed.set_footer(text=f"Par {ctx.author}")
            await member.send(embed=dm_embed)
            dm_sent = True
        except (discord.Forbidden, discord.HTTPException):
            pass

        try:
            await member.ban(reason=f"{ctx.author}: {reason or 'Aucune raison'}", delete_message_days=0)
        except discord.Forbidden:
            return await ctx.send(embed=utils.err("Je n'ai pas la permission de bannir ce membre."))

        embed = discord.Embed(
            title="🔨 Membre banni",
            description=f"**{member}** a été banni définitivement.",
            color=discord.Color.red(),
        )
        if reason:
            embed.add_field(name="Raison", value=reason, inline=False)
        embed.add_field(name="Notifié en DM", value="✅ Oui" if dm_sent else "❌ DMs fermés", inline=True)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Par {ctx.author.display_name}")
        await ctx.send(embed=embed)

    @ban.error
    async def ban_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(embed=utils.err("Il te faut la permission **Bannir des membres** pour ça."))
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=utils.err(f"Usage : `{ctx.prefix}ban <membre> [raison]`", "❌ Arguments manquants"))

    # ── UNBAN ─────────────────────────────────────────────────────────────────

    @commands.command(name="unban")
    @commands.has_permissions(ban_members=True)
    @commands.guild_only()
    async def unban(self, ctx: commands.Context, *, query: str):
        await ctx.message.delete()
        query = query.strip()
        banned = [entry async for entry in ctx.guild.bans()]

        target = None
        if query.isdigit():
            target = next((e for e in banned if e.user.id == int(query)), None)
        else:
            ql = query.lower()
            target = next((e for e in banned if str(e.user).lower() == ql or e.user.name.lower() == ql), None)
            if not target:
                target = next((e for e in banned if ql in e.user.name.lower()), None)

        if not target:
            return await ctx.send(embed=utils.err(
                f"Aucun banni trouvé pour `{query}`.\nUtilise l'ID ou le pseudo exact (nom#0000).",
                "❌ Utilisateur introuvable"
            ))

        await ctx.guild.unban(target.user, reason=f"Débanni par {ctx.author}")
        embed = discord.Embed(
            title="🔓 Membre débanni",
            description=f"**{target.user}** peut de nouveau rejoindre le serveur.",
            color=discord.Color.green(),
        )
        embed.set_thumbnail(url=target.user.display_avatar.url)
        embed.set_footer(text=f"Par {ctx.author.display_name}")
        await ctx.send(embed=embed)

    @unban.error
    async def unban_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(embed=utils.err("Il te faut la permission **Bannir des membres** pour débannir."))
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=utils.err(f"Usage : `{ctx.prefix}unban <ID ou pseudo>`", "❌ Arguments manquants"))

    # ── MUTE (timeout) ────────────────────────────────────────────────────────

    @commands.command(name="mute")
    @commands.has_permissions(moderate_members=True)
    @commands.guild_only()
    async def mute(self, ctx: commands.Context, *, query: str):
        await ctx.message.delete()
        parts = query.split(None, 2)

        if len(parts) < 2:
            return await ctx.send(embed=utils.err(
                f"Usage : `{ctx.prefix}mute <membre> <durée> [raison]`\nDurées valides : `30s` `10m` `2h` `1d` `1w`",
                "❌ Arguments manquants"
            ))

        member = await utils.find_member(ctx, parts[0])
        if not member:
            return await ctx.send(embed=utils.err(f"Membre `{parts[0]}` introuvable.", "❌ Introuvable"))

        seconds = utils.parse_duration(parts[1])
        if seconds is None:
            return await ctx.send(embed=utils.err(
                f"`{parts[1]}` n'est pas une durée reconnue.\nFormats acceptés : `30s`, `10m`, `2h`, `1d`, `1w`",
                "❌ Durée invalide"
            ))

        if seconds > 2419200:
            return await ctx.send(embed=utils.err("La durée max du timeout Discord est **28 jours**."))

        if msg := self._hierarchy_error(ctx, member):
            return await ctx.send(embed=utils.err(msg))

        reason = parts[2] if len(parts) > 2 else None
        until = discord.utils.utcnow() + timedelta(seconds=seconds)

        try:
            await member.timeout(until, reason=f"{ctx.author}: {reason or 'Aucune raison'}")
        except discord.Forbidden:
            return await ctx.send(embed=utils.err("Je n'ai pas la permission de mettre ce membre en timeout."))

        dm_sent = False
        try:
            dm_embed = discord.Embed(
                title="🔇 Tu as été mis en sourdine",
                description=f"Tu es en timeout sur **{ctx.guild.name}** pour **{utils.fmt_duration(seconds)}**.",
                color=discord.Color.orange(),
            )
            dm_embed.add_field(name="Raison", value=reason or "Aucune raison fournie")
            dm_embed.set_footer(text=f"Par {ctx.author}")
            await member.send(embed=dm_embed)
            dm_sent = True
        except (discord.Forbidden, discord.HTTPException):
            pass

        embed = discord.Embed(
            title="🔇 Membre mis en timeout",
            description=f"{member.mention} ne peut plus parler pendant **{utils.fmt_duration(seconds)}**.",
            color=discord.Color.orange(),
        )
        if reason:
            embed.add_field(name="Raison", value=reason, inline=False)
        embed.add_field(name="Notifié en DM", value="✅ Oui" if dm_sent else "❌ DMs fermés", inline=True)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Par {ctx.author.display_name}")
        await ctx.send(embed=embed)

    @mute.error
    async def mute_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(embed=utils.err("Il te faut la permission **Exclure temporairement des membres**."))
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=utils.err(f"Usage : `{ctx.prefix}mute <membre> <durée> [raison]`", "❌ Arguments manquants"))

    # ── UNMUTE ────────────────────────────────────────────────────────────────

    @commands.command(name="unmute")
    @commands.has_permissions(moderate_members=True)
    @commands.guild_only()
    async def unmute(self, ctx: commands.Context, *, query: str):
        await ctx.message.delete()
        member = await utils.find_member(ctx, query)
        if not member:
            return await ctx.send(embed=utils.err(f"Membre `{query}` introuvable.", "❌ Introuvable"))

        if not member.is_timed_out():
            return await ctx.send(embed=utils.err(
                f"{member.mention} n'est pas en timeout en ce moment.",
                "❌ Pas en timeout"
            ))

        if msg := self._hierarchy_error(ctx, member):
            return await ctx.send(embed=utils.err(msg))

        try:
            await member.timeout(None, reason=f"Timeout levé par {ctx.author}")
        except discord.Forbidden:
            return await ctx.send(embed=utils.err("Je n'ai pas la permission d'agir sur ce membre."))

        await ctx.send(embed=discord.Embed(
            title="🔊 Timeout levé",
            description=f"{member.mention} peut à nouveau s'exprimer.",
            color=discord.Color.green(),
        ).set_footer(text=f"Par {ctx.author.display_name}"))

    @unmute.error
    async def unmute_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(embed=utils.err("Il te faut la permission **Exclure temporairement des membres**."))
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=utils.err(f"Usage : `{ctx.prefix}unmute <membre>`", "❌ Arguments manquants"))

    # ── WARN ──────────────────────────────────────────────────────────────────

    @commands.command(name="warn")
    @commands.has_permissions(manage_messages=True)
    @commands.guild_only()
    async def warn(self, ctx: commands.Context, *, query: str):
        await ctx.message.delete()
        parts = query.split(None, 1)
        member = await utils.find_member(ctx, parts[0])
        reason = parts[1] if len(parts) > 1 else "Aucune raison fournie"

        if not member:
            return await ctx.send(embed=utils.err(f"Membre `{parts[0]}` introuvable.", "❌ Introuvable"))
        if member.id == ctx.author.id:
            return await ctx.send(embed=utils.err("Tu ne peux pas te warn toi-même."))
        if member.bot:
            return await ctx.send(embed=utils.err("Impossible de warn un bot."))

        warn_id = utils.add_warn(ctx.guild.id, member.id, ctx.author.id, reason)
        total = len(utils.get_warns(ctx.guild.id, member.id))

        dm_sent = False
        try:
            dm_embed = discord.Embed(
                title="⚠️ Tu as reçu un avertissement",
                description=f"Un modérateur de **{ctx.guild.name}** t'a averti.",
                color=discord.Color.yellow(),
            )
            dm_embed.add_field(name="Raison", value=reason, inline=False)
            dm_embed.add_field(name="Total de warns", value=str(total), inline=True)
            dm_embed.set_footer(text=f"Par {ctx.author}")
            await member.send(embed=dm_embed)
            dm_sent = True
        except (discord.Forbidden, discord.HTTPException):
            pass

        embed = discord.Embed(
            title="⚠️ Avertissement émis",
            description=f"{member.mention} a reçu le warn **#{warn_id}** ({total} au total).",
            color=discord.Color.yellow(),
        )
        embed.add_field(name="Raison", value=reason, inline=False)
        embed.add_field(name="Notifié en DM", value="✅ Oui" if dm_sent else "❌ DMs fermés", inline=True)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Par {ctx.author.display_name}")
        await ctx.send(embed=embed)

    @warn.error
    async def warn_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(embed=utils.err("Il te faut la permission **Gérer les messages** pour warn."))
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=utils.err(f"Usage : `{ctx.prefix}warn <membre> [raison]`", "❌ Arguments manquants"))

    # ── WARNS ─────────────────────────────────────────────────────────────────

    @commands.command(name="warns")
    @commands.has_permissions(manage_messages=True)
    @commands.guild_only()
    async def warns(self, ctx: commands.Context, *, query: str):
        member = await utils.find_member(ctx, query)
        if not member:
            return await ctx.send(embed=utils.err(f"Membre `{query}` introuvable.", "❌ Introuvable"))

        warn_list = utils.get_warns(ctx.guild.id, member.id)
        if not warn_list:
            return await ctx.send(embed=discord.Embed(
                title="✅ Aucun warn",
                description=f"{member.mention} n'a reçu aucun avertissement.",
                color=discord.Color.green(),
            ))

        embed = discord.Embed(
            title=f"⚠️ Warns de {member.display_name}",
            color=discord.Color.yellow(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        for w in warn_list:
            mod = ctx.guild.get_member(int(w["by"]))
            mod_name = mod.display_name if mod else f"ID {w['by']}"
            try:
                dt = datetime.fromisoformat(w["at"])
                ts = f"<t:{int(dt.timestamp())}:d>"
            except Exception:
                ts = "?"
            embed.add_field(
                name=f"Warn #{w['id']}",
                value=f"**Raison :** {w['reason']}\n**Par :** {mod_name} — {ts}",
                inline=False,
            )

        embed.set_footer(text=f"{len(warn_list)} avertissement(s) • -delwarn {member.display_name} <n°> pour en supprimer un")
        await ctx.send(embed=embed)

    @warns.error
    async def warns_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(embed=utils.err("Il te faut la permission **Gérer les messages**."))
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=utils.err(f"Usage : `{ctx.prefix}warns <membre>`", "❌ Arguments manquants"))

    # ── WARNLIST ──────────────────────────────────────────────────────────────

    @commands.command(name="warnlist")
    @commands.has_permissions(manage_messages=True)
    @commands.guild_only()
    async def warnlist(self, ctx: commands.Context):
        all_warned = utils.get_all_warned(ctx.guild.id)
        if not all_warned:
            return await ctx.send(embed=discord.Embed(
                title="✅ Aucun warn",
                description="Personne n'a été averti sur ce serveur.",
                color=discord.Color.green(),
            ))

        sorted_warns = sorted(all_warned.items(), key=lambda x: len(x[1]), reverse=True)
        lines = []
        for uid, warns in sorted_warns:
            member = ctx.guild.get_member(int(uid))
            name = member.display_name if member else f"Utilisateur inconnu ({uid})"
            lines.append(f"**{name}** — {len(warns)} warn(s)")

        description = "\n".join(lines[:25])
        if len(sorted_warns) > 25:
            description += f"\n*… et {len(sorted_warns) - 25} autres*"

        await ctx.send(embed=discord.Embed(
            title=f"📋 Membres avertis — {len(all_warned)} au total",
            description=description,
            color=discord.Color.orange(),
        ))

    @warnlist.error
    async def warnlist_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(embed=utils.err("Il te faut la permission **Gérer les messages**."))

    # ── DELWARN ───────────────────────────────────────────────────────────────

    @commands.command(name="delwarn")
    @commands.has_permissions(manage_messages=True)
    @commands.guild_only()
    async def delwarn(self, ctx: commands.Context, *, query: str):
        parts = query.rsplit(None, 1)
        if len(parts) < 2 or not parts[1].isdigit():
            return await ctx.send(embed=utils.err(
                f"Usage : `{ctx.prefix}delwarn <membre> <n° du warn>`",
                "❌ Arguments invalides"
            ))

        member = await utils.find_member(ctx, parts[0])
        if not member:
            return await ctx.send(embed=utils.err(f"Membre `{parts[0]}` introuvable.", "❌ Introuvable"))

        warn_id = int(parts[1])
        if not utils.del_warn(ctx.guild.id, member.id, warn_id):
            return await ctx.send(embed=utils.err(
                f"Le warn **#{warn_id}** n'existe pas pour {member.mention}.\nUtilise `{ctx.prefix}warns {member.display_name}` pour voir ses warns.",
                "❌ Warn introuvable"
            ))

        remaining = len(utils.get_warns(ctx.guild.id, member.id))
        await ctx.send(embed=discord.Embed(
            title="🗑️ Warn supprimé",
            description=f"Le warn **#{warn_id}** de {member.mention} a été retiré. ({remaining} warn(s) restant(s))",
            color=discord.Color.green(),
        ).set_footer(text=f"Par {ctx.author.display_name}"))

    @delwarn.error
    async def delwarn_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(embed=utils.err("Il te faut la permission **Gérer les messages**."))

    # ── CLEARWARNS ────────────────────────────────────────────────────────────

    @commands.command(name="clearwarns")
    @commands.has_permissions(manage_messages=True)
    @commands.guild_only()
    async def clearwarns(self, ctx: commands.Context, *, query: str):
        member = await utils.find_member(ctx, query)
        if not member:
            return await ctx.send(embed=utils.err(f"Membre `{query}` introuvable.", "❌ Introuvable"))

        count = utils.clear_warns(ctx.guild.id, member.id)
        if count == 0:
            return await ctx.send(embed=utils.err(
                f"{member.mention} n'a aucun warn à effacer.",
                "❌ Aucun warn"
            ))

        await ctx.send(embed=discord.Embed(
            title="🧹 Warns effacés",
            description=f"Les **{count}** avertissement(s) de {member.mention} ont été supprimés.",
            color=discord.Color.green(),
        ).set_footer(text=f"Par {ctx.author.display_name}"))

    @clearwarns.error
    async def clearwarns_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(embed=utils.err("Il te faut la permission **Gérer les messages**."))
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=utils.err(f"Usage : `{ctx.prefix}clearwarns <membre>`", "❌ Arguments manquants"))


async def setup(bot: commands.Bot):
    await bot.add_cog(ModCog(bot))
