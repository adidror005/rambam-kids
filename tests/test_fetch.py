"""Offline tests for full-book discovery, caching, and resumable downloading."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import common
import fetch_sefaria as fetch


class DiscoveryTests(unittest.TestCase):
    def test_toc_filters_only_mishneh_torah_in_order(self):
        toc = [
            {"category": "Halakhah", "contents": [
                {"category": "Mishneh Torah", "contents": [
                    {"title": "Mishneh Torah, Foundations of the Torah", "heTitle": "הלכות יסודי התורה"},
                    {"title": "Mishneh Torah, Slaves", "heTitle": "הלכות עבדים"},
                    {"title": "Mishneh Torah, Slaves", "heTitle": "Duplicate"},
                ]},
                {"title": "Lechem Mishneh on Mishneh Torah, Slaves"},
            ]}
        ]
        self.assertEqual(fetch.list_mishneh_torah_books(toc), [
            {"title": "Mishneh Torah, Foundations of the Torah", "he_title": "הלכות יסודי התורה"},
            {"title": "Mishneh Torah, Slaves", "he_title": "הלכות עבדים"},
        ])

    def test_shape_chapter_count(self):
        self.assertEqual(fetch.chapter_count_from_shape({"length": 9}, "Mishneh Torah, Slaves"), 9)
        self.assertEqual(fetch.chapter_count_from_shape({"chapters": [2, 3, 4]}, "Mishneh Torah, Slaves"), 3)
        self.assertEqual(fetch.chapter_count_from_shape([{"title": "Mishneh Torah, Slaves", "length": 9}], "Mishneh Torah, Slaves"), 9)
        with self.assertRaises(ValueError):
            fetch.chapter_count_from_shape({"length": 0, "chapters": []}, "Mishneh Torah, Slaves")

    def test_catalog_discovery_only_once(self):
        with tempfile.TemporaryDirectory() as td:
            catalog = Path(td) / "catalog.json"
            toc = [{"contents": [{"title": "Mishneh Torah, Slaves", "heTitle": "הלכות עבדים"}]}]
            responses = [toc, {"title": "Mishneh Torah, Slaves", "length": 9}]
            with patch.object(fetch, "CATALOG_PATH", catalog), patch.object(fetch, "request_json", side_effect=responses) as api:
                books = fetch.discover_all_books(delay=0)
                self.assertEqual(books, [{"title": "Mishneh Torah, Slaves", "he_title": "הלכות עבדים", "chapters": 9}])
                self.assertEqual(api.call_count, 2)
            with patch.object(fetch, "CATALOG_PATH", catalog), patch.object(fetch, "request_json", side_effect=AssertionError("should not request metadata")):
                self.assertEqual(fetch.discover_all_books(delay=0), books)


class FetchTests(unittest.TestCase):
    def test_bulk_limit_resumes_without_refetching_and_preserves_translations(self):
        catalog = [{"title": "Mishneh Torah, Slaves", "he_title": "הלכות עבדים", "chapters": 2}]
        calls = []

        def fake_get(ref, version):
            calls.append(ref)
            return {"ref": ref, "version_title": "Torat Emet 363", "segments": {
                "1": {"ref": ref + ":1", "original_hebrew": f"Text from {ref}"}
            }}

        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "data"
            with (patch.object(common, "DATA", data), patch.object(common, "BOOKS", data / "books"),
                  patch.object(fetch, "discover_all_books", return_value=catalog),
                  patch.object(fetch, "get_chapter", side_effect=fake_get),
                  patch.object(fetch.time, "sleep", return_value=None)):
                # First batch fetches chapter 1.
                fetch.main(["--all", "--limit", "1", "--delay", "0"])
                self.assertEqual(calls, ["Mishneh Torah, Slaves 1"])
                # Add kids version to the first chapter before the next download.
                common.merge_chapter({"ref": "Mishneh Torah, Slaves 1", "segments": {
                    "1": {"kids_english": "For kids"}
                }})
                # Next batch skips 1 and only fetches 2.
                fetch.main(["--all", "--limit", "1", "--delay", "0"])
                self.assertEqual(calls, ["Mishneh Torah, Slaves 1", "Mishneh Torah, Slaves 2"])
                # Entire corpus is cached; no calls this time.
                fetch.main(["--all", "--delay", "0"])
                self.assertEqual(len(calls), 2)
                self.assertEqual(common.load_json(data / "books/mishneh-torah-slaves/1.json")["segments"]["1"]["kids_english"], "For kids")
                # Explicit refresh refetches and still preserves kids version.
                fetch.main(["--all", "--refresh", "--limit", "1", "--delay", "0"])
                self.assertEqual(calls[-1], "Mishneh Torah, Slaves 1")
                self.assertEqual(common.load_json(data / "books/mishneh-torah-slaves/1.json")["segments"]["1"]["kids_english"], "For kids")

    def test_incomplete_manual_import_does_not_fake_download_checkpoint(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "data"
            with patch.object(common, "DATA", data), patch.object(common, "BOOKS", data / "books"):
                common.merge_chapter({"ref": "Mishneh Torah, Slaves 4", "segments": {"1": {"original_hebrew": "Only segment 1"}}})
                self.assertFalse(fetch.already_downloaded("Mishneh Torah, Slaves", 4, None, "Torat Emet 363"))


if __name__ == "__main__":
    unittest.main()


class MissingTranslationTests(unittest.TestCase):
    def test_all_missing_queue_only_includes_missing_fields(self):
        import list_missing
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "data"
            with patch.object(common, "DATA", data), patch.object(common, "BOOKS", data / "books"), patch.object(list_missing, "BOOKS", data / "books"):
                common.merge_chapter({"ref": "Mishneh Torah, Slaves 1", "segments": {
                    "1": {"original_hebrew": "foo", "kids_hebrew": "bar"}
                }})
                common.merge_chapter({"ref": "Mishneh Torah, Gifts to the Poor 1", "segments": {
                    "1": {"original_hebrew": "baz"}
                }})
                outfile = Path(td) / "missing.jsonl"
                list_missing.main(["--all", "--output", str(outfile)])
                rows = [json.loads(line) for line in outfile.read_text(encoding="utf-8").splitlines()]
                self.assertEqual(len(rows), 3)
                self.assertEqual({(r["chapter_ref"], r["field"]) for r in rows}, {
                    ("Mishneh Torah, Slaves 1", "kids_english"),
                    ("Mishneh Torah, Gifts to the Poor 1", "kids_english"),
                    ("Mishneh Torah, Gifts to the Poor 1", "kids_hebrew"),
                })
