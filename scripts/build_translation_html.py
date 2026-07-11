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
VERSIONS_DIR = ROOT / "translations" / "versions"
DEFAULT_SOURCE = ROOT / "translations" / "T1579-033-baihua.md"
VERSION_N_RE = re.compile(r"\.v(\d+)\.md$")
DEFAULT_OUTPUT_DIR = ROOT / "docs" / "T1579" / "translations"
REPO_BLOB = "https://github.com/davidshih/Yogcarabhumi-sastra/blob/main"  # Pages only serves docs/; repo files link out
TRANSLATION_NAME_RE = re.compile(r"(?P<work>[A-Za-z][A-Za-z0-9._-]*)-(?P<juan>\d{3})-baihua")


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


@lru_cache(maxsize=None)
def term_tips(work: str) -> tuple[tuple[str, str], ...]:
    """Glossary words -> hover-tip text, longest word first (avoids partial shadowing)."""
    path = ROOT / "translations" / "glossary" / f"{work}-terms.json"
    if not path.exists() and work == "T1579":
        path = GLOSSARY_PATH
    try:
        glossary = json.loads(path.read_text(encoding="utf-8"))
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


def works_by_id() -> dict[str, dict]:
    try:
        works = json.loads((ROOT / "works.json").read_text(encoding="utf-8"))["works"]
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return {}
    return {w["id"]: w for w in works if isinstance(w, dict) and "id" in w}


def infer_work_juan(source: Path) -> tuple[str, int]:
    match = TRANSLATION_NAME_RE.search(source.name)
    if not match:
        raise ValueError(f"Could not infer work and juan from filename: {source}")
    return match.group("work"), int(match.group("juan"))


def work_title(work: str) -> str:
    return works_by_id().get(work, {}).get("title", work)


def wrap_terms(rendered_html: str, work: str) -> str:
    """Wrap glossary terms in already-escaped HTML with tooltip spans (text nodes only)."""
    tips = term_tips(work)
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


def archived_versions(source: Path) -> list[Path]:
    base = source.name.split(".v")[0].removesuffix(".md")
    return sorted(VERSIONS_DIR.glob(f"{base}.v*.md"),
                  key=lambda p: int(VERSION_N_RE.search(p.name).group(1)))


def version_select_html(source: Path) -> str:
    base = source.name.split(".v")[0].removesuffix(".md")
    versions = archived_versions(source)
    if not versions:
        return ""
    current = source.stem
    options = [f"<option value='{base}.html'{' selected' if current == base else ''}>最新版</option>"]
    for path in versions:
        n = VERSION_N_RE.search(path.name).group(1)
        selected = " selected" if current == f"{base}.v{n}" else ""
        options.append(f"<option value='{base}.v{n}.html'{selected}>第 {n} 版（封存）</option>")
    return ("<label class='version-pick'>版本 <select class='version-select' "
            "onchange=\"location.href=this.value\">" + "".join(options) + "</select></label>")


def juan_neighbors(source: Path, work: str, juan: int) -> tuple[int | None, int | None]:
    juans = sorted(
        int(m.group(1))
        for p in source.parent.glob(f"{work}-*-baihua.md")
        if (m := re.search(rf"{re.escape(work)}-(\d{{3}})-baihua", p.name))
    )
    prev = max((j for j in juans if j < juan), default=None)
    nxt = min((j for j in juans if j > juan), default=None)
    return prev, nxt


def translation_output_path(source: Path, output: Path | None) -> Path:
    if output:
        return output
    work, _juan = infer_work_juan(source)
    return ROOT / "docs" / work / "translations" / f"{source.stem}.html"


def infer_juan(source: Path) -> int:
    return infer_work_juan(source)[1]


def infer_title(text: str, work: str, juan: int) -> str:
    first_line = text.splitlines()[0].lstrip("#").strip()
    if "白話" in first_line:
        return first_line.replace("來源稿", "").replace("白話對照", "白話對照").strip()
    return f"{work_title(work)}卷第{juan}白話對照"


def render(entries: list[Entry], source: Path, work: str, juan: int, title: str) -> str:
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
{wrap_terms(render_text(entry.translation), work)}
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
    prev_juan, next_juan = juan_neighbors(source, work, juan)
    prev_link = (f"<a class='juan-nav-link' href='{work}-{prev_juan:03d}-baihua.html'>← 卷第{prev_juan}</a>"
                 if prev_juan else "<span></span>")
    next_link = (f"<a class='juan-nav-link' href='{work}-{next_juan:03d}-baihua.html'>卷第{next_juan} →</a>"
                 if next_juan else "<span></span>")
    continue_tray = ""
    if next_juan:
        continue_tray = f"""  <aside class=\"continue-tray\" id=\"continueTray\" aria-live=\"polite\">
    <p id=\"continueMessage\">本卷已讀畢。</p>
    <div class=\"continue-actions\">
      <a id=\"nextVolume\" href=\"{work}-{next_juan:03d}-baihua.html\">前往卷第{next_juan}</a>
      <button type=\"button\" id=\"stayHere\">留在本卷</button>
    </div>
  </aside>"""
    glossary_path = ROOT / "translations" / "glossary" / f"{work}-terms.json"
    glossary_link = (f'<a href="{REPO_BLOB}/translations/glossary/{work}-terms.json">術語庫</a>'
                     if glossary_path.exists() else "")
    workflow_path = ROOT / "docs" / work / "docs" / "translation-workflow.html"
    workflow_link = '<a href="../docs/translation-workflow.html">翻譯流程</a>' if workflow_path.exists() else ""
    version_selector = version_select_html(source)
    rail_workflow_link = workflow_link
    brand = html.escape(work_title(work))
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <link rel="stylesheet" href="../../style.css?v=20260711">
  <script src="../../theme.js?v=20260711"></script>
</head>
<body class="source-collapsed reader-shell">
  <a class="skip-link" href="#parallelText">跳至閱讀內容</a>
  <div class="read-progress" id="readProgress"></div>
  <aside class="site-rail" id="siteRail" aria-label="網站導覽">
    <a class="rail-brand" href="../../index.html"><strong>佛典白話翻譯</strong><span>CBETA 對照閱讀</span></a>
    <nav class="rail-nav" aria-label="主要導覽">
      <span class="rail-label">閱讀</span>
      <a href="../../index.html">總目錄</a>
      <a href="../index.html" aria-current="page">{brand}</a>
{f'      {rail_workflow_link}' if rail_workflow_link else ''}
    </nav>
    <div class="rail-footer">
      <button class="rail-control theme-toggle" type="button" aria-label="切換深色或淺色模式"></button>
    </div>
  </aside>
  <button class="rail-toggle" type="button" aria-expanded="false" aria-controls="siteRail">目錄</button>
  <div class="site-frame">
    <header class="page-header">
      <div class="page-header-inner">
        <div class="kicker">CBETA {html.escape(work)} / Juan {juan}</div>
        <h1>{html.escape(title)}</h1>
        <p>底本範圍：{html.escape(first_start)} 至 {html.escape(last_end)}。</p>
        <p>核心術語保留白話詞與玄奘詞雙軌；可切換白話、對照與原文閱讀。</p>
        <nav class="translation-tools">
      <div class="mode-switch" role="group" aria-label="閱讀模式">
        <button type="button" data-mode="trans" aria-pressed="true">白話</button>
        <button type="button" data-mode="both" aria-pressed="false">對照</button>
        <button type="button" data-mode="source" aria-pressed="false">原文</button>
      </div>
{f'      {version_selector}' if version_selector else ''}
      <a href="{source_link}">來源稿</a>
{f'      {glossary_link}' if glossary_link else ''}
{f'      {workflow_link}' if workflow_link else ''}
      <a href="https://cbdata.dila.edu.tw/stable/juans?work={html.escape(work)}&amp;juan={juan}&amp;toc=1&amp;work_info=1">CBETA API</a>
        </nav>
        <nav class="juan-nav" aria-label="卷次導覽">{prev_link}{next_link}</nav>
      </div>
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
      <div id="readerEnd" tabindex="-1"></div>
    </main>
    </div>
    <nav class="section-nav" aria-label="段落導航">
      <button type="button" id="prevSection" aria-label="上一段">↑</button>
      <button type="button" id="nextSection" aria-label="下一段">↓</button>
    </nav>
  </div>
{continue_tray}
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

    // 到達卷末時提供可取消的自動續讀，不必返回索引頁選下一卷。
    const readerEnd = document.getElementById("readerEnd");
    const continueTray = document.getElementById("continueTray");
    const nextVolume = document.getElementById("nextVolume");
    const stayHere = document.getElementById("stayHere");
    const continueMessage = document.getElementById("continueMessage");
    let continueTimer = null;
    let remaining = 5;
    function cancelContinue() {{
      if (continueTimer) clearInterval(continueTimer);
      continueTimer = null;
      if (continueTray) continueTray.classList.remove("is-visible");
    }}
    function offerContinue() {{
      if (!continueTray || !nextVolume || continueTimer) return;
      remaining = 5;
      continueTray.classList.add("is-visible");
      continueMessage.textContent = `本卷已讀畢，${{remaining}} 秒後前往下一卷。`;
      continueTimer = setInterval(() => {{
        remaining -= 1;
        if (remaining <= 0) {{
          location.href = nextVolume.href;
          return;
        }}
        continueMessage.textContent = `本卷已讀畢，${{remaining}} 秒後前往下一卷。`;
      }}, 1000);
    }}
    if (readerEnd && continueTray && nextVolume) {{
      new IntersectionObserver((entries) => {{
        if (entries.some(entry => entry.isIntersecting)) offerContinue();
      }}, {{threshold: 0.9}}).observe(readerEnd);
      stayHere.addEventListener("click", cancelContinue);
    }}
  </script>
</body>
</html>
"""


def parse_range(range_label: str) -> tuple[str, str]:
    line_re = re.compile(r"^[A-Za-z0-9._-]+_p[0-9a-c]+$", re.IGNORECASE)
    if "-" in range_label:
        start, end_part = range_label.split("-", 1)
    else:
        start, end_part = range_label, ""
    if not line_re.fullmatch(start):
        raise ValueError(f"Invalid range: {range_label}")
    if not end_part:
        end = start
    elif line_re.fullmatch(end_part):
        end = end_part
    else:
        prefix = start.split("_p", 1)[0]
        suffix = end_part[1:] if end_part.startswith("p") else end_part
        end = f"{prefix}_p{suffix}"
        if not line_re.fullmatch(end):
            raise ValueError(f"Invalid range: {range_label}")
    return start, end


def build_page(source: Path, output: Path | None = None) -> Path:
    """Render one translation md (and any archived versions of it) to HTML."""
    for path in [source, *archived_versions(source)]:
        text = path.read_text(encoding="utf-8")
        entries = parse_entries(text)
        if not entries:
            raise ValueError(f"No entries found in {path}")
        work, juan = infer_work_juan(path)
        out = translation_output_path(path, output if path == source else None)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render(entries, path, work, juan, infer_title(text, work, juan)), encoding="utf-8")
        print(f"Wrote {out}")
    return translation_output_path(source, output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--translation", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    source = args.translation
    if not source.is_absolute():
        source = ROOT / source
    output = args.output
    if output and not output.is_absolute():
        output = ROOT / output
    build_page(source, output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
