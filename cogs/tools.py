import asyncio
import re

import discord
from discord.ext import commands

import utils

_tools_settings = utils.load_tools_settings()

sniped: dict[int, dict] = {}
edit_sniped: dict[int, dict] = {}
lock_backup: dict[int, dict] = _tools_settings.get("lock_backup", {})
french_only_channels: set[int] = set(_tools_settings.get("french_only_channels", []))

def _save_tools_settings():
    utils.save_tools_settings({
        "french_only_channels": list(french_only_channels),
        "lock_backup": lock_backup,
    })

_FRENCH_ACCENTS = set("àâäæçéèêëìîïñòôöœùûüÀÂÄÆÇÉÈÊËÌÎÏÑÒÔÖŒÙÛÜ")

_FRENCH_WORDS = {
    "le", "la", "les", "un", "une", "des", "de", "du", "à", "au", "aux",
    "et", "ou", "donc", "or", "ni", "car", "mais", "que", "qui", "quoi",
    "ce", "cette", "ces", "mon", "ton", "son", "mes", "tes", "ses",
    "je", "tu", "il", "elle", "on", "nous", "vous", "ils", "elles",
    "suis", "es", "est", "sommes", "êtes", "sont", "ai", "as", "a", "avons", "avez", "ont",
    "être", "avoir", "faire", "dire", "aller", "voir", "savoir", "pouvoir",
    "vouloir", "devoir", "venir", "prendre", "mettre", "croire", "trouver",
    "donner", "parler", "aimer", "passer", "rester", "penser", "regarder",
    "oui", "non", "salut", "bonjour", "bonsoir", "merci", "pardon", "désolé",
    "comment", "pourquoi", "quand", "où", "combien", "parce", "vraiment",
    "trop", "très", "bien", "mal", "super", "génial", "nul", "cool",
    "mec", "frère", "soeur", "pote", "gars", "meuf", "frr", "wsh",
    "mdr", "ptdr", "jpp", "tkt", "jsp", "nn", "oe", "bg", "tg",
    "osef", "oklm", "askip", "vrm", "dacc", "pk", "pcq", "pr",
    "c", "ct", "j", "t", "m", "l", "d", "n", "s", "y", "en",
    "alors", "après", "avant", "pendant", "toujours", "jamais", "souvent",
    "ici", "là", "partout", "rien", "tout", "chose", "personne",
    "aussi", "plus", "moins", "déjà", "encore", "maintenant", "demain", "hier",
    "jour", "nuit", "matin", "soir", "temps", "heure", "minute",
    "an", "année", "mois", "semaine", "argent", "travail", "maison", "vie",
    "gros", "petit", "grand", "beau", "bon", "mauvais", "vieux", "jeune",
    "nouveau", "noir", "blanc", "rouge", "bleu", "vert", "jaune",
    "manger", "boire", "dormir", "lire", "écrire", "écouter", "jouer",
    "travailler", "étudier", "apprendre", "comprendre", "oublier",
    "acheter", "vendre", "payer", "gagner", "perdre", "chercher",
    "ouvrir", "fermer", "entrer", "sortir", "monter", "descendre",
    "commencer", "finir", "arrêter", "continuer", "changer", "essayer",
    "mort", "amour", "haine", "joie", "tristesse", "peur", "colère",
    "france", "français", "paris", "langue", "mot", "phrase", "message",
}

_ENGLISH_WORDS = {
    "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "can", "may", "might", "must", "shall",
    "at", "to", "it", "and", "or", "in", "on", "for", "of", "as",
    "by", "from", "with", "this", "that", "these", "those",
    "he", "she", "we", "you", "they", "i", "me", "him", "her",
    "us", "them", "my", "your", "his", "its", "our", "their",
    "what", "which", "who", "when", "where", "why", "how",
    "all", "each", "every", "both", "few", "more", "some", "any",
    "most", "other", "another", "such", "no", "nor", "not", "only",
    "own", "same", "so", "than", "too", "very", "just",
    "about", "after", "again", "before", "between", "down",
    "during", "into", "like", "out", "over", "through", "up",
    "hello", "hi", "bye", "thanks", "thank", "please",
    "sorry", "excuse", "yes", "ok", "okay", "sure", "cool",
    "nice", "good", "bad", "great", "awesome", "terrible", "perfect",
    "love", "hate", "want", "need", "help", "get", "got", "make",
    "made", "take", "took", "come", "came", "go", "went",
    "see", "saw", "know", "knew", "think", "thought", "say", "said",
    "tell", "told", "ask", "asked", "give", "gave", "find", "found",
    "use", "used", "work", "worked", "call", "called", "try", "tried",
    "feel", "felt", "become", "became", "leave", "left", "put",
    "mean", "meant", "keep", "kept", "let", "begin", "began",
    "seem", "talk", "talked", "turn", "turned", "start", "started",
    "show", "showed", "hear", "heard", "play", "played", "run", "ran",
    "move", "moved", "live", "lived", "write", "wrote",
    "stop", "stopped", "read", "eat", "ate", "drink", "drank",
    "buy", "bought", "sell", "sold", "win", "won",
    "really", "actually", "basically", "literally",
    "maybe", "perhaps", "probably", "definitely", "absolutely",
    "thing", "stuff", "way", "lot", "bit", "kind", "sort",
    "guy", "dude", "man", "bro", "girl", "friend", "buddy",
    "lol", "lmao", "omg", "wtf", "btw", "imo", "tbh",
    "idk", "ikr", "np", "gg", "gl", "hf", "afk", "brb",
    "today", "tomorrow", "yesterday", "now", "later", "soon",
    "always", "never", "sometimes", "usually", "already", "still",
    "here", "there", "everywhere", "nowhere", "somewhere",
    "big", "small", "large", "little", "long", "short",
    "new", "old", "young", "high", "low", "fast", "slow",
    "hot", "cold", "warm", "dark", "light", "bright",
    "happy", "sad", "angry", "scared", "excited", "bored", "tired",
    "right", "wrong", "true", "false", "real", "fake", "easy", "hard",
    "free", "busy", "ready", "available", "gone", "dead", "alive",
    "first", "last", "next", "final", "second", "third",
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "time", "day", "night", "week", "month", "year", "hour", "minute",
    "people", "person", "woman", "child", "kid", "baby",
    "world", "life", "death", "war", "peace",
    "money", "water", "food", "car", "house", "home", "school", "work",
    "game", "movie", "music", "song", "video", "photo", "picture",
    "phone", "computer", "internet", "website", "email",
    "question", "answer", "problem", "solution", "idea", "story",
}

_FOREIGN_SCRIPTS = [
    (0x4E00, 0x9FFF), (0x3040, 0x309F), (0x30A0, 0x30FF),
    (0xAC00, 0xD7AF), (0x0400, 0x04FF), (0x0370, 0x03FF),
    (0x0600, 0x06FF), (0x0590, 0x05FF), (0x0E00, 0x0E7F), (0x0900, 0x097F),
]


def _is_french(text: str) -> bool:
    if not text or not text.strip():
        return True

    cleaned = re.sub(r"https?://\S+|www\.\S+", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"<a?:[a-zA-Z0-9_]+:\d+>", "", cleaned)
    cleaned = re.sub(r"<[@#]!?&?\d+>", "", cleaned)

    if not cleaned.strip():
        return True

    for ch in cleaned:
        cp = ord(ch)
        if any(lo <= cp <= hi for lo, hi in _FOREIGN_SCRIPTS):
            return False

    words = re.findall(r"[a-zàâäæçéèêëìîïñòôöœùûü]{2,}", cleaned.lower())
    if not words:
        return True

    if any(c in _FRENCH_ACCENTS for c in cleaned):
        return True

    fr = sum(1 for w in words if w in _FRENCH_WORDS)
    en = sum(1 for w in words if w in _ENGLISH_WORDS)

    if fr > 0:
        return True
    if en >= 3 and en / len(words) > 0.6:
        return False
    return True


class ToolsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── Listeners ─────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.author.bot or not message.content:
            return
        sniped[message.channel.id] = {
            "content": message.content,
            "author": message.author,
            "created_at": message.created_at,
            "attachments": [a.url for a in message.attachments],
        }

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before.author.bot or before.content == after.content:
            return
        edit_sniped[before.channel.id] = {
            "before": before.content or "*Message vide*",
            "after": after.content or "*Message vide*",
            "author": before.author,
            "edited_at": after.edited_at or discord.utils.utcnow(),
            "jump_url": after.jump_url,
        }

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.content:
            return
        if message.channel.id not in french_only_channels:
            return
        if message.content.startswith(("-", "/")):
            return
        if not _is_french(message.content):
            try:
                await message.delete()
                await message.channel.send(
                    f"{message.author.mention} — ce salon est **français uniquement**. 🇫🇷",
                    delete_after=6,
                )
            except (discord.Forbidden, discord.HTTPException):
                pass

    # ── SNIPE ─────────────────────────────────────────────────────────────────

    @commands.command(name="snipe")
    async def snipe(self, ctx: commands.Context):
        data = sniped.get(ctx.channel.id)
        if not data:
            return await ctx.send(embed=utils.err(
                "Aucun message supprimé récemment dans ce salon.",
                "🔍 Rien à snipe"
            ))
        embed = discord.Embed(
            description=data["content"] or "*Message vide*",
            color=discord.Color.dark_theme(),
            timestamp=data["created_at"],
        )
        embed.set_author(name=data["author"].display_name, icon_url=data["author"].display_avatar.url)
        if data["attachments"]:
            links = "\n".join(f"[Fichier {i+1}]({url})" for i, url in enumerate(data["attachments"]))
            embed.add_field(name="📎 Pièces jointes", value=links, inline=False)
        embed.set_footer(text="Supprimé le")
        await ctx.send(embed=embed)

    # ── ESNIPE ────────────────────────────────────────────────────────────────

    @commands.command(name="esnipe")
    async def esnipe(self, ctx: commands.Context):
        data = edit_sniped.get(ctx.channel.id)
        if not data:
            return await ctx.send(embed=utils.err(
                "Aucun message édité récemment dans ce salon.",
                "🔍 Rien à esnipe"
            ))
        embed = discord.Embed(
            color=discord.Color.dark_theme(),
            timestamp=data["edited_at"],
        )
        embed.set_author(name=data["author"].display_name, icon_url=data["author"].display_avatar.url)
        embed.add_field(name="Avant", value=data["before"][:1024], inline=False)
        embed.add_field(name="Après", value=data["after"][:1024], inline=False)
        embed.add_field(name="Message", value=f"[Aller au message]({data['jump_url']})", inline=False)
        embed.set_footer(text="Édité le")
        await ctx.send(embed=embed)

    # ── CLEAR ─────────────────────────────────────────────────────────────────

    @commands.command(name="clear", aliases=["purge"])
    @commands.guild_only()
    async def clear(self, ctx: commands.Context, amount: int = 10):
        await ctx.message.delete()
        if not 1 <= amount <= 100:
            return await ctx.send(embed=utils.err(
                "Le nombre de messages à supprimer doit être entre **1** et **100**.",
                "❌ Nombre invalide"
            ))
        try:
            deleted = await ctx.channel.purge(limit=amount)
            msg = await ctx.send(embed=discord.Embed(
                title="🗑️ Nettoyage effectué",
                description=f"**{len(deleted)}** message(s) supprimé(s).",
                color=discord.Color.green(),
            ).set_footer(text=f"Par {ctx.author.display_name}"))
            await asyncio.sleep(4)
            await msg.delete()
        except discord.Forbidden:
            await ctx.send(embed=utils.err("Je n'ai pas la permission de supprimer des messages ici."))

    @clear.error
    async def clear_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(embed=utils.err("Il te faut la permission **Gérer les messages**."))
        elif isinstance(error, commands.BadArgument):
            await ctx.send(embed=utils.err(f"Usage : `{ctx.prefix}clear [1-100]`", "❌ Argument invalide"))

    # ── LOCK ──────────────────────────────────────────────────────────────────

    @commands.command(name="lock")
    @commands.guild_only()
    async def lock(self, ctx: commands.Context, *, reason: str = None):
        await ctx.message.delete()
        channel = ctx.channel
        backup = {}
        everyone = ctx.guild.default_role

        try:
            ow = channel.overwrites_for(everyone)
            backup[everyone.id] = ow.send_messages
            ow.send_messages = False
            await channel.set_permissions(everyone, overwrite=ow)

            for role, overwrite in channel.overwrites.items():
                if not isinstance(role, discord.Role):
                    continue
                if role.is_default() or role.permissions.administrator:
                    continue
                backup[role.id] = overwrite.send_messages
                overwrite.send_messages = False
                await channel.set_permissions(role, overwrite=overwrite)

            lock_backup[channel.id] = backup

            embed = discord.Embed(
                title="🔒 Salon verrouillé",
                description=f"{channel.mention} est maintenant fermé. Seuls les admins peuvent écrire.",
                color=discord.Color.red(),
            )
            if reason:
                embed.add_field(name="Raison", value=reason, inline=False)
            embed.set_footer(text=f"Par {ctx.author.display_name}")
            await ctx.send(embed=embed)

        except discord.Forbidden:
            await ctx.send(embed=utils.err("Je n'ai pas la permission de modifier les permissions de ce salon."))

    @lock.error
    async def lock_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(embed=utils.err("Il te faut la permission **Gérer les salons**."))

    # ── UNLOCK ────────────────────────────────────────────────────────────────

    @commands.command(name="unlock")
    @commands.guild_only()
    async def unlock(self, ctx: commands.Context, *, reason: str = None):
        await ctx.message.delete()
        channel = ctx.channel

        if channel.id not in lock_backup:
            return await ctx.send(embed=utils.err(
                "Ce salon n'a pas été verrouillé via le bot, ou le bot a redémarré entre temps.",
                "⚠️ Pas de sauvegarde"
            ))

        backup = lock_backup.pop(channel.id)
        try:
            for role_id, send_val in backup.items():
                role = ctx.guild.get_role(role_id)
                if not role:
                    continue
                ow = channel.overwrites_for(role)
                ow.send_messages = send_val
                await channel.set_permissions(role, overwrite=ow)

            embed = discord.Embed(
                title="🔓 Salon déverrouillé",
                description=f"{channel.mention} est à nouveau ouvert. Les permissions précédentes sont restaurées.",
                color=discord.Color.green(),
            )
            if reason:
                embed.add_field(name="Raison", value=reason, inline=False)
            embed.set_footer(text=f"Par {ctx.author.display_name}")
            await ctx.send(embed=embed)

        except discord.Forbidden:
            await ctx.send(embed=utils.err("Je n'ai pas la permission de modifier les permissions de ce salon."))

    @unlock.error
    async def unlock_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(embed=utils.err("Il te faut la permission **Gérer les salons**."))

    # ── SLOWMODE ──────────────────────────────────────────────────────────────

    @commands.command(name="slowmode", aliases=["sm"])
    @commands.guild_only()
    async def slowmode(self, ctx: commands.Context, seconds: int = 0):
        await ctx.message.delete()
        if not 0 <= seconds <= 21600:
            return await ctx.send(embed=utils.err(
                "La valeur doit être entre **0** (désactivé) et **21600** secondes (6h max).",
                "❌ Valeur invalide"
            ))
        try:
            await ctx.channel.edit(slowmode_delay=seconds)
            if seconds == 0:
                embed = discord.Embed(
                    title="✅ Slowmode désactivé",
                    description=f"Les membres peuvent à nouveau envoyer des messages librement dans {ctx.channel.mention}.",
                    color=discord.Color.green(),
                )
            else:
                m, s = divmod(seconds, 60)
                dur = f"{m}m {s}s" if s else f"{m}m" if m else f"{s}s"
                embed = discord.Embed(
                    title="⏱️ Slowmode activé",
                    description=f"{ctx.channel.mention} est maintenant en slowmode — 1 message toutes les **{dur}**.",
                    color=discord.Color.orange(),
                )
            embed.set_footer(text=f"Par {ctx.author.display_name}")
            await ctx.send(embed=embed)
        except discord.Forbidden:
            await ctx.send(embed=utils.err("Je n'ai pas la permission de modifier ce salon."))

    @slowmode.error
    async def slowmode_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(embed=utils.err("Il te faut la permission **Gérer les salons**."))
        elif isinstance(error, commands.BadArgument):
            await ctx.send(embed=utils.err(f"Usage : `{ctx.prefix}slowmode [secondes]` — ex: `{ctx.prefix}slowmode 10`", "❌ Argument invalide"))

    # ── FRENCH ONLY ───────────────────────────────────────────────────────────

    @commands.command(name="fron")
    @commands.guild_only()
    async def fron(self, ctx: commands.Context):
        await ctx.message.delete()
        if ctx.channel.id in french_only_channels:
            return await ctx.send(embed=utils.info(
                f"Le mode **French Only** est déjà actif sur {ctx.channel.mention}.",
                "ℹ️ Déjà activé"
            ))
        french_only_channels.add(ctx.channel.id)
        _save_tools_settings()
        await ctx.send(embed=discord.Embed(
            title="🇫🇷 French Only — Activé",
            description=f"Les messages non-français seront supprimés dans {ctx.channel.mention}.",
            color=discord.Color.green(),
        ).set_footer(text=f"Par {ctx.author.display_name}"))

    @commands.command(name="froff")
    @commands.guild_only()
    async def froff(self, ctx: commands.Context):
        await ctx.message.delete()
        if ctx.channel.id not in french_only_channels:
            return await ctx.send(embed=utils.info(
                f"Le mode **French Only** n'est pas actif sur {ctx.channel.mention}.",
                "ℹ️ Déjà désactivé"
            ))
        french_only_channels.discard(ctx.channel.id)
        _save_tools_settings()
        await ctx.send(embed=discord.Embed(
            title="🇫🇷 French Only — Désactivé",
            description=f"{ctx.channel.mention} accepte à nouveau toutes les langues.",
            color=discord.Color.orange(),
        ).set_footer(text=f"Par {ctx.author.display_name}"))

    # ── USERINFO ──────────────────────────────────────────────────────────────

    @commands.command(name="userinfo", aliases=["ui", "whois"])
    @commands.guild_only()
    async def userinfo(self, ctx: commands.Context, *, query: str = None):
        member = await utils.find_member(ctx, query) if query else ctx.author
        if not member:
            return await ctx.send(embed=utils.err(f"Membre `{query}` introuvable.", "❌ Introuvable"))

        roles = [r.mention for r in reversed(member.roles) if not r.is_default()]
        joined_ts = f"<t:{int(member.joined_at.timestamp())}:R>" if member.joined_at else "?"
        created_ts = f"<t:{int(member.created_at.timestamp())}:R>"

        color = member.color if member.color.value else discord.Color.blurple()
        embed = discord.Embed(color=color)
        embed.set_author(name=str(member), icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Pseudo", value=member.display_name, inline=True)
        embed.add_field(name="ID", value=f"`{member.id}`", inline=True)
        embed.add_field(name="Bot", value="Oui 🤖" if member.bot else "Non", inline=True)
        embed.add_field(name="A rejoint le serveur", value=joined_ts, inline=True)
        embed.add_field(name="Compte créé", value=created_ts, inline=True)

        if member.is_timed_out():
            until = member.communication_disabled_until
            embed.add_field(name="⏱️ Timeout jusqu'à", value=f"<t:{int(until.timestamp())}:R>", inline=True)

        warns_count = len(utils.get_warns(ctx.guild.id, member.id))
        if warns_count:
            embed.add_field(name="⚠️ Warns", value=str(warns_count), inline=True)

        if roles:
            display = " ".join(roles[:15])
            if len(roles) > 15:
                display += f" *+{len(roles) - 15} autres*"
            embed.add_field(name=f"Rôles ({len(roles)})", value=display, inline=False)

        embed.set_footer(text=f"Demandé par {ctx.author.display_name}")
        await ctx.send(embed=embed)

    # ── SERVERINFO ────────────────────────────────────────────────────────────

    @commands.command(name="serverinfo", aliases=["si", "serveur"])
    @commands.guild_only()
    async def serverinfo(self, ctx: commands.Context):
        g = ctx.guild
        humans = sum(1 for m in g.members if not m.bot)
        bots = g.member_count - humans
        created_ts = f"<t:{int(g.created_at.timestamp())}:R>"

        embed = discord.Embed(title=g.name, color=discord.Color.blurple())
        if g.icon:
            embed.set_thumbnail(url=g.icon.url)
        if g.banner:
            embed.set_image(url=g.banner.url)

        embed.add_field(name="Propriétaire", value=g.owner.mention if g.owner else "?", inline=True)
        embed.add_field(name="Créé", value=created_ts, inline=True)
        embed.add_field(name="ID", value=f"`{g.id}`", inline=True)
        embed.add_field(name="Membres", value=f"👤 {humans} humains · 🤖 {bots} bots", inline=True)
        embed.add_field(name="Salons", value=f"💬 {len(g.text_channels)} texte · 🔊 {len(g.voice_channels)} vocal", inline=True)
        embed.add_field(name="Rôles", value=str(len(g.roles) - 1), inline=True)
        embed.add_field(
            name="Boosts",
            value=f"{g.premium_subscription_count} boost(s) — Niveau {g.premium_tier}",
            inline=True,
        )
        embed.add_field(name="Emojis", value=str(len(g.emojis)), inline=True)
        embed.set_footer(text=f"Demandé par {ctx.author.display_name}")
        await ctx.send(embed=embed)

    # ── AVATAR ────────────────────────────────────────────────────────────────

    @commands.command(name="avatar", aliases=["av", "pfp"])
    @commands.guild_only()
    async def avatar(self, ctx: commands.Context, *, query: str = None):
        member = await utils.find_member(ctx, query) if query else ctx.author
        if not member:
            return await ctx.send(embed=utils.err(f"Membre `{query}` introuvable.", "❌ Introuvable"))

        embed = discord.Embed(
            title=f"Avatar de {member.display_name}",
            color=discord.Color.blurple(),
        )
        embed.set_image(url=member.display_avatar.url)

        formats = ["png", "jpg", "webp"]
        if member.display_avatar.is_animated():
            formats.append("gif")
        links = " · ".join(
            f"[{fmt.upper()}]({member.display_avatar.replace(format=fmt, size=1024).url})"
            for fmt in formats
        )
        embed.set_footer(text=links)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(ToolsCog(bot))