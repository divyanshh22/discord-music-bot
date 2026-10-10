import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
MUSIC_DIR = BASE_DIR / "music"

load_dotenv(BASE_DIR / ".env")

TOKEN = os.getenv("DISCORD_TOKEN")

# Bumped by hand when behaviour changes so /version proves which code is live.
BUILD = "2026-10-11.2-lavalink"

# Render exposes these automatically; on a laptop they fall back to "local".
GIT_COMMIT = os.getenv("RENDER_GIT_COMMIT", "local")
GIT_BRANCH = os.getenv("RENDER_GIT_BRANCH", "local")


DATABASE_URL = os.getenv("DATABASE_URL")


LAVALINK_URI = os.getenv("LAVALINK_URI", "").strip()
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD", "").strip()
LAVALINK_LOCAL_AUDIO_BASE_URL = os.getenv("LAVALINK_LOCAL_AUDIO_BASE_URL", "").rstrip("/")
LOCAL_AUDIO_SECRET = os.getenv("LOCAL_AUDIO_SECRET", "").strip()



PORT = os.getenv("PORT")

if not TOKEN or TOKEN == "your_bot_token_here":
    raise RuntimeError(
        "DISCORD_TOKEN is not set. Put your real bot token in the .env file."
    )
