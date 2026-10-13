## 可稽核管線（`scripts/audited.py`）

`scripts/runner.py` 是正式排程器，行為不變。`scripts/audited.py` 是另一條獨立的管線：每次模型呼叫都留下紀錄，審查要附原文與譯文的逐字引文，過了品質關卡才封存成卷。它目前只支援 T1579，工作狀態放在 `jobs/audited/`，不會認領正式排程器的工作。兩者共用 `locks/runner.pid`，同一時間只能跑一個。

稽核紀錄在 `audits/<job id>/`（事件、coverage ledger、封存證明、報告），模型的輸入與輸出在 `artifacts/sha256/`。這兩個資料夾都進 git。

只審查已發布的舊譯文，不重譯：

```sh
python3 scripts/audited.py audit --juans 67-72 --no-push
python3 scripts/audited.py audit --resume <job id> --no-push
```

- 每一段跑術語與義理兩道審查，再跑術語機械檢查。沒有問題的段落一個字都不動，段落 id 也不動。
- 被點名的段落交給 fixer 重寫，只有重寫過的段落會再審一次。
- 還有問題就整卷不過：譯稿與網頁維持原狀，候選稿與報告留在 `audits/<job id>/`。
- 相鄰段落共用一行邊界只記成警告，不擋關。
- `--model echo` 可以離線試跑整個流程，不花模型額度。
- 用封存證明重建舊式頁面時加上 `--term-style legacy`：`python3 scripts/build_translation_html.py --translation translations/T1579-067-baihua.md --attestation <封存證明> --term-style legacy`。
