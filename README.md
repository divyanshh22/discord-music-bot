# Audira

A personal Discord music bot: one command plays a song in your voice channel.
It plays matching files from `music/` or searches **YouTube Music** for a song.

Built for my own private server.

## Features

- Join and leave voice channels
- Play local audio files from the `music/` folder
- Songs from YouTube Music: metadata from `ytmusicapi`, audio from yt-dlp
- Paste a YouTube / YouTube Music link and it plays that exact video
- Pause, resume, skip and stop
- Per-server queue with automatic advance to the next song
- `/queue` and `/nowplaying` displays
- `/autoplay` that follows real relationships (watch playlist, artist, album,
  related artists) instead of keyword guesses
- `/likes` saved per user per server, surviving restarts when a database is set
- Friendly error messages instead of raw tracebacks - an IP block is reported
  as an IP block, never as "no results"
- Cleans up after itself when disconnected

## Tech stack

- Python 3.11+
- [discord.py](https://discordpy.readthedocs.io/) 2.x
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) for extraction and playback
- [ytmusicapi](https://github.com/sigma67/ytmusicapi) for YouTube Music search
  and catalogue metadata
- FFmpeg (external program, not a Python package)
- Node.js 22+ (external program: yt-dlp needs it to solve YouTube's player JS)
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

## Node.js setup

yt-dlp solves YouTube's player signature and `n` challenges with a JavaScript
runtime. It wants **Node 22 or newer**; `cogs/music.py` looks for one on your
`PATH` (and in `./node/bin/node`, which `render-build.sh` installs when needed)
and logs the version it found at startup.

```bash
node --version                # v22.0.0 or newer
```

If it's older, install a newer one from [nodejs.org](https://nodejs.org/) (or
`nvm install 22`). Without a supported runtime, some YouTube formats go missing
and extraction gets flaky.

Two optional yt-dlp add-ons are installed from `requirements.txt` and make a
datacenter IP behave much better:

- `yt-dlp-ejs` - the signature/n-sig solvers themselves, shipped as a pip
  package so the build never has to download code from GitHub at runtime.
- `bgutil-ytdlp-pot-provider` - mints YouTube proof-of-origin tokens locally.
  The server half is cloned and compiled by `render-build.sh` (see below);
  locally, `potprovider/` in this repo is already built and picked up
  automatically.

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
- Playback always goes through YouTube/YouTube Music. Spotify, Apple Music,
  Deezer, Tidal and Amazon Music links are DRM-protected and are rejected with
  a message telling you to search by name instead.
- Playlists are not queued as a whole - paste one song, or search.

### How a request is resolved

1. A local file in `music/` matches the name? Play it.
2. Otherwise a YouTube / YouTube Music link plays that exact video (no re-search).
   JioSaavn links still resolve too, because likes saved before the provider
   change point at them.
3. Otherwise the text is searched on YouTube Music, ranked against what you typed
   (title, artist, edition, duration, popularity), and only played when the top
   hit is confident - otherwise `/play` shows a picker.
4. The chosen result is then extracted by yt-dlp (the player-client fallbacks are
   tried in order) and checked against the ranking result by video id, so the
   song that plays is the song that was validated.

### When things fail

Errors are classified on purpose; the bot never turns one kind into another:

- **Search itself is down / the IP is blocked** -> "YouTube Music search isn't
  available right now", with the real reason. Not "no results".
- **Found, but no audio** (sign-in challenge, 403, unavailable video) -> a
  `StreamResolveError` saying the track exists but wouldn't load. On a
  datacenter IP the usual cause is YouTube asking the server to prove it isn't
  a bot; the PO-token provider is there to make that rare, not impossible.
- **Expired stream URL** (googlevideo links last a few hours) -> the queue
  re-resolves the link once, automatically, and logs it.
- **Autoplay rails fail** -> they are skipped individually; a broken rail never
  disables autoplay or repeats the same failed track.

## Deploying to Render

1. Push this repo to GitHub.
2. On Render, create a **Web Service** from the repo (branch `main`).
3. In your `.env` or as Render env vars: `DISCORD_TOKEN`. Add a
   `DATABASE_URL` too if you want `/history` and `/likes`.
4. Paste the build command below in **Settings → Build & Deploy**.
5. Deploy. Render's health check passes because Audira listens on `$PORT`.

### Render build

```bash
bash render-build.sh
```

Put `bash render-build.sh` as the service's **Build Command** (Settings →
Build & Deploy). The script:

- installs `requirements.txt` (yt-dlp, yt-dlp-ejs, ytmusicapi, the PO-token
  provider plugin, ...)
- checks for Node 22+ (Render's native runtime already ships Node; if its
  version is too old the script downloads a local copy into `./node`, which
  the bot picks up)
- clones and compiles the bgutil PO-token server (`potprovider/`) so yt-dlp
  can mint proof-of-origin tokens from a datacenter IP. This step is
  best-effort: if it fails, the build still succeeds and the logs say so.

Verify after the first deploy (Render's log stream, in order):

1. `node.js: ... (v22.x)` - a supported runtime was found
2. `ffmpeg: /usr/bin/ffmpeg` - Render pre-installs it
3. `po-token provider: .../potprovider/server ...` - the helper was built
4. In the bot log or `/version`: the new build string

Free-tier caveats: the web service spins down after ~15 min of no HTTP
traffic, and the free Postgres database expires after 30 days. More
importantly for audio bots: **a free-tier IP is shared and datacenter-hosted**,
which is exactly the kind of IP YouTube sometimes challenges. The build above
mitigates that (PO token + JS solvers), but it cannot promise it away - if
playback is refused, the logs will say so honestly rather than claiming the
song doesn't exist.