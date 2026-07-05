#!/usr/bin/env python3
"""Check that relative href/src links in docs/ point at files that exist."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
HTML_DIR = ROOT / "docs"


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name in {"href", "src"} and value:
                self.links.append(value)


def is_local(link: str) -> bool:
    parsed = urlparse(link)
    return not parsed.scheme and not parsed.netloc and not link.startswith("#")


def main() -> int:
    missing: list[str] = []
    for page in sorted(HTML_DIR.rglob("*.html")):
        parser = LinkParser()
        parser.feed(page.read_text(encoding="utf-8"))
        for link in parser.links:
            if not is_local(link):
                continue
            target = unquote(urlparse(link).path.split("#")[0])
            if not target:
                continue
            resolved = (page.parent / target).resolve()
            if not resolved.exists():
                missing.append(f"{page.relative_to(ROOT)} -> {link}")
    if missing:
        print("Broken relative links:")
        for entry in missing:
            print(f"  {entry}")
        return 1
    print("All relative links resolve.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
