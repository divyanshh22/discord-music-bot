from __future__ import annotations

import base64
import hashlib
import hmac
import time
from pathlib import Path, PurePosixPath

from aiohttp import web

import config

AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".flac"}
URL_LIFETIME_SECONDS = 900


def signed_audio_url(path: Path) -> str:
    """Create a short-lived URL that lets Lavalink fetch one file under music/."""
    if not config.LAVALINK_LOCAL_AUDIO_BASE_URL or not config.LOCAL_AUDIO_SECRET:
        raise RuntimeError("Local audio requires LAVALINK_LOCAL_AUDIO_BASE_URL and LOCAL_AUDIO_SECRET.")

    root = config.MUSIC_DIR.resolve()
    resolved = path.resolve(strict=True)
    relative = resolved.relative_to(root).as_posix()
    if not resolved.is_file() or resolved.suffix.lower() not in AUDIO_EXTENSIONS:
        raise ValueError("Unsupported local audio file.")

    asset = base64.urlsafe_b64encode(relative.encode("utf-8")).decode("ascii").rstrip("=")
    expires = int(time.time()) + URL_LIFETIME_SECONDS
    message = f"{asset}:{expires}".encode("ascii")
    signature = hmac.new(
        config.LOCAL_AUDIO_SECRET.encode("utf-8"), message, hashlib.sha256
    ).hexdigest()
    return (
        f"{config.LAVALINK_LOCAL_AUDIO_BASE_URL}/local-audio/{asset}"
        f"?expires={expires}&signature={signature}"
    )


async def serve_local_audio(request: web.Request) -> web.StreamResponse:
    """Serve only a valid, signed audio-file request from the Lavalink node."""
    secret = config.LOCAL_AUDIO_SECRET
    if not secret:
        raise web.HTTPServiceUnavailable()

    asset = request.match_info.get("asset", "")
    try:
        expires = int(request.query.get("expires", ""))
        supplied = request.query.get("signature", "")
        message = f"{asset}:{expires}".encode("ascii")
        expected = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
        if expires < int(time.time()) or expires > int(time.time()) + URL_LIFETIME_SECONDS + 30:
            raise ValueError("expired URL")
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("invalid signature")
        relative = base64.urlsafe_b64decode(asset + "=" * (-len(asset) % 4)).decode("utf-8")
        relative_path = PurePosixPath(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError("invalid path")
        root = config.MUSIC_DIR.resolve()
        path = root.joinpath(*relative_path.parts).resolve(strict=True)
        path.relative_to(root)
        if not path.is_file() or path.suffix.lower() not in AUDIO_EXTENSIONS:
            raise ValueError("unsupported file")
    except (ValueError, OSError, UnicodeError):
        raise web.HTTPNotFound()

    return web.FileResponse(path)
