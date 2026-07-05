#!/usr/bin/env python3
"""Download and split CBETA HTML for Yogacarabhumi Sravakabhumi."""

from __future__ import annotations

import html
import json
import re
import ssl
import sys
import time
import urllib.request
from urllib.error import URLError
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


WORK = "T1579"
FILE_ID = "T30n1579"
TARGET_TITLE = "\u8072\u805e\u5730"
BASE_URL = "https://cbdata.dila.edu.tw/stable"

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
HTML_DIR = ROOT / "html"
SECTION_DIR = HTML_DIR / "sections"


@dataclass
class TocNode:
    title: str
    juan: int
    lb: str
    level: int
    children: list["TocNode"] = field(default_factory=list)
    parent_titles: list[str] = field(default_factory=list)

    @property
    def line_id(self) -> str:
        return f"{FILE_ID}_p{self.lb}"

    @property
    def sort_key(self) -> tuple[int, int, int, int]:
        return line_key(self.lb)


def fetch_json(url: str, path: Path) -> dict:
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            payload = response.read()
    except URLError as error:
        reason = getattr(error, "reason", None)
        if not isinstance(reason, ssl.SSLCertVerificationError):
            raise
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(url, timeout=60, context=context) as response:
            payload = response.read()
    path.write_bytes(payload)
    return json.loads(payload.decode("utf-8"))


def line_key(lb: str) -> tuple[int, int, int, int]:
    match = re.fullmatch(r"(\d{4})([abc])(\d{2})", lb)
    if not match:
        raise ValueError(f"Unsupported line label: {lb}")
    page, column, line = match.groups()
    return int(page), {"a": 0, "b": 1, "c": 2}[column], int(line), 0


def find_target(nodes: list[dict]) -> dict | None:
    for node in nodes:
        if TARGET_TITLE in node.get("title", ""):
            return node
        found = find_target(node.get("children", []) or [])
        if found:
            return found
    return None


def convert_node(raw: dict, level: int = 0, parents: list[str] | None = None) -> TocNode:
    parent_titles = list(parents or [])
    node = TocNode(
        title=raw["title"],
        juan=int(raw["juan"]),
        lb=raw["lb"],
        level=level,
        parent_titles=parent_titles,
    )
    node.children = [
        convert_node(child, level + 1, parent_titles + [node.title])
        for child in raw.get("children", []) or []
    ]
    return node


def flatten(nodes: Iterable[TocNode]) -> list[TocNode]:
    out: list[TocNode] = []
    for node in nodes:
        out.append(node)
        out.extend(flatten(node.children))
    return out


def node_descendant_count(node: TocNode) -> int:
    return sum(1 + node_descendant_count(child) for child in node.children)


def slugify(index: int, node: TocNode) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|()\s]+", "-", node.title).strip("-")
    return f"{index:03d}-{cleaned}.html"


def split_html(raw_html: str) -> tuple[str, dict[str, str], str]:
    body_start = raw_html.find("<div id='body'")
    if body_start < 0:
        body_start = raw_html.find('<div id="body"')
    if body_start < 0:
        raise RuntimeError("Could not locate CBETA body div")

    footnote_start_candidates = [
        pos
        for pos in [
            raw_html.find("<div class='footnote'"),
            raw_html.find('<div class="footnote"'),
            raw_html.find("<span class='footnote"),
            raw_html.find('<span class="footnote'),
            raw_html.find("<div id='cbeta-copyright'"),
            raw_html.find('<div id="cbeta-copyright"'),
        ]
        if pos >= 0
    ]
    main_end = min(footnote_start_candidates) if footnote_start_candidates else len(raw_html)
    main_html = raw_html[body_start:main_end]

    footnotes: dict[str, str] = {}
    for match in re.finditer(
        r"<(?P<tag>div|span)\s+class=['\"]footnote(?: add)?['\"]\s+id=['\"](?P<id>[^'\"]+)['\"][^>]*>.*?</(?P=tag)>",
        raw_html,
        flags=re.DOTALL,
    ):
        footnotes[match.group("id")] = match.group(0)

    copyright_start = raw_html.find("<div id='cbeta-copyright'")
    if copyright_start < 0:
        copyright_start = raw_html.find('<div id="cbeta-copyright"')
    copyright_html = raw_html[copyright_start:] if copyright_start >= 0 else ""
    return main_html, footnotes, copyright_html


def line_positions(main_html: str) -> dict[str, int]:
    return {
        match.group(1): match.start()
        for match in re.finditer(r"id=['\"](" + re.escape(FILE_ID) + r"_p\d{4}[abc]\d{2})['\"]", main_html)
    }


def extract_segment(
    juan_html: dict[int, str],
    juan_footnotes: dict[int, dict[str, str]],
    node: TocNode,
    end_node: TocNode | None,
) -> tuple[str, list[str]]:
    fragments: list[str] = []
    referenced_notes: list[str] = []
    start_key = node.sort_key
    end_key = end_node.sort_key if end_node else None

    for juan in sorted(juan_html):
        main_html = juan_html[juan]
        positions = line_positions(main_html)
        line_ids = sorted(positions, key=lambda item: line_key(item.removeprefix(f"{FILE_ID}_p")))
        if not line_ids:
            continue

        first_key = line_key(line_ids[0].removeprefix(f"{FILE_ID}_p"))
        last_key = line_key(line_ids[-1].removeprefix(f"{FILE_ID}_p"))
        if last_key < start_key:
            continue
        if end_key is not None and first_key >= end_key:
            continue

        start_idx = 0
        if start_key > first_key:
            start_idx = positions.get(node.line_id, -1)
            if start_idx < 0:
                continue

        end_idx = len(main_html)
        if end_key is not None:
            end_line_id = f"{FILE_ID}_p{end_node.lb}"
            if end_line_id in positions:
                end_idx = positions[end_line_id]

        if start_idx < end_idx:
            fragment = main_html[start_idx:end_idx].strip()
            fragments.append(fragment)
            referenced_notes.extend(
                re.findall(r"href=['\"]#([^'\"]+)['\"]", fragment)
            )

    unique_notes: list[str] = []
    seen: set[str] = set()
    for note_id in referenced_notes:
        if note_id in seen:
            continue
        seen.add(note_id)
        for notes in juan_footnotes.values():
            if note_id in notes:
                unique_notes.append(notes[note_id])
                break

    return "\n".join(fragments), unique_notes


def render_section(
    node: TocNode,
    filename: str,
    fragment: str,
    notes: list[str],
) -> str:
    title = html.escape(node.title)
    breadcrumb = " / ".join(html.escape(item) for item in node.parent_titles + [node.title])
    notes_html = "\n".join(notes) if notes else "<p class='empty-note'>No notes in this section.</p>"
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} - 瑜伽師地論聲聞地</title>
  <link rel="stylesheet" href="../style.css">
</head>
<body>
  <header class="site-header">
    <a href="../index.html">Index</a>
    <div class="kicker">CBETA T1579</div>
    <h1>{title}</h1>
    <p>{breadcrumb}</p>
    <p>Start: {html.escape(node.line_id)} / Juan {node.juan}</p>
  </header>
  <main class="reader">
{fragment}
  </main>
  <aside class="notes" id="notes">
    <h2>Notes</h2>
{notes_html}
  </aside>
</body>
</html>
"""


def render_index(entries: list[tuple[TocNode, str]]) -> str:
    links = []
    for node, filename in entries:
        indent = max(node.level - 1, 0)
        links.append(
            f"<li class='level-{indent}'><a href='sections/{html.escape(filename)}'>"
            f"{html.escape(node.title)}</a><span>{html.escape(node.line_id)}</span></li>"
        )
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>瑜伽師地論聲聞地</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <header class="site-header">
    <div class="kicker">CBETA T1579</div>
    <h1>瑜伽師地論聲聞地</h1>
    <p>Split from CBETA HTML by mulu nodes. Notes, line markers, and source anchors are preserved.</p>
    <p><a href="translations/T1579-033-baihua.html">卷第三十三白話左右對照翻譯</a></p>
    <p><a href="docs/translation-workflow.html">白話翻譯工作流程與術語庫</a></p>
  </header>
  <main class="index-list">
    <ol>
      {'\n      '.join(links)}
    </ol>
  </main>
</body>
</html>
"""


def write_style() -> None:
    (HTML_DIR / "style.css").write_text(
        """body {
  margin: 0;
  color: #1f2933;
  background: #f7f3ea;
  font-family: "Noto Serif CJK TC", "Songti TC", "PMingLiU", serif;
  line-height: 1.9;
}

a {
  color: #8a3f21;
}

.site-header {
  padding: 28px clamp(18px, 5vw, 56px);
  border-bottom: 1px solid #ddd1bd;
  background: #fffaf0;
}

.site-header h1 {
  margin: 4px 0 8px;
  font-size: 2rem;
  letter-spacing: 0;
}

.site-header p {
  margin: 4px 0;
  color: #5a5145;
}

.kicker {
  color: #805b2f;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.85rem;
}

.reader,
.notes,
.index-list {
  max-width: 980px;
  margin: 0 auto;
  padding: 28px clamp(18px, 5vw, 56px);
  background: #fffdf7;
}

.translation-tools {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  margin-top: 1rem;
}

.translation-tools a {
  border: 1px solid #cdbb9f;
  padding: 0.25rem 0.65rem;
  background: #fffdf7;
  text-decoration: none;
}

.parallel-text {
  max-width: 1280px;
}

.parallel-pair {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: clamp(1rem, 3vw, 2rem);
  padding: 1.2rem 0;
  border-bottom: 1px solid #eee2cc;
}

.parallel-pair h2 {
  grid-column: 1 / -1;
  margin: 0 0 0.25rem;
  font-size: 1.15rem;
}

.source-text,
.translation-text {
  min-width: 0;
}

.source-text {
  color: #4f463c;
}

.translation-text {
  font-size: 1.05rem;
}

.line-range {
  display: block;
  margin-bottom: 0.35rem;
  color: #8b8173;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.78rem;
}

.translation-note {
  grid-column: 1 / -1;
  margin: 0.2rem 0 0;
  padding-left: 0.75rem;
  border-left: 3px solid #d2b48c;
  color: #5f564a;
  font-size: 0.94rem;
}

.doc-page h2 {
  margin-top: 2rem;
  padding-bottom: 0.25rem;
  border-bottom: 1px solid #eee2cc;
  font-size: 1.35rem;
}

.doc-page h3 {
  margin: 0 0 0.25rem;
  font-size: 1rem;
}

.doc-table {
  width: 100%;
  border-collapse: collapse;
  margin: 1rem 0;
  font-size: 0.96rem;
}

.doc-table th,
.doc-table td {
  border-bottom: 1px solid #eee2cc;
  padding: 0.45rem 0.55rem;
  text-align: left;
  vertical-align: top;
}

.doc-table th {
  color: #5a5145;
  background: #fff6e5;
}

.doc-code {
  overflow-x: auto;
  padding: 0.9rem;
  border: 1px solid #e6d8c1;
  background: #fff8ea;
  line-height: 1.55;
}

.workflow-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 0.85rem;
  margin: 1rem 0;
}

.workflow-grid article {
  padding: 0.85rem;
  border: 1px solid #eee2cc;
  background: #fffaf0;
}

.workflow-grid p {
  margin: 0.25rem 0 0;
}

.doc-steps li,
.doc-page li {
  margin: 0.35rem 0;
}

.reader p {
  margin: 0.65rem 0;
}

.lb {
  display: inline-block;
  margin-right: 0.45rem;
  color: #9a8a73;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.72rem;
}

.noteAnchor::after {
  content: "＊";
  color: #9b2c2c;
  font-size: 0.78em;
  vertical-align: super;
}

.notes {
  border-top: 1px solid #ddd1bd;
  font-size: 0.95rem;
}

.footnote {
  display: block;
  margin: 0.35rem 0;
  padding-left: 0.75rem;
  border-left: 3px solid #d2b48c;
}

.empty-note {
  color: #766c5d;
}

.lg {
  margin: 0.8rem 0;
}

.lg-row {
  display: flex;
  gap: 2rem;
  flex-wrap: wrap;
}

.lg-cell {
  min-width: 12rem;
}

.index-list ol {
  list-style: none;
  padding: 0;
  margin: 0;
}

.index-list li {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 1rem;
  padding: 0.4rem 0;
  border-bottom: 1px solid #eee2cc;
}

.index-list span {
  color: #8b8173;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.8rem;
}

.level-1 { padding-left: 1.25rem !important; }
.level-2 { padding-left: 2.5rem !important; }
.level-3 { padding-left: 3.75rem !important; }
.level-4 { padding-left: 5rem !important; }
.level-5 { padding-left: 6.25rem !important; }

@media (max-width: 640px) {
  .index-list li {
    grid-template-columns: 1fr;
    gap: 0.1rem;
  }

  .parallel-pair {
    grid-template-columns: 1fr;
  }
}
""",
        encoding="utf-8",
    )


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SECTION_DIR.mkdir(parents=True, exist_ok=True)

    toc_url = f"{BASE_URL}/works/toc?work={WORK}"
    toc_data = fetch_json(toc_url, DATA_DIR / f"{WORK}-toc.json")
    target_raw = find_target(toc_data["results"][0]["mulu"])
    if target_raw is None:
        raise RuntimeError(f"Could not find target title: {TARGET_TITLE}")
    target = convert_node(target_raw)
    nodes = flatten([target])

    juans = sorted({node.juan for node in nodes})
    juan_html: dict[int, str] = {}
    juan_footnotes: dict[int, dict[str, str]] = {}

    for juan in juans:
        url = f"{BASE_URL}/juans?work={WORK}&juan={juan}&toc=1&work_info=1"
        data = fetch_json(url, DATA_DIR / f"{WORK}-{juan:03d}.json")
        main_html, footnotes, _copyright = split_html(data["results"][0])
        juan_html[juan] = main_html
        juan_footnotes[juan] = footnotes
        time.sleep(0.05)

    entries: list[tuple[TocNode, str]] = []
    for index, node in enumerate(nodes, start=1):
        subtree_size = node_descendant_count(node)
        next_index = index - 1 + subtree_size + 1
        end_node = nodes[next_index] if next_index < len(nodes) else None
        filename = slugify(index, node)
        fragment, notes = extract_segment(juan_html, juan_footnotes, node, end_node)
        if not fragment:
            print(f"Empty fragment: {node.title}", file=sys.stderr)
            return 1
        (SECTION_DIR / filename).write_text(
            render_section(node, filename, fragment, notes),
            encoding="utf-8",
        )
        entries.append((node, filename))

    write_style()
    (HTML_DIR / "index.html").write_text(render_index(entries), encoding="utf-8")
    print(f"Wrote {len(entries)} section files to {SECTION_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
