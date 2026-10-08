#!/usr/bin/env python3
"""Check that every translation <p> on the treatise pages has a valid, unique id.

The reader app restores the reading position by these ids, so a missing or duplicate id is an error.
"""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path

from build_translation_html import PAGE_UI_IDS

ROOT = Path(__file__).resolve().parents[1]
PAGE_GLOB = "*/translations/*-baihua*.html"
ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[tuple[str, bool]] = []  # (tag, is a .translation-text div)
        self.ids: list[str] = []
        self.paragraph_ids: list[str | None] = []  # one per <p> directly inside .translation-text

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        if attr.get("id") is not None:
            self.ids.append(attr["id"])
        if tag == "p" and self.stack and self.stack[-1][1]:
            self.paragraph_ids.append(attr.get("id"))
        if tag not in VOID_TAGS:
            classes = (attr.get("class") or "").split()
            self.stack.append((tag, tag == "div" and "translation-text" in classes))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break


def page_problems(page: Path) -> tuple[list[str], int]:
    parser = PageParser()
    parser.feed(page.read_text(encoding="utf-8"))
    problems = []
    if not parser.paragraph_ids:
        problems.append("no translation paragraphs found")
    for n, pid in enumerate(parser.paragraph_ids, 1):
        if not pid:
            problems.append(f"translation paragraph {n} has no id")
        elif not ID_RE.fullmatch(pid):
            problems.append(f"invalid id {pid!r}")
        elif pid in PAGE_UI_IDS:
            problems.append(f"paragraph id {pid!r} is a reserved page id")
    seen: set[str] = set()
    for pid in parser.ids:
        if pid in seen:
            problems.append(f"duplicate id {pid!r}")
        seen.add(pid)
    return problems, len(parser.paragraph_ids)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    pages = [Path(a) for a in args] or sorted((ROOT / "docs").glob(PAGE_GLOB))
    failures, paragraphs = [], 0
    for page in pages:
        problems, count = page_problems(page)
        paragraphs += count
        failures += [f"{page}: {problem}" for problem in problems]
    if failures:
        print("Translation paragraph id problems:")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print(f"{len(pages)} pages, {paragraphs} translation paragraphs: every id present and unique.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
