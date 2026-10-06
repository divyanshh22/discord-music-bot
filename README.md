# Audira

A personal Discord music bot: one command plays a song in your voice channel.
It plays matching files from `music/`, and for anything else it searches
**JioSaavn** (320k) first, then **SoundCloud** and **YouTube** — which is also
what a pasted link uses.

Built for my own private server.

## Features

- Join and leave voice channels
- Play local audio files from the `music/` folder
- Songs from the internet: **JioSaavn** first (320k, about 1s, no account,
  no bot check), then **SoundCloud** (128k), then **YouTube** through yt-dlp
  - which is what a pasted YouTube or SoundCloud link uses
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
2. **Create Application** → name it `Audira`
3. On the **Bot** tab → **Reset Token** → copy it (you only see it once)
4. No privileged intents needed. Audira uses slash commands only.
5. Invite Audira using the OAuth2 URL Generator:

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

On startup Audira logs that it's online and syncs slash commands. Discord can take
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

- The queue lives in memory. Restart Audira and it's gone.
- When the queue empties, CASE stays in the channel - only `/stop` makes it leave.
- `music/` is read once per `/play`, so new files work without a restart.
- `/history` only works when `DATABASE_URL` is set. Locally the bot runs fine
  without one and just says history is off.

## Deploying to Render

1. Push this repo to GitHub.
2. On Render, create a **Web Service** from the repo (branch `main`).
3. In your `.env` or as Render env vars: `DISCORD_TOKEN`. Add a
   `DATABASE_URL` too if you want `/history`.
4. Paste the build command below in **Settings → Build & Deploy**.
5. Deploy. Render's health check passes because Audira listens on `$PORT`.

### The PO-token provider

YouTube challenges data-centre IPs (Render, a VPS) with *"Sign in to confirm
you're not a bot"*, and yt-dlp cannot answer that on its own.
`bgutil-ytdlp-pot-provider` supplies the proof-of-origin token. The plugin
itself comes from `requirements.txt`; the token generator is a node.js app.
Render's Python runtime has no node.js by default, so the build also bakes a
node binary into the repo (at `node/bin/node`) that the bot finds at runtime.
The whole setup lives in `render-build.sh` - the Build Command is just:

```bash
bash render-build.sh
```

The `rm -rf` matters: Render's build cache kept a `potprovider/` without its
`package-lock.json`, and `npm ci` refuses to run without one.

Put `bash render-build.sh` as the service's **Build Command** (Settings →
Build & Deploy). After a deploy, `bot.py` logs
`po-token provider: .../potprovider/server`. Without it, or without node,
YouTube links fail with *"Sign in to confirm you're not a bot"* from a
flagged IP.

### YouTube cookies (optional but strongest)

Data-centre rotation occasionally defeats PO-token generation too. The
reliable fallback is the bot's own YouTube session: export a
**Netscape-format** cookies file (e.g. with the *Get cookies.txt LOCALLY*
extension), read it into a base64 string and set it as a `YOUTUBE_COOKIES`
env var (`DEFAULT_SEARCH` still works without it):

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("C:\path\to\cookies.txt"))
```

`YOUTUBE_COOKIES` accepts either that base64 blob or a file path, and can
also live in `.env` for local runs.

Free-tier caveats: the web service spins down after ~15 min of no HTTP
traffic, and the free Postgres database expires after 30 days.