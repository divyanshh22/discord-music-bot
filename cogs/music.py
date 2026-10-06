from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import shutil
import time
from pathlib import Path
from urllib.parse import urlparse

import discord
from discord.ext import commands
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

import config

log = logging.getLogger("case")

AUDIO_EXTENSIONS = (".mp3", ".wav", ".ogg", ".m4a", ".flac")

# Where FFmpeg usually lands on Windows, checked when it isn't on PATH yet.
FFMPEG_FALLBACK_PATHS = (
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links",
    Path("C:/ffmpeg/bin"),
    Path("C:/Program Files/ffmpeg/bin"),
    Path("C:/ProgramData/chocolatey/bin"),
    # heroku-buildpack-ffmpeg-static unpacks here on a Linux dyno
    Path("/app/vendor/ffmpeg/bin"),
)

FFMPEG_MISSING = (
    "FFmpeg isn't installed, so I can't play audio. "
    "Install FFmpeg and restart CASE."
)


def find_ffmpeg() -> str | None:
    """Locate the ffmpeg binary, or None if it isn't installed.

    FFmpeg is an external program, not a Python package. A terminal opened
    before FFmpeg was installed keeps the old PATH, so we also check the
    usual install locations.
    """
    found = shutil.which("ffmpeg")
    if found:
        return found

    for folder in FFMPEG_FALLBACK_PATHS:
        candidate = Path(folder, "ffmpeg.exe")
        if candidate.exists():
            return str(candidate)

    return None


def ffmpeg_available() -> bool:
    return find_ffmpeg() is not None


def find_node() -> str | None:
    """Locate node.js, which yt-dlp needs to unlock YouTube's signatures."""
    found = shutil.which("node")
    if found:
        return found

    for folder in (
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "nodejs",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "nodejs",
        Path("C:/Program Files/nodejs"),
    ):
        candidate = Path(folder, "node.exe")
        if candidate.exists():
            return str(candidate)

    return None


def normalize(text: str) -> str:
    """Lowercase and collapse spaces/underscores so queries match filenames."""
    text = Path(text.strip()).stem.lower()
    return re.sub(r"[_\s]+", " ", text).strip()


def is_url(text: str) -> bool:
    parsed = urlparse(text)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


# Sites that never hand over playable audio - either they only expose
# metadata through an API, or the stream is DRM-encrypted. yt-dlp refuses
# these with a long traceback nobody wants to read, so we catch them first.
UNSUPPORTED_DOMAINS = {
    "spotify.com": (
        "Spotify can't be played directly - copy the song name "
        "and search for that instead."
    ),
    "music.apple.com": (
        "Apple Music audio is DRM-protected - copy the song name "
        "and search for that instead."
    ),
    "tidal.com": (
        "Tidal audio is DRM-protected - copy the song name "
        "and search for that instead."
    ),
    "deezer.com": (
        "Deezer audio is DRM-protected - copy the song name "
        "and search for that instead."
    ),
    "music.amazon.com": (
        "Amazon Music is DRM-protected - copy the song name "
        "and search for that instead."
    ),
}


def unsupported_reason(text: str) -> str | None:
    """A chat-friendly explanation for links CASE can never play, else None."""
    if not is_url(text):
        return None
    host = urlparse(text).netloc.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    for domain, reason in UNSUPPORTED_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return reason
    return None


def find_song(query: str) -> Path | None:
    """Look for a local file in music/ matching the query.

    Tries an exact name match first, then a partial match. Spaces, underscores
    and a trailing .mp3 are all treated the same, so "shape of you",
    "shape_of_you" and "Shape of You.mp3" all find shape_of_you.mp3.
    """
    query = normalize(query)
    if not query:
        return None

    files = [p for p in config.MUSIC_DIR.iterdir() if p.suffix.lower() in AUDIO_EXTENSIONS]
    if not files:
        return None

    for path in files:
        if normalize(path.stem) == query:
            return path

    for path in files:
        if query in normalize(path.stem):
            return path

    return None


class Track:
    """One item in the queue.

    A track is either a file in music/ or a stream resolved by yt-dlp.
    `source` is what gets handed to FFmpeg: a path for local files, a URL for
    streams. The player only ever reads `.source`, so both kinds play the
    same way and nothing downstream needs to know where the audio came from.
    """

    def __init__(
        self,
        source: str,
        title: str,
        requested_by: str = "someone",
        duration: int | None = None,
        webpage_url: str | None = None,
        headers: dict[str, str] | None = None,
    ):
        self.source = source
        self.title = title
        self.requested_by = requested_by
        self.duration = duration
        self.webpage_url = webpage_url
        # HTTP headers yt-dlp says go with this stream. FFmpeg needs them too,
        # otherwise the CDN answers 403 because the request looks automated.
        self.headers = headers or {}

    @classmethod
    def from_file(cls, path: Path, requested_by: str = "someone") -> Track:
        return cls(
            source=str(path),
            title=normalize(path.stem).title(),
            requested_by=requested_by,
        )

    @property
    def is_stream(self) -> bool:
        return self.webpage_url is not None

    def duration_label(self) -> str:
        if not self.duration:
            return "live"
        minutes, seconds = divmod(self.duration, 60)
        return f"{minutes}:{seconds:02d}"


class _YDLLogger:
    """Keeps yt-dlp's own console output out of the terminal.

    yt-dlp prints directly to stdout/stderr otherwise, which mixes ugly
    ERROR blocks into CASE's log. Route it through logging instead.
    """

    def debug(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)

    def warning(self, msg: str) -> None:
        log.warning("yt-dlp: %s", msg)

    def error(self, msg: str) -> None:
        log.error("yt-dlp: %s", msg)


YDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    # Without this, a playlist link walks every entry (~160s for a big set)
    # and the command sits at "thinking" the whole time. We only ever play
    # the first track, so stop yt-dlp there.
    "playlist_items": "1",
    "quiet": True,
    "no_warnings": True,
    "nocheckcertificate": True,
    "default_search": "ytsearch1",
    "logger": _YDLLogger(),
}

# yt-dlp needs a JavaScript runtime to unlock YouTube's signatures. Without
# one it warns on every resolve and hands back stream URLs YouTube rejects
# with HTTP 403 a moment after FFmpeg opens them.
_NODE = find_node()
if _NODE:
    YDL_OPTIONS["js_runtimes"] = {"node": {"path": _NODE}}
else:
    log.warning("node.js not found - some YouTube streams may fail with 403")

MAX_RESULTS = 5


def resolve_track(query: str, requested_by: str) -> Track:
    """Turn a /play argument into a Track.

    Local files win over the internet so you can always play your own
    versions by name. Otherwise the query goes to yt-dlp, which handles
    direct URLs (YouTube, SoundCloud, ...) as well as plain search terms.

    Blocking, so it must run in a thread.
    """
    query = query.strip()
    if not query:
        raise ValueError("empty query")

    reason = unsupported_reason(query)
    if reason:
        raise ValueError(reason)

    path = find_song(query)
    if path is not None:
        return Track.from_file(path, requested_by)

    with YoutubeDL(YDL_OPTIONS) as ydl:
        if is_url(query):
            info = ydl.extract_info(query, download=False)
            if not info:
                raise LookupError("couldn't read that link")
        else:
            info = ydl.extract_info(f"ytsearch:{MAX_RESULTS}:{query}", download=False)
            entries = [e for e in (info or {}).get("entries", []) if e]
            if not entries:
                raise LookupError("no results")
            info = ydl.process_ie_result(entries[0], download=False)

        if not info:
            raise LookupError("no results")

        if info.get("_type") == "playlist":
            entries = [e for e in info.get("entries", []) if e]
            if not entries:
                raise LookupError("empty playlist")
            info = entries[0]
            # flat entries only carry an id/title - force the real stream details
            if not info.get("url") and not info.get("formats"):
                info = ydl.process_ie_result(info, download=False)

    url = info.get("url")
    if not url:
        # some sites only hand back formats, not a resolved stream url
        formats = [f for f in info.get("formats", []) if f.get("url")]
        if not formats:
            raise LookupError("no playable stream for that track")
        url = formats[-1]["url"]

    return Track(
        source=url,
        title=info.get("title") or query,
        requested_by=requested_by,
        duration=int(info.get("duration") or 0) or None,
        webpage_url=info.get("webpage_url"),
        headers=info.get("http_headers") or {},
    )


class MusicPlayer:
    """Plays a queue of local files in one voice channel.

    One of these exists per guild while CASE is connected.
    """

    def __init__(self, voice: discord.VoiceProtocol):
        self.voice = voice
        self.queue: list[Track] = []
        self.current: Track | None = None
        self.notify_channel: discord.abc.Messageable | None = None
        self._task: asyncio.Task | None = None

    @property
    def is_playing(self) -> bool:
        return self.voice.is_playing()

    @property
    def is_paused(self) -> bool:
        return self.voice.is_paused()

    def start(self) -> None:
        """Make sure the playback loop is running. Safe to call repeatedly."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    def is_idle(self) -> bool:
        """True when nothing is playing and nothing is waiting."""
        return self.current is None and not self.queue and not self.is_playing

    def add(self, track: Track) -> None:
        """Append a track and make sure the playback loop is running."""
        self.queue.append(track)
        self.start()

    def stop(self) -> None:
        """Clear the queue and halt the playback loop. Does not disconnect."""
        self.queue.clear()
        self.current = None
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        self.voice.stop()

    async def shutdown(self) -> None:
        """Stop everything and leave the voice channel."""
        self.stop()
        if self.voice.is_connected():
            await self.voice.disconnect()

    def skip(self) -> None:
        """Drop the current song. The playback loop picks up the next one."""
        if self.voice.is_paused():
            self.voice.resume()
        self.voice.stop()

    def clear(self) -> None:
        self.queue.clear()

    async def _run(self) -> None:
        """Play each queued track until the queue runs out or we get stopped."""
        try:
            while self.queue:
                if not self.voice.is_connected():
                    log.info("voice dropped, stopping playback loop")
                    break

                track = self.queue.pop(0)
                ok = await self._play_one(track)
                if not ok and track.is_stream and self.voice.is_connected():
                    # The stream URL had likely gone stale (YouTube answers 403
                    # once a link ages or gets reused). Resolve it again and
                    # try once more before telling the user it failed.
                    log.info("retrying %s with a fresh stream URL", track.title)
                    try:
                        fresh = await asyncio.to_thread(
                            resolve_track, track.webpage_url or track.title, track.requested_by
                        )
                    except (DownloadError, LookupError, ValueError) as exc:
                        log.warning("could not re-resolve %s: %s", track.title, exc)
                        continue
                    track = fresh
                    await self._play_one(track)

                self.current = None

        except asyncio.CancelledError:
            pass
        finally:
            self.current = None
            # Leave once the queue runs dry. on_voice_state_update removes the
            # player from the cog's dict when the disconnect registers.
            if self.voice.is_connected() and not self.queue:
                await self.voice.disconnect()

    async def _play_one(self, track: Track) -> bool:
        """Start one track and wait for it to finish.

        Returns True when FFmpeg ran to the end (or was skipped), False when
        the source died early - that's when a retry is worth attempting.
        """
        self.current = track
        log.info("playing %s", track.title)

        before_options = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
        if track.headers:
            # FFmpeg's default UA gets rejected by most CDNs, so replay the
            # exact headers yt-dlp said go with this stream.
            pairs = [f"{key}: {value}" for key, value in track.headers.items()]
            header_blob = "\r\n".join(pairs) + "\r\n"
            before_options += " -headers " + shlex.quote(header_blob)

        try:
            # FFmpegOpusAudio reads a local path or pulls a URL -
            # both end up as Opus frames, so playback doesn't care.
            source = discord.FFmpegOpusAudio(
                track.source,
                executable=find_ffmpeg(),
                before_options=before_options,
            )
        except Exception:
            log.exception("ffmpeg failed for %s", track.title)
            self._notify(f"Couldn't play **{track.title}** - FFmpeg rejected that source.")
            self.current = None
            return True

        errors: list[Exception | None] = []
        try:
            self.voice.play(source, after=errors.append)
        except discord.ClientException as exc:
            # voice dropped between the check in _run and now
            log.warning("voice.play failed: %s", exc)
            self.current = None
            return False

        # poll until the track ends, a skip happens, or stop() cancels us
        while self.voice.is_playing() or self.voice.is_paused():
            await asyncio.sleep(0.5)

        # discord.py hands the after-callback its error a tick after playback
        # stops, so give it a moment before we look.
        await asyncio.sleep(0.2)

        error = errors[0] if errors else None
        if error is not None:
            log.warning("playback of %s ended early: %s", track.title, error)

        self.current = None
        return error is None

    def _notify(self, message: str) -> None:
        """Best-effort notice in the channel that started playback."""
        if self.notify_channel:
            asyncio.create_task(self.notify_channel.send(message))


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.players: dict[int, MusicPlayer] = {}

    def get_player(self, guild: discord.Guild) -> MusicPlayer | None:
        return self.players.get(guild.id)

    async def ensure_player(self, interaction: discord.Interaction) -> tuple[MusicPlayer | None, str | None]:
        """Make sure CASE is connected to the user's channel and ready to play.

        Returns (player, error). On error the player is None and the caller
        decides how to reply - defer() may already have been sent by then.
        """
        member = interaction.user.voice
        if member is None:
            return None, "You're not in a voice channel."

        if not ffmpeg_available():
            return None, FFMPEG_MISSING

        voice = interaction.guild.voice_client

        # A timed-out or dropped handshake leaves a client object that is not
        # connected. It blocks reconnects, so throw it away first.
        if voice is not None and not voice.is_connected():
            try:
                await voice.disconnect(force=True)
            except Exception:
                log.warning("could not discard a stale voice client", exc_info=True)
            voice = None

        if voice is None:
            try:
                voice = await member.channel.connect(
                    self_deaf=False, self_mute=False, timeout=30
                )
            except (asyncio.TimeoutError, OSError, discord.ClientException) as exc:
                log.warning("voice connect failed: %s", exc, exc_info=exc)
                await self._discard_voice(interaction.guild)
                return None, "I couldn't join your voice channel. Try again."
        elif voice.channel != member.channel:
            try:
                await voice.move_to(member.channel)
            except (OSError, discord.ClientException) as exc:
                log.warning("move_to failed: %s", exc, exc_info=exc)
                await self._discard_voice(interaction.guild)
                return None, "I couldn't join your voice channel. Try again."

        player = self.players.get(interaction.guild.id)
        if player is None:
            player = MusicPlayer(voice)
            player.notify_channel = interaction.channel
            self.players[interaction.guild.id] = player
        else:
            # reconnecting after a drop - point the player at the live client
            player.voice = voice

        return player, None

    async def _discard_voice(self, guild: discord.Guild) -> None:
        """Drop a half-open voice client and its player so the next attempt starts clean."""
        voice = guild.voice_client
        if voice is not None:
            try:
                await voice.disconnect(force=True)
            except Exception:
                log.debug("disconnect during cleanup failed", exc_info=True)
        self.players.pop(guild.id, None)

    @discord.app_commands.command(name="join", description="Join your voice channel.")
    async def join(self, interaction: discord.Interaction):
        await interaction.response.defer()

        player, error = await self.ensure_player(interaction)
        if error:
            await interaction.followup.send(error)
            return

        await interaction.followup.send(
            f"Joined **{interaction.user.voice.channel.name}**."
        )

    @discord.app_commands.command(name="leave", description="Leave the voice channel.")
    async def leave(self, interaction: discord.Interaction):
        player = self.get_player(interaction.guild)
        if player is None:
            await interaction.response.send_message("CASE isn't connected to a voice channel.")
            return
        del self.players[interaction.guild.id]
        await player.shutdown()
        await interaction.response.send_message("Left the voice channel.")

    @discord.app_commands.command(name="play", description="Play a song by name, link, or from music/.")
    @discord.app_commands.describe(
        song="Song name, a YouTube/SoundCloud link, or a file in music/"
    )
    async def play(self, interaction: discord.Interaction, song: str):
        # Resolving a stream can take a few seconds, so acknowledge immediately.
        await interaction.response.defer(ephemeral=True)
        log.info("play: resolving %r", song)

        try:
            # yt-dlp is blocking, so keep it off the event loop. The timeout
            # stops a slow site from leaving the command at "thinking" forever.
            track = await asyncio.wait_for(
                asyncio.to_thread(resolve_track, song, interaction.user.display_name),
                timeout=45,
            )
        except asyncio.TimeoutError:
            log.warning("resolve timed out for %r", song)
            await interaction.followup.send(
                "That took too long to load. Try a direct link or a shorter search."
            )
            return
        except ValueError as exc:
            # our own message (unsupported link, bad query) - show it as-is
            log.warning("rejected %r: %s", song, exc)
            await interaction.followup.send(str(exc))
            return
        except (DownloadError, LookupError) as exc:
            # yt-dlp failures are expected, not crashes - one line, no traceback
            log.warning("could not resolve %r: %s", song, exc)
            await interaction.followup.send(
                f"I couldn't find anything for **{song}**."
            )
            return

        log.info("play: resolved %r -> %s", song, track.title)
        try:
            player, error = await asyncio.wait_for(
                self.ensure_player(interaction), timeout=40
            )
        except asyncio.TimeoutError:
            log.warning("voice connect timed out for %r", song)
            await self._discard_voice(interaction.guild)
            await interaction.followup.send(
                "CASE took too long to join the voice channel. Try again."
            )
            return
        if error:
            await interaction.followup.send(error)
            return
        log.info("play: connected, queueing")

        was_idle = player.is_idle()
        player.notify_channel = interaction.channel
        player.add(track)

        if was_idle:
            await interaction.followup.send(
                embed=self._now_playing_embed(track, player)
            )
        else:
            await interaction.followup.send(
                f"Added **{track.title}** to the queue — position {len(player.queue)}."
            )
        log.info("play: reply sent")

    @discord.app_commands.command(name="pause", description="Pause the current song.")
    async def pause(self, interaction: discord.Interaction):
        player = self.get_player(interaction.guild)
        if player is None or player.current is None:
            await interaction.response.send_message("Nothing is playing right now.")
            return
        # is_playing() is False while paused, so check paused first.
        if player.is_paused:
            await interaction.response.send_message("The music is already paused.")
            return
        if not player.is_playing:
            await interaction.response.send_message("Nothing is playing right now.")
            return
        player.voice.pause()
        await interaction.response.send_message(f"Paused **{player.current.title}**.")

    @discord.app_commands.command(name="resume", description="Resume the paused song.")
    async def resume(self, interaction: discord.Interaction):
        player = self.get_player(interaction.guild)
        if player is None:
            await interaction.response.send_message("Nothing is playing right now.")
            return
        if not player.is_paused:
            await interaction.response.send_message("The music isn't paused.")
            return
        player.voice.resume()
        await interaction.response.send_message(f"Resumed **{player.current.title}**.")

    @discord.app_commands.command(name="skip", description="Skip to the next song.")
    async def skip(self, interaction: discord.Interaction):
        player = self.get_player(interaction.guild)
        if player is None or player.current is None:
            await interaction.response.send_message("Nothing is playing right now.")
            return

        if player.queue:
            nxt = player.queue[0].title
            player.skip()
            await interaction.response.send_message(f"Skipped. Now playing **{nxt}**.")
        else:
            del self.players[interaction.guild.id]
            await player.shutdown()
            await interaction.response.send_message("Nothing left in the queue. Stopping.")

    @discord.app_commands.command(name="stop", description="Stop playback and clear the queue.")
    async def stop(self, interaction: discord.Interaction):
        player = self.get_player(interaction.guild)
        if player is None:
            await interaction.response.send_message("Nothing is playing right now.")
            return
        del self.players[interaction.guild.id]
        await player.shutdown()
        await interaction.response.send_message("Stopped. The queue was cleared.")

    @discord.app_commands.command(name="queue", description="Show the current queue.")
    async def queue(self, interaction: discord.Interaction):
        player = self.get_player(interaction.guild)
        if player is None or (player.current is None and not player.queue):
            await interaction.response.send_message("Nothing is playing right now.")
            return

        embed = discord.Embed(title="CASE Queue", colour=discord.Colour.blurple())
        embed.add_field(
            name="Now Playing",
            value=player.current.title if player.current else "—",
            inline=False,
        )

        if player.queue:
            lines = [
                f"{i}. {song.title}"
                for i, song in enumerate(player.queue[:10], start=1)
            ]
            if len(player.queue) > 10:
                lines.append(f"...and {len(player.queue) - 10} more")
            embed.add_field(name="Up Next", value="\n".join(lines), inline=False)

        await interaction.response.send_message(embed=embed)

    @discord.app_commands.command(name="nowplaying", description="Show the current song.")
    async def nowplaying(self, interaction: discord.Interaction):
        player = self.get_player(interaction.guild)
        if player is None or player.current is None:
            await interaction.response.send_message("Nothing is playing right now.")
            return
        await interaction.response.send_message(
            embed=self._now_playing_embed(player.current, player)
        )

    def _now_playing_embed(self, track: Track, player: MusicPlayer) -> discord.Embed:
        status = "Paused" if player.is_paused else "Playing"
        embed = discord.Embed(
            title=f"CASE {status}",
            description=f"Now playing **{track.title}**",
            colour=discord.Colour.blurple(),
        )
        source = "stream" if track.is_stream else "local file"
        embed.add_field(
            name="Details",
            value=f"{source} · {track.duration_label()}",
            inline=True,
        )
        embed.add_field(name="Requested by", value=track.requested_by, inline=True)
        if track.webpage_url:
            embed.url = track.webpage_url
        if player.queue:
            embed.add_field(
                name=f"Queue ({len(player.queue)})",
                value=", ".join(t.title for t in player.queue[:5]),
                inline=False,
            )
        return embed

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        # CASE got disconnected by someone (or Discord dropped us).
        # Clean up so the next /play starts fresh.
        if member.id == self.bot.user.id and after.channel is None:
            player = self.players.get(member.guild.id)
            if player:
                player.stop()
                del self.players[member.guild.id]


async def setup(bot):
    await bot.add_cog(Music(bot))