"""Tests for YouTube Music search ranking, resolution and autoplay logic.

Run from the project root with the project's own interpreter:

    .\\venv\\Scripts\\python.exe -m unittest discover -s tests -v

No network access is required: every provider call is replaced with a fixture.
The bot token is faked before importing the cog, because config.py refuses to
load without one.
"""

import asyncio
import os
import pathlib
import re
import sys
import types
import unittest

os.environ.setdefault("DISCORD_TOKEN", "test-token")

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cogs.music as m  # noqa: E402


# --- Fixtures ------------------------------------------------------------


def vid(name: str) -> str:
    """A stable 11-character YouTube-style video id for a fixture name."""
    base = re.sub(r"[^A-Za-z0-9_-]", "", name)
    return (base + "XXXXXXXXXXX")[:11]


def watch(video_id: str) -> str:
    return f"https://music.youtube.com/watch?v={video_id}"


def song(video_id, title, artists, album=None, duration=None, views=None,
         album_id=None, artist_ids=None):
    """A YouTube Music song object in the shape ytmusicapi returns."""
    item = {
        "videoId": vid(video_id),
        "title": title,
        "artists": [
            {"name": name, "id": (artist_ids or [None] * len(artists))[index]}
            for index, name in enumerate(artists)
        ],
        "thumbnails": [{"url": "https://i.ytimg.com/vi/x/w120-h120.jpg"}],
    }
    if album is not None:
        item["album"] = {"name": album, "id": album_id}
    if duration is not None:
        item["duration"] = m.format_time(duration)
    if views is not None:
        item["views"] = views
    return item


def norm(*items):
    out = []
    for item in items:
        candidate = m._normalize_ytm_item(item)
        assert candidate is not None, item
        out.append(candidate)
    return out


FLASHING_LIGHTS = norm(
    song("flashing-kanye", "Flashing Lights", ["Kanye West"], "Graduation", 237, "92M",
         album_id="grad-album", artist_ids=["UCkanye"]),
    song("flashing-road", "Flashing Lights", ["RoadTrip"], "Dynamite", 195, "1.2M",
         album_id="dyn-album", artist_ids=["UCroad"]),
    song("flashing-steve", "Flashing Lights", ["Steve Angello"], "HUMAN", 214, "880K",
         album_id="human-album", artist_ids=["UCsteve"]),
    song("flashing-remix", "Flashing Lights (Remix)", ["MakThaReaper"], "X", 114, "41K",
         album_id="x-album", artist_ids=["UCmak"]),
)

KHAT_NAVJOT = norm(
    song("khat-navjot", "Khat", ["Navjot Ahuja"], "Khat", 296, "12M",
         album_id="khat-album", artist_ids=["UCnavjot"]),
    song("khat-cover", "Khat (Female Cover)", ["Duomelo", "Arjama B"], "Khat (Female Cover)", 159, "220K"),
    song("khat-inst", "Khat (Instrumental)", ["Sonu Worldwide", "Nainsy"], "Khat", 213, "15K"),
)

KHAT_BROAD = norm(
    song("khat-navjot", "Khat", ["Navjot Ahuja"], "Khat", 296, "50M",
         album_id="khat-album", artist_ids=["UCnavjot"]),
    song("khat-guru", "Khat", ["Guru Randhawa"], "Khat", 228, "40M"),
    song("khatole", "Khatole 2", ["Masoom Sharma"], "Khatole 2", 148, "5M"),
    song("khatta", "Khatta Flow", ["Seedhe Maut", "KR$NA"], "Lunch Break", 152, "30M"),
)


THOSE_EYES = norm(
    # Deliberately lists the wrong recordings first: a karaoke instrumental and
    # a jazz cover. The real New West single must still win.
    song("eyes-karaoke", "Those Eyes (Instrumental)", ["Karaoke Kings"], "Karaoke Hits", 220, "90K"),
    song("eyes-jazz", "Those Eyes", ["Jazz Trio"], "Jazz Covers", 220, "210K"),
    song("eyes-official", "Those Eyes (Official Audio)", ["New West"], "Those Eyes", 220, "30M",
         album_id="eyes-album", artist_ids=["UCnewwest"]),
    song("eyes-newwest", "Those Eyes", ["New West"], "Those Eyes", 220, "829M",
         album_id="eyes-album", artist_ids=["UCnewwest"]),
)

SHAPE_OF_YOU = norm(
    song("shape-karaoke", "Shape of You", ["Karaoke Group"], "Karaoke Hits", 233, "88K"),
    song("shape-ed", "Shape of You", ["Ed Sheeran"], "Shape of You", 233, "3.9B",
         album_id="shape-album", artist_ids=["UCed"]),
)

FLASHING_LIGHTS_CANONICAL = norm(
    song("flashing-cover", "Flashing Lights", ["RoadTrip"], "Karaoke Hits", 237, "90K"),
    song("flashing-kanye2", "Flashing Lights", ["Kanye West"], "Graduation", 237, "92M",
         album_id="grad-album", artist_ids=["UCkanye"]),
)

BLINDING_LIGHTS = norm(
    song("blinding-other", "Blinding Lights", ["Other Singer"], "Singles", 200, "10"),
    song("blinding-weeknd", "Blinding Lights", ["The Weeknd"], "After Hours", 200, "4.1B",
         album_id="hours-album", artist_ids=["UCweeknd"]),
)


MEMORIES = norm(
    # The provider's relevance order lists an obscure cover first; the canonical
    # single sits further down but has vastly more views. With no artist named,
    # popularity must break the tie.
    song("memories-cover", "Memories", ["Ronald Meirs"], "Covers", 189, "833K"),
    song("memories-sarrb", "Memories", ["Sarrb", "Starboy X"], "Sarrb", 251, "20K"),
    song("memories-maroon", "Memories", ["Maroon 5"], "Memories", 189, "35M"),
)


def fake_flashing_search(query, limit=None):
    if "flashing" in query.lower():
        return [dict(c) for c in FLASHING_LIGHTS][: (limit or 5)]
    return []


def fake_memories_search(query, limit=None):
    if "memories" in query.lower():
        return [dict(c) for c in MEMORIES][: (limit or 5)]
    return []


def fake_those_eyes_search(query, limit=None):
    ql = query.lower()
    if "those" in ql and "eyes" in ql:
        return [dict(c) for c in THOSE_EYES][: (limit or 5)]
    return []


def fake_relaxing_search(query, limit=None):
    """Mimics the provider: the over-specific query yields nothing, the relaxed one works."""
    ql = query.lower()
    if "khat" not in ql:
        return []
    if "dream" in ql or "note" in ql:
        return []
    if "navjot" in ql:
        return [dict(c) for c in KHAT_NAVJOT][: (limit or 5)]
    return [dict(c) for c in KHAT_BROAD][: (limit or 5)]


def make_candidate(title, artist, video_id, language="english", album=None):
    return {
        "title": title,
        "artist": artist,
        "id": vid(video_id),
        "webpage_url": watch(vid(video_id)),
        "duration": 200,
        "language": language,
        "album": album,
        "artist_ids": ["UCprimary"],
        "album_id": "MPREb_album",
        "thumbnail": None,
        "year": None,
    }


def make_playable(video_id, title="Song", author="Artist"):
    return types.SimpleNamespace(
        identifier=video_id,
        uri=watch(video_id),
        title=title,
        author=author,
        length=200_000,
        artwork=None,
        album=types.SimpleNamespace(name=None),
    )


# --- Base helper ---------------------------------------------------------


class PatchedTestCase(unittest.TestCase):
    """Snapshot module attributes before each test and restore them afterwards."""

    def patch(self, **attrs):
        for name, value in attrs.items():
            if name not in self._originals:
                self._originals[name] = getattr(m, name, None)
            setattr(m, name, value)

    def setUp(self):
        self._originals = {}

    def tearDown(self):
        for name, value in self._originals.items():
            setattr(m, name, value)


# --- Search ranking ------------------------------------------------------


class SearchRankingTests(PatchedTestCase):
    def setUp(self):
        super().setUp()
        self.patch(search_youtube_music=fake_flashing_search)

    def scores(self):
        return [c["score"] for c in self.results]

    def test_plain_title_prefers_the_canonical_artist(self):
        self.results = m.search_candidates("Flashing Lights", m.MAX_POOL)
        self.assertTrue(self.results)
        self.assertEqual(self.results[0]["artist"], "Kanye West")
        self.assertEqual(self.results[0]["title"], "Flashing Lights")

    def test_plain_title_does_not_pick_a_remix(self):
        self.results = m.search_candidates("Flashing Lights", m.MAX_POOL)
        self.assertNotIn("remix", self.results[0]["title"].lower())

    def test_artist_hint_lifts_the_requested_artist(self):
        self.results = m.search_candidates("Flashing Lights Kanye West", m.MAX_POOL)
        self.assertEqual(self.results[0]["artist"], "Kanye West")
        self.assertTrue(self.results[0]["signals"].get("artist_hint"))
        others = [c for c in self.results if c["artist"] != "Kanye West"]
        self.assertTrue(others)
        for candidate in others:
            self.assertTrue(candidate["signals"].get("artist_mismatch"))

    def test_explicit_version_is_preferred(self):
        self.results = m.search_candidates("Flashing Lights (Remix)", m.MAX_POOL)
        self.assertIn("remix", self.results[0]["title"].lower())

    def test_candidates_expose_score_and_signals(self):
        self.results = m.search_candidates("Flashing Lights", m.MAX_POOL)
        for candidate in self.results:
            self.assertIsInstance(candidate["score"], float)
            self.assertIsInstance(candidate["signals"], dict)


class OriginalRecordingTests(PatchedTestCase):
    def test_shape_of_you_prefers_ed_sheeran_over_karaoke(self):
        self.patch(search_youtube_music=lambda query, limit=None: [dict(c) for c in SHAPE_OF_YOU])
        results = m.search_candidates("Shape of You", m.MAX_POOL)
        self.assertEqual(results[0]["artist"], "Ed Sheeran")
        self.assertEqual(results[0]["id"], vid("shape-ed"))

    def test_flashing_lights_prefers_kanye_when_catalog_metadata_supports_it(self):
        self.patch(
            search_youtube_music=lambda query, limit=None: [
                dict(c) for c in FLASHING_LIGHTS_CANONICAL
            ]
        )
        results = m.search_candidates("Flashing Lights", m.MAX_POOL)
        self.assertEqual(results[0]["artist"], "Kanye West")
        self.assertEqual(results[0]["id"], vid("flashing-kanye2"))

    def test_trailing_artist_hint_selects_the_weeknd(self):
        self.patch(search_youtube_music=lambda query, limit=None: [dict(c) for c in BLINDING_LIGHTS])
        results = m.search_candidates("Blinding Lights The Weeknd", m.MAX_POOL)
        self.assertEqual(results[0]["artist"], "The Weeknd")
        self.assertEqual(results[0]["id"], vid("blinding-weeknd"))

    def test_standard_request_rejects_nonstandard_recording_types(self):
        spec = m._parse_query("Song Title", manual_search=True)
        for suffix in (
            "Karaoke", "Cover", "Tribute", "Instrumental", "Backing Track", "Live", "Remix"
        ):
            candidate = make_candidate(
                f"Song Title ({suffix})", "Performer", f"id-{suffix.lower()}"
            )
            self.assertFalse(
                m._candidate_allowed_for_request(candidate, spec), suffix
            )

    def test_explicit_version_requests_remain_eligible(self):
        cases = (
            ("Song Title Karaoke", "Song Title (Karaoke)"),
            ("Song Title Cover", "Song Title (Cover)"),
            ("Song Title Remix", "Song Title (Remix)"),
            ("Song Title Live", "Song Title (Live)"),
            ("Song Title Instrumental", "Song Title (Instrumental)"),
        )
        for query, title in cases:
            candidate = make_candidate(title, "Performer", title)
            self.assertTrue(
                m._candidate_allowed_for_request(
                    candidate, m._parse_query(query, manual_search=True)
                ),
                query,
            )

    def test_album_edition_signals_are_manual_only(self):
        candidate = make_candidate("Song Title", "Performer", "song", album="Karaoke Hits")
        spec = m._parse_query("Song Title", manual_search=True)
        _score, autoplay_signals = m.score_candidate(candidate, spec)
        _manual_score, manual_signals = m.score_candidate(
            candidate, spec, manual_search=True
        )
        self.assertFalse(autoplay_signals["cover_like"])
        self.assertTrue(manual_signals["cover_like"])


class RelaxationTests(PatchedTestCase):
    def setUp(self):
        super().setUp()
        self.patch(search_youtube_music=fake_relaxing_search)

    def test_over_specific_query_relaxes_to_the_right_track(self):
        results = m.search_candidates("Khat Navjot Ahuja Dream Note", m.MAX_POOL)
        self.assertTrue(results, "relaxation should recover the track")
        self.assertEqual(results[0]["artist"], "Navjot Ahuja")
        self.assertGreaterEqual(results[0]["score"], m.CONFIDENCE_MEDIUM)

    def test_broad_query_keeps_the_requested_artist_on_top(self):
        # Both are exact "Khat" matches; the more popular one wins the tie.
        results = m.search_candidates("Khat", m.MAX_POOL)
        self.assertEqual(results[0]["artist"], "Navjot Ahuja")


class PopularityTieBreakTests(PatchedTestCase):
    """With no artist named, the canonical (popular) recording must beat covers."""

    def setUp(self):
        super().setUp()
        self.patch(search_youtube_music=fake_memories_search)

    def test_popular_recording_beats_an_earlier_cover(self):
        results = m.search_candidates("Memories", m.MAX_POOL)
        self.assertTrue(results)
        self.assertEqual(results[0]["artist"], "Maroon 5")
        self.assertGreaterEqual(results[0]["signals"]["play_count"], 1_000_000)

    def test_popularity_signal_is_populated(self):
        results = m.search_candidates("Memories", m.MAX_POOL)
        for candidate in results:
            self.assertIn("popularity", candidate["signals"])
            self.assertIn("play_count", candidate["signals"])

    def test_popularity_scales_between_zero_and_one(self):
        self.assertEqual(m._popularity(0), 0.0)
        self.assertEqual(m._popularity(None), 0.0)
        self.assertGreater(m._popularity(500_000), 0.0)
        self.assertLessEqual(m._popularity(10_000_000_000), 1.0)
        self.assertGreater(m._popularity(35_547_665), m._popularity(833_523))


# --- Exact song search ("Those Eyes by New West") ------------------------


class SearchAccuracyTests(PatchedTestCase):
    def setUp(self):
        super().setUp()
        self.patch(search_youtube_music=fake_those_eyes_search)

    def by_id(self, results):
        return {c["id"]: c for c in results}

    def test_title_by_artist_selects_the_correct_track(self):
        results = m.search_candidates("Those Eyes by New West", m.MAX_POOL)
        self.assertTrue(results)
        self.assertEqual(results[0]["artist"], "New West")
        self.assertTrue(results[0]["title"].startswith("Those Eyes"))
        self.assertEqual(results[0]["signals"]["title_similarity"], 1.0)
        self.assertFalse(results[0]["signals"]["artist_mismatch"])

    def test_title_typo_is_not_mistaken_for_an_artist_hint(self):
        candidate = norm(
            song("hale-dil", "Hale Dil", ["Harshit Saxena"], "Murder", 300)
        )[0]
        self.patch(search_youtube_music=lambda query, limit=None: [dict(candidate)])
        results = m.search_candidates("halde dil", m.MAX_POOL)
        self.assertTrue(results)
        self.assertEqual(results[0]["title"], "Hale Dil")
        self.assertFalse(results[0]["signals"]["artist_hint"])
        self.assertFalse(results[0]["signals"]["artist_mismatch"])

    def test_unrelated_first_result_is_not_selected(self):
        results = m.search_candidates("Those Eyes by New West", m.MAX_POOL)
        self.assertEqual(results[0]["artist"], "New West")
        candidates = self.by_id(results)
        self.assertTrue(candidates[vid("eyes-karaoke")]["signals"]["artist_mismatch"])
        self.assertLess(candidates[vid("eyes-karaoke")]["score"], results[0]["score"])
        self.assertLess(candidates[vid("eyes-jazz")]["score"], results[0]["score"])

    def test_slightly_different_title_format_still_matches(self):
        results = m.search_candidates("Those Eyes by New West", m.MAX_POOL)
        candidates = self.by_id(results)
        official = candidates[vid("eyes-official")]
        self.assertEqual(official["signals"]["title_similarity"], 1.0)
        self.assertFalse(official["signals"]["artist_mismatch"])
        self.assertGreaterEqual(official["score"], m.CONFIDENCE_HIGH)

    def test_query_parses_title_and_artist(self):
        spec = m._parse_query("Those Eyes by New West")
        self.assertEqual(spec["title"], "Those Eyes")
        self.assertEqual(spec["artist"], "New West")
        self.assertEqual(spec["core"], "those eyes")
        self.assertEqual(spec["separator"], "by")

    def test_dash_format_is_recognised_both_ways(self):
        spec = m._parse_query("New West - Those Eyes")
        self.assertEqual(spec["separator"], "-")
        self.assertIn("those", spec["tokens"])
        self.assertIn("west", spec["tokens"])

    def test_by_inside_a_title_is_not_split(self):
        # "Stand By Me" is a title, not "Stand" by "Me".
        spec = m._parse_query("Stand By Me")
        self.assertIsNone(spec["title"])
        self.assertIsNone(spec["artist"])

    def test_upload_suffixes_are_ignored(self):
        spec = m._parse_query("Those Eyes (Official Audio)")
        self.assertEqual(spec["core"], "those eyes")

    def test_upload_suffix_after_artist_does_not_pollute_artist_match(self):
        spec = m._parse_query(
            "Blinding Lights by The Weeknd official audio", manual_search=True
        )
        self.assertEqual(spec["title"], "Blinding Lights")
        self.assertEqual(spec["artist"], "The Weeknd")
        self.assertEqual(spec["core"], "blinding lights")

    def test_leading_live_word_can_be_part_of_a_normal_title(self):
        spec = m._parse_query("Live Your Life", manual_search=True)
        candidate = make_candidate("Live Your Life", "T.I.", "live-your-life")
        self.assertNotIn("live", spec["manual_version"])
        self.assertTrue(m._candidate_allowed_for_request(candidate, spec))
        cover_title = make_candidate("Cover Me", "Performer", "cover-me")
        self.assertTrue(m._candidate_allowed_for_request(cover_title, spec))


# --- resolve_track confidence gate (async) -------------------------------


class ResolveTrackTests(PatchedTestCase):
    def setUp(self):
        super().setUp()
        self.extracted = []

        async def fake_load(url, expected_id=None):
            self.extracted.append(url)
            if vid("failstream") in url:
                raise m.StreamResolveError("stream unavailable")
            return make_playable(expected_id or m._video_id_from_url(url) or vid("local"))

        self.patch(_load_lavalink_playable=fake_load)

    def run_resolve(self, query):
        return asyncio.run(m.resolve_track(query, "tester"))

    def test_low_confidence_raises_lookup_error(self):
        self.patch(search_candidates=lambda q, limit=5: [
            {"id": vid("unrelated"), "title": "Something Else", "artist": "Nobody",
             "webpage_url": watch(vid("unrelated")),
             "score": 0.2, "signals": {}},
        ])
        with self.assertRaises(LookupError):
            self.run_resolve("A Totally Unrelated Query")
        self.assertEqual(self.extracted, [])

    def test_artist_mismatch_raises_lookup_error(self):
        self.patch(search_candidates=lambda q, limit=5: [
            {"id": vid("road"), "title": "Flashing Lights", "artist": "RoadTrip",
             "webpage_url": watch(vid("road")),
             "score": 0.9, "signals": {"artist_hint": True, "artist_mismatch": True}},
        ])
        with self.assertRaises(LookupError):
            self.run_resolve("Flashing Lights Kanye West")

    def test_picks_top_candidate_and_falls_back_when_it_will_not_stream(self):
        self.patch(search_candidates=lambda q, limit=5: [
            {"id": vid("first"), "title": "Flashing Lights", "artist": "Kanye West",
             "webpage_url": watch(vid("first")),
             "score": 1.0, "signals": {"artist_hint": True, "artist_mismatch": False}},
            {"id": vid("second"), "title": "Flashing Lights", "artist": "Kanye West",
             "webpage_url": watch(vid("second")),
             "score": 0.9, "signals": {"artist_hint": True, "artist_mismatch": False}},
        ])

        async def fail_first(url, expected_id=None):
            self.extracted.append(url)
            if vid("first") in url:
                raise m.StreamResolveError("first stream unavailable")
            return make_playable(expected_id or m._video_id_from_url(url))

        self.patch(_load_lavalink_playable=fail_first)
        track = self.run_resolve("Flashing Lights Kanye West")
        self.assertEqual(
            self.extracted,
            [watch(vid("first")), watch(vid("second"))],
        )
        self.assertEqual(track.song_id, vid("second"))

    def test_ambiguous_result_offers_choices(self):
        self.patch(search_candidates=lambda q, limit=5: [
            {"id": vid("a"), "title": "Those Eyes", "artist": "New West",
             "webpage_url": watch(vid("a")),
             "score": 0.5, "signals": {"artist_hint": False, "artist_mismatch": False}},
            {"id": vid("b"), "title": "Those Eyes", "artist": "Jazz Trio",
             "webpage_url": watch(vid("b")),
             "score": 0.45, "signals": {"artist_hint": False, "artist_mismatch": False}},
        ])
        with self.assertRaises(m.AmbiguousMatch) as ctx:
            self.run_resolve("Those Eyes")
        self.assertEqual([c["id"] for c in ctx.exception.candidates], [vid("a"), vid("b")])
        self.assertEqual(self.extracted, [], "nothing may stream before the user picks")

    def test_missing_popularity_offers_same_title_artist_choices(self):
        self.patch(search_candidates=lambda q, limit=5: [
            {"id": vid("a"), "title": "Those Eyes", "artist": "New West",
             "webpage_url": watch(vid("a")),
             "score": 0.9, "signals": {"artist_hint": False, "artist_mismatch": False}},
            {"id": vid("b"), "title": "Those Eyes", "artist": "Jazz Trio",
             "webpage_url": watch(vid("b")),
             "score": 0.9, "signals": {"artist_hint": False, "artist_mismatch": False}},
        ])
        with self.assertRaises(m.AmbiguousMatch) as ctx:
            self.run_resolve("Those Eyes")
        self.assertEqual(
            [candidate["id"] for candidate in ctx.exception.candidates], [vid("a"), vid("b")]
        )
        self.assertEqual(self.extracted, [])

    def test_confident_match_is_not_ambiguous(self):
        self.patch(search_candidates=lambda q, limit=5: [
            {"id": vid("a"), "title": "Those Eyes", "artist": "New West",
             "webpage_url": watch(vid("a")),
             "score": 0.9, "signals": {"artist_hint": True, "artist_mismatch": False}},
        ])
        track = self.run_resolve("Those Eyes by New West")
        self.assertEqual(track.song_id, vid("a"))

    def test_track_found_but_unstreamable_is_a_stream_error(self):
        self.patch(search_candidates=lambda q, limit=5: [
            {"id": vid("failstream"), "title": "Those Eyes", "artist": "New West",
             "webpage_url": watch(vid("failstream")),
             "score": 0.9, "signals": {"artist_hint": False, "artist_mismatch": False}},
        ])
        with self.assertRaises(m.StreamResolveError):
            self.run_resolve("Those Eyes New West")

    def test_resolved_track_keeps_the_requested_query(self):
        self.patch(search_candidates=lambda q, limit=5: [
            {"id": vid("a"), "title": "Those Eyes", "artist": "New West",
             "webpage_url": watch(vid("a")),
             "score": 0.9, "signals": {"artist_hint": False, "artist_mismatch": False}},
        ])
        track = self.run_resolve("Those Eyes by New West")
        self.assertEqual(track.requested_query, "Those Eyes by New West")

    def test_failed_original_does_not_fall_back_to_a_cover(self):
        original = {
            "id": vid("original"), "title": "Flashing Lights", "artist": "Kanye West",
            "webpage_url": watch(vid("original")),
            "score": 0.92,
            "signals": {"artist_hint": True, "artist_mismatch": False, "cover_like": False},
        }
        cover = {
            "id": vid("cover"), "title": "Flashing Lights (Karaoke)", "artist": "Kanye West",
            "webpage_url": watch(vid("cover")),
            "score": 0.82,
            "signals": {"artist_hint": True, "artist_mismatch": False, "cover_like": True},
        }
        self.patch(search_candidates=lambda q, limit=5: [original, cover])

        async def fail_original(url, expected_id=None):
            self.extracted.append(url)
            if vid("original") in url:
                raise m.StreamResolveError("original stream unavailable")
            return make_playable(expected_id or m._video_id_from_url(url), "Flashing Lights", "Kanye West")

        self.patch(_load_lavalink_playable=fail_original)
        with self.assertRaises(m.StreamResolveError):
            self.run_resolve("Flashing Lights by Kanye West")
        self.assertEqual(self.extracted, [original["webpage_url"]])

    def test_failed_stream_does_not_fall_back_to_another_artist(self):
        original = {
            "id": vid("original"), "title": "Flashing Lights", "artist": "Kanye West",
            "webpage_url": watch(vid("original")),
            "score": 0.92,
            "signals": {"artist_hint": False, "artist_mismatch": False, "play_count": 50000000},
        }
        unrelated = {
            "id": vid("other"), "title": "Flashing Lights", "artist": "RoadTrip",
            "webpage_url": watch(vid("other")),
            "score": 0.90,
            "signals": {"artist_hint": False, "artist_mismatch": False, "play_count": 100},
        }
        self.patch(search_candidates=lambda q, limit=5: [original, unrelated])

        async def fail_original(url, expected_id=None):
            self.extracted.append(url)
            raise m.StreamResolveError("original stream unavailable")

        self.patch(_load_lavalink_playable=fail_original)
        with self.assertRaises(m.StreamResolveError):
            self.run_resolve("Flashing Lights")
        self.assertEqual(self.extracted, [original["webpage_url"]])

    def test_extractor_metadata_must_match_the_validated_candidate(self):
        candidate = {
            "id": vid("validated"), "title": "Those Eyes", "artist": "New West",
            "webpage_url": watch(vid("validated")),
            "score": 0.9,
            "signals": {"artist_hint": True, "artist_mismatch": False},
        }
        self.patch(search_candidates=lambda q, limit=5: [candidate])

        async def return_different_track(url, expected_id=None):
            self.extracted.append(url)
            return make_playable(vid("other-video"), "Unrelated Song", "Different Artist")

        self.patch(_load_lavalink_playable=return_different_track)
        with self.assertRaises(m.StreamResolveError):
            self.run_resolve("Those Eyes by New West")
        self.assertEqual(self.extracted, [candidate["webpage_url"]])


# --- URL handling --------------------------------------------------------


class YoutubeUrlTests(unittest.TestCase):
    def test_music_url(self):
        self.assertEqual(
            m._video_id_from_url("https://music.youtube.com/watch?v=GDND88fqt1o"),
            "GDND88fqt1o",
        )

    def test_watch_url_with_extra_params(self):
        self.assertEqual(
            m._video_id_from_url(
                "https://www.youtube.com/watch?v=GDND88fqt1o&list=RDGDND88fqt1o&t=42s"
            ),
            "GDND88fqt1o",
        )

    def test_short_and_mobile_forms(self):
        for url in (
            "https://youtu.be/GDND88fqt1o",
            "https://m.youtube.com/watch?v=GDND88fqt1o",
            "https://www.youtube-nocookie.com/embed/GDND88fqt1o",
            "https://www.youtube.com/shorts/GDND88fqt1o",
            "https://www.youtube.com/live/GDND88fqt1o",
        ):
            self.assertEqual(m._video_id_from_url(url), "GDND88fqt1o", url)

    def test_non_video_urls_have_no_id(self):
        for url in (
            "https://www.jiosaavn.com/song/khat/OSMIAyZ1Wws",
            "https://www.youtube.com/playlist?list=PLabc",
            "https://open.spotify.com/track/abc",
            "https://www.youtube.com/watch?v=short",
            "",
            None,
        ):
            self.assertIsNone(m._video_id_from_url(url), url)

    def test_is_youtube_url(self):
        self.assertTrue(m.is_youtube_url("https://music.youtube.com/watch?v=x"))
        self.assertTrue(m.is_youtube_url("https://youtu.be/x"))
        self.assertFalse(m.is_youtube_url("https://www.jiosaavn.com/song/x/Y"))
        self.assertFalse(m.is_youtube_url("https://soundcloud.com/x/y"))
        self.assertFalse(m.is_youtube_url(None))


class CountAndDurationTests(unittest.TestCase):
    def test_parse_count(self):
        self.assertEqual(m._parse_count("829M"), 829_000_000)
        self.assertEqual(m._parse_count("5.9K"), 5_900)
        self.assertEqual(m._parse_count("1.2B"), 1_200_000_000)
        self.assertEqual(m._parse_count(1234), 1234)
        self.assertEqual(m._parse_count("12,345"), 12345)
        self.assertEqual(m._parse_count(None), 0)
        self.assertEqual(m._parse_count("n/a"), 0)

    def test_parse_duration(self):
        self.assertEqual(m._parse_duration("3:41"), 221)
        self.assertEqual(m._parse_duration(221), 221)
        self.assertEqual(m._parse_duration("1:02:03"), 3723)
        self.assertIsNone(m._parse_duration(None))
        self.assertIsNone(m._parse_duration("abc"))


# --- Provider errors -----------------------------------------------------


class ProviderErrorTests(PatchedTestCase):
    """An unreachable provider must never be reported as an empty result set."""

    def test_search_raises_when_both_providers_fail(self):
        def boom(*args, **kwargs):
            raise RuntimeError("youtube music is down")

        self.patch(_ytm_search_songs=boom, _search_youtube_fallback=boom)
        with self.assertRaises(m.SearchProviderError) as ctx:
            m.search_youtube_music("those eyes")
        self.assertIn("unavailable", str(ctx.exception).lower())

    def test_pool_raises_the_real_reason_when_everything_fails(self):
        def boom(*args, **kwargs):
            raise m.SearchProviderError("YouTube Music search is unavailable: boom")

        self.patch(search_youtube_music=boom)
        with self.assertRaises(m.SearchProviderError):
            m._search_pool("those eyes", m._parse_query("those eyes"))

    def test_pool_reports_provider_errors_only_when_nothing_was_found(self):
        calls = []

        def flaky(query, limit=None):
            calls.append(query)
            if "those" in query.lower():
                raise m.SearchProviderError("unavailable")
            return []

        self.patch(search_youtube_music=flaky)
        # Relaxed queries all fail -> the error must surface, not "no results".
        with self.assertRaises(m.SearchProviderError):
            m._search_pool("those eyes new west", m._parse_query("those eyes new west"))

    def test_ytdlp_fallback_is_used_when_the_api_fails(self):
        fallback = [make_candidate("Those Eyes", "New West", "eyes-newwest")]
        self.patch(
            _ytm_search_songs=lambda query, limit: (_ for _ in ()).throw(
                RuntimeError("api blocked")
            ),
            _search_youtube_fallback=lambda query, limit: fallback,
        )
        results = m.search_youtube_music("those eyes new west")
        self.assertEqual([c["id"] for c in results], [vid("eyes-newwest")])


class SourceLabelTests(unittest.TestCase):
    def label(self, url, is_stream=True):
        track = m.Track(source="x", title="t", webpage_url=url)
        track.is_stream  # property driven by webpage_url
        return m.source_label(track)

    def test_youtube_music(self):
        self.assertEqual(self.label(watch(vid("x"))), "YouTube Music")

    def test_plain_youtube(self):
        self.assertEqual(
            self.label("https://www.youtube.com/watch?v=GDND88fqt1o"), "YouTube"
        )

    def test_legacy_jiosaavn(self):
        self.assertEqual(
            self.label("https://www.jiosaavn.com/song/khat/OSMIAyZ1Wws"), "JioSaavn"
        )

    def test_local_file(self):
        track = m.Track(source="music/x.mp3", title="x")
        self.assertEqual(m.source_label(track), "Local file")


class BuildTrackTests(unittest.TestCase):
    def info(self, **overrides):
        base = {
            "url": "http://media/stream",
            "title": "Those Eyes",
            "webpage_url": "https://www.youtube.com/watch?v=GDND88fqt1o",
            "duration": 221,
            "http_headers": {"User-Agent": "x"},
            "abr": 129.5,
        }
        base.update(overrides)
        return base

    def test_candidate_metadata_wins(self):
        candidate = make_candidate("Those Eyes", "New West", "eyes-newwest")
        candidate["artist_ids"] = ["UCnewwest"]
        candidate["album_id"] = "MPREb_eyes"
        track = m._build_track(self.info(), "query", "tester", candidate=candidate)
        self.assertEqual(track.song_id, vid("eyes-newwest"))
        self.assertEqual(track.webpage_url, watch(vid("eyes-newwest")))
        self.assertEqual(track.artist, "New West")
        self.assertEqual(track.artist_ids, ["UCnewwest"])
        self.assertEqual(track.album_id, "MPREb_eyes")
        self.assertEqual(track.quality, "130k")
        self.assertIsNotNone(track.resolved_at)

    def test_url_only_track_uses_the_extractor_metadata(self):
        track = m._build_track(self.info(artist="New West"), "query", "tester")
        self.assertEqual(track.song_id, "GDND88fqt1o")
        self.assertEqual(track.artist, "New West")
        self.assertTrue(track.is_stream)

    def test_missing_stream_raises_a_stream_error(self):
        with self.assertRaises(m.StreamResolveError):
            m._build_track({"title": "x", "webpage_url": watch(vid("x"))}, "q", "tester")

    def test_cut_track_preserves_provider_ids(self):
        track = m._build_track(self.info(), "query", "tester")

        def fake_run(command, **kwargs):
            with open(command[-1], "wb") as handle:
                handle.write(b"fake-audio")
            return types.SimpleNamespace(returncode=0)

        original_ffmpeg = m.find_ffmpeg
        original_run = m.subprocess.run
        m.find_ffmpeg = lambda: "ffmpeg"
        m.subprocess.run = fake_run
        try:
            clip = m._cut_track(track, 30)
        finally:
            m.find_ffmpeg = original_ffmpeg
            m.subprocess.run = original_run
        self.assertEqual(clip.song_id, track.song_id)
        self.assertEqual(clip.artist_ids, track.artist_ids)
        self.assertEqual(clip.album_id, track.album_id)
        self.assertEqual(clip.resolved_at, track.resolved_at)
        clip.cleanup()


# --- resolve_track URL branch --------------------------------------------


class ResolveUrlTests(PatchedTestCase):
    def run_resolve(self, query):
        return asyncio.run(m.resolve_track(query, "tester"))

    def test_youtube_music_url_plays_that_video(self):
        seen: list[str] = []

        async def fake_load(url, expected_id=None):
            seen.append(url)
            return make_playable(expected_id or m._video_id_from_url(url), "Those Eyes", "New West")

        self.patch(_load_lavalink_playable=fake_load)
        track = self.run_resolve("https://music.youtube.com/watch?v=GDND88fqt1o")
        self.assertEqual(seen, ["https://music.youtube.com/watch?v=GDND88fqt1o"])
        self.assertEqual(track.song_id, "GDND88fqt1o")
        self.assertEqual(track.requested_query, "https://music.youtube.com/watch?v=GDND88fqt1o")

    def test_short_link_is_normalised_to_a_watch_url(self):
        seen: list[str] = []

        async def fake_load(url, expected_id=None):
            seen.append(url)
            return make_playable(expected_id or m._video_id_from_url(url), "Those Eyes", "New West")

        self.patch(_load_lavalink_playable=fake_load)
        track = self.run_resolve("https://youtu.be/GDND88fqt1o?t=10")
        self.assertEqual(seen, ["https://music.youtube.com/watch?v=GDND88fqt1o"])
        self.assertEqual(track.song_id, "GDND88fqt1o")

    def test_playlist_only_url_is_rejected_with_a_hint(self):
        with self.assertRaises(ValueError) as ctx:
            self.run_resolve("https://www.youtube.com/playlist?list=PLabc")
        self.assertIn("playlist", str(ctx.exception).lower())

    def test_other_platform_urls_are_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self.run_resolve("https://open.spotify.com/track/abc")
        self.assertIn("youtube music", str(ctx.exception).lower())

    def test_drm_links_get_the_specific_reason(self):
        with self.assertRaises(ValueError) as ctx:
            self.run_resolve("https://music.apple.com/us/album/x/1")
        self.assertIn("DRM", str(ctx.exception))

    def test_legacy_jiosaavn_url_failure_explains_itself(self):
        def boom(_opts, _url):
            raise RuntimeError("site changed")

        self.patch(_extract_once=boom)
        with self.assertRaises(m.StreamResolveError) as ctx:
            self.run_resolve("https://www.jiosaavn.com/song/khat/OSMIAyZ1Wws")
        self.assertIn("jiosaavn", str(ctx.exception).lower())
        self.assertIn("search", str(ctx.exception).lower())

    def test_legacy_jiosaavn_url_still_plays_when_it_works(self):
        def fake_extract(_opts, url):
            return {"webpage_url": url, "title": "Khat", "artist": "Navjot Ahuja"}

        self.patch(_extract_once=fake_extract)
        self.patch(search_candidates=lambda query, limit=5: [
            {"id": vid("khat"), "title": "Khat", "artist": "Navjot Ahuja",
             "webpage_url": watch(vid("khat")), "score": 1.0,
             "signals": {"artist_hint": True, "artist_mismatch": False}},
        ])
        async def fake_load(url, expected_id=None):
            return make_playable(expected_id or vid("khat"), "Khat", "Navjot Ahuja")

        self.patch(_load_lavalink_playable=fake_load)
        track = self.run_resolve("https://www.jiosaavn.com/song/khat/OSMIAyZ1Wws")
        self.assertEqual(track.title, "Khat")
        self.assertEqual(track.song_id, vid("khat"))


class YoutubeErrorMessageTests(unittest.TestCase):
    def test_bot_check_is_reported_as_a_block(self):
        message = m._youtube_error_message(
            RuntimeError("ERROR: Sign in to confirm you're not a bot")
        )
        self.assertIn("bot", message.lower())
        self.assertNotIn("no results", message.lower())

    def test_unavailable_video_says_so(self):
        message = m._youtube_error_message(RuntimeError("ERROR: Video unavailable"))
        self.assertIn("unavailable", message.lower())

    def test_forbidden_is_reported_as_a_refusal(self):
        message = m._youtube_error_message(RuntimeError("ERROR: [youtube] 403 Forbidden"))
        self.assertIn("403", message)

    def test_generic_failure_keeps_the_reason(self):
        message = m._youtube_error_message(RuntimeError("ERROR: something odd happened"))
        self.assertIn("something odd", message)

    def test_no_error_at_all(self):
        self.assertIn("nothing", m._youtube_error_message(None).lower())


# --- Autoplay recommendations --------------------------------------------


class FakeSelf:
    def __init__(self, recent=None):
        self._recent = list(recent or [])


class AutoplayTests(PatchedTestCase):
    def setUp(self):
        super().setUp()
        self.finished = m.Track(
            source="x",
            title="Flashing Lights",
            artist="Kanye West",
            requested_by="tester",
            webpage_url=watch(vid("flashing-kanye")),
            song_id=vid("flashing-kanye"),
            album="Graduation",
            language="english",
            artist_ids=["UCkanye"],
            album_id="MPREb_grad",
        )
        self.detail = {
            "id": vid("flashing-kanye"),
            "artist_ids": ["UCkanye"],
            "album_id": "MPREb_grad",
        }
        self.patch(
            _resolve_music_detail=lambda track: self.detail,
            _ytm_watch_playlist=lambda video_id, limit=50: [
                make_candidate("Flashing Lights", "Kanye West", "flashing-kanye"),
                make_candidate("Stronger", "Kanye West", "stronger"),
                make_candidate("Stronger", "Kanye West", "stronger"),
            ],
            _ytm_artist_songs=self._artist_songs,
            _ytm_related_artist_ids=lambda artist_id, limit=5: ["UCjayz"],
            _ytm_album_tracks=lambda album_id, limit=30: [
                make_candidate("Champion", "Kanye West", "champion", album="Graduation"),
            ],
        )

    @staticmethod
    def _artist_songs(artist_id, limit=20):
        if artist_id == "UCkanye":
            return [
                make_candidate("Good Life", "Kanye West", "good-life"),
                make_candidate("Homecoming", "Kanye West", "homecoming"),
            ]
        return [
            make_candidate("Encore", "Jay-Z", "encore"),
            make_candidate("99 Problems", "Jay-Z", "99-problems"),
        ]

    def test_same_artist_ranks_above_related_artist(self):
        weighted = m.MusicPlayer._autoplay_candidates(None, self.finished)
        ranked = m.MusicPlayer._rank_autoplay(FakeSelf(), weighted, self.finished)
        ids = [c["id"] for _, c in ranked]
        self.assertIn(vid("good-life"), ids)
        self.assertIn(vid("encore"), ids)
        self.assertLess(ids.index(vid("good-life")), ids.index(vid("encore")))

    def test_finished_track_and_duplicates_are_excluded(self):
        weighted = m.MusicPlayer._autoplay_candidates(None, self.finished)
        ranked = m.MusicPlayer._rank_autoplay(FakeSelf(), weighted, self.finished)
        ids = [c["id"] for _, c in ranked]
        self.assertNotIn(vid("flashing-kanye"), ids, "the finished track must not recommend itself")
        self.assertEqual(ids.count(vid("stronger")), 1, "duplicates must collapse to one entry")

    def test_recent_history_is_excluded(self):
        weighted = m.MusicPlayer._autoplay_candidates(None, self.finished)
        ranked = m.MusicPlayer._rank_autoplay(
            FakeSelf([f"id:{vid('homecoming')}"]), weighted, self.finished
        )
        ids = [c["id"] for _, c in ranked]
        self.assertNotIn(vid("homecoming"), ids)

    def test_rails_are_weighted_by_relationship(self):
        weighted = m.MusicPlayer._autoplay_candidates(None, self.finished)
        by_id = {}
        for weight, candidate in weighted:
            by_id.setdefault(candidate["id"], weight)
        self.assertEqual(by_id[vid("stronger")], 1.00, "watch playlist is the closest rail")
        self.assertEqual(by_id[vid("good-life")], 0.95, "same-artist songs")
        self.assertEqual(by_id[vid("champion")], 0.90, "same-album tracks")
        self.assertEqual(by_id[vid("encore")], 0.70, "related artists")

    def _empty_rails(self):
        self.patch(
            _ytm_watch_playlist=lambda *a, **k: [],
            _ytm_artist_songs=lambda *a, **k: [],
            _ytm_related_artist_ids=lambda *a, **k: [],
            _ytm_album_tracks=lambda *a, **k: [],
        )

    def test_empty_rails_fall_back_to_an_artist_search(self):
        self._empty_rails()
        calls: list[str] = []

        def fake_search(query, limit=None):
            calls.append(query)
            return [
                make_candidate("Stronger", "Kanye West", "stronger"),
                make_candidate("Unrelated Song", "Some Other Band", "unrelated"),
            ]

        self.patch(search_youtube_music=fake_search)
        weighted = m.MusicPlayer._autoplay_candidates(None, self.finished)
        ids = [c["id"] for _, c in weighted]
        self.assertIn(vid("stronger"), ids, "same-artist songs should be surfaced")
        self.assertNotIn(vid("unrelated"), ids, "the fallback must stay on the same artist")
        self.assertTrue(calls, "the artist must be searched")
        self.assertNotIn(self.finished.title.lower(), calls[0].lower())

    def test_fallback_is_skipped_when_rails_return_tracks(self):
        # The default rails already return tracks, so no artist search happens.
        self.patch(
            search_youtube_music=lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("broad search should not run")
            )
        )
        weighted = m.MusicPlayer._autoplay_candidates(None, self.finished)
        self.assertTrue(weighted)

    def test_track_without_ids_still_finds_its_rails(self):
        # A track queued from a pasted link carries no catalogue ids; the detail
        # lookup (a title search) is what supplies them.
        self.patch(
            _resolve_music_detail=lambda track: {
                "id": vid("flashing-kanye"),
                "artist_ids": ["UCkanye"],
                "album_id": "MPREb_grad",
            }
        )
        bare = m.Track(
            source="x",
            title="Flashing Lights",
            artist="Kanye West",
            webpage_url=watch(vid("flashing-kanye")),
            song_id=vid("flashing-kanye"),
        )
        weighted = m.MusicPlayer._autoplay_candidates(None, bare)
        self.assertTrue(weighted)


class MusicDetailTests(PatchedTestCase):
    def test_stored_ids_are_used_without_a_search(self):
        calls: list[str] = []
        self.patch(
            _search_pool=lambda *a, **k: calls.append("search") or [],
        )
        track = m.Track(
            source="x",
            title="Those Eyes",
            artist="New West",
            song_id=vid("eyes-newwest"),
            artist_ids=["UCnewwest"],
            album_id="MPREb_eyes",
        )
        detail = m._resolve_music_detail(track)
        self.assertEqual(detail["id"], vid("eyes-newwest"))
        self.assertEqual(detail["artist_ids"], ["UCnewwest"])
        self.assertEqual(detail["album_id"], "MPREb_eyes")
        self.assertEqual(calls, [], "no search when the ids are already known")

    def test_local_file_falls_back_to_a_title_search(self):
        def fake_search(query, limit=None):
            candidate = make_candidate("Those Eyes", "New West", "eyes-newwest")
            candidate["artist_ids"] = ["UCnewwest"]
            return [candidate]

        self.patch(search_youtube_music=fake_search)
        track = m.Track(source="music/those-eyes.mp3", title="Those Eyes", artist="New West")
        detail = m._resolve_music_detail(track)
        self.assertIsNotNone(detail)
        self.assertEqual(detail["id"], vid("eyes-newwest"))
        self.assertEqual(detail["artist_ids"], ["UCnewwest"])


# --- Autoplay pipeline (queue insertion / retries) -----------------------


class FakeVoice:
    """The tiny slice of discord.VoiceProtocol that MusicPlayer touches."""

    def __init__(self):
        self.channel = None
        self.guild = types.SimpleNamespace(id=1)
        self.connected = True

    def is_connected(self):
        return self.connected


def make_player(**overrides):
    player = m.MusicPlayer(FakeVoice())
    for name, value in overrides.items():
        setattr(player, name, value)
    return player


class AutoplayPipelineTests(PatchedTestCase):
    def setUp(self):
        super().setUp()
        self.finished = m.Track(
            source="x",
            title="Flashing Lights",
            artist="Kanye West",
            requested_by="tester",
            song_id=vid("flashing-kanye"),
            webpage_url=watch(vid("flashing-kanye")),
            language="english",
        )
        self.candidate = make_candidate("Stronger", "Kanye West", "stronger")

    def run_autoplay(self, player):
        asyncio.run(player._queue_autoplay(self.finished))

    def resolved(self, title="Stronger", song_id=None):
        return m.Track(
            source="s", title=title, requested_by="tester",
            song_id=song_id or vid("stronger"),
        )

    def test_related_track_is_queued(self):
        player = make_player(autoplay=True)
        player._autoplay_candidates = lambda finished: [(0.9, self.candidate)]

        async def fake_first(ranked, finished):
            return self.resolved(), None

        player._first_related = fake_first
        self.run_autoplay(player)
        self.assertEqual([t.title for t in player.queue], ["Stronger"])

    def test_empty_recommendations_do_not_queue_or_crash(self):
        player = make_player(autoplay=True)
        player._autoplay_candidates = lambda finished: []

        async def fake_first(ranked, finished):
            return (None, None)

        player._first_related = fake_first
        self.run_autoplay(player)
        self.assertEqual(player.queue, [])
        self.assertFalse(player._autoplay_busy)

    def test_provider_error_does_not_disable_autoplay(self):
        player = make_player(autoplay=True)

        def boom(finished):
            raise RuntimeError("youtube music exploded")

        player._autoplay_candidates = boom

        async def fake_first(ranked, finished):
            return (None, None)

        player._first_related = fake_first
        self.run_autoplay(player)  # must not raise
        self.assertTrue(player.autoplay)
        self.assertFalse(player._autoplay_busy)
        self.assertEqual(player.queue, [])

        # A later, healthy lookup still works.
        player._autoplay_candidates = lambda finished: [(0.9, self.candidate)]

        async def ok_first(ranked, finished):
            return self.resolved(), None

        player._first_related = ok_first
        self.run_autoplay(player)
        self.assertEqual(len(player.queue), 1)

    def test_disabled_autoplay_never_inserts(self):
        player = make_player(autoplay=False)
        called: list[bool] = []
        player._autoplay_candidates = lambda finished: called.append(True) or []
        self.run_autoplay(player)
        self.assertEqual(player.queue, [])
        self.assertEqual(called, [])

    def test_empty_queue_and_failed_search_terminate(self):
        player = make_player(autoplay=True)
        player._autoplay_candidates = lambda finished: []

        async def fake_first(ranked, finished):
            return (None, None)

        player._first_related = fake_first
        player.queue.append(self.finished)

        async def fake_play_one(track):
            return True

        player._play_one = fake_play_one

        async def run():
            await asyncio.wait_for(player._run(), timeout=5)

        asyncio.run(run())  # finishing within the timeout proves it does not spin
        self.assertEqual(player.queue, [])


class IdentityTests(unittest.TestCase):
    def test_song_id_wins_over_title(self):
        track = m.Track(source="x", title="Whatever", song_id="abc")
        self.assertEqual(m._track_identity(track), "id:abc")

    def test_identity_falls_back_to_normalized_title(self):
        track = m.Track(source="x", title="Flashing Lights")
        self.assertEqual(m._track_identity(track), "t:" + m._autoplay_key("Flashing Lights"))

    def test_identity_derived_from_youtube_url(self):
        track = m.Track(
            source="x",
            title="Foo",
            webpage_url="https://music.youtube.com/watch?v=GDND88fqt1o",
        )
        self.assertEqual(track.song_id, "GDND88fqt1o")
        self.assertEqual(m._track_identity(track), "id:GDND88fqt1o")

    def test_identity_derived_from_legacy_jiosaavn_url(self):
        track = m.Track(
            source="x",
            title="Foo",
            webpage_url="https://www.jiosaavn.com/song/foo/barTOKEN",
        )
        self.assertEqual(track.song_id, "barTOKEN")
        self.assertEqual(m._track_identity(track), "id:barTOKEN")


# --- Normalization -------------------------------------------------------


class NormalizationTests(unittest.TestCase):
    def test_search_shape(self):
        candidate = m._normalize_ytm_item(
            song("flashing-kanye", "Flashing Lights", ["Kanye West"], "Graduation",
                 237, "92M", album_id="grad-album", artist_ids=["UCkanye"])
        )
        self.assertEqual(candidate["title"], "Flashing Lights")
        self.assertEqual(candidate["artist"], "Kanye West")
        self.assertEqual(candidate["album"], "Graduation")
        self.assertEqual(candidate["album_id"], "grad-album")
        self.assertEqual(candidate["artist_ids"], ["UCkanye"])
        self.assertEqual(candidate["duration"], 237)
        self.assertEqual(candidate["play_count"], 92_000_000)
        self.assertEqual(candidate["webpage_url"], watch(vid("flashing-kanye")))
        self.assertTrue(candidate["id"])

    def test_multiple_artists_are_joined(self):
        candidate = m._normalize_ytm_item(
            song("duo", "Khatta Flow", ["Seedhe Maut", "KR$NA"], "Lunch Break", 152, "30M",
                 artist_ids=["UCseedhe", "UCkrna"])
        )
        self.assertEqual(candidate["artist"], "Seedhe Maut, KR$NA")
        self.assertEqual(len(candidate["artist_ids"]), 2)

    def test_watch_playlist_shape_uses_duration_seconds(self):
        candidate = m._normalize_ytm_item(
            {
                "videoId": vid("watch-item"),
                "title": "Stronger",
                "artists": [{"name": "Kanye West", "id": "UCkanye"}],
                "album": {"name": "Graduation", "id": "grad-album"},
                "duration": "3:51",
                "views": "1.2B",
                "thumbnails": [{"url": "https://i.ytimg.com/vi/x/w120-h120.jpg"}],
            }
        )
        self.assertEqual(candidate["duration"], 231)
        self.assertEqual(candidate["play_count"], 1_200_000_000)

    def test_album_track_without_artists_uses_the_album_credit(self):
        candidate = m._normalize_ytm_item(
            {
                "videoId": vid("album-track"),
                "title": "Champion",
                "artists": [],
                "duration": "4:00",
                "thumbnails": [],
            },
            fallback_artist="Kanye West",
            fallback_album="Graduation",
        )
        self.assertEqual(candidate["artist"], "Kanye West")
        self.assertEqual(candidate["album"], "Graduation")

    def test_items_without_a_video_id_are_rejected(self):
        self.assertIsNone(m._normalize_ytm_item({"title": "No id"}))
        self.assertIsNone(m._normalize_ytm_item({"videoId": "too-short", "title": "x"}))
        self.assertIsNone(m._normalize_ytm_item({"videoId": vid("ok")}))
        self.assertIsNone(m._normalize_ytm_item("not a dict"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
