# 瑜伽師地論 HTML

這個 repo 會從 CBETA API 下載《瑜伽師地論》相關卷次，並產出可閱讀的 HTML 檔。`docs/T1579/sections/` 目前仍依〈聲聞地〉目次切分；`docs/T1579/translations/` 則可保存任一卷的白話對照翻譯。

輸出位置：

- `docs/T1579/index.html`：章節索引
- `docs/T1579/sections/*.html`：各章節 HTML
- `docs/T1579/translations/T1579-XXX-baihua.html`：各卷白話左右對照翻譯
- `docs/T1579/docs/translation-workflow.html`：後續卷次白話翻譯工作流程與 agent 分工
- `data/*.json`：下載時保留的 CBETA API 原始回應
- `translations/*.md`：白話翻譯來源稿
- `translations/segments/*.tsv`：每卷翻譯段落切分表，可重建來源稿骨架
- `translations/glossary/T1579-terms.json`：跨卷術語庫

線上閱讀：

- GitHub Pages：`https://davidshih.github.io/Yogcarabhumi-sastra/`

部署方式：

- `.github/workflows/deploy-pages.yml` 會在每次 push 到 `main` 時，把 `docs/` 目錄部署到 GitHub Pages。
- 手動重跑部署可到 GitHub Actions 執行 `Deploy Pages` workflow。
- 若改用 S3，直接同步 `docs/` 目錄即可；但若要 HTTPS、自訂網域與快取，通常還需要 CloudFront 與 DNS 設定。此 repo 目前以 GitHub Pages 為預設發布方式。

使用方式：

```sh
python3 scripts/build_shengwen_di_html.py
python3 scripts/make_translation_skeleton.py --juan 11 --data data/T1579-011.json --segments translations/segments/T1579-011.tsv --output translations/T1579-011-baihua.md --start T30n1579_p0328c02 --end T30n1579_p0335a10
python3 scripts/build_translation_html.py --translation translations/T1579-033-baihua.md --diagnostic-output /tmp/T1579-033-baihua.html
python3 scripts/check_translation_terms.py --translation translations/T1579-033-baihua.md
python3 scripts/check_translation_coverage.py --translation translations/T1579-033-baihua.md --data data/T1579-033.json --start T30n1579_p0465a23 --end T30n1579_p0470c05 --ranges-only
python3 scripts/check_html_links.py
```

後續卷次請把 `033`、`data`、`segments`、`start`、`end` 換成該卷設定。例：卷11 使用 `translations/T1579-011-baihua.md`、`data/T1579-011.json`、`translations/segments/T1579-011.tsv`、`T30n1579_p0328c02` 到 `T30n1579_p0335a10`；卷12 使用 `T30n1579_p0335a13` 到 `T30n1579_p0341a19`；卷34 使用 `T30n1579_p0470c08` 到 `T30n1579_p0478b01`。

直接寫入 `docs/` 的單卷 builder 必須提供包含來源與輸出 hash 的 `--attestation`。未封存的檢查輸出請明確使用 `--diagnostic-output` 並寫到 `docs/` 以外；全站舊式重建必須同時指定 `--all --legacy-rebuild`。Runner 的 `--requested-parallel` 只記錄外部 orchestrator 要求的 worker 上限；單一 job 內的卷次仍序列執行，實際 fan-out 仍由外部 orchestrator 負責。

資料來源：

- CBETA 目次 API：`https://cbdata.dila.edu.tw/stable/works/toc?work=T1579`
- CBETA 卷文 API：`https://cbdata.dila.edu.tw/stable/juans?work=T1579&juan=...&toc=1&work_info=1`

後續翻譯任務請先閱讀 `docs/T1579/docs/translation-workflow.html`。該頁記錄段落切分、agent pool 分工、術語庫欄位、必要檢查與 commit/push 前流程。
