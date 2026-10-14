> Security reviewer: Codex gpt-6.1-sol xhigh (run_codex.sh securityreview, read-only), prompt prompts/security-review.md.

## Verdict

**PASS** — 已讀完全部 26 個變更檔案及 hunk，未找到符合六道門檻的新增安全漏洞；判定限於本輪靜態審查。

## Findings

無。

## Checked and found fine

- `.github/workflows/ci.yml`：追查 PR 入口與命令執行；權限為 `contents: read`，沒有不可信內容插入 shell（CWE-78、200）。
- `README.md`：檢查新增操作指令；未指示停用 TLS 或新增秘密值（CWE-295、798）。
- `prompts/audited/common_policy.txt`：追查政策與模型呼叫；文字未直接進入 shell 或程式求值。
- `prompts/audited/fix.txt`：追查 finding → fixer → JSON 驗證 → 譯稿及 HTML（CWE-20、79）。
- `prompts/audited/merge.txt`：追查候選稿合併與輸出驗證（CWE-20、79）。
- `prompts/audited/review_doctrine.txt`：追查 review 身分綁定及引文驗證（CWE-20）。
- `prompts/audited/review_parallel.txt`：追查 reference allowlist 與無證據分支（CWE-20）。
- `prompts/audited/review_terms.txt`：追查術語提議、glossary 驗證及 tooltip escaping（CWE-20、79）。
- `prompts/audited/segment.txt`：追查模型輸出 → TSV → skeleton → 頁面；沒有模型控制的 shell 命令或輸出路徑（CWE-22、78、79）。
- `prompts/audited/translate.txt`：追查契約驗證、譯稿寫入及頁面 escaping（CWE-20、79）。
- [scripts/audit.py](/Users/davidshih/projects/translation/yogacara-merge-audit-pipeline/scripts/audit.py)：追查 artifact 路徑、gzip、秘密遮罩及 JSONL；未找到低權限輸入控制讀檔路徑或取得秘密的具體路徑（CWE-22、502、200）。
- [scripts/audited.py](/Users/davidshih/projects/translation/yogacara-merge-audit-pipeline/scripts/audited.py)：追查 CLI、job 載入、模型契約、換檔、還原與發布；resume 格式驗證、backup canonicalization 及不覆寫安裝均已核對（CWE-20、22、78、79）。
- [scripts/build_translation_html.py](/Users/davidshih/projects/translation/yogacara-merge-audit-pipeline/scripts/build_translation_html.py)：追查譯文、標題、note、tooltip 與輸出路徑；文字及屬性 escaping、Range 格式與 attested output hash 均已核對（CWE-79、22、20）。
- [scripts/check_translation_coverage.py](/Users/davidshih/projects/translation/yogacara-merge-audit-pipeline/scripts/check_translation_coverage.py)：追查 Markdown、JSON ledger、hash、引文與 coverage 模式；未使用可執行反序列化（CWE-20、502、327）。
- [scripts/llm.py](/Users/davidshih/projects/translation/yogacara-merge-audit-pipeline/scripts/llm.py)：追查兩支 runner 與 webapp caller、子程序 argv、環境處理、診斷及重試；沒有 shell 字串插值，正式 effort 有 allowlist（CWE-78、20、200、400）。
- [scripts/publisher.py](/Users/davidshih/projects/translation/yogacara-merge-audit-pipeline/scripts/publisher.py)：追查封存、receipt、Git staging 與 push；使用 argv、`git add --` 及 canonical repo-relative 路徑，未找到低權限發布入口（CWE-78、22、20）。
- `tests/__init__.py`：空檔，沒有執行或設定內容。
- `tests/test_audit_hardening.py`：檢查秘密樣本與 artifact fixture；屬測試資料，未被 production 匯入（排除 A）。
- `tests/test_audited_adaptations.py`：檢查契約、重用及隔離 patch；未弱化 production 控制（排除 A）。
- `tests/test_build_translation_html.py`：檢查 escaping、builder 授權及 hash mismatch 測試；寫入限於暫存樹（CWE-79、22、20）。
- `tests/test_legacy_audit_acceptance.py`：檢查 driver、故障注入、換檔及續跑；測試替換限於複製樹（排除 A）。
- `tests/test_legacy_audit_paths.py`：檢查不覆寫、backup 路徑及 owner-save 模擬（CWE-22、20；排除 A）。
- `tests/test_llm_rate_limits.py`：檢查診斷通道、prompt echo 與限流 patch；沒有 production 控制繞過（CWE-20、200）。
- `tests/test_pipeline_audit.py`：檢查全部契約、audit store 與 publisher 測試；fake command 未被 production 匯入（排除 A）。
- `tests/test_shared_libraries_union.py`：檢查後端 routing、coverage 模式與 renderer 相容測試（CWE-20、79）。
- `tests/test_translation_coverage.py`：檢查 ledger、引文、缺行與重疊 fixture（CWE-20、502）。
- **範圍外既有缺陷**：`scripts/build_shengwen_di_html.py:60` 的 TLS 驗證 fallback（CWE-295）及 `:373` 的搜尋 `innerHTML` 插值（CWE-79）均已存在於 base，原管線已可觸及；本次未改其可達性或嚴重度，不列 Findings。

## Not reviewed

- `scripts/llm.py` 呼叫的外部 Codex／Claude CLI 實作：依範圍限制未讀外部來源；實際 sandbox、工具權限及輸出通道仍需另驗。
- 未執行測試、瀏覽器或 exploit；本輪依要求只使用讀取命令。
- 計畫目錄內其餘報告與 artifact，依指示排除；指定的 `plan-final.html` 已讀。