## Verdict

FIX — 前輪兩項反例的修正已到位，但新增的索引還原分支在快照缺檔時，會留下未封存候選稿的搜尋內容。

## Findings

1. **medium — 沒有舊索引快照時，失敗候選稿仍留在搜尋索引**

   **證據：**`scripts/audited.py:2831–2832` 在索引快照不存在時直接返回；`:2875` 隨後清掉 `site_backup`。`snapshot_site`（`:2816–2820`）只保存存在的索引，因此審查前沒有 `search.json` 就能走到這條分支。涉及 M4／K5.12、G4 與已接受的 G2-2。

   **影響：**審查前索引不存在，重建後程序被殺；續跑封存失敗、原稿又無法放回時，頁面恢復舊版，搜尋索引卻仍含候選稿文字。

   **建議：**原先沒有索引時，把該卷的快照 records 視為空集合，移除 live 索引中該卷的候選稿 records，保留其他卷。

   **反例：**加入 `tests/test_legacy_audit_paths.py` 的 `LegacyAuditPathTests`：

   ```python
   def test_failed_resume_removes_candidate_records_without_index_snapshot(self):
       # Codex contribution: regression counterexample.
       search = self.tree / "docs" / WORK / "search.json"
       search.unlink()
       old_page = self.page.read_bytes()

       killed = fixture.drive(
           self.tree, KILL_AT_THE_SEAL,
           "--juans", "7", "--model", "echo-finding",
       )
       self.assert_exit(killed, 9)
       self.assertIn(MARK, search.read_text(encoding="utf-8"))

       resumed = fixture.drive(
           self.tree,
           fixture.FAIL_AT_SEAL + THE_ORIGINAL_CANNOT_BE_LINKED,
           "--resume", self.job_path().stem,
       )

       self.assert_exit(resumed, 1)
       self.assertEqual("installed", self.report()["install"])
       self.assertEqual(old_page, self.page.read_bytes())
       records = json.loads(search.read_text(encoding="utf-8"))
       self.assertTrue(any(record["j"] == 76 for record in records))
       self.assertNotIn(MARK, search.read_text(encoding="utf-8"))
   ```

   **執行：**`not run`；唯讀沙盒不允許 fixture 所需的暫存寫入。依目前控制流程，最後一個斷言會失敗。

## Report claims not supported by the diff

None.