
import discord
from discord.ext import commands

import utils

# Chaque entrée : (nom, usage, description, restriction)
# restriction :
#   None                          -> commande publique
#   "owner"                       -> uniquement Fabrice
#   ("perm", "kick_members")      -> nécessite cette permission Discord

CATEGORIES: dict[str, dict] = {
    "🛡️ Modération": {
        "desc": "Sanctions et gestion des membres (admins)",
        "cmds": [
            ("kick", "<membre> [raison]", "Expulse un membre du serveur", ("perm", "kick_members")),
            ("ban", "<membre> [raison]", "Bannit un membre définitivement", ("perm", "ban_members")),
            ("unban", "<ID ou pseudo>", "Débannit un utilisateur", ("perm", "ban_members")),
            ("mute", "<membre> <durée> [raison]", "Timeout un membre (ex: 10m, 2h, 1d)", ("perm", "moderate_members")),
            ("unmute", "<membre>", "Lève le timeout d'un membre", ("perm", "moderate_members")),
            ("warn", "<membre> [raison]", "Avertit un membre — il reçoit le warn en DM", ("perm", "manage_messages")),
            ("warns", "<membre>", "Affiche tous les warns d'un membre", ("perm", "manage_messages")),
            ("warnlist", "", "Liste tous les membres ayant au moins un warn", ("perm", "manage_messages")),
            ("delwarn", "<membre> <n°>", "Supprime un warn précis", ("perm", "manage_messages")),
            ("clearwarns", "<membre>", "Efface tous les warns d'un membre", ("perm", "manage_messages")),
        ],
    },
    "🔧 Outils de salon": {
        "desc": "Gestion des salons (admins)",
        "cmds": [
            ("clear", "[1-100]", "Supprime des messages (défaut : 10)", ("perm", "manage_messages")),
            ("lock", "[raison]", "Verrouille le salon en écriture", ("perm", "manage_channels")),
            ("unlock", "[raison]", "Déverrouille le salon", ("perm", "manage_channels")),
            ("slowmode", "[secondes]", "Active le slowmode (0 pour désactiver)", ("perm", "manage_channels")),
            ("fron", "", "Active le mode French Only sur ce salon", ("perm", "manage_channels")),
            ("froff", "", "Désactive le mode French Only", ("perm", "manage_channels")),
        ],
    },
    "🔍 Infos & Divers": {
        "desc": "Commandes publiques, accessibles à tous",
        "cmds": [
            ("ping", "", "Latence du bot", None),
            ("snipe", "", "Affiche le dernier message supprimé du salon", None),
            ("esnipe", "", "Affiche le dernier message édité du salon (avant/après)", None),
            ("userinfo", "[membre]", "Infos complètes sur un membre", None),
            ("serverinfo", "", "Infos sur le serveur", None),
            ("avatar", "[membre]", "Affiche l'avatar d'un membre en grand", None),
        ],
    },
    "🎟️ Recrutement & Serveur": {
        "desc": "Candidatures et animations (admins)",
        "cmds": [
            ("panel", "", "Poste le panel de candidature aux divisions", ("perm", "administrator")),
            ("accepter", "", "Accepte le candidat du ticket courant (capitaine)", None),
            ("giveaway", "", "Lance un giveaway", ("perm", "manage_guild")),
            ("gwcancel", "", "Annule un giveaway en cours", ("perm", "manage_guild")),
            ("gwend", "", "Termine un giveaway immédiatement", ("perm", "manage_guild")),
            ("gwreroll", "", "Retire un nouveau gagnant", ("perm", "manage_guild")),
            ("gwforcewin", "<membre>", "Force un gagnant précis", ("perm", "manage_guild")),
        ],
    },
    "👑 Réservé Owner": {
        "desc": "Commandes strictement personnelles — inaccessibles à tout autre membre",
        "cmds": [
            ("sfixeon", "<membre> [pseudo]", "Fixe le pseudo d'un membre", "owner"),
            ("sunfixe", "<membre>", "Défixe le pseudo d'un membre", "owner"),
            ("sfixelist", "", "Liste les pseudos fixés du serveur", "owner"),
            ("banvoc", "<membre> [durée] [salon]", "Bannit un membre des salons vocaux", "owner"),
            ("unbanvoc", "<membre>", "Lève le ban vocal d'un membre", "owner"),
            ("pv", "[@membres...]", "Protège le salon vocal courant", "owner"),
            ("pvw", "<@membres...>", "Ajoute des membres à la whitelist du salon protégé", "owner"),
            ("pvb", "<@membres...>", "Ajoute des membres à la blacklist du salon protégé", "owner"),
            ("unpv", "", "Retire la protection du salon vocal courant", "owner"),
            ("ownerban", "<membre> [durée] <raison>", "Sanction renforcée (annule les actions du membre)", "owner"),
            ("ownerunban", "<membre>", "Lève un ownerban", "owner"),
            ("ownerbanlist", "", "Liste les membres ownerbannis", "owner"),
        ],
    },
}


def _member_can_see(member: discord.Member, restriction) -> bool:
    if restriction is None:
        return True
    if restriction == "owner":
        return member.id == utils.OWNER_ID
    if isinstance(restriction, tuple) and restriction[0] == "perm":
        return getattr(member.guild_permissions, restriction[1], False) or member.guild_permissions.administrator
    return False


def _visible_categories(member: discord.Member) -> dict:
    visible = {}
    for cat_name, cat_data in CATEGORIES.items():
        cmds = [c for c in cat_data["cmds"] if _member_can_see(member, c[3])]
        if cmds:
            visible[cat_name] = {"desc": cat_data["desc"], "cmds": cmds}
    return visible


class CategorySelect(discord.ui.Select):
    def __init__(self, prefix: str, categories: dict):
        self.prefix = prefix
        self.categories = categories
        options = [
            discord.SelectOption(
                label=name.split(" ", 1)[1],
                emoji=name.split(" ")[0],
                description=data["desc"][:100],
            )
            for name, data in categories.items()
        ]
        super().__init__(placeholder="Choisis une catégorie...", options=options)

    async def callback(self, interaction: discord.Interaction):
        label = self.values[0]
        cat_name = next(k for k in self.categories if label in k)
        await interaction.response.edit_message(embed=_category_embed(cat_name, self.categories, self.prefix))


class HelpView(discord.ui.View):
    def __init__(self, prefix: str, categories: dict):
        super().__init__(timeout=120)
        self.categories = categories
        if categories:
            self.add_item(CategorySelect(prefix, categories))

    @discord.ui.button(label="Vue d'ensemble", style=discord.ButtonStyle.secondary, emoji="🏠", row=1)
    async def home_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            embed=_overview_embed(interaction.client.command_prefix, self.categories)
        )


def _overview_embed(prefix: str, categories: dict) -> discord.Embed:
    embed = discord.Embed(
        title="📖 Aide restreinte (d!rh)",
        description=f"Préfixe : `{prefix}` — Sélectionne une catégorie dans le menu pour voir les détails.",
        color=discord.Color.blurple(),
    )
    total = sum(len(v["cmds"]) for v in categories.values())
    if total == 0:
        embed.description = "Tu n'as accès à aucune commande pour le moment."
        return embed
    for cat_name, cat_data in categories.items():
        cmd_list = " ".join(f"`{c[0]}`" for c in cat_data["cmds"])
        embed.add_field(name=cat_name, value=f"{cat_data['desc']}\n{cmd_list}", inline=False)
    embed.set_footer(text=f"{total} commande(s) disponible(s) pour toi • < > = obligatoire · [ ] = optionnel")
    return embed


def _category_embed(cat_name: str, categories: dict, prefix: str) -> discord.Embed:
    cat = categories[cat_name]
    embed = discord.Embed(title=cat_name, description=cat["desc"], color=discord.Color.blurple())
    for name, args, desc, _restriction in cat["cmds"]:
        usage = f"`{prefix}{name}{' ' + args if args else ''}`"
        embed.add_field(name=usage, value=desc, inline=False)
    embed.set_footer(text="< > = obligatoire · [ ] = optionnel")
    return embed


class HelpCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="rh", aliases=["aide", "h"])
    @commands.guild_only()
    async def rh(self, ctx: commands.Context, *, category: str = None):
        prefix = ctx.prefix
        categories = _visible_categories(ctx.author)

        if category:
            ql = category.lower()
            match = next(
                (k for k in categories if ql in k.lower() or ql in categories[k]["desc"].lower()),
                None,
            )
            if not match:
                match = next(
                    (k for k in categories if any(ql == cmd[0].lower() for cmd in categories[k]["cmds"])),
                    None,
                )
            if match:
                return await ctx.send(embed=_category_embed(match, categories, prefix), view=HelpView(prefix, categories))

        await ctx.send(embed=_overview_embed(prefix, categories), view=HelpView(prefix, categories))


async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))
