import asyncio
import json
import os
import random
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord.ext import commands, tasks

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "giveaways_data.json")
_KV_NAMESPACE = "yamamotobot"
_KV_KEY = "giveaways_data.json"

UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "j": 86400}
UNIT_LABEL = {"s": "seconde(s)", "m": "minute(s)", "h": "heure(s)", "j": "jour(s)"}

EMBED_COLOR_SETUP = 0x5865F2
EMBED_COLOR_RUNNING = 0x2ECC71
EMBED_COLOR_ENDED = 0x95A5A6
EMBED_COLOR_CANCELLED = 0xE74C3C


# ============================================================================
# UTILITAIRES 
# ============================================================================

def parse_duration(text: str) -> Optional[int]:
    """Parse une durée du type '1j2h30m', '45m', '10s' -> secondes (ou None)."""
    if not text:
        return None
    text = text.replace(" ", "").lower()
    matches = re.findall(r"(\d+)([smhj])", text)
    if not matches:
        return None
    total = 0
    for value, unit in matches:
        total += int(value) * UNIT_SECONDS[unit]
    return total if total > 0 else None


def format_duration(seconds: int) -> str:
    if seconds is None:
        return "—"
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}j")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


def new_id_counter(data: dict) -> int:
    data["_counter"] = data.get("_counter", 0) + 1
    return data["_counter"]


# ============================================================================
# MODALS (formulaires)
# ============================================================================

class GiveawayInfoModal(discord.ui.Modal, title="🎉 Configuration du Giveaway"):
    nom = discord.ui.TextInput(
        label="Nom du giveaway",
        placeholder="Ex: Giveaway Nitro",
        max_length=100,
        required=True,
    )
    description = discord.ui.TextInput(
        label="Description (optionnel)",
        style=discord.TextStyle.paragraph,
        placeholder="Détails, conditions, message à afficher...",
        max_length=500,
        required=False,
    )
    recompense = discord.ui.TextInput(
        label="Récompense",
        placeholder="Ex: 1x Discord Nitro 1 mois",
        max_length=200,
        required=True,
    )
    gagnants = discord.ui.TextInput(
        label="Nombre de gagnants",
        placeholder="1 par défaut",
        max_length=3,
        required=False,
    )
    duree = discord.ui.TextInput(
        label="Durée (ex: 1j12h, 30m, 45s)",
        placeholder="Unités : s / m / h / j",
        max_length=50,
        required=True,
    )

    def __init__(self, panel: "ConfigPanelView"):
        super().__init__()
        self.panel = panel
        cfg = panel.config
        self.nom.default = cfg.get("name") or None
        self.description.default = cfg.get("description") or None
        self.recompense.default = cfg.get("reward") or None
        self.gagnants.default = str(cfg.get("winners_count", 1))
        self.duree.default = cfg.get("duration_text") or None

    async def on_submit(self, interaction: discord.Interaction):
        winners_raw = (self.gagnants.value or "1").strip()
        if not winners_raw.isdigit() or int(winners_raw) < 1:
            await interaction.response.send_message(
                "❌ Le nombre de gagnants doit être un entier ≥ 1.", ephemeral=True
            )
            return
        duration = parse_duration(self.duree.value)
        if duration is None:
            await interaction.response.send_message(
                "❌ Durée invalide. Format attendu : nombres suivis de s/m/h/j (ex: `1j12h`, `30m`).",
                ephemeral=True,
            )
            return

        self.panel.config["name"] = self.nom.value.strip()
        self.panel.config["description"] = (self.description.value or "").strip()
        self.panel.config["reward"] = self.recompense.value.strip()
        self.panel.config["winners_count"] = int(winners_raw)
        self.panel.config["duration_text"] = self.duree.value.strip()
        self.panel.config["duration_seconds"] = duration

        await self.panel.refresh(interaction)


class NumberModal(discord.ui.Modal):
    valeur = discord.ui.TextInput(label="Valeur (entier, 0 = désactiver)", max_length=10, required=True)

    def __init__(self, panel: "ConfigPanelView", key: str, title: str, default=None):
        super().__init__(title=title)
        self.panel = panel
        self.key = key
        if default is not None:
            self.valeur.default = str(default)

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.valeur.value.strip()
        if not raw.isdigit():
            await interaction.response.send_message("❌ Merci d'entrer un nombre entier valide.", ephemeral=True)
            return
        n = int(raw)
        self.panel.config["conditions"][self.key] = n if n > 0 else None
        await self.panel.refresh(interaction)


class DurationConditionModal(discord.ui.Modal, title="⏱️ Temps vocal requis"):
    valeur = discord.ui.TextInput(
        label="Durée (vide/0 = désactiver)",
        placeholder="Ex: 1h30m",
        max_length=30,
        required=False,
    )

    def __init__(self, panel: "ConfigPanelView"):
        super().__init__()
        self.panel = panel

    async def on_submit(self, interaction: discord.Interaction):
        raw = (self.valeur.value or "").strip()
        if raw in ("", "0"):
            self.panel.config["conditions"]["min_voice_time"] = None
        else:
            secs = parse_duration(raw)
            if secs is None:
                await interaction.response.send_message("❌ Durée invalide.", ephemeral=True)
                return
            self.panel.config["conditions"]["min_voice_time"] = secs
        await self.panel.refresh(interaction)


# ============================================================================
# VUES DE CONFIGURATION (avant lancement)
# ============================================================================

class RoleSelectView(discord.ui.View):
    """Vue temporaire affichée pour choisir les rôles autorisés / interdits."""

    def __init__(self, panel: "ConfigPanelView", target_key: str, label: str):
        super().__init__(timeout=180)
        self.panel = panel
        self.target_key = target_key
        select = discord.ui.RoleSelect(
            placeholder=f"Choisir les rôles {label}",
            min_values=0,
            max_values=25,
        )
        select.callback = self.on_select
        self.add_item(select)

        back = discord.ui.Button(label="◀ Retour", style=discord.ButtonStyle.secondary)
        back.callback = self.on_back
        self.add_item(back)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.panel.owner_id:
            await interaction.response.send_message("Ce panneau ne t'appartient pas.", ephemeral=True)
            return False
        return True

    async def on_select(self, interaction: discord.Interaction):
        select: discord.ui.RoleSelect = interaction.data  # not used directly
        # récupérer les rôles choisis depuis le composant
        for item in self.children:
            if isinstance(item, discord.ui.RoleSelect):
                self.panel.config[self.target_key] = [r.id for r in item.values]
        await self.panel.refresh(interaction)

    async def on_back(self, interaction: discord.Interaction):
        await self.panel.refresh(interaction)


class ConditionsView(discord.ui.View):
    """Sous-panneau pour configurer les conditions optionnelles de participation."""

    def __init__(self, panel: "ConfigPanelView"):
        super().__init__(timeout=180)
        self.panel = panel
        self.build()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.panel.owner_id:
            await interaction.response.send_message("Ce panneau ne t'appartient pas.", ephemeral=True)
            return False
        return True

    def build(self):
        self.clear_items()
        cond = self.panel.config["conditions"]

        voc_on = bool(cond.get("voice_present"))
        btn_voc = discord.ui.Button(
            label=f"🎙️ Présent en vocal : {'✅ Activé' if voc_on else '◻️ Désactivé'}",
            style=discord.ButtonStyle.success if voc_on else discord.ButtonStyle.secondary,
            row=0,
        )
        btn_voc.callback = self.toggle_voice
        self.add_item(btn_voc)

        msg_val = cond.get("min_messages")
        btn_msg = discord.ui.Button(
            label=f"💬 Messages requis : {msg_val if msg_val else 'Désactivé'}",
            style=discord.ButtonStyle.success if msg_val else discord.ButtonStyle.secondary,
            row=1,
        )
        btn_msg.callback = self.set_messages
        self.add_item(btn_msg)

        inv_val = cond.get("min_invites")
        btn_inv = discord.ui.Button(
            label=f"📨 Invitations requises : {inv_val if inv_val else 'Désactivé'}",
            style=discord.ButtonStyle.success if inv_val else discord.ButtonStyle.secondary,
            row=2,
        )
        btn_inv.callback = self.set_invites
        self.add_item(btn_inv)

        voctime_val = cond.get("min_voice_time")
        btn_voctime = discord.ui.Button(
            label=f"⏱️ Temps vocal requis : {format_duration(voctime_val) if voctime_val else 'Désactivé'}",
            style=discord.ButtonStyle.success if voctime_val else discord.ButtonStyle.secondary,
            row=3,
        )
        btn_voctime.callback = self.set_voice_time
        self.add_item(btn_voctime)

        back = discord.ui.Button(label="◀ Retour", style=discord.ButtonStyle.secondary, row=4)
        back.callback = self.on_back
        self.add_item(back)

    async def toggle_voice(self, interaction: discord.Interaction):
        cond = self.panel.config["conditions"]
        cond["voice_present"] = not bool(cond.get("voice_present"))
        self.build()
        await interaction.response.edit_message(embed=self.panel.build_embed(), view=self)

    async def set_messages(self, interaction: discord.Interaction):
        await interaction.response.send_modal(
            NumberModal(self.panel, "min_messages", "💬 Nombre de messages requis",
                        self.panel.config["conditions"].get("min_messages"))
        )

    async def set_invites(self, interaction: discord.Interaction):
        await interaction.response.send_modal(
            NumberModal(self.panel, "min_invites", "📨 Nombre d'invitations requises",
                        self.panel.config["conditions"].get("min_invites"))
        )

    async def set_voice_time(self, interaction: discord.Interaction):
        await interaction.response.send_modal(DurationConditionModal(self.panel))

    async def on_back(self, interaction: discord.Interaction):
        await self.panel.refresh(interaction)

    async def refresh_in_place(self, interaction: discord.Interaction):
        self.build()
        await interaction.response.edit_message(embed=self.panel.build_embed(), view=self)


class ConfigPanelView(discord.ui.View):
    """Panneau principal de configuration d'un giveaway, avant lancement."""

    def __init__(self, cog: "GiveawayCog", owner: discord.Member, channel: discord.abc.Messageable):
        super().__init__(timeout=600)
        self.cog = cog
        self.owner_id = owner.id
        self.channel = channel
        self.message: Optional[discord.Message] = None
        self.config = {
            "name": "",
            "description": "",
            "reward": "",
            "winners_count": 1,
            "duration_text": "",
            "duration_seconds": None,
            "required_roles": [],
            "forbidden_roles": [],
            "conditions": {
                "voice_present": False,
                "min_messages": None,
                "min_invites": None,
                "min_voice_time": None,
            },
        }
        self.build_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Ce panneau ne t'appartient pas.", ephemeral=True)
            return False
        return True

    def build_buttons(self):
        self.clear_items()

        b_info = discord.ui.Button(label="📝 Infos du giveaway", style=discord.ButtonStyle.primary, row=0)
        b_info.callback = self.open_info
        self.add_item(b_info)

        b_req = discord.ui.Button(
            label=f"✅ Rôles autorisés ({len(self.config['required_roles'])})",
            style=discord.ButtonStyle.secondary, row=1,
        )
        b_req.callback = self.open_required_roles
        self.add_item(b_req)

        b_forb = discord.ui.Button(
            label=f"🚫 Rôles interdits ({len(self.config['forbidden_roles'])})",
            style=discord.ButtonStyle.secondary, row=1,
        )
        b_forb.callback = self.open_forbidden_roles
        self.add_item(b_forb)

        nb_cond = sum(1 for v in self.config["conditions"].values() if v)
        b_cond = discord.ui.Button(
            label=f"⚙️ Conditions ({nb_cond} active(s))", style=discord.ButtonStyle.secondary, row=2
        )
        b_cond.callback = self.open_conditions
        self.add_item(b_cond)

        ready = self.is_ready()
        b_launch = discord.ui.Button(
            label="🚀 Lancer le giveaway",
            style=discord.ButtonStyle.success,
            row=3,
            disabled=not ready,
        )
        b_launch.callback = self.launch
        self.add_item(b_launch)

        b_cancel = discord.ui.Button(label="❌ Annuler", style=discord.ButtonStyle.danger, row=3)
        b_cancel.callback = self.cancel_setup
        self.add_item(b_cancel)

    def is_ready(self) -> bool:
        return bool(self.config["name"]) and bool(self.config["reward"]) and self.config["duration_seconds"]

    def build_embed(self) -> discord.Embed:
        cfg = self.config
        e = discord.Embed(
            title="🛠️ Configuration du Giveaway",
            description="Configure chaque section ci-dessous puis clique sur **🚀 Lancer le giveaway**.",
            color=EMBED_COLOR_SETUP,
        )
        e.add_field(name="Nom", value=cfg["name"] or "*non défini*", inline=True)
        e.add_field(name="Récompense", value=cfg["reward"] or "*non définie*", inline=True)
        e.add_field(name="Gagnants", value=str(cfg["winners_count"]), inline=True)
        e.add_field(name="Durée", value=cfg["duration_text"] or "*non définie*", inline=True)
        e.add_field(
            name="Description",
            value=cfg["description"] or "*aucune*",
            inline=False,
        )

        req = cfg["required_roles"]
        forb = cfg["forbidden_roles"]
        e.add_field(
            name="✅ Rôles autorisés (requis)",
            value=", ".join(f"<@&{r}>" for r in req) if req else "*aucun*",
            inline=False,
        )
        e.add_field(
            name="🚫 Rôles interdits",
            value=", ".join(f"<@&{r}>" for r in forb) if forb else "*aucun*",
            inline=False,
        )

        cond = cfg["conditions"]
        lines = []
        if cond.get("voice_present"):
            lines.append("🎙️ Être en vocal à la fin du giveaway")
        if cond.get("min_messages"):
            lines.append(f"💬 Avoir envoyé au moins **{cond['min_messages']}** messages depuis le lancement")
        if cond.get("min_invites"):
            lines.append(f"📨 Avoir invité au moins **{cond['min_invites']}** membres depuis le lancement")
        if cond.get("min_voice_time"):
            lines.append(f"⏱️ Avoir passé au moins **{format_duration(cond['min_voice_time'])}** en vocal depuis le lancement")
        e.add_field(name="⚙️ Conditions de victoire", value="\n".join(lines) if lines else "*aucune*", inline=False)

        if not self.is_ready():
            e.set_footer(text="⚠️ Le nom, la récompense et la durée sont obligatoires pour lancer le giveaway.")
        else:
            e.set_footer(text="Tout est prêt — tu peux lancer le giveaway quand tu veux.")
        return e

    async def refresh(self, interaction: discord.Interaction):
        self.build_buttons()
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=self.build_embed(), view=self)
        else:
            await interaction.response.edit_message(embed=self.build_embed(), view=self)

    # --- callbacks ---

    async def open_info(self, interaction: discord.Interaction):
        await interaction.response.send_modal(GiveawayInfoModal(self))

    async def open_required_roles(self, interaction: discord.Interaction):
        view = RoleSelectView(self, "required_roles", "autorisés")
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="✅ Rôles autorisés",
                description="Tout participant devra posséder **tous** les rôles sélectionnés. Laisse vide pour désactiver.",
                color=EMBED_COLOR_SETUP,
            ),
            view=view,
        )

    async def open_forbidden_roles(self, interaction: discord.Interaction):
        view = RoleSelectView(self, "forbidden_roles", "interdits")
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="🚫 Rôles interdits",
                description="Tout membre possédant **au moins un** de ces rôles ne pourra pas participer. Laisse vide pour désactiver.",
                color=EMBED_COLOR_SETUP,
            ),
            view=view,
        )

    async def open_conditions(self, interaction: discord.Interaction):
        view = ConditionsView(self)
        await interaction.response.edit_message(embed=self.build_embed(), view=view)

    async def cancel_setup(self, interaction: discord.Interaction):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            embed=discord.Embed(title="❌ Configuration annulée", color=EMBED_COLOR_CANCELLED),
            view=self,
        )
        self.stop()

    async def launch(self, interaction: discord.Interaction):
        if not self.is_ready():
            await interaction.response.send_message("❌ Configuration incomplète.", ephemeral=True)
            return
        await interaction.response.defer()
        gw = await self.cog.create_giveaway(self.channel, interaction.user, self.config)
        for item in self.children:
            item.disabled = True
        await interaction.edit_original_response(
            embed=discord.Embed(
                title="✅ Giveaway lancé !",
                description=f"Le giveaway **{gw['name']}** (ID `{gw['id']}`) est maintenant actif dans {self.channel.mention}.",
                color=EMBED_COLOR_RUNNING,
            ),
            view=self,
        )
        self.stop()


# ============================================================================
# VUES DU GIVEAWAY LANCÉ (persistantes)
# ============================================================================

class UnsubscribeView(discord.ui.View):
    """Vue envoyée en éphémère quand un membre qui participe déjà reclique sur Participer."""

    def __init__(self, cog: "GiveawayCog", gw_id: int, user_id: int):
        super().__init__(timeout=120)
        self.cog = cog
        self.gw_id = gw_id
        self.user_id = user_id

    @discord.ui.button(label="Se désinscrire", style=discord.ButtonStyle.danger, emoji="🚪")
    async def unsubscribe(self, interaction: discord.Interaction, button: discord.ui.Button):
        gw = self.cog.data["giveaways"].get(str(self.gw_id))
        if not gw or gw["status"] != "active":
            await interaction.response.edit_message(content="Ce giveaway n'est plus actif.", view=None)
            return
        if self.user_id in gw["participants"]:
            gw["participants"].remove(self.user_id)
            self.cog.save_data()
            self.cog.schedule_embed_update(self.gw_id)
            await interaction.response.edit_message(content="✅ Tu as bien été désinscrit du giveaway.", view=None)
        else:
            await interaction.response.edit_message(content="Tu ne participais déjà plus.", view=None)


class GiveawayView(discord.ui.View):
    """Vue persistante attachée au message public du giveaway."""

    def __init__(self, cog: "GiveawayCog", gw_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.gw_id = gw_id

        join_btn = discord.ui.Button(
            label="Participer", emoji="🎉", style=discord.ButtonStyle.success,
            custom_id=f"gw_join_{gw_id}",
        )
        join_btn.callback = self.join
        self.add_item(join_btn)

        list_btn = discord.ui.Button(
            label="Participants", emoji="👥", style=discord.ButtonStyle.secondary,
            custom_id=f"gw_list_{gw_id}",
        )
        list_btn.callback = self.show_participants
        self.add_item(list_btn)

    async def join(self, interaction: discord.Interaction):
        gw = self.cog.data["giveaways"].get(str(self.gw_id))
        if not gw or gw["status"] != "active":
            await interaction.response.send_message("Ce giveaway n'est plus actif.", ephemeral=True)
            return

        member = interaction.user
        if member.id in gw["participants"]:
            await interaction.response.send_message(
                "ℹ️ Tu participes déjà à ce giveaway.",
                view=UnsubscribeView(self.cog, self.gw_id, member.id),
                ephemeral=True,
            )
            return

        ok, reason = self.cog.check_join_eligibility(gw, member)
        if not ok:
            await interaction.response.send_message(f"❌ {reason}", ephemeral=True)
            return

        gw["participants"].append(member.id)
        self.cog.save_data()
        self.cog.schedule_embed_update(self.gw_id)
        await interaction.response.send_message("✅ Tu participes désormais au giveaway, bonne chance !", ephemeral=True)

    async def show_participants(self, interaction: discord.Interaction):
        gw = self.cog.data["giveaways"].get(str(self.gw_id))
        if not gw:
            await interaction.response.send_message("Giveaway introuvable.", ephemeral=True)
            return
        participants = gw["participants"]
        if not participants:
            desc = "*Aucun participant pour le moment.*"
        else:
            desc = "\n".join(f"{i+1}. <@{uid}>" for i, uid in enumerate(participants))
            if len(desc) > 3900:
                desc = desc[:3900] + "\n*(liste tronquée)*"
        e = discord.Embed(
            title=f"👥 Participants — {gw['name']} ({len(participants)})",
            description=desc,
            color=EMBED_COLOR_RUNNING,
        )
        await interaction.response.send_message(embed=e, ephemeral=True)


# ============================================================================
# COG PRINCIPAL
# ============================================================================

class GiveawayCog(commands.Cog, name="Giveaway"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Valeur temporaire tant que cog_load() (async) n'a pas encore rechargé
        # depuis Turso. __init__ est synchrone : impossible d'attendre Turso ici.
        self.data = self._load_local_fallback()
        self.invites_cache: dict[int, dict[str, int]] = {}
        # Gestion de la mise à jour "temps réel" de l'embed (avec anti-rate-limit)
        self._update_tasks: dict[int, asyncio.Task] = {}
        self._dirty: set[int] = set()

    async def cog_load(self):
        # Rechargement depuis Turso, seule source de vérité durable.
        # AVANT : self.data venait uniquement de giveaways_data.json, un
        # fichier sur le disque éphémère de Railway. Ce fichier disparaissait
        # à chaque redéploiement/redémarrage, effaçant tous les giveaways
        # (actifs comme terminés). Turso survit aux redémarrages.
        from data import kv_store
        await kv_store.init_kv_table()
        stored = await kv_store.kv_get(_KV_NAMESPACE, _KV_KEY, None)
        if stored is not None:
            self.data = stored
        self.check_loop.start()

    def cog_unload(self):
        self.check_loop.cancel()
        for task in self._update_tasks.values():
            task.cancel()
        self.save_data()

    # ------------------------------------------------------------------
    # Persistance
    # ------------------------------------------------------------------

    def _load_local_fallback(self) -> dict:
        """Filet de sécurité synchrone pour __init__ uniquement (avant que
        cog_load() ait pu recharger depuis Turso). Ne doit jamais être la
        source de vérité en production."""
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {"_counter": 0, "giveaways": {}}

    def save_data(self):
        # Écriture atomique locale (best-effort, survit le temps du process)
        # + sauvegarde persistante vers Turso en tâche de fond. Turso est la
        # seule des deux à survivre à un redéploiement Railway.
        tmp_file = DATA_FILE + ".tmp"
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_file, DATA_FILE)
        except OSError:
            pass

        from data import kv_store

        async def _persist():
            try:
                await kv_store.kv_set(_KV_NAMESPACE, _KV_KEY, self.data)
            except Exception as exc:
                print(f"[Giveaway] Échec de sauvegarde Turso : {exc}")

        try:
            asyncio.get_running_loop()
            asyncio.create_task(_persist())
        except RuntimeError:
            pass

    # ------------------------------------------------------------------
    # Cycle de vie / listeners
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_ready(self):
        # Réenregistrer les vues persistantes pour les giveaways encore actifs
        for gw in self.data["giveaways"].values():
            if gw["status"] == "active":
                self.bot.add_view(GiveawayView(self, gw["id"]))

        # Mettre en cache les invitations de chaque serveur pour le tracking
        for guild in self.bot.guilds:
            try:
                invites = await guild.invites()
                self.invites_cache[guild.id] = {inv.code: inv.uses or 0 for inv in invites}
            except discord.Forbidden:
                self.invites_cache[guild.id] = {}

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        self.invites_cache.setdefault(invite.guild.id, {})[invite.code] = invite.uses or 0

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        self.invites_cache.setdefault(invite.guild.id, {}).pop(invite.code, None)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        inviter_id = None
        try:
            new_invites = await guild.invites()
        except discord.Forbidden:
            return
        old_cache = self.invites_cache.get(guild.id, {})
        new_cache = {inv.code: inv.uses or 0 for inv in new_invites}
        for inv in new_invites:
            if new_cache.get(inv.code, 0) > old_cache.get(inv.code, 0):
                inviter_id = inv.inviter.id if inv.inviter else None
                break
        self.invites_cache[guild.id] = new_cache

        if inviter_id is None:
            return

        changed = False
        for gw in self.data["giveaways"].values():
            if gw["status"] != "active" or gw["guild_id"] != guild.id:
                continue
            if gw["conditions"].get("min_invites"):
                gw["invite_counts"][str(inviter_id)] = gw["invite_counts"].get(str(inviter_id), 0) + 1
                changed = True
        if changed:
            self.save_data()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        changed = False
        for gw in self.data["giveaways"].values():
            if gw["status"] != "active" or gw["guild_id"] != message.guild.id:
                continue
            if gw["conditions"].get("min_messages"):
                uid = str(message.author.id)
                gw["message_counts"][uid] = gw["message_counts"].get(uid, 0) + 1
                changed = True
        if changed:
            self.save_data()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot:
            return
        now = time.time()
        changed = False
        was_connected = before.channel is not None
        is_connected = after.channel is not None
        if was_connected == is_connected:
            return  # changement de salon vocal uniquement, pas de transition connecté/déconnecté

        for gw in self.data["giveaways"].values():
            if gw["status"] != "active" or gw["guild_id"] != member.guild.id:
                continue
            if not gw["conditions"].get("min_voice_time"):
                continue
            uid = str(member.id)
            sessions = gw.setdefault("voice_sessions", {})
            if is_connected and not was_connected:
                sessions[uid] = now
                changed = True
            elif was_connected and not is_connected:
                start = sessions.pop(uid, None)
                if start:
                    gw["voice_time"][uid] = gw["voice_time"].get(uid, 0) + (now - start)
                    changed = True
        if changed:
            self.save_data()

    @tasks.loop(seconds=15)
    async def check_loop(self):
        now = time.time()
        # IMPORTANT : on itère sur une copie (list(...)) et non directement sur
        # self.data["giveaways"].values(). Un giveaway peut être créé (nouvelle
        # clé insérée dans le dict) pendant que cette boucle tourne (ex: un
        # membre lance `d!giveaway` en même temps), ce qui provoquait un
        # RuntimeError("dictionary changed size during iteration"). Cette
        # exception, non rattrapée, faisait mourir silencieusement la tâche
        # @tasks.loop pour toujours : plus aucun giveaway ne se terminait
        # jamais après ça, sans aucune erreur visible. C'était la cause
        # principale du bug.
        snapshot = list(self.data["giveaways"].values())
        to_end = [gw for gw in snapshot if gw["status"] == "active" and gw["end_time"] <= now]
        for gw in to_end:
            try:
                await self.end_giveaway(gw["id"])
            except Exception as exc:
                print(f"[Giveaway] Erreur lors de la clôture du giveaway {gw['id']}: {exc}")

    @check_loop.before_loop
    async def before_check_loop(self):
        await self.bot.wait_until_ready()

    @check_loop.error
    async def check_loop_error(self, error: BaseException):
        # Filet de sécurité ultime : si malgré tout une exception inattendue
        # s'échappe de check_loop, discord.ext.tasks arrête la boucle pour de
        # bon et ne la relance jamais tout seul. On logue et on la relance
        # nous-même pour ne plus jamais rester bloqué avec des giveaways qui
        # ne se terminent plus.
        print(f"[Giveaway] check_loop a levé une exception non gérée, relance automatique : {error}")
        if not self.check_loop.is_running():
            self.check_loop.start()

    # ------------------------------------------------------------------
    # Logique métier
    # ------------------------------------------------------------------

    def get_current_voice_time(self, gw: dict, user_id: int) -> float:
        uid = str(user_id)
        total = gw["voice_time"].get(uid, 0)
        start = gw.get("voice_sessions", {}).get(uid)
        if start:
            total += time.time() - start
        return total

    def check_join_eligibility(self, gw: dict, member: discord.Member) -> tuple[bool, str]:
        """Vérifie les rôles autorisés/interdits au moment de l'inscription."""
        role_ids = {r.id for r in member.roles}
        required = gw.get("required_roles", [])
        forbidden = gw.get("forbidden_roles", [])
        if required and not set(required).issubset(role_ids):
            return False, "Tu ne possèdes pas le ou les rôles requis pour participer à ce giveaway."
        if forbidden and (set(forbidden) & role_ids):
            return False, "Tu possèdes un rôle qui t'empêche de participer à ce giveaway."
        return True, ""

    def check_win_conditions(self, gw: dict, guild: discord.Guild, user_id: int) -> bool:
        """Vérifie les conditions de victoire (vocal, messages, invites, temps vocal)."""
        cond = gw["conditions"]
        member = guild.get_member(user_id)

        if cond.get("voice_present"):
            if member is None or member.voice is None or member.voice.channel is None:
                return False

        if cond.get("min_messages"):
            if gw["message_counts"].get(str(user_id), 0) < cond["min_messages"]:
                return False

        if cond.get("min_invites"):
            if gw["invite_counts"].get(str(user_id), 0) < cond["min_invites"]:
                return False

        if cond.get("min_voice_time"):
            if self.get_current_voice_time(gw, user_id) < cond["min_voice_time"]:
                return False

        return True

    def pick_winners(self, gw: dict, guild: discord.Guild, exclude: Optional[set] = None) -> list[int]:
        exclude = exclude or set()
        pool = [uid for uid in gw["participants"] if uid not in exclude]
        random.shuffle(pool)

        winners: list[int] = []
        needed = gw["winners_count"]

        forced = gw.get("forced_winner")
        if forced and forced not in exclude:
            winners.append(forced)
            if forced in pool:
                pool.remove(forced)

        for uid in pool:
            if len(winners) >= needed:
                break
            if self.check_win_conditions(gw, guild, uid):
                winners.append(uid)

        return winners

    async def refresh_giveaway_message(self, gw: dict):
        """Réédite l'embed du message public du giveaway pour refléter son état actuel
        (nombre de participants, statut, etc.) sans toucher à la vue (boutons)."""
        guild = self.bot.get_guild(gw["guild_id"])
        if not guild:
            return
        channel = guild.get_channel(gw["channel_id"])
        if not channel:
            return
        try:
            msg = await channel.fetch_message(gw["message_id"])
            await msg.edit(embed=self.build_giveaway_embed(gw))
        except (discord.NotFound, discord.HTTPException):
            pass

    def schedule_embed_update(self, gw_id: int):
        """Planifie une mise à jour 'temps réel' de l'embed, en évitant de spammer
        l'API Discord si plusieurs membres rejoignent/quittent en même temps
        (coalescing : une édition immédiate, puis au plus une par fenêtre de 1.5s)."""
        existing = self._update_tasks.get(gw_id)
        if existing and not existing.done():
            self._dirty.add(gw_id)
            return
        self._update_tasks[gw_id] = asyncio.create_task(self._debounced_update(gw_id))

    async def _debounced_update(self, gw_id: int):
        try:
            while True:
                self._dirty.discard(gw_id)
                gw = self.data["giveaways"].get(str(gw_id))
                if gw:
                    await self.refresh_giveaway_message(gw)
                await asyncio.sleep(1.5)
                if gw_id not in self._dirty:
                    break
        finally:
            self._update_tasks.pop(gw_id, None)

    async def create_giveaway(self, channel: discord.abc.Messageable, host: discord.Member, config: dict) -> dict:
        gw_id = new_id_counter(self.data)
        end_time = time.time() + config["duration_seconds"]

        gw = {
            "id": gw_id,
            "guild_id": host.guild.id,
            "channel_id": channel.id,
            "message_id": None,
            "host_id": host.id,
            "name": config["name"],
            "description": config["description"],
            "reward": config["reward"],
            "winners_count": config["winners_count"],
            "duration_seconds": config["duration_seconds"],
            "end_time": end_time,
            "required_roles": config["required_roles"],
            "forbidden_roles": config["forbidden_roles"],
            "conditions": config["conditions"],
            "participants": [],
            "message_counts": {},
            "invite_counts": {},
            "voice_time": {},
            "voice_sessions": {},
            "status": "active",
            "forced_winner": None,
            "winners": [],
            "created_at": time.time(),
        }

        # Pour la condition de temps vocal : initialiser les sessions des membres déjà en vocal
        if gw["conditions"].get("min_voice_time"):
            for vc in host.guild.voice_channels:
                for m in vc.members:
                    if not m.bot:
                        gw["voice_sessions"][str(m.id)] = time.time()

        self.data["giveaways"][str(gw_id)] = gw

        view = GiveawayView(self, gw_id)
        embed = self.build_giveaway_embed(gw)
        msg = await channel.send(embed=embed, view=view)
        gw["message_id"] = msg.id
        self.save_data()
        return gw

    def build_giveaway_embed(self, gw: dict) -> discord.Embed:
        status = gw["status"]
        color = {
            "active": EMBED_COLOR_RUNNING,
            "ended": EMBED_COLOR_ENDED,
            "cancelled": EMBED_COLOR_CANCELLED,
        }.get(status, EMBED_COLOR_RUNNING)

        title_prefix = {"active": "🎉", "ended": "🏁", "cancelled": "❌"}.get(status, "🎉")
        e = discord.Embed(
            title=f"{title_prefix} {gw['name']}",
            description=gw["description"] or None,
            color=color,
        )
        e.add_field(name="🎁 Récompense", value=gw["reward"], inline=True)
        e.add_field(name="🏆 Gagnant(s)", value=str(gw["winners_count"]), inline=True)

        if status == "active":
            e.add_field(name="⏰ Fin", value=f"<t:{int(gw['end_time'])}:R>", inline=True)
        else:
            e.add_field(name="⏰ Statut", value="Terminé" if status == "ended" else "Annulé", inline=True)

        req = gw.get("required_roles", [])
        forb = gw.get("forbidden_roles", [])
        if req:
            e.add_field(name="✅ Rôle(s) requis", value=", ".join(f"<@&{r}>" for r in req), inline=False)
        if forb:
            e.add_field(name="🚫 Rôle(s) interdits", value=", ".join(f"<@&{r}>" for r in forb), inline=False)

        cond = gw["conditions"]
        lines = []
        if cond.get("voice_present"):
            lines.append("🎙️ Être en vocal à la fin du giveaway")
        if cond.get("min_messages"):
            lines.append(f"💬 Au moins **{cond['min_messages']}** messages envoyés")
        if cond.get("min_invites"):
            lines.append(f"📨 Au moins **{cond['min_invites']}** invitations")
        if cond.get("min_voice_time"):
            lines.append(f"⏱️ Au moins **{format_duration(cond['min_voice_time'])}** en vocal")
        if lines:
            e.add_field(name="⚙️ Conditions pour gagner", value="\n".join(lines), inline=False)

        e.add_field(name="👥 Participants", value=str(len(gw["participants"])), inline=True)
        e.set_footer(text=f"ID giveaway : {gw['id']} • Organisé par")
        e.timestamp = discord.utils.utcnow()

        if status == "ended" and gw.get("winners"):
            winners_txt = ", ".join(f"<@{uid}>" for uid in gw["winners"])
            e.add_field(name="🏆 Gagnant(s)", value=winners_txt, inline=False)
        elif status == "ended":
            e.add_field(name="🏆 Gagnant(s)", value="Aucun participant ne remplissait les conditions.", inline=False)

        return e

    async def end_giveaway(self, gw_id: int, announce: bool = True) -> Optional[dict]:
        gw = self.data["giveaways"].get(str(gw_id))
        if not gw or gw["status"] != "active":
            return None

        guild = self.bot.get_guild(gw["guild_id"])
        if guild is None:
            # Le cache du serveur n'est pas encore disponible (ex: juste après un
            # redémarrage/reconnexion). On NE clôture PAS maintenant : on retente
            # au prochain passage de check_loop (15s plus tard) pour éviter de
            # figer le giveaway en "ended" sans gagnants et sans jamais éditer
            # le message public (c'était la cause du bug "le gw ne se termine
            # pas correctement à la fin du temps").
            print(f"[Giveaway] Serveur {gw['guild_id']} introuvable pour le giveaway {gw_id}, nouvelle tentative au prochain cycle.")
            return None

        channel = guild.get_channel(gw["channel_id"])
        if channel is None:
            try:
                channel = await guild.fetch_channel(gw["channel_id"])
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                channel = None

        # clôturer les sessions vocales en cours pour figer le temps vocal
        if gw["conditions"].get("min_voice_time"):
            now = time.time()
            for uid, start in list(gw.get("voice_sessions", {}).items()):
                gw["voice_time"][uid] = gw["voice_time"].get(uid, 0) + (now - start)
            gw["voice_sessions"] = {}

        winners = self.pick_winners(gw, guild)
        gw["winners"] = winners
        gw["status"] = "ended"
        gw["forced_winner"] = None
        self.save_data()

        # Annuler toute mise à jour "temps réel" encore en attente pour ce giveaway,
        # afin qu'elle n'écrase pas l'embed final (statut/gagnants) juste après.
        self._dirty.discard(gw_id)
        task = self._update_tasks.pop(gw_id, None)
        if task and not task.done():
            task.cancel()

        if channel:
            embed = self.build_giveaway_embed(gw)
            try:
                msg = await channel.fetch_message(gw["message_id"])
                view = GiveawayView(self, gw_id)
                for item in view.children:
                    item.disabled = True
                await msg.edit(embed=embed, view=view)
            except (discord.NotFound, discord.HTTPException):
                pass

            if announce:
                if winners:
                    mentions = ", ".join(f"<@{uid}>" for uid in winners)
                    await channel.send(
                        f"🎉 Félicitations {mentions} ! Tu remportes **{gw['reward']}** au giveaway **{gw['name']}** !"
                    )
                else:
                    await channel.send(
                        f"😕 Aucun gagnant valide n'a été trouvé pour le giveaway **{gw['name']}** "
                        f"(aucun participant éligible ne remplissait les conditions)."
                    )

        return gw

    # ------------------------------------------------------------------
    # Commandes
    # ------------------------------------------------------------------

    @commands.command(name="giveaway", aliases=["gw"])
    @commands.guild_only()
    async def giveaway(self, ctx: commands.Context):
        """Ouvre le panneau de configuration d'un nouveau giveaway."""
        panel = ConfigPanelView(self, ctx.author, ctx.channel)
        msg = await ctx.send(embed=panel.build_embed(), view=panel)
        panel.message = msg

    @commands.command(name="gwcancel", aliases=["gwc"])
    @commands.guild_only()
    async def gwcancel(self, ctx: commands.Context, gw_id: int):
        """Annule un giveaway actif sans désigner de gagnant."""
        gw = self.data["giveaways"].get(str(gw_id))
        if not gw or gw["guild_id"] != ctx.guild.id:
            await ctx.send("❌ Giveaway introuvable sur ce serveur.")
            return
        if gw["status"] != "active":
            await ctx.send("❌ Ce giveaway n'est pas actif.")
            return

        gw["status"] = "cancelled"
        gw["voice_sessions"] = {}
        self.save_data()

        self._dirty.discard(gw_id)
        task = self._update_tasks.pop(gw_id, None)
        if task and not task.done():
            task.cancel()

        channel = ctx.guild.get_channel(gw["channel_id"])
        if channel:
            try:
                msg = await channel.fetch_message(gw["message_id"])
                view = GiveawayView(self, gw_id)
                for item in view.children:
                    item.disabled = True
                await msg.edit(embed=self.build_giveaway_embed(gw), view=view)
            except (discord.NotFound, discord.HTTPException):
                pass

        await ctx.send(f"✅ Le giveaway **{gw['name']}** (ID `{gw_id}`) a été annulé.")

    @commands.command(name="gwend")
    @commands.guild_only()
    async def gwend(self, ctx: commands.Context, gw_id: int):
        """Termine immédiatement un giveaway actif."""
        gw = self.data["giveaways"].get(str(gw_id))
        if not gw or gw["guild_id"] != ctx.guild.id:
            await ctx.send("❌ Giveaway introuvable sur ce serveur.")
            return
        if gw["status"] != "active":
            await ctx.send("❌ Ce giveaway n'est pas actif.")
            return

        result = await self.end_giveaway(gw_id)
        if result:
            await ctx.send(f"✅ Le giveaway **{result['name']}** (ID `{gw_id}`) a été terminé manuellement.")

    @commands.command(name="gwreroll", aliases=["gwr"])
    @commands.guild_only()
    async def gwreroll(self, ctx: commands.Context, gw_id: int, nombre: int = 1):
        """Retire au sort de(s) nouveau(x) gagnant(s) pour un giveaway déjà terminé."""
        gw = self.data["giveaways"].get(str(gw_id))
        if not gw or gw["guild_id"] != ctx.guild.id:
            await ctx.send("❌ Giveaway introuvable sur ce serveur.")
            return
        if gw["status"] != "ended":
            await ctx.send("❌ Ce giveaway doit être terminé avant de pouvoir effectuer un reroll.")
            return
        if nombre < 1:
            await ctx.send("❌ Le nombre de gagnants à retirer au sort doit être ≥ 1.")
            return

        original_count = gw["winners_count"]
        gw["winners_count"] = nombre
        new_winners = self.pick_winners(gw, ctx.guild, exclude=set(gw.get("winners", [])))
        gw["winners_count"] = original_count

        if not new_winners:
            await ctx.send("😕 Aucun participant éligible supplémentaire n'a été trouvé pour le reroll.")
            return

        gw["winners"] = new_winners
        self.save_data()

        channel = ctx.guild.get_channel(gw["channel_id"])
        if channel:
            try:
                msg = await channel.fetch_message(gw["message_id"])
                await msg.edit(embed=self.build_giveaway_embed(gw))
            except (discord.NotFound, discord.HTTPException):
                pass

        mentions = ", ".join(f"<@{uid}>" for uid in new_winners)
        await ctx.send(f"🎉 Nouveau tirage pour **{gw['name']}** : félicitations {mentions} ! Tu remportes **{gw['reward']}** !")

    @commands.command(name="gwforcewin", aliases=["gwfw"], hidden=True)
    @commands.guild_only()
    async def gwforcewin(self, ctx: commands.Context, membre: discord.Member, gw_id: Optional[int] = None):
        """Force discrètement un membre à remporter un giveaway actif.

        Commande strictement réservée au propriétaire du bot, invisible pour
        tout le monde (jamais listée dans l'aide, jamais accordable via
        d!give ou d!bwl, même à quelqu'un en accès total).
        """
        import utils as _utils
        if ctx.author.id != _utils.OWNER_ID:
            return

        # Supprimer le message de commande au plus vite, en silence
        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass

        if gw_id is None:
            candidates = [
                g for g in self.data["giveaways"].values()
                if g["guild_id"] == ctx.guild.id and g["status"] == "active"
            ]
            if not candidates:
                await self._discreet_reply(ctx, "❌ Aucun giveaway actif trouvé sur ce serveur.")
                return
            if len(candidates) > 1:
                liste = "\n".join(f"• `{g['id']}` — {g['name']}" for g in candidates)
                await self._discreet_reply(
                    ctx,
                    f"⚠️ Plusieurs giveaways actifs, précise l'ID :\n{liste}",
                )
                return
            gw = candidates[0]
        else:
            gw = self.data["giveaways"].get(str(gw_id))
            if not gw or gw["guild_id"] != ctx.guild.id or gw["status"] != "active":
                await self._discreet_reply(ctx, "❌ Giveaway actif introuvable avec cet ID.")
                return

        gw["forced_winner"] = membre.id
        self.save_data()

        await self._discreet_reply(
            ctx,
            f"✅ **{membre}** remportera le giveaway **{gw['name']}** (ID `{gw['id']}`) à la fin, "
            f"sans tenir compte des conditions. Action invisible pour le reste du serveur.",
        )

    async def _discreet_reply(self, ctx: commands.Context, content: str):
        """Répond uniquement à l'exécuteur, de manière la plus discrète possible."""
        try:
            await ctx.author.send(content)
        except discord.Forbidden:
            temp = await ctx.send(content)
            await asyncio.sleep(4)
            try:
                await temp.delete()
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                pass


async def setup(bot: commands.Bot):
    await bot.add_cog(GiveawayCog(bot))
