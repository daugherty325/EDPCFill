from __future__ import annotations

import gc
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import mtg_art_picker as core
import mtg_art_picker_web as web


class DeckOrderTests(unittest.TestCase):
    def test_duplicate_lines_remain_separate_picker_slots(self) -> None:
        entries = core.parse_deck_list("1x Orim's Chant\n1x Orim's Chant")

        self.assertEqual([entry.name for entry in entries], ["Orim's Chant", "Orim's Chant"])
        self.assertEqual([entry.quantity for entry in entries], [1, 1])

    def test_interleaved_duplicates_keep_input_order(self) -> None:
        entries = core.parse_deck_list("1x Orim's Chant\n1x Mountain\n1x Orim's Chant")

        self.assertEqual([entry.name for entry in entries], ["Orim's Chant", "Mountain", "Orim's Chant"])

    def test_basic_land_names_cover_regular_snow_and_wastes(self) -> None:
        basics = ["Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes", "Snow-Covered Island"]

        self.assertTrue(all(core.is_basic_land_name(name) for name in basics))
        self.assertFalse(core.is_basic_land_name("Dryad Arbor"))

    def test_excluding_basics_preserves_nonbasic_order_and_duplicates(self) -> None:
        entries = core.parse_deck_list("1x Orim's Chant\n4x Mountain\n1x Wastes\n1x Orim's Chant")

        filtered = core.exclude_basic_lands(entries)

        self.assertEqual([entry.name for entry in filtered], ["Orim's Chant", "Orim's Chant"])

    def test_print_images_follow_slot_order_and_quantity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = [Path(temp_dir, name) for name in ("chant-one.png", "mountain.png", "chant-two.png")]
            for path in paths:
                path.touch()
            job = {
                "image_paths": {"chant-one": str(paths[0]), "mountain": str(paths[1]), "chant-two": str(paths[2])},
                "print_image_paths": {"chant-one": str(paths[0]), "mountain": str(paths[1]), "chant-two": str(paths[2])},
            }
            payload = {
                "slots": [
                    {"quantity": 1, "current_index": 0, "options": [{"preference_id": "chant-one"}]},
                    {"quantity": 2, "current_index": 0, "options": [{"preference_id": "mountain"}]},
                    {"quantity": 1, "current_index": 0, "options": [{"preference_id": "chant-two"}]},
                ]
            }

            self.assertEqual(web.selected_image_paths(payload, job), [paths[0], paths[1], paths[1], paths[2]])


class SelectFirstTests(unittest.TestCase):
    def test_foreign_and_list_printings_have_independent_filters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = core.ScryfallClient(Path(temp_dir))

            def card(card_id: str, *, lang: str = "en", set_code: str = "tst", set_name: str = "Test") -> dict:
                return {
                    "id": card_id,
                    "oracle_id": "oracle-id",
                    "name": "Example Card",
                    "set": set_code,
                    "set_name": set_name,
                    "collector_number": card_id,
                    "games": ["paper"],
                    "lang": lang,
                    "image_uris": {"png": f"https://cards.example/{card_id}.png"},
                }

            cards = [
                card("english"),
                card("japanese", lang="ja"),
                card("the-list", set_code="plst", set_name="The List"),
            ]
            visible = client._options_from_cards(
                cards,
                "oracle-id",
                "Example Card",
                lambda _message: None,
                download_missing=False,
                include_uncached=True,
            )
            foreign_filtered = client._options_from_cards(
                cards,
                "oracle-id",
                "Example Card",
                lambda _message: None,
                download_missing=False,
                hide_foreign=True,
                include_uncached=True,
            )
            list_filtered = client._options_from_cards(
                cards,
                "oracle-id",
                "Example Card",
                lambda _message: None,
                download_missing=False,
                hide_list=True,
                include_uncached=True,
            )
            both_filtered = client._options_from_cards(
                cards,
                "oracle-id",
                "Example Card",
                lambda _message: None,
                download_missing=False,
                hide_foreign=True,
                hide_list=True,
                include_uncached=True,
            )

            visible_ids = {option.card_id for option in visible}
            foreign_filtered_ids = {option.card_id for option in foreign_filtered}
            list_filtered_ids = {option.card_id for option in list_filtered}
            both_filtered_ids = {option.card_id for option in both_filtered}
            del client
            gc.collect()
            self.assertEqual(visible_ids, {"english", "japanese", "the-list"})
            self.assertEqual(foreign_filtered_ids, {"english", "the-list"})
            self.assertEqual(list_filtered_ids, {"english", "japanese"})
            self.assertEqual(both_filtered_ids, {"english"})

    def test_preference_categories_override_date_sort_and_disabled_categories_are_hidden(self) -> None:
        def option(card_id: str, released_at: str, *categories: str) -> core.ArtOption:
            return core.ArtOption(
                card_id=card_id,
                oracle_id="oracle",
                display_name=card_id,
                printed_name=card_id,
                set_code="tst",
                set_name="Test",
                collector_number=card_id,
                released_at=released_at,
                artist="Artist",
                png_url=f"https://cards.example/{card_id}.png",
                cache_path=Path(f"{card_id}.png"),
                preference_categories=categories,
            )

        options = [
            option("old-border-newer", "2000-01-01", "old_border"),
            option("borderless-newer", "2024-01-01", "borderless"),
            option("borderless-older", "2020-01-01", "borderless"),
            option("promo", "1999-01-01", "new_border", "promo"),
            option("new-border", "2005-01-01", "new_border"),
        ]
        preferences = [
            {"key": "borderless", "enabled": True},
            {"key": "old_border", "enabled": True},
            {"key": "new_border", "enabled": True},
            {"key": "promo", "enabled": False},
            {"key": "extended_art", "enabled": True},
            {"key": "foreign", "enabled": True},
            {"key": "the_list", "enabled": True},
        ]

        result = core.filter_and_sort_art_options(options, "oldest", preferences)

        self.assertEqual(
            [item.card_id for item in result],
            ["borderless-older", "borderless-newer", "old-border-newer", "new-border"],
        )

    def test_scryfall_categories_distinguish_borders_and_cross_cutting_traits(self) -> None:
        self.assertEqual(
            core.art_preference_categories_for_card({"frame": "1997", "lang": "en"}),
            ("old_border",),
        )
        self.assertEqual(
            core.art_preference_categories_for_card(
                {
                    "frame": "2015",
                    "border_color": "borderless",
                    "lang": "ja",
                    "promo": True,
                    "set": "plst",
                    "set_name": "The List",
                }
            ),
            ("borderless", "foreign", "promo", "the_list"),
        )
        self.assertEqual(
            core.art_preference_categories_for_card(
                {"frame": "2015", "frame_effects": ["extendedart"], "lang": "en"}
            ),
            ("extended_art",),
        )

    def test_uncached_printing_is_available_without_downloading_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = core.ScryfallClient(Path(temp_dir))
            card = {
                "id": "card-id",
                "oracle_id": "oracle-id",
                "name": "Example Card",
                "set": "tst",
                "set_name": "Test Set",
                "collector_number": "42",
                "released_at": "2026-01-01",
                "artist": "Test Artist",
                "games": ["paper"],
                "lang": "en",
                "image_uris": {"png": "https://cards.example/example.png"},
            }

            options = client._options_from_cards(
                [card],
                "oracle-id",
                "Example Card",
                lambda _message: None,
                download_missing=False,
                include_uncached=True,
            )

            self.assertEqual(len(options), 1)
            self.assertEqual(options[0].png_url, "https://cards.example/example.png")
            self.assertFalse(options[0].cache_path.exists())
            serialized = web.option_to_json(options[0], "job-id")
            self.assertEqual(serialized["image_url"], "https://cards.example/example.png")
            del client
            gc.collect()

    def test_prepare_downloads_only_distinct_selected_arts(self) -> None:
        class FakeUpscaler:
            def __init__(self) -> None:
                self.received: list[Path] = []

            def ensure_batch(self, sources, status_callback=None, progress_callback=None):
                self.received = list(sources)
                if progress_callback:
                    progress_callback(len(sources), len(sources))
                return {source: source for source in sources}

            def cached_path(self, source):
                return source

        class FakeClient:
            instance = None

            def __init__(self, _cache_dir) -> None:
                self.upscaler = FakeUpscaler()
                self.downloaded: list[str] = []
                FakeClient.instance = self

            def _download_png(self, option, _status_callback):
                self.downloaded.append(option.preference_id)
                option.cache_path.parent.mkdir(parents=True, exist_ok=True)
                option.cache_path.write_bytes(b"png")
                return True

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            selected_path = root / "png" / "selected.png"
            unselected_path = root / "png" / "unselected.png"
            job_id = "select-first-test"
            web.JOBS[job_id] = {
                "cache_dir": str(root),
                "image_paths": {},
                "print_image_paths": {},
                "option_sources": {
                    "selected": {
                        "card_id": "selected",
                        "display_name": "Selected",
                        "png_url": "https://cards.example/selected.png",
                        "cache_path": str(selected_path),
                    },
                    "unselected": {
                        "card_id": "unselected",
                        "display_name": "Unselected",
                        "png_url": "https://cards.example/unselected.png",
                        "cache_path": str(unselected_path),
                    },
                },
            }
            try:
                with patch.object(web.core, "ScryfallClient", FakeClient):
                    result = web.prepare_selected(
                        {
                            "job_id": job_id,
                            "print_items": [
                                {"preference_id": "selected"},
                                {"preference_id": "selected"},
                            ],
                        }
                    )
            finally:
                web.JOBS.pop(job_id, None)

            self.assertEqual(FakeClient.instance.downloaded, ["selected"])
            self.assertEqual(FakeClient.instance.upscaler.received, [selected_path])
            self.assertTrue(selected_path.exists())
            self.assertFalse(unselected_path.exists())
            self.assertEqual(result["prepared_count"], 1)

    def test_normal_cached_mode_still_hides_an_uncached_printing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = core.ScryfallClient(Path(temp_dir))
            card = {
                "id": "card-id",
                "oracle_id": "oracle-id",
                "name": "Example Card",
                "set": "tst",
                "set_name": "Test Set",
                "collector_number": "42",
                "games": ["paper"],
                "lang": "en",
                "image_uris": {"png": "https://cards.example/example.png"},
            }

            options = client._options_from_cards(
                [card],
                "oracle-id",
                "Example Card",
                lambda _message: None,
                download_missing=False,
            )

            self.assertEqual(options, [])
            del client
            gc.collect()

    def test_pdf_creation_reports_real_progress_through_finalization(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "card.png"
            Image.new("RGB", (488, 680), (30, 60, 90)).save(source)
            job_id = "pdf-progress-test"
            web.JOBS[job_id] = {
                "image_paths": {"card": str(source)},
                "print_image_paths": {"card": str(source)},
            }
            updates = []
            try:
                pdf_data, filename, card_count, page_count = web.create_print_pdf(
                    {
                        "job_id": job_id,
                        "filename": "progress-test",
                        "print_items": [{"preference_id": "card"}],
                        "page_size": "letter",
                        "orientation": "portrait",
                        "card_width_mm": 63,
                        "card_height_mm": 88,
                        "bleed_mm": 0,
                        "cut_lines": False,
                    },
                    progress_callback=lambda percent, status: updates.append((percent, status)),
                )
            finally:
                web.JOBS.pop(job_id, None)
                web.build_print_image.cache_clear()

            self.assertTrue(pdf_data.startswith(b"%PDF"))
            self.assertEqual((filename, card_count, page_count), ("progress-test.pdf", 1, 1))
            self.assertTrue(any(percent >= 70 for percent, _status in updates))
            self.assertEqual(updates[-1][0], 99)


if __name__ == "__main__":
    unittest.main()
