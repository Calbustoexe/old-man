"""
Cog central de gestion des permissions par commande (Urahara).

Concept :
- Le propriétaire du bot (utils.OWNER_ID) a toujours accès à absolument tout.
- Toutes les commandes préfixées "hors RP" (c'est-à-dire hors des commandes
  slash, hors d!accepter, et hors des commandes de gestion staff du RP comme
  skick/sban/sreset/sunban/smute/sunmute/panel) sont désormais restreintes.
- Un membre ne peut utiliser une commande restreinte que si :
    1) il est owner, OU
    2) il a la "full access" (d!bwl), OU
    3) la commande précise lui a été donnée via d!give (directement ou via
       une catégorie avec d!cgive).
- Si un membre sans la permission tente d'utiliser une commande restreinte,
  aucune réponse n'est envoyée (comportement silencieux, comme demandé).

Commandes de gestion (toutes réservées au propriétaire) :
    d!give   <commande> <membre>       — donne l'accès à une commande précise
    d!ungive <commande> <membre>       — retire l'accès à une commande précise
    d!bwl    <membre>                  — donne l'accès total (full access)
    d!unbwl  <membre>                  — retire l'accès total
    d!bwllist                          — liste les membres en full access

    d!ccreate  <nom>                   — crée une catégorie de commandes vide
    d!cdelete  <nom>                   — supprime une catégorie
    d!crename  <ancien nom> | <nouveau nom> — renomme une catégorie
    d!cadd     <nom> <commande>        — ajoute une commande à une catégorie
    d!cremove  <nom> <commande>        — retire une commande d'une catégorie
    d!cgive    <nom> <membre>          — donne toutes les commandes d'une catégorie à un membre
    d!clist                            — liste les catégories existantes
    d!cpanel                           — ouvre le panel de gestion complet des catégories
"""
from __future__ import annotations

import discord
from discord.ext import commands

import utils

OWNER_ID = utils.OWNER_ID

# ─────────────────────────────────────────────────────────────────────────────
# Commandes exclues du système de permissions (RP pur, ou gérées différemment)
# ─────────────────────────────────────────────────────────────────────────────
# - Commandes de gestion staff du RP (division capitaine) : jamais touchées.
# - d!accepter / d!panel : candidatures RP, gérées par la hiérarchie du RP.
# - d!rh / d!aide / d!h : l'aide elle-même reste accessible à tous (elle
#   s'adapte simplement au contenu visible par chacun).
# - d!gwforcewin / d!gwfw : commande invisible strictement owner-only, gérée
#   à part (jamais accessible via give/bwl, jamais listée dans rh).
RP_STAFF_COMMANDS = {"skick", "sban", "sreset", "sunban", "smute", "sunmute", "accepter", "panel"}
HELP_COMMANDS = {"rh", "aide", "h"}
OWNER_LOCKED_COMMANDS = {"gwforcewin", "gwfw"}

# Commandes publiques : accessibles à tout le monde, jamais restreintes.
PUBLIC_COMMANDS = {"ping", "snipe", "esnipe", "userinfo", "ui", "whois", "serverinfo", "si", "serveur", "avatar", "av", "pfp"}

# Commandes de gestion des permissions elles-mêmes : toujours owner-only,
# jamais données via give/bwl (sécurité : on ne peut pas se donner le pouvoir
# de donner des permissions).
PERMISSION_MANAGEMENT_COMMANDS = {
    "give", "ungive", "bwl", "unbwl", "bwllist",
    "ccreate", "cdelete", "crename", "cadd", "cremove", "cgive", "clist", "cpanel",
}


def is_restricted_command(command_name: str) -> bool:
    """Vrai si cette commande doit passer par le système de permissions."""
    name = command_name.lower()
    if name in RP_STAFF_COMMANDS or name in HELP_COMMANDS or name in OWNER_LOCKED_COMMANDS:
        return False
    if name in PUBLIC_COMMANDS:
        return False
    if name in PERMISSION_MANAGEMENT_COMMANDS:
        return False
    return True


def can_use_command(guild_id: int, user_id: int, command_name: str) -> bool:
    """Vérifie si un membre peut utiliser une commande restreinte donnée."""
    if user_id == OWNER_ID:
        return True
    if not is_restricted_command(command_name):
        return True
    if utils.is_full_access(guild_id, user_id):
        return True
    grants = utils.get_user_grants(guild_id, user_id)
    return command_name.lower() in grants


# ─────────────────────────────────────────────────────────────────────────────
# Panel de gestion des catégories (UI complète)
# ─────────────────────────────────────────────────────────────────────────────

class CategoryNameModal(discord.ui.Modal, title="📁 Nouvelle catégorie"):
    nom = discord.ui.TextInput(label="Nom de la catégorie", max_length=50, required=True)

    def __init__(self, panel: "CategoryPanelView"):
        super().__init__()
        self.panel = panel

    async def on_submit(self, interaction: discord.Interaction):
        name = self.nom.value.strip()
        if not name:
            await interaction.response.send_message("❌ Le nom ne peut pas être vide.", ephemeral=True)
            return
        if not utils.create_category(interaction.guild_id, name):
            await interaction.response.send_message(f"❌ La catégorie `{name}` existe déjà.", ephemeral=True)
            return
        await self.panel.refresh(interaction, selected=name)


class RenameCategoryModal(discord.ui.Modal, title="✏️ Renommer la catégorie"):
    nom = discord.ui.TextInput(label="Nouveau nom", max_length=50, required=True)

    def __init__(self, panel: "CategoryPanelView", old_name: str):
        super().__init__()
        self.panel = panel
        self.old_name = old_name

    async def on_submit(self, interaction: discord.Interaction):
        new_name = self.nom.value.strip()
        if not new_name:
            await interaction.response.send_message("❌ Le nom ne peut pas être vide.", ephemeral=True)
            return
        if not utils.rename_category(interaction.guild_id, self.old_name, new_name):
            await interaction.response.send_message("❌ Renommage impossible (nom déjà pris ou catégorie introuvable).", ephemeral=True)
            return
        await self.panel.refresh(interaction, selected=new_name)


class AddCommandModal(discord.ui.Modal, title="➕ Ajouter une commande"):
    cmd = discord.ui.TextInput(label="Nom exact de la commande", placeholder="ex: kick", max_length=50, required=True)

    def __init__(self, panel: "CategoryPanelView", category_name: str):
        super().__init__()
        self.panel = panel
        self.category_name = category_name

    async def on_submit(self, interaction: discord.Interaction):
        cmd_name = self.cmd.value.strip().lower()
        bot: commands.Bot = interaction.client
        if bot.get_command(cmd_name) is None:
            await interaction.response.send_message(f"❌ Commande `{cmd_name}` introuvable sur le bot.", ephemeral=True)
            return
        added = utils.add_command_to_category(interaction.guild_id, self.category_name, cmd_name)
        if not added:
            await interaction.response.send_message(f"❌ `{cmd_name}` est déjà dans cette catégorie.", ephemeral=True)
            return
        await self.panel.refresh(interaction, selected=self.category_name)


class GiveCategoryModal(discord.ui.Modal, title="🎁 Donner la catégorie"):
    membre = discord.ui.TextInput(label="Mention / pseudo / ID du membre", max_length=100, required=True)

    def __init__(self, panel: "CategoryPanelView", category_name: str):
        super().__init__()
        self.panel = panel
        self.category_name = category_name

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        query = self.membre.value.strip()
        member = await _resolve_member(guild, query)
        if member is None:
            await interaction.response.send_message(f"❌ Membre `{query}` introuvable.", ephemeral=True)
            return
        added = utils.grant_category(guild.id, member.id, self.category_name)
        if not added:
            await interaction.response.send_message(
                f"ℹ️ {member.mention} avait déjà toutes les commandes de cette catégorie (ou celle-ci est vide).",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"✅ {member.mention} a reçu **{len(added)}** commande(s) de la catégorie `{self.category_name}`.",
            ephemeral=True,
        )


class CategorySelectMenu(discord.ui.Select):
    def __init__(self, panel: "CategoryPanelView", categories: list, selected: str | None):
        self.panel = panel
        options = [
            discord.SelectOption(label=name[:100], value=name, default=(name == selected))
            for name in categories
        ] or [discord.SelectOption(label="Aucune catégorie", value="__none__")]
        super().__init__(placeholder="Choisis une catégorie...", options=options[:25])

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        if value == "__none__":
            await interaction.response.defer()
            return
        await self.panel.refresh(interaction, selected=value)


class CommandRemoveSelect(discord.ui.Select):
    def __init__(self, panel: "CategoryPanelView", category_name: str, cmds: list):
        self.panel = panel
        self.category_name = category_name
        options = [discord.SelectOption(label=c[:100], value=c) for c in cmds] or [
            discord.SelectOption(label="Aucune commande", value="__none__")
        ]
        super().__init__(placeholder="Retirer une commande...", options=options[:25], row=2)

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        if value == "__none__":
            await interaction.response.defer()
            return
        utils.remove_command_from_category(interaction.guild_id, self.category_name, value)
        await self.panel.refresh(interaction, selected=self.category_name)


class CategoryPanelView(discord.ui.View):
    def __init__(self, owner_id: int, selected: str | None = None):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.selected = selected

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Ce panel ne t'appartient pas.", ephemeral=True)
            return False
        return True

    def build(self, guild_id: int):
        self.clear_items()
        categories = utils.get_categories(guild_id)
        names = sorted(categories.keys())
        self.add_item(CategorySelectMenu(self, names, self.selected))

        create_btn = discord.ui.Button(label="Créer", emoji="🆕", style=discord.ButtonStyle.success, row=1)
        create_btn.callback = self.on_create
        self.add_item(create_btn)

        if self.selected and self.selected in categories:
            rename_btn = discord.ui.Button(label="Renommer", emoji="✏️", style=discord.ButtonStyle.secondary, row=1)
            rename_btn.callback = self.on_rename
            self.add_item(rename_btn)

            add_btn = discord.ui.Button(label="Ajouter commande", emoji="➕", style=discord.ButtonStyle.primary, row=1)
            add_btn.callback = self.on_add_cmd
            self.add_item(add_btn)

            give_btn = discord.ui.Button(label="Donner à un membre", emoji="🎁", style=discord.ButtonStyle.success, row=1)
            give_btn.callback = self.on_give
            self.add_item(give_btn)

            delete_btn = discord.ui.Button(label="Supprimer", emoji="🗑️", style=discord.ButtonStyle.danger, row=1)
            delete_btn.callback = self.on_delete
            self.add_item(delete_btn)

            self.add_item(CommandRemoveSelect(self, self.selected, categories[self.selected]))

        return self

    def build_embed(self, guild_id: int) -> discord.Embed:
        categories = utils.get_categories(guild_id)
        embed = discord.Embed(
            title="📁 Gestion des catégories de commandes",
            color=discord.Color.blurple(),
        )
        if not categories:
            embed.description = "Aucune catégorie pour le moment. Clique sur **Créer** pour commencer."
            return embed

        if self.selected and self.selected in categories:
            cmds = categories[self.selected]
            embed.description = f"Catégorie sélectionnée : **{self.selected}**"
            embed.add_field(
                name=f"Commandes ({len(cmds)})",
                value="\n".join(f"• `{c}`" for c in cmds) if cmds else "*Aucune commande.*",
                inline=False,
            )
        else:
            embed.description = "Sélectionne une catégorie dans le menu, ou crée-en une nouvelle."

        overview = "\n".join(f"• **{name}** — {len(cmds)} commande(s)" for name, cmds in sorted(categories.items()))
        embed.add_field(name="Toutes les catégories", value=overview, inline=False)
        return embed

    async def refresh(self, interaction: discord.Interaction, selected: str | None = None):
        if selected is not None:
            self.selected = selected
        self.build(interaction.guild_id)
        embed = self.build_embed(interaction.guild_id)
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=self)
        else:
            await interaction.response.edit_message(embed=embed, view=self)

    async def on_create(self, interaction: discord.Interaction):
        await interaction.response.send_modal(CategoryNameModal(self))

    async def on_rename(self, interaction: discord.Interaction):
        await interaction.response.send_modal(RenameCategoryModal(self, self.selected))

    async def on_add_cmd(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AddCommandModal(self, self.selected))

    async def on_give(self, interaction: discord.Interaction):
        await interaction.response.send_modal(GiveCategoryModal(self, self.selected))

    async def on_delete(self, interaction: discord.Interaction):
        utils.delete_category(interaction.guild_id, self.selected)
        self.selected = None
        await self.refresh(interaction)


# ─────────────────────────────────────────────────────────────────────────────
# Aide restreinte dynamique (d!rh) — construite à partir des commandes
# réellement enregistrées sur le bot, filtrées selon ce que peut faire le membre.
# ─────────────────────────────────────────────────────────────────────────────

_CATEGORY_ICONS = {
    "Moderation": "🛡️ Modération",
    "Tools": "🔧 Outils",
    "Fun": "🎉 Fun",
    "Giveaway": "🎁 Giveaway",
    "NickFixCog": "📌 Pseudos",
    "VocBanCog": "🔇 Vocal",
    "VocProtectCog": "🔒 Protection vocale",
    "OwnerBanCog": "👑 Ownerban",
    "HelpCog": "📖 Aide",
}


def _visible_commands(bot: commands.Bot, member: discord.Member) -> dict[str, list]:
    """Retourne {nom_de_cog: [commandes visibles]} pour ce membre précis."""
    guild_id = member.guild.id
    result: dict[str, list] = {}
    for cmd in bot.commands:
        if cmd.hidden:
            continue
        name = cmd.name.lower()
        if name in RP_STAFF_COMMANDS or name in OWNER_LOCKED_COMMANDS or name in PERMISSION_MANAGEMENT_COMMANDS:
            continue
        if name in HELP_COMMANDS:
            continue
        if not can_use_command(guild_id, member.id, name):
            continue
        cog_name = cmd.cog_name or "Autres"
        result.setdefault(cog_name, []).append(cmd)
    for cog_name in result:
        result[cog_name].sort(key=lambda c: c.name)
    return result


def _overview_embed(prefix: str, visible: dict, member: discord.Member) -> discord.Embed:
    embed = discord.Embed(
        title="📖 Aide (d!rh)",
        description=f"Préfixe : `{prefix}`",
        color=discord.Color.blurple(),
    )
    total = sum(len(cmds) for cmds in visible.values())
    if member.id == OWNER_ID:
        embed.description += "\n👑 Tu es le propriétaire du bot : accès total à toutes les commandes."
    if total == 0:
        embed.description += "\n\nTu n'as accès à aucune commande pour le moment."
        return embed
    for cog_name, cmds in sorted(visible.items()):
        label = _CATEGORY_ICONS.get(cog_name, f"📂 {cog_name}")
        cmd_list = " ".join(f"`{c.name}`" for c in cmds)
        embed.add_field(name=label, value=cmd_list, inline=False)
    embed.set_footer(text=f"{total} commande(s) disponible(s) pour toi")
    return embed


def _category_embed(cog_name: str, cmds: list, prefix: str) -> discord.Embed:
    label = _CATEGORY_ICONS.get(cog_name, f"📂 {cog_name}")
    embed = discord.Embed(title=label, color=discord.Color.blurple())
    for cmd in cmds:
        usage = f"`{prefix}{cmd.name}{' ' + cmd.signature if cmd.signature else ''}`"
        embed.add_field(name=usage, value=cmd.help or cmd.short_doc or "Aucune description.", inline=False)
    embed.set_footer(text="< > = obligatoire · [ ] = optionnel")
    return embed


class HelpCategorySelect(discord.ui.Select):
    def __init__(self, prefix: str, visible: dict):
        self.prefix = prefix
        self.visible = visible
        options = [
            discord.SelectOption(label=name[:100], description=f"{len(cmds)} commande(s)"[:100])
            for name, cmds in sorted(visible.items())
        ] or [discord.SelectOption(label="Aucune commande", value="__none__")]
        super().__init__(placeholder="Choisis une catégorie...", options=options[:25])

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        if value == "__none__":
            await interaction.response.defer()
            return
        await interaction.response.edit_message(embed=_category_embed(value, self.visible[value], self.prefix))


class HelpView(discord.ui.View):
    def __init__(self, prefix: str, visible: dict):
        super().__init__(timeout=120)
        self.visible = visible
        if visible:
            self.add_item(HelpCategorySelect(prefix, visible))

    @discord.ui.button(label="Vue d'ensemble", style=discord.ButtonStyle.secondary, emoji="🏠", row=1)
    async def home_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            embed=_overview_embed(interaction.client.command_prefix, self.visible, interaction.user)
        )


async def _resolve_member(guild: discord.Guild, query: str) -> discord.Member | None:
    """Résolution de membre indépendante de ctx, pour les modals du panel."""
    import re
    if not query or guild is None:
        return None
    query = query.strip()
    m = re.match(r"^<@!?(\d+)>$", query)
    if m:
        return guild.get_member(int(m.group(1)))
    if query.isdigit():
        member = guild.get_member(int(query))
        if member:
            return member
        try:
            return await guild.fetch_member(int(query))
        except (discord.NotFound, discord.HTTPException):
            return None
    ql = query.lower()
    for member in guild.members:
        if str(member).lower() == ql or member.name.lower() == ql:
            return member
    for member in guild.members:
        if member.nick and member.nick.lower() == ql:
            return member
    for member in guild.members:
        if ql in member.name.lower() or (member.nick and ql in member.nick.lower()):
            return member
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Cog
# ─────────────────────────────────────────────────────────────────────────────

class PermissionsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.remove_command("help")  # on désactive le help par défaut, qui casse tout
        bot.add_check(self._global_permission_check)

    def _global_permission_check(self, ctx: commands.Context) -> bool:
        """Check global appliqué à TOUTE commande texte du bot."""
        if ctx.guild is None:
            return True
        if ctx.author.id == OWNER_ID:
            return True
        cmd_name = ctx.command.qualified_name.lower() if ctx.command else ""
        if not is_restricted_command(cmd_name):
            return True
        return can_use_command(ctx.guild.id, ctx.author.id, cmd_name)

    async def cog_command_error(self, ctx: commands.Context, error):
        # Le check global échoué doit rester totalement silencieux.
        if isinstance(error, commands.CheckFailure):
            return
        raise error

    # ── GIVE / UNGIVE ─────────────────────────────────────────────────────────

    @commands.command(name="give")
    @commands.guild_only()
    async def give(self, ctx: commands.Context, cmd_or_cat: str, *, query: str):
        """d!give <commande|catégorie> <membre> — donne l'accès à une commande ou une catégorie entière."""
        if ctx.author.id != OWNER_ID:
            return
        await ctx.message.delete()

        member = await utils.find_member(ctx, query)
        if not member:
            return await ctx.send(embed=utils.err(f"Membre `{query}` introuvable."), delete_after=10)

        name = cmd_or_cat.lower()
        # d'abord on regarde si c'est une catégorie custom (recherche insensible
        # à la casse : avant, "d!give vip @x" échouait silencieusement si la
        # catégorie s'appelait "VIP", tombant à tort dans "commande introuvable")
        categories = utils.get_categories(ctx.guild.id)
        matched_category = next((c for c in categories if c.lower() == name), None)
        if matched_category is not None:
            added = utils.grant_category(ctx.guild.id, member.id, matched_category)
            if not added:
                return await ctx.send(embed=utils.info(
                    f"{member.mention} avait déjà toutes les commandes de la catégorie `{matched_category}`."
                ), delete_after=10)
            return await ctx.send(embed=utils.ok(
                f"{member.mention} a reçu **{len(added)}** commande(s) de la catégorie `{matched_category}` : "
                + ", ".join(f"`{c}`" for c in added)
            ), delete_after=15)

        if self.bot.get_command(name) is None:
            return await ctx.send(embed=utils.err(f"Commande ou catégorie `{cmd_or_cat}` introuvable."), delete_after=10)

        added = utils.grant_command(ctx.guild.id, member.id, name)
        if not added:
            return await ctx.send(embed=utils.info(f"{member.mention} a déjà accès à `{name}`."), delete_after=10)
        await ctx.send(embed=utils.ok(f"{member.mention} peut désormais utiliser `{name}`."), delete_after=15)

    @commands.command(name="ungive")
    @commands.guild_only()
    async def ungive(self, ctx: commands.Context, cmd: str, *, query: str):
        """d!ungive <commande> <membre> — retire l'accès à une commande précise."""
        if ctx.author.id != OWNER_ID:
            return
        await ctx.message.delete()

        member = await utils.find_member(ctx, query)
        if not member:
            return await ctx.send(embed=utils.err(f"Membre `{query}` introuvable."), delete_after=10)

        name = cmd.lower()
        removed = utils.revoke_command(ctx.guild.id, member.id, name)
        if not removed:
            return await ctx.send(embed=utils.info(f"{member.mention} n'avait pas accès à `{name}`."), delete_after=10)
        await ctx.send(embed=utils.ok(f"{member.mention} n'a plus accès à `{name}`."), delete_after=15)

    # ── BWL / UNBWL ───────────────────────────────────────────────────────────

    @commands.command(name="bwl")
    @commands.guild_only()
    async def bwl(self, ctx: commands.Context, *, query: str):
        """d!bwl <membre> — donne l'accès total à toutes les commandes (comme owner)."""
        if ctx.author.id != OWNER_ID:
            return
        await ctx.message.delete()

        member = await utils.find_member(ctx, query)
        if not member:
            return await ctx.send(embed=utils.err(f"Membre `{query}` introuvable."), delete_after=10)

        added = utils.grant_full_access(ctx.guild.id, member.id)
        if not added:
            return await ctx.send(embed=utils.info(f"{member.mention} a déjà l'accès total."), delete_after=10)
        await ctx.send(embed=discord.Embed(
            title="👑 Accès total accordé",
            description=f"{member.mention} peut désormais utiliser **toutes** les commandes du bot.",
            color=discord.Color.gold(),
        ), delete_after=15)

    @commands.command(name="unbwl")
    @commands.guild_only()
    async def unbwl(self, ctx: commands.Context, *, query: str):
        """d!unbwl <membre> — retire l'accès total."""
        if ctx.author.id != OWNER_ID:
            return
        await ctx.message.delete()

        member = await utils.find_member(ctx, query)
        if not member:
            return await ctx.send(embed=utils.err(f"Membre `{query}` introuvable."), delete_after=10)

        removed = utils.revoke_full_access(ctx.guild.id, member.id)
        if not removed:
            return await ctx.send(embed=utils.info(f"{member.mention} n'avait pas l'accès total."), delete_after=10)
        await ctx.send(embed=utils.ok(f"{member.mention} n'a plus l'accès total."), delete_after=15)

    @commands.command(name="bwllist")
    @commands.guild_only()
    async def bwllist(self, ctx: commands.Context):
        """d!bwllist — liste les membres en accès total."""
        if ctx.author.id != OWNER_ID:
            return
        await ctx.message.delete()

        ids = utils.get_full_access_list(ctx.guild.id)
        if not ids:
            return await ctx.send(embed=utils.info("Aucun membre en accès total."), delete_after=10)
        desc = "\n".join(f"👑 <@{uid}>" for uid in ids)
        await ctx.send(embed=discord.Embed(title="👑 Accès total", description=desc, color=discord.Color.gold()), delete_after=20)

    # ── CATÉGORIES (commandes texte rapides) ─────────────────────────────────

    @commands.command(name="ccreate")
    @commands.guild_only()
    async def ccreate(self, ctx: commands.Context, *, name: str):
        """d!ccreate <nom> — crée une catégorie de commandes."""
        if ctx.author.id != OWNER_ID:
            return
        await ctx.message.delete()
        if not utils.create_category(ctx.guild.id, name.strip()):
            return await ctx.send(embed=utils.err(f"La catégorie `{name}` existe déjà."), delete_after=10)
        await ctx.send(embed=utils.ok(f"Catégorie `{name}` créée. Utilise `{ctx.prefix}cpanel` pour la configurer."), delete_after=15)

    @commands.command(name="cdelete")
    @commands.guild_only()
    async def cdelete(self, ctx: commands.Context, *, name: str):
        """d!cdelete <nom> — supprime une catégorie."""
        if ctx.author.id != OWNER_ID:
            return
        await ctx.message.delete()
        if not utils.delete_category(ctx.guild.id, name.strip()):
            return await ctx.send(embed=utils.err(f"Catégorie `{name}` introuvable."), delete_after=10)
        await ctx.send(embed=utils.ok(f"Catégorie `{name}` supprimée."), delete_after=15)

    @commands.command(name="crename")
    @commands.guild_only()
    async def crename(self, ctx: commands.Context, *, args: str):
        """d!crename <ancien nom> | <nouveau nom>"""
        if ctx.author.id != OWNER_ID:
            return
        await ctx.message.delete()
        if "|" not in args:
            return await ctx.send(embed=utils.err(f"Usage : `{ctx.prefix}crename ancien nom | nouveau nom`"), delete_after=10)
        old, new = (p.strip() for p in args.split("|", 1))
        if not utils.rename_category(ctx.guild.id, old, new):
            return await ctx.send(embed=utils.err("Renommage impossible (catégorie introuvable ou nom déjà pris)."), delete_after=10)
        await ctx.send(embed=utils.ok(f"Catégorie `{old}` renommée en `{new}`."), delete_after=15)

    @commands.command(name="cadd")
    @commands.guild_only()
    async def cadd(self, ctx: commands.Context, name: str, *, cmd: str):
        """d!cadd <catégorie> <commande> — ajoute une commande à une catégorie."""
        if ctx.author.id != OWNER_ID:
            return
        await ctx.message.delete()
        cmd_name = cmd.strip().lower()
        if self.bot.get_command(cmd_name) is None:
            return await ctx.send(embed=utils.err(f"Commande `{cmd_name}` introuvable."), delete_after=10)
        if not utils.add_command_to_category(ctx.guild.id, name, cmd_name):
            return await ctx.send(embed=utils.err(f"Impossible d'ajouter (catégorie introuvable ou commande déjà présente)."), delete_after=10)
        await ctx.send(embed=utils.ok(f"`{cmd_name}` ajoutée à la catégorie `{name}`."), delete_after=15)

    @commands.command(name="cremove")
    @commands.guild_only()
    async def cremove(self, ctx: commands.Context, name: str, *, cmd: str):
        """d!cremove <catégorie> <commande> — retire une commande d'une catégorie."""
        if ctx.author.id != OWNER_ID:
            return
        await ctx.message.delete()
        cmd_name = cmd.strip().lower()
        if not utils.remove_command_from_category(ctx.guild.id, name, cmd_name):
            return await ctx.send(embed=utils.err("Impossible de retirer (catégorie ou commande introuvable)."), delete_after=10)
        await ctx.send(embed=utils.ok(f"`{cmd_name}` retirée de la catégorie `{name}`."), delete_after=15)

    @commands.command(name="cgive")
    @commands.guild_only()
    async def cgive(self, ctx: commands.Context, name: str, *, query: str):
        """d!cgive <catégorie> <membre> — donne toutes les commandes d'une catégorie à un membre."""
        if ctx.author.id != OWNER_ID:
            return
        await ctx.message.delete()

        member = await utils.find_member(ctx, query)
        if not member:
            return await ctx.send(embed=utils.err(f"Membre `{query}` introuvable."), delete_after=10)

        if utils.get_category(ctx.guild.id, name) is None:
            return await ctx.send(embed=utils.err(f"Catégorie `{name}` introuvable."), delete_after=10)
        added = utils.grant_category(ctx.guild.id, member.id, name)
        if not added:
            return await ctx.send(embed=utils.info(f"{member.mention} avait déjà toutes les commandes de `{name}`."), delete_after=10)
        await ctx.send(embed=utils.ok(
            f"{member.mention} a reçu **{len(added)}** commande(s) de la catégorie `{name}`."
        ), delete_after=15)

    @commands.command(name="clist")
    @commands.guild_only()
    async def clist(self, ctx: commands.Context):
        """d!clist — liste les catégories existantes."""
        if ctx.author.id != OWNER_ID:
            return
        await ctx.message.delete()
        categories = utils.get_categories(ctx.guild.id)
        if not categories:
            return await ctx.send(embed=utils.info(f"Aucune catégorie. Utilise `{ctx.prefix}cpanel` pour en créer une."), delete_after=10)
        desc = "\n".join(f"📁 **{name}** — {len(cmds)} commande(s)" for name, cmds in sorted(categories.items()))
        await ctx.send(embed=discord.Embed(title="📁 Catégories", description=desc, color=discord.Color.blurple()), delete_after=20)

    @commands.command(name="cpanel")
    @commands.guild_only()
    async def cpanel(self, ctx: commands.Context):
        """d!cpanel — ouvre le panel complet de gestion des catégories."""
        if ctx.author.id != OWNER_ID:
            return
        await ctx.message.delete()
        panel = CategoryPanelView(ctx.author.id)
        panel.build(ctx.guild.id)
        await ctx.send(embed=panel.build_embed(ctx.guild.id), view=panel)

    # ── AIDE DYNAMIQUE ────────────────────────────────────────────────────────

    @commands.command(name="rh", aliases=["aide", "h"])
    @commands.guild_only()
    async def rh(self, ctx: commands.Context, *, category: str = None):
        """d!rh [catégorie] — aide adaptée aux commandes que tu peux utiliser."""
        prefix = ctx.prefix
        visible = _visible_commands(self.bot, ctx.author)

        if category:
            ql = category.lower()
            match = next((k for k in visible if ql in k.lower()), None)
            if not match:
                match = next(
                    (k for k in visible if any(ql == c.name.lower() for c in visible[k])),
                    None,
                )
            if match:
                return await ctx.send(embed=_category_embed(match, visible[match], prefix), view=HelpView(prefix, visible))

        await ctx.send(embed=_overview_embed(prefix, visible, ctx.author), view=HelpView(prefix, visible))


async def setup(bot: commands.Bot):
    await bot.add_cog(PermissionsCog(bot))
