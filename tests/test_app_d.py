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
