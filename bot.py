import os
import discord

print("STARTING...")
token = os.environ.get("DISCORD_TOKEN")
if not token:
    print("ERROR: TOKEN IS MISSING")
else:
    print("TOKEN FOUND")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = discord.Client(intents=intents)

@bot.event
async def on_ready():
    print(f"LOGGED IN AS {bot.user}")

bot.run(token)
