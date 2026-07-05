#!/usr/bin/env python3
"""Local web UI: submit translation jobs, watch progress, preview docs/.

  python3 scripts/webapp.py          # http://127.0.0.1:8787
"""

from __future__ import annotations

import json
import re
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import llm
import runner

FORM_PAGE = """<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>翻譯工作台</title>
  <link rel="stylesheet" href="/style.css">
  <script src="/theme.js"></script>
  <style>
    .job-form {{ max-width: 40rem; display: grid; gap: 1rem; }}
    .job-form label {{ display: grid; gap: .3rem; font-weight: 600; }}
    .job-form input[type=text] {{ padding: .5rem; font: inherit; }}
    .job-form .models {{ display: flex; gap: 1.2rem; font-weight: 400; }}
    .job-form button {{ padding: .6rem 1.4rem; font: inherit; cursor: pointer; }}
    #jobs table {{ border-collapse: collapse; width: 100%; }}
    #jobs td, #jobs th {{ padding: .4rem .6rem; text-align: left; border-bottom: 1px solid rgba(128,128,128,.3); }}
    .msg {{ color: #2a7; font-weight: 600; }}
  </style>
</head>
<body>
  <div class="topbar">
    <a class="topbar-brand" href="/">翻譯工作台</a>
    <a class="topbar-link" href="/index.html">閱讀站</a>
    <button class="theme-toggle" type="button" aria-label="切換深色或淺色模式"></button>
  </div>
  <header class="site-header">
    <div class="kicker">Pipeline</div>
    <h1>送出翻譯工作</h1>
    <p class="msg">{message}</p>
  </header>
  <main class="index-list">
    <form class="job-form" method="post" action="/jobs">
      <label>CBETA 連結或經號
        <input type="text" name="link" placeholder="https://cbetaonline.dila.edu.tw/zh/T1579_013" required>
      </label>
      <label>卷號範圍（可留空，用連結內卷號）
        <input type="text" name="juans" placeholder="13-15 或 13,15">
      </label>
      <label>模型
        <span class="models">
          <label><input type="radio" name="model" value="claude" checked> claude</label>
          <label><input type="radio" name="model" value="codex"> codex</label>
          <label><input type="radio" name="model" value="glm"> glm</label>
        </span>
      </label>
      <button type="submit">排入佇列</button>
    </form>
    <h2>工作佇列</h2>
    <div id="jobs">載入中…</div>
  </main>
  <script>
    const zh = {{queued: "排隊中", running: "進行中", done: "完成", failed: "失敗", waiting_limit: "等待額度重置"}};
    async function refresh() {{
      const data = await fetch("/api/status").then(r => r.json()).catch(() => null);
      if (!data) return;
      const rows = data.jobs.map(j => {{
        const prog = Object.entries(j.progress || {{}}).map(([k, v]) =>
          `卷${{k}}:${{v.error ? "錯誤" : v.step}}${{v.sections_total ? `（${{v.section || 0}}/${{v.sections_total}}）` : ""}}`).join("、");
        const resume = j.resume_at ? `，${{j.resume_at.slice(11, 16)}} 續跑` : "";
        return `<tr><td>${{j.id}}</td><td>${{j.work}} 卷 ${{j.juans.join(",")}}</td><td>${{j.model}}</td>` +
               `<td>${{zh[j.state] || j.state}}${{resume}}</td><td>${{prog}}</td></tr>`;
      }});
      document.getElementById("jobs").innerHTML = rows.length
        ? `<table><tr><th>Job</th><th>範圍</th><th>模型</th><th>狀態</th><th>進度</th></tr>${{rows.join("")}}</table>`
        : "目前沒有工作。";
    }}
    refresh();
    setInterval(refresh, 5000);
  </script>
</body>
</html>
"""


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "docs"), **kwargs)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            message = "已排入佇列。" if "queued=1" in self.path else ""
            self._send_html(FORM_PAGE.format(message=message))
        elif self.path == "/api/status":
            jobs = []
            for path in sorted(runner.JOBS_DIR.glob("*.json")):
                try:
                    job = runner.load_job(path)
                except (json.JSONDecodeError, OSError):
                    continue
                job.pop("pid", None)
                jobs.append(job)
            body = json.dumps({"jobs": jobs}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            super().do_GET()  # static preview of docs/

    def do_POST(self):
        if self.path != "/jobs":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(length).decode("utf-8"))
        link = form.get("link", [""])[0].strip()
        juans_spec = form.get("juans", [""])[0].strip()
        model = form.get("model", [""])[0]
        try:
            if model not in llm.MODELS + llm.FAKE_MODELS:
                raise ValueError(f"unknown model: {model}")
            if re.fullmatch(r"[A-Z]+\d+[a-z]?", link):
                work, link_juan = link, None
            else:
                work, link_juan = runner.parse_link(link)
            juans = runner.parse_juans(juans_spec) if juans_spec else ([link_juan] if link_juan else None)
            if work != "T1579":
                raise ValueError("目前僅支援 T1579（腳本尚未參數化）")
            if not juans:
                raise ValueError("請給卷號範圍，或用帶卷號的連結")
        except ValueError as err:
            self._send_html(FORM_PAGE.format(message=f"錯誤：{err}"), status=400)
            return
        from datetime import datetime
        import uuid
        job = {
            "id": f"{datetime.now():%Y%m%d-%H%M%S}-{model}-{uuid.uuid4().hex[:4]}",
            "work": work, "juans": juans, "model": model,
            "state": "queued", "created": runner.now_iso(), "updated": runner.now_iso(),
            "pid": None, "resume_at": None, "error": None,
            "push": True, "summary": False, "progress": {},
        }
        runner.JOBS_DIR.mkdir(exist_ok=True)
        runner.save_job(job)
        self.send_response(303)
        self.send_header("Location", "/?queued=1")
        self.end_headers()

    def _send_html(self, page: str, status: int = 200) -> None:
        body = page.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # quiet static-file noise
        if "/api/" not in (args[0] if args else ""):
            return


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 8787), Handler)
    print("webapp on http://127.0.0.1:8787 (form + /api/status + docs/ preview)")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
