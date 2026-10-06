import discord
from discord.ext import commands


class General(commands.Cog):
    """Commands that have nothing to do with music."""

    def __init__(self, bot):
        self.bot = bot

    @discord.app_commands.command(name="ping", description="Check how responsive CASE is.")
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

    @discord.app_commands.command(name="help", description="Show all CASE commands.")
    async def help(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="CASE Commands",
            colour=discord.Colour.blurple(),
        )
        embed.add_field(
            name="General",
            value="`/ping` — check latency\n`/help` — show this list",
            inline=False,
        )
        embed.add_field(
            name="Music",
            value=(
                "`/play <song>` — play by name, link, or local file\n"
                "`/pause` — pause playback\n"
                "`/resume` — resume playback\n"
                "`/skip` — jump to the next song\n"
                "`/stop` — stop and clear the queue"
            ),
            inline=False,
        )
        embed.add_field(
            name="Voice",
            value="`/join` — join your voice channel\n`/leave` — leave the voice channel",
            inline=False,
        )
        embed.set_footer(text="/play searches YouTube, or plays a matching file from music/.")
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(General(bot))