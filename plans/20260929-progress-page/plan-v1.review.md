## Verdict

REVISE — 現行流程會把「進行中」版本 push 上線，最後的「已翻」重建只留在本機；另 `acceptance.sh` 在唯讀 sandbox 於 `mktemp` 遭 `Operation not permitted`，因此無法獨立重現作者宣稱的 baseline 結果。

## Findings

1. **high — 最後完成的卷會在線上永久顯示「進行中」**
   - **Evidence:** [plan-v1.html:66](/Users/davidshih/projects/translation/yogacara/plans/20260929-progress-page/plan-v1.html:66) 規定非終止狀態且 step 未完成就是 active；[runner.py:1405](/Users/davidshih/projects/translation/yogacara/scripts/runner.py:1405) 先把 step 設成 `commit`，接著 [runner.py:1408](/Users/davidshih/projects/translation/yogacara/scripts/runner.py:1408) commit/push；直到 [runner.py:1411](/Users/davidshih/projects/translation/yogacara/scripts/runner.py:1411) 才 `mark_juan_done()`。`git_commit_push()` 又在 [runner.py:1240](/Users/davidshih/projects/translation/yogacara/scripts/runner.py:1240) 重建頁面並立即 stage/push。
   - **Why it matters:** 被 push 的 `status.html` 會把當前卷標成 active；後續 `save_job()` 雖會在本機改成 done，卻沒有下一次 commit/push。最後一卷完成後，GitHub Pages 仍顯示進行中，直接違反「自動保持最新」。
   - **Suggested fix:** 計畫需新增安全的發佈時序，例如 commit 時以不提前持久化 `done` 的完成快照產生頁面，或在成功後另行提交狀態修正；同時處理 push 失敗，避免提前設 `done` 導致 retry 被短路。驗收應模擬完整 `run_juan`/push 流程並檢查被 stage 的頁面已是 done。

2. **medium — 驗收沒有覆蓋 runner 的實際發佈生命週期**
   - **Evidence:** [acceptance.sh:58](/Users/davidshih/projects/translation/yogacara/plans/20260929-progress-page/acceptance.sh:58) 只直接呼叫 `publish_status()`；[acceptance.sh:162](/Users/davidshih/projects/translation/yogacara/plans/20260929-progress-page/acceptance.sh:162) 至 [acceptance.sh:174](/Users/davidshih/projects/translation/yogacara/plans/20260929-progress-page/acceptance.sh:174) 沒有執行或 mock `git_commit_push()`、`run_juan()`、`mark_juan_done()`。
   - **Why it matters:** 即使 finding 1 的線上狀態錯誤存在，整份 acceptance 仍可能全綠，主成功條件等於沒鎖住。
   - **Suggested fix:** 加入一個無網路的生命週期測試，mock git subprocess，記錄 `git add` 當下的 `status.html`，確認完成卷為 done、其他執行中卷仍為 active。

3. **medium — CSS 篩選器完全失效也能通過 locked acceptance**
   - **Evidence:** 篩選是明確目標與實作契約（[plan-v1.html:73](/Users/davidshih/projects/translation/yogacara/plans/20260929-progress-page/plan-v1.html:73)、[plan-v1.html:198](/Users/davidshih/projects/translation/yogacara/plans/20260929-progress-page/plan-v1.html:198)），但 acceptance 只確認四個 radio 存在（[acceptance.sh:120](/Users/davidshih/projects/translation/yogacara/plans/20260929-progress-page/acceptance.sh:120)），CSS 僅 grep 兩個 dark-mode selector（[acceptance.sh:173](/Users/davidshih/projects/translation/yogacara/plans/20260929-progress-page/acceptance.sh:173)）。
   - **Why it matters:** 缺少三條 `:checked ~ ...` 隱藏規則、選中樣式或 focus 樣式時，腳本照樣 exit 0；四顆 tab 看得到但按了沒用也會過。
   - **Suggested fix:** acceptance 至少靜態檢查三種過濾 selector 與四種 selected-chip selector；最後驗證仍須依 [plan-v1.html:233](/Users/davidshih/projects/translation/yogacara/plans/20260929-progress-page/plan-v1.html:233) 用真實瀏覽器點擊並檢查可見 cell 與 375px overflow。

4. **low — 驗證流程漏掉強制的 qtest-first gate**
   - **Evidence:** 計畫直接執行完整 unittest（[plan-v1.html:230](/Users/davidshih/projects/translation/yogacara/plans/20260929-progress-page/plan-v1.html:230)），locked script 也直接跑兩個 unittest 與 full suite（[acceptance.sh:169](/Users/davidshih/projects/translation/yogacara/plans/20260929-progress-page/acceptance.sh:169)），未先用 `qtest` 跑受影響測試。
   - **Why it matters:** 這違反本 repo 工作流程明訂的「affected tests 先 qtest、完整套件留給 pre-commit gate」。
   - **Suggested fix:** 在 verification 順序最前加入針對 `tests/test_status_page.py` 的 `qtest`；通過後才進行 acceptance 與完整 gate。

## Questions for the plan author

None.