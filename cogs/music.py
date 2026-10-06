from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import discord
import aiohttp
from discord.ext import commands
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

import config
import db

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


def _node_version(path: str) -> str:
    """`v22.4.0` - bgutil's script needs node 22+, so log what we actually have."""
    try:
        out = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=10
        )
        return (out.stdout or out.stderr).strip() or "unknown version"
    except Exception:
        return "unknown version"


def normalize(text: str) -> str:
    """Lowercase and collapse spaces/underscores so queries match filenames."""
    text = Path(text.strip()).stem.lower()
    return re.sub(r"[_\s]+", " ", text).strip()


def is_url(text: str) -> bool:
    parsed = urlparse(text)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def parse_timestamp(text: str) -> int:
    """Turn "90", "1:30" or "1:02:03" into seconds."""
    text = text.strip()
    if text.isdigit():
        return int(text)
    parts = text.split(":")
    if not 1 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        raise ValueError("Use seconds (90) or mm:ss (1:30).")
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds


def progress_bar(elapsed: int, total: int, width: int = 20) -> str:
    """A compact slider like `------*-------------` for embeds."""
    if not total:
        return "♪" * width
    filled = max(0, min(width, round(width * elapsed / total)))
    return "▓" * filled + "░" * (width - filled)


def short_duration(seconds: int | None) -> str:
    """`3:45` for dropdown descriptions, blank when unknown."""
    if not seconds:
        return ""
    minutes, rest = divmod(seconds, 60)
    if minutes >= 60:
        return f"{minutes // 60}:{minutes % 60:02d}:{rest:02d}"
    return f"{minutes}:{rest:02d}"


def reason_line(exc: Exception) -> str:
    """One short line for a yt-dlp failure, so the reply can show why.

    yt-dlp errors are often multi-line stacks with a URL on the first line
    and the actual problem ("Sign in to confirm you're not a bot") on the
    last, so the last non-empty line is the useful one.
    """
    lines = [line.strip() for line in str(exc).strip().splitlines() if line.strip()]
    text = type(exc).__name__
    for line in lines:
        if line.startswith("ERROR"):
            text = line
            break
    else:
        if lines:
            text = lines[-1]
    if text.startswith("ERROR:"):
        text = text[6:].strip()
    return text[:200].replace("`", "'")


LRCLIB_SEARCH = "https://lrclib.net/api/search"


async def fetch_lyrics(query: str) -> dict | None:
    """Best match for `query` from lrclib.net - free, no API key needed.

    Returns the raw result (it has plainLyrics/artistName/...) or None.
    """
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(LRCLIB_SEARCH, params={"q": query}) as resp:
            if resp.status != 200:
                log.warning("lrclib returned HTTP %s", resp.status)
                return None
            results = await resp.json()

    for item in results:
        if item.get("plainLyrics"):
            return item
    return None


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
        # Seconds to skip to when this track starts - set by /seek, consumed
        # by _play_one so the restart plays from that point.
        self.seek_offset = 0

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
        # yt-dlp hides PO-token progress in debug output, and that is exactly
        # what we need to see when YouTube starts challenging our IP.
        if "[pot" in msg or "PO Token" in msg:
            log.info("yt-dlp: %s", msg)
        else:
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
    # yt-dlp retries a failing request 10 times by default, which can eat the
    # whole command timeout on a network YouTube distrusts. Give up quickly so
    # the next player client gets a turn.
    "retries": 1,
    "fragment_retries": 1,
    "socket_timeout": 15,
    "logger": _YDLLogger(),
}

# yt-dlp needs a JavaScript runtime to unlock YouTube's signatures. Without
# one it warns on every resolve and hands back stream URLs YouTube rejects
# with HTTP 403 a moment after FFmpeg opens them.
_NODE = find_node()
if _NODE:
    YDL_OPTIONS["js_runtimes"] = {"node": {"path": _NODE}}
    log.info("node.js: %s (%s)", _NODE, _node_version(_NODE))
else:
    log.warning("node.js not found - some YouTube streams may fail with 403")

# Both are external programs, so check them once at startup instead of
# discovering a missing one when someone runs /play.
log.info("ffmpeg: %s", find_ffmpeg() or "MISSING")

# YouTube wants a proof-of-origin token from data-centre IPs, which yt-dlp
# cannot make on its own. bgutil generates one with node.js; the build clones
# and compiles it into potprovider/. On a home IP the token is never needed,
# so a missing provider is only a warning.
_POT_SCRIPT = config.BASE_DIR / "potprovider" / "server" / "build" / "generate_once.js"
if _POT_SCRIPT.exists() and _NODE:
    YDL_OPTIONS["extractor_args"] = {
        "youtubepot-bgutilscript": {"server_home": str(_POT_SCRIPT.parents[1])}
    }
    log.info("po-token provider: %s", _POT_SCRIPT.parents[1])
else:
    log.warning("po-token provider not available - YouTube may ask us to sign in")

MAX_RESULTS = 5

# YouTube treats data-centre IPs (Render, VPS, ...) as suspicious and answers
# its default browser client with "Sign in to confirm you're not a bot". The
# TV/mweb clients are checked far less strictly, so on a hosted network we
# fall back through them before giving up. On a home connection the first
# attempt wins and the rest never run.
YOUTUBE_CLIENT_ATTEMPTS: list[dict] = [
    {},
    {"extractor_args": {"youtube": {"player_client": ["tv", "web_safari"]}}},
    {"extractor_args": {"youtube": {"player_client": ["mweb", "android_vr"]}}},
]


def _merge_options(extra: dict) -> dict:
    """A copy of YDL_OPTIONS with one fallback attempt applied.

    The attempts only touch `youtube:player_client`, so their extractor_args
    are merged key-by-key - otherwise the PO-token provider configured above
    would be thrown away on every retry.
    """
    options = {**YDL_OPTIONS, **extra}
    if "extractor_args" in extra:
        options["extractor_args"] = {
            **YDL_OPTIONS.get("extractor_args", {}),
            **extra["extractor_args"],
        }
    return options


def _extract_once(options: dict, query: str) -> dict | None:
    """One yt-dlp extraction - search first, then the full video details."""
    with YoutubeDL(options) as ydl:
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

    return info


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

    info: dict | None = None
    last_error: Exception | None = None
    for attempt, extra in enumerate(YOUTUBE_CLIENT_ATTEMPTS):
        if attempt:
            log.info("retrying with client set %s", extra["extractor_args"])
        started = time.monotonic()
        try:
            info = _extract_once(_merge_options(extra), query)
            break
        except DownloadError as exc:
            # Only a rejected client is worth retrying - our own LookupError
            # ("no results") would fail identically with every client.
            last_error = exc
            log.warning(
                "client attempt %d failed after %.1fs: %s",
                attempt,
                time.monotonic() - started,
                reason_line(exc),
            )

    if info is None:
        if last_error is None:
            raise LookupError("no results")
        raise last_error

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


def search_candidates(query: str, limit: int = MAX_RESULTS) -> list[dict]:
    """Top results for a query, without resolving a stream URL yet.

    Flat extraction is fast, which matters because /search shows a picker and
    the user may take a while to choose - by then a resolved stream URL would
    have gone stale. The picked result is resolved properly at play time.
    """
    query = query.strip()
    if not query:
        raise ValueError("empty query")

    reason = unsupported_reason(query)
    if reason:
        raise ValueError(reason)

    options = dict(YDL_OPTIONS)
    options["extract_flat"] = "in_playlist"
    # /play sets this to keep playlist links short, but on a ytsearch it would
    # cap the whole result list at one - which defeats a picker.
    options.pop("playlist_items", None)

    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        entries = [e for e in (info or {}).get("entries", []) if e]

    results = []
    for entry in entries:
        url = entry.get("webpage_url") or entry.get("url")
        if not url:
            continue
        if not is_url(url):
            # flat YouTube entries hand back just the video id
            url = f"https://www.youtube.com/watch?v={url}"
        results.append(
            {
                "title": entry.get("title") or query,
                "webpage_url": url,
                "duration": int(entry.get("duration") or 0) or None,
            }
        )

    if not results:
        raise LookupError("no results")
    return results


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
        # "off" repeats nothing, "song" replays the current track, "queue"
        # pushes a finished track back onto the end of the queue.
        self.loop_mode = "off"
        self.autoplay = False
        # Monotonic timestamp of when the current track actually started, so
        # /nowplaying can draw a progress bar.
        self.started_at: float | None = None
        # Set by /seek, cleared by the playback loop so the same track is
        # queued again instead of moving on.
        self._seek_pending = False
        # Makes /skip ignore song-loop for one round so skip still advances.
        self._skip_once = False
        # Last few titles, used by autoplay to avoid instantly repeating.
        self._recent: list[str] = []

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
        self._skip_once = True
        if self.voice.is_paused():
            self.voice.resume()
        self.voice.stop()

    def clear(self) -> None:
        self.queue.clear()

    def shuffle(self) -> None:
        random.shuffle(self.queue)

    def elapsed(self) -> int:
        """Seconds played of the current track, or 0 if unknown."""
        if self.started_at is None:
            return 0
        return max(0, int(time.monotonic() - self.started_at))

    def seek(self, seconds: int) -> bool:
        """Restart the current track at `seconds`. Needs a track to seek in."""
        if self.current is None or not self.voice.is_connected():
            return False
        self.current.seek_offset = max(0, seconds)
        self._seek_pending = True
        # Stopping makes _play_one return, and the loop re-queues this track.
        self.voice.stop()
        return True

    async def _run(self) -> None:
        """Play each queued track until the queue runs out or we get stopped."""
        try:
            while self.queue:
                if not self.voice.is_connected():
                    log.info("voice dropped, stopping playback loop")
                    break

                track = self.queue.pop(0)
                ok = await self._play_one(track)

                # /seek stopped this track on purpose - play it again from
                # the new position instead of moving on.
                if self._seek_pending:
                    self._seek_pending = False
                    self.queue.insert(0, track)
                    continue

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
                    ok = await self._play_one(track)

                if ok:
                    self._apply_loop(track)
                    if not self.queue and self.autoplay:
                        await self._queue_autoplay(track)

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
        if track.seek_offset:
            # -ss before the input seeks fast instead of decoding from zero.
            before_options += f" -ss {track.seek_offset}"
            track.seek_offset = 0
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
        self.started_at = time.monotonic()
        while self.voice.is_playing() or self.voice.is_paused():
            await asyncio.sleep(0.5)

        # discord.py hands the after-callback its error a tick after playback
        # stops, so give it a moment before we look.
        await asyncio.sleep(0.2)
        self.started_at = None

        error = errors[0] if errors else None
        if error is not None:
            log.warning("playback of %s ended early: %s", track.title, error)
        else:
            # It actually played, so it is worth remembering.
            await db.log_play(
                self.voice.guild.id,
                track.title,
                channel_id=self.voice.channel.id if self.voice.channel else None,
                url=track.webpage_url,
                requested_by=track.requested_by,
                duration=track.duration,
            )

        self.current = None
        return error is None

    def _apply_loop(self, track: Track) -> None:
        """Put a finished track back in the queue when looping is on."""
        if self.loop_mode == "song" and not self._skip_once:
            self.queue.insert(0, track)
        elif self.loop_mode == "queue":
            self.queue.append(track)
        if self._skip_once:
            # /skip must move forward even with song-loop on, so ignore the
            # loop exactly once instead of trapping the user in a replay.
            self._skip_once = False

    async def _queue_autoplay(self, finished: Track) -> None:
        """Keep the music going with something similar when the queue runs dry.

        Autoplay looks for tracks like the one that just ended and skips
        anything already played recently, so it doesn't loop the same few
        songs. It also stops if the bot is left alone in the channel.
        """
        if not self.voice.is_connected():
            return
        channel = self.voice.channel
        if channel is not None and len([m for m in channel.members if not m.bot]) == 0:
            log.info("autoplay off - nobody is listening")
            return

        self._recent.append(finished.title)
        self._recent = self._recent[-15:]

        try:
            candidates = await asyncio.to_thread(
                search_candidates, finished.title, limit=5
            )
        except (DownloadError, LookupError, ValueError) as exc:
            log.warning("autoplay search failed: %s", exc)
            return

        for candidate in candidates:
            if normalize(candidate["title"]) == normalize(finished.title):
                continue
            if any(normalize(candidate["title"]) == normalize(t) for t in self._recent):
                continue
            try:
                track = await asyncio.to_thread(
                    resolve_track, candidate["webpage_url"], finished.requested_by
                )
            except (DownloadError, LookupError, ValueError) as exc:
                log.warning("autoplay could not resolve %s: %s", candidate["title"], exc)
                continue
            log.info("autoplay picked %s", track.title)
            self.queue.append(track)
            self._notify(f"Autoplay: **{track.title}**")
            return

        log.info("autoplay found nothing new to play")

    def _notify(self, message: str) -> None:
        """Best-effort notice in the channel that started playback."""
        if self.notify_channel:
            asyncio.create_task(self.notify_channel.send(message))


class SearchPicker(discord.ui.View):
    """A dropdown of search results that queues whichever one is picked.

    Search and the actual resolve are kept apart on purpose: resolving pins a
    stream URL that expires, and a human picking from a list can take a while.
    """

    def __init__(self, cog: "Music", results: list[dict]):
        super().__init__(timeout=60)
        self.cog = cog
        self.results = results
        self.message: discord.WebhookMessage | None = None

        select = discord.ui.Select(
            placeholder="Pick a song to play",
            options=[
                discord.SelectOption(
                    label=result["title"][:100],
                    description=short_duration(result["duration"]) or None,
                    value=str(i),
                )
                for i, result in enumerate(results)
            ],
        )
        select.callback = self.on_pick
        self.add_item(select)

    async def on_pick(self, interaction: discord.Interaction) -> None:
        chosen = self.results[int(interaction.data["values"][0])]
        self.stop()
        await self._tidy()
        await interaction.response.defer()

        try:
            track = await asyncio.wait_for(
                asyncio.to_thread(
                    resolve_track,
                    chosen["webpage_url"],
                    interaction.user.display_name,
                ),
                timeout=100,
            )
        except asyncio.TimeoutError:
            await interaction.followup.send("That took too long to load. Try again.")
        except ValueError as exc:
            await interaction.followup.send(str(exc))
        except (DownloadError, LookupError):
            await interaction.followup.send(f"I couldn't play **{chosen['title']}**.")
        else:
            await self.cog._queue_track(interaction, track, chosen["title"])

    async def on_timeout(self) -> None:
        self.stop()
        await self._tidy()

    async def _tidy(self) -> None:
        """Remove the dropdown so nobody clicks a stale choice."""
        if self.message is None:
            return
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            log.debug("could not disable the search picker", exc_info=True)


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
        started = time.monotonic()

        try:
            # yt-dlp is blocking, so keep it off the event loop. The timeout
            # stops a slow site from leaving the command at "thinking" forever.
            # It has to cover every player-client fallback attempt.
            track = await asyncio.wait_for(
                asyncio.to_thread(resolve_track, song, interaction.user.display_name),
                timeout=100,
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
                f"I couldn't find anything for **{song}**.\nReason: `{reason_line(exc)}`"
            )
            return

        log.info(
            "play: resolved %r -> %s (%.1fs)", song, track.title, time.monotonic() - started
        )
        await self._queue_track(interaction, track, song)

    async def _queue_track(
        self, interaction: discord.Interaction, track: Track, label: str
    ) -> None:
        """Connect, queue `track`, and reply. Caller must have deferred.

        Shared by /play and /search so both behave identically.
        """
        try:
            player, error = await asyncio.wait_for(
                self.ensure_player(interaction), timeout=40
            )
        except asyncio.TimeoutError:
            log.warning("voice connect timed out for %r", label)
            await self._discard_voice(interaction.guild)
            await interaction.followup.send(
                "CASE took too long to join the voice channel. Try again."
            )
            return
        if error:
            await interaction.followup.send(error)
            return
        log.info("connected, queueing")

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

    @discord.app_commands.command(name="shuffle", description="Shuffle the queue.")
    async def shuffle(self, interaction: discord.Interaction):
        player = self.get_player(interaction.guild)
        if player is None or not player.queue:
            await interaction.response.send_message("There's nothing in the queue to shuffle.")
            return
        player.shuffle()
        await interaction.response.send_message(
            f"Shuffled {len(player.queue)} songs in the queue."
        )

    @discord.app_commands.command(name="remove", description="Remove one song from the queue.")
    async def remove(self, interaction: discord.Interaction, position: int):
        player = self.get_player(interaction.guild)
        if player is None or not player.queue:
            await interaction.response.send_message("There's nothing in the queue.")
            return
        if position < 1 or position > len(player.queue):
            await interaction.response.send_message(
                f"Pick a position between 1 and {len(player.queue)}."
            )
            return
        removed = player.queue.pop(position - 1)
        await interaction.response.send_message(f"Removed **{removed.title}** from the queue.")

    @discord.app_commands.command(name="clear", description="Clear the queue but keep playing.")
    async def clear(self, interaction: discord.Interaction):
        player = self.get_player(interaction.guild)
        if player is None or not player.queue:
            await interaction.response.send_message("The queue is already empty.")
            return
        count = len(player.queue)
        player.clear()
        await interaction.response.send_message(
            f"Cleared {count} songs. The current song keeps playing."
        )

    @discord.app_commands.command(name="jump", description="Skip ahead to a position in the queue.")
    async def jump(self, interaction: discord.Interaction, position: int):
        player = self.get_player(interaction.guild)
        if player is None or not player.queue:
            await interaction.response.send_message("There's nothing queued to jump to.")
            return
        if position < 1 or position > len(player.queue):
            await interaction.response.send_message(
                f"Pick a position between 1 and {len(player.queue)}."
            )
            return

        target = player.queue[position - 1]
        dropped = position - 1
        player.queue = player.queue[position - 1:]
        if player.current is None:
            player.start()
        else:
            player.skip()
        await interaction.response.send_message(
            f"Jumped to **{target.title}**" + (f", dropped {dropped} song(s)." if dropped else ".")
        )

    @discord.app_commands.command(
        name="loop", description="Repeat the current song, the whole queue, or turn it off."
    )
    async def loop(self, interaction: discord.Interaction, mode: str):
        player = self.get_player(interaction.guild)
        if player is None:
            await interaction.response.send_message("Nothing is playing right now.")
            return
        if mode not in ("off", "song", "queue"):
            await interaction.response.send_message("Mode must be off, song, or queue.")
            return
        player.loop_mode = mode
        label = {"off": "Loop is off.", "song": "Looping the current song.", "queue": "Looping the whole queue."}
        await interaction.response.send_message(label[mode])

    @loop.autocomplete("mode")
    async def loop_autocomplete(self, interaction: discord.Interaction, current: str):
        player = self.get_player(interaction.guild)
        options = ["off", "song", "queue"]
        if player and player.loop_mode in options:
            options.remove(player.loop_mode)
            options.insert(0, player.loop_mode)
        return [
            discord.app_commands.Choice(name=m, value=m)
            for m in options
            if current.lower() in m
        ]

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

    @discord.app_commands.command(name="seek", description="Jump to a spot in the current song.")
    async def seek(self, interaction: discord.Interaction, position: str):
        player = self.get_player(interaction.guild)
        if player is None or player.current is None:
            await interaction.response.send_message("Nothing is playing right now.")
            return

        try:
            seconds = parse_timestamp(position)
        except ValueError as exc:
            await interaction.response.send_message(str(exc))
            return

        total = player.current.duration
        if total and seconds >= total:
            await interaction.response.send_message(
                f"That song is only {player.current.duration_label()} long."
            )
            return

        if not player.seek(seconds):
            await interaction.response.send_message("Couldn't seek right now. Try again.")
            return
        await interaction.response.send_message(
            f"Seeked to **{seconds // 60}:{seconds % 60:02d}** in **{player.current.title}**."
        )

    @discord.app_commands.command(
        name="search", description="Search and pick from a list before playing."
    )
    async def search(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(ephemeral=True)

        # An exact local match is unambiguous, so don't make them pick it.
        path = find_song(query)
        if path is not None:
            await interaction.followup.send(
                f"That's **{path.name}** in music/ - use `/play {path.stem}` to play it."
            )
            return

        try:
            results = await asyncio.wait_for(
                asyncio.to_thread(search_candidates, query), timeout=30
            )
        except asyncio.TimeoutError:
            await interaction.followup.send("The search took too long. Try a shorter query.")
            return
        except ValueError as exc:
            await interaction.followup.send(str(exc))
            return
        except (DownloadError, LookupError) as exc:
            await interaction.followup.send(
                f"I couldn't find anything for **{query}**.\nReason: `{reason_line(exc)}`"
            )
            return

        picker = SearchPicker(self, results)
        picker.message = await interaction.followup.send(
            f"Results for **{query}** - pick one:", view=picker, wait=True
        )

    @discord.app_commands.command(
        name="lyrics", description="Lyrics for the current song, or any search."
    )
    async def lyrics(self, interaction: discord.Interaction, query: str | None = None):
        player = self.get_player(interaction.guild)
        if query is None:
            if player is None or player.current is None:
                await interaction.response.send_message(
                    "Nothing is playing - try `/lyrics brown rang` instead."
                )
                return
            query = player.current.title

        await interaction.response.defer()

        # YouTube titles carry junk like "| Artist" and "(Official Video)".
        # lrclib matches better on a trimmed version, so try the full one first.
        trimmed = re.split(r"\s+\|\s+|\s+-\s+", query)[0].strip()

        item = await fetch_lyrics(query)
        if item is None and trimmed and trimmed.lower() != query.lower():
            item = await fetch_lyrics(trimmed)

        if item is None:
            await interaction.followup.send(f"I couldn't find lyrics for **{query}**.")
            return

        text = item["plainLyrics"].strip()
        if len(text) > 3900:
            # Discord cuts embeds off at 4096, leave room for the title.
            text = text[:3900].rsplit("\n", 1)[0] + "\n…"

        embed = discord.Embed(
            title=item.get("trackName") or trimmed or query,
            description=text,
            colour=discord.Colour.blurple(),
        )
        if item.get("artistName"):
            embed.set_author(name=item["artistName"])
        await interaction.followup.send(embed=embed)

    @discord.app_commands.command(
        name="autoplay", description="Keep playing similar songs automatically."
    )
    async def autoplay(self, interaction: discord.Interaction):
        player = self.get_player(interaction.guild)
        if player is None:
            await interaction.response.send_message(
                "Play something first - autoplay needs a song to go on from."
            )
            return

        player.autoplay = not player.autoplay
        if player.autoplay:
            await interaction.response.send_message(
                "Autoplay **on**. When the queue runs out I'll pick similar songs."
            )
        else:
            await interaction.response.send_message("Autoplay **off**.")

    @discord.app_commands.command(name="history", description="Show recently played songs.")
    async def history(self, interaction: discord.Interaction):
        if not db.available():
            await interaction.response.send_message(
                "Play history isn't enabled - CASE is running without a database."
            )
            return

        await interaction.response.defer()
        rows = await db.recent_plays(interaction.guild.id, limit=10)

        if not rows:
            await interaction.followup.send("No songs have been played yet.")
            return

        lines = []
        for row in rows:
            stamp = row["played_at"].astimezone().strftime("%d %b %H:%M")
            lines.append(f"`{stamp}` **{row['title']}** — {row['requested_by']}")

        embed = discord.Embed(
            title="Recently Played",
            description="\n".join(lines),
            colour=discord.Colour.blurple(),
        )
        await interaction.followup.send(embed=embed)

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

        if track.duration:
            elapsed = min(player.elapsed(), track.duration)
            embed.add_field(
                name="Progress",
                value=(
                    f"`{elapsed // 60}:{elapsed % 60:02d}` "
                    f"{progress_bar(elapsed, track.duration)} "
                    f"`{track.duration_label()}`"
                ),
                inline=False,
            )

        flags = []
        if player.loop_mode != "off":
            flags.append(f"loop: {player.loop_mode}")
        if player.autoplay:
            flags.append("autoplay")
        if flags:
            embed.add_field(name="Modes", value=" · ".join(flags), inline=True)

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