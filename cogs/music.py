from __future__ import annotations

import asyncio
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
import urllib.parse
from datetime import datetime, timezone
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


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

INVIDIOUS_INSTANCES = [
    "https://invidious.fdn.fr",
    "https://invidious.privacydev.net",
    "https://iv.melmac.space",
    "https://invidious.slipfox.xyz",
]

AUDIO_EXTENSIONS = (".mp3", ".wav", ".ogg", ".m4a", ".flac")


FFMPEG_FALLBACK_PATHS = (
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links",
    Path("C:/ffmpeg/bin"),
    Path("C:/Program Files/ffmpeg/bin"),
    Path("C:/ProgramData/chocolatey/bin"),

    Path("/app/vendor/ffmpeg/bin"),
)

FFMPEG_MISSING = (
    "FFmpeg isn't installed, so I can't play audio. "
    "Install FFmpeg and restart Audira."
)


def find_ffmpeg() -> str | None:
    """Locate the ffmpeg binary, or None if it isn't installed."""
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

    for candidate in (
        config.BASE_DIR / "node" / "bin" / "node",
        config.BASE_DIR / "potprovider" / "server" / "node" / "bin" / "node",
    ):
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


ACCENT = discord.Colour.from_rgb(99, 102, 241)
MUTED = discord.Colour.from_rgb(96, 100, 112)
BRAND_ICON = "https://cdn-icons-png.flaticon.com/512/2361/2361845.png"
CARD_TICK = 1.5


def format_time(seconds: int | None) -> str:
    """`3:45` or `1:02:03`, never negative."""
    seconds = max(0, int(seconds or 0))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _stamp(seconds: int) -> str:
    """`01:24` / `1:02:03` - zero-padded minutes for the progress line."""
    text = format_time(seconds)
    head, sep, tail = text.partition(":")
    if sep and len(head) == 1:
        return "0" + text
    return text


def progress_line(elapsed: int, total: int, width: int = 16) -> str:
    """``01:24 ━━━━●━━━━━━━━ 05:46`` - a compact monospace player bar."""
    ratio = min(1.0, max(0.0, elapsed / total)) if total else 0.0
    filled = round(ratio * width)
    if filled >= width:
        bar = "━" * width
    else:
        bar = "━" * filled + "●" + "─" * (width - filled - 1)
    return f"`{_stamp(elapsed)} {bar} {_stamp(total)}`"


def source_label(track: Track) -> str:
    """Where the audio is coming from, for the card metadata line."""
    page = (track.webpage_url or "").lower()
    if "youtube" in page:
        return "YouTube"
    if "jiosaavn" in page:
        return "JioSaavn"
    if not track.is_stream:
        return "Local file"
    return "Stream"


def quality_label(track: Track) -> str | None:
    """`320 kbps` when the source reports a bitrate."""
    quality = track.quality or ("320k" if track.is_stream else None)
    if not quality:
        return None
    if quality.endswith("k") and quality[:-1].isdigit():
        return f"{quality[:-1]} kbps"
    return quality


def truncate(text: str, limit: int = 60) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def short_duration(seconds: int | None) -> str:
    """`3:45` for dropdown descriptions, blank when unknown."""
    if not seconds:
        return ""
    minutes, rest = divmod(seconds, 60)
    if minutes >= 60:
        return f"{minutes // 60}:{minutes % 60:02d}:{rest:02d}"
    return f"{minutes}:{rest:02d}"


def reason_line(exc: Exception) -> str:
    """One short line for a yt-dlp failure, so the reply can show why."""
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


def _autoplay_words(text: str) -> set[str]:
    """Words that should influence autoplay similarity, excluding remix/mood noise."""
    noise = {
        "audio",
        "cover",
        "edit",
        "emraan",
        "full",
        "hashmi",
        "hd",
        "lyric",
        "lyrics",
        "lofi",
        "mix",
        "music",
        "official",
        "remaster",
        "remastered",
        "reverbed",
        "rewind",
        "slowed",
        "soul",
        "song",
        "video",
    }
    text = text or ""
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"\[[^\]]*\]", " ", text)
    text = re.sub(r"[|/•—]", " ", text)
    return {w for w in _words(text) if w not in noise}


def _autoplay_key(title: str) -> str:
    """A title reduced to its core, so editions count as one song."""
    return " ".join(sorted(_autoplay_words(title)))


def _related_to(candidate: dict, query: str) -> bool:
    """True when a search result is close to the same song or same-genre match."""
    wanted = _autoplay_words(query)
    title_words = _autoplay_words(candidate.get("title") or "")
    artist_words = _autoplay_words(candidate.get("artist") or "")
    if not wanted or not (title_words or artist_words):
        return False

    title_overlap = len(wanted & title_words)
    artist_overlap = len(wanted & artist_words)
    if title_overlap >= 2:
        return True
    if title_overlap >= 1 and artist_overlap >= 1:
        return True
    if len(wanted) == 1 and title_overlap == 1:
        return True
    return False


def lyrics_score(item: dict, title: str, artist: str | None, duration: int | None) -> int:
    """Confidence that `item` is the same song."""
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
    """Best matching lyrics for `query` from lrclib.net - free, no API key."""
    timeout = aiohttp.ClientTimeout(total=10)
    results: list = []
    async with aiohttp.ClientSession(timeout=timeout) as session:


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





UNSUPPORTED_DOMAINS = {
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

JIOSAAVN_API = "https://www.jiosaavn.com/api.php"


def search_jiosaavn(query: str, limit: int | None = None) -> list[dict]:
    """Exact-track results from JioSaavn for a single title or URL-derived song name."""
    if limit is None:
        limit = MAX_RESULTS
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


def _extract_title_from_url(url: str) -> str | None:
    """Best-effort title extraction from a non-YouTube platform URL."""
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower().split(":")[0].replace("www.", "")
        path = urllib.parse.unquote(parsed.path or "")
        if netloc in {"spotify.com", "open.spotify.com"}:
            parts = [p for p in path.split("/") if p]
            if len(parts) >= 2:
                slug = parts[-1]
                return slug.replace("-", " ").strip()
        if path:
            slug = path.rstrip("/").split("/")[-1]
            if slug:
                return slug.replace("-", " ").replace("_", " ").strip()
    except Exception:
        pass
    return None


def _jiosaavn_info_for_url(url: str) -> dict | None:
    """JioSaavn fallback only for exact URL-derived song names, not generic keyword search."""
    host = urlparse(url).netloc.lower().split(":")[0].replace("www.", "")
    if "youtube" in host or host in {"youtu.be", "m.youtube.com", "music.youtube.com"}:
        return None
    title = _extract_title_from_url(url)
    if not title:
        return None
    try:
        matches = search_jiosaavn(title, limit=1)
        if not matches:
            return None
        page = matches[0]["webpage_url"]
        return _extract_once(YDL_OPTIONS, page)
    except Exception as exc:
        log.warning("jiosaavn url fallback failed for %r: %s", url, reason_line(exc))
        return None


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
    """Look for a local file in music/ matching the query."""
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
    """One item in the queue. A track is either a file in music/ or a stream resolved by yt-dlp. `source` is what gets handed to FFmpeg: a path for local files, a U..."""

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
        temp_path: str | None = None,
        start_offset: int = 0,
        quality: str | None = None,
    ):
        self.source = source
        self.title = title
        self.requested_by = requested_by
        self.duration = duration
        self.webpage_url = webpage_url


        self.headers = headers or {}


        self.artist = artist

        self.thumbnail = thumbnail


        self.announced = False


        self.temp_path = temp_path
        self.start_offset = start_offset
        self.quality = quality

    def cleanup(self) -> None:
        """Delete the temporary file backing this track, if there is one."""
        if not self.temp_path:
            return
        try:
            os.remove(self.temp_path)
        except OSError:
            pass
        self.temp_path = None

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
    """Keeps yt-dlp's own console output out of the terminal."""

    def debug(self, msg: str) -> None:


        if "[pot" in msg or "PO Token" in msg:
            log.info("yt-dlp: %s", msg)
        else:
            log.debug("yt-dlp: %s", msg)

    def warning(self, msg: str) -> None:
        log.warning("yt-dlp: %s", msg)

    def error(self, msg: str) -> None:
        log.error("yt-dlp: %s", msg)


YDL_OPTIONS = {
    "format": "bestaudio[ext=m4a]/bestaudio/best",
    "audioquality": 0,
    "format_sort": ["quality", "vcodec:unknown", "acodec:unknown"],
    "noplaylist": True,
    "playlist_items": "1",
    "quiet": True,
    "no_warnings": True,
    "nocheckcertificate": True,
    "thumbnail": True,
    "retries": 1,
    "fragment_retries": 1,
    "socket_timeout": 15,
    "logger": _YDLLogger(),
}




_NODE = find_node()
if _NODE:
    YDL_OPTIONS["js_runtimes"] = {"node": {"path": _NODE}}
    log.info("node.js: %s (%s)", _NODE, _node_version(_NODE))
else:
    log.warning("node.js not found - some YouTube streams may fail with 403")



log.info("ffmpeg: %s", find_ffmpeg() or "MISSING")





_POT_HOME = config.BASE_DIR / "potprovider" / "server"
if _POT_HOME.exists() and _NODE:
    YDL_OPTIONS["extractor_args"] = {
        "youtubepot-bgutilscript": {"server_home": str(_POT_HOME)},
        "youtube": {"pot_trace": "true"},
    }
    log.info("po-token provider: %s (%s)", _POT_HOME, _NODE)
else:
    log.warning("po-token provider not available - YouTube may ask us to sign in")


_POT_BOOSTED = False


def _boost_pot_provider() -> None:
    """Make bgutil:script-node the preferred po-token provider."""
    global _POT_BOOSTED
    if _POT_BOOSTED:
        return
    _POT_BOOSTED = True
    try:
        from yt_dlp.extractor.youtube.pot import _registry as _pot_registry
        from yt_dlp.extractor.youtube.pot.provider import register_preference

        _node_ptp = next(
            (
                cls
                for cls in _pot_registry._pot_providers.value.values()
                if getattr(cls, "PROVIDER_NAME", "") == "bgutil:script-node"
            ),
            None,
        )
        if _node_ptp:
            register_preference(_node_ptp)(lambda provider, request: 1000)
        else:
            log.warning("po-token provider bgutil:script-node not registered")
    except Exception as exc:
        log.warning("could not boost po-token provider preference: %s", exc)


MAX_RESULTS = 5






YOUTUBE_CLIENT_ATTEMPTS: list[dict] = [
    {},
    {"extractor_args": {"youtube": {"player_client": ["tv"]}}},
    {"extractor_args": {"youtube": {"player_client": ["mweb"]}}},
    {"extractor_args": {"youtube": {"player_client": ["web_embedded"]}}},
]


def _merge_options(extra: dict) -> dict:
    """A copy of YDL_OPTIONS with one fallback attempt applied."""
    options = {**YDL_OPTIONS, **extra}
    if "extractor_args" in extra:
        options["extractor_args"] = {
            **YDL_OPTIONS.get("extractor_args", {}),
            **extra["extractor_args"],
        }
    return options


def _extract_once(options: dict, query: str) -> dict | None:
    """Extract one direct JioSaavn URL; text search is handled by the Saavn API."""
    if not is_url(query):
        raise ValueError("Only direct JioSaavn links can be extracted")
    host = urlparse(query).netloc.lower().split(":")[0].removeprefix("www.")
    if host != "jiosaavn.com" and not host.endswith(".jiosaavn.com"):
        raise ValueError("Only JioSaavn links can be extracted")

    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(query, download=False)
        if not info:
            raise LookupError("couldn't read that JioSaavn link")

        if not info:
            raise LookupError("no results")

        if info.get("_type") == "playlist":
            entries = [e for e in info.get("entries", []) if e]
            if not entries:
                raise LookupError("empty playlist")
            info = entries[0]

            if not info.get("url") and not info.get("formats"):
                info = ydl.process_ie_result(info, download=False)

    return info


def _ua() -> str:
    return random.choice(USER_AGENTS)


def _with_ua(extra: dict | None = None) -> dict:
    base = dict(YDL_OPTIONS)
    base["http_headers"] = {"User-Agent": _ua(), "Accept-Language": "en-US,en;q=0.9"}
    if extra:
        base.update(extra)
    return base


def _merge_options(extra: dict | None = None) -> dict:
    return _with_ua(extra)


def search_soundcloud(query: str, limit: int = MAX_RESULTS) -> list[dict]:
    """SoundCloud matches for a plain query, in the shape search_candidates uses."""
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
    """First usable SoundCloud match for a plain query - or None."""
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

    bitrate = info.get("abr") or info.get("tbr")
    quality = f"{round(bitrate)}k" if bitrate else (info.get("format_note") or None)
    webpage = info.get("webpage_url") or ""
    if "jiosaavn" in webpage.lower() or "jiosaavn" in str(info.get("extractor", "")).lower():
        quality = quality or "320k"
        if quality and quality.startswith("0"):
            quality = "320k"

    return Track(
        source=url,
        title=info.get("title") or fallback_title,
        requested_by=requested_by,
        duration=int(info.get("duration") or 0) or None,
        webpage_url=webpage or None,
        headers=info.get("http_headers") or {},
        artist=artist,
        thumbnail=thumbnail,
        quality=quality,
    )

def _cut_track(track: Track, seconds: int) -> Track | None:
    """Return a temporary file copy of `track` starting at `seconds`."""
    if seconds <= 0:
        return track

    handle = tempfile.NamedTemporaryFile(prefix="audira-seek-", suffix=".m4a", delete=False)
    target = handle.name
    handle.close()

    before = f"-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -ss {seconds}"
    if track.headers:
        blob = "\r\n".join(f"{key}: {value}" for key, value in track.headers.items()) + "\r\n"
        before += " -headers " + shlex.quote(blob)
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return None



    for extra in (["-c:a", "copy"], []):
        command = [ffmpeg, *shlex.split(before), "-i", track.source, "-vn", *extra, "-y", target]
        try:
            result = subprocess.run(command, capture_output=True, timeout=90)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("seek cut failed for %s: %s", track.title, exc)
            continue
        if result.returncode == 0 and os.path.getsize(target) > 0:
            break
    else:
        try:
            os.remove(target)
        except OSError:
            pass
        log.warning("seek cut produced nothing for %s", track.title)
        return None

    return Track(
        source=target,
        title=track.title,
        requested_by=track.requested_by,
        duration=track.duration,
        webpage_url=track.webpage_url,
        artist=track.artist,
        thumbnail=track.thumbnail,
        temp_path=target,
        start_offset=(track.start_offset or 0) + seconds,
        quality=track.quality,
    )


def _is_bot_check(exc: Exception) -> bool:
    """True when YouTube answered 'sign in to confirm you're not a bot'."""
    message = str(exc).lower()
    return "sign in to confirm" in message or "not a bot" in message


async def _youtube_info(query: str) -> tuple[dict | None, Exception | None]:
    """First usable YouTube result for a pasted link across client fallbacks."""
    last_error: Exception | None = None
    for attempt, extra in enumerate(YOUTUBE_CLIENT_ATTEMPTS):
        if attempt:
            log.info("youtube retry %d with client set %s", attempt, extra["extractor_args"])
        started = time.monotonic()
        try:
            info = await asyncio.to_thread(_extract_once, _merge_options(extra), query)
            return info, None
        except DownloadError as exc:
            last_error = exc
            log.warning(
                "youtube client attempt %d failed after %.1fs: %s",
                attempt,
                time.monotonic() - started,
                reason_line(exc),
            )
            if _is_bot_check(exc):
                log.warning("youtube is blocking this IP - skipping remaining clients")
                break
    return None, last_error


async def _invidious_info(query: str) -> tuple[dict | None, Exception | None]:
    import urllib.parse as _parse

    instances = list(INVIDIOUS_INSTANCES)
    random.shuffle(instances)
    for inst in instances[:2]:
        try:
            url = f"{inst.rstrip('/')}/api/v1/search?q={_parse.quote(query)}&type=video&limit=1"
            req = urllib.request.Request(
                url, headers={"User-Agent": _ua(), "Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=0.9) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            if not data:
                continue
            vid = data[0]
            video_id = vid.get("videoId") or vid.get("id")
            if not video_id:
                continue
            info_url = f"{inst.rstrip('/')}/api/v1/videos/{video_id}"
            req2 = urllib.request.Request(
                info_url, headers={"User-Agent": _ua(), "Accept": "application/json"}
            )
            with urllib.request.urlopen(req2, timeout=0.9) as resp2:
                vinfo = json.loads(resp2.read().decode("utf-8", errors="ignore"))
            title = vinfo.get("title") or vid.get("title") or query
            thumb = vinfo.get("videoThumbnails") or []
            thumbnail = None
            for t in thumb:
                if t.get("quality") in ("maxres", "high", "medium"):
                    thumbnail = t.get("url")
                    break
            if not thumbnail and thumb:
                thumbnail = thumb[-1].get("url")
            formats = vinfo.get("adaptiveFormats") or vinfo.get("formatStreams") or []
            best = None
            for f in formats:
                if str(f.get("type", "")).startswith("audio/"):
                    best = f
                    break
            if best is None and formats:
                best = formats[-1]
            if best is None:
                continue
            audio_url = best.get("url")
            if not audio_url:
                continue
            return {
                "id": video_id,
                "title": title,
                "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
                "thumbnail": thumbnail,
                "url": audio_url,
                "extractor": "invidious",
                "duration": vinfo.get("lengthSeconds") or vid.get("lengthSeconds") or 0,
            }, None
        except Exception as exc:
            log.debug("invidious failed on %s: %s", inst, exc)
            continue
    return None, LookupError("invidious unavailable")


async def resolve_track(query: str, requested_by: str) -> Track:
    """Resolve local files or JioSaavn tracks; never stream from other platforms."""
    query = query.strip()
    if not query:
        raise ValueError("empty query")

    reason = unsupported_reason(query)
    if reason:
        raise ValueError(reason)

    path = find_song(query)
    if path is not None:
        return Track.from_file(path, requested_by)

    if is_url(query):
        host = urlparse(query).netloc.lower().split(":")[0].replace("www.", "")
        if host != "jiosaavn.com" and not host.endswith(".jiosaavn.com"):
            raise ValueError(
                "Audira plays from JioSaavn only. Search by song name or paste a JioSaavn link."
            )
        try:
            info = await asyncio.to_thread(_extract_once, YDL_OPTIONS, query)
        except Exception as exc:
            raise LookupError(reason_line(exc)) from exc
        if not info:
            raise LookupError("JioSaavn returned no playable track")
        return _build_track(info, query, requested_by)

    last_error: Exception | None = None
    try:
        candidates = await asyncio.wait_for(
            asyncio.to_thread(search_candidates, query, MAX_RESULTS),
            timeout=30,
        )
    except asyncio.TimeoutError as exc:
        raise LookupError("JioSaavn search timed out after 30 seconds") from exc
    except Exception as exc:
        raise LookupError(reason_line(exc)) from exc

    for candidate in candidates:
        url = candidate.get("webpage_url")
        if not url:
            continue
        try:
            info = await asyncio.to_thread(_extract_once, YDL_OPTIONS, url)
            if info:
                return _build_track(info, query, requested_by)
        except Exception as exc:
            last_error = exc
            log.warning("JioSaavn candidate resolve failed for %r: %s", url, reason_line(exc))

    if last_error is not None:
        raise LookupError(reason_line(last_error)) from last_error
    raise LookupError("no results")


def search_candidates(query: str, limit: int = MAX_RESULTS) -> list[dict]:
    """Search JioSaavn and return selectable song results."""
    query = query.strip()
    if not query:
        raise ValueError("empty query")

    try:
        results = search_jiosaavn(query, limit=limit)
    except Exception as exc:
        log.warning("JioSaavn search failed for %r: %s", query, reason_line(exc))
        raise LookupError(reason_line(exc)) from exc
    if not results:
        raise LookupError("no results")
    log.info("JioSaavn search: %d result(s)", len(results))
    return results


def build_now_playing_embed(track: Track, player: MusicPlayer) -> discord.Embed:
    """Audira's now-playing card: title-first hierarchy, integrated artwork, live progress."""
    paused = player.is_paused
    total = track.duration
    elapsed = min(player.elapsed(), total) if total and player.current is track else 0

    parts: list[str] = []
    if track.artist:
        parts.append(f"*{truncate(track.artist, 80)}*")
        parts.append("")
    if total:
        parts.append(progress_line(elapsed, total))
        parts.append("")

    status = "Ⅱ Paused" if paused else "● Playing"
    if not total:
        status += " · Live"
    parts.append(status)

    meta = source_label(track)
    quality = quality_label(track)
    parts.append(f"{meta} · {quality}" if quality else meta)

    queue_line = f"Requested by **{truncate(track.requested_by, 40)}** · Queue {len(player.queue)}"
    modes = []
    if player.shuffle_enabled:
        modes.append("Shuffle")
    if player.loop_mode == "song":
        modes.append("Repeat song")
    elif player.loop_mode == "queue":
        modes.append("Repeat queue")
    if player.autoplay:
        modes.append("Autoplay")
    if modes:
        queue_line += " · " + " · ".join(modes)
    parts.append(queue_line)

    if player.queue:
        parts.append("")
        parts.append(f"Up next · {truncate(player.queue[0].title, 80)}")

    embed = discord.Embed(
        title=truncate(track.title, 256),
        description="\n".join(parts),
        colour=ACCENT,
        timestamp=datetime.now(timezone.utc),
    )
    if track.webpage_url:
        embed.url = track.webpage_url
    embed.set_author(name="AUDIRA · NOW PLAYING", icon_url=BRAND_ICON)
    if track.thumbnail:
        embed.set_image(url=track.thumbnail)
    return embed


def build_idle_embed(reason: str) -> discord.Embed:
    """Final card state once playback has stopped or the queue ran out."""
    embed = discord.Embed(
        title=reason,
        description="Use `/play` to start listening again.",
        colour=MUTED,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_author(name="AUDIRA", icon_url=BRAND_ICON)
    return embed


class MusicPlayer:
    """Plays a queue of local files in one voice channel. One of these exists per guild while CASE is connected."""

    def __init__(self, voice: discord.VoiceProtocol):
        self.voice = voice
        self.queue: list[Track] = []
        self.current: Track | None = None
        self.notify_channel: discord.abc.Messageable | None = None
        self._task: asyncio.Task | None = None


        self.loop_mode = "off"
        self.autoplay = False


        self.started_at: float | None = None


        self._seek_pending = False


        self._seek_replacement: Track | None = None

        self._skip_once = False

        self._recent: list[str] = []

        self.history: list[Track] = []
        self.shuffle_enabled = False
        self.action_lock = asyncio.Lock()

        self._paused_total = 0.0
        self._paused_since: float | None = None

        self._now_message: discord.Message | None = None
        self._now_task: asyncio.Task | None = None
        self._view: PlayerView | None = None
        self._card_finalized = False

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
        """Queue a track; with shuffle on it lands at a random position."""
        if self.shuffle_enabled and self.queue:
            self.queue.insert(random.randrange(len(self.queue) + 1), track)
        else:
            self.queue.append(track)
        self.start()

    def stop(self) -> None:
        """Clear the queue and halt the playback loop. Does not disconnect."""
        if self._now_task and not self._now_task.done():
            self._now_task.cancel()
        self._now_task = None
        message, self._now_message = self._now_message, None
        if message is not None and not self._card_finalized:
            asyncio.create_task(self.retire_card(message, "Playback stopped", delay=0.5))
        for track in self.queue:
            if track.temp_path:
                track.cleanup()
        self.queue.clear()
        self.current = None
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        self.voice.stop()

    async def retire_card(self, message: discord.Message | None, reason: str, delay: float = 0.0) -> None:
        """Turn the player card into a final idle state and disable its controls."""
        if delay:
            await asyncio.sleep(delay)
        if self._card_finalized or message is None:
            return
        if self.current is not None or self.queue:
            return
        self._card_finalized = True
        view = self._view
        if view is not None:
            view.retired = True
            for item in view.children:
                item.disabled = True
        try:
            await message.edit(embed=build_idle_embed(reason), view=view)
        except discord.HTTPException:
            log.debug("could not finalize the player card", exc_info=True)
        if view is not None:
            view.stop()

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
        """Seconds played of the current track; freezes while the audio is paused."""
        if self.current is None:
            return 0
        base = self.current.start_offset or 0
        if self.started_at is None:
            return base
        now = time.monotonic()
        if self.voice.is_paused():
            if self._paused_since is None:
                self._paused_since = now
            paused = self._paused_total + (now - self._paused_since)
        else:
            if self._paused_since is not None:
                self._paused_total += now - self._paused_since
                self._paused_since = None
            paused = self._paused_total
        return base + max(0, int(now - self.started_at - paused))

    async def seek(self, seconds: int) -> bool:
        """Restart the current track at `seconds`."""
        if self.current is None or not self.voice.is_connected():
            return False
        clip = await asyncio.to_thread(_cut_track, self.current, max(0, seconds))
        if clip is None:
            return False
        self._seek_pending = True
        self._seek_replacement = clip
        if seconds <= 0:
            self.current.start_offset = 0
        self.started_at = None

        self.voice.stop()
        return True

    def _remember(self, track: Track) -> None:
        """Keep the last played tracks so the Previous button has something to return to."""
        if track is None or (self.history and self.history[-1].title == track.title):
            return
        self.history.append(track)
        if len(self.history) > 20:
            self.history.pop(0)

    async def _run(self) -> None:
        """Play each queued track until the queue runs out or we get stopped."""
        try:
            while self.queue:
                if not self.voice.is_connected():
                    log.info("voice dropped, stopping playback loop")
                    break

                track = self.queue.pop(0)
                ok = await self._play_one(track)



                if self._seek_pending:
                    self._seek_pending = False
                    track = self._seek_replacement or track
                    self._seek_replacement = None
                    self.queue.insert(0, track)
                    continue

                if not ok and track.is_stream and self.voice.is_connected():



                    log.info("retrying %s with a fresh stream URL", track.title)
                    try:
                        fresh = await resolve_track(
                            track.webpage_url or track.title, track.requested_by
                        )
                    except (DownloadError, LookupError, ValueError) as exc:
                        log.warning("could not re-resolve %s: %s", track.title, exc)
                        continue
                    track = fresh
                    ok = await self._play_one(track)

                if ok:
                    self._remember(track)
                    self._apply_loop(track)



                if not self.queue and self.autoplay:
                    await self._queue_autoplay(track)

                self.current = None

        except asyncio.CancelledError:
            pass
        except Exception:


            log.exception("playback loop stopped on an unexpected error")
        finally:
            self.current = None


            if self.voice.is_connected() and not self.queue:
                log.info("queue finished - staying in the voice channel")

    async def _play_one(self, track: Track) -> bool:
        """Start one track and wait for it to finish."""
        self.current = track
        log.info("playing %s", track.title)

        before_options = ""
        if track.is_stream and not track.temp_path:



            before_options = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
            if track.headers:


                pairs = [f"{key}: {value}" for key, value in track.headers.items()]
                header_blob = "\r\n".join(pairs) + "\r\n"
                before_options += " -headers " + shlex.quote(header_blob)

        try:


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

            log.warning("voice.play failed: %s", exc)
            self.current = None
            return False



        self.started_at = time.monotonic()
        self._paused_total = 0.0
        self._paused_since = None
        self._announce(track)


        while self.voice.is_playing() or self.voice.is_paused():
            await asyncio.sleep(0.5)



        await asyncio.sleep(0.2)
        self.started_at = None

        error = errors[0] if errors else None
        if error is not None:
            log.warning("playback of %s ended early: %s", track.title, error)
        else:

            await db.log_play(
                self.voice.guild.id,
                track.title,
                channel_id=self.voice.channel.id if self.voice.channel else None,
                url=track.webpage_url,
                requested_by=track.requested_by,
                duration=track.duration,
            )

        self.current = None


        if track.temp_path and self.loop_mode == "off":
            track.cleanup()
        return error is None

    def _apply_loop(self, track: Track) -> None:
        """Put a finished track back in the queue when looping is on."""
        if self.loop_mode == "song" and not self._skip_once:
            self.queue.insert(0, track)
        elif self.loop_mode == "queue":
            self.queue.append(track)
        if self._skip_once:


            self._skip_once = False

    async def _first_playable(self, candidates: list[dict], finished: Track) -> Track | None:
        """First candidate that resolves and isn't a song we already played."""
        played = set(self._recent)
        finished_key = _autoplay_key(finished.title)
        for candidate in candidates:
            title = candidate.get("title") or ""
            key = _autoplay_key(title)
            if not key or key == finished_key or key in played:
                continue
            try:
                track = await resolve_track(
                    candidate["webpage_url"], finished.requested_by
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
        """Keep the music going with something similar when the queue runs dry."""
        if not self.voice.is_connected():
            return
        channel = self.voice.channel
        if channel is not None and len([m for m in channel.members if not m.bot]) == 0:
            log.info("autoplay off - nobody is listening")
            return

        self._recent.append(_autoplay_key(finished.title))
        self._recent = self._recent[-15:]



        queries = [
            q
            for q in (
                f"{finished.title} {finished.artist}" if finished.artist else finished.title,
                finished.title,
                finished.artist,
            )
            if q
        ]
        log.info("autoplay: looking for something like %r", finished.title)

        track = None
        candidates: list[dict] = []
        seen: set[str] = set()
        for query in queries:
            for source, limit, timeout in (
                (search_candidates, 12, 30),
            ):
                try:
                    found = await asyncio.wait_for(
                        asyncio.to_thread(source, query, limit),
                        timeout=timeout,
                    )
                except Exception as exc:
                    log.warning(
                        "autoplay %s search failed for %r: %s",
                        source.__name__,
                        query,
                        type(exc).__name__,
                    )
                    continue
                for cand in found:
                    url = cand.get("webpage_url")
                    if not url or url in seen:
                        continue
                    seen.add(url)
                    if _related_to(cand, query):
                        candidates.append(cand)
            if candidates:
                break
            log.info("autoplay: %r gave no similar candidates yet", query)

        if candidates:
            log.info("autoplay: %d candidate(s) pooled", len(candidates))
            track = await self._first_playable(candidates, finished)

        if track is None and queries:
            for query in queries:
                try:
                    candidates = await asyncio.wait_for(
                        asyncio.to_thread(search_candidates, query, limit=10),
                        timeout=60,
                    )
                except Exception as exc:
                    log.warning(
                        "autoplay search failed for %r: %s",
                        query,
                        type(exc).__name__,
                    )
                    continue
                if not candidates:
                    continue
                log.info("autoplay: %r -> %d fallback candidate(s)", query, len(candidates))
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
        """Keep the persistent Audira player card running for this song."""
        if track.announced:
            track.announced = False
            return
        if self.notify_channel is None:
            return
        self._card_finalized = False
        if self._view is None or self._view.retired:
            self._view = PlayerView(self)
        if self._now_task and not self._now_task.done():
            self._now_task.cancel()
        self._now_task = asyncio.create_task(self._now_card(track))

    async def _now_card(self, track: Track) -> None:
        """One persistent player message per guild: post once, then edit it in place."""
        view = self._view
        if view is not None:
            view.sync()
        notify = self.notify_channel

        async def push(embed: discord.Embed) -> discord.Message | None:
            message = self._now_message
            if message is not None:
                try:
                    await message.edit(embed=embed, view=view)
                    return message
                except discord.NotFound:
                    self._now_message = None
                except discord.HTTPException:
                    log.warning("could not update the player card", exc_info=True)
                    return self._now_message
            try:
                message = await notify.send(embed=embed, view=view)
            except Exception:
                log.warning("could not post the now-playing card", exc_info=True)
                return None
            self._now_message = message
            return message

        embed = build_now_playing_embed(track, self)
        message = await push(embed)
        if message is None:
            return
        signature = (embed.description,)

        try:
            while (
                self.current is track
                and (self.voice.is_playing() or self.voice.is_paused())
                and not self._card_finalized
            ):
                await asyncio.sleep(CARD_TICK)
                if self.current is not track or self._card_finalized:
                    break
                if view is not None:
                    view.sync()
                embed = build_now_playing_embed(track, self)
                if (embed.description,) == signature:
                    continue  # paused or a live stream: nothing changed, skip the API call
                message = await push(embed)
                if message is None:
                    break
                signature = (embed.description,)

            # Playback loop is gone: settle into a final idle card if nothing took over.
            for _ in range(40):
                await asyncio.sleep(1.5)
                if self.current is not None or self._card_finalized:
                    return
                if self._task is None or self._task.done():
                    break
            if self.current is None and not self._card_finalized:
                await self.retire_card(self._now_message, "Queue finished")
        except asyncio.CancelledError:
            pass


async def find_track_lyrics(track: Track) -> dict | None:
    """Best lyrics match for a queued track, trying a few query shapes."""
    title = track.title
    trimmed = re.split(r"\s+\|\s+|\s+-\s+", title)[0].strip()
    attempts = [title]
    if trimmed and trimmed.lower() != title.lower():
        attempts.append(trimmed)
    if track.artist:
        attempts.append(f"{trimmed} {track.artist}")
    for attempt in attempts:
        item = await fetch_lyrics(attempt, artist=track.artist, duration=track.duration)
        if item is not None:
            return item
    return None


def control_error(interaction: discord.Interaction, player: MusicPlayer | None) -> str | None:
    """Why this user may not use the player buttons, or None when allowed."""
    if player is None or (player.current is None and not player.queue):
        return "Nothing is playing right now."
    guild = interaction.guild
    voice = guild.voice_client if guild else None
    if voice is None or not voice.is_connected():
        return "I'm not in a voice channel right now."
    channel = getattr(getattr(interaction.user, "voice", None), "channel", None)
    if channel != voice.channel:
        return "You need to be in my voice channel to control playback."
    return None


class PlayerView(discord.ui.View):
    """The control panel attached to the persistent Audira player card."""

    def __init__(self, player: MusicPlayer):
        super().__init__(timeout=None)
        self.player = player
        self.retired = False

    def sync(self) -> None:
        """Reflect the live playback state on every button."""
        player = self.player
        active = player.current is not None
        self.prev_btn.disabled = not active or not player.history
        self.play_btn.disabled = not active
        self.play_btn.emoji = "▶" if player.is_paused else "⏸"
        self.next_btn.disabled = not active or not (player.queue or player.autoplay)
        self.shuffle_btn.style = (
            discord.ButtonStyle.success
            if player.shuffle_enabled
            else discord.ButtonStyle.secondary
        )
        repeat_style = (
            discord.ButtonStyle.secondary if player.loop_mode == "off" else discord.ButtonStyle.success
        )
        repeat_emoji = "🔂" if player.loop_mode == "song" else "🔁"
        self.repeat_btn.style = repeat_style
        self.repeat_btn.emoji = repeat_emoji
        self.restart_btn.disabled = not active
        self.stop_btn.disabled = not active and not player.queue
        self.queue_btn.disabled = not (active or player.queue)
        self.lyrics_btn.disabled = not active

    async def _edit_card(self, interaction: discord.Interaction) -> None:
        """Re-render the card in place as the answer to a button press."""
        self.sync()
        if self.player.current is not None:
            await interaction.response.edit_message(
                embed=build_now_playing_embed(self.player.current, self.player), view=self
            )
        else:
            await interaction.response.edit_message(view=self)

    @discord.ui.button(emoji="⏮", style=discord.ButtonStyle.secondary, row=0)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self.player
        error = control_error(interaction, player)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        if not player.history:
            await interaction.response.send_message("There's no previous track.", ephemeral=True)
            return

        notice = None
        async with player.action_lock:
            target = player.history.pop()
            if target.webpage_url:
                await interaction.response.send_message(
                    f"⏮ Loading **{truncate(target.title, 60)}**…", ephemeral=True
                )
                notice = await interaction.original_response()
                try:
                    target = await asyncio.wait_for(
                        resolve_track(target.webpage_url, target.requested_by), timeout=60
                    )
                except Exception:
                    player.history.append(target)
                    await notice.edit(
                        content="I couldn't reload the previous track. Try `/play` instead."
                    )
                    return
            player.queue.insert(0, target)
            player.skip()

        text = f"⏮ **{truncate(target.title, 80)}**"
        if notice is not None:
            await notice.edit(content=text)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    @discord.ui.button(emoji="▶", style=discord.ButtonStyle.primary, row=0)
    async def play_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self.player
        error = control_error(interaction, player)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        async with player.action_lock:
            if player.current is None or not (player.is_playing or player.is_paused):
                await interaction.response.send_message("Nothing is playing right now.", ephemeral=True)
                return
            if player.is_paused:
                player.voice.resume()
            else:
                player.voice.pause()
            await self._edit_card(interaction)

    @discord.ui.button(emoji="⏭", style=discord.ButtonStyle.secondary, row=0)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self.player
        error = control_error(interaction, player)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        async with player.action_lock:
            if player.queue:
                title = player.queue[0].title
                player.skip()
                text = f"⏭ **{truncate(title, 80)}**"
            elif player.autoplay:
                player.skip()
                text = "⏭ Skipped — autoplay is picking something similar…"
            else:
                text = "Nothing else is queued right now."
        await interaction.response.send_message(text, ephemeral=True)

    @discord.ui.button(emoji="🔀", style=discord.ButtonStyle.secondary, row=1)
    async def shuffle_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self.player
        error = control_error(interaction, player)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        async with player.action_lock:
            player.shuffle_enabled = not player.shuffle_enabled
            count = len(player.queue)
            if player.shuffle_enabled and count > 1:
                player.shuffle()
        await self._edit_card(interaction)
        if player.shuffle_enabled:
            note = "🔀 Shuffle on"
            if count:
                note += f" — {count} queued track{'s' if count != 1 else ''} shuffled"
            else:
                note += " — new tracks will be added in random order"
        else:
            note = "🔀 Shuffle off"
        await interaction.followup.send(note, ephemeral=True)

    @discord.ui.button(emoji="🔁", style=discord.ButtonStyle.secondary, row=1)
    async def repeat_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self.player
        error = control_error(interaction, player)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        modes = ("off", "song", "queue")
        async with player.action_lock:
            player.loop_mode = (
                modes[(modes.index(player.loop_mode) + 1) % 3]
                if player.loop_mode in modes
                else "off"
            )
        await self._edit_card(interaction)
        labels = {
            "off": "🔁 Repeat off",
            "song": "🔂 Repeating this song",
            "queue": "🔁 Repeating the queue",
        }
        await interaction.followup.send(labels[player.loop_mode], ephemeral=True)

    @discord.ui.button(emoji="🔄", style=discord.ButtonStyle.secondary, row=2)
    async def restart_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self.player
        error = control_error(interaction, player)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        async with player.action_lock:
            if not await player.seek(0):
                await interaction.response.send_message(
                    "Couldn't restart that track right now.", ephemeral=True
                )
                return
            await self._edit_card(interaction)

    @discord.ui.button(emoji="⏹", style=discord.ButtonStyle.danger, row=2)
    async def stop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self.player
        error = control_error(interaction, player)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return

        player._card_finalized = True
        self.retired = True
        for item in self.children:
            item.disabled = True
        card = player._now_message
        await interaction.response.edit_message(embed=build_idle_embed("Playback stopped"), view=self)
        if card is not None and (interaction.message is None or card.id != interaction.message.id):
            try:
                await card.edit(embed=build_idle_embed("Playback stopped"), view=self)
            except discord.HTTPException:
                log.debug("could not retire the card from the stop button", exc_info=True)

        guild = interaction.guild
        cog = interaction.client.get_cog("Music")
        if cog is not None and guild is not None:
            cog.players.pop(guild.id, None)
        try:
            await player.shutdown()
        except Exception:
            log.warning("stop button could not shut the player down cleanly", exc_info=True)

        view = self

        async def _release() -> None:
            await asyncio.sleep(1)
            view.stop()

        asyncio.create_task(_release())

    @discord.ui.button(emoji="📜", style=discord.ButtonStyle.secondary, row=1)
    async def queue_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self.player
        error = control_error(interaction, player)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        pages = build_queue_pages(player)
        pager = QueuePager(pages)
        await interaction.response.send_message(embed=pages[0], view=pager, ephemeral=True)
        pager.message = await interaction.original_response()
        pager.sync_state()

    @discord.ui.button(emoji="🎵", style=discord.ButtonStyle.secondary, row=2)
    async def lyrics_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self.player
        error = control_error(interaction, player)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        track = player.current
        if track is None:
            await interaction.response.send_message("Nothing is playing right now.", ephemeral=True)
            return
        await interaction.response.send_message("🎵 Looking up lyrics…", ephemeral=True)
        notice = await interaction.original_response()
        try:
            item = await find_track_lyrics(track)
        except Exception:
            log.warning("lyrics lookup failed", exc_info=True)
            item = None
        if item is None:
            await notice.edit(content=f"I couldn't find lyrics for **{truncate(track.title, 60)}**.")
            return
        text = (item.get("plainLyrics") or "").strip()
        if len(text) > 3900:
            text = text[:3900].rsplit("\n", 1)[0] + "\n…"
        embed = discord.Embed(
            title=item.get("trackName") or track.title,
            description=text or "Lyrics were empty for this track.",
            colour=ACCENT,
        )
        if item.get("artistName"):
            embed.set_author(name=item["artistName"])
        await notice.edit(content=None, embed=embed)


class QueuePager(discord.ui.View):
    """Small ephemeral pager behind the player's Queue button."""

    def __init__(self, pages: list[discord.Embed]):
        super().__init__(timeout=60)
        self.pages = pages
        self.index = 0
        self.message: discord.WebhookMessage | None = None
        self.sync_state()

    def sync_state(self) -> None:
        self.prev_btn.disabled = self.index <= 0
        self.next_btn.disabled = self.index >= len(self.pages) - 1
        self.page_btn.label = f"{self.index + 1} / {len(self.pages)}"

    @discord.ui.button(emoji="◀", style=discord.ButtonStyle.secondary, row=0)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = max(0, self.index - 1)
        self.sync_state()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="1 / 1", style=discord.ButtonStyle.secondary, row=0, disabled=True)
    async def page_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        return

    @discord.ui.button(emoji="▶", style=discord.ButtonStyle.secondary, row=0)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = min(len(self.pages) - 1, self.index + 1)
        self.sync_state()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                log.debug("could not disable the expired queue pager", exc_info=True)
        self.stop()


def build_queue_pages(player: MusicPlayer, per_page: int = 10) -> list[discord.Embed]:
    """The queue split into small embeds: now playing + a numbered slice."""
    entries = player.queue
    total_pages = max(1, (len(entries) + per_page - 1) // per_page)
    pages: list[discord.Embed] = []
    for index in range(total_pages):
        chunk = entries[index * per_page : (index + 1) * per_page]
        embed = discord.Embed(
            title=f"Queue — {len(entries)} track{'s' if len(entries) != 1 else ''}",
            colour=ACCENT,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_author(name="Audira", icon_url=BRAND_ICON)
        if player.current is not None:
            now = f"**{truncate(player.current.title, 80)}**"
            if player.current.artist:
                now += f" — {truncate(player.current.artist, 40)}"
            embed.add_field(name="● Now playing", value=now, inline=False)
        lines = []
        for offset, track in enumerate(chunk):
            position = index * per_page + offset + 1
            line = f"`{position:02d}` **{truncate(track.title, 60)}**"
            if track.artist:
                line += f" — {truncate(track.artist, 30)}"
            if track.duration:
                line += f" `{format_time(track.duration)}`"
            lines.append(line)
        embed.add_field(
            name="Up next",
            value="\n".join(lines) if lines else "Nothing queued yet.",
            inline=False,
        )
        embed.set_footer(text=f"Page {index + 1} / {total_pages}")
        pages.append(embed)
    return pages


class SearchPicker(discord.ui.View):
    """A dropdown of search results that queues whichever one is picked."""

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
                resolve_track(
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


        self.default_autoplay = False

    def get_player(self, guild: discord.Guild) -> MusicPlayer | None:
        return self.players.get(guild.id)

    async def ensure_player(self, interaction: discord.Interaction) -> tuple[MusicPlayer | None, str | None]:
        """Make sure CASE is connected to the user's channel and ready to play."""
        member = interaction.user.voice
        if member is None:
            return None, "You're not in a voice channel."

        if not ffmpeg_available():
            return None, FFMPEG_MISSING

        voice = interaction.guild.voice_client



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
        song="Song name, JioSaavn link, or a file in music/"
    )
    async def play(self, interaction: discord.Interaction, song: str):


        await interaction.response.defer()
        log.info("play: resolving %r", song)
        started = time.monotonic()

        try:



            track = await asyncio.wait_for(
                resolve_track(song, interaction.user.display_name),
                timeout=100,
            )
        except asyncio.TimeoutError:
            log.warning("resolve timed out for %r", song)
            await interaction.followup.send(
                "That took too long to load. Try a direct link or a shorter search."
            )
            return
        except ValueError as exc:

            log.warning("rejected %r: %s", song, exc)
            await interaction.followup.send(str(exc))
            return
        except (DownloadError, LookupError) as exc:

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
        """Connect, queue `track`, and reply. Caller must have deferred. Shared by /play and /search so both behave identically."""
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
        player.add(track)

        if was_idle:
            await interaction.followup.send(f"Now playing **{track.title}**.")
        else:
            await interaction.followup.send(
                f"Added **{track.title}** to the queue - position {len(player.queue)}."
            )
        log.info("play: reply sent")

    @discord.app_commands.command(name="pause", description="Pause the current song.")
    async def pause(self, interaction: discord.Interaction):
        player = self.get_player(interaction.guild)
        if player is None or player.current is None:
            await interaction.response.send_message("Nothing is playing right now.")
            return

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
        view = player._view
        if view is None or view.retired:
            view = player._view = PlayerView(player)
        view.sync()
        await interaction.response.send_message(
            embed=build_now_playing_embed(player.current, player),
            view=view,
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

        if not await player.seek(seconds):
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


            if _lyrics_key(title) == _lyrics_key(player.current.title):
                artist = player.current.artist
                duration = player.current.duration

        await interaction.response.defer()



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

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):


        if member.id == self.bot.user.id and after.channel is None:
            player = self.players.get(member.guild.id)
            if player:
                player.stop()
                del self.players[member.guild.id]


async def setup(bot):
    await bot.add_cog(Music(bot))


