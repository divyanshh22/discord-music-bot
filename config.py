import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
MUSIC_DIR = BASE_DIR / "music"

load_dotenv(BASE_DIR / ".env")

TOKEN = os.getenv("DISCORD_TOKEN")

# Optional. Without it CASE runs exactly as before, only /history is off.
DATABASE_URL = os.getenv("DATABASE_URL")

# Render assigns a port to web services. Blank locally, so the tiny health
# server in bot.py stays off when we run on our own machine.
PORT = os.getenv("PORT")

if not TOKEN or TOKEN == "your_bot_token_here":
    raise RuntimeError(
        "DISCORD_TOKEN is not set. Put your real bot token in the .env file."
    )