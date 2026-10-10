import asyncio
import logging
import os
from pathlib import Path

import discord
import wavelink
from aiohttp import web
from discord import app_commands
from discord.ext import commands

import config
import db
from audio_files import serve_local_audio

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s %(message)s")
log = logging.getLogger("case")



intents = discord.Intents.default()

class TangoBot(commands.Bot):
    async def setup_hook(self) -> None:
        if not config.LAVALINK_URI or not config.LAVALINK_PASSWORD:
            raise RuntimeError("LAVALINK_URI and LAVALINK_PASSWORD must be set.")

        node = wavelink.Node(
            identifier="tango-lavalink",
            uri=config.LAVALINK_URI,
            password=config.LAVALINK_PASSWORD,
            retries=2,
        )
        connected = await wavelink.Pool.connect(nodes=[node], client=self)
        if node.identifier not in connected:
            raise RuntimeError(
                "Could not connect to Lavalink. Check LAVALINK_URI, the shared password, "
                "and the Lavalink service logs."
            )
        log.info("Lavalink node connected: %s", node.identifier)


client = TangoBot(command_prefix="!", intents=intents)


async def start_health_server() -> web.AppRunner | None:
    """Serve Render health checks and short-lived local audio URLs."""
    if not config.PORT:
        return

    async def health(_request: web.Request) -> web.Response:
        return web.Response(text="Tango Music is online")

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_get("/local-audio/{asset}", serve_local_audio)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(config.PORT))
    await site.start()
    log.info("health server listening on port %s", config.PORT)
    return runner


async def load_cogs():

    for path in Path(__file__).parent.joinpath("cogs").glob("*.py"):
        name = path.stem
        if name.startswith("_"):
            continue
        await client.load_extension(f"cogs.{name}")
        log.info("loaded cog: %s", name)


@client.event
async def on_ready():
    log.info("Audira is online as %s (ID: %s)", client.user, client.user.id)
    log.info("build=%s commit=%s branch=%s", config.BUILD, config.GIT_COMMIT, config.GIT_BRANCH)
    log.info("connected to %s guild(s)", len(client.guilds))
    log.info("connected listeners: %s", len(client.extra_events))



    for guild in client.guilds:
        await client.tree.sync(guild=guild)
        log.info("synced %s command(s) to %s", len(client.tree.get_commands()), guild.name)


    if await client.tree.sync():
        log.info("cleared old global commands")

    await client.change_presence(activity=discord.Game(name="music ≡ƒÄ╡"))
    log.info("play history database: %s", "on" if db.available() else "off")


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
    health_runner = None
    try:
        async with client:
            await load_cogs()


            health_runner = await start_health_server()
            await db.connect(config.DATABASE_URL)
            await client.start(config.TOKEN)
    finally:
        if health_runner is not None:
            await health_runner.cleanup()
        await wavelink.Pool.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
