"""Mocked Lavalink and signed local-audio transport tests."""

import asyncio
import os
import pathlib
import sys
import tempfile
import types
import unittest
from urllib.parse import urlsplit, parse_qs

os.environ.setdefault("DISCORD_TOKEN", "test-token")

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import cogs.music as music  # noqa: E402
from audio_files import serve_local_audio, signed_audio_url  # noqa: E402


class FakePlayable:
    identifier = "GDND88fqt1o"
    uri = "https://music.youtube.com/watch?v=GDND88fqt1o"
    title = "Those Eyes"
    author = "New West"
    length = 221_000
    artwork = "https://img.example/cover.jpg"
    album = types.SimpleNamespace(name="Those Eyes")


class FakeVoice:
    def __init__(self):
        self.connected = True
        self.paused = False
        self.playing = False
        self.position = 0
        self.guild = types.SimpleNamespace(id=1)
        self.channel = types.SimpleNamespace(id=2)
        self.node = types.SimpleNamespace(identifier="test")
        self.music_player = None
        self.stopped = False

    async def play(self, track, *, start=0, add_history=False):
        self.position = start
        started = types.SimpleNamespace(player=self, track=track)
        self.music_player.handle_track_start(started)
        ended = types.SimpleNamespace(player=self, track=track, reason="finished")
        self.music_player.handle_track_end(ended)
        return track

    async def pause(self, value):
        self.paused = value

    async def stop(self):
        self.stopped = True

    async def seek(self, position):
        self.position = position


class LavalinkPlaybackTests(unittest.TestCase):
    def player(self):
        voice = FakeVoice()
        player = music.MusicPlayer(voice)
        voice.music_player = player
        return player

    def test_playback_waits_for_start_and_end_events(self):
        player = self.player()
        track = music.Track(
            source=FakePlayable.uri,
            title=FakePlayable.title,
            artist=FakePlayable.author,
            webpage_url=FakePlayable.uri,
            playable=FakePlayable(),
        )
        self.assertTrue(asyncio.run(player._play_one(track)))
        self.assertTrue(track.playback_started)
        self.assertTrue(track.started_event.is_set())
        self.assertIsNone(player.current)

    def test_track_exception_is_not_reported_as_success(self):
        player = self.player()
        track = music.Track(
            source=FakePlayable.uri,
            title=FakePlayable.title,
            webpage_url=FakePlayable.uri,
            playable=FakePlayable(),
        )

        async def fail_play(track, *, start=0, add_history=False):
            player.handle_track_exception(
                types.SimpleNamespace(
                    player=player.voice,
                    track=track,
                    exception=types.SimpleNamespace(message="Video unavailable"),
                )
            )
            player.handle_track_end(
                types.SimpleNamespace(player=player.voice, track=track, reason="loadFailed")
            )

        player.voice.play = fail_play
        self.assertFalse(asyncio.run(player._play_one(track)))
        self.assertIn("unavailable", track.playback_error.lower())

    def test_skip_pause_resume_and_seek_use_lavalink_controls(self):
        player = self.player()
        track = music.Track(
            source=FakePlayable.uri,
            title=FakePlayable.title,
            playable=FakePlayable(),
        )
        player.current = track
        player.voice.paused = True
        asyncio.run(player.skip())
        self.assertFalse(player.voice.paused)
        self.assertTrue(player.voice.stopped)

        self.assertTrue(asyncio.run(player.seek(42)))
        self.assertEqual(player.voice.position, 42_000)
        asyncio.run(player.voice.pause(True))
        self.assertTrue(player.voice.paused)
        asyncio.run(player.voice.pause(False))
        self.assertFalse(player.voice.paused)

    def test_youtube_bot_check_has_a_distinct_load_error(self):
        message = music._lavalink_error_message(
            RuntimeError("Sign in to confirm you're not a bot")
        )
        self.assertIn("verify it isn't a bot", message)


class SignedLocalAudioTests(unittest.TestCase):
    def setUp(self):
        self.original = (
            config.MUSIC_DIR,
            config.LAVALINK_LOCAL_AUDIO_BASE_URL,
            config.LOCAL_AUDIO_SECRET,
        )
        self.temp = tempfile.TemporaryDirectory()
        config.MUSIC_DIR = pathlib.Path(self.temp.name)
        config.LAVALINK_LOCAL_AUDIO_BASE_URL = "http://tango-music:10000"
        config.LOCAL_AUDIO_SECRET = "unit-test-secret"
        self.path = config.MUSIC_DIR / "quiet song.mp3"
        self.path.write_bytes(b"test audio")

    def tearDown(self):
        (
            config.MUSIC_DIR,
            config.LAVALINK_LOCAL_AUDIO_BASE_URL,
            config.LOCAL_AUDIO_SECRET,
        ) = self.original
        self.temp.cleanup()

    def test_signed_url_hides_filename_and_serves_only_signed_file(self):
        url = signed_audio_url(self.path)
        parsed = urlsplit(url)
        self.assertEqual(parsed.netloc, "tango-music:10000")
        self.assertNotIn("quiet song.mp3", url)
        params = parse_qs(parsed.query)
        asset = parsed.path.rsplit("/", 1)[-1]
        request = types.SimpleNamespace(
            match_info={"asset": asset},
            query={key: values[0] for key, values in params.items()},
        )
        response = asyncio.run(serve_local_audio(request))
        self.assertEqual(pathlib.Path(response._path), self.path)

    def test_tampered_signature_is_rejected(self):
        url = signed_audio_url(self.path)
        parsed = urlsplit(url)
        params = parse_qs(parsed.query)
        request = types.SimpleNamespace(
            match_info={"asset": parsed.path.rsplit("/", 1)[-1]},
            query={
                "expires": params["expires"][0],
                "signature": "0" * 64,
            },
        )
        with self.assertRaises(Exception) as ctx:
            asyncio.run(serve_local_audio(request))
        self.assertEqual(ctx.exception.status, 404)

    def test_queued_local_file_receives_a_fresh_lavalink_reference(self):
        track = music.Track.from_file(self.path, "tester", playable=FakePlayable())
        player = music.MusicPlayer(FakeVoice())
        fresh = FakePlayable()
        calls = []
        original = music._load_lavalink_playable

        async def fake_load(url, expected_id=None):
            calls.append(url)
            return fresh

        music._load_lavalink_playable = fake_load
        try:
            result = asyncio.run(player._refresh_stale_stream(track))
        finally:
            music._load_lavalink_playable = original

        self.assertIs(result, track)
        self.assertIs(track.playable, fresh)
        self.assertIn("/local-audio/", calls[0])
