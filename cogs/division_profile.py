import sys
import pathlib

sys.path.append(str(pathlib.Path(__file__).parent.parent))
from data import division_profile as profile_mod

async def setup(bot):
    await profile_mod.setup(bot)
