"""
Cog du panel de candidature (tickets) pour rejoindre une division.

d!panel     : poste le panel persistant de candidature (Administrateur).
d!accepter  : accepte rapidement le candidat du ticket courant (capitaine).
"""
import sys
import pathlib
import logging

import discord
from discord.ext import commands

sys.path.append(str(pathlib.Path(__file__).parent.parent))
from data import database as db
from cogs import divisions as div_mod

logger = logging.getLogger("urahara.tickets")

MAX_MEMBERS = div_mod.MAX_DIVISION_MEMBERS


async def build_panel_embed(guild: discord.Guild, divisions: list) -> discord.Embed:
    embed = discord.Embed(
        title="📋 Candidature - Rejoindre une division",
        description="Sélectionne une division dans le menu ci-dessous pour ouvrir un ticket de candidature.",
        color=discord.Color.blurple(),
    )
    if not divisions:
        embed.description += "\n\nAucune division active pour le moment."
    for d in divisions:
        captain, vices, _, count = await div_mod.get_division_staff(guild, d)
        cap_txt = captain.mention if captain else "Aucun"
        label = f"{(d['emoji'] + ' ') if d['emoji'] else ''}Division {d['number']}"
        embed.add_field(name=label, value=f"👤 {cap_txt} • {count}/{MAX_MEMBERS} membres", inline=True)
    embed.set_footer(text="Urahara • Gestion RP")
    return embed


async def build_ticket_embed(guild: discord.Guild, div, member: discord.Member) -> discord.Embed:
    captain, vices, lieutenants, count = await div_mod.get_division_staff(guild, div)
    embed = discord.Embed(
        title=f"📨 Candidature • Division {div['number']}",
        description=f"{member.mention}, ta candidature va être prise en charge par le capitaine de la division.",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Effectif", value=f"{count}/{MAX_MEMBERS}", inline=True)
    embed.add_field(name="Capitaine", value=captain.mention if captain else "Aucun", inline=True)
    embed.add_field(name="Vice-capitaine", value=", ".join(v.mention for v in vices) if vices else "Aucun", inline=True)
    embed.add_field(name="Lieutenant", value=", ".join(l.mention for l in lieutenants) if lieutenants else "Aucun", inline=True)
    if div["emoji"]:
        embed.add_field(name="Emoji", value=div["emoji"], inline=True)
    embed.set_footer(text="Urahara • Ticket de candidature")
    return embed


def channel_name_for(member: discord.Member) -> str:
    return f"ticket-{member.name}".lower().replace(" ", "-")[:90]


async def is_captain_or_admin(interaction: discord.Interaction, div) -> bool:
    if interaction.user.guild_permissions.administrator:
        return True
    if div and interaction.user.id == div["captain_id"]:
        return True
    await interaction.response.send_message(div_mod.error_embed("Seul le capitaine de la division peut faire ça."), ephemeral=True)
    return False


# ---------------------------------------------------------------------------
# Panel - Select
# ---------------------------------------------------------------------------

class DivisionSelect(discord.ui.Select):
    def __init__(self, divisions: list):
        options = [
            discord.SelectOption(label=f"Division {d['number']}", value=str(d["number"]), emoji=d["emoji"] or None)
            for d in divisions
        ] or [discord.SelectOption(label="Aucune division disponible", value="none")]
        super().__init__(
            placeholder="Choisis une division...", options=options, custom_id="urahara_panel_select",
            min_values=1, max_values=1, disabled=(len(divisions) == 0),
        )

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            return
        await create_ticket_flow(interaction, int(self.values[0]))


class PanelView(discord.ui.View):
    def __init__(self, divisions: list):
        super().__init__(timeout=None)
        self.add_item(DivisionSelect(divisions))


# ---------------------------------------------------------------------------
# Ticket - création
# ---------------------------------------------------------------------------

async def create_ticket_flow(interaction: discord.Interaction, division_number: int):
    guild = interaction.guild
    member = interaction.user

    div = await db.get_division(division_number)
    if div is None:
        await interaction.response.send_message(embed=div_mod.error_embed("Cette division n'est pas encore active."), ephemeral=True)
        return

    division_role = guild.get_role(div["role_id"])
    if division_role and division_role in member.roles:
        await interaction.response.send_message(embed=div_mod.error_embed("Tu es déjà membre de cette division."), ephemeral=True)
        return
    if div_mod.find_division_roles(member):
        await interaction.response.send_message(embed=div_mod.error_embed("Tu fais déjà partie d'une autre division."), ephemeral=True)
        return

    existing_ticket = await db.get_open_ticket(division_number, member.id)
    if existing_ticket:
        channel = guild.get_channel(existing_ticket["channel_id"])
        msg = f"Tu as déjà une candidature en cours pour cette division{f' ({channel.mention})' if channel else ''}."
        await interaction.response.send_message(embed=div_mod.error_embed(msg), ephemeral=True)
        return

    now = int(discord.utils.utcnow().timestamp())
    sanction = await db.get_sanction(member.id)
    if sanction and sanction["no_join_until"] and sanction["no_join_until"] > now:
        await interaction.response.send_message(embed=div_mod.error_embed(f"Tu ne peux pas postuler avant <t:{sanction['no_join_until']}:F>."), ephemeral=True)
        return
    leave_cd = await db.get_leave_cooldown(member.id, division_number)
    if leave_cd and leave_cd["no_rejoin_until"] > now:
        await interaction.response.send_message(embed=div_mod.error_embed(f"Tu ne peux pas rejoindre cette division avant <t:{leave_cd['no_rejoin_until']}:F>."), ephemeral=True)
        return
    ban = await db.get_division_ban(member.id, division_number)
    if ban and (ban["banned_until"] is None or ban["banned_until"] > now):
        await interaction.response.send_message(embed=div_mod.error_embed("Tu es banni de cette division."), ephemeral=True)
        return
    expulsion = await db.get_division_expulsion(member.id, division_number)
    if expulsion and expulsion["until_ts"] > now:
        await interaction.response.send_message(
            embed=div_mod.error_embed(f"Tu as été expulsé récemment, impossible de postuler avant <t:{expulsion['until_ts']}:F>."), ephemeral=True
        )
        return

    captain, vices, _, count = await div_mod.get_division_staff(guild, div)
    if count >= MAX_MEMBERS:
        await interaction.response.send_message(embed=div_mod.error_embed(f"La division {division_number} est complète ({MAX_MEMBERS}/{MAX_MEMBERS})."), ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    category = guild.get_channel(div["category_id"])
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    if captain:
        overwrites[captain] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    for vice in vices:
        overwrites[vice] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

    try:
        channel = await guild.create_text_channel(channel_name_for(member), category=category, overwrites=overwrites)
    except discord.Forbidden:
        await interaction.followup.send(embed=div_mod.error_embed("Je n'ai pas la permission de créer le salon du ticket."), ephemeral=True)
        return

    ticket_id = await db.create_ticket(division_number, member.id, channel.id, now)
    embed = await build_ticket_embed(guild, div, member)
    view = TicketActionView(ticket_id)

    mentions = " ".join(m.mention for m in ([member, captain] + vices) if m)
    ticket_message = await channel.send(content=mentions, embed=embed, view=view)
    interaction.client.add_view(view, message_id=ticket_message.id)
    await db.set_ticket_message(ticket_id, ticket_message.id)
    try:
        await ticket_message.pin(reason="Message d'accueil de candidature")
    except discord.HTTPException:
        pass

    await interaction.followup.send(embed=div_mod.success_embed("Ticket créé", f"Ta candidature a été envoyée : {channel.mention}"), ephemeral=True)


# ---------------------------------------------------------------------------
# Ticket - actions
# ---------------------------------------------------------------------------

class TicketActionView(discord.ui.View):
    def __init__(self, ticket_id: int):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
        accept = discord.ui.Button(label="Accepter", style=discord.ButtonStyle.success, custom_id=f"ticket_accept:{ticket_id}")
        refuse = discord.ui.Button(label="Refuser", style=discord.ButtonStyle.danger, custom_id=f"ticket_refuse:{ticket_id}")
        close = discord.ui.Button(label="Fermer", style=discord.ButtonStyle.secondary, custom_id=f"ticket_close:{ticket_id}")
        accept.callback = self._callback(handle_ticket_accept)
        refuse.callback = self._callback(handle_ticket_refuse)
        close.callback = self._callback(handle_ticket_close)
        self.add_item(accept)
        self.add_item(refuse)
        self.add_item(close)

    def _callback(self, handler):
        async def cb(interaction: discord.Interaction):
            await handler(interaction, self.ticket_id)
        return cb


def disabled_decision_view(ticket_id: int) -> TicketActionView:
    view = TicketActionView(ticket_id)
    for item in view.children:
        if item.custom_id.startswith("ticket_accept") or item.custom_id.startswith("ticket_refuse"):
            item.disabled = True
    return view


async def handle_ticket_accept(interaction: discord.Interaction, ticket_id: int):
    ticket = await db.get_ticket(ticket_id)
    if ticket is None or ticket["status"] != "pending":
        await interaction.response.send_message(embed=div_mod.error_embed("Ce ticket n'est plus en attente."), ephemeral=True)
        return
    div = await db.get_division(ticket["division_number"])
    if div is None:
        await interaction.response.send_message(embed=div_mod.error_embed("Cette division n'existe plus."), ephemeral=True)
        return
    if not await is_captain_or_admin(interaction, div):
        return

    guild = interaction.guild
    _, _, _, count = await div_mod.get_division_staff(guild, div)
    if count >= MAX_MEMBERS:
        await interaction.response.send_message(embed=div_mod.error_embed(f"La division est complète ({MAX_MEMBERS}/{MAX_MEMBERS})."), ephemeral=True)
        return

    member = guild.get_member(ticket["applicant_id"])
    if member is None:
        await interaction.response.send_message(embed=div_mod.error_embed("Ce membre a quitté le serveur."), ephemeral=True)
        return
    if div_mod.find_division_roles(member):
        await interaction.response.send_message(embed=div_mod.error_embed("Ce membre fait déjà partie d'une division."), ephemeral=True)
        return

    # On defer tout de suite : tout ce qui suit (rôle, pseudo, message de bienvenue,
    # DB) peut dépasser les 3s que Discord laisse avant d'invalider le token.
    await interaction.response.defer()

    division_role = guild.get_role(div["role_id"])
    try:
        await member.add_roles(division_role, reason="Candidature acceptée")
    except discord.HTTPException:
        await interaction.followup.send(embed=div_mod.error_embed("Impossible d'ajouter le rôle."), ephemeral=True)
        return
    await div_mod.set_division_tag(member, ticket["division_number"])
    await div_mod.send_welcome(guild, div, member, "par candidature")
    await db.update_ticket_status(ticket_id, "accepted")

    embed = interaction.message.embeds[0]
    embed.add_field(name="Statut", value=f"✅ Accepté par {interaction.user.mention}", inline=False)
    await interaction.edit_original_response(embed=embed, view=disabled_decision_view(ticket_id))
    await interaction.channel.send(embed=div_mod.success_embed("Candidature acceptée", f"{member.mention} rejoint la division {ticket['division_number']} !"))


async def handle_ticket_refuse(interaction: discord.Interaction, ticket_id: int):
    ticket = await db.get_ticket(ticket_id)
    if ticket is None or ticket["status"] != "pending":
        await interaction.response.send_message(embed=div_mod.error_embed("Ce ticket n'est plus en attente."), ephemeral=True)
        return
    div = await db.get_division(ticket["division_number"])
    if not await is_captain_or_admin(interaction, div):
        return

    await interaction.response.defer()

    guild = interaction.guild
    member = guild.get_member(ticket["applicant_id"])
    if member:
        try:
            await interaction.channel.set_permissions(member, overwrite=None)
        except discord.HTTPException:
            pass
    await db.update_ticket_status(ticket_id, "refused")

    embed = interaction.message.embeds[0]
    embed.add_field(name="Statut", value=f"❌ Refusé par {interaction.user.mention}", inline=False)
    await interaction.edit_original_response(embed=embed, view=disabled_decision_view(ticket_id))
    await interaction.channel.send(embed=div_mod.info_embed("Candidature refusée."))


async def handle_ticket_close(interaction: discord.Interaction, ticket_id: int):
    ticket = await db.get_ticket(ticket_id)
    if ticket is None:
        await interaction.response.send_message(embed=div_mod.error_embed("Ticket introuvable."), ephemeral=True)
        return
    div = await db.get_division(ticket["division_number"])
    if not await is_captain_or_admin(interaction, div):
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    guild = interaction.guild
    channel = interaction.channel

    history = [m async for m in channel.history(limit=500, oldest_first=True)]
    transcript_lines = [f"{m.author}: {m.content}" for m in history if m.id != ticket["message_id"] and m.content]

    if ticket["status"] == "pending":
        await db.update_ticket_status(ticket_id, "closed")

    if transcript_lines:
        transcript = f"Transcript du ticket - Division {ticket['division_number']}\n\n" + "\n".join(transcript_lines)
        if len(transcript) > 1900:
            transcript = transcript[:1900] + "\n(...)"
        applicant = guild.get_member(ticket["applicant_id"])
        captain = guild.get_member(div["captain_id"]) if div else None
        for recipient in (applicant, captain):
            if recipient:
                try:
                    await recipient.send(f"```{transcript}```")
                except discord.HTTPException:
                    pass

    await interaction.followup.send(embed=div_mod.success_embed("Ticket fermé", "Le salon va être supprimé."), ephemeral=True)
    try:
        await channel.delete(reason="Ticket de candidature fermé")
    except discord.HTTPException:
        pass


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        divisions = await db.get_all_divisions()
        for panel in await db.get_panel_messages():
            self.bot.add_view(PanelView(divisions), message_id=panel["message_id"])
        for ticket in await db.get_pending_tickets():
            self.bot.add_view(TicketActionView(ticket["id"]))

    @commands.Cog.listener()
    async def on_ready(self):
        # Force la remise à jour de TOUS les panels déjà postés, sur tous les
        # serveurs connus, à chaque démarrage/redéploiement. Sans ça, un panel
        # gardait l'ancien embed/état tant qu'aucune division n'était créée ou
        # dissoute après le déploiement.
        for guild in self.bot.guilds:
            await self.refresh_panels(guild)

    async def refresh_panels(self, guild: discord.Guild):
        divisions = await db.get_all_divisions()
        embed = await build_panel_embed(guild, divisions)
        for panel in await db.get_panel_messages():
            channel = guild.get_channel(panel["channel_id"])
            if channel is None:
                # Le cache interne du bot peut ne pas encore contenir ce salon
                # juste après un reboot/reconnexion, même s'il existe bel et
                # bien sur Discord. On retente un fetch réseau explicite avant
                # de conclure que le salon a été supprimé : sinon on risque de
                # supprimer par erreur une entrée panel_messages parfaitement
                # valide et de perdre la persistance du panel pour rien.
                try:
                    channel = await guild.fetch_channel(panel["channel_id"])
                except discord.NotFound:
                    # Là, le salon a vraiment été supprimé : impossible de recréer où que ce soit.
                    await db.remove_panel_message(panel["message_id"])
                    continue
                except discord.HTTPException:
                    # Erreur réseau/permission temporaire : on ne touche pas à la DB,
                    # on retentera au prochain refresh.
                    continue
            try:
                msg = await channel.fetch_message(panel["message_id"])
            except discord.NotFound:
                # Le message a été supprimé manuellement (par erreur ou nettoyage du salon) :
                # on le recrée automatiquement dans le même salon plutôt que de forcer
                # un nouveau d!panel. C'est la seule vraie "persistance" possible ici,
                # Discord ne permettant pas de retrouver un message supprimé.
                await db.remove_panel_message(panel["message_id"])
                try:
                    view = PanelView(divisions)
                    new_msg = await channel.send(embed=embed, view=view)
                    self.bot.add_view(view, message_id=new_msg.id)
                    await db.add_panel_message(channel.id, new_msg.id)
                    logger.info("Panel recréé automatiquement dans #%s (message précédent supprimé).", channel.name)
                except discord.HTTPException:
                    logger.exception("Impossible de recréer automatiquement le panel dans #%s.", channel.name)
                continue
            except discord.HTTPException:
                continue

            view = PanelView(divisions)
            try:
                await msg.edit(embed=embed, view=view)
                self.bot.add_view(view, message_id=msg.id)
            except discord.HTTPException:
                pass

    @commands.command(name="panel")
    @commands.has_permissions(administrator=True)
    async def panel_cmd(self, ctx: commands.Context):
        """d!panel — poste (ou met à jour) le panel de candidature.
        Si un panel existe déjà dans CE salon, il est édité en place (l'embed
        n'est jamais perdu/renvoyé). S'il existe dans un AUTRE salon, il est
        déplacé : l'ancien est supprimé et un nouveau est posté ici. Il n'y a
        donc jamais plusieurs panels actifs en même temps."""
        divisions = await db.get_all_divisions()
        embed = await build_panel_embed(ctx.guild, divisions)
        view = PanelView(divisions)

        old_panels = await db.get_panel_messages()

        # Cas 1 : un panel existe déjà dans ce salon -> on l'édite en place.
        for old in old_panels:
            if old["channel_id"] != ctx.channel.id:
                continue
            try:
                old_msg = await ctx.channel.fetch_message(old["message_id"])
            except discord.HTTPException:
                # Le message a disparu : on nettoie l'entrée et on tombe au cas 2 (recréation).
                await db.remove_panel_message(old["message_id"])
                break
            await old_msg.edit(embed=embed, view=view)
            self.bot.add_view(view, message_id=old_msg.id)
            await ctx.message.add_reaction("✅")
            return

        # Cas 2 : nettoyage des panels situés dans d'autres salons (on ne veut
        # jamais plusieurs panels actifs en même temps sur le serveur), puis
        # création du nouveau panel ici.
        for old in old_panels:
            old_channel = ctx.guild.get_channel(old["channel_id"])
            if old_channel is None:
                try:
                    old_channel = await ctx.guild.fetch_channel(old["channel_id"])
                except discord.HTTPException:
                    old_channel = None
            if old_channel is not None:
                try:
                    old_msg = await old_channel.fetch_message(old["message_id"])
                    await old_msg.delete()
                except discord.HTTPException:
                    pass
            await db.remove_panel_message(old["message_id"])

        msg = await ctx.send(embed=embed, view=view)
        self.bot.add_view(view, message_id=msg.id)
        await db.add_panel_message(ctx.channel.id, msg.id)

    @commands.command(name="accepter")
    async def accepter_cmd(self, ctx: commands.Context):
        ticket = await db.get_ticket_by_channel(ctx.channel.id)
        if ticket is None or ticket["status"] != "pending":
            await ctx.send(embed=div_mod.error_embed("Cette commande ne fonctionne que dans un ticket de candidature en attente."))
            return
        div = await db.get_division(ticket["division_number"])
        if div is None:
            await ctx.send(embed=div_mod.error_embed("Cette division n'existe plus."))
            return
        if ctx.author.id != div["captain_id"] and not ctx.author.guild_permissions.administrator:
            await ctx.send(embed=div_mod.error_embed("Seul le capitaine de la division peut utiliser cette commande."))
            return

        guild = ctx.guild
        _, _, _, count = await div_mod.get_division_staff(guild, div)
        if count >= MAX_MEMBERS:
            await ctx.send(embed=div_mod.error_embed(f"La division est complète ({MAX_MEMBERS}/{MAX_MEMBERS})."))
            return

        member = guild.get_member(ticket["applicant_id"])
        if member is None:
            await ctx.send(embed=div_mod.error_embed("Ce membre a quitté le serveur."))
            return
        if div_mod.find_division_roles(member):
            await ctx.send(embed=div_mod.error_embed("Ce membre fait déjà partie d'une division."))
            return

        division_role = guild.get_role(div["role_id"])
        try:
            await member.add_roles(division_role, reason="Candidature acceptée (commande)")
        except discord.HTTPException:
            await ctx.send(embed=div_mod.error_embed("Impossible d'ajouter le rôle."))
            return
        await div_mod.set_division_tag(member, ticket["division_number"])
        await div_mod.send_welcome(guild, div, member, "par candidature")
        await db.update_ticket_status(ticket["id"], "accepted")

        if ticket["message_id"]:
            try:
                msg = await ctx.channel.fetch_message(ticket["message_id"])
                embed = msg.embeds[0]
                embed.add_field(name="Statut", value=f"✅ Accepté par {ctx.author.mention}", inline=False)
                await msg.edit(embed=embed, view=disabled_decision_view(ticket["id"]))
                if not msg.pinned:
                    await msg.pin()
            except discord.HTTPException:
                pass

        await ctx.send(embed=div_mod.success_embed("Candidature acceptée", f"{member.mention} rejoint la division {ticket['division_number']} !"))

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(embed=div_mod.error_embed("Réservé aux administrateurs."))
            return
        logger.exception("Erreur non gérée sur une commande de Tickets", exc_info=error)


async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))