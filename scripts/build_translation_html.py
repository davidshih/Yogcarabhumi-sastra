#!/usr/bin/env python3
"""Render vernacular translation pages."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import html
import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import publisher


ROOT = Path(__file__).resolve().parents[1]
# Repo paths that the renderer reads are fixed at import, so a page does not depend on a later change of ROOT.
WORKS_PATH = ROOT / "works.json"
GLOSSARY_DIR = ROOT / "translations" / "glossary"
DOCS_DIR = ROOT / "docs"
GLOSSARY_PATH = GLOSSARY_DIR / "T1579-terms.json"
VERSIONS_DIR = ROOT / "translations" / "versions"
DEFAULT_SOURCE = ROOT / "translations" / "T1579-033-baihua.md"
VERSION_N_RE = re.compile(r"\.v(\d+)\.md$")
DEFAULT_OUTPUT_DIR = ROOT / "docs" / "T1579" / "translations"
REPO_BLOB = "https://github.com/davidshih/Yogcarabhumi-sastra/blob/main"  # Pages only serves docs/; repo files link out
TRANSLATION_NAME_RE = re.compile(r"(?P<work>[A-Za-z][A-Za-z0-9._-]*)-(?P<juan>\d{3})-baihua")
TRANSLATION_BLOCK_RE = re.compile(r"Translation:\n<<<\n(.*?)\n>>>", re.DOTALL)
PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")
# Paragraph id marker: one line `<!-- #<id> -->` inside a Translation paragraph (hidden on GitHub).
PARA_MARKER_RE = re.compile(r"<!--\s*#([A-Za-z0-9_-]{1,64})\s*-->")
PARA_MARKER_LINE_RE = re.compile(r"^[^\S\n]*<!--\s*#[A-Za-z0-9_-]{1,64}\s*-->[^\S\n]*(?:\n|$)", re.MULTILINE)
# Fixed element ids of the reader page; a paragraph id must never take one of them.
PAGE_UI_IDS = frozenset({"readProgress", "siteRail", "tocSide", "parallelText", "readerEnd", "prevSection",
                         "nextSection", "continueTray", "continueMessage", "nextVolume", "stayHere"})
# "legacy": the tooltips of the published site. "policy": the tooltips of the audited pipeline (scripts/audited.py).
TERM_STYLES = ("legacy", "policy")
# A Range label: a full CBETA line id, then optionally `-` and the end as a full id of the same file,
# `p<label>` or `<label>`.
RANGE_LABEL_RE = re.compile(
    r"(?P<start>(?P<prefix>[A-Z]+\d+n\d+[A-Za-z]?)_p\d{4}[abc]\d{2})"
    r"(?:-(?:(?P=prefix)_p|p)?(?P<end_label>\d{4}[abc]\d{2}))?"
)


@dataclass
class Entry:
    title: str
    range_label: str
    source: str
    translation: str
    note: str
    paragraph_ids: tuple[str | None, ...] = ()


@dataclass(frozen=True)
class TermTip:
    word: str
    term_id: str
    xuanzang: str
    plain: str
    tip: str
    alternate_ids: tuple[str, ...] = ()


def marked_paragraphs(block: str) -> list[tuple[str | None, list[str]]]:
    """Split a Translation block the way render_text does, as (id, content lines) per paragraph.

    The first marker in a paragraph names it (a merge keeps the first id). A paragraph that holds only
    a marker passes its id to the next paragraph without one. An empty block is one empty paragraph,
    because render_text renders it as `<p></p>`.
    """
    paragraphs: list[tuple[str | None, list[str]]] = []
    carry = None
    for raw in PARAGRAPH_SPLIT_RE.split(block.strip()):
        lines = raw.split("\n")
        ids = [m.group(1) for line in lines if (m := PARA_MARKER_RE.fullmatch(line.strip()))]
        content = [line for line in lines if not PARA_MARKER_RE.fullmatch(line.strip())]
        if not any(line.strip() for line in content):
            carry = carry or (ids[0] if ids else None)
            continue
        paragraphs.append((ids[0] if ids else carry, content))
        carry = None
    return paragraphs or [(carry, [])]


def format_block(paragraphs: list[tuple[str | None, list[str]]]) -> str:
    return "\n\n".join("\n".join(([f"<!-- #{pid} -->"] if pid else []) + content)
                       for pid, content in paragraphs)


def new_paragraph_id(section_start: str, content: list[str], taken: set[str]) -> str:
    """`<section start>-<6 hex>`: hash of the paragraph text, salted until it is free on the page."""
    seed = section_start + "\n" + "\n".join(line.strip() for line in content).strip()
    salt = 0
    while True:
        digest = hashlib.sha1(f"{seed}\n{salt}".encode() if salt else seed.encode()).hexdigest()
        pid = f"{section_start}-{digest[:6]}"
        if len(pid) > 64:
            raise ValueError(f"Paragraph id longer than 64 characters: {pid}")
        if pid not in taken:
            return pid
        salt += 1


def assign_paragraph_ids(text: str) -> str:
    """Give every Translation paragraph an id marker. Existing ids stay; only missing ones are added.

    An id is assigned once and then lives in the markdown, so edits and rebuilds never move it.
    """
    heads = list(re.finditer(r"^##\s+", text, flags=re.MULTILINE))
    blocks = []  # (span of the block contents, section start, paragraphs)
    for i, head in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        chunk = text[head.start():end]
        range_match = re.search(r"^Range:\s*(.+)$", chunk, flags=re.MULTILINE)
        block = TRANSLATION_BLOCK_RE.search(chunk)
        if not (range_match and block):
            continue  # parse_entries reports malformed entries
        span = (head.start() + block.start(1), head.start() + block.end(1))
        blocks.append((span, parse_range(range_match.group(1).strip())[0], marked_paragraphs(block.group(1))))
    taken = set(PAGE_UI_IDS) | {start for _span, start, _paras in blocks}
    for _span, _start, paragraphs in blocks:  # keep the first use of each valid id; later copies get a new one
        for k, (pid, content) in enumerate(paragraphs):
            if pid in taken:
                paragraphs[k] = (None, content)
            elif pid:
                taken.add(pid)
    out, pos = [], 0
    for (s, e), start, paragraphs in blocks:
        for k, (pid, content) in enumerate(paragraphs):
            if pid is None:
                pid = new_paragraph_id(start, content, taken)
                taken.add(pid)
                paragraphs[k] = (pid, content)
        out.append(text[pos:s] + format_block(paragraphs))
        pos = e
    return "".join(out) + text[pos:]


def carry_paragraph_ids(old_block: str, new_text: str) -> str:
    """Put the old block's ids on a rewritten translation (LLM output has no markers).

    Paragraphs are aligned with difflib: an unchanged paragraph keeps its id, and a replaced run pairs
    up in order, so an edited or split paragraph keeps its id on the first part. Inserted paragraphs
    stay unmarked and get a new id at the next build_page.
    """
    old = marked_paragraphs(old_block)
    if not any(pid for pid, _content in old):
        return new_text.strip()
    new = marked_paragraphs(new_text)
    matcher = difflib.SequenceMatcher(None, ["\n".join(c).strip() for _pid, c in old],
                                      ["\n".join(c).strip() for _pid, c in new], autojunk=False)
    used = {pid for pid, _content in new if pid}
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag not in ("equal", "replace"):
            continue
        for k in range(min(i2 - i1, j2 - j1)):
            old_id, (pid, content) = old[i1 + k][0], new[j1 + k]
            if pid is None and old_id and old_id not in used:
                new[j1 + k] = (old_id, content)
                used.add(old_id)
    return format_block(new)


def parse_entries(text: str) -> list[Entry]:
    entries: list[Entry] = []
    parts = re.split(r"^##\s+", text, flags=re.MULTILINE)
    for part in parts[1:]:
        lines = part.splitlines()
        title = lines[0].strip()
        body = "\n".join(lines[1:])
        range_match = re.search(r"^Range:\s*(.+)$", body, flags=re.MULTILINE)
        source_match = re.search(r"Source:\n<<<\n(.*?)\n>>>", body, flags=re.DOTALL)
        translation_match = TRANSLATION_BLOCK_RE.search(body)
        note_match = re.search(r"Note:\n<<<\n(.*?)\n>>>", body, flags=re.DOTALL)
        if not (range_match and source_match and translation_match):
            raise ValueError(f"Invalid translation entry: {title}")
        entries.append(
            Entry(
                title=title,
                range_label=range_match.group(1).strip(),
                source=source_match.group(1).strip(),
                translation=PARA_MARKER_LINE_RE.sub("", translation_match.group(1)).strip(),
                note=note_match.group(1).strip() if note_match else "",
                paragraph_ids=tuple(pid for pid, _content in marked_paragraphs(translation_match.group(1))),
            )
        )
    return entries


def render_text(text: str, ids: tuple[str | None, ...] | None = None) -> str:
    paras = PARAGRAPH_SPLIT_RE.split(text.strip())
    if ids is not None and (len(ids) != len(paras) or not all(ids)):
        raise ValueError(f"Translation paragraphs without ids (run build_page): {text[:60]!r}")
    paragraphs = []
    for i, para in enumerate(paras):
        lines = [html.escape(line.strip()) for line in para.splitlines() if line.strip()]
        attr = f' id="{html.escape(ids[i])}"' if ids is not None else ""
        paragraphs.append(f"<p{attr}>" + "<br>\n".join(lines) + "</p>")
    return "\n".join(paragraphs)


@lru_cache(maxsize=None)
def legacy_term_tips(work: str) -> tuple[tuple[str, str], ...]:
    """Glossary words -> hover-tip text, longest word first (avoids partial shadowing)."""
    path = GLOSSARY_DIR / f"{work}-terms.json"
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


def _tip_lines(term: dict[str, Any]) -> list[str]:
    lines = [f"{term['xuanzang']}｜{term['plain']}"]
    if term.get("sanskrit"):
        lines.append("梵 " + " / ".join(term["sanskrit"]))
    if term.get("english"):
        lines.append("英 " + " / ".join(term["english"]))
    return lines


def build_term_tips(glossary: dict[str, Any]) -> tuple[TermTip, ...]:
    """Build validated tooltip aliases with deterministic primary ownership."""
    terms = glossary.get("terms")
    if not isinstance(terms, list):
        raise ValueError("Glossary must contain a terms list")

    by_id: dict[str, dict[str, Any]] = {}
    owners: dict[str, list[dict[str, Any]]] = {}
    for term in terms:
        if not isinstance(term, dict):
            raise ValueError("Each glossary term must be an object")
        term_id = term.get("id")
        xuanzang = term.get("xuanzang")
        plain = term.get("plain")
        if not all(isinstance(value, str) and value for value in (term_id, xuanzang, plain)):
            raise ValueError("Tooltip terms require non-empty id, xuanzang, and plain strings")
        if term_id in by_id:
            raise ValueError(f"Duplicate tooltip term id: {term_id}")
        by_id[term_id] = term
        for word in {xuanzang, plain}:
            if len(word) >= 2:
                owners.setdefault(word, []).append(term)

    tips: list[TermTip] = []
    for word, candidates in owners.items():
        canonical = [term for term in candidates if term["xuanzang"] == word]
        if len(canonical) == 1:
            primary = canonical[0]
        elif len(candidates) == 1:
            primary = candidates[0]
        else:
            candidate_ids = ", ".join(sorted(term["id"] for term in candidates))
            raise ValueError(f"Ambiguous tooltip alias {word!r}: {candidate_ids}")

        alternates = sorted(
            (term for term in candidates if term["id"] != primary["id"]),
            key=lambda term: term["id"],
        )
        lines = _tip_lines(primary)
        lines.extend(
            f"其他義項 {term['xuanzang']}｜{term['plain']} [{term['id']}]"
            for term in alternates
        )
        tips.append(
            TermTip(
                word=word,
                term_id=primary["id"],
                xuanzang=primary["xuanzang"],
                plain=primary["plain"],
                tip="\n".join(lines),
                alternate_ids=tuple(term["id"] for term in alternates),
            )
        )

    tips.sort(key=lambda tip: (-len(tip.word), tip.word, tip.term_id))
    issues = validate_term_tips(tuple(tips))
    if issues:
        raise ValueError("; ".join(issues))
    return tuple(tips)


def validate_term_tips(tips: tuple[TermTip, ...]) -> list[str]:
    """Validate unique aliases and longest-match ordering for overlapping words."""
    issues: list[str] = []
    seen_words: set[str] = set()
    positions = {tip.word: index for index, tip in enumerate(tips)}
    for tip in tips:
        if tip.word in seen_words:
            issues.append(f"Duplicate tooltip alias: {tip.word}")
        seen_words.add(tip.word)
    for shorter in tips:
        for longer in tips:
            if shorter.word != longer.word and shorter.word in longer.word:
                if positions[longer.word] > positions[shorter.word]:
                    issues.append(
                        f"Overlapping tooltip alias {longer.word!r} must precede {shorter.word!r}"
                    )
    return issues


def term_tips(glossary_path: Path = GLOSSARY_PATH) -> tuple[TermTip, ...]:
    """Load fresh glossary data for each render to avoid stale process state."""
    glossary = json.loads(glossary_path.read_text(encoding="utf-8"))
    return build_term_tips(glossary)


def works_by_id() -> dict[str, dict]:
    try:
        works = json.loads(WORKS_PATH.read_text(encoding="utf-8"))["works"]
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


def legacy_wrap_terms(rendered_html: str, work: str) -> str:
    """Wrap glossary terms in already-escaped HTML with tooltip spans (text nodes only)."""
    tips = legacy_term_tips(work)
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


def wrap_terms(
    rendered_html: str,
    *,
    tips: tuple[TermTip, ...] | None = None,
    occurrence_prefix: str = "term",
    first_terms: set[str] | None = None,
) -> str:
    """Wrap glossary terms in already-escaped HTML with tooltip spans (text nodes only)."""
    if tips is None:
        tips = term_tips()
    if not tips:
        return rendered_html
    tipmap = {tip.word: tip for tip in tips}
    pattern = re.compile("|".join(re.escape(tip.word) for tip in tips))
    occurrence_counts: dict[str, int] = {}
    seen_first = first_terms if first_terms is not None else set()
    out = []

    def replacement(match: re.Match[str]) -> str:
        tip = tipmap[match.group(0)]
        occurrence_counts[tip.term_id] = occurrence_counts.get(tip.term_id, 0) + 1
        occurrence_id = f"{occurrence_prefix}-{tip.term_id}-{occurrence_counts[tip.term_id]}"
        visible_text = match.group(0)
        attributes = [
            'class="term"',
            'tabindex="0"',
            f'data-term-id="{html.escape(tip.term_id, quote=True)}"',
            f'data-term-occurrence="{html.escape(occurrence_id, quote=True)}"',
            f'data-tip="{html.escape(tip.tip, quote=True)}"',
        ]
        if tip.alternate_ids:
            attributes.append(
                f'data-term-alternates="{html.escape(" ".join(tip.alternate_ids), quote=True)}"'
            )
        if tip.term_id not in seen_first:
            attributes.append('data-first-in-juan="true"')
            seen_first.add(tip.term_id)
            visible_text = f"{html.escape(tip.xuanzang)}〔{html.escape(tip.plain)}〕"
        return f"<span {' '.join(attributes)}>{visible_text}</span>"

    for part in re.split(r"(<[^>]+>)", rendered_html):
        if part.startswith("<"):
            out.append(part)
        else:
            out.append(pattern.sub(replacement, part))
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
    return DEFAULT_OUTPUT_DIR.parents[1] / work / "translations" / f"{source.stem}.html"


def infer_juan(source: Path) -> int:
    return infer_work_juan(source)[1]


def infer_title(text: str, work: str, juan: int) -> str:
    first_line = text.splitlines()[0].lstrip("#").strip()
    if "白話" in first_line:
        return first_line.replace("來源稿", "").replace("白話對照", "白話對照").strip()
    return f"{work_title(work)}卷第{juan}白話對照"


def _complete_paragraph_ids(entry: Entry) -> tuple[str | None, ...] | None:
    """The ids of an entry when every paragraph of its translation has one, else None."""
    ids = entry.paragraph_ids
    if ids and all(ids) and len(ids) == len(PARAGRAPH_SPLIT_RE.split(entry.translation.strip())):
        return ids
    return None


def render(entries: list[Entry], source: Path, work: str, juan: int, title: str, *,
           term_style: str = "legacy") -> str:
    """Render one translation page.

    term_style "legacy" is the published site: tooltips on the translation only, and every paragraph
    must carry its id. "policy" is the audited pipeline: translation and source are both wrapped, a
    span names its term and its occurrence, the first occurrence in the juan shows `玄奘詞〔白話〕`,
    and an entry gets paragraph ids only when all of its paragraphs have one.
    """
    if term_style not in TERM_STYLES:
        raise ValueError(f"Unknown term style: {term_style}")
    policy = term_style == "policy"
    pairs = []
    toc_items = []
    tips = term_tips(GLOSSARY_DIR / f"{work}-terms.json") if policy else ()
    first_terms: set[str] = set()
    for entry in entries:
        source_start, source_end = parse_range(entry.range_label)
        if policy:
            occurrence_base = f"{work}-{juan:03d}-{source_start}"
            translation_html = wrap_terms(
                render_text(entry.translation, _complete_paragraph_ids(entry)), tips=tips,
                occurrence_prefix=occurrence_base + "-translation", first_terms=first_terms)
            source_html = wrap_terms(
                render_text(entry.source), tips=tips,
                occurrence_prefix=occurrence_base + "-source", first_terms=first_terms)
        else:
            translation_html = legacy_wrap_terms(render_text(entry.translation, entry.paragraph_ids), work)
            source_html = render_text(entry.source)
        note_html = ""
        if entry.note:
            note_html = f"\n      <p class=\"translation-note\">{html.escape(entry.note)}</p>"
        toc_items.append(f"<li><a href='#{html.escape(source_start)}'>{html.escape(entry.title)}</a></li>")
        pairs.append(
            f"""    <section class="parallel-pair" id="{html.escape(source_start)}" data-source-start="{html.escape(source_start)}" data-source-end="{html.escape(source_end)}">
      <h2>{html.escape(entry.title)}</h2>
      <div class="translation-text">
        <span class="line-range">白話譯文</span>
{translation_html}
      </div>
      <div class="source-text" aria-label="文言原文">
        <span class="line-range">文言原文 / {html.escape(entry.range_label)}</span>
{source_html}
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
    glossary_path = GLOSSARY_DIR / f"{work}-terms.json"
    glossary_link = (f'<a href="{REPO_BLOB}/translations/glossary/{work}-terms.json">術語庫</a>'
                     if glossary_path.exists() else "")
    workflow_path = DOCS_DIR / work / "docs" / "translation-workflow.html"
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
    """`T30n1579_p0684c24-p0685a24` -> (`T30n1579_p0684c24`, `T30n1579_p0685a24`); anything else is an error."""
    match = RANGE_LABEL_RE.fullmatch(range_label)
    if not match:
        raise ValueError(f"Invalid range: {range_label}")
    start = match.group("start")
    end = f"{match.group('prefix')}_p{match.group('end_label')}" if match.group("end_label") else start
    return start, end


def build_page(source: Path, output: Path | None = None) -> Path:
    """Render one translation md (and any archived versions of it) to HTML.

    New translation paragraphs get their id here, written back to the md once.
    """
    for path in [source, *archived_versions(source)]:
        text = path.read_text(encoding="utf-8")
        marked = assign_paragraph_ids(text)
        if marked != text:
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(marked, encoding="utf-8")
            os.replace(tmp, path)
            text = marked
        entries = parse_entries(text)
        if not entries:
            raise ValueError(f"No entries found in {path}")
        work, juan = infer_work_juan(path)
        out = translation_output_path(path, output if path == source else None)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render(entries, path, work, juan, infer_title(text, work, juan)), encoding="utf-8")
        print(f"Wrote {out}")
    return translation_output_path(source, output)


def render_translation_bytes(source: Path, *, term_style: str = "policy") -> bytes:
    text = source.read_text(encoding="utf-8")
    entries = parse_entries(text)
    if not entries:
        raise ValueError(f"No entries found in {source}")
    work, juan = infer_work_juan(source)
    title = infer_title(text, work, juan)
    return render(entries, source, work, juan, title, term_style=term_style).encode("utf-8")


def _sealed_item_for_path(sealed: dict, path: Path) -> dict:
    resolved = path.resolve()
    for item in sealed.get("files", {}).values():
        if (ROOT / item["path"]).resolve() == resolved:
            return item
    raise ValueError(f"attestation does not seal {path}")


def build_translation(
    source: Path,
    output: Path | None = None,
    *,
    attestation: Path | None = None,
    allow_unsealed: bool = False,
    term_style: str = "policy",
) -> Path:
    """Write one page without touching the md. A write under docs/ needs a volume attestation.

    The production runner does not come here: it calls build_page.
    """
    destination = output or translation_output_path(source, None)
    docs_root = (ROOT / "docs").resolve()
    if destination.resolve().is_relative_to(docs_root) and not (attestation or allow_unsealed):
        raise PermissionError("writes under docs/ require an attestation or allow_unsealed=True")

    sealed = None
    expected_output = None
    if attestation:
        sealed = publisher.verify_attestation(ROOT, attestation)
        _sealed_item_for_path(sealed, source)
        expected_output = _sealed_item_for_path(sealed, destination)

    candidate = render_translation_bytes(source, term_style=term_style)
    if expected_output and hashlib.sha256(candidate).hexdigest() != expected_output["sha256"]:
        raise ValueError("rendered candidate does not match the attested output SHA-256")

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(candidate)
    if sealed is not None:
        publisher.verify_attestation(ROOT, attestation)
    return destination


def build_all(
    source_dir: Path = ROOT / "translations",
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    allow_unsealed: bool = False,
) -> list[Path]:
    """Rebuild every T1579 translation page in the policy term style."""
    outputs = []
    for source in sorted(source_dir.glob("T1579-*-baihua.md")):
        outputs.append(build_translation(
            source,
            output_dir / f"{source.stem}.html",
            allow_unsealed=allow_unsealed,
        ))
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--translation", type=Path)
    group.add_argument("--all", action="store_true", help="Rebuild every T1579 translation page")
    outputs = parser.add_mutually_exclusive_group()
    outputs.add_argument("--output", type=Path)
    outputs.add_argument("--diagnostic-output", type=Path,
                         help="Write an unsealed diagnostic outside docs/")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--attestation", type=Path,
                        help="Volume attestation required for writes under docs/")
    parser.add_argument("--legacy-rebuild", action="store_true",
                        help="Explicitly allow the legacy all-pages rebuild")
    parser.add_argument("--term-style", choices=TERM_STYLES, default="policy",
                        help="Tooltip style of one page: policy (audited pipeline) or legacy (published site)")
    args = parser.parse_args()

    if args.all:
        if not args.legacy_rebuild:
            parser.error("--all requires --legacy-rebuild")
        if args.output:
            parser.error("--output cannot be used with --all; use --output-dir")
        if args.term_style != "policy":
            parser.error("--all renders the policy term style only")
        output_dir = args.output_dir
        if not output_dir.is_absolute():
            output_dir = ROOT / output_dir
        for output in build_all(output_dir=output_dir, allow_unsealed=True):
            print(f"Wrote {output}")
        return 0

    source = args.translation or DEFAULT_SOURCE
    if not source.is_absolute():
        source = ROOT / source
    if args.diagnostic_output and args.attestation:
        parser.error("--diagnostic-output cannot be combined with --attestation")
    output = args.diagnostic_output or translation_output_path(source, args.output)
    if not output.is_absolute():
        output = ROOT / output
    docs_root = (ROOT / "docs").resolve()
    attestation = None
    if args.diagnostic_output:
        if output.resolve().is_relative_to(docs_root):
            parser.error("--diagnostic-output must be outside docs/")
    else:
        if not args.attestation:
            parser.error("writes under docs/ require --attestation")
        attestation = args.attestation if args.attestation.is_absolute() else ROOT / args.attestation
    try:
        build_translation(source, output, attestation=attestation, term_style=args.term_style)
    except (PermissionError, ValueError, publisher.PublishError) as error:
        parser.error(str(error))
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
