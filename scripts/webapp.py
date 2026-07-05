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
from urllib.parse import parse_qs, urlparse

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
    :root {
      --ops-bg: #f8fafc;
      --ops-surface: #ffffff;
      --ops-surface-2: #eef2f7;
      --ops-text: #0f172a;
      --ops-muted: #475569;
      --ops-border: #cbd5e1;
      --ops-accent: #15803d;
      --ops-warn: #b45309;
      --ops-danger: #b91c1c;
      --ops-ring: rgba(21, 128, 61, .24);
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --ops-bg: #020617;
        --ops-surface: #0f172a;
        --ops-surface-2: #1e293b;
        --ops-text: #f8fafc;
        --ops-muted: #cbd5e1;
        --ops-border: #334155;
        --ops-accent: #22c55e;
        --ops-warn: #f59e0b;
        --ops-danger: #f87171;
        --ops-ring: rgba(34, 197, 94, .24);
      }
    }
    body {
      background: var(--ops-bg);
      color: var(--ops-text);
    }
    .ops-shell {
      width: min(1180px, calc(100vw - 32px));
      margin: 0 auto 48px;
      display: grid;
      gap: 16px;
    }
    .ops-header {
      display: grid;
      gap: 8px;
      padding: 24px 0 8px;
    }
    .ops-header h1 {
      margin: 0;
      font-size: clamp(1.75rem, 4vw, 2.6rem);
      letter-spacing: 0;
    }
    .ops-header p {
      margin: 0;
      color: var(--ops-muted);
      max-width: 68ch;
      line-height: 1.55;
    }
    .ops-grid {
      display: grid;
      grid-template-columns: minmax(280px, 380px) 1fr;
      gap: 16px;
      align-items: start;
    }
    .ops-panel, .job-card {
      background: var(--ops-surface);
      border: 1px solid var(--ops-border);
      border-radius: 8px;
      box-shadow: 0 10px 30px rgba(15, 23, 42, .08);
    }
    .ops-panel {
      padding: 16px;
      display: grid;
      gap: 14px;
    }
    .ops-panel h2, .jobs-head h2 {
      margin: 0;
      font-size: 1rem;
    }
    .job-form {
      display: grid;
      gap: 12px;
    }
    .job-form label {
      display: grid;
      gap: 6px;
      font-weight: 650;
      color: var(--ops-text);
    }
    .job-form input[type=text] {
      min-height: 44px;
      border: 1px solid var(--ops-border);
      border-radius: 8px;
      padding: 0 12px;
      font: inherit;
      background: var(--ops-bg);
      color: var(--ops-text);
    }
    .job-form input[type=text]:focus, button:focus-visible {
      outline: 3px solid var(--ops-ring);
      outline-offset: 2px;
    }
    .models {
      display: grid;
      gap: 8px;
      font-weight: 500;
    }
    .models label {
      grid-template-columns: auto 1fr;
      align-items: start;
      gap: 8px;
      min-height: 32px;
      color: var(--ops-muted);
    }
    .primary-btn, .cancel-btn {
      min-height: 44px;
      border: 0;
      border-radius: 8px;
      padding: 0 14px;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
      transition: transform .18s ease, opacity .18s ease, background .18s ease;
    }
    .primary-btn {
      background: var(--ops-accent);
      color: #fff;
    }
    .cancel-btn {
      border: 1px solid rgba(185, 28, 28, .35);
      background: transparent;
      color: var(--ops-danger);
    }
    .primary-btn:hover, .cancel-btn:hover {
      transform: translateY(-1px);
    }
    .primary-btn:disabled, .cancel-btn:disabled {
      opacity: .5;
      cursor: not-allowed;
      transform: none;
    }
    .msg {
      min-height: 1.5rem;
      color: var(--ops-accent);
      font-weight: 700;
    }
    .jobs-panel {
      display: grid;
      gap: 12px;
    }
    .jobs-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    .summary {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      color: var(--ops-muted);
      font-size: .92rem;
    }
    .metric {
      border: 1px solid var(--ops-border);
      border-radius: 999px;
      padding: 4px 9px;
      background: var(--ops-surface);
      font-variant-numeric: tabular-nums;
    }
    .job-list {
      display: grid;
      gap: 12px;
    }
    .job-card {
      padding: 14px;
      display: grid;
      gap: 12px;
    }
    .job-top, .volume-top {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    .job-title {
      display: grid;
      gap: 3px;
      min-width: 0;
    }
    .job-id {
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: .82rem;
      color: var(--ops-muted);
      overflow-wrap: anywhere;
    }
    .status-pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      min-height: 28px;
      border-radius: 999px;
      border: 1px solid var(--ops-border);
      padding: 3px 9px;
      white-space: nowrap;
      font-size: .86rem;
      font-weight: 700;
    }
    .dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--ops-muted);
    }
    .state-running .dot, .state-done .dot { background: var(--ops-accent); }
    .state-waiting_limit .dot, .state-waiting_model .dot, .state-waiting .dot { background: var(--ops-warn); }
    .state-failed .dot, .state-cancelled .dot { background: var(--ops-danger); }
    .volume-list {
      display: grid;
      gap: 10px;
    }
    .volume {
      border: 1px solid var(--ops-border);
      border-radius: 8px;
      padding: 10px;
      background: var(--ops-bg);
      display: grid;
      gap: 10px;
    }
    .stage-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(132px, 1fr));
      gap: 8px;
    }
    .stage {
      border: 1px solid var(--ops-border);
      border-radius: 8px;
      padding: 8px;
      background: var(--ops-surface);
      display: grid;
      gap: 4px;
      min-height: 72px;
    }
    .stage-label {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      font-weight: 750;
      font-size: .88rem;
    }
    .stage-meta {
      color: var(--ops-muted);
      font-size: .8rem;
      line-height: 1.35;
      overflow-wrap: anywhere;
    }
    .empty-state {
      border: 1px dashed var(--ops-border);
      border-radius: 8px;
      padding: 24px;
      color: var(--ops-muted);
      background: var(--ops-surface);
    }
    @media (max-width: 840px) {
      .ops-grid {
        grid-template-columns: 1fr;
      }
      .job-top, .volume-top {
        align-items: flex-start;
        flex-direction: column;
      }
      .status-pill {
        white-space: normal;
      }
    }
    @media (prefers-reduced-motion: reduce) {
      * {
        transition: none !important;
      }
    }
  </style>
</head>
<body>
  <div class="topbar">
    <a class="topbar-brand" href="/">翻譯工作台</a>
    <a class="topbar-link" href="/index.html">閱讀站</a>
    <button class="theme-toggle" type="button" aria-label="切換深色或淺色模式"></button>
  </div>
  <main class="ops-shell">
    <header class="ops-header">
      <div class="kicker">Pipeline</div>
      <h1>翻譯工作管線</h1>
      <p>每卷先檢查模型可用性；Codex/GPT 是必要條件，GLM 與 Claude 可降級。Draft 可平行，review 會優先 Claude、不可用時改用 GPT。</p>
      <p class="msg" id="message">__MESSAGE__</p>
    </header>
    <div class="ops-grid">
      <section class="ops-panel" aria-labelledby="submit-title">
        <h2 id="submit-title">送出工作</h2>
        <form class="job-form" method="post" action="/jobs">
          <label>CBETA 連結或經號
            <input type="text" name="link" placeholder="https://cbetaonline.dila.edu.tw/zh/T1579_013" required>
          </label>
          <label>卷號範圍
            <input type="text" name="juans" placeholder="13-15 或 13,15；留空時使用連結卷號">
          </label>
          <label>模型策略
            <span class="models">
              <label><input type="radio" name="model" value="dual" checked> dual team：GPT + GLM draft，Claude/GPT review</label>
              <label><input type="radio" name="model" value="mix"> mix：舊版多模型路由</label>
              <label><input type="radio" name="model" value="claude"> claude only</label>
              <label><input type="radio" name="model" value="codex"> codex only</label>
              <label><input type="radio" name="model" value="glm"> glm only</label>
            </span>
          </label>
          <button class="primary-btn" type="submit">排入佇列</button>
        </form>
      </section>
      <section class="jobs-panel" aria-labelledby="jobs-title">
        <div class="jobs-head">
          <h2 id="jobs-title">工作佇列</h2>
          <div class="summary" id="summary" aria-live="polite"></div>
        </div>
        <div id="jobs" class="job-list" aria-live="polite">載入中...</div>
      </section>
    </div>
  </main>
  <script>
    const zh = {
      queued: "排隊中", running: "進行中", done: "完成", failed: "失敗",
      waiting_limit: "等待額度", waiting_model: "等待 GPT", waiting: "等待中",
      pending: "未開始", skipped: "略過", cancelled: "已取消"
    };
    const taskLabels = {
      availability: "模型檢查", segment: "切段", skeleton: "骨架",
      draft_codex: "GPT draft", draft_glm: "GLM draft", review: "Review",
      checks: "檢查", repair: "修補", html: "HTML", commit: "Commit"
    };
    const taskOrder = Object.keys(taskLabels);
    function esc(value) {
      return String(value ?? "").replace(/[&<>"']/g, c => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }[c]));
    }
    function label(state) {
      return zh[state] || state || "未開始";
    }
    function stateClass(state) {
      return `state-${esc(state || "pending")}`;
    }
    function resumeText(value) {
      return value ? `，${esc(value.slice(11, 16))} 後續跑` : "";
    }
    function statusPill(state, extra = "") {
      return `<span class="status-pill ${stateClass(state)}"><span class="dot"></span>${label(state)}${extra}</span>`;
    }
    function canCancel(volume) {
      if (!volume || volume.cancelled || volume.step === "cancelled") return false;
      if (![undefined, null, "queued", "availability", "waiting_model"].includes(volume.step)) return false;
      const tasks = volume.tasks || {};
      return Object.entries(tasks).every(([name, task]) =>
        name === "availability" || [undefined, null, "pending", "queued", "cancelled"].includes(task.state));
    }
    function taskCard(name, task) {
      const state = task?.state || "pending";
      const model = task?.model ? `<span>${esc(task.model)}</span>` : "";
      const progress = task?.sections_total ? `<span>${esc(task.section || 0)}/${esc(task.sections_total)}</span>` : "";
      const reason = task?.reason ? `<span>${esc(task.reason)}</span>` : "";
      const error = task?.error ? `<span>${esc(task.error)}</span>` : "";
      return `<div class="stage ${stateClass(state)}">
        <div class="stage-label"><span>${esc(taskLabels[name] || name)}</span><span class="dot"></span></div>
        <div class="stage-meta">${label(state)} ${model} ${progress} ${reason} ${error}</div>
      </div>`;
    }
    function volumeCard(job, juan) {
      const volume = (job.progress || {})[String(juan)] || {step: "queued", tasks: {}};
      const state = volume.cancelled ? "cancelled" : (volume.error ? "failed" : volume.step || "queued");
      const tasks = volume.tasks || {};
      const cancel = canCancel(volume)
        ? `<button class="cancel-btn" type="button" data-job="${esc(job.id)}" data-juan="${esc(juan)}">取消此卷</button>`
        : "";
      const cards = taskOrder.map(name => taskCard(name, tasks[name])).join("");
      const error = volume.error ? `<div class="stage-meta">錯誤：${esc(volume.error)}</div>` : "";
      return `<article class="volume">
        <div class="volume-top">
          <strong>卷 ${esc(juan)}</strong>
          <div>${statusPill(state)} ${cancel}</div>
        </div>
        ${error}
        <div class="stage-grid">${cards}</div>
      </article>`;
    }
    function jobCard(job) {
      const resume = resumeText(job.resume_at);
      const volumes = (job.juans || []).map(juan => volumeCard(job, juan)).join("");
      return `<article class="job-card">
        <div class="job-top">
          <div class="job-title">
            <strong>${esc(job.work)} 卷 ${(job.juans || []).map(esc).join(", ")} · ${esc(job.model)}</strong>
            <span class="job-id">${esc(job.id)}</span>
          </div>
          ${statusPill(job.state, resume)}
        </div>
        <div class="volume-list">${volumes}</div>
      </article>`;
    }
    async function cancelVolume(button) {
      button.disabled = true;
      const body = new URLSearchParams({juan: button.dataset.juan});
      const url = `/api/jobs/${encodeURIComponent(button.dataset.job)}/cancel-volume`;
      const result = await fetch(url, {method: "POST", body}).then(r => r.json()).catch(() => null);
      document.getElementById("message").textContent = result?.ok ? "已取消該卷。" : `取消失敗：${result?.error || "unknown error"}`;
      await refresh();
    }
    async function refresh() {
      const data = await fetch("/api/status").then(r => r.json()).catch(() => null);
      if (!data) return;
      const jobs = data.jobs || [];
      const counts = jobs.reduce((acc, job) => {
        acc[job.state] = (acc[job.state] || 0) + 1;
        return acc;
      }, {});
      document.getElementById("summary").innerHTML = Object.entries(counts)
        .map(([state, count]) => `<span class="metric">${label(state)} ${count}</span>`).join("");
      document.getElementById("jobs").innerHTML = jobs.length
        ? jobs.map(jobCard).join("")
        : `<div class="empty-state">目前沒有工作。</div>`;
      document.querySelectorAll("[data-job][data-juan]").forEach(btn => {
        btn.addEventListener("click", () => cancelVolume(btn), {once: true});
      });
    }
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
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/jobs"):
            message = "已排入佇列。" if "queued=1" in parsed.query else ""
            self._send_html(FORM_PAGE.replace("__MESSAGE__", message))
        elif parsed.path == "/api/status":
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
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/cancel-volume"):
            job_id = parsed.path.split("/")[3]
            length = int(self.headers.get("Content-Length", 0))
            form = parse_qs(self.rfile.read(length).decode("utf-8"))
            try:
                juan = int(form.get("juan", [""])[0])
            except ValueError:
                self._send_json({"ok": False, "error": "invalid juan"}, status=400)
                return
            ok, message = runner.cancel_juan(job_id, juan)
            self._send_json({"ok": ok, "error": None if ok else message}, status=200 if ok else 409)
            return
        if parsed.path != "/jobs":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(length).decode("utf-8"))
        link = form.get("link", [""])[0].strip()
        juans_spec = form.get("juans", [""])[0].strip()
        model = form.get("model", [""])[0]
        try:
            if model not in runner.QUEUES:
                raise ValueError(f"unknown model: {model}")
            if re.fullmatch(r"[A-Z]+\d+[a-z]?", link):
                work, link_juan = link, None
            else:
                work, link_juan = runner.parse_link(link)
            juans = runner.parse_juans(juans_spec) if juans_spec else ([link_juan] if link_juan else None)
            runner.get_work(work)
            if not juans:
                raise ValueError("請給卷號範圍，或用帶卷號的連結")
        except ValueError as err:
            self._send_html(FORM_PAGE.replace("__MESSAGE__", f"錯誤：{err}"), status=400)
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
        self.send_header("Location", "/jobs?queued=1")
        self.end_headers()

    def _send_html(self, page: str, status: int = 200) -> None:
        body = page.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # quiet static-file noise
        if "/api/" not in str(args[0] if args else ""):
            return


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 8787), Handler)
    print("webapp on http://127.0.0.1:8787 (form + /api/status + docs/ preview)")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
