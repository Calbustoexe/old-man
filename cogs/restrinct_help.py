"""
Ce fichier est désormais VIDE / désactivé.

L'ancien système d'aide restreinte (catégories figées en dur dans le code)
a été entièrement remplacé par cogs/permissions.py, qui gère :
- Le système de permissions par commande (d!give / d!ungive / d!bwl / d!unbwl)
- Les catégories de commandes personnalisées (d!ccreate, d!cpanel, etc.)
- La commande d!rh, désormais dynamique et adaptée à ce que chaque membre
  peut réellement utiliser.

Ce fichier est conservé (avec un cog vide) uniquement pour éviter une erreur
si un ancien import y faisait référence quelque part. Il peut être supprimé
sans risque du dossier cogs/ si tu préfères.
"""
from discord.ext import commands


async def setup(bot: commands.Bot):
    # Rien à charger : tout est géré par cogs/permissions.py
    pass