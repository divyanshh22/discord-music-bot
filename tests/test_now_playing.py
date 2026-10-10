"""Tests for the Tango now-playing embed and its control buttons.

Run from the project root:

    .\\venv\\Scripts\\python.exe -m unittest discover -s tests -v

These are pure rendering tests: no Discord connection and no network.
"""

import os
import pathlib
import sys
import unittest

os.environ.setdefault("DISCORD_TOKEN", "test-token")

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import discord  # noqa: E402

import cogs.music as m  # noqa: E402


class StubTrack:
    def __init__(self, **fields):
        self.title = "Song"
        self.artist = "Artist"
        self.requested_by = "someone"
        self.duration = 200
        self.webpage_url = "https://www.jiosaavn.com/song/song/TOKEN"
        self.thumbnail = "https://c.saavncdn.com/cover-500x500.jpg"
        self.quality = "320k"
        self.song_id = "TOKEN"
        self.album = "Album"
        self.language = "english"
        self.is_stream = True
        self.source = "x"
        self.start_offset = 0
        self.temp_path = None
        self.announced = False
        self.__dict__.update(fields)

    def cleanup(self):
        pass


class StubPlayer:
    def __init__(self, *, current=None, queue=None, autoplay=False, liked=frozenset(),
                 shuffle=False, loop="off", paused=False, elapsed=0):
        self.current = current
        self.queue = list(queue or [])
        self.history = []
        self.autoplay = autoplay
        self.liked = set(liked)
        self.shuffle_enabled = shuffle
        self.loop_mode = loop
        self._paused = paused
        self._elapsed = elapsed

    @property
    def is_paused(self):
        return self._paused

    def elapsed(self):
        return self._elapsed


class NowPlayingEmbedTests(unittest.TestCase):
    def setUp(self):
        self.track = StubTrack()
        self.next_track = StubTrack(title="Next Song", song_id="NEXT", duration=180)
        self.player = StubPlayer(
            current=self.track, queue=[self.next_track], autoplay=True, elapsed=84
        )

    def test_heading_and_branding(self):
        embed = m.build_now_playing_embed(self.track, self.player)
        self.assertEqual(embed.author.name, "🎵 TANGO — NOW PLAYING")

    def test_kaist_logo_uses_only_the_configured_image_url(self):
        original = m.KAIST_LOGO_URL
        try:
            m.KAIST_LOGO_URL = "https://assets.example/kaist-mark.png"
            embed = m.build_now_playing_embed(self.track, self.player)
            self.assertEqual(embed.author.icon_url, m.KAIST_LOGO_URL)
            m.KAIST_LOGO_URL = ""
            embed = m.build_now_playing_embed(self.track, self.player)
            self.assertIsNone(embed.author.icon_url)
        finally:
            m.KAIST_LOGO_URL = original

    def test_title_is_a_blue_link_to_the_source(self):
        embed = m.build_now_playing_embed(self.track, self.player)
        self.assertEqual(embed.title, "Song")
        self.assertEqual(embed.url, self.track.webpage_url)

    def test_uses_square_thumbnail_not_banner(self):
        embed = m.build_now_playing_embed(self.track, self.player)
        self.assertIsNotNone(embed.thumbnail)
        self.assertEqual(embed.thumbnail.url, self.track.thumbnail)
        self.assertIsNone(embed.image.url)

    def test_artist_progress_and_metadata(self):
        embed = m.build_now_playing_embed(self.track, self.player)
        self.assertIn("**Artist**", embed.description)
        self.assertIn("01:24", embed.description)  # elapsed
        self.assertIn("03:20", embed.description)  # total
        self.assertIn("**someone**", embed.description)  # requestor
        self.assertIn("1 in queue", embed.description)
        self.assertIn("Autoplay on", embed.description)

    def test_divider_separates_info_from_controls(self):
        embed = m.build_now_playing_embed(self.track, self.player)
        self.assertTrue(embed.description.rstrip().endswith(m.DIVIDER))

    def test_up_next_shown_when_queue_has_tracks(self):
        embed = m.build_now_playing_embed(self.track, self.player)
        self.assertIn("Up next", embed.description)
        self.assertIn("Next Song", embed.description)

    def test_liked_and_modes_are_reflected(self):
        self.player.liked = {"id:" + self.track.song_id}
        self.player.shuffle_enabled = True
        self.player.loop_mode = "song"
        embed = m.build_now_playing_embed(self.track, self.player)
        self.assertIn("Liked", embed.description)
        self.assertIn("Shuffle", embed.description)
        self.assertIn("Repeat one", embed.description)

    def test_footer_reports_status_source_and_quality(self):
        embed = m.build_now_playing_embed(self.track, self.player)
        self.assertIn("Playing", embed.footer.text)
        self.assertIn("JioSaavn", embed.footer.text)
        self.assertIn("320 kbps", embed.footer.text)

    def test_paused_footer(self):
        self.player._paused = True
        embed = m.build_now_playing_embed(self.track, self.player)
        self.assertIn("Paused", embed.footer.text)

    def test_live_stream_has_no_total(self):
        live = StubTrack(duration=None, artist="Live DJ")
        player = StubPlayer(current=live)
        embed = m.build_now_playing_embed(live, player)
        self.assertIn("Live", embed.description)

    def test_no_thumbnail_is_allowed(self):
        track = StubTrack(thumbnail=None)
        player = StubPlayer(current=track)
        embed = m.build_now_playing_embed(track, player)
        self.assertIsNone(embed.thumbnail.url)

    def test_idle_embed_is_branded(self):
        embed = m.build_idle_embed("Queue finished")
        self.assertEqual(embed.author.name, "🎵 TANGO")


class PlayerViewTests(unittest.TestCase):
    def make(self, **kwargs):
        player = StubPlayer(**kwargs)
        view = m.PlayerView(player)
        view.sync()
        return player, view

    @staticmethod
    def emoji(button):
        return str(button.emoji)

    @staticmethod
    def find(view, glyph):
        return next(c for c in view.children if str(c.emoji) == glyph)

    @staticmethod
    def rows(view):
        grouped = {}
        for child in view.children:
            grouped.setdefault(child.row, []).append(child)
        return grouped

    def test_all_eleven_controls_are_present(self):
        _player, view = self.make()
        self.assertEqual(len(view.children), 11)

    def test_row_layout(self):
        _player, view = self.make()
        rows = self.rows(view)
        self.assertEqual(
            [self.emoji(c) for c in rows[0]], ["⏮️", "⏸️", "⏭️", "🔀", "🔁"]
        )
        self.assertEqual(
            [self.emoji(c) for c in rows[1]], ["🛑", "🎵", "🤍", "🔗"]
        )
        self.assertEqual(
            [self.emoji(c) for c in rows[2]], ["🔄", "🎤"]
        )

    def test_controls_have_compact_native_text_labels(self):
        _player, view = self.make(current=StubTrack())
        labels = [child.label for child in view.children]
        self.assertEqual(
            labels,
            ["Prev", "Pause", "Skip", "Shuffle", "Repeat", "Stop", "Queue", "Like",
             "Autoplay", "Restart", "Lyrics"],
        )

    def test_controls_keep_their_original_callbacks(self):
        _player, view = self.make(current=StubTrack())
        callbacks = {
            child.label: child.callback.callback.__name__ for child in view.children
        }
        self.assertEqual(
            callbacks,
            {
                "Prev": "prev_btn",
                "Pause": "play_btn",
                "Skip": "next_btn",
                "Shuffle": "shuffle_btn",
                "Repeat": "repeat_btn",
                "Stop": "stop_btn",
                "Queue": "queue_btn",
                "Like": "like_btn",
                "Autoplay": "autoplay_btn",
                "Restart": "restart_btn",
                "Lyrics": "lyrics_btn",
            },
        )

    def test_play_label_reflects_current_playback_state(self):
        _player, playing = self.make(current=StubTrack())
        self.assertEqual(self.find(playing, "⏸️").label, "Pause")
        _player, paused = self.make(current=StubTrack(), paused=True)
        self.assertEqual(self.find(paused, "▶️").label, "Play")

    def test_stop_is_red_and_play_is_primary(self):
        _player, view = self.make(current=StubTrack())
        stop = self.find(view, "🛑")
        play = self.find(view, "⏸️")
        self.assertEqual(stop.style, discord.ButtonStyle.danger)
        self.assertEqual(play.style, discord.ButtonStyle.primary)

    def test_autoplay_is_highlighted_when_enabled(self):
        _player, off = self.make(current=StubTrack(), autoplay=False)
        self.assertEqual(self.find(off, "🔗").style, discord.ButtonStyle.secondary)

        _player, on = self.make(current=StubTrack(), autoplay=True)
        self.assertEqual(self.find(on, "🔗").style, discord.ButtonStyle.success)

    def test_like_highlights_only_for_liked_tracks(self):
        track = StubTrack(song_id="ABC")
        _player, unliked = self.make(current=track)
        self.assertEqual(self.find(unliked, "🤍").style, discord.ButtonStyle.secondary)

        _player, liked = self.make(current=track, liked={"id:ABC"})
        self.assertEqual(self.find(liked, "❤️").style, discord.ButtonStyle.danger)

    def test_like_is_disabled_without_a_track(self):
        _player, view = self.make()
        self.assertTrue(self.find(view, "🤍").disabled)

    def test_transport_disabled_when_idle(self):
        _player, view = self.make()
        for glyph in ("⏮️", "⏸️", "⏭️"):
            self.assertTrue(self.find(view, glyph).disabled)


if __name__ == "__main__":
    unittest.main(verbosity=2)
