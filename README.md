# Audira

A personal Discord music bot: one command plays a song in your voice channel.
It plays matching files from `music/` or searches **YouTube Music** for a song.

Built for my own private server.

## Features

- Join and leave voice channels
- Play local audio files from the `music/` folder
- Songs from YouTube Music: metadata and ranking from `ytmusicapi`, playback through Lavalink v4
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
- [Wavelink](https://wavelink.readthedocs.io/) 3.5.2 for Lavalink v4
- [Lavalink](https://github.com/lavalink-devs/Lavalink) 4.2.2 and the official YouTube source plugin 1.18.2
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) retained for search fallback and legacy JioSaavn metadata only; active audio playback uses Lavalink
- [ytmusicapi](https://github.com/sigma67/ytmusicapi) for YouTube Music search
  and catalogue metadata
- FFmpeg is not required for active Lavalink playback
- aiohttp for Render health checks and signed local-file streaming
- python-dotenv

## Project structure

```
CASE/
├── bot.py             # connects to Discord, loads cogs, sets presence
├── config.py          # reads .env and exposes paths
├── requirements.txt
├── .env               # bot token (git-ignored)
├── audio_files.py      # signed short-lived local-file URLs for Lavalink
├── lavalink/           # Lavalink Dockerfile and server configuration
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

Legacy yt-dlp/FFmpeg helper code and dependencies remain in the repository
until the Render smoke test is complete. yt-dlp is still used for search
fallback and legacy JioSaavn metadata; neither yt-dlp nor FFmpeg is used by the
active Lavalink `/play` audio path. There is no automatic FFmpeg playback
fallback if Lavalink is unavailable.

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

6. Put the bot token and local Lavalink connection in `.env`:

   ```env
   DISCORD_TOKEN=your_real_token_here
  LAVALINK_URI=http://localhost:2333
  LAVALINK_PASSWORD=the_same_random_password_used_by_lavalink
   ```

  Start a Lavalink service using `lavalink/application.yml` before starting
  the bot. `.env` is in `.gitignore`. Never paste credentials into chat or
  commit them.

  If playing local files in `music/`, also set `PORT`,
  `LAVALINK_LOCAL_AUDIO_BASE_URL` (the bot's address reachable from Lavalink),
  and a separate random `LOCAL_AUDIO_SECRET`.

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
4. The selected YouTube URL is loaded through Lavalink's official YouTube source
  plugin and checked against the ranked candidate by video id.

### When things fail

Errors are classified on purpose; the bot never turns one kind into another:

- **Search itself is down / the IP is blocked** -> "YouTube Music search isn't
  available right now", with the real reason. Not "no results".
- **Found, but no audio** (sign-in challenge, 403, unavailable video) -> a
  `StreamResolveError` saying the track exists but wouldn't load. YouTube may
  still challenge Render datacenter IPs when Lavalink resolves or plays a track.
- **Lavalink disconnect or playback exception** -> the actual node/track error
  is logged separately from YouTube Music search errors.
- **Autoplay rails fail** -> they are skipped individually; a broken rail never
  disables autoplay or repeats the same failed track.

## Deploying to Render

### One free Web Service

This option runs Lavalink and the bot in one container, so it needs no separate
Private Service or internal Lavalink URL.

1. In Render, create or edit a **Web Service** for this repository on branch
   `main`. Choose **Docker**, leave Root Directory blank, and set Dockerfile
   Path to `Dockerfile` at the repository root.
2. Leave Build Command and Start Command blank so Render uses the Dockerfile.
   Do not use `bash render-build.sh` for this combined image.
3. In the Web Service's **Environment** tab, set:

   - `DISCORD_TOKEN`: your existing Discord bot token.
   - `LAVALINK_PASSWORD`: a generated secret. Lavalink and Wavelink share it;
     the entrypoint sets Wavelink's URI to `http://127.0.0.1:2333` automatically.
   - `DATABASE_URL`: optional, only for `/history` and persisted likes.

4. Deploy. The entrypoint starts the pinned Lavalink node, waits for port 2333,
   then starts the bot and its HTTP health server on Render's `$PORT`.

The container limits Java heap, metaspace, direct memory, and thread stacks to
fit a 512 MB service as a best effort. **This is not guaranteed to fit or stay
awake:** Lavalink and Python compete for the same 512 MB and 0.1 CPU, and
Render Free Web Services can spin down when idle. If Render restarts the
service for memory use or sleep, reliable 24/7 playback requires an always-on
host. Lavalink's port is bound to loopback in this combined mode and is not
exposed as a public endpoint.

`lavalink/Dockerfile` remains available for a separate paid Private Service if
you later choose that arrangement; do not use it for the single-service setup.

### Optional YouTube authentication

The official plugin supports environment-backed `YOUTUBE_PO_TOKEN` and
`YOUTUBE_VISITOR_DATA`, or OAuth with `YOUTUBE_OAUTH_ENABLED=true` and
`YOUTUBE_OAUTH_REFRESH_TOKEN`. Leave OAuth disabled unless you deliberately
configure it. The plugin warns that OAuth can cause rate limits or account
termination; use a dedicated burner account, never a personal account. PO
tokens apply only to the documented Web clients. Neither method guarantees
playback or prevents Render IP restrictions. Do not paste tokens into source,
commit them, or include them in support logs.

### Verification checklist

1. Render logs show Lavalink `4.2.2`, YouTube plugin `1.18.2`, then
   `Lavalink node connected: tango-lavalink`.
2. `/version` reports Wavelink `3.5.2`.
3. Try `/search Those Eyes by New West`, then `/play Those Eyes by New West`.
  Confirm metadata is ranked first and a Lavalink track-start event appears.
4. Test `/pause`, `/resume`, `/seek`, `/skip`, `/queue`, `/loop`, `/autoplay`,
  `/stop`, `/join`, and `/leave`.
5. Test `music/` playback if using local files; confirm the Lavalink service can
  reach the configured signed local-audio URL.

Search, resolver, event, and control tests in this repository are mocked; they
do not prove that the Render services can reach Discord voice or that YouTube
will allow a particular datacenter IP. If the plugin reports a bot challenge,
Lavalink, client choice, OAuth, or a PO token cannot guarantee a fix; use a
legitimate alternative audio source if YouTube continues to block the host.