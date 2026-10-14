> Implementer: opus-implementer-max (claude-opus-5-5 max), tier hard.

## Status
DONE — plan 的 M1–M4 全部做完，`acceptance.sh` 28/28 PASS（exit 0），整套 242 個測試在 Python 3.14.7 與 3.12.13 都 OK；沒有 commit，`MERGE_HEAD`（a5f0779）還在。有 6 個超出 plan 字面的選擇，全列在 Deviations，請裁決。

## Changes
Repo：`/Users/davidshih/projects/translation/yogacara-merge-audit-pipeline`（以下路徑都在這底下）

- `scripts/audited.py`：新檔。分支的 `scripts/runner.py` 加 K4.1–K4.9，再加 K5／K7 的 `audit` 子指令（`audit_existing_juan`、`place_exclusive`、`build_legacy_page`、`create_legacy_attestation`、`cmd_audit` 等）。
- `scripts/llm.py`：K1 聯集。`MODELS`／`AUDIT_MODELS`／`FAKE_MODELS`／`AUDIT_FAKE_MODELS`、分支的 `classify_output`、新增 `classify_combined_output`、`run_llm` 依後端分流、`call_with_limit_retry` 同時支援 `run_fn` 與 `effort`／`on_attempt`、K6 三個離線後端、兩邊的 self-check。
- `scripts/check_translation_coverage.py`：K2 聯集。`check_ranges(..., source_lines=None, *, exact_once=True)`、`range_overlaps`、`full_line_id(line_id, prefix)`、`check_coverage_ledger(..., exact_once=None)`、`translation_content` 去掉段落 id 標記行、CLI 先讀 ledger 的 coverage 模式。
- `scripts/build_translation_html.py`：K3 聯集。`render(..., term_style="legacy"|"policy")`、`legacy_term_tips`／`legacy_wrap_terms`（main 的改名）、分支的 `TermTip`／`build_term_tips`／`term_tips`／`wrap_terms`、嚴格多論典 `parse_range`、`render_translation_bytes`／`build_translation`／`build_all`、CLI 加 `--term-style`。
- `scripts/audit.py`、`scripts/publisher.py`：新檔，分支 bytes，沒改。
- `prompts/audited/{common_policy,fix,merge,review_doctrine,review_parallel,review_terms,segment,translate}.txt`：新檔，分支的 8 個 prompt，bytes 不變。
- `tests/__init__.py`、`tests/test_translation_coverage.py`、`tests/test_audit_hardening.py`：新檔，分支 bytes，沒改。
- `tests/test_pipeline_audit.py`、`tests/test_llm_rate_limits.py`、`tests/test_build_translation_html.py`：分支版本，只做 K9 的三種修改（共 5 行）。
- `tests/test_audited_adaptations.py`：新檔，24 個測試（K4：normalize、legacy 契約、`review_pass` 三個 keyword、coverage 模式、新卷走完整管線、隔離與 claim、其他論典）。
- `tests/test_shared_libraries_union.py`：新檔，24 個測試（K1 後端分流與 K6 字面輸出、K2 兩種模式、K3 兩種樣式與 `parse_range`）。
- `tests/test_legacy_audit_paths.py`：新檔，12 個測試（M4 第 3 點點名的五條路徑，外加 6 條）。
- `.github/workflows/ci.yml`：新檔，`files/ci-yml.txt` 逐 byte。
- `README.md`：R5 衝突兩段都留（`Runner 的` 改成 `scripts/audited.py 的`），檔尾加 K8 段落。
- 取回 main bytes、相對 HEAD 無 diff（只解衝突）：`scripts/runner.py`；`docs/T1579/index.html`、`docs/T1579/search.json`、卷 1/2/3/11/12/33/34 與卷 67–72 的 `docs/T1579/translations/*.html`；`translations/T1579-06{7,8,9}-baihua.md`、`07{0,1,2}`；`translations/segments/T1579-067..072.tsv`；`prompts/{merge,review_doctrine,review_parallel,review_terms,segment,translate}.txt`。`prompts/common_policy.txt`、`prompts/fix.txt` 已從 index 移除。
- 沒動：`plans/` 全部、`tests/test_legacy_audit_acceptance.py`（sha256 `46d383df…`，與 `acceptance.sha256` 相同）、`acceptance.sh`（`664cd3a5…`，相同）、`HANDOFF.md`、`files/frozen-paths.txt` 列的路徑。

Staging：衝突路徑、`prompts/audited/` 已 `git add`，上層兩個 prompt 用 `git rm`；其餘新檔與後續修改都沒 stage。

## Verification
全部在 repo 根目錄跑，`PYTHONDONTWRITEBYTECODE=1`。

- Baseline：沒自己重跑，採用 `plans/20261013-merge-audit-pipeline-claude/baseline.md`（44 OK；`discover` 71 個裡 planner 的測試照設計失敗）。
- `git merge --no-commit --no-ff origin/codex/batch1-69`：停在 26 個衝突（預期）；現在 `git diff --name-only --diff-filter=U` 是空的。
- `python3 -B -m unittest tests.test_check_translation_coverage tests.test_llm_codex_config tests.test_paragraph_ids tests.test_runner_parallel tests.test_status_page`：Ran 44, OK（M1 函式庫寫完時與最後各一次）。
- 分支 5 個測試模組：Ran 112, OK。
- `python3 -B -m unittest tests.test_legacy_audit_acceptance`：Ran 27, OK（約 20 秒）。
- `python3 -B -m unittest discover -s tests`：Ran 242, OK（Python 3.14.7）。
- `/opt/homebrew/bin/python3.12 -B -m unittest discover -s tests`：Ran 242, OK（3.12.13，macOS；Linux runner 仍未驗）。
- `python3 -B scripts/llm.py`：`llm.py self-check OK`，exit 0。
- `python3 -B scripts/check_html_links.py`：`All relative links resolve.`，exit 0。
- `python3 -B scripts/check_paragraph_ids.py`：`81 pages, 11556 translation paragraphs: every id present and unique.`，exit 0。
- `actionlint .github/workflows/*.yml`：exit 0。
- `bash plans/20261013-merge-audit-pipeline-claude/acceptance.sh`：
  - 第一次（M1、M2 做完，K5 程式也已在檔案裡）：M1 16/16、M2 6/6、M4 4/4 PASS，M3 2 條 FAIL（還沒做），exit 1。
  - M3 做完並補完測試後、以及最後一次改 code 之後：28 PASS、0 FAIL，exit 0。
- `bash plans/20260929-progress-page/acceptance.sh`：12 PASS，exit 0。
- 跑完後 checkout 裡沒有 `audits/`、`artifacts/`、`jobs/`、`locks/`。
- 手寫 mutant 抽查（丟棄式暫存樹，不動 checkout）：33 個全被殺掉，涵蓋 `check_translation_coverage.py`、`llm.py` 分類與分流、`audited.py` 的 `review_pass` 重用／`only_units`、`place_exclusive`、換檔、還原、封存檢查、job 驗證。
- 手動在 scratch 樹跑 `audit --juans 7,76 --model echo-finding` 再 `--resume`：exit 0，兩卷 sealed，resume 不寫任何檔。
- 手動在 scratch 樹跑完整管線（`enqueue` + `run_job`，echo，全新的卷 7，`bsh.main` stub 掉）：走到 `checks_pre_review` 後因 echo 譯文不含「異熟」的 pattern 而 failed。這個 glossary 條目在分支與 main 相同，是 echo 抄原文的既有限制；合成資料的完整流程由 `FreshJuanTests` 與分支的 `test_dual_echo_one_volume_reaches_attested_build_in_temp_root` 驗過。

## Deviations from plan
1. **順序**。第一次跑 `acceptance.sh` 之前就先做了 K9 的測試修改與整份 `audited.py`（含 K5）。原因：`acceptance.sh` 會跑整個 suite，分支測試若還 `import runner`，會在 checkout 裡對 main 的正式 runner 執行。所以沒有「只做完 M1」的 acceptance 結果。
2. **`build_translation_html.py` 動到 main 的三個函式內部**（K3 說它們不變）。`works_by_id`、`legacy_term_tips`、`render` 讀 repo 路徑改用 import 時定好的 `WORKS_PATH`、`GLOSSARY_DIR`、`DOCS_DIR`，不再呼叫時讀 `ROOT`。不這樣做，分支的 `test_attested_build_verifies_before_and_after_write` 會失敗（它在兩次 render 之間 patch `bth.ROOT`，實測改回去就 `rendered candidate does not match`），而 K9 不准改那個測試。正式輸出不變（81 頁重現）。
3. **`audited.py` 的 skeleton 多傳 `work` 與 `work_title`**（K4 沒列）。main 的 `make_translation_skeleton.render` 簽名變了，分支原本的呼叫會把新卷寫成「# 經典卷第…」「CBETA WORK《經典》」，頁面標題跟著錯。改成與 `scripts/runner.py` L1478 相同的呼叫，一行，有測試。
4. **一卷 fail 時若路徑上還是先前被殺的嘗試裝上去的候選稿，就把原稿還原**（K5 第 12 步只列兩個使用點）。依據是 G4「整卷不過時譯稿與網頁維持原狀」；不還原的話，fixer 改過、沒過審的文字會留在正式的 md。有測試。
5. **封存後多一道檢查**：封存到的譯稿 hash 必須等於過審候選稿的 hash，不符就照第 12 步還原，blocking 是 `the translation changed during the audit`。補的是「第 9 步檢查之後、封存之前有人存檔」的空窗，否則沒審過的文字會被封存。有測試。
6. **K5 其他小選擇**：
   - 第 2 步：md 等於 `installed_sha256` 時也沿用候選稿，不當成被別人改過。
   - 第 3 步：glossary 不合法或 `data/` 檔不存在也直接 fail；審查指令不連網抓資料。
   - 第 6 步：術語問題逐段計算來對應段落，不從訊息切標題（結果相同）。
   - 第 12 步：還原時 md 路徑是空的就不放任何檔（可能是核准重跑搬走的）。
   - `--resume` 帶了與 job 不同的 `--model` 會被拒絕，避免以為用 echo、實際花真額度。
   - job 檔裡的 backup 路徑必須在該 job 的 `backup/` 內；失敗時只刪這次嘗試自己寫的封存檔。
   - exit 0 的條件是每卷報告 `pass` 且 `sealed`（已封存但檔案被改的卷算 exit 1）。
   - 報告內容沒變就不重寫；`findings.round1` 在後續嘗試沒有新 finding 時保留先前的數字。
   - 每卷多了 `base_units`、`original_backup`、`audit_warnings`、`findings` 幾個狀態欄。
   - 審查指令也會寫 `locks/audited-status.json` 與 `locks/jobs.lock`（都在 `.gitignore`），K5 的寫入清單沒列。
7. **K1**：`run_llm("codex", ...)` 的 effort 取 `codex_reasoning_effort or effort or CODEX_REASONING_EFFORT`（plan 沒說 `effort` 對 codex 別名的意義）。
8. **K3 CLI**：`--all` 搭配 `--term-style legacy` 會被拒絕，因為 `build_all` 的簽名沒有樣式參數，不想默默忽略。
9. **K2**：嚴格模式的訊息順序變成邊界檢查在前、Source 區塊在後（分支是逐段交錯），檢查項目相同。
10. **`cmd_run`** 改用共用的 `acquire_runner_lock()`，開 `runner.pid` 用 `a+` 而不是 `w`。

## Open issues
- **CI 在 Linux 上還沒跑過**。本機 3.12.13 全過，但 `ubuntu-latest` 要等 PR 的 `ci` run。
- **`normalize_translation` 照 plan 的字面公式**。模型輸出若含 U+2028 或 `\r\n` 這類行分隔，`parse_entries` 讀回來會不同，該段會被判成 stale 而整卷 fail（fail closed）。目前 81 份譯稿都沒有這類字元。
- **`review_pass` 的重用條件照字面比對 prompt hash**。某段若是靠 semantic retry 的第二次回答才過，之後每次 `--resume` 都會重審那一段（多花呼叫，不影響正確性）。
- **暫存資料夾殘留**：第一輪 mutant 抽查留下 `/var/folders/zv/g0jcxzqd3qngjqkxxg14pdv40000gp/T/mutants-wwulumwd`（約 20 MB，fixture 樹的副本）。它不在 repo 或 scratch 內，照 shell 規則我沒刪，請 owner 處理或等系統清。
- Scratch 目錄 `…/scratchpad/yoga-impl-scratch/` 留有產生器與抽查腳本（`patch_audited_*.py`、`build_bth.py`、`mutants*.py` 等），不屬於交付內容。
