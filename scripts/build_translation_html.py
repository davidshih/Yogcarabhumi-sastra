#!/usr/bin/env python3
"""Render the juan 33 vernacular translation page."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "translations" / "T1579-033-baihua.md"
OUTPUT = ROOT / "html" / "translations" / "T1579-033-baihua.html"


@dataclass
class Entry:
    title: str
    range_label: str
    source: str
    translation: str
    note: str


def parse_entries(text: str) -> list[Entry]:
    entries: list[Entry] = []
    parts = re.split(r"^##\s+", text, flags=re.MULTILINE)
    for part in parts[1:]:
        lines = part.splitlines()
        title = lines[0].strip()
        body = "\n".join(lines[1:])
        range_match = re.search(r"^Range:\s*(.+)$", body, flags=re.MULTILINE)
        source_match = re.search(r"Source:\n<<<\n(.*?)\n>>>", body, flags=re.DOTALL)
        translation_match = re.search(r"Translation:\n<<<\n(.*?)\n>>>", body, flags=re.DOTALL)
        note_match = re.search(r"Note:\n<<<\n(.*?)\n>>>", body, flags=re.DOTALL)
        if not (range_match and source_match and translation_match):
            raise ValueError(f"Invalid translation entry: {title}")
        entries.append(
            Entry(
                title=title,
                range_label=range_match.group(1).strip(),
                source=source_match.group(1).strip(),
                translation=translation_match.group(1).strip(),
                note=note_match.group(1).strip() if note_match else "",
            )
        )
    return entries


def render_text(text: str) -> str:
    paragraphs = []
    for para in re.split(r"\n\s*\n", text.strip()):
        lines = [html.escape(line.strip()) for line in para.splitlines() if line.strip()]
        paragraphs.append("<p>" + "<br>\n".join(lines) + "</p>")
    return "\n".join(paragraphs)


def render(entries: list[Entry]) -> str:
    pairs = []
    for entry in entries:
        source_start, source_end = parse_range(entry.range_label)
        note_html = ""
        if entry.note:
            note_html = f"\n      <p class=\"translation-note\">{html.escape(entry.note)}</p>"
        pairs.append(
            f"""    <section class="parallel-pair" id="{html.escape(source_start)}" data-source-start="{html.escape(source_start)}" data-source-end="{html.escape(source_end)}">
      <h2>{html.escape(entry.title)}</h2>
      <div class="source-text">
        <span class="line-range">{html.escape(entry.range_label)}</span>
{render_text(entry.source)}
      </div>
      <div class="translation-text">
        <span class="line-range">白話譯文</span>
{render_text(entry.translation)}
      </div>{note_html}
    </section>"""
        )
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>瑜伽師地論卷第三十三白話對照</title>
  <link rel="stylesheet" href="../style.css">
</head>
<body>
  <header class="site-header">
    <a href="../index.html">Index</a>
    <div class="kicker">CBETA T1579 / Juan 33</div>
    <h1>瑜伽師地論卷第三十三白話對照</h1>
    <p>底本範圍：T30n1579_p0465a23 至 T30n1579_p0470c05。正文不納入卷三十四。</p>
    <p>譯例：核心術語採白話詞（玄奘詞）雙軌，疑難處以精簡校註標示。</p>
    <nav class="translation-tools">
      <a href="../../translations/T1579-033-baihua.md">來源稿</a>
      <a href="https://cbdata.dila.edu.tw/stable/juans?work=T1579&amp;juan=33&amp;toc=1&amp;work_info=1">CBETA API</a>
    </nav>
  </header>
  <main class="reader parallel-text">
{chr(10).join(pairs)}
  </main>
</body>
</html>
"""


def parse_range(range_label: str) -> tuple[str, str]:
    match = re.fullmatch(r"(T30n1579_p[0-9abc]+)(?:-(?:T30n1579_)?p?([0-9abc]+))?", range_label)
    if not match:
        raise ValueError(f"Invalid range: {range_label}")
    start = match.group(1)
    end = f"T30n1579_p{match.group(2)}" if match.group(2) else start
    return start, end


def main() -> int:
    entries = parse_entries(SOURCE.read_text(encoding="utf-8"))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(render(entries), encoding="utf-8")
    print(f"Wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
