## Verdict

REVISE — 這版先修再開工：中斷的審查工作可能被翻譯 worker 接走、既有段落 id 可能消失，多卷審查也會互相弄壞封存證明。

唯讀檢查已確認 81/81 頁可逐 byte 重現、81/81 份譯稿通過現有範圍檢查。`acceptance.sh` 語法檢查通過；實際執行在 `mktemp` 被沙箱擋住，exit 2，輸出為 `mktemp: mkdtemp failed ... Operation not permitted`，因此未確認計畫聲稱的 9 過／18 失敗。沒有修改檔案。

## Findings

以下計畫與腳本行號均指 `plans/20261013-merge-audit-pipeline-claude/`；`a5f0779:` 表示讀取分支的 Git 物件。下列新增測試須由 **planner 先寫入 manifest 指定的 oracle 檔，再重新上鎖**，不能交給 implementer 自行補驗收。

1. **high — 中斷的審查工作會被翻譯 worker 認領**

   **證據：** `plan-v1.html:270,280` 保留 `run` 行為，並把 `legacy_audit` 放進同一個 `jobs/audited/`；`a5f0779:scripts/runner.py:1970–2008` 的 `claimable` 不檢查 `kind`，worker 一律呼叫 `run_job → run_juan`。

   已唯讀重現：`legacy_audit` 在 `queued`、無活躍 PID 的 `running`、`waiting_limit` 三種狀態都能被認領。審查被殺掉後啟動 `audited.py run`，便可能直接對正式 md 執行翻譯或 repair，違反「只審不重譯」及候選稿隔離。

   **修正：** 翻譯 worker 排除 `legacy_audit`；審查只能由明確的審查入口續跑。

   ```bash
   check "M2: legacy audits stay out of translation workers" py <<< 'import audited; assert all(not audited.claimable({"kind":"legacy_audit","model":m,"state":s,"pid":None},m) for m in ("auto","echo") for s in ("queued","running","waiting_limit"))'
   ```

2. **high — fixer 合併或刪除段落時，既有 id 保不住**

   **證據：** G4（`plan-v1.html:126`）要求所有既有 id 不動；K4.3 卻直接沿用 `carry_paragraph_ids`。`scripts/build_translation_html.py:135–142` 只配對新舊段落的較小數量；`tests/test_paragraph_ids.py:123–124` 明確預期刪除段落會丟掉 id。

   已重現三段合成一段時，`['a','b','c']` 變成 `['a']`。既有 fixer prompt、契約驗證沒有禁止改變段落數量；目前驗收只測追加「【已修】」，碰不到這條路。

   **修正：** legacy audit 在發布前檢查舊 id 全數保留；無法保留時整卷 fail、保留候選稿。這比修改正式 runner 的對齊規則更簡單。

   ```bash
   check "M4: paragraph merges cannot drop saved anchors" ran_ok 1 tests.test_legacy_audit_acceptance.LegacyAuditAcceptance.test_fixer_merge_preserves_ids_or_fails_without_publishing
   ```

3. **high — 下一卷改稿會讓上一卷的封存證明失效**

   **證據：** K5 保留 `create_volume_attestation`、`require_quality_gate`；`a5f0779:scripts/runner.py:1749` 封存整部論的 `search.json`，`scripts/build_search_index.py:18–38` 每次重建同一份索引。`publisher.verify_attestation:52–55` 要求封存檔案仍與目前 bytes 相同。

   先讓卷 7 通過並封存，再改卷 76，卷 7 的索引 hash 就失效。完成後 `--resume` 會重審上一卷。現有雙卷測試是「72 fail、76 pass」，沒有兩卷成功改稿，因此漏掉這個問題。

   **修正：** 將不可變的審查證據與可變的網站發布檔案分開封存，並驗證多卷完成後所有成功證明仍有效、續跑不增加模型呼叫。

   ```bash
   check "M4: successive rewrites preserve seals and finished resumes" ran_ok 1 tests.test_legacy_audit_acceptance.LegacyAuditAcceptance.test_two_successful_rewrites_keep_both_seals_valid_on_resume
   ```

4. **high — 安裝、硬殺及發布失敗沒有一致的恢復協定**

   **證據：** `plan-v1.html:294` 只保證 `(e)–(g)` 的例外還原；`(h)` 的 commit/push 不在其中。既有 publisher 有兩次 push（`a5f0779:scripts/publisher.py:136–153`），第一次成功、第二次失敗是已存在的路徑。

   另外，State 表 `plan-v1.html:403` 說硬殺後會保留未改動段落的 attestation；但 K5.2（`:287`）在 md 已換成候選稿、base hash 不符時，會清掉整卷契約與 attestation。兩段指示互相矛盾。記憶體中的例外還原也無法處理 SIGKILL。

   **修正：** 明訂並持久化安裝與發布階段、備份及恢復規則；區分「審查通過」和「發布待重試」。續跑不得把已成功發布的內容當成未通過審查，也不得重新執行 fixer。

   ```bash
   check "M4: interrupted installs and publication retries recover safely" ran_ok 2 tests.test_legacy_audit_acceptance.LegacyAuditAcceptance.test_resume_recovers_each_install_phase tests.test_legacy_audit_acceptance.LegacyAuditAcceptance.test_publication_retry_does_not_repeat_audit
   ```

5. **high — State and order 漏掉實際存在的其他 writer 與檢查後覆寫競態**

   **證據：** `plan-v1.html:400–406` 假設 runner PID lock 足以保護共用檔案。實際上，網頁排工作會呼叫 `save_job → publish_status`，重寫狀態頁與 app manifest（`scripts/runner.py:204–223`）；網頁核准重跑更會直接搬走 md（`:818–845`），只拿 `jobs.lock`。獨立 builder、搜尋索引工具及手動 git 也不受 PID lock 約束。

   K5 的 `(d)` hash 檢查與 `(e)` 覆寫之間，仍可插入使用者存檔；其後的無條件 rollback 也可能覆蓋較新的檔案。`build_page` 還會處理封存版本（`scripts/build_translation_html.py:512–526`），超出計畫列出的還原範圍。

   此次需要列完整的操作包括：模型審查／fix／retry／額度等待、job/status 保存、候選稿／event／artifact／ledger／report／seal 寫入、頁面與全域索引重建，以及 Git 查詢／fetch／commit／兩次 push。共用輸入還包括 data、glossary、policy、review evidence；K4.9 的重用條件沒有綁住後三者的內容版本。

   **修正：** 明訂所有 writer 的互斥或工作樹隔離規則；發布及 rollback 只能修改仍由本次操作持有的版本。輸入變更時，必須使相關審查快取失效。

   ```bash
   check "M4: concurrent writers and newer review inputs are preserved" ran_ok 2 tests.test_legacy_audit_acceptance.LegacyAuditAcceptance.test_concurrent_writers_preserve_newer_files tests.test_legacy_audit_acceptance.LegacyAuditAcceptance.test_resume_invalidates_changed_review_inputs
   ```

6. **high — 驗收可用一行把正式審查偷換成 echo**

   **證據：** `tests/test_legacy_audit_acceptance.py` 的 11 個流程測試全部使用 fake backend；`acceptance.sh:465` 只單獨檢查 `stage_model` 的常數，沒有測 `audit --model auto` 的實際呼叫鏈。

   **最小作弊實作：** 建立審查 job 時加入 `if model == "auto": model = "echo"`。其他功能照做，全部現有檢查仍可通過；真正使用者卻取得無模型審查的 pass。

   **修正：** planner 加一個無網路測試，攔截實際 backend 呼叫，確認 `audit --model auto` 使用指定模型與 effort，且保存的 job、事件與呼叫一致。真實 CLI 的輸出通道行為仍是外部前提；`:555` 目前只有 mock，應以去密的真實輸出樣本補驗。

   ```bash
   check "M4: auto audits reach the pinned real backend" ran_ok 1 tests.test_legacy_audit_acceptance.LegacyAuditAcceptance.test_auto_audit_calls_pinned_backend_and_preserves_model
   ```

7. **medium — 有平行文證據時，fix 後沒有重跑平行文審查**

   **證據：** K5.5（`plan-v1.html:290`）允許 `review_parallel` 產生 finding；K5.8（`:293`）卻只重跑 terms、doctrine。既有 ledger 也只要求這兩道證據（`a5f0779:scripts/runner.py:1587`）。

   平行文 finding 即使未被修好，仍可能通過最後關卡。

   **修正：** 對被重寫且有平行文證據的 unit，重跑 parallel，將其剩餘 finding 納入 blocking。

   ```bash
   check "M4: parallel findings must pass a matching re-gate" ran_ok 1 tests.test_legacy_audit_acceptance.LegacyAuditAcceptance.test_parallel_finding_is_regated_with_same_evidence
   ```

8. **medium — 新 CLI 的路徑與載入狀態缺少驗證，T1579 限制也只守住 audit**

   **證據：** K5（`plan-v1.html:280`）對 resume 只要求檔案存在、`kind` 正確，沒有 job id、路徑 containment、載入後 work 等驗證。`a5f0779:scripts/audit.py:65` 直接用 job id 組路徑；已確認 `../../outside` 會得到 `audits/../../outside`。

   同時 K4 保留 enqueue；`a5f0779:scripts/runner.py:2054–2063` 的 `get_work` 會接受 `works.json` 中 `pipeline_ready: true` 的 T1821，與 N3、固定 T1579 glossary 衝突。

   **修正：** 在讀取／建立 job 前驗證 id 與解析後路徑；驗證載入後的 work、卷次及狀態結構。enqueue、run、resume 都必須落實 T1579 範圍限制。

   ```bash
   check "M4: CLI paths and loaded jobs stay within supported scope" ran_ok 2 tests.test_legacy_audit_acceptance.LegacyAuditAcceptance.test_rejects_resume_path_escape_and_invalid_job_fields tests.test_legacy_audit_acceptance.LegacyAuditAcceptance.test_enqueue_refuses_other_works
   ```

9. **medium — 測試數量不等於測試真的執行，而且新增測試沒有被 acceptance 跑到**

   **證據：** `acceptance.sh:220–240` 只要求 exit 0 與 `Ran N tests`；unittest 的 `OK (skipped=...)` 也會通過。允許修改的三份分支測試共有 94 個測試，可以保留名稱與斷言、加 skip 後繼續滿足數量檢查。`:249–254` 對完整 suite 只數量計算，沒有執行 M2/M4 要求新增的測試。

   **修正：** acceptance 執行完整 suite，拒絕 skipped 與 expected failures；斷言完整性仍須保留既有 harness 檢查。

   ```bash
   check "M4: the complete CI suite executes without skips" python3 -B -c 'import unittest; r=unittest.TextTestRunner().run(unittest.defaultTestLoader.discover("tests")); assert r.wasSuccessful() and not r.skipped and not r.expectedFailures'
   ```

10. **medium — planner oracle 沒有進 checkpoint**

    **證據：** `acceptance.manifest:1` 指向 `tests/test_legacy_audit_acceptance.py`，但它目前是 untracked；`a01df22` 只提交 run folder 的九個檔案。

    從 checkpoint 建立新 worktree 或 clone 時，會缺少這份不可由 implementer 重寫的 oracle。

    **修正：** planner 將 oracle 納入 checkpoint，再上鎖與交付。

    ```bash
    check "M4: the planner oracle is tracked" git ls-files --error-unmatch -- tests/test_legacy_audit_acceptance.py
    ```

11. **medium — acceptance 在 base 上會寫入 checkout，與自身宣告不符**

    **證據：** `acceptance.sh:3–4` 宣告不寫 tracked file；`:415` 卻直接對 repo 執行未帶 attestation 的 builder。base 的 builder 會呼叫 `build_page` 並寫正式 HTML（`scripts/build_translation_html.py:526,543`），才以 exit 0 讓檢查失敗。

    即使 bytes 恰好相同，這仍是寫入操作；因此這條 base reproduction 不能安全地在唯讀 checkout 執行。

    **修正：** 將現有 `builder_cli_guard` 移至暫存 fixture 執行，取代原檢查：

    ```bash
    check "M1: unsealed builder rejection is tested outside the checkout" python3 -B -c 'import sys,tempfile; from pathlib import Path; sys.path.insert(0,"tests"); from test_legacy_audit_acceptance import build_template,run_python,digest; t=tempfile.TemporaryDirectory(); r=Path(t.name)/"repo"; build_template(r); h=digest(r,"docs","translations"); p=run_python(r,"scripts/build_translation_html.py","--translation","translations/T1579-007-baihua.md"); assert p.returncode==2 and "--attestation" in p.stderr,(p.stdout,p.stderr); assert digest(r,"docs","translations")==h'
    ```

12. **medium — 合法的 legacy ledger 仍會被 coverage CLI 拒絕**

    **證據：** K2 保留分支 CLI（`plan-v1.html:233`）。該 CLI 先以預設 exact-once 執行 `check_ranges`（`a5f0779:scripts/check_translation_coverage.py:304`），再讀 ledger。因此，即使 ledger 宣告 `at_least_once`，卷 76 的合法共用邊界仍先被判為錯誤。

    **修正：** `--ledger` 模式先讀取並驗證 coverage 模式，再用相同模式檢查 ranges；`--ranges-only` 保持嚴格模式。

    ```bash
    check "M4: legacy ledgers can be independently revalidated" ran_ok 1 tests.test_legacy_audit_acceptance.LegacyAuditAcceptance.test_legacy_ledger_cli_accepts_shared_boundaries_and_rejects_gaps
    ```

## Questions for the plan author

- 內容 push 已成功、publication metadata push 失敗時，要回報「審查 pass、發布待重試」，還是整卷 fail？目前 K5 的 fail 還原保證，無法直接套用到已成功 push 的內容。