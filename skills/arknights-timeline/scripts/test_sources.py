"""Offline checks for selection ambiguity and human-owned map choices."""
import tempfile
import unittest
from pathlib import Path

from fetch_map import map_id, rune_selection, select
from source_io import Snapshot


class SourceTests(unittest.TestCase):
    def test_url_preserves_variant(self):
        self.assertEqual(map_id("http://lutonada.online/?id=M8-8Mat"), "M8-8Mat")

    def test_same_code_requires_disambiguation(self):
        tree = [{"id": "normal", "operation": "M8-8", "levelId": "normal/path"},
                {"id": "M8-8Mat", "operation": "M8-8", "levelId": "matrix/path"}]
        with self.assertRaisesRegex(ValueError, "uniquely"):
            select(tree, "M8-8")
        self.assertEqual(select(tree, "M8-8Mat")[0]["levelId"], "matrix/path")

    def test_rune_choice_is_not_silently_defaulted(self):
        data = {"parsedRunes": [
            {"runeId": "base", "essential": True},
            {"runeId": "a", "essential": True, "exclusiveGroupId": "route"},
            {"runeId": "b", "essential": True, "exclusiveGroupId": "route"}]}
        result = rune_selection(data, [], True)
        self.assertEqual(result["status"], "needs_map_choices")
        self.assertEqual(result["selected_ids"], ["base"])
        self.assertEqual(result["missing_required_groups"][0]["options"], ["a", "b"])
        self.assertEqual(rune_selection(data, ["a"], True)["status"], "specified_not_applied")
        with self.assertRaisesRegex(ValueError, "Mutually exclusive"):
            rune_selection(data, ["a", "b"], True)
        with self.assertRaisesRegex(ValueError, "Unknown rune"):
            rune_selection(data, ["unknown"], True)

    def test_optional_runes_need_explicit_completion(self):
        data = {"parsedRunes": [{"runeId": "extra", "essential": False}]}
        self.assertEqual(rune_selection(data, [], False)["status"], "needs_map_choices")
        self.assertEqual(rune_selection(data, [], True)["selected_ids"], [])

    def test_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "keep.txt"
            marker.write_text("existing data", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                Snapshot(directory, "test")
            self.assertEqual(marker.read_text(encoding="utf-8"), "existing data")


if __name__ == "__main__":
    unittest.main()
