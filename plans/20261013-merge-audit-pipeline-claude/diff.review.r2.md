## Verdict

FIX — 本輪修正均對應已接受的 review，但新增的 `put_back` 備援仍可能刪掉新版譯稿，或讓續跑採用未寫完的檔案。

## Findings

1. **high — 寫回失敗的清理會刪掉不屬於本次操作的檔案**

   **證據：**[scripts/audited.py:2301](/Users/davidshih/projects/translation/yogacara-merge-audit-pipeline/scripts/audited.py:2301)，M4／K5.12、C-1／G-1。`open(dst, "xb")` 本身失敗，或寫入期間 owner 以新版替換 `dst`，都會進入 2306–2307，無條件刪除目前的 `dst`。

   **影響：**exclusive open 只能防止開啟時覆寫既有檔；這段清理仍可能直接刪掉 owner 已存好的新版，違反譯稿保全保證。

   **建議：**不要在例外清理中刪除正式路徑。只清理私有暫存檔，再以原子且不覆寫的操作安裝完整內容；無法安裝時保留備份與恢復狀態。

   **反例：**加入 `tests/test_legacy_audit_paths.py` 的 `PlaceExclusiveTests`。只替換系統呼叫邊界，驗證目的檔在 I/O 錯誤下仍保留。

   ```python
   def test_failed_put_back_never_deletes_an_existing_save(self):
       from unittest.mock import patch

       self.dst.write_bytes(b"OWNER-SAVE")
       with patch("os.link", side_effect=OSError("link I/O failure")):
           with patch("builtins.open", side_effect=PermissionError("open failed")):
               try:
                   audited.put_back(self.src, self.dst)
               except OSError:
                   pass

       self.assertTrue(self.dst.is_file())
       self.assertEqual(b"OWNER-SAVE", self.dst.read_bytes())
   ```

   **執行：**not run。嘗試執行唯讀反例時，`uv` 初始化快取被沙盒拒絕：`Operation not permitted`。

2. **high — 寫回途中被殺，續跑會把半份譯稿當成 owner 新稿**

   **證據：**[scripts/audited.py:2302](/Users/davidshih/projects/translation/yogacara-merge-audit-pipeline/scripts/audited.py:2302) 直接在正式路徑建立並寫入檔案。若途中被殺，`settle_swap` 在 [2591](/Users/davidshih/projects/translation/yogacara-merge-audit-pipeline/scripts/audited.py:2591) 遇到既非候選稿也非原稿的內容，便清掉 `swap`；`select_audit_candidate` 隨後於 [2406](/Users/davidshih/projects/translation/yogacara-merge-audit-pipeline/scripts/audited.py:2406) 採用半檔並清掉既有狀態。涉及 M4／K5.2、K5.12、C-1／G-1。

   **影響：**原稿雖留在 backup，正常 `--resume` 已失去恢復關聯；指令失敗後，正式 md 仍可能只剩幾個 bytes。

   **建議：**完整寫好私有暫存檔後再安裝。若保留直接寫回方式，就必須持久記錄未完成的複製，讓續跑能辨認並完成恢復，不能當成 owner 修改。

   **反例：**加入 `tests/test_legacy_audit_paths.py` 的 `LegacyAuditPathTests`。

   ```python
   def test_resume_does_not_adopt_a_partial_put_back(self):
       original = self.md.read_bytes()
       driver = NO_HARD_LINK_ONTO_THE_TRANSLATION + """
   import builtins
   real_open = builtins.open

   class InterruptedCopy:
       def __init__(self, out):
           self.out = out

       def __enter__(self):
           return self

       def __exit__(self, *args):
           self.out.close()

       def write(self, data):
           self.out.write(data[:1])
           self.out.flush()
           os._exit(9)

   def interrupt_copy(file, mode="r", *args, **kwargs):
       out = real_open(file, mode, *args, **kwargs)
       if mode == "xb" and Path(file).name == "T1579-007-baihua.md":
           return InterruptedCopy(out)
       return out

   builtins.open = interrupt_copy
   """
       stopped = fixture.drive(
           self.tree, driver, "--juans", "7", "--model", "echo-finding"
       )
       self.assertIn(stopped.returncode, (1, 9), stopped.stdout + stopped.stderr)
       (self.tree / "data" / "T1579-007.json").unlink()

       resumed = fixture.audit(self.tree, "--resume", self.job_path().stem)

       self.assert_exit(resumed, 1)
       self.assertEqual(original, self.md.read_bytes())
   ```

   **執行：**not run；此 fixture 需要建立暫存檔，唯讀沙盒不允許。

## Report claims not supported by the diff

[implement-report.md:5](/Users/davidshih/projects/translation/yogacara-merge-audit-pipeline/plans/20261013-merge-audit-pipeline-claude/implement-report.md:5) 的「M1–M4 全部做完」仍過度宣稱：上述 M4／K5 的失敗與續跑保全尚未完整。沒有證據否定報告記錄的既有測試通過結果。