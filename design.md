# TWSE 權證查詢系統 — 設計文件

> 本文件由 owner 撰寫，定義 API contract，供 backend / frontend 兩個 worker 平行開發時共用。
> 最後更新：2026-10-16

## 目標

做一個本地可跑的 web app，使用者輸入台股的代號（如 `2330`）或名稱（如 `台積電`），
顯示對應的全部**上市權證**清單。

## 資料源

兩個 TWSE 公開 OpenAPI 端點（皆為 JSON，無需 API key）：

| 用途 | Endpoint | 角色 |
|------|----------|------|
| 上市權證基本資料彙總表（母檔） | `GET https://openapi.twse.com.tw/v1/opendata/t187ap37_L` | 主資料，每檔權證一筆 |
| 上市認購(售)權證每日成交資料檔 | `GET https://openapi.twse.com.tw/v1/opendata/t187ap42_L` | 補強欄位（成交金額、收盤價等） |

**已知陷阱**：
1. 母檔 JSON 內有「備註」欄位含真實 `\r\n` 換行（非合法 JSON），parser 須用 `strict=False` 或事先清洗。
2. 檔案約 7 MB，下載可能逾時 → 須支援 resumable / streaming 解析。
3. `標的證券/指數` 欄位是**名稱**（如「台積電」），不是代號。要支援使用者輸入代號查詢，須另抓一份代號↔名稱對照表。
4. 日期格式為 ROC 民國年月日，例 `1151015` = 2026/10/15。
5. `履約開始日` 才是真正的「新發行日」；`出表日期` 是這份資料被產出的當天。

## 系統架構

```
twse-warrant-finder/
├── app.py                  # Flask app: 提供 /api/* + 靜態前端
├── warrant_finder/
│   ├── __init__.py
│   ├── fetcher.py          # 抓 /opendata/t187ap37_L 與 /t187ap42_L，落地 cache
│   ├── stock_map.py        # 代號↔名稱對照表 (從 t187ap03_L 上市公司基本資料抓)
│   ├── parser.py           # 寬鬆 JSON 解析 (strict=False + 控制字元清洗)
│   ├── data.py             # 載入、合併、快取管理
│   └── search.py           # 查詢邏輯：給定輸入，回傳符合的權證清單
├── templates/
│   └── index.html          # 主頁
├── static/
│   ├── app.js
│   └── style.css
├── data/                   # 落地快取 (git-ignored)
│   ├── warrants.json
│   ├── warrants_trade.json
│   └── stocks.json
├── tests/
│   ├── test_parser.py
│   ├── test_search.py
│   └── test_api.py
├── requirements.txt
└── README.md
```

## API Contract

Base URL: `http://localhost:5000`

### `GET /api/health`
回傳 `{ "status": "ok", "warrants": 2345, "stocks": 1023, "last_updated": "..." }`

### `GET /api/stocks?q=台積`
回傳股票清單（給前端 autocomplete 用）：
```json
{ "results": [ { "code": "2330", "name": "台積電" }, ... ] }
```

### `GET /api/warrants?q=2330&type=認購&new_only=true`

- `q` 必填。可以是代號（`2330`）、名稱（`台積電`）、或部分字串（`台積`）。大小寫不敏感。
- `type` 可選，`認購` / `認售`。
- `new_only` 可選，`true` 時只回傳「履約開始日」== 今日的權證。
- 回傳：
```json
{
  "query": "2330",
  "matched_stock": { "code": "2330", "name": "台積電" },
  "count": 12,
  "results": [
    {
      "code": "03002T",
      "name": "台積電群益5A售12",
      "type": "認售",
      "category": "一般型",
      "underlying": "台積電",
      "start_date": "2026-10-16",
      "last_trade_date": "2026-10-14",
      "strike_price": 1849.61,
      "issue_size": 5000,
      "traded_today": {                  // 若 t187ap42_L 有當日資料
        "close": 1.23,
        "volume": 50000,
        "amount": 61500
      },
      "notes": "..."
    }
  ]
}
```

錯誤：找不到標的 → `404 { "error": "stock not found: 9999" }`。

### `GET /` 
回傳前端 HTML。

## 前端 UX

- 單頁、極簡。
- 頂部一個搜尋框：placeholder 寫「輸入股票代號或名稱（例：2330 / 台積電）」
- 輸入時即時顯示 autocomplete 建議（debounce 200ms）。
- 點選或 Enter 後，下方顯示該標的的權證清單（卡片或表格皆可）。
- 表格欄位：權證代號 / 簡稱 / 認購/認售 / 履約價 / 最後交易日 / 備註摘要。
- 篩選器：勾選「只看今天新發行」「只看認購」「只看認售」。
- 底部顯示資料更新時間與來源（TWSE OpenAPI）。

## 環境與相依

- Python 3.11+
- Flask
- requests
- pytest（測試）

`requirements.txt`：
```
flask>=3.0
requests>=2.31
pytest>=7.4
```

## 驗收標準（給 Verifier 用）

1. `python app.py` 啟動後，訪問 `http://localhost:5000/` 看到前端。
2. `GET /api/health` 回 200 且有 `warrants > 0`。
3. 搜尋 `2330` 或 `台積電` 都能找到對應權證。
4. 搜尋不存在的代號回 404。
5. 篩選 `new_only=true` 只顯示今天新發行的。
6. 解析器能處理 `\r\n` 在字串內的壞 JSON，不 crash。
7. 全部 pytest 通過。

## 注意事項

- 不要把 secrets 寫進程式碼。
- 不要 deploy 任何服務到外部（owner 自己決定）。
- 不要寫寫超過 30 分鐘。
- 完成後在 `deliverable.md` 寫下：
  - 改了哪些檔
  - 怎麼跑
  - pytest 輸出
  - curl 範例
  - 任何已知問題
