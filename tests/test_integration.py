"""整合測試：用真正的下載資料驗證搜尋。

預設跳過；只有當 ``RUN_INTEGRATION=1`` 且 ``data/`` 有快取時才執行。
"""

import os

import pytest

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
WARRANTS = os.path.join(DATA_DIR, "warrants.json")
WARRANTS_TRADE = os.path.join(DATA_DIR, "warrants_trade.json")
STOCKS = os.path.join(DATA_DIR, "stocks.json")


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1"
    or not all(os.path.exists(p) for p in (WARRANTS, WARRANTS_TRADE, STOCKS)),
    reason="integration test disabled (set RUN_INTEGRATION=1 with data/ present)",
)


def test_real_data_loads():
    from warrant_finder.data import load_normalized
    from warrant_finder.stock_map import load_stock_map

    stocks = load_stock_map(STOCKS)
    warrants = load_normalized(WARRANTS, WARRANTS_TRADE)
    assert len(stocks) > 100
    assert len(warrants) > 100


def test_real_data_search_2330():
    from warrant_finder.data import load_normalized
    from warrant_finder.search import search_warrants
    from warrant_finder.stock_map import load_stock_map

    stocks = load_stock_map(STOCKS)
    warrants = load_normalized(WARRANTS, WARRANTS_TRADE)
    matched, results = search_warrants("2330", warrants, stocks)
    assert matched["code"] == "2330"
    assert len(results) > 50  # 2330 通常有 1k+ 個權證


def test_real_data_new_only_today():
    from warrant_finder.data import load_normalized
    from warrant_finder.dates import today_taipei
    from warrant_finder.search import search_warrants
    from warrant_finder.stock_map import load_stock_map

    stocks = load_stock_map(STOCKS)
    warrants = load_normalized(WARRANTS, WARRANTS_TRADE)
    matched, results = search_warrants("2330", warrants, stocks, new_only=True)
    assert matched["code"] == "2330"
    today = today_taipei().isoformat()
    for r in results:
        assert r["start_date"] == today
