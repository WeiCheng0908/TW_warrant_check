# TWSE Warrant Finder — Backend

抓 TWSE 公開 OpenAPI 的上市權證資料，提供本地 Flask API。
前端 (HTML / JS) 由另一個 worker 維護；本 repo 只放後端。

## 資料源

| 用途 | Endpoint |
|------|----------|
| 上市權證基本資料 (主資料, ~40 MB) | `https://openapi.twse.com.tw/v1/opendata/t187ap37_L` |
| 上市權證每日成交 | `https://openapi.twse.com.tw/v1/opendata/t187ap42_L` |
| 上市公司基本資料 (代號↔名稱) | `https://openapi.twse.com.tw/v1/opendata/t187ap03_L` |

## 環境

- Python 3.11+
- Flask / requests / pytest

## 安裝與啟動

```bash
cd twse-warrant-finder
pip install -r requirements.txt
python app.py            # Flask 預設 0.0.0.0:5000
```

第一次啟動會在背景 thread 嘗試下載三個 JSON 端點並落地到 `data/`。
若網路失敗或快取已過期超過 12 小時會自動重抓。

## 環境變數

| 變數 | 預設 | 說明 |
|------|------|------|
| `PORT` | `5000` | Flask listen port |
| `RUN_INTEGRATION` | - | 設為 `1` 時會跑需要 `data/` 內快取的整合測試 |

## API

### `GET /api/health`
```json
{ "status": "ok", "warrants": 43357, "stocks": 1095,
  "last_updated": "2026-10-16T03:19:48", "today": "2026-10-16" }
```

### `GET /api/stocks?q=台積`
```json
{ "results": [ { "code": "2330", "name": "台積電" }, ... ] }
```

### `GET /api/warrants?q=2330&type=認售&new_only=true`
```json
{
  "query": "2330",
  "matched_stock": { "code": "2330", "name": "台積電" },
  "count": 184,
  "results": [
    { "code": "03002T", "name": "...", "type": "認售",
      "underlying": "台積電", "start_date": "2026-10-16",
      "strike_price": 1849.61, "issue_size": 5000.0,
      "traded_today": { "amount": 280730.0, "volume_lots": 107000,
                         "trade_date": "1151015" },
      "notes": "..." }
  ]
}
```

錯誤：
- `400 { "error": "missing q" }` — 未帶 `q` 參數
- `404 { "error": "stock not found: 9999" }` — 找不到對應標的且無 fuzzy 命中
- `503 { "error": "warrants data not ready" }` — 背景 fetcher 還沒完成且無快取

## 模組

```
warrant_finder/
  parser.py      # 寬鬆 JSON: 控制字元清洗 + 逐 object 解析
  fetcher.py     # streaming 下載、retry、graceful 過期更新
  stock_map.py   # t187ap03_L → 代號↔名稱
  data.py        # 載入 / join / 標準化
  search.py      # 查詢邏輯 (代號/名稱/子字串, type, new_only)
  dates.py       # ROC 民國年月日 ↔ ISO
app.py           # Flask app
tests/           # 22 unit + 3 integration
```

## 開發指令

```bash
# 只跑單元測試 (不需要 network / data/)
python -m pytest tests/ -q

# 含整合測試 (需要 RUN_INTEGRATION=1 與 data/ 已下載)
RUN_INTEGRATION=1 python -m pytest tests/ -q

# 強制重新抓資料
python -m warrant_finder.fetcher --data-dir data --force
```

## 已知陷阱

1. `備註` 欄位含真實 `\r\n` — parser 使用 `json.loads(strict=False)` 並先清洗控制字元。
2. 主檔 ~40 MB，下載逾時常見 — fetcher 預設 60s timeout × 3 retry；對大檔可手動 `python -c "from warrant_finder.fetcher import fetch_to_file; fetch_to_file('https://openapi.twse.com.tw/v1/opendata/t187ap37_L', 'data/warrants.json', timeout=180)"`。
3. 設計文件寫的欄位名 (`認購售別` / `履約價格`) 與實際 API 欄位 (`權證類型` / `最新履約價格(元)/履約指數`) 不同；`data.normalize_warrant` 同時容忍兩種。
4. t187ap42_L 的「成交」檔只含 `成交金額` 與 `成交張數`，不含收盤價；規格裡的 `close` 欄位在 `traded_today` 內不會出現。
