import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
MUSIC_DIR = BASE_DIR / "music"

load_dotenv(BASE_DIR / ".env")

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN or TOKEN == "your_bot_token_here":
    raise RuntimeError(
        "DISCORD_TOKEN is not set. Put your real bot token in the .env file."
    )