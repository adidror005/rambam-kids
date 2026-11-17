import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import common


class DataTests(unittest.TestCase):
    def test_parse_ref(self):
        self.assertEqual(common.parse_chapter_ref('Mishneh Torah, Slaves 4'), ('Mishneh Torah, Slaves', 4))

    def test_incremental_merge_does_not_delete_translations(self):
        with tempfile.TemporaryDirectory() as d:
            new_data = Path(d) / 'data'
            with patch.object(common, 'DATA', new_data), patch.object(common, 'BOOKS', new_data / 'books'):
                initial = {'ref': 'Mishneh Torah, Slaves 4', 'segments': {
                    '1': {'original_hebrew': 'original', 'kids_hebrew': 'first kid text'}}}
                common.merge_chapter(initial)
                update = {'ref': 'Mishneh Torah, Slaves 4', 'segments': {
                    '1': {'original_hebrew': 'should not replace', 'kids_english': 'English'},
                    '2': {'original_hebrew': 'new original'}}}
                path, result = common.merge_chapter(update)
                self.assertEqual(result['segments']['1']['kids_hebrew'], 'first kid text')
                self.assertEqual(result['segments']['1']['kids_english'], 'English')
                self.assertEqual(result['segments']['1']['original_hebrew'], 'original')
                self.assertEqual(len(result['segments']), 2)
                manifest = common.load_json(new_data / 'manifest.json')
                self.assertEqual(manifest['books'][0]['chapters'][0]['counts']['kids_english'], 1)
                self.assertTrue(path.exists())


if __name__ == '__main__':
    unittest.main()
