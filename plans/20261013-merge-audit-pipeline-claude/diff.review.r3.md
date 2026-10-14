## Verdict

FIX — 前輪兩個 `put_back` 問題已修，但索引回復會蓋掉其他卷的新內容，prompt 的 pin 也仍有漏網路徑。

## Findings

1. **medium — 還原失敗時，舊索引快照會蓋掉其他 job 已發布的更新**

   **證據：**[scripts/audited.py:2854](/Users/davidshih/projects/translation/yogacara-merge-audit-pipeline/scripts/audited.py:2854) 在候選稿無法移除時，設定 `rebuild_search=False`；[scripts/audited.py:2830](/Users/davidshih/projects/translation/yogacara-merge-audit-pipeline/scripts/audited.py:2830) 隨即寫回所有搜尋索引的舊快照。涉及 M4／K5.12、G2-2，以及 C-2 已接受的跨 job 更新保全要求。

   **影響：**審查被殺後，另一個 job 可以更新卷 76。續跑卷 7 若無法還原原稿，就會把卷 76 的搜尋內容退回舊版；其他論典的新索引也可能被覆寫或刪掉。新增測試只驗證候選稿沒有進入索引，沒有同時驗證其他卷的新內容仍在。

   **建議：**這條 fallback 保留目前索引中其他卷的 records，只從快照回復失敗卷的 records；不要寫回整份舊索引。

   **反例：**加入 `tests/test_legacy_audit_paths.py` 的 `LegacyAuditPathTests`：

   ```python
   def test_failed_restore_preserves_another_jobs_search_update(self):
       # Codex contribution: regression counterexample.
       killed = fixture.drive(
           self.tree, KILL_AT_THE_SEAL,
           "--juans", "7", "--model", "echo-finding",
       )
       self.assert_exit(killed, 9)

       other = self.tree / "translations" / fixture.md_name(76)
       text = other.read_text(encoding="utf-8")
       published = re.sub(
           r"(Translation:\n<<<\n.*?)(\n>>>)",
           r"\1OTHER-JOB\2", text, count=1, flags=re.DOTALL,
       )
       self.assertNotEqual(text, published)
       other.write_text(published, encoding="utf-8")
       self.assert_exit(
           fixture.run_python(self.tree, "scripts/build_search_index.py"), 0,
       )
       search = self.tree / "docs" / WORK / "search.json"
       self.assertIn("OTHER-JOB", search.read_text(encoding="utf-8"))
       (self.tree / "data" / f"{WORK}-007.json").unlink()

       resumed = fixture.drive(
           self.tree, THE_ORIGINAL_CANNOT_BE_LINKED,
           "--resume", self.job_path().stem,
       )

       self.assert_exit(resumed, 1)
       self.assertEqual("installed", self.report()["install"])
       index = search.read_text(encoding="utf-8")
       self.assertNotIn(MARK, index)
       self.assertIn("OTHER-JOB", index)
   ```

   **執行：**`not run`。測試需建立暫存檔，唯讀沙盒不允許；嘗試啟動 `uv` 也以 exit 2 結束，印出 `Failed to initialize cache`、`Operation not permitted`。

2. **medium — pin 清單漏掉的必要 prompt，仍可從 live 檔讀入並通過封存**

   **證據：**[scripts/audited.py:514](/Users/davidshih/projects/translation/yogacara-merge-audit-pipeline/scripts/audited.py:514) 用 `glob("*.txt")` 決定 pin 清單；[scripts/audited.py:494](/Users/davidshih/projects/translation/yogacara-merge-audit-pipeline/scripts/audited.py:494) 對清單外的路徑退回 `path.read_bytes()`；[scripts/audited.py:1853](/Users/davidshih/projects/translation/yogacara-merge-audit-pipeline/scripts/audited.py:1853) 卻只保存清單內的 prompt。涉及 M4／K5.8、G2-1。

   **影響：**若必要 prompt 在列目錄時暫時不存在，隨後被重新建立，最後一輪審查會讀它的 live 內容，gate 卻沒有保存那份模板。若它在該輪再次變更，不同 unit 還可能回答不同版本；封存依然可以成功，G2-1 要消除的版本空窗仍存在。

   **建議：**明確讀取並 pin 必要模板；pin 啟用期間，遇到未保存的必要輸入就阻擋，不能退回 live 檔。

   **反例：**加入同一個 `LegacyAuditPathTests`。只攔截 `Path.glob`，模擬模板暫時被移除再重新建立：

   ```python
   def test_required_prompt_cannot_escape_the_gate_snapshot(self):
       # Codex contribution: regression counterexample.
       driver = """
   real_glob = Path.glob
   OMITTED = []

   def prompt_absent_during_listing(folder, pattern, *args, **kwargs):
       if folder == audited.PROMPTS_DIR and pattern == "*.txt" and not OMITTED:
           OMITTED.append(True)
           prompt = folder / "review_terms.txt"
           original = prompt.read_bytes()
           prompt.unlink()
           listing = list(real_glob(folder, pattern, *args, **kwargs))
           prompt.write_bytes(original + b"\\nUNPINNED-PROMPT\\n")
           return iter(listing)
       return real_glob(folder, pattern, *args, **kwargs)

   Path.glob = prompt_absent_during_listing
   """
       done = fixture.drive(
           self.tree, driver, "--juans", "7", "--model", "echo",
       )
       self.assertIn(done.returncode, (0, 1), done.stdout + done.stderr)
       if done.returncode == 1:
           self.assertEqual("fail", self.report()["verdict"])
           self.assertIsNone(self.report()["attestation"])
           return

       self.assert_sealed(self.report())
       gate = [
           event for event in self.events()
           if event["type"] == "quality_gate"
       ][-1]
       self.assertIn("prompt:review_terms.txt", gate["artifacts"])
       self.assertIn(
           "UNPINNED-PROMPT",
           self.artifact_text(gate["artifacts"]["prompt:review_terms.txt"]),
       )
   ```

   此測試接受完整保存實際模板，或明確阻擋；目前會成功封存，但缺少 `prompt:review_terms.txt`。

   **執行：**`not run`；唯讀沙盒不允許 fixture 所需的暫存檔寫入。

## Report claims not supported by the diff

[implement-report.md:5](/Users/davidshih/projects/translation/yogacara-merge-audit-pipeline/plans/20261013-merge-audit-pipeline-claude/implement-report.md:5) 的「M1–M4 全部做完」仍過度宣稱：上述 M4 的索引保全與審查輸入封存尚未完整。本輪未重跑測試，不否定報告記錄的通過結果。