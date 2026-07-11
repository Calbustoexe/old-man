import asyncio
import logging
import os
import pathlib
import discord
from discord.ext import commands
from dotenv import load_dotenv

from data import database as db
from data import division_profiles as div_db
from data import member_profiles as member_db

load_dotenv()
BASE_DIR = pathlib.Path(__file__).parent
TOKEN = os.getenv("TOKEN")
PREFIX = os.getenv("PREFIX", "d!")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(
    command_prefix=PREFIX,
    intents=intents,
    description="Urahara dcp - RP Bleach Bot",
    help_command=None,  # d!help par défaut désactivé : il cassait tout, remplacé par d!rh
)


_ready_once = False


@bot.event
async def on_ready():
    global _ready_once
    print(f"Connected as {bot.user} (ID: {bot.user.id})")
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="Yoruichi"))

    if _ready_once:
        # on_ready peut se redéclencher après une reconnexion réseau (normal sur
        # Railway) : on ne refait ni l'init DB ni le sync global à chaque fois.
        return
    _ready_once = True

    await div_db.init_profiles_tables()
    print("Division profiles table initialized.")

    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s) globally.")
    except Exception as e:
        print(f"Failed to sync slash commands: {e}")


# NOTE: la commande "ping" a été retirée d'ici (doublon) — elle vit désormais
# uniquement dans cogs/fun.py (embed avec couleur selon la latence).

@bot.command(name="sync")
@commands.is_owner()
async def sync_here(ctx: commands.Context):
    """Force un sync global immédiat (dev only, propriétaire du bot).
    N'utilise JAMAIS copy_global_to ici : ça crée des doublons guild+global."""
    synced = await bot.tree.sync()
    await ctx.send(f"✅ {len(synced)} commande(s) synchronisée(s) globalement.")


@bot.command(name="cleanup_guild_commands")
@commands.is_owner()
async def cleanup_guild_commands(ctx: commands.Context):
    """À lancer UNE SEULE FOIS pour supprimer les commandes locales en double
    créées par un ancien copy_global_to(guild). Les commandes globales restent."""
    bot.tree.clear_commands(guild=ctx.guild)
    await bot.tree.sync(guild=ctx.guild)
    await ctx.send("✅ Commandes locales en double supprimées. Les commandes globales restent actives.")


async def load_cogs():
    cogs_dir = BASE_DIR / "cogs"
    if not cogs_dir.exists():
        return
    for file in cogs_dir.iterdir():
        if file.suffix == ".py" and not file.name.startswith("_"):
            module = f"cogs.{file.stem}"
            try:
                await bot.load_extension(module)
                print(f"Loaded cog: {module}")
            except Exception as e:
                print(f"Failed to load {module}: {e}")


async def main():
    await db.init_db()
    print("Database initialized.")
    
    # Initialiser les profils de division
    await div_db.init_profiles_tables()
    print("Division profiles table initialized.")
    
    # Initialiser les profils de membres
    await member_db.ensure_member_profile_table()
    print("Member profiles table initialized.")

    # Précharger depuis Turso tout ce que utils.py sert en synchrone aux cogs
    # (permissions/catégories, fun_settings, vocban, vocprotect, ownerban,
    # tools_settings, nickfix, warns). SANS ÇA, les cogs repartiraient à vide
    # à chaque redémarrage : c'était la cause du bug "cpanel perd ses
    # catégories au reboot" (et pareil pour warns, vocban, etc.), le disque
    # Railway étant éphémère. DOIT être appelé avant load_cogs() puisque
    # certains cogs lisent leurs réglages au moment même de l'import du module.
    import utils
    await utils.bootstrap_persistence()
    print("Persistence cache (Turso) preloaded.")

    await load_cogs()
    if not TOKEN:
        print("TOKEN not found. Please create a .env file with TOKEN=your_token_here")
        return
    try:
        await bot.start(TOKEN)
    finally:
        from data import db_conn
        await db_conn.close_shared_client()


if __name__ == "__main__":
    asyncio.run(main())
