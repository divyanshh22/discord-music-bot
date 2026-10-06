import discord
from discord.ext import commands


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

    @discord.app_commands.command(name="help", description="Show all Audira commands.")
    async def help(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="Audira Commands",
            colour=discord.Colour.blurple(),
        )
        embed.add_field(
            name="General",
            value="`/ping` ΓÇö check latency\n`/help` ΓÇö show this list",
            inline=False,
        )
        embed.add_field(
            name="Playback",
            value=(
                "`/play <song>` ΓÇö play by name, link, or local file\n"
                "`/search <query>` ΓÇö pick from a list before playing\n"
                "`/pause` ┬╖ `/resume` ┬╖ `/skip` ┬╖ `/stop`\n"
                "`/seek 1:30` ΓÇö jump inside the current song\n"
                "`/lyrics` ΓÇö lyrics for what's playing (or any search)"
            ),
            inline=False,
        )
        embed.add_field(
            name="Queue",
            value=(
                "`/queue` ΓÇö show what's up next\n"
                "`/shuffle` ΓÇö randomise the queue\n"
                "`/remove <n>` ┬╖ `/clear` ┬╖ `/jump <n>`\n"
                "`/loop off|song|queue` ΓÇö repeat\n"
                "`/autoplay` ΓÇö keep playing similar songs\n"
                "`/nowplaying` ΓÇö current song with progress\n"
                "`/history` ΓÇö recently played songs"
            ),
            inline=False,
        )
        embed.add_field(
            name="Voice",
            value="`/join` ΓÇö join your voice channel\n`/leave` ΓÇö leave the voice channel",
            inline=False,
        )
        embed.set_footer(
            text="/play searches JioSaavn, SoundCloud and YouTube, or plays a matching file from music/."
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(General(bot))
