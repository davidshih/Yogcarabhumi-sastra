#!/usr/bin/env python3
"""Render vernacular translation pages."""

from __future__ import annotations

import argparse
import html
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GLOSSARY_PATH = ROOT / "translations" / "glossary" / "T1579-terms.json"
DEFAULT_SOURCE = ROOT / "translations" / "T1579-033-baihua.md"
DEFAULT_OUTPUT_DIR = ROOT / "docs" / "T1579" / "translations"
REPO_BLOB = "https://github.com/davidshih/Yogcarabhumi-sastra/blob/main"  # Pages only serves docs/; repo files link out


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


@lru_cache(maxsize=1)
def term_tips() -> tuple[tuple[str, str], ...]:
    """Glossary words -> hover-tip text, longest word first (avoids partial shadowing)."""
    try:
        glossary = json.loads(GLOSSARY_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return ()
    tips: dict[str, str] = {}
    for term in glossary.get("terms", []):
        lines = [f"{term['xuanzang']}｜{term['plain']}"]
        if term.get("sanskrit"):
            lines.append("梵 " + " / ".join(term["sanskrit"]))
        if term.get("english"):
            lines.append("英 " + " / ".join(term["english"]))
        tip = "\n".join(lines)
        for word in {term["xuanzang"], term["plain"]}:
            if word and len(word) >= 2:
                tips.setdefault(word, tip)
    return tuple(sorted(tips.items(), key=lambda kv: -len(kv[0])))


def wrap_terms(rendered_html: str) -> str:
    """Wrap glossary terms in already-escaped HTML with tooltip spans (text nodes only)."""
    tips = term_tips()
    if not tips:
        return rendered_html
    tipmap = dict(tips)
    pattern = re.compile("|".join(re.escape(word) for word, _ in tips))
    out = []
    for part in re.split(r"(<[^>]+>)", rendered_html):
        if part.startswith("<"):
            out.append(part)
        else:
            out.append(pattern.sub(
                lambda m: f'<span class="term" tabindex="0" data-tip="{html.escape(tipmap[m.group(0)])}">{m.group(0)}</span>',
                part))
    return "".join(out)


def juan_neighbors(source: Path, juan: int) -> tuple[int | None, int | None]:
    juans = sorted(
        int(m.group(1))
        for p in source.parent.glob("T1579-*-baihua.md")
        if (m := re.search(r"T1579-(\d{3})-baihua", p.name))
    )
    prev = max((j for j in juans if j < juan), default=None)
    nxt = min((j for j in juans if j > juan), default=None)
    return prev, nxt


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
    toc_items = []
    for entry in entries:
        source_start, source_end = parse_range(entry.range_label)
        note_html = ""
        if entry.note:
            note_html = f"\n      <p class=\"translation-note\">{html.escape(entry.note)}</p>"
        toc_items.append(f"<li><a href='#{html.escape(source_start)}'>{html.escape(entry.title)}</a></li>")
        pairs.append(
            f"""    <section class="parallel-pair" id="{html.escape(source_start)}" data-source-start="{html.escape(source_start)}" data-source-end="{html.escape(source_end)}">
      <h2>{html.escape(entry.title)}</h2>
      <div class="translation-text">
        <span class="line-range">白話譯文</span>
{wrap_terms(render_text(entry.translation))}
      </div>
      <div class="source-text" aria-label="文言原文">
        <span class="line-range">文言原文 / {html.escape(entry.range_label)}</span>
{render_text(entry.source)}
      </div>{note_html}
    </section>"""
        )
    first_start, _ = parse_range(entries[0].range_label)
    _, last_end = parse_range(entries[-1].range_label)
    source_link = html.escape(f"{REPO_BLOB}/translations/{source.name}")
    prev_juan, next_juan = juan_neighbors(source, juan)
    prev_link = (f"<a class='juan-nav-link' href='T1579-{prev_juan:03d}-baihua.html'>← 卷第{prev_juan}</a>"
                 if prev_juan else "<span></span>")
    next_link = (f"<a class='juan-nav-link' href='T1579-{next_juan:03d}-baihua.html'>卷第{next_juan} →</a>"
                 if next_juan else "<span></span>")
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <link rel="stylesheet" href="../../style.css">
  <script src="../../theme.js"></script>
</head>
<body class="source-collapsed">
  <div class="read-progress" id="readProgress"></div>
  <div class="topbar">
    <a class="topbar-brand" href="../index.html">聲聞地</a>
    <a class="topbar-link" href="../../index.html">總目錄</a>
    <button class="theme-toggle" type="button" aria-label="切換深色或淺色模式"></button>
  </div>
  <header class="site-header">
    <div class="kicker">CBETA T1579 / Juan {juan}</div>
    <h1>{html.escape(title)}</h1>
    <p>底本範圍：{html.escape(first_start)} 至 {html.escape(last_end)}。</p>
    <p>譯例：核心術語採白話詞（玄奘詞）雙軌，疑難處以精簡校註標示；術語下有虛線者可懸停或點按看註解。</p>
    <nav class="translation-tools">
      <div class="mode-switch" role="group" aria-label="閱讀模式">
        <button type="button" data-mode="trans" aria-pressed="true">白話</button>
        <button type="button" data-mode="both" aria-pressed="false">對照</button>
        <button type="button" data-mode="source" aria-pressed="false">原文</button>
      </div>
      <a href="{source_link}">來源稿</a>
      <a href="{REPO_BLOB}/translations/glossary/T1579-terms.json">術語庫</a>
      <a href="../docs/translation-workflow.html">翻譯流程</a>
      <a href="https://cbdata.dila.edu.tw/stable/juans?work=T1579&amp;juan={juan}&amp;toc=1&amp;work_info=1">CBETA API</a>
    </nav>
    <nav class="juan-nav">{prev_link}{next_link}</nav>
  </header>
  <div class="page-with-toc">
    <details class="toc-side" id="tocSide">
      <summary>本卷目次</summary>
      <ol>
        {"\n        ".join(toc_items)}
      </ol>
    </details>
    <main class="reader parallel-text" id="parallelText">
{chr(10).join(pairs)}
    </main>
  </div>
  <nav class="section-nav" aria-label="段落導航">
    <button type="button" id="prevSection" aria-label="上一段">↑</button>
    <button type="button" id="nextSection" aria-label="下一段">↓</button>
  </nav>
  <script>
    // 閱讀模式：白話（預設）/ 對照 / 原文，記憶於 localStorage
    const MODE_CLASS = {{trans: "source-collapsed", both: "", source: "translation-collapsed"}};
    const buttons = Array.from(document.querySelectorAll(".mode-switch button"));
    function setMode(mode) {{
      document.body.classList.remove("source-collapsed", "translation-collapsed");
      if (MODE_CLASS[mode]) document.body.classList.add(MODE_CLASS[mode]);
      buttons.forEach(b => b.setAttribute("aria-pressed", String(b.dataset.mode === mode)));
      try {{ localStorage.setItem("readmode", mode); }} catch (e) {{}}
    }}
    buttons.forEach(b => b.addEventListener("click", () => setMode(b.dataset.mode)));
    try {{ setMode(localStorage.getItem("readmode") in MODE_CLASS ? localStorage.getItem("readmode") : "trans"); }}
    catch (e) {{ setMode("trans"); }}

    // 閱讀進度條
    const progress = document.getElementById("readProgress");
    addEventListener("scroll", () => {{
      const h = document.documentElement;
      progress.style.width = (h.scrollTop / (h.scrollHeight - h.clientHeight) * 100) + "%";
    }}, {{passive: true}});

    // 上一段／下一段
    const sections = Array.from(document.querySelectorAll(".parallel-pair"));
    function jump(dir) {{
      const y = scrollY + 80;
      const idx = sections.findIndex(s => s.offsetTop > y + 4);
      const current = idx === -1 ? sections.length - 1 : Math.max(idx - 1, 0);
      const target = sections[Math.min(Math.max(current + dir, 0), sections.length - 1)];
      target.scrollIntoView({{behavior: "smooth"}});
    }}
    document.getElementById("prevSection").addEventListener("click", () => jump(-1));
    document.getElementById("nextSection").addEventListener("click", () => jump(1));

    // 寬螢幕自動展開側欄目次
    const toc = document.getElementById("tocSide");
    const wide = matchMedia("(min-width: 1000px)");
    function syncToc() {{ toc.open = wide.matches; }}
    syncToc();
    wide.addEventListener("change", syncToc);
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
