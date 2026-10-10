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
import unittest

os.environ.setdefault("DISCORD_TOKEN", "test-token")

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cogs.music as m  # noqa: E402


# --- Fixtures ------------------------------------------------------------


def v3(song, artists, album, duration, language, url, song_id, image=None):
    """A JioSaavn v3 `search.getResults` song object."""
    return {
        "song": song,
        "primary_artists": artists,
        "album": album,
        "duration": str(duration),
        "language": language,
        "perma_url": url,
        "id": song_id,
        "image": image or "https://c.saavncdn.com/x-150x150.jpg",
    }


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


def fake_flashing_search(query, limit=None):
    if "flashing" in query.lower():
        return [dict(c) for c in FLASHING_LIGHTS][: (limit or 5)]
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


# --- resolve_track confidence gate (async) -------------------------------


class ResolveTrackTests(PatchedTestCase):
    def setUp(self):
        super().setUp()
        self.extracted = []

        def fake_extract(_opts, url):
            self.extracted.append(url)
            if url.endswith("/F"):
                raise RuntimeError("stream unavailable")
            return {"title": "ok", "webpage_url": url, "url": "http://media"}

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

    def test_no_title_keyword_fallback(self):
        # Even if every relationship source is empty, autoplay must not fall back
        # to matching the title words of the finished track.
        self.patch(
            _saavn_reco=lambda song_id, language, limit=12: [],
            _saavn_artist_other_top_songs=lambda *a, **k: [],
            _saavn_album_tracks=lambda *a, **k: [],
            _saavn_similar_artists=lambda artist_id: [],
        )
        weighted = m.MusicPlayer._autoplay_candidates(None, self.finished)
        self.assertEqual(weighted, [])


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
