import re
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_translation_html as bth  # noqa: E402
import check_paragraph_ids  # noqa: E402
import runner  # noqa: E402


def section(start: str, translation: str, title: str = "01 標題") -> str:
    return f"""## {title}
Range: {start}-p0001a05

Source:
<<<
原文
>>>

Translation:
<<<
{translation}
>>>

Note:
<<<
校註
>>>
"""


def ids_of(md: str) -> list[list[str | None]]:
    return [list(entry.paragraph_ids) for entry in bth.parse_entries(md)]


S1, S2 = "T29n1558_p0001a02", "T29n1558_p0001b02"
MD = "# 卷\n\n" + section(S1, "第一段\n第一段次行\n\n第二段\n\n第三段") + "\n" + section(S2, "", "02 未譯")


class AssignParagraphIdsTests(unittest.TestCase):
    def test_every_paragraph_gets_a_section_prefixed_id(self):
        marked = bth.assign_paragraph_ids(MD)
        (a, b, c), (empty,) = ids_of(marked)
        for pid in (a, b, c):
            self.assertRegex(pid, rf"^{S1}-[0-9a-f]{{6}}$")
        self.assertRegex(empty, rf"^{S2}-[0-9a-f]{{6}}$")
        self.assertEqual(len({a, b, c, empty}), 4)

    def test_markers_are_stripped_from_entry_text(self):
        entries = bth.parse_entries(bth.assign_paragraph_ids(MD))
        self.assertEqual(entries[0].translation, "第一段\n第一段次行\n\n第二段\n\n第三段")
        self.assertEqual(entries[1].translation, "")
        self.assertNotIn("<!--", "".join(e.source + e.note for e in entries))

    def test_assignment_is_idempotent_and_deterministic(self):
        marked = bth.assign_paragraph_ids(MD)
        self.assertEqual(bth.assign_paragraph_ids(marked), marked)
        self.assertEqual(bth.assign_paragraph_ids(MD), marked)

    def test_ids_survive_edits_to_own_and_other_paragraphs(self):
        marked = bth.assign_paragraph_ids(MD)
        before = ids_of(marked)
        edited = marked.replace("第一段次行", "第一段改寫").replace("第三段", "第三段加長了")
        self.assertEqual(ids_of(bth.assign_paragraph_ids(edited)), before)

    def test_split_keeps_id_on_first_part_and_new_part_gets_new_id(self):
        marked = bth.assign_paragraph_ids(MD)
        (a, b, c), _ = ids_of(marked)
        split = bth.assign_paragraph_ids(marked.replace("第一段\n第一段次行", "第一段\n\n第一段次行"))
        (a1, new, b1, c1), _ = ids_of(split)
        self.assertEqual((a1, b1, c1), (a, b, c))
        self.assertNotIn(new, {a, b, c})

    def test_merge_keeps_first_id(self):
        marked = bth.assign_paragraph_ids(MD)
        (a, b, c), _ = ids_of(marked)
        merged = re.sub(r"第一段次行\n\n", "第一段次行\n", marked)
        (m, c1), _ = ids_of(bth.assign_paragraph_ids(merged))
        self.assertEqual((m, c1), (a, c))

    def test_duplicate_or_reserved_ids_are_replaced(self):
        md = "# 卷\n\n" + section(S1, "<!-- #dup -->\n甲\n\n<!-- #dup -->\n乙\n\n<!-- #readerEnd -->\n丙")
        (a, b, c), = ids_of(bth.assign_paragraph_ids(md))
        self.assertEqual(a, "dup")
        self.assertNotIn(b, {"dup", "readerEnd"})
        self.assertNotIn(c, {"dup", "readerEnd"})

    def test_identical_paragraphs_get_distinct_ids(self):
        md = "# 卷\n\n" + section(S1, "如是\n\n如是")
        (a, b), = ids_of(bth.assign_paragraph_ids(md))
        self.assertNotEqual(a, b)

    def test_marker_only_paragraph_names_the_next_paragraph(self):
        md = "# 卷\n\n" + section(S1, "<!-- #keep -->\n\n甲")
        self.assertEqual(ids_of(md), [["keep"]])
        self.assertEqual(bth.parse_entries(md)[0].translation, "甲")

    def test_marker_with_full_width_indent_is_still_a_marker(self):
        md = "# 卷\n\n" + section(S1, "　<!-- #x -->\n甲 乙")
        entry = bth.parse_entries(md)[0]  # parse_entries already turned U+2028 into a newline before ids
        self.assertEqual((entry.paragraph_ids, entry.translation), (("x",), "甲\n乙"))
        self.assertIn("<!-- #x -->\n甲 乙", bth.assign_paragraph_ids(md))  # the md keeps its characters


class CarryParagraphIdsTests(unittest.TestCase):
    OLD = "<!-- #a -->\n甲\n\n<!-- #b -->\n乙\n\n<!-- #c -->\n丙"

    def carry(self, new_text: str) -> list[str | None]:
        return [pid for pid, _c in bth.marked_paragraphs(bth.carry_paragraph_ids(self.OLD, new_text))]

    def test_reworded_paragraphs_keep_ids(self):
        self.assertEqual(self.carry("甲改\n\n乙\n\n丙改"), ["a", "b", "c"])

    def test_split_and_insert_get_no_id_yet(self):
        self.assertEqual(self.carry("甲\n\n乙前半\n\n乙後半\n\n丙"), ["a", "b", None, "c"])
        self.assertEqual(self.carry("新段\n\n甲\n\n乙\n\n丙"), [None, "a", "b", "c"])

    def test_deleted_paragraph_id_is_dropped(self):
        self.assertEqual(self.carry("甲\n\n丙"), ["a", "c"])

    def test_unmarked_old_block_returns_text_unchanged(self):
        self.assertEqual(bth.carry_paragraph_ids("甲\n\n乙", "  新譯\n\n第二段  "), "新譯\n\n第二段")

    def test_empty_placeholder_id_moves_to_first_paragraph(self):
        out = bth.carry_paragraph_ids("<!-- #p -->", "甲\n\n乙")
        self.assertEqual([pid for pid, _c in bth.marked_paragraphs(out)], ["p", None])

    def test_runner_splice_keeps_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "T29-001-baihua.md"
            md.write_text("# 卷\n\n" + section(S1, self.OLD), encoding="utf-8")
            runner.splice(md, 0, "甲（審訂）\n\n乙\n\n丙", "新校註")
            entry = bth.parse_entries(md.read_text(encoding="utf-8"))[0]
        self.assertEqual(entry.paragraph_ids, ("a", "b", "c"))
        self.assertEqual(entry.translation, "甲（審訂）\n\n乙\n\n丙")
        self.assertEqual(entry.note, "新校註")


class BuildAndCheckTests(unittest.TestCase):
    def test_build_page_writes_ids_to_md_and_html_and_check_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "T1558-001-baihua.md"
            md.write_text(MD, encoding="utf-8")
            out = bth.build_page(md, Path(tmp) / "page.html")
            marked = md.read_text(encoding="utf-8")
            page = out.read_text(encoding="utf-8")
            self.assertEqual(check_paragraph_ids.main([str(out)]), 0)
        for ids in ids_of(marked):
            for pid in ids:
                self.assertIn(f'<p id="{pid}">', page)
        self.assertNotIn("<!--", page)

    def test_render_refuses_paragraphs_without_ids(self):
        with self.assertRaises(ValueError):
            bth.render_text("甲\n\n乙", (None, None))

    def test_check_fails_on_missing_and_duplicate_ids(self):
        bad = """<main><section id="T29n1558_p0001a02"><div class="translation-text">
<span class="line-range">白話譯文</span>
<p id="x-1">甲<br>乙</p>
<p>丙</p>
<p id="x-1">丁</p>
<p id="stayHere">戊</p>
</div><div class="source-text"><p>原文</p></div></section></main>"""
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "bad.html"
            page.write_text(bad, encoding="utf-8")
            problems, count = check_paragraph_ids.page_problems(page)
            self.assertEqual(check_paragraph_ids.main([str(page)]), 1)
        self.assertEqual(count, 4)
        self.assertIn("translation paragraph 2 has no id", problems)
        self.assertIn("duplicate id 'x-1'", problems)
        self.assertIn("paragraph id 'stayHere' is a reserved page id", problems)


if __name__ == "__main__":
    unittest.main()
