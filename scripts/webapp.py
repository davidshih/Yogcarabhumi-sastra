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
    [hidden] { display: none !important; }
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
      gap: 4px;
    }
    .volume-row {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 4px 6px;
      border: 1px solid var(--ops-border);
      border-radius: 8px;
      padding: 4px 8px;
      background: var(--ops-bg);
      min-height: 30px;
      font-size: .84rem;
    }
    .volume-row > strong {
      min-width: 3.2em;
    }
    .chip {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      border: 1px solid var(--ops-border);
      border-radius: 999px;
      padding: 1px 8px;
      background: var(--ops-surface);
      color: var(--ops-muted);
      white-space: nowrap;
      font-variant-numeric: tabular-nums;
      cursor: default;
    }
    .chip .dot { width: 6px; height: 6px; }
    .chip.state-done { color: var(--ops-accent); border-color: var(--ops-accent); }
    .chip.state-running { color: var(--ops-text); border-color: var(--ops-accent); font-weight: 700; }
    .chip.state-failed { color: var(--ops-danger); border-color: var(--ops-danger); }
    .chip.state-skipped, .chip.state-cancelled { opacity: .55; text-decoration: line-through; }
    .chip.state-waiting { color: var(--ops-warn); border-color: var(--ops-warn); }
    .volume-error {
      flex-basis: 100%;
      color: var(--ops-danger);
      font-size: .8rem;
      overflow-wrap: anywhere;
    }
    .stage-config summary {
      cursor: pointer;
      font-weight: 650;
    }
    .stage-rows {
      display: grid;
      gap: 6px;
      margin-top: 8px;
    }
    .stage-row {
      display: grid;
      grid-template-columns: 4.5em 1fr 1fr;
      gap: 6px;
      align-items: center;
      font-size: .9rem;
    }
    .stage-row select {
      min-height: 34px;
      border: 1px solid var(--ops-border);
      border-radius: 6px;
      background: var(--ops-bg);
      color: var(--ops-text);
      font: inherit;
      font-size: .88rem;
      padding: 0 6px;
    }
    .job-actions {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }
    .retry-btn, .approve-btn {
      min-height: 32px;
      border-radius: 8px;
      padding: 0 12px;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
      border: 1px solid var(--ops-border);
      background: transparent;
      color: var(--ops-accent);
    }
    .approve-btn {
      background: var(--ops-accent);
      color: #fff;
      border: 0;
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
      <p>每卷先檢查所選模型可用性；階段主用模型不可用時自動切備援，撞額度自動解析重置時間、5 分鐘後續跑。已有譯文的卷會暫停等你核准，核准後舊譯封存為版本。</p>
      <p class="msg" id="message">__MESSAGE__</p>
    </header>
    <div class="ops-grid">
      <section class="ops-panel" aria-labelledby="submit-title">
        <h2 id="submit-title">送出工作</h2>
        <form class="job-form" method="post" action="/jobs">
          <label>經典
            <select name="work" id="workSelect">
__WORK_OPTIONS__
              <option value="__custom__">其他（貼 CBETA 連結）</option>
            </select>
          </label>
          <label id="customLinkRow" hidden>CBETA 連結
            <input type="text" name="link" placeholder="https://cbetaonline.dila.edu.tw/zh/T1585_001">
          </label>
          <label>卷號範圍（同一部經可多卷）
            <input type="text" name="juans" placeholder="13-15 或 13,15" required>
          </label>
          <details class="stage-config" open>
            <summary>各階段模型（主用 → 備援）</summary>
            <div class="stage-rows">
              <div class="stage-row"><span>切段</span>
                <select name="segment_primary"><option>claude</option><option>codex</option><option>glm</option></select>
                <select name="segment_fallback"><option value="">無備援</option><option selected>codex</option><option>claude</option><option>glm</option></select>
              </div>
              <div class="stage-row"><span>草稿 1</span>
                <select name="draft_codex_primary"><option selected>codex</option><option>claude</option><option>glm</option></select>
                <select name="draft_codex_fallback"><option value="" selected>無備援</option><option>claude</option><option>codex</option><option>glm</option></select>
              </div>
              <div class="stage-row"><span>草稿 2</span>
                <select name="draft_glm_primary"><option selected>glm</option><option>claude</option><option>codex</option><option value="">無（單稿）</option></select>
                <select name="draft_glm_fallback"><option value="" selected>無備援</option><option>claude</option><option>codex</option><option>glm</option></select>
              </div>
              <div class="stage-row"><span>終審</span>
                <select name="merge_primary"><option selected>claude</option><option>codex</option><option>glm</option></select>
                <select name="merge_fallback"><option value="">無備援</option><option selected>codex</option><option>claude</option><option>glm</option></select>
              </div>
              <div class="stage-row"><span>修補</span>
                <select name="repair_primary"><option selected>claude</option><option>codex</option><option>glm</option></select>
                <select name="repair_fallback"><option value="">無備援</option><option selected>codex</option><option>claude</option><option>glm</option></select>
              </div>
            </div>
          </details>
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
      waiting_limit: "等待額度", waiting_model: "等待模型", waiting: "等待中",
      pending: "未開始", skipped: "略過", cancelled: "已取消",
      awaiting_approval: "待核准重跑"
    };
    const taskLabels = {
      availability: "查", segment: "段", skeleton: "骨",
      draft_codex: "稿1", draft_glm: "稿2", review: "審",
      checks: "檢", repair: "修", html: "頁", commit: "推"
    };
    const taskFull = {
      availability: "模型檢查", segment: "切段", skeleton: "骨架",
      draft_codex: "草稿1", draft_glm: "草稿2", review: "終審",
      checks: "檢查", repair: "修補", html: "HTML", commit: "Commit+Push"
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
    function chipFor(name, task) {
      const state = task?.state || "pending";
      const cls = state === "waiting" || state === "waiting_model" ? "state-waiting" : stateClass(state);
      const progress = task?.sections_total ? ` ${task.section || 0}/${task.sections_total}` : "";
      const model = task?.model ? ` · ${task.model}` : "";
      const detail = [taskFull[name] || name, label(state), task?.model, task?.reason, task?.error]
        .filter(Boolean).join(" ｜ ");
      return `<span class="chip ${cls}" title="${esc(detail)}"><span class="dot"></span>${esc(taskLabels[name] || name)}${esc(progress)}${state === "running" ? esc(model) : ""}</span>`;
    }
    function volumeRow(job, juan) {
      const volume = (job.progress || {})[String(juan)] || {step: "queued", tasks: {}};
      const state = volume.cancelled ? "cancelled" : (volume.error ? "failed" : volume.step || "queued");
      const chips = taskOrder.map(name => chipFor(name, (volume.tasks || {})[name])).join("");
      const cancel = canCancel(volume)
        ? `<button class="retry-btn" type="button" data-action="cancel-volume" data-job="${esc(job.id)}" data-juan="${esc(juan)}">取消</button>`
        : "";
      const error = volume.error ? `<span class="volume-error">${esc(volume.error)}</span>` : "";
      return `<div class="volume-row">
        <strong>卷 ${esc(juan)}</strong>${statusPill(state)}${chips}${cancel}${error}
      </div>`;
    }
    function jobActions(job) {
      const actions = [];
      if (job.state === "awaiting_approval") {
        actions.push(`<button class="approve-btn" type="button" data-action="approve" data-job="${esc(job.id)}">核准重跑（另存新版）</button>`);
      }
      if (["failed", "cancelled"].includes(job.state)) {
        actions.push(`<button class="retry-btn" type="button" data-action="retry" data-job="${esc(job.id)}">重試</button>`);
      }
      if (!["done", "cancelled"].includes(job.state)) {
        actions.push(`<button class="cancel-btn" type="button" data-action="cancel" data-job="${esc(job.id)}">取消</button>`);
      }
      return `<div class="job-actions">${actions.join("")}</div>`;
    }
    function jobCard(job) {
      const resume = resumeText(job.resume_at);
      const volumes = (job.juans || []).map(juan => volumeRow(job, juan)).join("");
      const approval = job.state === "awaiting_approval"
        ? `<div class="volume-error">卷 ${(job.needs_approval || []).map(esc).join("、")} 已有譯文；核准後舊版封存為新版本、重新翻譯。</div>`
        : "";
      return `<article class="job-card">
        <div class="job-top">
          <div class="job-title">
            <strong>${esc(job.work)} 卷 ${(job.juans || []).map(esc).join(", ")}</strong>
            <span class="job-id">${esc(job.id)}</span>
          </div>
          <div>${statusPill(job.state, resume)}${jobActions(job)}</div>
        </div>
        ${approval}
        <div class="volume-list">${volumes}</div>
      </article>`;
    }
    async function jobAction(button) {
      button.disabled = true;
      const action = button.dataset.action;
      const url = action === "cancel-volume"
        ? `/api/jobs/${encodeURIComponent(button.dataset.job)}/cancel-volume`
        : `/api/jobs/${encodeURIComponent(button.dataset.job)}/${action}`;
      const body = action === "cancel-volume" ? new URLSearchParams({juan: button.dataset.juan}) : null;
      const result = await fetch(url, {method: "POST", body}).then(r => r.json()).catch(() => null);
      document.getElementById("message").textContent = result?.ok
        ? (result.message || "完成。") : `失敗：${result?.error || "unknown error"}`;
      await refresh();
    }
    document.getElementById("workSelect").addEventListener("change", e => {
      document.getElementById("customLinkRow").hidden = e.target.value !== "__custom__";
    });
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
      document.querySelectorAll("[data-action][data-job]").forEach(btn => {
        btn.addEventListener("click", () => jobAction(btn), {once: true});
      });
    }
    refresh();
    setInterval(refresh, 5000);
  </script>
</body>
</html>
"""


def work_options_html() -> str:
    works = json.loads((ROOT / "works.json").read_text(encoding="utf-8"))["works"]
    options = []
    for w in works:
        disabled = "" if w.get("pipeline_ready") else " disabled"
        suffix = "" if w.get("pipeline_ready") else "・準備中"
        options.append(f'              <option value="{w["id"]}"{disabled}>'
                       f'{w["title"]}（{w["id"]}）{suffix}</option>')
    return "\n".join(options)


STAGE_NAMES = ("segment", "draft_codex", "draft_glm", "merge", "repair")
STAGE_MODEL_CHOICES = {"claude", "codex", "glm", ""}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "docs"), **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/jobs"):
            message = "已排入佇列。" if "queued=1" in parsed.query else ""
            if "approval=1" in parsed.query:
                message = "此經卷已有譯文，工作已暫停，請在佇列中核准重跑。"
            self._send_html(FORM_PAGE.replace("__MESSAGE__", message)
                            .replace("__WORK_OPTIONS__", work_options_html()))
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
        if parsed.path.startswith("/api/jobs/"):
            parts = parsed.path.split("/")
            job_id, action = parts[3], parts[4] if len(parts) > 4 else ""
            length = int(self.headers.get("Content-Length", 0))
            form = parse_qs(self.rfile.read(length).decode("utf-8"))
            if action == "cancel-volume":
                try:
                    juan = int(form.get("juan", [""])[0])
                except ValueError:
                    self._send_json({"ok": False, "error": "invalid juan"}, status=400)
                    return
                ok, message = runner.cancel_juan(job_id, juan)
            elif action == "cancel":
                ok, message = runner.cancel_job(job_id)
            elif action == "retry":
                ok, message = runner.retry_job(job_id)
            elif action == "approve":
                ok, message = runner.approve_job(job_id)
            else:
                self.send_error(404)
                return
            self._send_json({"ok": ok, "message": message if ok else None,
                             "error": None if ok else message}, status=200 if ok else 409)
            return
        if parsed.path != "/jobs":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(length).decode("utf-8"))
        work_choice = form.get("work", [""])[0]
        link = form.get("link", [""])[0].strip()
        juans_spec = form.get("juans", [""])[0].strip()
        try:
            if work_choice and work_choice != "__custom__":
                work = work_choice
            elif re.fullmatch(r"[A-Z]+\d+[a-z]?", link):
                work = link
            else:
                work, _ = runner.parse_link(link)
            juans = runner.parse_juans(juans_spec) if juans_spec else None
            runner.get_work(work)
            if not juans:
                raise ValueError("請給卷號範圍")
            stages = {}
            for name in STAGE_NAMES:
                primary = form.get(f"{name}_primary", [""])[0]
                fallback = form.get(f"{name}_fallback", [""])[0]
                if not {primary, fallback} <= STAGE_MODEL_CHOICES:
                    raise ValueError(f"無效的模型選擇：{name}")
                stages[name] = {"primary": primary or None, "fallback": fallback or None}
        except ValueError as err:
            self._send_html(FORM_PAGE.replace("__MESSAGE__", f"錯誤：{err}")
                            .replace("__WORK_OPTIONS__", work_options_html()), status=400)
            return
        from datetime import datetime
        import uuid
        # already-translated volumes pause the job for explicit human approval
        needs_approval = [j for j in juans if runner.juan_translated(work, j)]
        job = {
            "id": f"{datetime.now():%Y%m%d-%H%M%S}-dual-{uuid.uuid4().hex[:4]}",
            "work": work, "juans": juans, "model": "dual", "stages": stages,
            "state": "awaiting_approval" if needs_approval else "queued",
            "needs_approval": needs_approval,
            "created": runner.now_iso(), "updated": runner.now_iso(),
            "pid": None, "resume_at": None, "error": None,
            "push": True, "summary": False, "progress": {},
        }
        runner.JOBS_DIR.mkdir(exist_ok=True)
        runner.save_job(job)
        self.send_response(303)
        self.send_header("Location", "/jobs?" + ("approval=1" if needs_approval else "queued=1"))
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
