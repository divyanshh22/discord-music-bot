import platform
import sys

import discord
from discord.ext import commands
import yt_dlp

import config


class General(commands.Cog):
    """Commands that have nothing to do with music."""

    def __init__(self, bot):
        self.bot = bot

    @discord.app_commands.command(name="ping", description="Check how responsive Audira is.")
    async def ping(self, interaction: discord.Interaction):
        latency = round(self.bot.latency * 1000)
        colour = discord.Colour.green() if latency < 200 else discord.Colour.red()
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Pong!",
                description=f"Latency: {latency}ms",
                colour=colour,
            )
        )

    @discord.app_commands.command(name="version", description="Show which build is running.")
    async def version(self, interaction: discord.Interaction):
        embed = discord.Embed(title="Audira build", colour=discord.Colour.blurple())
        embed.add_field(name="Build", value=config.BUILD, inline=False)
        embed.add_field(
            name="Commit",
            value=f"`{config.GIT_COMMIT[:12]}` on `{config.GIT_BRANCH}`",
            inline=False,
        )
        embed.add_field(
            name="Runtime",
            value=(
                f"Python {platform.python_version()} · discord.py {discord.__version__} · "
                f"yt-dlp {yt_dlp.version.__version__}"
            ),
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.app_commands.command(name="help", description="Show all Audira commands.")
    async def help(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="Audira Commands",
            colour=discord.Colour.blurple(),
        )
        embed.add_field(
            name="General",
            value="`/ping` — check latency\n`/help` — show this list\n`/version` — show the running build",
            inline=False,
        )
        embed.add_field(
            name="Playback",
            value=(
                "`/play <song>` — play by name, link, or local file\n"
                "`/search <query>` — pick from a list before playing\n"
                "`/pause` • `/resume` • `/skip` • `/stop`\n"
                "`/seek 1:30` — jump inside the current song\n"
                "`/lyrics` — lyrics for what's playing (or any search)"
            ),
            inline=False,
        )
        embed.add_field(
            name="Queue",
            value=(
                "`/queue` — show what's up next\n"
                "`/shuffle` — randomise the queue\n"
                "`/remove <n>` • `/clear` • `/jump <n>`\n"
                "`/loop off|song|queue` — repeat\n"
                "`/autoplay` — keep playing similar songs\n"
                "`/nowplaying` — current song with progress\n"
                "`/history` — recently played songs"
            ),
            inline=False,
        )
        embed.add_field(
            name="Voice",
            value="`/join` — join your voice channel\n`/leave` — leave the voice channel",
            inline=False,
        )
        embed.set_footer(
            text="/play searches JioSaavn or plays a matching file from music/."
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(General(bot))
