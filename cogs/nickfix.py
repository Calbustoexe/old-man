import asyncio
import logging

import discord
from discord.ext import commands

import utils

log = logging.getLogger("dmallbot.nickfix")


_REFIX_COOLDOWN = 2.0  # secondes


class NickFixCog(commands.Cog):
    """Commandes pour fixer / défixer le pseudo d'un membre."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # {(guild_id, user_id): timestamp} — dernière correction auto effectuée
        self._bot_fix_timestamps: dict[tuple[int, int], float] = {}

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _cooldown_active(self, guild_id: int, user_id: int) -> bool:
        """Vrai si on vient tout juste de corriger ce membre (évite la boucle d'events)."""
        key = (guild_id, user_id)
        last = self._bot_fix_timestamps.get(key, 0.0)
        return (asyncio.get_event_loop().time() - last) < _REFIX_COOLDOWN

    def _mark_correction(self, guild_id: int, user_id: int):
        self._bot_fix_timestamps[(guild_id, user_id)] = asyncio.get_event_loop().time()

    async def _apply_fix(self, member: discord.Member, fixed_nick: str, reason: str = "Pseudo fixé"):
        """Applique le pseudo fixé au membre, absorbe les erreurs de permission."""
        try:
            self._mark_correction(member.guild.id, member.id)
            await member.edit(nick=fixed_nick, reason=reason)
        except discord.Forbidden:
            log.warning(
                "[NickFix] Impossible de corriger le pseudo de %s (%d) — permission manquante.",
                member, member.id,
            )
        except discord.HTTPException as exc:
            log.error("[NickFix] Erreur HTTP pour %s (%d) : %s", member, member.id, exc)

    # ─────────────────────────────────────────────────────────────────────────
    # Listener : on_member_update
    # ─────────────────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """Rétablit immédiatement le pseudo fixé si quelqu'un le change."""
        # Seul le changement de nick nous intéresse
        if before.nick == after.nick:
            return

        fixed = utils.get_fixed_nick(after.guild.id, after.id)
        if fixed is None:
            return  # Pas de fix actif pour ce membre

        # Le pseudo actuel correspond déjà au fix → rien à faire
        if after.nick == fixed:
            return

        # Cooldown : évite la boucle infinie si c'est le bot qui vient de changer le nick
        if self._cooldown_active(after.guild.id, after.id):
            return

        log.info(
            "[NickFix] Pseudo de %s modifié ('%s' → '%s'), rétablissement vers '%s'.",
            after, before.nick, after.nick, fixed,
        )
        await self._apply_fix(after, fixed, reason="Pseudo fixé automatiquement")

    # ─────────────────────────────────────────────────────────────────────────
    # -sfixeon <membre> [pseudo]
    # ─────────────────────────────────────────────────────────────────────────

    @commands.command(name="sfixeon")
    @commands.guild_only()
    async def sfixeon(self, ctx: commands.Context, *, query: str = ""):
        """Fixe le pseudo d'un membre (impossible à changer jusqu'au d!sunfixe).

        Usage :
          d!sfixeon <membre>           → fixe sur son pseudo actuel
          d!sfixeon <membre> <pseudo>  → fixe sur le pseudo précisé
        """
        if ctx.author.id != utils.OWNER_ID:
            return
        await ctx.message.delete()

        if not query:
            return await ctx.send(embed=utils.err(
                f"Usage : `{ctx.prefix}sfixeon <membre> [pseudo]`",
                "❌ Arguments manquants"
            ))

        # Séparation "premier token = membre, reste = pseudo optionnel"
        parts = query.split(None, 1)
        member = await utils.find_member(ctx, parts[0])

        if not member:
            return await ctx.send(embed=utils.err(
                f"Impossible de trouver `{parts[0]}`.\n"
                "Essaie avec sa mention, son ID ou une partie de son pseudo.",
                "❌ Membre introuvable"
            ))

        if member.id == ctx.guild.me.id:
            return await ctx.send(embed=utils.err("Je ne peux pas fixer mon propre pseudo."))

        if member.top_role >= ctx.guild.me.top_role:
            return await ctx.send(embed=utils.err(
                "Ce membre a un rôle trop élevé pour que je puisse modifier son pseudo."
            ))

        # Détermination du pseudo à fixer
        if len(parts) > 1 and parts[1].strip():
            target_nick = parts[1].strip()
        else:
            # Aucun pseudo précisé → on fixe sur le pseudo actuel (ou le nom d'utilisateur)
            target_nick = member.nick or member.name

        # Vérifie la longueur (limite Discord : 1–32 caractères)
        if len(target_nick) > 32:
            return await ctx.send(embed=utils.err(
                f"Le pseudo `{target_nick}` dépasse 32 caractères (limite Discord)."
            ))

        # Sauvegarde + application immédiate
        utils.set_fixed_nick(ctx.guild.id, member.id, target_nick)
        await self._apply_fix(member, target_nick, reason=f"Pseudo fixé par {ctx.author}")

        embed = discord.Embed(
            title="📌 Pseudo fixé",
            description=(
                f"Le pseudo de {member.mention} est maintenant **fixé** sur :\n"
                f"**{discord.utils.escape_markdown(target_nick)}**\n\n"
                f"Toute modification sera annulée automatiquement."
            ),
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Par {ctx.author.display_name} • -sunfixe pour retirer le fix")
        await ctx.send(embed=embed)

    @sfixeon.error
    async def sfixeon_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=utils.err(
                f"Usage : `{ctx.prefix}sfixeon <membre> [pseudo]`",
                "❌ Arguments manquants"
            ))

    # ─────────────────────────────────────────────────────────────────────────
    # -sunfixe <membre>
    # ─────────────────────────────────────────────────────────────────────────

    @commands.command(name="sunfixe")
    @commands.guild_only()
    async def sunfixe(self, ctx: commands.Context, *, query: str = ""):
        """Défix le pseudo d'un membre (il pourra à nouveau le changer librement).

        Usage : d!sunfixe <membre>
        """
        if ctx.author.id != utils.OWNER_ID:
            return
        await ctx.message.delete()

        if not query:
            return await ctx.send(embed=utils.err(
                f"Usage : `{ctx.prefix}sunfixe <membre>`",
                "❌ Arguments manquants"
            ))

        member = await utils.find_member(ctx, query.strip())
        if not member:
            return await ctx.send(embed=utils.err(
                f"Impossible de trouver `{query.strip()}`.\n"
                "Essaie avec sa mention, son ID ou une partie de son pseudo.",
                "❌ Membre introuvable"
            ))

        old_fix = utils.get_fixed_nick(ctx.guild.id, member.id)
        removed = utils.unset_fixed_nick(ctx.guild.id, member.id)

        if not removed:
            return await ctx.send(embed=utils.err(
                f"{member.mention} n'a aucun pseudo fixé en ce moment.",
                "❌ Aucun fix actif"
            ))

        embed = discord.Embed(
            title="📌 Pseudo libéré",
            description=(
                f"Le pseudo de {member.mention} n'est plus fixé.\n"
                f"*(ancien fix : **{discord.utils.escape_markdown(old_fix)}**)*\n\n"
                f"Il peut à nouveau modifier son pseudo librement."
            ),
            color=discord.Color.green(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Par {ctx.author.display_name}")
        await ctx.send(embed=embed)

    @sunfixe.error
    async def sunfixe_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=utils.err(
                f"Usage : `{ctx.prefix}sunfixe <membre>`",
                "❌ Arguments manquants"
            ))

    # ─────────────────────────────────────────────────────────────────────────
    # -sfixelist  (bonus : voir tous les pseudos fixés du serveur)
    # ─────────────────────────────────────────────────────────────────────────

    @commands.command(name="sfixelist")
    @commands.guild_only()
    async def sfixelist(self, ctx: commands.Context):
        """Affiche la liste de tous les membres avec un pseudo fixé sur ce serveur."""
        if ctx.author.id != utils.OWNER_ID:
            return
        fixed = utils.get_all_fixed(ctx.guild.id)
        if not fixed:
            return await ctx.send(embed=discord.Embed(
                title="📌 Aucun pseudo fixé",
                description="Personne n'a de pseudo fixé sur ce serveur.",
                color=discord.Color.greyple(),
            ))

        lines = []
        for uid, nick in fixed.items():
            member = ctx.guild.get_member(int(uid))
            name = member.mention if member else f"Utilisateur inconnu (`{uid}`)"
            lines.append(f"{name} → **{discord.utils.escape_markdown(nick)}**")

        embed = discord.Embed(
            title=f"📌 Pseudos fixés — {len(fixed)} membre(s)",
            description="\n".join(lines[:30]),
            color=discord.Color.blurple(),
        )
        if len(fixed) > 30:
            embed.set_footer(text=f"… et {len(fixed) - 30} autres")
        await ctx.send(embed=embed)



async def setup(bot: commands.Bot):
    await bot.add_cog(NickFixCog(bot))
