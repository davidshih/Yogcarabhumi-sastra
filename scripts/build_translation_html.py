#!/usr/bin/env python3
"""Render vernacular translation pages."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from dataclasses import dataclass
from pathlib import Path

import publisher
from typing import Any


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


@dataclass(frozen=True)
class TermTip:
    word: str
    term_id: str
    xuanzang: str
    plain: str
    tip: str
    alternate_ids: tuple[str, ...] = ()


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
    tips = term_tips()
    first_terms: set[str] = set()
    for entry in entries:
        source_start, source_end = parse_range(entry.range_label)
        occurrence_base = f"T1579-{juan:03d}-{source_start}"
        note_html = ""
        if entry.note:
            note_html = f"\n      <p class=\"translation-note\">{html.escape(entry.note)}</p>"
        toc_items.append(f"<li><a href='#{html.escape(source_start)}'>{html.escape(entry.title)}</a></li>")
        pairs.append(
            f"""    <section class="parallel-pair" id="{html.escape(source_start)}" data-source-start="{html.escape(source_start)}" data-source-end="{html.escape(source_end)}">
      <h2>{html.escape(entry.title)}</h2>
      <div class="translation-text">
        <span class="line-range">白話譯文</span>
{wrap_terms(render_text(entry.translation), tips=tips, occurrence_prefix=occurrence_base + "-translation", first_terms=first_terms)}
      </div>
      <div class="source-text" aria-label="文言原文">
        <span class="line-range">文言原文 / {html.escape(entry.range_label)}</span>
{wrap_terms(render_text(entry.source), tips=tips, occurrence_prefix=occurrence_base + "-source", first_terms=first_terms)}
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
    match = re.fullmatch(
        r"(T30n1579_p\d{4}[abc]\d{2})(?:-(?:T30n1579_)?p?(\d{4}[abc]\d{2}))?",
        range_label,
    )
    if not match:
        raise ValueError(f"Invalid range: {range_label}")
    start = match.group(1)
    end = f"T30n1579_p{match.group(2)}" if match.group(2) else start
    return start, end


def render_translation_bytes(source: Path) -> bytes:
    text = source.read_text(encoding="utf-8")
    entries = parse_entries(text)
    if not entries:
        raise ValueError(f"No entries found in {source}")
    juan = infer_juan(source)
    title = infer_title(text, juan)
    return render(entries, source, juan, title).encode("utf-8")


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
) -> Path:
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

    candidate = render_translation_bytes(source)
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
    """Rebuild every translation page after shared glossary or renderer changes."""
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
    args = parser.parse_args()

    if args.all:
        if not args.legacy_rebuild:
            parser.error("--all requires --legacy-rebuild")
        if args.output:
            parser.error("--output cannot be used with --all; use --output-dir")
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
    if args.diagnostic_output:
        if output.resolve().is_relative_to(docs_root):
            parser.error("--diagnostic-output must be outside docs/")
    else:
        if not args.attestation:
            parser.error("writes under docs/ require --attestation")
        attestation = args.attestation if args.attestation.is_absolute() else ROOT / args.attestation
    try:
        build_translation(
            source,
            output,
            attestation=attestation if not args.diagnostic_output else None,
        )
    except (PermissionError, ValueError, publisher.PublishError) as error:
        parser.error(str(error))
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
