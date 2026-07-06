import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_translation_coverage as coverage  # noqa: E402


class CheckTranslationCoverageTests(unittest.TestCase):
    def test_parse_ranges_accepts_non_t1579_work_ids(self):
        text = """# Test

## 01 卷首題署
Range: T29n1558_p0001a02-p0001a07

Translation:
<<<
text
>>>
"""

        entries = coverage.parse_ranges(text)

        self.assertEqual(entries[0].start, "T29n1558_p0001a02")
        self.assertEqual(entries[0].end, "T29n1558_p0001a07")

    def test_data_line_ids_returns_full_line_ids(self):
        payload = {
            "results": [
                '<span class="lb" id="T29n1558_p0001a02">T29n1558_p0001a02</span>'
                '<span class="lb" id="T29n1558_p0001a07">T29n1558_p0001a07</span>'
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            ids = coverage.data_line_ids(path)

        self.assertEqual(ids, {"T29n1558_p0001a02", "T29n1558_p0001a07"})


if __name__ == "__main__":
    unittest.main()
