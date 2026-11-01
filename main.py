import os
import asyncio
import discord
from discord.ext import commands

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="$", intents=intents, help_command=None)

@bot.event
async def on_ready():
    print(f"logged in as {bot.user} ({bot.user.id})")

async def main():
    async with bot:
        await bot.load_extension("bot")  # <--- This loads your bot.py file
        await bot.start(os.environ["DISCORD_TOKEN"])

asyncio.run(main())
