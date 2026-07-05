# 聲聞地 HTML

這個 repo 會從 CBETA API 下載《瑜伽師地論》〈聲聞地〉，並依 CBETA 目次切成可閱讀的 HTML 檔。

輸出位置：

- `html/index.html`：章節索引
- `html/sections/*.html`：各章節 HTML
- `html/translations/T1579-033-baihua.html`：卷第三十三白話左右對照翻譯
- `data/*.json`：下載時保留的 CBETA API 原始回應
- `translations/*.md`：白話翻譯來源稿

使用方式：

```sh
python3 scripts/build_shengwen_di_html.py
python3 scripts/build_translation_html.py
```

資料來源：

- CBETA 目次 API：`https://cbdata.dila.edu.tw/stable/works/toc?work=T1579`
- CBETA 卷文 API：`https://cbdata.dila.edu.tw/stable/juans?work=T1579&juan=...&toc=1&work_info=1`
