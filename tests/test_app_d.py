"""針對 step D 新 endpoint 的 smoke tests。

用 Flask test_client（in-process），不走真實 HTTP server。
部分測試需要 ``data/warrants_otc.csv`` 與 ``data/stocks_otc.csv`` 在 cache 裡，
否則 results 為空但不應該 crash。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import app as app_mod


HERE = Path(__file__).resolve().parent.parent
DATA = HERE / "data"


@pytest.fixture(scope="module")
def client():
    flask_app = app_mod._create_app()
    flask_app.testing = True
    # preload state with whatever is on disk (會 graceful handle 缺失檔)
    flask_app.config["state"] = app_mod.state  # 已 module 層建好
    if not DATA.exists():
        DATA.mkdir(exist_ok=True)
    # In-memory test disk — 不實際下載；只測「空狀態」與「有資料」兩種下的 robustness。
    # 觸發 load_from_disk 把當前 on-disk 資料吃進來
    app_mod.state.load_from_disk()
    return flask_app.test_client()


def _have_data() -> bool:
    return (
        (DATA / "warrants_otc.csv").exists()
        and (DATA / "stocks_otc.csv").exists()
    )


# --------------------------------------------------------------------------- #
# /api/health — 應該 200
# --------------------------------------------------------------------------- #
def test_health_returns_200(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert "status" in payload
    assert "warrants" in payload
    assert "stocks" in payload


# --------------------------------------------------------------------------- #
# /api/warrants — 已升級成「含 OTC」
# --------------------------------------------------------------------------- #
def test_warrants_missing_q_returns_400(client):
    resp = client.get("/api/warrants")
    assert resp.status_code == 400
    assert resp.get_json().get("error") == "missing q"


def test_warrants_6187_includes_otc(client):
    """6187 萬潤是上櫃股，verify ``/api/warrants?q=6187`` 至少回 60 個認購。"""
    if not _have_data():
        pytest.skip("data/warrants_otc.csv 缺；先跑 demo 把 OTC 主檔抓下來")
    resp = client.get("/api/warrants?q=6187&type=認購")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["query"] == "6187"
    assert body["matched_stock"]["code"] == "6187"
    assert body["matched_stock"]["name"] == "萬潤"
    # SonarCue: 60 認購（end_date >= today）
    # 我們的 raw CSV 應該 >= 60；有些可能已 expired，所以放寬到 50
    assert body["count"] >= 50, f"got {body['count']} — 應該 >= 50"
    # 確認結果裡有 712218（SonarCue 排名第一的那檔）
    codes = {r["code"] for r in body["results"]}
    assert "712218" in codes


def test_warrants_market_filter(client):
    """``market=otc`` 應該只回上櫃，並非全部。"""
    if not _have_data():
        pytest.skip("data/warrants_otc.csv 缺")
    resp_all = client.get("/api/warrants?q=6187&type=認購")
    resp_otc = client.get("/api/warrants?q=6187&type=認購&market=otc")
    assert resp_all.status_code == 200
    assert resp_otc.status_code == 200
    body_all = resp_all.get_json()
    body_otc = resp_otc.get_json()
    assert body_otc["count"] <= body_all["count"]
    # OTC filter 結果的 market 都應該是 "otc"
    for r in body_otc["results"]:
        assert r.get("market") == "otc"


# --------------------------------------------------------------------------- #
# /api/warrants/screen — metadata-level 篩選，不發 quote
# --------------------------------------------------------------------------- #
def test_screen_missing_q_returns_400(client):
    resp = client.get("/api/warrants/screen")
    assert resp.status_code == 400


def test_screen_returns_enriched_filters(client):
    if not _have_data():
        pytest.skip("data/warrants_otc.csv 缺")
    resp = client.get("/api/warrants/screen?q=6187&min_days=30&limit=10")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["count"] <= 10
    assert body["filters"]["min_days"] == 30
    assert body["filters"]["limit"] == 10
    for r in body["results"]:
        assert "days_to_expiry" in r
        assert "years_to_expiry" in r
        assert r["days_to_expiry"] >= 30
        # 排序會以 days_to_expiry asc 為主（同天內 code asc）
    # 確認排序
    days = [r["days_to_expiry"] for r in body["results"]]
    assert days == sorted(days)


def test_screen_invalid_int_param_returns_400(client):
    resp = client.get("/api/warrants/screen?q=6187&min_days=foo")
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# /api/warrant/<code>/quote — 透過 exchange 顯式指定
# --------------------------------------------------------------------------- #
def test_quote_invalid_code_returns_400(client):
    resp = client.get("/api/warrant/  /quote")
    # 空 code 字串會被 strip 成空 → 400
    assert resp.status_code in (400, 404)


def test_quote_otc_explicit(client):
    """``exchange=otc`` 對 6187 應該拿到上櫃股的 prev_close (1390) 或 last。"""
    resp = client.get("/api/warrant/6187/quote?exchange=otc")
    assert resp.status_code in (200, 502), f"got {resp.status_code}"
    if resp.status_code == 200:
        body = resp.get_json()
        assert body["found"] is True
        # 至少有 prev_close 或 last_price
        assert (body.get("prev_close") or body.get("last_price"))


# --------------------------------------------------------------------------- #
# /api/warrant/<code>/iv — 計算 IV
# --------------------------------------------------------------------------- #
def test_iv_returns_structure(client):
    """6127 是上櫃 warrant，但若 cache 沒資料會 404 / 503。"""
    if not _have_data():
        pytest.skip("data/warrants_otc.csv 缺")
    resp = client.get("/api/warrant/712218/iv?underlying=6187")
    # 可能因為報價 API 暫時 fail 或不在交易時段；只要 status 是 200/503 之一即可。
    assert resp.status_code in (200, 404, 422, 503), f"got {resp.status_code}"
    if resp.status_code == 200:
        body = resp.get_json()
        assert "implied_volatility" in body
        assert "warrant" in body
        assert "underlying" in body
        assert "greeks" in body
        # IV 應該是 0~5.0 之間（合理範圍）
        iv = body["implied_volatility"]
        if iv is not None:
            assert 0 < iv < 5.0, f"IV out of range: {iv}"


def test_iv_invalid_warrant_code_returns_400(client):
    resp = client.get("/api/warrant/  /iv")
    assert resp.status_code in (400, 404)


def test_iv_warrant_not_in_cache_returns_404(client):
    resp = client.get("/api/warrant/00NOTREAL/iv")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# /screener — frontend HTML page
# --------------------------------------------------------------------------- #
def test_screener_returns_200_and_html(client):
    resp = client.get("/screener")
    assert resp.status_code == 200
    assert b"IV" in resp.data or b"html" in resp.data.lower()
    # 內容應該有 screener.js 引用
    assert b"screener.js" in resp.data or b"<script" in resp.data


def test_index_has_screener_link(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"/screener" in resp.data


# --------------------------------------------------------------------------- #
# /market — frontend HTML page
# --------------------------------------------------------------------------- #
def test_market_returns_200_and_html(client):
    resp = client.get("/market")
    assert resp.status_code == 200
    assert b"market.js" in resp.data or b"<script" in resp.data


# --------------------------------------------------------------------------- #
# /api/market/top-traded — 市場熱度排行
# --------------------------------------------------------------------------- #
def _have_otc_data() -> bool:
    return (
        (DATA / "warrants_otc.csv").exists()
        and (DATA / "warrants_trade_otc.csv").exists()
        and (DATA / "stocks_otc.csv").exists()
    )


def test_top_traded_otc_returns_sorted(client):
    if not _have_otc_data():
        pytest.skip("OTC warrants/trade/stocks 缺 — 需要先跑 demo 抓檔")
    resp = client.get("/api/market/top-traded?limit=10&market=otc")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["count"] >= 5, f"got {body['count']}, 應 >= 5"
    rows = body["results"]
    # total_amount desc
    amounts = [r["total_amount"] for r in rows]
    assert amounts == sorted(amounts, reverse=True)
    # 萬潤（6187）在市場熱度榜是有排名的（Demo case）
    codes = [r.get("stock_code") for r in rows]
    if 6187 in [int(c) if c and c.isdigit() else None for c in codes]:
        # 若上榜，必須有正確 stock_code 解析
        wanrun = next((r for r in rows if r.get("stock_code") == "6187"), None)
        assert wanrun is not None
        assert wanrun["stock_name"] == "萬潤"
        assert wanrun["market"] == "otc"
        assert wanrun["total_amount"] > 0


def test_top_traded_min_amount_filter(client):
    if not _have_otc_data():
        pytest.skip("OTC data 缺")
    resp = client.get("/api/market/top-traded?limit=100&market=otc&min_amount=100000000")
    assert resp.status_code == 200
    body = resp.get_json()
    for r in body["results"]:
        assert r["total_amount"] >= 100000000


def test_top_traded_invalid_limit_returns_400(client):
    resp = client.get("/api/market/top-traded?limit=abc")
    assert resp.status_code == 400


def test_top_traded_includes_proxy_note(client):
    if not _have_otc_data():
        pytest.skip("OTC data 缺")
    resp = client.get("/api/market/top-traded?limit=3&market=otc")
    body = resp.get_json()
    assert "proxy_note" in body
    assert "權證" in body["proxy_note"]
    assert "波動" in body["proxy_note"]


# --------------------------------------------------------------------------- #
# Regression：TWSE 在 2026 年中把 t187ap37_L 從 JSON 改 CSV，
# data._read 必須能兩個格式都吃。
# --------------------------------------------------------------------------- #
class TestListedWarrantFormatAutoDetect:
    """TWSE 2026 年中把 ``t187ap37_L`` 從 JSON 改回 CSV；data._read 要能兩種都吃。"""

    def test_json_still_works(self, tmp_path):
        """舊 JSON 必須繼續能被讀取。"""
        from warrant_finder import data
        import json
        f = tmp_path / "warrants.json"
        f.write_bytes(json.dumps([
            {"出表日期": "1151016", "權證代號": "030001", "權證簡稱": "友達台新6A購01",
             "權證類型": "認購", "類別": "一般型", "標的證券/指數": "友達",
             "最新履約價格(元)/履約指數": "53.0000",
             "履約開始日": "1151005", "最後交易日": "1160930", "備註": ""},
        ], ensure_ascii=False).encode("utf-8"))
        rows = data._read(str(f))
        assert len(rows) == 1
        assert rows[0]["權證代號"] == "030001"
        assert rows[0]["標的證券/指數"] == "友達"

    def test_csv_still_works(self, tmp_path):
        """新 CSV（TWSE 改版後）必須能讀。"""
        from warrant_finder import data
        f = tmp_path / "warrants_L.csv"
        f.write_bytes((
            '﻿出表日期,權證代號,權證簡稱,權證類型,類別,標的證券/指數,'
            '最新履約價格(元)/履約指數,履約開始日,最後交易日,備註\n'
            '"1151016","030001","友達台新6A購01","認購","一般型","友達",'
            '"53.0000","1151005","1160930",""\n'
        ).encode("utf-8"))
        rows = data._read(str(f))
        assert len(rows) == 1
        assert rows[0]["權證代號"] == "030001"
        assert rows[0]["標的證券/指數"] == "友達"

    def test_csv_with_embedded_crlf_in_notes(self, tmp_path):
        """CSV 不會被「\\r\\n 在 備註 裡」炸掉（這就是 TWSE 改 CSV 的主因）。"""
        from warrant_finder import data
        f = tmp_path / "warrants_L.csv"
        f.write_bytes((
            '﻿權證代號,權證簡稱,備註\n'
            '"030001","友達","line1\r\nline2"\n'
        ).encode("utf-8"))
        rows = data._read(str(f))
        assert len(rows) == 1
        assert "line1" in rows[0]["備註"]
        assert "line2" in rows[0]["備註"]

    def test_real_warrants_file_loads(self):
        """實際下載的 warrants.json（或更新後的 CSV）必須能 load_normalized 成功。"""
        from warrant_finder import data
        p = DATA / "warrants.json"
        if not p.exists():
            pytest.skip("warrants.json 不存在")
        warrs = data.load_normalized(str(p))
        assert len(warrs) > 1000
        # 第一筆要有基本欄位
        assert warrs[0].get("code")
        assert warrs[0].get("underlying")


# --------------------------------------------------------------------------- #
# market_activity 純函式測試（不發 API）
# --------------------------------------------------------------------------- #
class TestMarketActivityAggregation:
    """確保 aggregate_warrant_activity 排序 / 加總 / 名稱 mapping 邏輯正確。"""

    def _setup(self):
        """兩個權證 + 一個 stock，三條 trade rows。"""
        warrs = [
            {"code": "700001", "underlying": "ABC", "market": "otc"},
            {"code": "700002", "underlying": "ABC", "market": "otc"},
            {"code": "700003", "underlying": "XYZ", "market": "otc"},
        ]
        trades = [
            {"code": "700001", "amount": 100.0, "volume": 10.0},
            {"code": "700002", "amount": 200.0, "volume": 20.0},
            {"code": "700003", "amount": 50.0, "volume": 5.0},
        ]
        return warrs, trades

    def test_aggregation_basic_sum(self):
        from warrant_finder import market_activity
        warrs, trades = self._setup()
        out = market_activity.aggregate_warrant_activity(warrs, trades)
        out_dict = {r["stock_name"]: r for r in out}
        assert out_dict["ABC"]["total_amount"] == 300.0
        assert out_dict["ABC"]["total_volume"] == 30.0
        assert out_dict["ABC"]["warrant_count"] == 2
        assert out_dict["XYZ"]["total_amount"] == 50.0

    def test_aggregation_sort_desc(self):
        from warrant_finder import market_activity
        warrs, trades = self._setup()
        out = market_activity.aggregate_warrant_activity(warrs, trades)
        amounts = [r["total_amount"] for r in out]
        assert amounts == sorted(amounts, reverse=True)

    def test_aggregation_skips_unknown_codes(self):
        """不在 master 的 warrant code 應該跳過，不會汙染 group。"""
        from warrant_finder import market_activity
        warrs = [{"code": "700001", "underlying": "ABC", "market": "otc"}]
        trades = [
            {"code": "700001", "amount": 100.0, "volume": 10.0},
            {"code": "999999", "amount": 9999.0, "volume": 999.0},  # 不在 master
        ]
        out = market_activity.aggregate_warrant_activity(warrs, trades)
        assert len(out) == 1
        assert out[0]["stock_name"] == "ABC"
        assert out[0]["total_amount"] == 100.0

    def test_top_n_respects_limit(self):
        from warrant_finder import market_activity
        warrs = [
            {"code": f"7000{i:02d}", "underlying": f"S{i}", "market": "otc"}
            for i in range(5)
        ]
        trades = [
            {"code": f"7000{i:02d}", "amount": float(100 - i), "volume": float(i + 1)}
            for i in range(5)
        ]
        out = market_activity.top_stocks_by_warrant_trading(warrs, trades, limit=3)
        assert len(out) == 3
        assert out[0]["stock_name"] == "S0"
        assert out[2]["total_amount"] == 98.0

    def test_min_amount_filter(self):
        from warrant_finder import market_activity
        warrs = [
            {"code": "7000A", "underlying": "Big", "market": "otc"},
            {"code": "7000B", "underlying": "Small", "market": "otc"},
        ]
        trades = [
            {"code": "7000A", "amount": 1000.0, "volume": 10.0},
            {"code": "7000B", "amount": 5.0, "volume": 1.0},
        ]
        out = market_activity.top_stocks_by_warrant_trading(
            warrs, trades, limit=10, min_total_amount=100
        )
        names = {r["stock_name"] for r in out}
        assert "Big" in names
        assert "Small" not in names
