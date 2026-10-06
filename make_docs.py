"""Generates CASE_TECHNICAL_DOCUMENT.pdf - a technical write-up of the bot."""

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)

OUT = "CASE_TECHNICAL_DOCUMENT.pdf"

ACCENT = colors.HexColor("#4B6BF2")
INK = colors.HexColor("#1A1D26")
MUTED = colors.HexColor("#6B7280")
CODE_BG = colors.HexColor("#F4F5F7")
LINE = colors.HexColor("#D8DBE2")

styles = getSampleStyleSheet()

H1 = ParagraphStyle(
    "H1", parent=styles["Heading1"], fontName="Helvetica-Bold",
    fontSize=20, leading=25, textColor=ACCENT, spaceBefore=14, spaceAfter=8,
)
H2 = ParagraphStyle(
    "H2", parent=styles["Heading2"], fontName="Helvetica-Bold",
    fontSize=13.5, leading=18, textColor=INK, spaceBefore=12, spaceAfter=5,
)
H3 = ParagraphStyle(
    "H3", parent=styles["Heading3"], fontName="Helvetica-Bold",
    fontSize=11, leading=14, textColor=MUTED, spaceBefore=8, spaceAfter=3,
)
BODY = ParagraphStyle(
    "Body", parent=styles["BodyText"], fontName="Helvetica",
    fontSize=9.6, leading=14.5, textColor=INK, alignment=TA_LEFT, spaceAfter=6,
)
SMALL = ParagraphStyle("Small", parent=BODY, fontSize=8.4, leading=12, textColor=MUTED)
CODE = ParagraphStyle(
    "Code", fontName="Courier", fontSize=8.2, leading=11.4,
    textColor=INK, leftIndent=7, rightIndent=7, spaceBefore=3, spaceAfter=8,
)
BULLET = ParagraphStyle(
    "Bullet", parent=BODY, leftIndent=13, bulletIndent=3, spaceAfter=3,
)


def code(text):
    return Preformatted(text.strip("\n"), CODE)


def bullets(items, style=BULLET):
    return Table(
        [[Paragraph(f"- {i}", style)] for i in items],
        colWidths=[156 * mm],
        hAlign="LEFT",
        style=TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]),
    )


def table(rows, widths):
    t = Table(rows, colWidths=widths, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.6),
        ("TEXTCOLOR", (0, 1), (-1, -1), INK),
        ("BACKGROUND", (0, 1), (-1, -1), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8F9FB")]),
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def header_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.6)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 287 * mm, "CASE - Discord Music Bot")
    canvas.drawRightString(192 * mm, 287 * mm, "Technical Document")
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.4)
    canvas.line(18 * mm, 284 * mm, 192 * mm, 284 * mm)
    canvas.line(18 * mm, 13 * mm, 192 * mm, 13 * mm)
    canvas.drawCentredString(105 * mm, 9 * mm, f"Page {doc.page}")
    canvas.restoreState()


def build():
    doc = BaseDocTemplate(
        OUT, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=20 * mm, bottomMargin=18 * mm,
        title="CASE - Technical Document",
        author="CASE",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")
    doc.addPageTemplates([PageTemplate(id="p", frames=[frame], onPage=header_footer)])

    s = []
    A = s.append

    # ---------------- title ----------------
    A(Paragraph("CASE", ParagraphStyle(
        "T", fontName="Helvetica-Bold", fontSize=34, leading=38, textColor=ACCENT,
        spaceAfter=2,
    )))
    A(Paragraph("Discord Music Bot - Technical Document", H2))
    A(Paragraph(
        "A personal Discord music bot written in Python. This document explains the "
        "technology used, how each piece works, and the workflow a command follows "
        "from typing to sound.", BODY,
    ))
    A(Spacer(1, 8))

    A(table([
        ["Field", "Value"],
        ["Bot name", "CASE"],
        ["Language", "Python 3.13"],
        ["Framework", "discord.py 2.7.1"],
        ["Audio conversion", "FFmpeg 9.0.2 (external binary)"],
        ["Stream resolution", "yt-dlp 2026.08.19"],
        ["Secret handling", "python-dotenv 1.2.4"],
        ["Voice requirements", "PyNaCl 1.6.2, davey 0.1.6"],
        ["Commands", "11 slash commands in 2 cogs"],
        ["Storage", "None - queue lives in memory"],
        ["Database", "None"],
    ], [38 * mm, 118 * mm]))
    A(Spacer(1, 10))

    A(Paragraph("Contents", H2))
    for n, title in [
        ("1", "Why this stack"), ("2", "Technology explained"),
        ("3", "Project structure"), ("4", "Architecture layers"),
        ("5", "End-to-end workflow"), ("6", "The queue mechanism"),
        ("7", "How Discord voice + audio actually work"),
        ("8", "Error handling"), ("9", "Known limitations"),
    ]:
        A(Paragraph(f"<b>{n}.</b> &nbsp; {title}", BODY))

    # ---------------- 1 ----------------
    A(Paragraph("1. Why this stack", H1))
    A(Paragraph(
        "Every choice here exists to keep the project small enough to fully understand. "
        "There is no Lavalink server, no Redis, no database, and no cloud service, "
        "because none of them are needed yet.", BODY,
    ))
    A(bullets([
        "<b>discord.py</b> - the only mature Python library for Discord. It handles "
        "the WebSocket connection, the command tree, and the voice protocol.",
        "<b>FFmpeg</b> - Discord accepts only Opus audio. FFmpeg decodes MP3 and "
        "re-encodes it, which no Python library does well on its own.",
        "<b>yt-dlp</b> - resolves a search term or URL into a direct audio stream. "
        "It is kept strictly behind one function so playback never depends on it.",
        "<b>python-dotenv</b> - loads the bot token from .env so it never sits in code.",
        "<b>PyNaCl / davey</b> - required for voice encryption. discord.py lists these "
        "as optional extras, so pip will not install them unless asked.",
    ]))

    # ---------------- 2 ----------------
    A(Paragraph("2. Technology explained", H1))

    A(Paragraph("2.1 discord.py", H2))
    A(Paragraph(
        "Connects to Discord over a persistent WebSocket. Commands arrive as "
        "<font face='Courier'>Interaction</font> objects. The library owns the "
        "Gateway session, so CASE never parses Discord's protocol by hand.", BODY,
    ))

    A(Paragraph("2.2 Slash commands and the command tree", H2))
    A(Paragraph(
        "Commands are declared with decorators on cog methods. At startup, "
        "<font face='Courier'>tree.sync(guild)</font> uploads their names, "
        "descriptions and parameters to Discord. Global sync can take up to an hour "
        "to appear; guild sync is immediate, which suits a single private server.", BODY,
    ))
    A(code("""@discord.app_commands.command(name="play", description="Play a song by name, link, or from music/")
@discord.app_commands.describe(song="Song name, a YouTube/SoundCloud link, or a file in music/")
async def play(self, interaction: discord.Interaction, song: str):
    ..."""))

    A(Paragraph("2.3 Intents", H2))
    A(Paragraph(
        "Intents tell Discord which events to deliver. CASE only uses slash commands, "
        "so the default intents are enough and no privileged toggle is required in the "
        "Developer Portal. The harmless warning about message content intent can be "
        "ignored.", BODY,
    ))

    A(Paragraph("2.4 FFmpeg - the part people miss", H2))
    A(Paragraph(
        "FFmpeg is not a Python package. It is a separate program "
        "(<font face='Courier'>ffmpeg.exe</font> on Windows) that "
        "<font face='Courier'>pip</font> cannot install. discord.py launches it as a "
        "child process, writes the audio command to its standard input, and reads "
        "encoded Opus frames back from standard output. CASE locates the binary itself "
        "because a terminal opened before FFmpeg was installed keeps the old PATH.", BODY,
    ))
    A(code("""import shutil
from pathlib import Path

FFMPEG_FALLBACK_PATHS = (
    Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "WinGet" / "Links",
    Path("C:/ffmpeg/bin"),
)

def find_ffmpeg() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    for folder in FFMPEG_FALLBACK_PATHS:
        candidate = Path(folder, "ffmpeg.exe")
        if candidate.exists():
            return str(candidate)
    return None"""))

    A(Paragraph("2.5 yt-dlp", H2))
    A(Paragraph(
        "Given a search phrase or a URL, yt-dlp returns metadata plus a direct, "
        "signed audio URL that FFmpeg can stream. It is a blocking library, so CASE "
        "runs it in a worker thread to keep the event loop free.", BODY,
    ))
    A(code("track = await asyncio.to_thread(resolve_track, song, user.display_name)"))

    A(Paragraph("2.6 python-dotenv", H2))
    A(Paragraph(
        "Reads key/value pairs from <font face='Courier'>.env</font> into environment "
        "variables. The file is listed in <font face='Courier'>.gitignore</font> and is "
        "never printed or logged.", BODY,
    ))

    # ---------------- 3 ----------------
    A(Paragraph("3. Project structure", H1))
    A(code("""CASE/
|-- bot.py              entry point: login, load cogs, sync commands, set presence
|-- config.py           reads .env, exposes MUSIC_DIR and TOKEN
|-- requirements.txt
|-- .env                bot token (git-ignored)
|-- .gitignore
|-- start_case.bat      double-click launcher
|-- music/              local audio files (git-ignored)
`-- cogs/
    |-- general.py      /ping, /help
    `-- music.py        voice, playback, queue, track resolution"""))
    A(Paragraph(
        "Only two cog files. A cog is just discord.py's grouping mechanism - splitting "
        "non-music commands away from music commands was the only separation that earned "
        "its keep. Line counts: bot.py 59, config.py 17, general.py 62, music.py 530.", BODY,
    ))

    # ---------------- 4 ----------------
    A(Paragraph("4. Architecture layers", H1))
    A(Paragraph("Responsibilities stay separated so each piece can be replaced alone:", BODY))
    A(code("""Discord commands        cogs/music.py   Music.play(), Music.pause()
        |
        v
Music player            MusicPlayer     queue, current track, playback loop
        |
        v
Queue                   list[Track]     in memory, per guild
        |
        v
Audio source            Track.source    local file path OR remote URL
        |
        v
FFmpeg                  FFmpegOpusAudio mp3/webm -> Opus frames
        |
        v
Discord voice           VoiceClient     encrypted packets to the channel"""))
    A(Paragraph("The important seam", H3))
    A(Paragraph(
        "<font face='Courier'>Track</font> is the boundary. It carries a "
        "<font face='Courier'>source</font> string that is either a local path or a "
        "stream URL. The player only ever reads <font face='Courier'>.source</font>, so "
        "adding a new source later means writing one resolver function and touching "
        "nothing else.", BODY,
    ))
    A(code("""class Track:
    def __init__(self, source, title, requested_by="someone",
                 duration=None, webpage_url=None):
        self.source = source          # path OR url
        self.title = title
        ...

    @property
    def is_stream(self) -> bool:
        return self.webpage_url is not None"""))

    A(Paragraph("Per-guild state", H3))
    A(Paragraph(
        "The cog keeps <font face='Courier'>self.players: dict[int, MusicPlayer]</font>, "
        "keyed by guild id. Two servers therefore get independent queues and independent "
        "voice connections.", BODY,
    ))

    # ---------------- 5 ----------------
    A(Paragraph("5. End-to-end workflow", H1))
    A(Paragraph("What happens after a user types /play believer", H2))

    steps = [
        ("1. Discord delivers the interaction",
         "The Gateway WebSocket pushes an Interaction carrying guild id, user and the "
         "argument string."),
        ("2. Acknowledge immediately",
         "play() calls defer() first. Discord requires a reply within 3 seconds, and "
         "a network lookup can take longer. defer() shows \"thinking...\" and buys time."),
        ("3. Resolve to a Track",
         "resolve_track() runs in a thread. It first looks in music/ for a filename "
         "match; local files always win. If nothing matches, yt-dlp handles the query "
         "as a search or a direct URL."),
        ("4. Connect to voice",
         "ensure_player() checks that the user is in a voice channel, verifies FFmpeg is "
         "present, then connects once per guild or moves the existing client."),
        ("5. Hand to the player",
         "player.add(track) appends to the queue and starts the background loop. The "
         "bot replies with a Now Playing embed, or with a queue position if something "
         "was already playing."),
        ("6. The loop plays it",
         "_run() pops the track, builds a FFmpegOpusAudio from the source, and hands it "
         "to the voice client."),
        ("7. Audio flows",
         "FFmpeg decodes the MP3 or pulls the stream, re-encodes to Opus, and writes "
         "20 ms frames. discord.py encrypts each frame and sends it to Discord, which "
         "plays it for everyone in the channel."),
    ]
    for title, text in steps:
        A(Paragraph(title, H3))
        A(Paragraph(text, BODY))

    A(Paragraph("Command lifecycle at a glance", H2))
    A(code("""user types /play believer
      |
      v
Interaction arrives -----> play()
      |                       |
      |                       +--> defer()            "CASE is thinking..."
      |                       +--> resolve_track()    to_thread, off the loop
      |                       |      |
      |                       |      +--> music/believer.mp3 ? use local file
      |                       |      +--> else yt-dlp search / URL parse
      |                       |
      |                       +--> ensure_player()
      |                       |      +--> user in a voice channel?
      |                       |      +--> ffmpeg located?
      |                       |      +--> connect or move
      |                       |
      |                       +--> player.add(track)  --> _run() task
      |                       |
      `--> followup.send()     "Now Playing" embed
      |
      v
FFmpeg -> Opus -> encrypted packets -> voice channel"""))

    # ---------------- 6 ----------------
    A(Paragraph("6. The queue mechanism", H1))
    A(Paragraph(
        "One background task drains the queue. It pops a track, plays it, then polls "
        "until the track ends - which also covers a pause, because a paused player "
        "reports paused rather than finished.", BODY,
    ))
    A(code("""while self.queue:
    track = self.queue.pop(0)
    self.current = track
    source = discord.FFmpegOpusAudio(track.source, executable=find_ffmpeg())
    self.voice.play(source)

    while self.voice.is_playing() or self.voice.is_paused():
        await asyncio.sleep(0.5)

    self.current = None"""))
    A(Paragraph("Design consequences", H3))
    A(bullets([
        "<b>Auto-advance is free.</b> When the last frame is sent, is_playing() turns "
        "false, the inner loop exits, and the next iteration picks up the next track.",
        "<b>Skip reuses the same loop.</b> Calling voice.stop() ends playback, which is "
        "indistinguishable from a track finishing - so skip() only stops the voice and "
        "lets the loop continue. If the song was paused, skip() resumes first.",
        "<b>Stop cancels instead.</b> stop() clears the queue and cancels the task, "
        "whose finally block disconnects the voice client.",
        "<b>Auto-leave.</b> When the queue empties normally, the finally block "
        "disconnects, and on_voice_state_update removes the stale player so the next "
        "/play starts clean.",
        "<b>is_idle() decides the reply.</b> /play checks whether anything is active to "
        "choose between a Now Playing embed and a queue position.",
    ]))

    # ---------------- 7 ----------------
    A(Paragraph("7. How Discord voice + audio actually work", H1))
    A(Paragraph("The audio pipeline, end to end", H2))
    A(code("""believer.mp3 on disk            (or a YouTube stream URL)
      |
      |  FFmpeg process, spawned by discord.py
      |  args: -i <source> -f opus -ac 2 -ar 48000 -b:a 128k
      v
raw Opus frames, 20 ms each    (~3840 bytes, ~384 bytes after overhead)
      |
      |  header stripped, frame encrypted (AES-GCM via PyNaCl / libsodium)
      |  960 samples per frame @ 48 kHz stereo = Discord's requirement
      v
WebSocket -> Discord voice server
      |
      v
decoded, mixed, and played in the voice channel"""))
    A(Paragraph("Why PyNaCl is mandatory", H2))
    A(Paragraph(
        "Discord does not accept plain audio. Every Opus frame is encrypted with a "
        "session key obtained during the voice handshake. PyNaCl provides the AES-GCM "
        "primitive; davey handles the UDP transport. Without them discord.py logs "
        "\"voice will NOT be supported\" and /join fails with a confusing error - so they "
        "are listed explicitly in requirements.txt.", BODY,
    ))
    A(Paragraph("Encoding numbers Discord expects", H2))
    A(table([
        ["Property", "Required value"],
        ["Sample rate", "48000 Hz"],
        ["Channels", "2 (stereo)"],
        ["Frame size", "20 ms"],
        ["Samples per frame", "960"],
        ["Codec", "Opus"],
        ["Max bitrate", "~128 kbps (60 KB/s ceiling)"],
    ], [50 * mm, 106 * mm]))
    A(Spacer(1, 8))
    A(Paragraph("Reconnection", H2))
    A(Paragraph(
        "Streamed audio can drop mid-song. FFmpeg is started with "
        "<font face='Courier'>-reconnect 1 -reconnect_streamed 1 "
        "-reconnect_delay_max 5</font> so it retries a broken connection instead of "
        "silently ending the track.", BODY,
    ))

    # ---------------- 8 ----------------
    A(Paragraph("8. Error handling", H1))
    A(Paragraph(
        "User-facing messages stay friendly while the full traceback goes to the "
        "terminal log, where it is useful for debugging instead of confusing for users.", BODY,
    ))
    A(table([
        ["Situation", "CASE replies"],
        ["User not in a voice channel", "You're not in a voice channel."],
        ["FFmpeg missing", "FFmpeg isn't installed, so I can't play audio. ..."],
        ["Song / search finds nothing", "I couldn't find anything for <b>name</b>."],
        ["Nothing is playing", "Nothing is playing right now."],
        ["Already paused", "The music is already paused."],
        ["Not paused", "The music isn't paused."],
        ["Bot not connected", "CASE isn't connected to a voice channel."],
        ["Skip with empty queue", "Nothing left in the queue. Stopping."],
    ], [58 * mm, 98 * mm]))
    A(Spacer(1, 8))
    A(Paragraph("One-response rule", H2))
    A(Paragraph(
        "A Discord interaction accepts exactly one reply. CASE calls defer() first and "
        "then replies with followup(). ensure_player() therefore returns "
        "(player, error) instead of replying itself - otherwise the interaction would "
        "be answered twice and Discord returns \"The application did not respond\".", BODY,
    ))
    A(code("""player, error = await self.ensure_player(interaction)
if error:
    await interaction.followup.send(error)
    return"""))

    # ---------------- 9 ----------------
    A(Paragraph("9. Known limitations", H1))
    A(bullets([
        "The queue is in memory. Restarting CASE loses it.",
        "CASE only runs while the host machine is awake. A VPS is needed for 24/7 use.",
        "Spotify is unsupported: the official API exposes metadata, not playable audio.",
        "SoundCloud only surfaces results that match the query - its search is loose, "
        "so an unrelated track is dropped rather than played.",
        "One MusicPlayer per guild, stored in memory, so state is lost on restart.",
        "No volume, shuffle, loop, or per-track removal yet - planned for v2.",
    ]))

    A(Spacer(1, 10))
    A(Paragraph("Planned next", H2))
    A(bullets([
        "/remove, /clear, /shuffle, /loop (off / track / queue), /volume",
        "Button controls on the Now Playing embed",
        "A pluggable source interface so new resolvers slot in without touching playback",
        "SQLite for per-server settings such as a DJ role or default channel",
        "Hosting on a VPS with logging and restart-on-failure",
    ]))

    doc.build(s)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    build()