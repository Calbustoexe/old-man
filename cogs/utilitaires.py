"""
Cog utilitaire (Gotei RP Bleach).

d!nikreset : réinitialise le pseudo de tous les membres du serveur qui ont
un pseudo personnalisé (surnom serveur), en le retirant pour revenir à leur
nom d'affichage de base (username / nom global Discord).
"""
import asyncio
import logging

import discord
from discord.ext import commands

logger = logging.getLogger("urahara.utility")


def error_embed(description: str) -> discord.Embed:
    return discord.Embed(description=f"❌ {description}", color=discord.Color.red())


def success_embed(title: str, description: str = "") -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=discord.Color.green())
    embed.set_footer(text="Urahara • Utilitaire")
    return embed


class Utility(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="nikreset")
    async def nikreset(self, ctx: commands.Context):
        """d!nikreset — retire le pseudo personnalisé de tous les membres du serveur."""
        guild = ctx.guild

        to_reset = [m for m in guild.members if m.nick is not None]
        if not to_reset:
            await ctx.send(embed=success_embed("Rien à faire", "Aucun membre n'a de pseudo personnalisé."))
            return

        progress = await ctx.send(embed=discord.Embed(
            description=f"⏳ Réinitialisation de **{len(to_reset)}** pseudo(s) en cours...",
            color=discord.Color.orange(),
        ))

        reset_count = 0
        failed_count = 0
        for member in to_reset:
            try:
                await member.edit(nick=None, reason=f"Réinitialisation globale des pseudos par {ctx.author}")
                reset_count += 1
            except discord.Forbidden:
                failed_count += 1
            except discord.HTTPException:
                failed_count += 1
            await asyncio.sleep(0.5) 

        summary = success_embed(
            "Pseudos réinitialisés",
            f"✅ {reset_count} pseudo(s) réinitialisé(s).\n"
            + (f"⚠️ {failed_count} échec(s) (probablement des membres avec un rôle supérieur au mien)." if failed_count else ""),
        )
        await progress.edit(embed=summary)

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(embed=error_embed("Tu dois avoir la permission de gérer les pseudos pour utiliser cette commande."))
            return
        if isinstance(error, commands.CommandNotFound):
            return
        logger.exception("Erreur non gérée sur une commande utilitaire", exc_info=error)
        await ctx.send(embed=error_embed("Une erreur inattendue est survenue."))


async def setup(bot: commands.Bot):
    await bot.add_cog(Utility(bot))