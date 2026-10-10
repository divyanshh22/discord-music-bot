import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
MUSIC_DIR = BASE_DIR / "music"

load_dotenv(BASE_DIR / ".env")

TOKEN = os.getenv("DISCORD_TOKEN")

# Bumped by hand when behaviour changes so /version proves which code is live.
BUILD = "2026-10-11.1-youtube-music"

# Render exposes these automatically; on a laptop they fall back to "local".
GIT_COMMIT = os.getenv("RENDER_GIT_COMMIT", "local")
GIT_BRANCH = os.getenv("RENDER_GIT_BRANCH", "local")


DATABASE_URL = os.getenv("DATABASE_URL")



PORT = os.getenv("PORT")

if not TOKEN or TOKEN == "your_bot_token_here":
    raise RuntimeError(
        "DISCORD_TOKEN is not set. Put your real bot token in the .env file."
    )
