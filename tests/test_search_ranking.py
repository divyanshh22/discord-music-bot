"""Tests for JioSaavn search ranking and autoplay recommendation logic.

Run from the project root with the project's own interpreter:

    .\\venv\\Scripts\\python.exe -m unittest discover -s tests -v

No network access is required: every JioSaavn call is replaced with a fixture.
The bot token is faked before importing the cog, because config.py refuses to
load without one.
"""

import asyncio
import os
import pathlib
import sys
import types
import unittest

os.environ.setdefault("DISCORD_TOKEN", "test-token")

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cogs.music as m  # noqa: E402


# --- Fixtures ------------------------------------------------------------


def v3(song, artists, album, duration, language, url, song_id, image=None, play_count=None):
    """A JioSaavn v3 `search.getResults` song object."""
    item = {
        "song": song,
        "primary_artists": artists,
        "album": album,
        "duration": str(duration),
        "language": language,
        "perma_url": url,
        "id": song_id,
        "image": image or "https://c.saavncdn.com/x-150x150.jpg",
    }
    if play_count is not None:
        item["play_count"] = str(play_count)
    return item


def v4(title, artists, album, duration, language, url, song_id, album_id=None):
    """A JioSaavn v4 entity, where extra metadata lives under `more_info`."""
    return {
        "title": title,
        "perma_url": url,
        "more_info": {
            "artistMap": {
                "primary_artists": [{"name": a, "id": f"{a.lower().replace(' ', '-')}-id"} for a in artists]
            },
            "album": album,
            "album_id": album_id,
            "duration": str(duration),
            "language": language,
        },
    }


def norm(*items):
    out = []
    for item in items:
        candidate = m._normalize_song_item(item)
        assert candidate is not None, item
        out.append(candidate)
    return out


FLASHING_LIGHTS = norm(
    v3("Flashing Lights", "Kanye West", "Graduation", 237, "english",
       "https://www.jiosaavn.com/song/flashing-lights/A", "vdcjl4dH"),
    v3("Flashing Lights", "RoadTrip", "Dynamite", 195, "english",
       "https://www.jiosaavn.com/song/flashing-lights/B", "OKoeR2Lj"),
    v3("Flashing Lights", "Steve Angello", "HUMAN", 214, "english",
       "https://www.jiosaavn.com/song/flashing-lights/C", "1d90mDUY"),
    v3("Flashing Lights (Remix)", "MakThaReaper", "X", 114, "english",
       "https://www.jiosaavn.com/song/flashing-lights-remix/E", "remix1"),
)

KHAT_NAVJOT = norm(
    v3("Khat", "Navjot Ahuja", "Khat", 296, "hindi",
       "https://www.jiosaavn.com/song/khat/A", "navjot-khat"),
    v3("Khat (Female Cover)", "Duomelo, Arjama B", "Khat (Female Cover)", 159, "hindi",
       "https://www.jiosaavn.com/song/khat-female-cover/B", "cover1"),
    v3("Khat (Instrumental)", "Sonu Worldwide, Nainsy", "Khat", 213, "unknown",
       "https://www.jiosaavn.com/song/khat-instrumental/C", "inst1"),
)

KHAT_BROAD = norm(
    v3("Khat", "Navjot Ahuja", "Khat", 296, "hindi",
       "https://www.jiosaavn.com/song/khat/A", "navjot-khat"),
    v3("Khat", "Guru Randhawa", "Khat", 228, "punjabi",
       "https://www.jiosaavn.com/song/khat/G", "guru-khat"),
    v3("Khatole 2", "Masoom Sharma", "Khatole 2", 148, "haryanvi",
       "https://www.jiosaavn.com/song/khatole-2/H", "khatole"),
    v3("Khatta Flow", "Seedhe Maut, KR$NA", "Lunch Break", 152, "hindi",
       "https://www.jiosaavn.com/song/khatta-flow/I", "khatta"),
)


THOSE_EYES = norm(
    # Deliberately lists the wrong recordings first: a karaoke instrumental and
    # a jazz cover. The real New West single must still win.
    v3("Those Eyes (Instrumental)", "Karaoke Kings", "Karaoke Hits", 220, "english",
       "https://www.jiosaavn.com/song/those-eyes-instrumental/A", "wrong-instrumental"),
    v3("Those Eyes", "Jazz Trio", "Jazz Covers", 220, "english",
       "https://www.jiosaavn.com/song/those-eyes/B", "wrong-jazz"),
    v3("Those Eyes (Official Audio)", "New West", "Those Eyes", 220, "english",
       "https://www.jiosaavn.com/song/those-eyes-official/C", "right-official"),
    v3("Those Eyes", "New West", "Those Eyes", 220, "english",
       "https://www.jiosaavn.com/song/those-eyes/D", "right-newwest"),
)

SHAPE_OF_YOU = norm(
     v3("Shape of You", "Karaoke Group", "Karaoke Hits", 233, "english",
         "https://www.jiosaavn.com/song/shape-of-you/karaoke", "shape-karaoke"),
     v3("Shape of You", "Ed Sheeran", "Shape of You", 233, "english",
         "https://www.jiosaavn.com/song/shape-of-you/ed-sheeran", "shape-ed", play_count=50000000),
)

FLASHING_LIGHTS_CANONICAL = norm(
     v3("Flashing Lights", "RoadTrip", "Karaoke Hits", 237, "english",
         "https://www.jiosaavn.com/song/flashing-lights/karaoke", "flashing-karaoke"),
     v3("Flashing Lights", "Kanye West", "Graduation", 237, "english",
         "https://www.jiosaavn.com/song/flashing-lights/kanye", "flashing-kanye", play_count=50000000),
)

BLINDING_LIGHTS = norm(
     v3("Blinding Lights", "Other Singer", "Singles", 200, "english",
         "https://www.jiosaavn.com/song/blinding-lights/other", "blinding-other", play_count=10),
     v3("Blinding Lights", "The Weeknd", "After Hours", 200, "english",
         "https://www.jiosaavn.com/song/blinding-lights/weeknd", "blinding-weeknd", play_count=50000000),
)


MEMORIES = norm(
    # JioSaavn's relevance order lists an obscure cover first; the canonical
    # single sits further down but has vastly more plays. With no artist named,
    # popularity must break the tie.
    v3("Memories", "Ronald Meirs", "Covers", 189, "english",
       "https://www.jiosaavn.com/song/memories/A", "cover-ronald", play_count=833523),
    v3("Memories", "Sarrb, Starboy X", "Sarrb", 251, "hindi",
       "https://www.jiosaavn.com/song/memories/B", "sarrb", play_count=19948),
    v3("Memories", "Maroon 5", "Memories", 189, "english",
       "https://www.jiosaavn.com/song/memories/C", "maroon5", play_count=35547665),
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
    """Mimics JioSaavn: the over-specific query yields nothing, the relaxed one works."""
    ql = query.lower()
    if "khat" not in ql:
        return []
    if "dream" in ql or "note" in ql:
        return []
    if "navjot" in ql:
        return [dict(c) for c in KHAT_NAVJOT][: (limit or 5)]
    return [dict(c) for c in KHAT_BROAD][: (limit or 5)]


def make_candidate(title, artist, song_id, language="english", album=None):
    return {
        "title": title,
        "artist": artist,
        "id": song_id,
        "webpage_url": f"https://www.jiosaavn.com/song/{song_id}/X",
        "duration": 200,
        "language": language,
        "album": album,
        "artist_ids": ["primary-artist-id"],
        "album_id": "album-id",
        "thumbnail": None,
        "year": None,
    }


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
        self.patch(search_jiosaavn=fake_flashing_search)

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
        self.patch(search_jiosaavn=lambda query, limit=None: [dict(c) for c in SHAPE_OF_YOU])
        results = m.search_candidates("Shape of You", m.MAX_POOL)
        self.assertEqual(results[0]["artist"], "Ed Sheeran")
        self.assertEqual(results[0]["id"], "shape-ed")

    def test_flashing_lights_prefers_kanye_when_catalog_metadata_supports_it(self):
        self.patch(
            search_jiosaavn=lambda query, limit=None: [
                dict(c) for c in FLASHING_LIGHTS_CANONICAL
            ]
        )
        results = m.search_candidates("Flashing Lights", m.MAX_POOL)
        self.assertEqual(results[0]["artist"], "Kanye West")
        self.assertEqual(results[0]["id"], "flashing-kanye")

    def test_trailing_artist_hint_selects_the_weeknd(self):
        self.patch(search_jiosaavn=lambda query, limit=None: [dict(c) for c in BLINDING_LIGHTS])
        results = m.search_candidates("Blinding Lights The Weeknd", m.MAX_POOL)
        self.assertEqual(results[0]["artist"], "The Weeknd")
        self.assertEqual(results[0]["id"], "blinding-weeknd")

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
        self.patch(search_jiosaavn=fake_relaxing_search)

    def test_over_specific_query_relaxes_to_the_right_track(self):
        results = m.search_candidates("Khat Navjot Ahuja Dream Note", m.MAX_POOL)
        self.assertTrue(results, "relaxation should recover the track")
        self.assertEqual(results[0]["artist"], "Navjot Ahuja")
        self.assertGreaterEqual(results[0]["score"], m.CONFIDENCE_MEDIUM)

    def test_broad_query_keeps_the_requested_artist_on_top(self):
        results = m.search_candidates("Khat", m.MAX_POOL)
        self.assertEqual(results[0]["artist"], "Navjot Ahuja")


class PopularityTieBreakTests(PatchedTestCase):
    """With no artist named, the canonical (popular) recording must beat covers."""

    def setUp(self):
        super().setUp()
        self.patch(search_jiosaavn=fake_memories_search)

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
        self.patch(search_jiosaavn=fake_those_eyes_search)

    def by_id(self, results):
        return {c["id"]: c for c in results}

    def test_title_by_artist_selects_the_correct_track(self):
        results = m.search_candidates("Those Eyes by New West", m.MAX_POOL)
        self.assertTrue(results)
        self.assertEqual(results[0]["artist"], "New West")
        self.assertTrue(results[0]["title"].startswith("Those Eyes"))
        self.assertEqual(results[0]["signals"]["title_similarity"], 1.0)
        self.assertFalse(results[0]["signals"]["artist_mismatch"])

    def test_unrelated_first_result_is_not_selected(self):
        results = m.search_candidates("Those Eyes by New West", m.MAX_POOL)
        self.assertEqual(results[0]["artist"], "New West")
        candidates = self.by_id(results)
        self.assertTrue(candidates["wrong-instrumental"]["signals"]["artist_mismatch"])
        self.assertLess(candidates["wrong-instrumental"]["score"], results[0]["score"])
        self.assertLess(candidates["wrong-jazz"]["score"], results[0]["score"])

    def test_slightly_different_title_format_still_matches(self):
        results = m.search_candidates("Those Eyes by New West", m.MAX_POOL)
        candidates = self.by_id(results)
        official = candidates["right-official"]
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

        def fake_extract(_opts, url):
            self.extracted.append(url)
            if url.endswith("/F"):
                raise RuntimeError("stream unavailable")
            return {"webpage_url": url, "url": "http://media"}

        def fake_build(info, query, requested_by, *, candidate=None):
            return m.Track(
                source=info.get("url") or "http://media",
                title=info.get("title") or query,
                requested_by=requested_by,
                webpage_url=info.get("webpage_url"),
                song_id=candidate.get("id") if candidate else None,
            )

        self.patch(_extract_once=fake_extract, _build_track=fake_build)

    def run_resolve(self, query):
        return asyncio.run(m.resolve_track(query, "tester"))

    def test_low_confidence_raises_lookup_error(self):
        self.patch(search_candidates=lambda q, limit=5: [
            {"id": "x", "title": "Something Else", "artist": "Nobody",
             "webpage_url": "https://www.jiosaavn.com/song/x/U",
             "score": 0.2, "signals": {}},
        ])
        with self.assertRaises(LookupError):
            self.run_resolve("A Totally Unrelated Query")
        self.assertEqual(self.extracted, [])

    def test_artist_mismatch_raises_lookup_error(self):
        self.patch(search_candidates=lambda q, limit=5: [
            {"id": "x", "title": "Flashing Lights", "artist": "RoadTrip",
             "webpage_url": "https://www.jiosaavn.com/song/x/U",
             "score": 0.9, "signals": {"artist_hint": True, "artist_mismatch": True}},
        ])
        with self.assertRaises(LookupError):
            self.run_resolve("Flashing Lights Kanye West")

    def test_picks_top_candidate_and_falls_back_when_it_will_not_stream(self):
        self.patch(search_candidates=lambda q, limit=5: [
            {"id": "first", "title": "Flashing Lights", "artist": "Kanye West",
             "webpage_url": "https://www.jiosaavn.com/song/first/F",
             "score": 1.0, "signals": {"artist_hint": True, "artist_mismatch": False}},
            {"id": "second", "title": "Flashing Lights", "artist": "Kanye West",
             "webpage_url": "https://www.jiosaavn.com/song/second/S",
             "score": 0.9, "signals": {"artist_hint": True, "artist_mismatch": False}},
        ])
        track = self.run_resolve("Flashing Lights Kanye West")
        self.assertEqual(
            self.extracted,
            ["https://www.jiosaavn.com/song/first/F", "https://www.jiosaavn.com/song/second/S"],
        )
        self.assertEqual(track.song_id, "second")

    def test_ambiguous_result_offers_choices(self):
        self.patch(search_candidates=lambda q, limit=5: [
            {"id": "a", "title": "Those Eyes", "artist": "New West",
             "webpage_url": "https://www.jiosaavn.com/song/x/a",
             "score": 0.5, "signals": {"artist_hint": False, "artist_mismatch": False}},
            {"id": "b", "title": "Those Eyes", "artist": "Jazz Trio",
             "webpage_url": "https://www.jiosaavn.com/song/x/b",
             "score": 0.45, "signals": {"artist_hint": False, "artist_mismatch": False}},
        ])
        with self.assertRaises(m.AmbiguousMatch) as ctx:
            self.run_resolve("Those Eyes")
        self.assertEqual([c["id"] for c in ctx.exception.candidates], ["a", "b"])
        self.assertEqual(self.extracted, [], "nothing may stream before the user picks")

    def test_missing_popularity_offers_same_title_artist_choices(self):
        self.patch(search_candidates=lambda q, limit=5: [
            {"id": "a", "title": "Those Eyes", "artist": "New West",
             "webpage_url": "https://www.jiosaavn.com/song/those-eyes/a",
             "score": 0.9, "signals": {"artist_hint": False, "artist_mismatch": False}},
            {"id": "b", "title": "Those Eyes", "artist": "Jazz Trio",
             "webpage_url": "https://www.jiosaavn.com/song/those-eyes/b",
             "score": 0.9, "signals": {"artist_hint": False, "artist_mismatch": False}},
        ])
        with self.assertRaises(m.AmbiguousMatch) as ctx:
            self.run_resolve("Those Eyes")
        self.assertEqual([candidate["id"] for candidate in ctx.exception.candidates], ["a", "b"])
        self.assertEqual(self.extracted, [])

    def test_confident_match_is_not_ambiguous(self):
        self.patch(search_candidates=lambda q, limit=5: [
            {"id": "a", "title": "Those Eyes", "artist": "New West",
             "webpage_url": "https://www.jiosaavn.com/song/x/S",
             "score": 0.9, "signals": {"artist_hint": True, "artist_mismatch": False}},
        ])
        track = self.run_resolve("Those Eyes by New West")
        self.assertEqual(track.song_id, "a")

    def test_track_found_but_unstreamable_is_a_stream_error(self):
        self.patch(search_candidates=lambda q, limit=5: [
            {"id": "a", "title": "Those Eyes", "artist": "New West",
             "webpage_url": "https://www.jiosaavn.com/song/x/F",
             "score": 0.9, "signals": {"artist_hint": False, "artist_mismatch": False}},
        ])
        with self.assertRaises(m.StreamResolveError):
            self.run_resolve("Those Eyes New West")

    def test_resolved_track_keeps_the_requested_query(self):
        self.patch(search_candidates=lambda q, limit=5: [
            {"id": "a", "title": "Those Eyes", "artist": "New West",
             "webpage_url": "https://www.jiosaavn.com/song/x/S",
             "score": 0.9, "signals": {"artist_hint": False, "artist_mismatch": False}},
        ])
        track = self.run_resolve("Those Eyes by New West")
        self.assertEqual(track.requested_query, "Those Eyes by New West")

    def test_failed_original_does_not_fall_back_to_a_cover(self):
        original = {
            "id": "original-id", "title": "Flashing Lights", "artist": "Kanye West",
            "webpage_url": "https://www.jiosaavn.com/song/flashing-lights/original",
            "score": 0.92,
            "signals": {"artist_hint": True, "artist_mismatch": False, "cover_like": False},
        }
        cover = {
            "id": "cover-id", "title": "Flashing Lights (Karaoke)", "artist": "Kanye West",
            "webpage_url": "https://www.jiosaavn.com/song/flashing-lights/cover",
            "score": 0.82,
            "signals": {"artist_hint": True, "artist_mismatch": False, "cover_like": True},
        }
        self.patch(search_candidates=lambda q, limit=5: [original, cover])

        def fail_original(_opts, url):
            self.extracted.append(url)
            if url.endswith("/original"):
                raise RuntimeError("original stream unavailable")
            return {"title": "Flashing Lights", "artist": "Kanye West", "url": "http://media"}

        self.patch(_extract_once=fail_original)
        with self.assertRaises(m.StreamResolveError):
            self.run_resolve("Flashing Lights by Kanye West")
        self.assertEqual(self.extracted, [original["webpage_url"]])

    def test_failed_stream_does_not_fall_back_to_another_artist(self):
        original = {
            "id": "original-id", "title": "Flashing Lights", "artist": "Kanye West",
            "webpage_url": "https://www.jiosaavn.com/song/flashing-lights/original",
            "score": 0.92,
            "signals": {"artist_hint": False, "artist_mismatch": False, "play_count": 50000000},
        }
        unrelated = {
            "id": "other-id", "title": "Flashing Lights", "artist": "RoadTrip",
            "webpage_url": "https://www.jiosaavn.com/song/flashing-lights/other",
            "score": 0.90,
            "signals": {"artist_hint": False, "artist_mismatch": False, "play_count": 100},
        }
        self.patch(search_candidates=lambda q, limit=5: [original, unrelated])

        def fail_original(_opts, url):
            self.extracted.append(url)
            raise RuntimeError("original stream unavailable")

        self.patch(_extract_once=fail_original)
        with self.assertRaises(m.StreamResolveError):
            self.run_resolve("Flashing Lights")
        self.assertEqual(self.extracted, [original["webpage_url"]])

    def test_extractor_metadata_must_match_the_validated_candidate(self):
        candidate = {
            "id": "validated-id", "title": "Those Eyes", "artist": "New West",
            "webpage_url": "https://www.jiosaavn.com/song/those-eyes/validated-token",
            "score": 0.9,
            "signals": {"artist_hint": True, "artist_mismatch": False},
        }
        self.patch(search_candidates=lambda q, limit=5: [candidate])

        def return_different_track(_opts, url):
            self.extracted.append(url)
            return {
                "title": "Unrelated Song", "artist": "Different Artist",
                "webpage_url": "https://www.jiosaavn.com/song/unrelated/other-token",
                "url": "http://media",
            }

        self.patch(_extract_once=return_different_track)
        with self.assertRaises(m.StreamResolveError):
            self.run_resolve("Those Eyes by New West")
        self.assertEqual(self.extracted, [candidate["webpage_url"]])


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
            webpage_url="https://www.jiosaavn.com/song/flashing-lights/vdcjl4dH",
            song_id="vdcjl4dH",
            album="Graduation",
            language="english",
        )
        self.detail = {
            "id": "vdcjl4dH",
            "title": "Flashing Lights",
            "artist": "Kanye West",
            "artist_ids": ["kanye-id"],
            "album_id": "grad-id",
            "language": "english",
            "webpage_url": self.finished.webpage_url,
        }
        self.patch(
            _resolve_song_detail=lambda track: self.detail,
            _saavn_reco=lambda song_id, language, limit=12: [
                make_candidate("Flashing Lights", "Kanye West", "vdcjl4dH"),
                make_candidate("Stronger", "Kanye West", "stronger"),
                make_candidate("Stronger", "Kanye West", "stronger"),
            ],
            _saavn_artist_other_top_songs=self._same_artist,
            _saavn_album_tracks=lambda album_id, limit=30: [
                make_candidate("Champion", "Kanye West", "champion", album="Graduation"),
            ],
            _saavn_similar_artists=lambda artist_id: [{"id": "jayz-id", "name": "Jay-Z"}],
            _saavn_artist_page=lambda artist_id: {"topSongs": []},
        )

    @staticmethod
    def _same_artist(artist_ids, song_id, language, limit=12):
        if artist_ids == ["kanye-id"]:
            return [
                make_candidate("Good Life", "Kanye West", "good-life"),
                make_candidate("Homecoming", "Kanye West", "homecoming"),
            ]
        return [
            make_candidate("Encore", "Jay-Z", "encore"),
            make_candidate("99 Problems", "Jay-Z", "99-problems"),
        ]

    def test_same_artist_ranks_above_similar_artist(self):
        weighted = m.MusicPlayer._autoplay_candidates(None, self.finished)
        ranked = m.MusicPlayer._rank_autoplay(FakeSelf(), weighted, self.finished)
        ids = [c["id"] for _, c in ranked]
        self.assertIn("good-life", ids)
        self.assertIn("encore", ids)
        self.assertLess(ids.index("good-life"), ids.index("encore"))

    def test_finished_track_and_duplicates_are_excluded(self):
        weighted = m.MusicPlayer._autoplay_candidates(None, self.finished)
        ranked = m.MusicPlayer._rank_autoplay(FakeSelf(), weighted, self.finished)
        ids = [c["id"] for _, c in ranked]
        self.assertNotIn("vdcjl4dH", ids, "the finished track must not recommend itself")
        self.assertEqual(ids.count("stronger"), 1, "duplicates must collapse to one entry")

    def test_recent_history_is_excluded(self):
        weighted = m.MusicPlayer._autoplay_candidates(None, self.finished)
        ranked = m.MusicPlayer._rank_autoplay(FakeSelf(["id:homecoming"]), weighted, self.finished)
        ids = [c["id"] for _, c in ranked]
        self.assertNotIn("homecoming", ids)

    def _empty_rails(self):
        self.patch(
            _saavn_reco=lambda song_id, language, limit=12: [],
            _saavn_artist_other_top_songs=lambda *a, **k: [],
            _saavn_album_tracks=lambda *a, **k: [],
            _saavn_similar_artists=lambda artist_id: [],
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

        self.patch(search_jiosaavn=fake_search)
        weighted = m.MusicPlayer._autoplay_candidates(None, self.finished)
        ids = [c["id"] for _, c in weighted]
        self.assertIn("stronger", ids, "same-artist songs should be surfaced")
        self.assertNotIn("unrelated", ids, "the fallback must stay on the same artist")
        self.assertTrue(calls, "the artist must be searched")
        self.assertNotIn(self.finished.title.lower(), calls[0].lower())

    def test_fallback_is_skipped_when_rails_return_tracks(self):
        # The default rails already return tracks, so no artist search happens.
        self.patch(
            search_jiosaavn=lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("broad search should not run")
            )
        )
        weighted = m.MusicPlayer._autoplay_candidates(None, self.finished)
        self.assertTrue(weighted)


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
            song_id="vdcjl4dH",
            webpage_url="https://www.jiosaavn.com/song/flashing-lights/vdcjl4dH",
            language="english",
        )
        self.candidate = make_candidate("Stronger", "Kanye West", "stronger")

    def run_autoplay(self, player):
        asyncio.run(player._queue_autoplay(self.finished))

    def resolved(self, title="Stronger", song_id="stronger"):
        return m.Track(
            source="s", title=title, requested_by="tester", song_id=song_id,
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
            raise RuntimeError("jiosaavn exploded")

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

    def test_identity_derived_from_jiosaavn_url(self):
        track = m.Track(
            source="x",
            title="Foo",
            webpage_url="https://www.jiosaavn.com/song/foo/barTOKEN",
        )
        self.assertEqual(track.song_id, "barTOKEN")
        self.assertEqual(m._track_identity(track), "id:barTOKEN")


# --- Normalization -------------------------------------------------------


class NormalizationTests(unittest.TestCase):
    def test_v3_search_shape(self):
        candidate = m._normalize_song_item(
            v3("Flashing Lights", "Kanye West", "Graduation", 237, "english",
               "https://www.jiosaavn.com/song/flashing-lights/A", "vdcjl4dH")
        )
        self.assertEqual(candidate["title"], "Flashing Lights")
        self.assertEqual(candidate["artist"], "Kanye West")
        self.assertEqual(candidate["album"], "Graduation")
        self.assertEqual(candidate["duration"], 237)
        self.assertEqual(candidate["language"], "english")
        self.assertTrue(candidate["id"])

    def test_v4_entity_shape_reads_more_info(self):
        candidate = m._normalize_song_item(
            v4("Khat", ["Navjot Ahuja"], "Khat", 296, "hindi",
               "https://www.jiosaavn.com/song/khat/A", "song-token", album_id="album-token")
        )
        self.assertEqual(candidate["artist"], "Navjot Ahuja")
        self.assertEqual(candidate["artist_ids"], ["navjot-ahuja-id"])
        self.assertEqual(candidate["album_id"], "album-token")
        self.assertEqual(candidate["duration"], 296)

    def test_thumbnail_is_upgraded(self):
        candidate = m._normalize_song_item(
            v3("X", "Y", "Z", 100, "english",
               "https://www.jiosaavn.com/song/x/A", "id",
               image="https://c.saavncdn.com/x-150x150.jpg")
        )
        self.assertNotIn("150x150", candidate["thumbnail"])
        self.assertIn("500x500", candidate["thumbnail"])

    def test_items_without_a_permalink_are_rejected(self):
        self.assertIsNone(m._normalize_song_item({"song": "No URL"}))

    def test_v3_carries_artist_and_album_ids(self):
        item = v3("Flashing Lights", "Kanye West", "Graduation", 237, "english",
                  "https://www.jiosaavn.com/song/flashing-lights/BgwIWxgEU3s", "vdcjl4dH")
        item["primary_artists_id"] = "527097, 564807"
        item["albumid"] = "1945440"
        candidate = m._normalize_song_item(item)
        self.assertEqual(candidate["artist_ids"], ["527097", "564807"])
        self.assertEqual(candidate["album_id"], "1945440")

    def test_v4_artist_map_wins_over_v3_field(self):
        item = v4("Khat", ["Navjot Ahuja"], "Khat", 296, "hindi",
                  "https://www.jiosaavn.com/song/khat/A", "song-token", album_id="album-token")
        item["primary_artists_id"] = "should-not-be-used"
        candidate = m._normalize_song_item(item)
        self.assertEqual(candidate["artist_ids"], ["navjot-ahuja-id"])


class SongDetailTests(PatchedTestCase):
    """Guards the two autoplay bugs: wrong token and a list response."""

    def test_raw_song_id_returns_a_list_and_is_handled(self):
        # webapi.get answers the short id with [] rather than a dict.
        self.patch(_saavn_get=lambda *a, **k: [])
        self.assertIsNone(m._saavn_song_detail("vdcjl4dH"))

    def test_perma_token_detail_is_normalized(self):
        payload = {
            "songs": [
                v4("Flashing Lights", ["Kanye West"], "Graduation", 237, "english",
                   "https://www.jiosaavn.com/song/flashing-lights/BgwIWxgEU3s",
                   "vdcjl4dH", album_id="1945440")
            ]
        }
        self.patch(_saavn_get=lambda *a, **k: payload)
        detail = m._saavn_song_detail("BgwIWxgEU3s")
        self.assertIsNotNone(detail)
        self.assertEqual(detail["artist_ids"], ["kanye-west-id"])
        self.assertEqual(detail["album_id"], "1945440")

    def test_resolve_song_detail_prefers_the_perma_token(self):
        seen: list[str] = []

        def fake_detail(token):
            seen.append(token)
            return {"id": "vdcjl4dH", "artist_ids": ["527097"]} if token == "BgwIWxgEU3s" else None

        self.patch(_saavn_song_detail=fake_detail)
        track = m.Track(
            source="x",
            title="Flashing Lights",
            song_id="vdcjl4dH",
            webpage_url="https://www.jiosaavn.com/song/flashing-lights/BgwIWxgEU3s",
        )
        detail = m._resolve_song_detail(track)
        self.assertEqual(detail["artist_ids"], ["527097"])
        self.assertEqual(seen[0], "BgwIWxgEU3s", "the perma-url token must be tried first")


if __name__ == "__main__":
    unittest.main(verbosity=2)
