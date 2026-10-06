import asyncio
import logging
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s %(message)s")
log = logging.getLogger("case")

# We only use slash commands, so we do not need the message_content intent.
# Nothing in the Developer Portal needs to be enabled as a privileged intent.
intents = discord.Intents.default()

client = commands.Bot(command_prefix="!", intents=intents)


async def load_cogs():
    # every .py file in cogs/ is a feature group with its own commands
    for path in Path(__file__).parent.joinpath("cogs").glob("*.py"):
        name = path.stem
        if name.startswith("_"):
            continue
        await client.load_extension(f"cogs.{name}")
        log.info("loaded cog: %s", name)


@client.event
async def on_ready():
    log.info("CASE is online as %s (ID: %s)", client.user, client.user.id)
    log.info("connected to %s guild(s)", len(client.guilds))
    log.info("connected listeners: %s", len(client.extra_events))

    # Sync to the guild instead of globally. Global commands can take up to an
    # hour to appear, guild ones show up right away - better for a single server.
    for guild in client.guilds:
        await client.tree.sync(guild=guild)
        log.info("synced %s command(s) to %s", len(client.tree.get_commands()), guild.name)

    # drop any stale global registrations so nothing shows up twice
    if await client.tree.sync():
        log.info("cleared old global commands")

    await client.change_presence(activity=discord.Game(name="music 🎵"))


@client.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Without this, an exception inside a command is swallowed silently."""
    log.error("command error: %s: %s", type(error).__name__, error, exc_info=error.original if hasattr(error, "original") else error)
    try:
        if interaction.response.is_done():
            await interaction.followup.send("Something went wrong. Check the console for details.")
        else:
            await interaction.response.send_message("Something went wrong. Check the console for details.")
    except discord.HTTPException:
        pass


@client.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    log.error("command error in %s: %s", ctx.command, error, exc_info=error)


async def main():
    async with client:
        await load_cogs()
        await client.start(config.TOKEN)


if __name__ == "__main__":
    asyncio.run(main())