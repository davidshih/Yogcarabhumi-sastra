#!/usr/bin/env bash
# Locked acceptance checks for 20260929-progress-page. Written before implementation; do not edit after hand-off.
set -uo pipefail
cd "$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
fail=0
check() {  # check <name> <command...>: passes when the command exits 0
  local name=$1 out; shift
  if out=$("$@" 2>&1); then echo "PASS  $name"; else echo "FAIL  $name"; tail -n 12 <<<"$out"; fail=1; fi
}
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# --- Fixture: publish_status() on a temp docs/jobs tree must write a correct status.html next to STATUS_JSON.
cat > "$tmp/fixture.py" <<'PY'
import hashlib, json, re, sys, tempfile
from pathlib import Path
from unittest import mock

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT / "scripts"))
import runner  # noqa: E402

real_page = ROOT / "docs" / "status.html"
digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
before = digest(real_page)

base = Path(tempfile.mkdtemp())
docs, jobs = base / "docs", base / "jobs"
archive = jobs / "archive"
for d in (docs / "T0001" / "translations", docs / "T0002" / "translations", archive, base / "locks"):
    d.mkdir(parents=True)
(docs / "T0001" / "index.html").write_text("x", encoding="utf-8")
(docs / "T0003" / "translations").mkdir(parents=True)
for rel in ("T0001/translations/T0001-001-baihua.html",
            "T0001/translations/T0001-002-baihua.html",
            "T0002/translations/T0002-003-baihua.html",
            "T0003/translations/T0003-001-baihua.html"):
    (docs / rel).write_text("x", encoding="utf-8")
works = base / "works.json"
works.write_text(json.dumps({"works": [
    {"id": "T0001", "file_id": "T00n0001", "title": "甲論", "subtitle": "某菩薩造", "juans": 4},
    {"id": "T0002", "file_id": "T00n0002", "title": "乙論", "subtitle": ""},
    {"id": "T0003", "file_id": "T00n0003", "title": "丙論", "subtitle": "某譯", "juans": 2},
]}, ensure_ascii=False), encoding="utf-8")

def job(name, work, juans, state, progress, where=jobs):
    (where / f"{name}.json").write_text(json.dumps(
        {"id": name, "work": work, "juans": juans, "model": "dual", "state": state, "progress": progress},
        ensure_ascii=False), encoding="utf-8")

job("j1", "T0001", [2, 3], "running", {"2": {"step": "segment"}, "3": {"step": "<b>x</b>"}})
job("j2", "T0001", [4], "cancelled", {"4": {"step": "translate"}})
job("j3", "T0001", [1], "running", {"1": {"step": "done"}})
job("j4", "T0002", [1], "queued", {})
job("j5", "T0001", [4], "running", {"4": {"step": "segment"}}, where=archive)
job("j6", "T0002", [2], "failed", {"2": {"step": "merge"}})
job("j7", "T9999", [1], "queued", {})
job("j8", "T0003", [1, 2], "running", {"1": {"step": "commit"},
                                       "2": {"step": "translate", "tasks": {"translate": {"state": "failed"}}}})
(jobs / "broken.json").write_text("{not json", encoding="utf-8")

with mock.patch.object(runner, "JOBS_DIR", jobs), \
     mock.patch.object(runner, "ARCHIVED_JOBS_DIR", archive), \
     mock.patch.object(runner, "LOCKS_DIR", base / "locks"), \
     mock.patch.object(runner, "STATUS_JSON", docs / "status.json"), \
     mock.patch.object(runner, "WORKS_PATH", works):
    runner.publish_status()

errors = []
def expect(cond, msg):
    if not cond:
        errors.append(msg)

page = docs / "status.html"
expect(page.exists(), "publish_status() did not write status.html next to STATUS_JSON")
expect(digest(real_page) == before, "real docs/status.html changed while STATUS_JSON pointed at a temp dir")
status = json.loads((docs / "status.json").read_text(encoding="utf-8"))
expect(len(status.get("jobs", [])) == 7, f"status.json must still list the 7 readable jobs, got {len(status.get('jobs', []))}")
if not page.exists():
    print("\n".join(errors))
    sys.exit(1)
html = page.read_text(encoding="utf-8")

sections = dict(re.findall(r'<section class="progress-work" id="work-(T\d+)"(.*?)</section>', html, re.S))
expect(set(sections) == {"T0001", "T0002", "T0003"}, f"sections: {sorted(sections)} (T9999 has no works.json entry and must be ignored)")

def cells(work):
    return {int(j): (state, body) for state, j, body in re.findall(
        r'<li class="juan (is-done|is-active|is-todo)" data-juan="(\d+)"[^>]*>(.*?)</li>', sections.get(work, ""), re.S)}

def counts(work):
    return dict(re.findall(r'data-(total|done|active|todo)="(\d+)"', sections.get(work, "").split(">", 1)[0]))

def states(work):
    return {j: s for j, (s, _) in cells(work).items()}

def body(work, juan):
    return cells(work).get(juan, ("", ""))[1]

expect(states("T0001") == {1: "is-done", 2: "is-active", 3: "is-active", 4: "is-todo"},
       f"T0001 states: {states('T0001')} (step 'done' is not active; cancelled and archived jobs are ignored)")
expect('href="T0001/translations/T0001-001-baihua.html"' in body("T0001", 1), "done juan links to its translation page")
expect('href="T0001/translations/T0001-002-baihua.html"' in body("T0001", 2), "active juan with an existing page still links to it")
expect("<a" not in body("T0001", 3), "active juan without a page is not a link")
expect("<a" not in body("T0001", 4), "todo juan is not a link")
expect(counts("T0001") == {"total": "4", "done": "1", "active": "2", "todo": "1"}, f"T0001 counts: {counts('T0001')}")

expect(states("T0002") == {1: "is-active", 2: "is-todo", 3: "is-done"},
       f"T0002 states: {states('T0002')} (no juans key: grid runs to the highest seen juan; failed job ignored)")
expect(counts("T0002") == {"total": "3", "done": "1", "active": "1", "todo": "1"}, f"T0002 counts: {counts('T0002')}")

expect(states("T0003") == {1: "is-done", 2: "is-todo"},
       f"T0003 states: {states('T0003')} (step 'commit' with a page is done; a juan with a failed task is not active)")
expect(counts("T0003") == {"total": "2", "done": "1", "active": "0", "todo": "1"}, f"T0003 counts: {counts('T0003')}")
expect('class="progress-stamp"' not in sections.get("T0003", ""), "no stamp when nothing is in progress")
stamp = sections.get("T0001", "").split('class="progress-stamp"', 1)
expect(len(stamp) == 2 and "<b>2</b>" in stamp[1][:200], "T0001 stamp shows its 2 active juans")

expect('<a href="T0001/index.html">甲論</a>' in sections.get("T0001", ""), "title links to the work index when it exists")
h2 = re.search(r"<h2>(.*?)</h2>", sections.get("T0002", ""), re.S)
expect(h2 is not None and "<a" not in h2.group(1), "title is plain text when the work has no index page")

expect("<b>x</b>" not in html and "&lt;b&gt;x&lt;/b&gt;" in html, "job step text is HTML-escaped")
tabs = dict(re.findall(r'<label for="filter-(all|done|active|todo)">[^<]*<b>(\d+)</b></label>', html))
expect(tabs == {"all": "9", "done": "3", "active": "3", "todo": "3"}, f"tab counts: {tabs}")
expect(len(re.findall(r'<input class="progress-filter" type="radio" name="progress-filter" id="filter-(?:all|done|active|todo)"', html)) == 4,
       "four filter radios")
expect(re.search(r'<input class="progress-filter"[^>]*id="filter-all"[^>]*checked', html) is not None, "全部 is the default filter")
expect('<a href="status.html" aria-current="page">翻譯進度</a>' in html, "rail marks 翻譯進度 as the current page")
expect('fetch("status.json")' not in html, "old job-list script is gone")

print("\n".join(errors) if errors else "ok")
sys.exit(1 if errors else 0)
PY

# --- Real repo: committed docs/status.html matches works.json and the translation pages on disk.
cat > "$tmp/real.py" <<'PY'
import json, re, sys
from pathlib import Path

html = Path("docs/status.html").read_text(encoding="utf-8")
works = json.loads(Path("works.json").read_text(encoding="utf-8"))["works"]
errors = []
for w in works:
    wid = w["id"]
    m = re.search(rf'<section class="progress-work" id="work-{wid}"(.*?)</section>', html, re.S)
    if not m:
        errors.append(f"{wid}: section missing")
        continue
    sec = m.group(1)
    head = sec.split(">", 1)[0]
    total = re.search(r'data-total="(\d+)"', head)
    pages = Path("docs", wid, "translations")
    on_disk = {int(p.name.split("-")[1]) for p in pages.glob(f"{wid}-*-baihua.html")} if pages.exists() else set()
    done = {int(j) for j in re.findall(r'<li class="juan is-done" data-juan="(\d+)"', sec)}
    active = {int(j) for j in re.findall(r'<li class="juan is-active" data-juan="(\d+)"', sec)}
    cells = re.findall(r'<li class="juan is-(?:done|active|todo)" data-juan="(\d+)"', sec)
    if total is None or int(total.group(1)) != w.get("juans"):
        errors.append(f"{wid}: data-total {total and total.group(1)} != works.json juans {w.get('juans')}")
    if len(cells) != w.get("juans"):
        errors.append(f"{wid}: {len(cells)} cells, expected {w.get('juans')}")
    if not done <= on_disk or (done | (active & on_disk)) != on_disk:
        errors.append(f"{wid}: done cells {sorted(done)} do not match pages on disk {sorted(on_disk)}")
print("\n".join(errors) if errors else "ok")
sys.exit(1 if errors else 0)
PY

# --- Lifecycle: the page staged by git_commit_push() already shows the juan being committed as done.
cat > "$tmp/lifecycle.py" <<'PY'
import json, re, subprocess, sys, tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path.cwd() / "scripts"))
import runner  # noqa: E402

base = Path(tempfile.mkdtemp())
docs, jobs, locks = base / "docs", base / "jobs", base / "locks"
(docs / "T0001" / "translations").mkdir(parents=True)
jobs.mkdir()
(docs / "T0001" / "translations" / "T0001-001-baihua.html").write_text("x", encoding="utf-8")
works = base / "works.json"
works.write_text('{"works":[{"id":"T0001","title":"甲論","subtitle":"","juans":2}]}', encoding="utf-8")
job = {"id": "life", "work": "T0001", "juans": [1, 2], "model": "dual", "state": "running", "push": True,
       "progress": {"1": {"step": "commit", "tasks": {"commit": {"state": "running"}}},
                    "2": {"step": "translate", "tasks": {"translate": {"state": "running"}}}}}
staged = {}

def fake_sh(cmd, *args, **kwargs):
    if cmd[:2] == ["git", "add"]:
        page = docs / "status.html"
        staged["page"] = page.read_text(encoding="utf-8") if page.exists() else None
    code = 1 if cmd[:4] == ["git", "diff", "--cached", "--quiet"] else 0
    return subprocess.CompletedProcess(cmd, code, "", "")

with mock.patch.object(runner, "JOBS_DIR", jobs), \
     mock.patch.object(runner, "ARCHIVED_JOBS_DIR", jobs / "archive"), \
     mock.patch.object(runner, "LOCKS_DIR", locks), \
     mock.patch.object(runner, "STATUS_JSON", docs / "status.json"), \
     mock.patch.object(runner, "WORKS_PATH", works), \
     mock.patch.object(runner, "sh", side_effect=fake_sh):
    runner.save_job(job)
    runner.git_commit_push(job, 1)

page = staged.get("page")
if page is None:
    sys.exit("no status.html existed when git add ran")
got = dict((int(j), s) for s, j in re.findall(r'<li class="juan (is-done|is-active|is-todo)" data-juan="(\d+)"', page))
sys.exit(0 if got == {1: "is-done", 2: "is-active"} else f"staged page states: {got} (want juan 1 done, juan 2 active)")
PY

check "publish_status writes a correct progress page (fixture)" python3 "$tmp/fixture.py"
check "staged page shows the committed juan as done"            python3 "$tmp/lifecycle.py"
check "committed docs/status.html matches the repo"            python3 "$tmp/real.py"
check "works.json has juan totals"                              python3 -c '
import json, sys
w = {x["id"]: x.get("juans") for x in json.load(open("works.json", encoding="utf-8"))["works"]}
want = {"T1579": 100, "T1585": 10, "T1558": 30, "T1821": 30}
sys.exit(0 if w == want else f"juans: {w}")'
check "test: cell states and counts"      python3 -m unittest discover -s tests -p test_status_page.py -k test_cell_states_and_counts
check "test: missing juans key fallback"  python3 -m unittest discover -s tests -p test_status_page.py -k test_work_without_juans_uses_highest_seen_juan
check "full unittest suite"               python3 -m unittest discover -s tests -q
check "all relative links resolve"        python3 scripts/check_html_links.py
check "progress CSS has both dark rules"  bash -c 'grep -q "^:root\[data-theme=\"dark\"\] \.progress-page" docs/style.css && grep -q ":root:not(\[data-theme\]) \.progress-page" docs/style.css'
check "CSS filter hides non-matching cells" bash -c 'for s in done active todo; do grep -qF "#filter-$s:checked ~ .progress-list .juan:not(.is-$s)" docs/style.css || { echo "missing hide rule for $s"; exit 1; }; done'
check "CSS marks the selected tab chip"    bash -c 'for s in all done active todo; do grep -qF "#filter-$s:checked ~ .progress-tabs label[for=\"filter-$s\"]" docs/style.css || { echo "missing selected rule for $s"; exit 1; }; done'
check "README documents status.html"      grep -q "docs/status.html" README.md
exit $fail
