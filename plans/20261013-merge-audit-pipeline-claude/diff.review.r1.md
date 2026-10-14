## Verdict

FIX — M1–M4 都有對應實作，但換檔失敗、續跑還原與審查版本綁定仍有三個洞，需要補完才能提交。

## Findings

以下反例皆可加入 `tests/test_legacy_audit_paths.py` 的 `LegacyAuditPathTests`。全部 **not run**：本次 read-only 沙盒不允許 fixture 建立暫存檔；未修改任何檔案。

1. **high — 換檔途中遇到 I/O 例外，會留下缺失的正式譯稿**

   **證據：**[scripts/audited.py:2573](/Users/davidshih/projects/translation/yogacara-merge-audit-pipeline/scripts/audited.py:2573) 先搬走 md；2584–2596 的候選稿寫入與安裝只處理 `FileExistsError`，成功後才保存 `installed`。外層 2731 的還原則要求已是 `installed`。違反 M4／K5 的失敗保全保證。

   **影響：**候選稿寫入或 `os.link` 拋出其他 `OSError` 時，指令回傳 fail／none，但 md 仍在 backup，正式路徑缺檔。同一區間若安裝完成卻在保存狀態前被殺，續跑也缺少可靠的還原狀態。

   **建議：**搬走 md 後，所有安裝例外都必須以不覆寫方式恢復；保存可辨認「已搬走／已裝上但尚未記錄」的恢復資訊。

   **反例：**

   ```python
   def test_candidate_link_error_restores_translation(self):
       original = self.md.read_bytes()
       driver = """
   real_link = os.link
   def fail_candidate_link(src, dst, *args, **kwargs):
       if Path(src).name.endswith(".candidate"):
           raise OSError("simulated candidate link failure")
       return real_link(src, dst, *args, **kwargs)
   os.link = fail_candidate_link
   """
       done = fixture.drive(
           self.tree, driver, "--juans", "7", "--model", "echo-finding"
       )
       self.assert_exit(done, 1)
       self.assertTrue(self.md.is_file())
       self.assertEqual(original, self.md.read_bytes())
   ```

2. **high — 重建後被殺，再續跑失敗，只還原 md，留下不同版本的網站**

   **證據：**[scripts/audited.py:2661](/Users/davidshih/projects/translation/yogacara-merge-audit-pipeline/scripts/audited.py:2661) 的頁面與搜尋索引備份只存在記憶體；程序死亡後即消失。續跑若在結構或審查階段失敗，2731–2736 只呼叫 `restore_original_translation`。違反 M4／G4，以及 K5 的譯稿與網站保全要求。

   **影響：**頁面和搜尋索引已重建、封存前程序被殺；下一次因資料缺失等原因失敗，md 回到原稿，網站卻仍顯示前次候選稿。現有中斷測試是在頁面重建**之前**殺程序，沒涵蓋這個時點。

   **建議：**續跑失敗而還原 md 時，同步恢復頁面與索引。若要求逐 byte 還原，重建前的快照也必須能跨程序恢復。

   **反例：**

   ```python
   def test_failed_resume_after_site_build_restores_the_site(self):
       before = fixture.digest(self.tree, "docs", "translations")
       driver = """
   def kill_before_sealing(*args, **kwargs):
       os._exit(9)
   audited.create_legacy_attestation = kill_before_sealing
   """
       killed = fixture.drive(
           self.tree, driver, "--juans", "7", "--model", "echo-finding"
       )
       self.assert_exit(killed, 9)
       (self.tree / "data" / "T1579-007.json").unlink()

       resumed = fixture.audit(self.tree, "--resume", self.job_path().stem)

       self.assert_exit(resumed, 1)
       self.assertEqual(
           before, fixture.digest(self.tree, "docs", "translations")
       )
   ```

3. **medium — 審查等待期間更新 policy，紀錄與封存快照可能指向沒用過的版本**

   **證據：**[scripts/audited.py:946](/Users/davidshih/projects/translation/yogacara-merge-audit-pipeline/scripts/audited.py:946) 在模型回覆後重新讀 policy，填入事件 hash；1809–1817 又在品質關卡重新讀 live 輸入作快照。`write_coverage_ledger` 驗證 source／translation／contract，沒有確認各審查回答的 prompt 仍符合關卡採用的版本。涉及 M2／K4.9、M4／K5.8。

   **影響：**第一個 request 用 P0，等待時有人保存 P1；該事件卻記成 P1，最後也封存 P1。指令仍能 sealed，但第一段沒有依封存所稱的 policy 審查。

   **建議：**送出 request 時捕捉其輸入版本，避免回覆後重新取樣；過關前確認所有有效審查仍綁定目前採用的輸入，變更者重新審查。

   **反例：**

   ```python
   def test_policy_snapshot_matches_the_effective_review_request(self):
       import gzip

       driver = fixture.CAPTURE_BACKEND + """
   real_backend = llm.subprocess.run
   def change_policy_during_call(command, input=None, **kwargs):
       result = real_backend(command, input=input, **kwargs)
       if len(CALLS) == 1:
           policy = Path("prompts/audited/common_policy.txt")
           policy.write_text(
               policy.read_text(encoding="utf-8") + "\\n- New policy.\\n",
               encoding="utf-8",
           )
       return result
   llm.subprocess.run = change_policy_during_call
   """
       done = fixture.drive(self.tree, driver, "--juans", "7")
       self.assert_exit(done, 0)
       job = json.loads(self.job_path().read_text(encoding="utf-8"))
       log = self.tree / "audits" / job["id"] / "events.jsonl"
       events = [
           json.loads(line)
           for line in log.read_text(encoding="utf-8").splitlines()
       ]
       gate = next(
           event for event in reversed(events)
           if event.get("type") == "quality_gate"
       )
       policy = gzip.decompress(
           (self.tree / gate["artifacts"]["policy"]["path"]).read_bytes()
       ).decode("utf-8").strip()
       review_id = job["progress"]["7"]["review_attestations"][
           "T1579-007-001"
       ]["review_terms"]["review_event_id"]
       review = next(e for e in events if e["event_id"] == review_id)
       request = gzip.decompress(
           (self.tree / review["artifacts"]["request"]["path"]).read_bytes()
       ).decode("utf-8")

       self.assertTrue(request.startswith(policy + "\n\n---\n\n"))
   ```

## Report claims not supported by the diff

`implement-report.md` 的「M1–M4 全部做完」過度宣稱：上述失敗恢復與版本綁定仍未滿足 K5。報告列出的測試通過結果未在本次重跑；沒有證據判定那些執行結果為假。