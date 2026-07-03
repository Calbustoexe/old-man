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

bot = commands.Bot(command_prefix=PREFIX, intents=intents, description="Urahara dcp - RP Bleach Bot")


@bot.event
async def on_ready():
    print(f"Connected as {bot.user} (ID: {bot.user.id})")
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="Yoruichi"))
    
    # Initialiser la table division_profiles
    await div_db.init_profiles_tables()
    print("Division profiles table initialized.")
    
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s) globally.")
    except Exception as e:
        print(f"Failed to sync slash commands: {e}")


@bot.command()
async def ping(ctx):
    """Répond avec la latence du bot."""
    await ctx.send(f"Hoy! {round(bot.latency * 1000)} ms")


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
    
    await load_cogs()
    if not TOKEN:
        print("TOKEN not found. Please create a .env file with TOKEN=your_token_here")
        return
    await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
