import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_translation_html as bth


class TooltipRenderingTests(unittest.TestCase):
    def test_juan_70_wraps_source_and_translation_with_stable_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "T1579-070-baihua.md"
            source.write_text(
                """# Test translation

## 02 Test entry
Range: T30n1579_p0684c24-p0684c25

Source:
<<<
為解脫而修行。
>>>

Translation:
<<<
為了解脫而修行。
>>>
""",
                encoding="utf-8",
            )
            entry = bth.parse_entries(source.read_text(encoding="utf-8"))[0]

            first = bth.render([entry], source, 70, "test")
            second = bth.render([entry], source, 70, "test")

        self.assertEqual(first, second)
        self.assertIn('data-term-id="vimoksha"', first)
        self.assertIn('data-term-occurrence="T1579-070-', first)
        translation_html, source_html = first.split('<div class="source-text"', 1)
        self.assertIn('data-term-id="vimoksha"', translation_html)
        self.assertIn('data-term-id="vimoksha"', source_html)

    def test_glossary_changes_are_visible_in_the_same_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            glossary_path = Path(tmp) / "terms.json"
            glossary_path.write_text(
                json.dumps({"terms": [self._term("samadhi", "等持", "心安住一境")]}),
                encoding="utf-8",
            )
            first = bth.term_tips(glossary_path)
            glossary_path.write_text(
                json.dumps({"terms": [self._term("samadhi", "等持", "專注一境")]}),
                encoding="utf-8",
            )
            second = bth.term_tips(glossary_path)

        self.assertIn("心安住一境", first[0].tip)
        self.assertIn("專注一境", second[0].tip)

    def test_alias_owner_and_overlap_are_deterministic(self):
        glossary = {
            "terms": [
                self._term("attention", "作意", "專注觀修"),
                self._term("seven-attentions", "七種作意", "七階段觀修"),
                self._term("alternate", "心念", "作意"),
            ]
        }

        tips = bth.build_term_tips(glossary)
        rendered = bth.wrap_terms(
            "<p>七種作意與作意</p>",
            tips=tips,
            occurrence_prefix="T1579-070-test",
        )

        self.assertEqual([], bth.validate_term_tips(tips))
        self.assertIn('data-term-id="seven-attentions"', rendered)
        self.assertIn('data-term-id="attention"', rendered)
        self.assertIn('data-term-alternates="alternate"', rendered)
        self.assertRegex(
            rendered,
            r'data-term-id="seven-attentions"[^>]*>七種作意〔七階段觀修〕</span>與'
            r'<span [^>]*data-term-id="attention"[^>]*>作意〔專注觀修〕</span>',
        )

    def test_first_occurrence_uses_visible_canonical_format(self):
        tips = bth.build_term_tips(
            {"terms": [self._term("attention", "作意", "專注觀修")]}
        )

        from_plain = bth.wrap_terms("<p>專注觀修與專注觀修</p>", tips=tips)
        from_xuanzang = bth.wrap_terms("<p>作意與作意</p>", tips=tips)

        self.assertRegex(
            from_plain,
            r'data-first-in-juan="true"[^>]*>作意〔專注觀修〕</span>與'
            r'<span [^>]*>專注觀修</span>',
        )
        self.assertRegex(
            from_xuanzang,
            r'data-first-in-juan="true"[^>]*>作意〔專注觀修〕</span>與'
            r'<span [^>]*>作意</span>',
        )

    def test_first_occurrence_visible_text_is_escaped(self):
        tips = bth.build_term_tips(
            {"terms": [self._term("escaped", "甲詞", "解釋 & 補充")]}
        )

        rendered = bth.wrap_terms("<p>甲詞</p>", tips=tips)

        self.assertIn(">甲詞〔解釋 &amp; 補充〕</span>", rendered)

    def test_ambiguous_alias_without_primary_owner_is_rejected(self):
        glossary = {
            "terms": [
                self._term("first", "甲詞", "共同別名"),
                self._term("second", "乙詞", "共同別名"),
            ]
        }

        with self.assertRaisesRegex(ValueError, "Ambiguous tooltip alias"):
            bth.build_term_tips(glossary)

    @staticmethod
    def _term(term_id, xuanzang, plain):
        return {
            "id": term_id,
            "xuanzang": xuanzang,
            "plain": plain,
            "english": [],
            "sanskrit": [],
        }


class FullRebuildTests(unittest.TestCase):
    def test_build_all_renders_every_translation(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            outputs = bth.build_all(output_dir=output_dir)

            expected = list((ROOT / "translations").glob("T1579-*-baihua.md"))
            self.assertEqual(len(expected), len(outputs))
            self.assertTrue(all(path.parent == output_dir for path in outputs))
            self.assertTrue(all(path.exists() for path in outputs))

    def test_cli_requires_explicit_legacy_gate_for_all(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(sys, "argv", ["build_translation_html.py", "--all",
                                        "--output-dir", tmp]), self.assertRaises(SystemExit):
                bth.main()

    def test_cli_requires_attestation_for_docs_or_explicit_external_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "T1579-067-baihua.md"
            source.write_text(
                "# test\n\n## 01 Test\nRange: T30n1579_p0001a01\n\n"
                "Source:\n<<<\n原文\n>>>\n\nTranslation:\n<<<\n譯文\n>>>\n",
                encoding="utf-8",
            )
            diagnostic = root / "diagnostic.html"
            with patch.object(sys, "argv", [
                "build_translation_html.py", "--translation", str(source),
                "--diagnostic-output", str(diagnostic),
            ]):
                self.assertEqual(0, bth.main())
            self.assertTrue(diagnostic.exists())
            with patch.object(sys, "argv", [
                "build_translation_html.py", "--translation", str(source),
            ]), self.assertRaises(SystemExit):
                bth.main()

    def test_attested_candidate_hash_mismatch_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._translation_source(root)
            output = root / "docs" / "T1579" / "translations" / "T1579-067-baihua.html"
            output.parent.mkdir(parents=True)
            original = b"sealed output that does not match the current renderer"
            output.write_bytes(original)
            attestation = root / "attestation.json"
            bth.publisher.create_attestation(
                root, attestation, {"translation": source, "volume_html": output},
            )

            with patch.object(bth, "ROOT", root), patch.object(sys, "argv", [
                "build_translation_html.py", "--translation", str(source),
                "--output", str(output), "--attestation", str(attestation),
            ]), self.assertRaises(SystemExit):
                bth.main()

            self.assertEqual(original, output.read_bytes())

    def test_direct_docs_build_requires_explicit_authorization(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._translation_source(root)
            output = root / "docs" / "T1579" / "translations" / "T1579-067-baihua.html"

            with patch.object(bth, "ROOT", root), self.assertRaises(PermissionError):
                bth.build_translation(source, output)
            self.assertFalse(output.exists())

    def test_attested_build_verifies_before_and_after_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._translation_source(root)
            output = root / "docs" / "T1579" / "translations" / "T1579-067-baihua.html"
            output.parent.mkdir(parents=True)
            output.write_bytes(bth.render_translation_bytes(source))
            attestation = root / "attestation.json"
            bth.publisher.create_attestation(
                root, attestation, {"translation": source, "volume_html": output},
            )

            verifier = bth.publisher.verify_attestation
            with patch.object(bth, "ROOT", root), patch.object(
                bth.publisher, "verify_attestation", wraps=verifier,
            ) as verify:
                bth.build_translation(source, output, attestation=attestation)

            self.assertEqual(2, verify.call_count)

    @staticmethod
    def _translation_source(root: Path) -> Path:
        source = root / "translations" / "T1579-067-baihua.md"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            "# test\n\n## 01 Test\nRange: T30n1579_p0001a01\n\n"
            "Source:\n<<<\n原文\n>>>\n\nTranslation:\n<<<\n譯文\n>>>\n",
            encoding="utf-8",
        )
        return source


class RangeParsingTests(unittest.TestCase):
    def test_parse_range_requires_full_cbeta_line_ids(self):
        self.assertEqual(
            ("T30n1579_p0684c24", "T30n1579_p0685a24"),
            bth.parse_range("T30n1579_p0684c24-p0685a24"),
        )
        for malformed in ("T30n1579_pabc", "T30n1579_p123"):
            with self.subTest(malformed=malformed):
                with self.assertRaisesRegex(ValueError, "Invalid range"):
                    bth.parse_range(malformed)


if __name__ == "__main__":
    unittest.main()
