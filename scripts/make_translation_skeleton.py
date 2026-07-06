#!/usr/bin/env python3
"""Create a translation Markdown skeleton from CBETA juan JSON."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LINE_ID_RE = re.compile(r"([A-Za-z0-9._-]+)_p(\d{4}[abc]\d{2})")
CHINESE_DIGITS = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九"]


@dataclass
class Segment:
    number: int
    title: str
    start: str
    end: str
    note: str


class LineTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.lines: dict[str, list[str]] = {}
        self.current_line: str | None = None
        self.current_prefix = ""
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        classes = set((attr.get("class") or "").split())
        if tag in {"a", "span"} and ("lb" in classes or "lineInfo" in classes):
            self.skip_depth += 1
            line_id = attr.get("id")
            if line_id and LINE_ID_RE.fullmatch(line_id):
                self.current_line = line_id
                self.current_prefix = line_id.split("_p", 1)[0]
                self.lines.setdefault(line_id, [])
            return
        if tag == "span" and "t" in classes and attr.get("l"):
            if not self.current_prefix:
                return
            self.current_line = f"{self.current_prefix}_p{attr['l']}"
            self.lines.setdefault(self.current_line, [])
        if tag == "p" and self.current_line:
            self.lines.setdefault(self.current_line, [])

    def handle_endtag(self, tag: str) -> None:
        if self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.skip_depth or not self.current_line:
            return
        text = data.strip()
        if text and not LINE_ID_RE.fullmatch(text):
            self.lines.setdefault(self.current_line, []).append(text)


def line_key(line_id: str) -> tuple[int, int, int]:
    match = LINE_ID_RE.fullmatch(line_id)
    if not match:
        raise ValueError(f"Invalid line id: {line_id}")
    label = match.group(2)
    return int(label[:4]), {"a": 0, "b": 1, "c": 2}[label[4]], int(label[5:])


def parse_range(raw_range: str) -> tuple[str, str]:
    if "-" in raw_range:
        start, end_part = raw_range.split("-", 1)
    else:
        start, end_part = raw_range, ""
    if not LINE_ID_RE.fullmatch(start):
        raise ValueError(f"Invalid range: {raw_range}")
    if not end_part:
        end = start
    elif LINE_ID_RE.fullmatch(end_part):
        end = end_part
    else:
        prefix = start.split("_p", 1)[0]
        suffix = end_part[1:] if end_part.startswith("p") else end_part
        end = f"{prefix}_p{suffix}"
        if not LINE_ID_RE.fullmatch(end):
            raise ValueError(f"Invalid range: {raw_range}")
    return start, end


def chinese_number(number: int) -> str:
    if not 1 <= number <= 100:
        return str(number)
    if number == 100:
        return "一百"
    tens, ones = divmod(number, 10)
    if tens == 0:
        return CHINESE_DIGITS[ones]
    if tens == 1:
        return "十" + (CHINESE_DIGITS[ones] if ones else "")
    return CHINESE_DIGITS[tens] + "十" + (CHINESE_DIGITS[ones] if ones else "")


def extract_lines(data_path: Path) -> dict[str, str]:
    payload = json.loads(data_path.read_text(encoding="utf-8"))
    raw_html = payload["results"][0]
    main_end = len(raw_html)
    for marker in ("<div class='footnote'", '<div class="footnote"', "<div id='cbeta-copyright'", '<div id="cbeta-copyright"'):
        pos = raw_html.find(marker)
        if pos >= 0:
            main_end = min(main_end, pos)
    parser = LineTextParser()
    parser.feed(raw_html[:main_end])
    return {line_id: "".join(parts).strip() for line_id, parts in parser.lines.items()}


def read_segments(path: Path) -> list[Segment]:
    segments: list[Segment] = []
    for index, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("\t")]
        if len(parts) < 2:
            raise ValueError(f"{path}:{index}: expected title, range, and optional note separated by tabs")
        title, raw_range = parts[0], parts[1]
        start, end = parse_range(raw_range)
        note = parts[2] if len(parts) >= 3 else ""
        segments.append(Segment(len(segments) + 1, title, start, end, note))
    return segments


def source_for_segment(lines: dict[str, str], segment: Segment) -> str:
    start_key = line_key(segment.start)
    end_key = line_key(segment.end)
    selected = [
        text
        for line_id, text in sorted(lines.items(), key=lambda item: line_key(item[0]))
        if start_key <= line_key(line_id) <= end_key and text
    ]
    return "\n".join(selected)


def compact_range(start: str, end: str) -> str:
    prefix = start.split("_p", 1)[0]
    if start == end:
        return start
    if end.startswith(prefix + "_p"):
        return f"{start}-p{end.split('_p', 1)[1]}"
    return f"{start}-{end}"


def render(juan: int, start: str, end: str, segments: list[Segment], lines: dict[str, str],
           work: str = "WORK", work_title: str = "經典") -> str:
    chinese_juan = chinese_number(juan)
    out = [
        f"# {work_title}卷第{chinese_juan}白話對照來源稿",
        "",
        f"底本：CBETA {work}《{work_title}》卷第{chinese_juan}。範圍為 `{start}` 至 `{end}`。",
        "",
        "翻譯原則：以玄奘譯語為準，白話詞與玄奘詞雙軌並列；先求正確精準，再求通順。校註只記錄會影響理解或譯法的重點。",
        "",
    ]
    for segment in segments:
        range_label = compact_range(segment.start, segment.end)
        out.extend(
            [
                f"## {segment.number:02d} {segment.title}",
                f"Range: {range_label}",
                "",
                "Source:",
                "<<<",
                source_for_segment(lines, segment),
                ">>>",
                "",
                "Translation:",
                "<<<",
                "",
                ">>>",
                "",
                "Note:",
                "<<<",
                segment.note,
                ">>>",
                "",
            ]
        )
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--juan", type=int, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--segments", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--work", default="WORK")
    parser.add_argument("--work-title", default="經典")
    parser.add_argument("--force", action="store_true", help="overwrite output even if it already exists")
    args = parser.parse_args()

    data = args.data if args.data.is_absolute() else ROOT / args.data
    segment_path = args.segments if args.segments.is_absolute() else ROOT / args.segments
    output = args.output if args.output.is_absolute() else ROOT / args.output

    if output.exists() and not args.force:
        raise SystemExit(f"refusing to overwrite existing {output}; pass --force to replace a finalized draft")

    lines = extract_lines(data)
    segments = read_segments(segment_path)
    output.write_text(render(args.juan, args.start, args.end, segments, lines,
                             work=args.work, work_title=args.work_title), encoding="utf-8")
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
