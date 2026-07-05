#!/usr/bin/env python3
"""Render vernacular translation pages."""

from __future__ import annotations

import argparse
import html
import re
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "translations" / "T1579-033-baihua.md"
DEFAULT_OUTPUT_DIR = ROOT / "html" / "translations"


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


def translation_output_path(source: Path, output: Path | None) -> Path:
    if output:
        return output
    return DEFAULT_OUTPUT_DIR / f"{source.stem}.html"


def infer_juan(source: Path) -> int:
    match = re.search(r"T1579-(\d{3})-baihua", source.name)
    if not match:
        raise ValueError(f"Could not infer juan from filename: {source}")
    return int(match.group(1))


def infer_title(text: str, juan: int) -> str:
    first_line = text.splitlines()[0].lstrip("#").strip()
    if "白話" in first_line:
        return first_line.replace("來源稿", "").replace("白話對照", "白話對照").strip()
    return f"瑜伽師地論卷第{juan}白話對照"


def render(entries: list[Entry], source: Path, juan: int, title: str) -> str:
    pairs = []
    for entry in entries:
        source_start, source_end = parse_range(entry.range_label)
        note_html = ""
        if entry.note:
            note_html = f"\n      <p class=\"translation-note\">{html.escape(entry.note)}</p>"
        pairs.append(
            f"""    <section class="parallel-pair" id="{html.escape(source_start)}" data-source-start="{html.escape(source_start)}" data-source-end="{html.escape(source_end)}">
      <h2>{html.escape(entry.title)}</h2>
      <div class="translation-text">
        <span class="line-range">白話譯文</span>
{render_text(entry.translation)}
      </div>
      <div class="source-text" aria-label="文言原文">
        <span class="line-range">文言原文 / {html.escape(entry.range_label)}</span>
{render_text(entry.source)}
      </div>{note_html}
    </section>"""
        )
    first_start, _ = parse_range(entries[0].range_label)
    _, last_end = parse_range(entries[-1].range_label)
    source_link = html.escape(f"../../translations/{source.name}")
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <link rel="stylesheet" href="../style.css">
</head>
<body class="source-collapsed">
  <header class="site-header">
    <a href="../index.html">Index</a>
    <div class="kicker">CBETA T1579 / Juan {juan}</div>
    <h1>{html.escape(title)}</h1>
    <p>底本範圍：{html.escape(first_start)} 至 {html.escape(last_end)}。</p>
    <p>譯例：核心術語採白話詞（玄奘詞）雙軌，疑難處以精簡校註標示。</p>
    <nav class="translation-tools">
      <a href="{source_link}">來源稿</a>
      <a href="../../translations/glossary/T1579-terms.json">術語庫</a>
      <a href="../docs/translation-workflow.html">翻譯流程</a>
      <a href="https://cbdata.dila.edu.tw/stable/juans?work=T1579&amp;juan={juan}&amp;toc=1&amp;work_info=1">CBETA API</a>
      <button class="source-toggle" type="button" id="sourceToggle" aria-pressed="false" aria-controls="parallelText">顯示文言原文</button>
    </nav>
  </header>
  <main class="reader parallel-text" id="parallelText">
{chr(10).join(pairs)}
  </main>
  <script>
    const sourceToggle = document.getElementById("sourceToggle");
    sourceToggle.addEventListener("click", () => {{
      const collapsed = document.body.classList.toggle("source-collapsed");
      sourceToggle.textContent = collapsed ? "顯示文言原文" : "隱藏文言原文";
      sourceToggle.setAttribute("aria-pressed", collapsed ? "false" : "true");
    }});
  </script>
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--translation", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    source = args.translation
    if not source.is_absolute():
        source = ROOT / source
    output = translation_output_path(source, args.output)
    if not output.is_absolute():
        output = ROOT / output

    text = source.read_text(encoding="utf-8")
    entries = parse_entries(text)
    if not entries:
        raise ValueError(f"No entries found in {source}")
    juan = infer_juan(source)
    title = infer_title(text, juan)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(entries, source, juan, title), encoding="utf-8")
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
