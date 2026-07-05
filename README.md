# 聲聞地 HTML

這個 repo 會從 CBETA API 下載《瑜伽師地論》〈聲聞地〉，並依 CBETA 目次切成可閱讀的 HTML 檔。

輸出位置：

- `html/index.html`：章節索引
- `html/sections/*.html`：各章節 HTML
- `data/*.json`：下載時保留的 CBETA API 原始回應

使用方式：

```sh
python3 scripts/build_shengwen_di_html.py
```

資料來源：

- CBETA 目次 API：`https://cbdata.dila.edu.tw/stable/works/toc?work=T1579`
- CBETA 卷文 API：`https://cbdata.dila.edu.tw/stable/juans?work=T1579&juan=...&toc=1&work_info=1`

