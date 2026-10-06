from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import random
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlencode, urlparse

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
    "Install FFmpeg and restart Audira."
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


def _lyrics_key(text: str) -> str:
    """Letters and digits only, so 'Hale Dil (From Murder)' compares cleanly."""
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _words(text: str) -> set[str]:
    """Meaningful words in a title or artist name, for loose matching."""
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2}


def _related_to(candidate: dict, query: str) -> bool:
    """True when a search result really is about what we searched for.

    Search APIs answer almost anything with something, so we need to tell a
    song matching the query apart from one that merely ranks well. Two shared
    words (or the only one, for a one-word query) is enough - plus any result
    that is simply a shorter form of the query, so a long YouTube title still
    matches its own short name. That keeps "hale dil by murder" pointing at
    "Hale Dil Murder2" while rejecting a song that only contains "band".
    """
    wanted = _words(query)
    found = _words(candidate.get("title")) | _words(candidate.get("artist"))
    if not wanted or not found:
        return False
    if found <= wanted or wanted <= found:
        return True
    needed = 1 if len(wanted) == 1 else 2
    return len(wanted & found) >= needed


def lyrics_score(item: dict, title: str, artist: str | None, duration: int | None) -> int:
    """Confidence that `item` is the same song. 0 means "not this song".

    lrclib's search is loose, so the first hit is often a different track
    entirely. A score of 3+ needs the titles to match (equal or one inside
    the other); artist and duration only break ties between close matches.
    """
    want, got = _lyrics_key(title), _lyrics_key(item.get("trackName") or "")
    if not want or not got:
        return 0
    if want == got:
        score = 5
    elif want in got or got in want:
        score = 3
    else:
        return 0

    if artist:
        theirs = _lyrics_key(item.get("artistName") or "")
        ours = _lyrics_key(artist)
        if theirs and ours and (ours in theirs or theirs in ours):
            score += 3
        elif theirs and ours:
            score -= 1

    their_duration = item.get("duration")
    if duration and their_duration:
        diff = abs(int(their_duration) - int(duration))
        if diff <= 5:
            score += 3
        elif diff <= 15:
            score += 1
        elif diff > 30:
            score -= 2

    return score


async def fetch_lyrics(
    query: str, *, artist: str | None = None, duration: int | None = None
) -> dict | None:
    """Best matching lyrics for `query` from lrclib.net - free, no API key.

    Returns the raw result (it has plainLyrics/artistName/...) or None when
    nothing looks like the same song - a wrong answer is worse than none.
    """
    timeout = aiohttp.ClientTimeout(total=10)
    results: list = []
    async with aiohttp.ClientSession(timeout=timeout) as session:
        # lrclib answers 503 now and then; a couple of retries turns most of
        # those "couldn't find lyrics" moments into results.
        for attempt in range(3):
            try:
                async with session.get(LRCLIB_SEARCH, params={"q": query}) as resp:
                    if resp.status == 200:
                        results = await resp.json()
                        break
                    log.warning("lrclib returned HTTP %s", resp.status)
            except aiohttp.ClientError as exc:
                log.warning("lrclib request failed: %s", exc)
            if attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))

    if not results:
        return None

    best: dict | None = None
    best_score = 0
    for item in results:
        if not item.get("plainLyrics"):
            continue
        score = lyrics_score(item, query, artist, duration)
        if score > best_score:
            best, best_score = item, score

    if best is not None:
        log.info("lyrics %r -> %r (score %d)", query, best.get("trackName"), best_score)
    return best


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
        artist: str | None = None,
        thumbnail: str | None = None,
    ):
        self.source = source
        self.title = title
        self.requested_by = requested_by
        self.duration = duration
        self.webpage_url = webpage_url
        # HTTP headers yt-dlp says go with this stream. FFmpeg needs them too,
        # otherwise the CDN answers 403 because the request looks automated.
        self.headers = headers or {}
        # Who made it, when the source says so. Autoplay leans on this to
        # find something in the same lane as the song that just ended.
        self.artist = artist
        # Cover art for the now-playing card, when the source provides one.
        self.thumbnail = thumbnail
        # True when a command already posted the now-playing card for this
        # track, so starting it does not post a second one.
        self.announced = False
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
    # Pick by bitrate, not by container. yt-dlp otherwise prefers m4a over mp3
    # and hands SoundCloud back a 96k AAC stream when a 128k MP3 exists - and
    # nobody wants 96k.
    "format_sort": ["abr"],
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


def _cookie_file(value: str) -> str:
    """Turn YOUTUBE_COOKIES into a path yt-dlp can read.

    Accepts either a path to a cookies.txt (handy on our own machine) or the
    file's base64, because Render's environment variables are single-line.
    """
    raw = value.strip()
    path = Path(raw)
    if path.is_file():
        return str(path)

    target = Path(tempfile.gettempdir()) / "case_youtube_cookies.txt"
    target.write_bytes(base64.b64decode(raw))
    return str(target)


# A PO token alone rarely satisfies YouTube from a data-centre IP - its own
# docs say to add cookies as well. Off unless YOUTUBE_COOKIES is set, so a
# home connection needs nothing.
_COOKIES = os.getenv("YOUTUBE_COOKIES")
if _COOKIES:
    try:
        YDL_OPTIONS["cookiefile"] = _cookie_file(_COOKIES)
        log.info("youtube cookies: %s", YDL_OPTIONS["cookiefile"])
    except Exception as exc:
        log.warning("YOUTUBE_COOKIES could not be read: %s", exc)
else:
    log.info("youtube cookies: none set")

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


JIOSAAVN_API = "https://www.jiosaavn.com/api.php"


def search_jiosaavn(query: str, limit: int = MAX_RESULTS) -> list[dict]:
    """Top songs for a query from JioSaavn - no account, no bot check.

    Returns the same shape as search_candidates so /search can fall back to
    it when YouTube refuses our IP. Runs in a thread: it blocks on urllib.
    """
    params = urlencode(
        {
            "_format": "json",
            "_method": "get",
            "_page": "1",
            "p": "1",
            "n": str(limit),
            "q": query,
            "result": "song",
            "specific": "true",
            "__call": "search.getResults",
        }
    )
    request = urllib.request.Request(
        f"{JIOSAAVN_API}?{params}",
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        data = json.load(response)

    results = []
    for item in (data.get("results") or [])[:limit]:
        page = item.get("perma_url")
        if not page:
            continue
        artist = (item.get("primary_artists") or item.get("music") or "").strip()
        results.append(
            {
                "title": item.get("song") or query,
                "webpage_url": page,
                "duration": int(item.get("duration") or 0) or None,
                "artist": artist or None,
                "thumbnail": item.get("image") or None,
            }
        )
    return results


def _jiosaavn_info(query: str) -> dict | None:
    """First JioSaavn match for a plain query, fully resolved - or None.

    Any failure just means "let the next source have a go", so nothing
    propagates.
    """
    try:
        matches = search_jiosaavn(query, limit=1)
        if not matches:
            log.info("jiosaavn: no match for %r", query)
            return None
        page = matches[0]["webpage_url"]
        log.info("jiosaavn: %r -> %s", query, page)
        return _extract_once(YDL_OPTIONS, page)
    except Exception as exc:
        log.warning("jiosaavn failed for %r: %s", query, reason_line(exc))
        return None


def search_soundcloud(query: str, limit: int = MAX_RESULTS) -> list[dict]:
    """SoundCloud matches for a plain query, in the shape search_candidates uses.

    Results stay flat: a page link is all /search shows and all _extract_once
    needs later. The search arrives as a playlist, so noplaylist (which /play
    sets for YouTube links) has to come off. Runs in a thread: it blocks on
    yt-dlp.
    """
    options = {**YDL_OPTIONS, "noplaylist": False, "extract_flat": "in_playlist"}
    options.pop("playlist_items", None)
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(f"scsearch{limit}:{query}", download=False)

    results = []
    for entry in [e for e in (info or {}).get("entries") or [] if e]:
        url = entry.get("webpage_url") or entry.get("url")
        if not url or not is_url(url):
            continue
        results.append(
            {
                "title": entry.get("title") or query,
                "webpage_url": url,
                "duration": int(entry.get("duration") or 0) or None,
            }
        )
    return results


def _soundcloud_info(query: str, limit: int = 5) -> dict | None:
    """First usable SoundCloud match for a plain query - or None.

    Tried after JioSaavn: SoundCloud answers from a data-centre IP without a
    sign-in and without a bot check, but its best is 128k. Its search is
    loose too, so the match has to actually relate to the query - otherwise
    YouTube gets the turn.
    """
    try:
        matches = search_soundcloud(query, limit=limit)
    except Exception as exc:
        log.info("soundcloud search failed for %r: %s", query, reason_line(exc))
        return None

    for match in matches:
        if not _related_to(match, query):
            continue
        try:
            full = _extract_once(YDL_OPTIONS, match["webpage_url"])
        except Exception as exc:
            log.info(
                "soundcloud: no stream for %r: %s", match["title"], reason_line(exc)
            )
            continue
        if full:
            log.info("soundcloud: %r -> %s", query, full.get("title"))
            return full

    log.info("soundcloud: no usable match for %r", query)
    return None


def _build_track(info: dict, fallback_title: str, requested_by: str) -> Track:
    """Turn an extraction result into a playable Track."""
    url = info.get("url")
    if not url:
        # some sites only hand back formats, not a resolved stream url
        formats = [f for f in info.get("formats", []) if f.get("url")]
        if not formats:
            raise LookupError("no playable stream for that track")
        url = formats[-1]["url"]

    artist = info.get("artist") or info.get("creator") or info.get("uploader")
    if isinstance(artist, str):
        artist = artist.strip() or None
    else:
        artist = None
    thumbnail = info.get("thumbnail")
    if thumbnail and not isinstance(thumbnail, str):
        thumbnail = None

    return Track(
        source=url,
        title=info.get("title") or fallback_title,
        requested_by=requested_by,
        duration=int(info.get("duration") or 0) or None,
        webpage_url=info.get("webpage_url"),
        headers=info.get("http_headers") or {},
        artist=artist,
        thumbnail=thumbnail,
    )


def resolve_track(query: str, requested_by: str) -> Track:
    """Turn a /play argument into a Track.

    Local files win over the internet so you can always play your own
    versions by name. Plain queries go to JioSaavn first - it streams 320k
    with no account and no bot check - then to SoundCloud (128k is its
    best without logging in), then to yt-dlp, which is also what handles a
    YouTube or SoundCloud link you paste in yourself.

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

    if not is_url(query):
        jsaavn_info = _jiosaavn_info(query)
        if jsaavn_info:
            try:
                return _build_track(jsaavn_info, query, requested_by)
            except LookupError:
                log.warning("jiosaavn had no playable stream for %r", query)

        sc_info = _soundcloud_info(query)
        if sc_info:
            try:
                return _build_track(sc_info, query, requested_by)
            except LookupError:
                log.warning("soundcloud had no playable stream for %r", query)

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

    return _build_track(info, query, requested_by)


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

    # Same order as /play, so the picker shows what pressing Enter would do:
    # Same order as /play, so the picker shows what pressing Enter would do:
    # JioSaavn (320k), then SoundCloud (128k), and only then YouTube.
    results = []
    try:
        results = search_jiosaavn(query, limit)
        log.info("jiosaavn search: %d result(s)", len(results))
    except Exception as exc:
        log.warning("jiosaavn search failed: %s", reason_line(exc))
        results = []

    if not results:
        try:
            matches = search_soundcloud(query, limit)
            results = [m for m in matches if _related_to(m, query)]
            log.info("soundcloud search: %d result(s)", len(results))
        except Exception as exc:
            log.warning("soundcloud search failed: %s", reason_line(exc))
            results = []

    if not results:
        options = dict(YDL_OPTIONS)
        options["extract_flat"] = "in_playlist"
        # /play sets this to keep playlist links short, but on a ytsearch it
        # would cap the whole result list at one - which defeats a picker.
        options.pop("playlist_items", None)
        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
                entries = [e for e in (info or {}).get("entries", []) if e]

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
            if results:
                log.info("youtube search: %d result(s)", len(results))
        except (DownloadError, LookupError) as exc:
            log.warning("youtube search failed: %s", reason_line(exc))

    if not results:
        raise LookupError("no results")
    return results


def build_now_playing_embed(track: Track, player: MusicPlayer) -> discord.Embed:
    """The card posted whenever a song starts - used by commands and by the
    playback loop itself, so every song gets the same treatment."""
    status = "Paused" if player.is_paused else "Playing"
    embed = discord.Embed(
        title=f"Audira {status}",
        description=f"Now playing **{track.title}**",
        colour=discord.Colour.blurple(),
    )
    source = "stream" if track.is_stream else "local file"
    embed.add_field(
        name="Details",
        value=f"{source} · {track.duration_label()}",
        inline=True,
    )
    if track.artist:
        embed.add_field(name="Artist", value=track.artist[:100], inline=True)
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

    if track.thumbnail:
        embed.set_thumbnail(url=track.thumbnail)
    if track.webpage_url:
        embed.url = track.webpage_url
    if player.queue:
        embed.add_field(
            name=f"Queue ({len(player.queue)})",
            value=", ".join(t.title for t in player.queue[:5]),
            inline=False,
        )
    return embed


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

                # Autoplay runs after a skip too, so /skip on the last song
                # keeps the music going instead of winding down.
                if not self.queue and self.autoplay:
                    await self._queue_autoplay(track)

                self.current = None

        except asyncio.CancelledError:
            pass
        except Exception:
            # Anything unexpected must not take the whole playback loop down
            # silently - the queue would sit there with nothing happening.
            log.exception("playback loop stopped on an unexpected error")
        finally:
            self.current = None
            # Stay in the channel when the queue runs dry - only /stop (or
            # someone kicking us out) ends the session.
            if self.voice.is_connected() and not self.queue:
                log.info("queue finished - staying in the voice channel")

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

        # The queue advances on its own after a skip or an autoplay pick, so
        # this is where most songs get their card posted.
        self._announce(track)

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

    async def _first_playable(self, candidates: list[dict], finished: Track) -> Track | None:
        """First candidate that actually resolves and isn't a repeat."""
        for candidate in candidates:
            title = candidate.get("title") or ""
            if normalize(title) == normalize(finished.title):
                continue
            if any(normalize(title) == normalize(t) for t in self._recent):
                continue
            try:
                track = await asyncio.to_thread(
                    resolve_track, candidate["webpage_url"], finished.requested_by
                )
            except (DownloadError, LookupError, ValueError) as exc:
                log.warning("autoplay could not resolve %s: %s", title, exc)
                continue
            except Exception as exc:
                log.warning(
                    "autoplay resolve broke for %s: %s: %s",
                    title,
                    type(exc).__name__,
                    exc,
                )
                continue
            return track
        return None

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

        # Artist first (keeps the next pick in the same style), then the
        # title - one of them usually works when the other doesn't.
        queries = [q for q in (finished.artist, finished.title) if q]
        log.info("autoplay: looking for something like %r", finished.title)

        track = None

        # JioSaavn first: it answers in about a second while YouTube can sit
        # there for a minute when it doesn't like our IP. Its search is loose
        # though, so results have to actually relate to what we asked for.
        for query in queries:
            try:
                candidates = await asyncio.wait_for(
                    asyncio.to_thread(search_jiosaavn, query, limit=5),
                    timeout=20,
                )
            except Exception as exc:
                log.warning(
                    "autoplay jiosaavn search failed for %r: %s: %s",
                    query,
                    type(exc).__name__,
                    exc,
                )
                continue
            candidates = [c for c in candidates if _related_to(c, query)]
            if not candidates:
                continue
            log.info("autoplay: jiosaavn %r -> %d candidate(s)", query, len(candidates))
            track = await self._first_playable(candidates, finished)
            if track:
                break

        # Slower fallback - YouTube search (with JioSaavn inside it) covers
        # songs the Indian catalog doesn't have.
        for query in queries:
            if track:
                break
            try:
                candidates = await asyncio.wait_for(
                    asyncio.to_thread(search_candidates, query, limit=8),
                    timeout=60,
                )
            except Exception as exc:
                log.warning(
                    "autoplay search failed for %r: %s: %s",
                    query,
                    type(exc).__name__,
                    exc,
                )
                continue
            if not candidates:
                continue
            log.info("autoplay: %r -> %d candidate(s)", query, len(candidates))
            track = await self._first_playable(candidates, finished)
            if track:
                break

        if track is None:
            log.info("autoplay found nothing new to play")
            self._notify("Autoplay couldn't find anything similar to play.")
            return

        log.info("autoplay picked %s", track.title)
        self.queue.append(track)
        self._notify(f"Autoplay: **{track.title}**")

    def _notify(self, message: str) -> None:
        """Best-effort notice in the channel that started playback."""
        if self.notify_channel:
            asyncio.create_task(self.notify_channel.send(message))

    def _announce(self, track: Track) -> None:
        """Post the now-playing card when a song starts on its own.

        Commands already post it when they start a song directly, which is
        what `track.announced` marks - everything else (queue advancing,
        /skip, autoplay) gets the card from here.
        """
        if track.announced:
            track.announced = False
            return
        if self.notify_channel is None:
            return
        embed = build_now_playing_embed(track, self)

        async def send() -> None:
            try:
                await self.notify_channel.send(embed=embed)
            except Exception:
                log.warning("could not post the now-playing card", exc_info=True)

        asyncio.create_task(send())


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
        # Autoplay choice lives on the cog too, so it survives a player being
        # thrown away (queue ended, bot restarted) instead of resetting.
        self.default_autoplay = False

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
            player.autoplay = self.default_autoplay
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
            await interaction.response.send_message("Audira isn't connected to a voice channel.")
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
        # Not ephemeral: everyone should see what got queued (and any error).
        await interaction.response.defer()
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
                "Audira took too long to join the voice channel. Try again."
            )
            return
        if error:
            await interaction.followup.send(error)
            return
        log.info("connected, queueing")

        was_idle = player.is_idle()
        player.notify_channel = interaction.channel
        if was_idle:
            # This reply carries the now-playing card, so the playback loop
            # won't post a second one for the same track.
            track.announced = True
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

        # Skip only moves on - staying in the channel is handled by the
        # playback loop, and leaving is /stop's job alone.
        if player.queue:
            nxt = player.queue[0].title
            player.skip()
            await interaction.response.send_message(f"Skipped. Now playing **{nxt}**.")
        else:
            player.skip()
            if player.autoplay:
                await interaction.response.send_message(
                    "Skipped. Autoplay is picking something similar..."
                )
            else:
                await interaction.response.send_message(
                    "Skipped. Nothing else queued - I'll stay in the channel."
                )

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

        embed = discord.Embed(title="Audira Queue", colour=discord.Colour.blurple())
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
        title = query
        artist: str | None = None
        duration: int | None = None

        if title is None:
            if player is None or player.current is None:
                await interaction.response.send_message(
                    "Nothing is playing - try `/lyrics brown rang` instead."
                )
                return
            title = player.current.title

        if player is not None and player.current is not None:
            # When the query is the song on right now, use its metadata too so
            # duration and artist can rule out look-alike tracks.
            if _lyrics_key(title) == _lyrics_key(player.current.title):
                artist = player.current.artist
                duration = player.current.duration

        await interaction.response.defer()

        # YouTube titles carry junk like "| Artist" and "(Official Video)".
        # lrclib matches better on a trimmed version, so try both.
        trimmed = re.split(r"\s+\|\s+|\s+-\s+", title)[0].strip()
        attempts = [title]
        if trimmed and trimmed.lower() != title.lower():
            attempts.append(trimmed)
        if artist:
            attempts.append(f"{trimmed} {artist}")

        item = None
        for attempt in attempts:
            item = await fetch_lyrics(attempt, artist=artist, duration=duration)
            if item is not None:
                break

        if item is None:
            await interaction.followup.send(f"I couldn't find lyrics for **{title}**.")
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
            # No session running yet - remember the choice for the next one.
            self.default_autoplay = not self.default_autoplay
            if self.default_autoplay:
                await interaction.response.send_message(
                    "Autoplay **on**. The next session will keep picking similar songs."
                )
            else:
                await interaction.response.send_message("Autoplay **off**.")
            return

        player.autoplay = not player.autoplay
        self.default_autoplay = player.autoplay
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
                "Play history isn't enabled - Audira is running without a database."
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
        return build_now_playing_embed(track, player)

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