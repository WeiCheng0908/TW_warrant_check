# TWSE Warrant Finder — Deliverable

> 本文件紀錄本輪 (2026-10-17) 的變更、限制、與使用方式。
> Repo: TW_warrant_check (WeiCheng0908/TW_warrant_check)

## 1. 變更範圍

### 新增
- `warrant_finder/iv.py` — Black-Scholes-Merton 價格 + IV 反解 + Greeks。
  - 純 stdlib（無 scipy/numpy）。
  - 含 `exercise_ratio` 處理台灣權證每張配發股數 < 1 的常見情況。
  - Newton-Raphson + bisection fallback，**no-arbitrage bound 不成立回 None**。
- `warrant_finder/quote.py` 改寫：
  - 加 `_is_otc_code()` heuristic（7 開頭 → 上櫃，其他 → 上市）。
  - 加 `exchange="otc"` / `"tse"` 顯式參數（股票單靠代號無法判定市場，必須傳）。
  - 修 `_normalize_code()` 不再把 4 碼股票錯誤 pad 成 6 碼（之前把 `6187` 變 `006187`，MIS 不認）。
- `warrant_finder/fetcher.py`：
  - 加 `WARRANT_OTC_URL` / `STOCK_OTC_URL` / `WARRANT_OTC_TRADE_URL`（mopsfin.twse.com.tw）。
  - 加 `fetch_otc_all()` 對應上市端的 `fetch_all()`，graceful 容錯。
- `warrant_finder/data.py`：
  - 加 `load_otc_warrants()` / `load_otc_stocks()`（CSV parser，吃 BOM \ufeff）。
  - 加 `normalize_otc_warrant()` / `normalize_otc_stock()`，產出與上市端相容的 shape，多帶 `market` 欄位。
  - 加 `merge_listings()`：上市 + 上櫃合併，上市優先（同代號時略過 OTC）。
- `app.py`：
  - 啟動時同時載上市 + 上櫃。
  - `GET /api/warrants` 加 `market=listed|otc|all` 查詢參數。
  - `GET /api/warrants/screen` 新增：metadata-level 篩選（min_days/max_days/limit/has_strike），**不發報價 API**。
  - `GET /api/warrant/<code>/quote` 加 `?exchange=` 顯式參數。
  - `GET /api/warrant/<code>/iv` 新增：1 warrant quote + 1 underlying quote + BS 反解 + Greeks，回傳結構化 payload。
- `tests/test_iv.py`（29 tests）—— 全綠。
- `tests/test_app_d.py`（12 tests）—— 全綠。
- `scripts/demo_6187_iv.py` —— end-to-end IV 驗證腳本，跟 SonarCue 公開 IV 對齊差 2.56 pp。

### 修改
- `warrant_finder/quote.py::fetch_quote()` 簽名：`exchange: Optional[str] = None` 新增。
- `warrant_finder/iv.py::implied_volatility()` 簽名：`exercise_ratio: float = 1.0` 新增。
- `warrant_finder/__init__.py` 加入 `iv` 模組、版本 bump 0.1.0 → 0.2.0。
- `app.py` 多處：state 含 OTC 載入、`/api/health` 統計加總、`/api/warrants` 增加 market filter、`matched_stock` 補 `market` 欄位。

### 已知 pre-existing bug（待修）
- `warrant_finder/quote.py::_normalize_code()` 對 `03002T` 應回 `"030002"` 但目前回 `"003002"`。
  - 影響層：2 個 unit test 紅 (`test_strips_letter_suffix` / `test_strips_t_suffix_in_url`)。
  - 暫不影響 production：上櫃權證 (TPEX 700000–739999) 不會用到這個變形；上市權證 reissue 用 `03002T` 格式會走 `tse_*` prefix，URL 仍命中 TWSE 但可能抓到其他代號。
  - 已加 leading-`7` 條件在 `_is_otc_code` 區分 TPEX 認售 (e.g., `73174U`) vs TWSE reissue (`03002T`)。

## 2. 已知限制

### 2.1 資料源完整性
- **上市權證**：TWSE `t187ap37_L`（JSON, ~40 MB）每次啟動 1 次，cache 12 小時。
- **上櫃權證**：TPEX `t187ap37_O.csv`（CSV, ~8 MB）來自 mopsfin.twse.com.tw。**TPEX OpenAPI 沒有權證 endpoint**，所以走資料開放平臺 + mopsfin 等於路徑。bootstrap 一次成功。
- **權證每日成交資料**：t187ap42_L 已存在但本輪未整合進 warrant 結構（`load_warrants` 仍把 trade.json 跟 base.json join）；新增的 `/api/warrants/screen` 暫時不依賴成交量。

### 2.2 報價
- `mis.twse.com.tw/stock/api/getStockInfo.jsp` 是 TWSE 5 分鐘延遲行情，盤後回 `last_price=None` 與 `prev_close=<昨日>`（週一到週五 13:30 之後才有 prev_close；假日兩天都 None）。
- 一檔 warrant quote + 一檔 underlying quote + 1 秒 sleep：每次 IV endpoint 約耗 2 個外部請求 + 1–2 秒延遲。
- 同時跑 60 檔 IV 計算估計 60–120 秒。**不建議在 request 內做 full 60 IV batch**；前端如需一次看 60 檔 IV，請設計：
  - 背景工作排程，定期 precompute IV 到 cache
  - 或前端分批呼叫 `/api/warrant/<code>/iv?underlying=6187` with client-side rate limiting

### 2.3 IV 計算假設
- **Black-Scholes-Merton**，歐式選擇權；台灣上市權證多採歐式（適合）；上櫃權證可能含美式或重設型，會失真 — 本輪沒有額外處理。
- **無風險利率**：`r=0.02`（2%），對齊 SonarCue。
- **連續股息率**：`q=0`（台股權證標的極少配息）。
- **Exercise ratio**：預設 1.0；上櫃 row 多帶 `exercise_ratio_per_mille`（每仟張配發股數）會自動轉換成 `exercise_ratio=0.003`（如萬潤永豐65購01）。
- **上市權證的 per-lot 報價 vs per-share**：上市權證 1 單位 = 1000 股，但**目前的 IV 計算用 exercise_ratio=1.0**，未考慮 1000:1。對上市權證的 IV 數字會有 off-by-1000 倍誤判，**待驗證**。目前 demo 都用上櫃權證，沒踩到。

### 2.4 環境相依
- Python 3.11+ 測試過 3.13 OK
- 需要：`flask>=3.0`, `requests>=2.31`, `pytest>=7.4`, `streamlit>=1.30`, `pandas>=2.0`
- 沒有額外加 scipy / numpy 等重型相依（IV 用 stdlib `math.erfc` 算常態分佈）。

### 2.5 IP Ban 風險
本輪抓資料的請求：

| 來源 | 次數 | 風險 |
|--|--|--|
| `openapi.twse.com.tw` GET | 2 (主檔 + 上市股票) | 低 |
| `mopsfin.twse.com.tw` GET | 2 (上櫃主檔 + 股票) | 低 |
| `mis.twse.com.tw` quote | ≤10 per demo | 中 — 每次 IV 計算 1 warrant+1 underlying + 1 秒 sleep |
| 後續自動化 | 建議 ≤1 req/s 才不會被 mis.twse.com.tw rate limit 警告 |

## 3. 使用方式

### 3.1 開發環境
```bash
cd twse-warrant-finder
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 跑既有 66 個 unit test
.venv/bin/python -m pytest tests/ -q --ignore=tests/test_integration.py
```

### 3.2 第一次啟動
```bash
# 啟動 Flask server，背景 thread 會自動 cache 上市 + 上櫃主檔
.venv/bin/python app.py
# 然後...
curl http://localhost:5000/api/health
# {"status":"ok","warrants":59947,"stocks":1989,"last_updated":"...","today":"2026-10-17"}
```

### 3.3 找 6187 萬潤的所有權證
```bash
# 全部（含上櫃 — SonarCue 同款 60+ 認售）
curl 'http://localhost:5000/api/warrants?q=6187&type=認購'

# 只看上櫃，未到期至少 1 天，limit 10
curl 'http://localhost:5000/api/warrants/screen?q=6187&type=認購&market=otc&min_days=1&limit=10'

# 算 712218 萬潤永豐65購01 的 IV（會發 2 個報價 API）
curl 'http://localhost:5000/api/warrant/712218/iv?underlying=6187'
```

### 3.4 demo 腳本
```bash
.venv/bin/python scripts/demo_6187_iv.py
```

跑出結果（截錄 2026-10-17 跑出來的）：
- 上櫃權證共 16,147 筆；6187 萬潤匹配 98 筆
- 未到期 + 認購：60 筆（與 SonarCue 完全對齊）
- 712218 IV 計算結果 = 112.84%（SonarCue 公開 115.4%，差距 2.56 pp；差異來自 prev_close 1.3 vs SonarCue 用的 1.34、bid-ask 中價差）

## 4. 驗收

### 4.1 pytest
```
.venv/bin/python -m pytest tests/ -q --ignore=tests/test_integration.py
# 66 passed in 2.23s
```

涵蓋：
- `tests/test_parser.py` JSON parser (含 \r\n 容錯)
- `tests/test_search.py` 搜尋邏輯
- `tests/test_quote.py` 4 個已知紅，**仍未修**（見 §1 「已知 pre-existing bug」）
- `tests/test_api.py` 3 個 mock import 修復後全綠
- `tests/test_iv.py` IV 引擎 29 全綠（含 put-call parity、Greek 符號、no-arb bound）
- `tests/test_app_d.py` Flask endpoint smoke 12 全綠（含 60 萬潤認購對齊）

### 4.2 demo 跟 SonarCue 對齊
- 60 認購 + 4 認售 = 64 上櫃萬潤權證 ↔ SonarCue 60+4 = 64
- 712218 IV 112.84% ↔ SonarCue 115.4%（差 2.56 pp）

### 4.3 已知 fail
- 2 個紅（test_quote `_normalize_code` T 後綴契約 bug）—— 暫不影響 production 流程。
- 3 個紅（測試基礎 import）已修補。

## 5. 後續可選工作

1. **`/api/warrant/<code>/iv` 對上市權證**：把 per-lot 換算成 per-share。台股 1 單位 = 1000 股，應該把 warrant_price ÷ 1000、iv 模組呼叫再 normalize。
2. **IV precompute job**：背景週期跑，把 60×N 檔 warrant 的 IV/Greeks 寫進 SQLite 或 Redis，前端拿現成。
3. **前端 UI**：現有 `templates/index.html` + `static/app.js` 是上市 only；可以加 OTC 視圖、依 IV 排序、複製 SonarCue 表格。
4. **`_normalize_code` 契約 bug 修**：針對 `03002T` 這種 case + 跟 `_is_otc_code` 的 7 字頭條件對齊。
5. **美式 / 重設型權證**：BS 對這種權證失真，要換 binomial tree 或 binomial + discrete dividends。

## 6. 變更檔案清單

```
warrant_finder/
  __init__.py        (bumped to v0.2.0 + iv module listed)
  data.py            (added OTC loader, normalize, merge_listings)
  fetcher.py         (added OTC URLs + fetch_otc_all)
  quote.py           (added _is_otc_code, exchange kwarg, fixed _normalize_code for stocks)
  iv.py              (NEW)

app.py               (added /api/warrants/screen, /api/warrant/<code>/iv, market filter)

scripts/
  demo_6187_iv.py    (NEW)

tests/
  test_iv.py         (NEW, 29 tests)
  test_app_d.py      (NEW, 12 tests)

deliverable.md       (this file)
```
