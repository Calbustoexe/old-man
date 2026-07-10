import discord
from discord.ext import commands

import utils

_fun_settings = utils.load_fun_settings()

_zerospace: bool = _fun_settings["zerospace"]
_zerospace_exceptions: set[int] = set(_fun_settings["zerospace_exceptions"])

_bustogoat: bool = _fun_settings["bustogoat"]
_bustogoat_exceptions: set[int] = set(_fun_settings["bustogoat_exceptions"])
_bustogoat_word: str = _fun_settings["bustogoat_word"]

_modmot: bool = _fun_settings["modmot"]
_modmot_words: set[str] = set(_fun_settings["modmot_words"])


def _save_fun_settings():
    utils.save_fun_settings({
        "zerospace": _zerospace,
        "zerospace_exceptions": list(_zerospace_exceptions),
        "bustogoat": _bustogoat,
        "bustogoat_exceptions": list(_bustogoat_exceptions),
        "bustogoat_word": _bustogoat_word,
        "modmot": _modmot,
        "modmot_words": list(_modmot_words),
    })


class FunCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── PING ──────────────────────────────────────────────────────────────────

    @commands.command(name="ping")
    async def ping(self, ctx: commands.Context):
        await ctx.message.delete()
        latency = round(self.bot.latency * 1000)
        color = (
            discord.Color.green() if latency < 100
            else discord.Color.orange() if latency < 200
            else discord.Color.red()
        )
        await ctx.send(embed=discord.Embed(
            title="🏓 Pong !",
            description=f"Latence API : **{latency}ms**",
            color=color,
        ))

    # ── SAY ───────────────────────────────────────────────────────────────────

    @commands.command(name="say", aliases=["dis"])
    async def say(self, ctx: commands.Context, *, message: str):
        await ctx.message.delete()
        await ctx.send(message)

    @say.error
    async def say_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(embed=utils.err("Il te faut les permissions d'administrateur pour ça."))
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=utils.err(f"Usage : `{ctx.prefix}say <message>`", "❌ Arguments manquants"))

    # ── ZERO-SPACE ────────────────────────────────────────────────────────────

    @commands.command(name="0spaceon", aliases=["0son"])
    async def zerospace_on(self, ctx: commands.Context):
        global _zerospace
        _zerospace = True
        _save_fun_settings()
        await ctx.message.delete()
        await ctx.send(embed=discord.Embed(
            title="🚫 Zero-Space activé",
            description="Tous les messages contenant des espaces seront supprimés.",
            color=discord.Color.red(),
        ).set_footer(text=f"Par {ctx.author.display_name}"))

    @commands.command(name="0spaceoff", aliases=["0sof"])
    async def zerospace_off(self, ctx: commands.Context):
        global _zerospace
        _zerospace = False
        _save_fun_settings()
        await ctx.message.delete()
        await ctx.send(embed=discord.Embed(
            title="✅ Zero-Space désactivé",
            description="Les espaces sont à nouveau autorisés.",
            color=discord.Color.green(),
        ).set_footer(text=f"Par {ctx.author.display_name}"))

    @commands.command(name="0spaceignore", aliases=["0si"])
    async def zerospace_ignore(self, ctx: commands.Context, *, query: str):
        member = await utils.find_member(ctx, query)
        if not member:
            return await ctx.send(embed=utils.err(f"Membre `{query}` introuvable."))
        if member.id in _zerospace_exceptions:
            return await ctx.send(embed=utils.info(f"{member.mention} est déjà exempté."))
        _zerospace_exceptions.add(member.id)
        _save_fun_settings()
        await ctx.send(embed=utils.ok(f"{member.mention} est exempté du zero-space."))

    @commands.command(name="0spacedel", aliases=["0sdel", "0su"])
    async def zerospace_del(self, ctx: commands.Context, *, query: str):
        member = await utils.find_member(ctx, query)
        if not member:
            return await ctx.send(embed=utils.err(f"Membre `{query}` introuvable."))
        if member.id not in _zerospace_exceptions:
            return await ctx.send(embed=utils.info(f"{member.mention} n'est pas en exception."))
        _zerospace_exceptions.discard(member.id)
        _save_fun_settings()
        await ctx.send(embed=utils.ok(f"{member.mention} retiré des exceptions zero-space."))

    # ── BUSTOGOAT ─────────────────────────────────────────────────────────────

    @commands.command(name="bgon")
    async def bg_on(self, ctx: commands.Context):
        global _bustogoat
        _bustogoat = True
        _save_fun_settings()
        await ctx.message.delete()
        await ctx.send(embed=discord.Embed(
            title="🐐 Bustogoat — Activé",
            description=f"Chaque message doit se terminer par **`{_bustogoat_word}`** ou il sera supprimé.",
            color=discord.Color.red(),
        ).set_footer(text=f"Par {ctx.author.display_name}"))

    @commands.command(name="bgoff")
    async def bg_off(self, ctx: commands.Context):
        global _bustogoat
        _bustogoat = False
        _save_fun_settings()
        await ctx.message.delete()
        await ctx.send(embed=discord.Embed(
            title="🐐 Bustogoat — Désactivé",
            description="Le mode Bustogoat est désactivé.",
            color=discord.Color.green(),
        ).set_footer(text=f"Par {ctx.author.display_name}"))

    @commands.command(name="bgset")
    async def bg_set(self, ctx: commands.Context, *, word: str):
        global _bustogoat_word
        word = word.strip()
        if not word:
            return await ctx.send(embed=utils.err("Le mot ne peut pas être vide."))
        _bustogoat_word = word
        _save_fun_settings()
        await ctx.message.delete()
        await ctx.send(embed=utils.ok(f"Mot de fin défini sur : **`{word}`**"))

    @bg_set.error
    async def bgset_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=utils.err(f"Usage : `{ctx.prefix}bgset <mot>`", "❌ Arguments manquants"))

    @commands.command(name="bgignore")
    async def bg_ignore(self, ctx: commands.Context, *, query: str):
        member = await utils.find_member(ctx, query)
        if not member:
            return await ctx.send(embed=utils.err(f"Membre `{query}` introuvable."))
        if member.id in _bustogoat_exceptions:
            return await ctx.send(embed=utils.info(f"{member.mention} est déjà exempté."))
        _bustogoat_exceptions.add(member.id)
        _save_fun_settings()
        await ctx.send(embed=utils.ok(f"{member.mention} est exempté du mode Bustogoat."))

    @commands.command(name="bgdel")
    async def bg_del(self, ctx: commands.Context, *, query: str):
        member = await utils.find_member(ctx, query)
        if not member:
            return await ctx.send(embed=utils.err(f"Membre `{query}` introuvable."))
        if member.id not in _bustogoat_exceptions:
            return await ctx.send(embed=utils.info(f"{member.mention} n'est pas en exception."))
        _bustogoat_exceptions.discard(member.id)
        _save_fun_settings()
        await ctx.send(embed=utils.ok(f"{member.mention} retiré des exceptions Bustogoat."))

    # ── MODMOT ────────────────────────────────────────────────────────────────

    @commands.command(name="mmon")
    async def mm_on(self, ctx: commands.Context):
        global _modmot
        _modmot = True
        _save_fun_settings()
        await ctx.message.delete()
        desc = f"**{len(_modmot_words)}** mot(s) filtré(s)." if _modmot_words else f"Aucun mot filtré pour l'instant — ajoutes-en avec `{ctx.prefix}mmadd`."
        await ctx.send(embed=discord.Embed(
            title="🚫 ModMot activé",
            description=desc,
            color=discord.Color.red(),
        ).set_footer(text=f"Par {ctx.author.display_name}"))

    @commands.command(name="mmoff")
    async def mm_off(self, ctx: commands.Context):
        global _modmot
        _modmot = False
        _save_fun_settings()
        await ctx.message.delete()
        await ctx.send(embed=discord.Embed(
            title="✅ ModMot désactivé",
            description="Le filtre de mots est désactivé.",
            color=discord.Color.green(),
        ).set_footer(text=f"Par {ctx.author.display_name}"))

    @commands.command(name="mmadd")
    async def mm_add(self, ctx: commands.Context, *, word: str):
        word = word.lower().strip()
        if not word:
            return await ctx.send(embed=utils.err("Le mot ne peut pas être vide."))
        if word in _modmot_words:
            return await ctx.send(embed=utils.info(f"`{word}` est déjà dans la liste."))
        _modmot_words.add(word)
        _save_fun_settings()
        await ctx.send(embed=utils.ok(f"Mot ajouté : **`{word}`** ({len(_modmot_words)} au total)"))

    @mm_add.error
    async def mmadd_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=utils.err(f"Usage : `{ctx.prefix}mmadd <mot>`", "❌ Arguments manquants"))

    @commands.command(name="mmdel")
    async def mm_del(self, ctx: commands.Context, *, word: str):
        word = word.lower().strip()
        if word not in _modmot_words:
            return await ctx.send(embed=utils.err(f"`{word}` n'est pas dans la liste."))
        _modmot_words.discard(word)
        _save_fun_settings()
        await ctx.send(embed=utils.ok(f"Mot retiré : **`{word}`** ({len(_modmot_words)} restant(s))"))

    @mm_del.error
    async def mmdel_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=utils.err(f"Usage : `{ctx.prefix}mmdel <mot>`", "❌ Arguments manquants"))

    @commands.command(name="mmlist")
    async def mm_list(self, ctx: commands.Context):
        if not _modmot_words:
            return await ctx.send(embed=utils.info("Aucun mot dans la liste pour l'instant.", "📋 ModMot — Liste vide"))
        await ctx.send(embed=discord.Embed(
            title=f"📋 ModMot — {len(_modmot_words)} mot(s) filtré(s)",
            description="\n".join(f"`{w}`" for w in sorted(_modmot_words)),
            color=discord.Color.blurple(),
        ))

    # ── Listener ──────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.content:
            return

        content = message.content

        if _zerospace and message.author.id not in _zerospace_exceptions and " " in content:
            try:
                await message.delete()
                await message.channel.send(
                    f"{message.author.mention} — les espaces sont **interdits** ici.",
                    delete_after=5,
                )
            except (discord.Forbidden, discord.HTTPException):
                pass
            return

        if _bustogoat and message.author.id not in _bustogoat_exceptions:
            if content.strip() and not content.strip().lower().endswith(_bustogoat_word.lower()):
                try:
                    await message.delete()
                    await message.channel.send(
                        f"{message.author.mention} — ton message doit finir par **`{_bustogoat_word}`**.",
                        delete_after=5,
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return

        if _modmot:
            lower = content.lower()
            if any(word in lower for word in _modmot_words):
                try:
                    await message.delete()
                except (discord.Forbidden, discord.HTTPException):
                    pass


async def setup(bot: commands.Bot):
    await bot.add_cog(FunCog(bot))