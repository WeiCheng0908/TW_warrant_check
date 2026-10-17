"""Flask API endpoint 測試。"""

import os
import sys
import unittest
from unittest import mock  # noqa: F401  # 用於 unittest.mock.* 的向下相容

# 確保可以 import app (從 backend 根目錄)
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import _create_app  # noqa: E402
from app import state  # noqa: E402


def _seed_state():
    state.stocks = [
        {"code": "2330", "name": "台積電", "full_name": "台灣積體電路製造股份有限公司"},
        {"code": "2317", "name": "鴻海", "full_name": "鴻海精密工業股份有限公司"},
    ]
    from warrant_finder.data import normalize_warrant
    from warrant_finder.dates import roc_to_date, today_taipei
    from datetime import timedelta

    today = today_taipei()
    today_roc = f"{today.year - 1911:03d}{today.month:02d}{today.day:02d}"
    yesterday = today - timedelta(days=1)
    y_roc = f"{yesterday.year - 1911:03d}{yesterday.month:02d}{yesterday.day:02d}"

    state.warrants = [
        normalize_warrant(
            {
                "權證代號": "03001T",
                "權證簡稱": "台積電元大5A購01",
                "認購售別": "認購",
                "標的證券/指數": "台積電",
                "標的證券代號": "2330",
                "履約開始日": today_roc,
                "最後交易日": "1151215",
                "履約價格": "1849.61",
                "發行單位": "5000",
                "備註": "備註 A",
            }
        ),
        normalize_warrant(
            {
                "權證代號": "03100T",
                "權證簡稱": "鴻海元大5A購01",
                "認購售別": "認購",
                "標的證券/指數": "鴻海",
                "標的證券代號": "2317",
                "履約開始日": y_roc,
                "最後交易日": "1151215",
                "履約價格": "210.00",
                "發行單位": "5000",
                "備註": "",
            }
        ),
    ]


class ApiTests(unittest.TestCase):
    def setUp(self):
        _seed_state()
        self.app = _create_app()
        self.client = self.app.test_client()

    def test_health(self):
        resp = self.client.get("/api/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["warrants"] == 2
        assert data["stocks"] == 2
        assert data["status"] in ("ok", "degraded")

    def test_stocks(self):
        resp = self.client.get("/api/stocks?q=台積")
        assert resp.status_code == 200
        data = resp.get_json()
        codes = [r["code"] for r in data["results"]]
        assert "2330" in codes

    def test_warrants_by_code(self):
        resp = self.client.get("/api/warrants?q=2330")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["matched_stock"]["code"] == "2330"
        assert data["count"] == 1
        assert data["results"][0]["code"] == "03001T"

    def test_warrants_by_name(self):
        resp = self.client.get("/api/warrants?q=台積電")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["matched_stock"]["code"] == "2330"
        assert data["count"] == 1

    def test_warrants_type_filter(self):
        resp = self.client.get("/api/warrants?q=2330&type=認售")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 0

    def test_warrants_new_only(self):
        resp = self.client.get("/api/warrants?q=2330&new_only=true")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 1

        resp2 = self.client.get("/api/warrants?q=2317&new_only=true")
        data2 = resp2.get_json()
        assert data2["count"] == 0

    def test_warrants_not_found(self):
        resp = self.client.get("/api/warrants?q=9999")
        assert resp.status_code == 404
        data = resp.get_json()
        assert "error" in data

    def test_warrants_missing_q(self):
        resp = self.client.get("/api/warrants")
        assert resp.status_code == 400
        data = resp.get_json()
        assert "error" in data

    # === /api/warrant/<code>/quote ===
    def test_quote_success(self):
        from warrant_finder import quote as quote_mod
        with unittest.mock.patch.object(
            quote_mod,
            "fetch_quote",
            return_value={
                "code": "030002",
                "name": "新巨台新64購01",
                "found": True,
                "last_price": 1.65,
                "prev_close": 1.60,
                "change": 0.05,
                "change_pct": 3.12,
                "cum_volume_lots": 12450,
                "cum_amount": 20542500.0,
                "asks": [1.66, 1.67, 1.68, 1.69, 1.70],
                "bids": [1.65, 1.64, 1.63, 1.62, 1.61],
                "market_open": True,
            },
        ):
            resp = self.client.get("/api/warrant/030002/quote")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["found"] is True
        assert data["last_price"] == 1.65
        assert data["cum_amount"] == 20542500.0

    def test_quote_not_found(self):
        from warrant_finder import quote as quote_mod
        with unittest.mock.patch.object(
            quote_mod,
            "fetch_quote",
            return_value={"code": "XXXXXX", "found": False, "error": "no quote"},
        ):
            resp = self.client.get("/api/warrant/XXXXXX/quote")
        assert resp.status_code == 404
        data = resp.get_json()
        assert data["found"] is False

    def test_quote_strips_t_suffix(self):
        from warrant_finder import quote as quote_mod
        with unittest.mock.patch.object(
            quote_mod,
            "fetch_quote",
            return_value={"code": "030002", "found": True, "last_price": 1.0},
        ) as m_fetch:
            resp = self.client.get("/api/warrant/03002T/quote")
        assert resp.status_code == 200
        # fetch_quote 收到的 code 應該是 normalization 後的 6 碼
        assert m_fetch.call_args.args[0] == "03002T"  # 端點路徑原樣傳入
