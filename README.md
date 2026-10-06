# CASE

A personal Discord music bot I built to learn Discord bot development from the ground up.
It plays local audio files, so you can actually see how the voice/audio layer works
before adding anything more complicated like YouTube or Lavalink.

Built for my own private server. Version 1 is local files only — no database, no Redis,
no cloud anything.

## Features

- Join and leave voice channels
- Play local audio files from the `music/` folder
- Pause, resume, skip and stop
- Per-server queue with automatic advance to the next song
- `/queue` and `/nowplaying` displays
- Friendly error messages instead of raw tracebacks
- Cleans up after itself when disconnected

## Tech stack

- Python 3.11+
- [discord.py](https://discordpy.readthedocs.io/) 2.x
- FFmpeg (external program, not a Python package)
- python-dotenv

## Project structure

```
CASE/
├── bot.py             # connects to Discord, loads cogs, sets presence
├── config.py          # reads .env and exposes paths
├── requirements.txt
├── .env               # bot token (git-ignored)
├── music/             # your local audio files go here
└── cogs/
    ├── general.py     # /ping, /help
    └── music.py       # everything voice + playback related
```

The split into cogs is just discord.py's built-in way of grouping commands. Two
files is plenty at this size.

## Installation

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## FFmpeg setup

FFmpeg is a standalone program that converts your `.mp3` into the Opus audio
Discord accepts. `pip` cannot install it.

**Windows:**

1. Download a build from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) (`ffmpeg-release-essentials.zip`)
2. Extract it somewhere permanent, e.g. `C:\ffmpeg`
3. Add `C:\ffmpeg\bin` to your system `PATH` (System Properties → Environment Variables → Path → New)
4. Open a **new** Command Prompt and check:

```
ffmpeg -version
```

If you see a version banner, it's working. Restart your IDE/terminal so it picks up the new PATH.

**macOS / Linux:**

```bash
brew install ffmpeg          # macOS
sudo apt install ffmpeg      # Debian/Ubuntu
```

## Discord bot setup

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. **Create Application** → name it `CASE`
3. On the **Bot** tab → **Reset Token** → copy it (you only see it once)
4. No privileged intents needed. CASE uses slash commands only.
5. Invite CASE using the OAuth2 URL Generator:

   - Scopes: `bot` + `applications.commands`
   - Permissions: `View Channels`, `Send Messages`, `Connect`, `Speak`, `Use Voice Activity`

   ```
   https://discord.com/api/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=3148800&scope=bot%20applications.commands
   ```

   `3148800` is the bitfield for exactly those five permissions. Administrator is not needed.

   | Permission | Why |
   |---|---|
   | View Channels | See the text channel to reply in |
   | Send Messages | Reply to slash commands |
   | Connect | Join your voice channel |
   | Speak | Send audio into the channel |
   | Use Voice Activity | Voice isn't flagged as screen-share activity |

6. Put the token in `.env`:

   ```env
   DISCORD_TOKEN=your_real_token_here
   ```

   `.env` is in `.gitignore`. Never paste your token in chat or commit it.

## Running

```bash
venv\Scripts\activate
python bot.py
```

On startup CASE logs that it's online and syncs slash commands. Discord can take
up to an hour to propagate new global commands, but they normally appear within
seconds.

## Commands

| Command | Description |
|---|---|
| `/ping` | Bot latency |
| `/help` | Command list |
| `/join` | Join your voice channel |
| `/leave` | Leave the voice channel |
| `/play <song>` | Play a file from `music/`, or queue it |
| `/search <query>` | Pick from a list of results before playing |
| `/pause` | Pause playback |
| `/resume` | Resume playback |
| `/skip` | Next song, or stop if nothing is queued |
| `/stop` | Stop and clear the queue |
| `/seek 1:30` | Jump to a spot in the current song |
| `/loop off\|song\|queue` | Repeat the song or the whole queue |
| `/shuffle` | Randomise the queue |
| `/remove <n>` | Take one song out of the queue |
| `/clear` | Empty the queue, keep the current song |
| `/jump <n>` | Skip ahead to a queue position |
| `/autoplay` | Keep picking similar songs when the queue runs dry |
| `/queue` | Show what's playing and what's next |
| `/nowplaying` | Current song, with a progress bar |
| `/lyrics` | Lyrics for the current song, or any search |
| `/history` | Recently played songs (needs a database) |

Song lookup is forgiving: `believer`, `Believer.mp3` and `BE_LIEVER` all match
`music/believer.mp3`.

## Adding songs

Drop `.mp3`, `.wav`, `.ogg`, `.m4a` or `.flac` files into `music/`. They're
git-ignored, so your library won't end up in the repo.

```
music/
├── believer.mp3
├── faded.mp3
└── perfect.mp3
```

## Notes and known limits

- The queue lives in memory. Restart CASE and it's gone.
- When the queue empties, CASE leaves the voice channel on its own.
- `music/` is read once per `/play`, so new files work without a restart.
- `/history` only works when `DATABASE_URL` is set. Locally the bot runs fine
  without one and just says history is off.

## Deploying to Render

`render.yaml` describes the whole setup: one **Web Service** (Render's free
plan has no background workers) plus a free **PostgreSQL** database.

1. Push this repo to GitHub.
2. On Render choose **New + → Blueprint** and point it at the repo.
3. Add an environment variable `DISCORD_TOKEN` with your bot token
   (it is left blank on purpose, `sync: false`).
4. Deploy. Render creates `case-db` and passes its connection string to the
   bot as `DATABASE_URL` automatically.

CASE listens on `$PORT` so Render's health check passes, and the Discord
connection itself is outbound, so no extra networking is needed.

Free-tier caveats: the web service spins down after ~15 min of no HTTP
traffic, and the free Postgres database expires after 30 days.

## Roadmap

Roughly in the order I expect to attempt them:

- **Controls** — `/remove`, `/clear`, `/shuffle`, `/loop`, `/volume`
- **Audio source layer** — separate track resolution from playback so online
  sources can be added without rewriting the player
- **Buttons** — pause/skip/stop controls on the Now Playing embed
- **Persistence** — SQLite for per-server settings (DJ role, default channel)
- **Deployment** — logging, restart handling, Docker if it earns its place