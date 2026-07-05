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
HTML_DIR = ROOT / "docs" / WORK  # GitHub Pages serves docs/; per-work subdir
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
  <link rel="stylesheet" href="../../style.css">
  <script src="../../theme.js"></script>
</head>
<body>
  <div class="topbar">
    <a class="topbar-brand" href="../index.html">聲聞地</a>
    <a class="topbar-link" href="../index.html">Index</a>
    <button class="theme-toggle" type="button" aria-label="切換深色或淺色模式"></button>
  </div>
  <header class="site-header">
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
            f"<li class='level-{indent}' data-juan='{node.juan}'><a href='sections/{html.escape(filename)}'>"
            f"{html.escape(node.title)}</a><span>{html.escape(node.line_id)}</span></li>"
        )
    juans = sorted({node.juan for node, _ in entries})
    tabs = [
        "<button type='button' class='juan-tab' data-juan='all' aria-selected='true'>全部</button>"
    ]
    tabs.extend(
        f"<button type='button' class='juan-tab' data-juan='{juan}' aria-selected='false'>卷{juan}</button>"
        for juan in juans
    )
    juan_tabs = (
        "    <nav class='juan-tabs' aria-label='依卷次瀏覽'>\n      "
        + "\n      ".join(tabs)
        + "\n    </nav>\n"
    )
    translation_links = []
    for path in sorted((ROOT / "translations").glob("T1579-*-baihua.md")):
        match = re.search(r"T1579-(\d{3})-baihua", path.name)
        label = f"卷第{int(match.group(1))}白話對照翻譯" if match else path.stem
        html_name = f"{path.stem}.html"
        translation_links.append(
            f"<li><a href='translations/{html.escape(html_name)}'>{html.escape(label)}</a></li>"
        )
    translation_list = ""
    if translation_links:
        joined_translation_links = "\n        ".join(translation_links)
        translation_list = (
            "    <section class='translation-index'>\n"
            "      <h2>白話對照翻譯</h2>\n"
            "      <ul>\n"
            f"        {joined_translation_links}\n"
            "      </ul>\n"
            "    </section>\n"
        )
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>瑜伽師地論聲聞地</title>
  <link rel="stylesheet" href="../style.css">
  <script src="../theme.js"></script>
</head>
<body>
  <div class="topbar">
    <a class="topbar-brand" href="index.html">聲聞地</a>
    <a class="topbar-link" href="../index.html">總目錄</a>
    <a class="topbar-link" href="docs/translation-workflow.html">翻譯流程</a>
    <button class="theme-toggle" type="button" aria-label="切換深色或淺色模式"></button>
  </div>
  <header class="site-header">
    <div class="kicker">CBETA T1579</div>
    <h1>瑜伽師地論聲聞地</h1>
    <p>Split from CBETA HTML by mulu nodes. Notes, line markers, and source anchors are preserved.</p>
    <p><a href="docs/translation-workflow.html">白話翻譯工作流程與術語庫</a></p>
  </header>
  <main class="index-list">
    <div class="site-search">
      <input type="search" id="siteSearch" placeholder="搜尋經文、譯文、章節標題…" aria-label="全站搜尋">
      <ol id="searchResults" hidden></ol>
    </div>
{translation_list}    <details class="index-disclosure">
      <summary>章節索引</summary>
{juan_tabs}      <ol>
      {'\n      '.join(links)}
      </ol>
    </details>
  </main>
  <script>
    // 全站搜尋：首次輸入才載入 search.json，中文用 substring 比對即正確
    const searchInput = document.getElementById("siteSearch");
    const searchResults = document.getElementById("searchResults");
    let searchIndex = null;
    async function ensureIndex() {{
      if (!searchIndex) searchIndex = await fetch("search.json").then(r => r.json()).catch(() => []);
      return searchIndex;
    }}
    searchInput.addEventListener("input", async () => {{
      const q = searchInput.value.trim();
      if (q.length < 2) {{ searchResults.hidden = true; return; }}
      const hits = (await ensureIndex()).filter(r => r.t.includes(q) || r.x.includes(q)).slice(0, 30);
      searchResults.innerHTML = hits.map(r => {{
        const pos = r.x.indexOf(q);
        const excerpt = pos >= 0 ? r.x.slice(Math.max(0, pos - 20), pos + 40).replace(/\\n/g, " ") : "";
        return `<li class='level-0'><a href='${{r.u}}'>${{r.t}}</a><span>卷${{r.j}}${{excerpt ? "・…" + excerpt + "…" : ""}}</span></li>`;
      }}).join("") || "<li class='level-0'>沒有結果</li>";
      searchResults.hidden = false;
    }});
  </script>
  <script>
    const juanTabs = Array.from(document.querySelectorAll(".juan-tab"));
    const juanItems = Array.from(document.querySelectorAll(".index-list ol > li"));
    juanTabs.forEach((tab) => {{
      tab.addEventListener("click", () => {{
        const selected = tab.dataset.juan;
        juanTabs.forEach((other) => other.setAttribute("aria-selected", String(other === tab)));
        juanItems.forEach((item) => {{
          item.hidden = selected !== "all" && item.dataset.juan !== selected;
        }});
      }});
    }});
  </script>
</body>
</html>
"""


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

    (HTML_DIR / "index.html").write_text(render_index(entries), encoding="utf-8")
    print(f"Wrote {len(entries)} section files to {SECTION_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
