"""測試 ``warrant_finder.quote`` 的解析邏輯（不實際打 API）。"""

from __future__ import annotations

import json
from unittest import mock

import pytest

from warrant_finder import quote


# --------------------------------------------------------------------------- #
# 假 API 回傳
# --------------------------------------------------------------------------- #
SAMPLE_MARKET_OPEN = {
    "msgArray": [
        {
            "c": "030002",
            "n": "新巨台新64購01",
            "nf": "新巨台新64購01  －新巨            20270407美購",
            "d": "20261016",
            "t": "10:30:00",
            "z": "1.65",
            "y": "1.60",
            "o": "1.62",
            "h": "1.70",
            "l": "1.58",
            "u": "2.68",
            "w": "0.51",
            "v": "523",
            "tv": "12450",
            "a": "1.66_1.67_1.68_1.69_1.70_",
            "b": "1.65_1.64_1.63_1.62_1.61_",
        }
    ]
}

SAMPLE_MARKET_CLOSED = {
    "msgArray": [
        {
            "c": "030002",
            "n": "新巨台新64購01",
            "nf": "新巨台新64購01  －新巨            20270407美購",
            "d": "20261016",
            "t": "13:30:00",
            "z": "-",
            "y": "1.60",
            "o": "0",
            "v": "0",
            "tv": "-",
            "a": "",
            "b": "",
        }
    ]
}

SAMPLE_EMPTY = {"msgArray": []}


# --------------------------------------------------------------------------- #
# _normalize_code
# --------------------------------------------------------------------------- #
class TestNormalizeCode:
    def test_strips_letter_suffix(self):
        assert quote._normalize_code("03002T") == "030002"

    def test_already_digits(self):
        assert quote._normalize_code("030002") == "030002"

    def test_zfill_short(self):
        assert quote._normalize_code("3002") == "003002"

    def test_empty_returns_empty(self):
        assert quote._normalize_code("") == ""
        assert quote._normalize_code(None) == ""  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# _split_prices
# --------------------------------------------------------------------------- #
class TestSplitPrices:
    def test_normal(self):
        assert quote._split_prices("1.6_0.73_") == [1.6, 0.73]

    def test_dash_for_missing(self):
        assert quote._split_prices("1.6_-_") == [1.6, None, None]

    def test_empty(self):
        assert quote._split_prices("") == [None]
        assert quote._split_prices("_") == [None, None]


# --------------------------------------------------------------------------- #
# _parse_quote_response
# --------------------------------------------------------------------------- #
class TestParseQuoteResponse:
    def test_market_open(self):
        out = quote._parse_quote_response(SAMPLE_MARKET_OPEN, code="030002")
        assert out["found"] is True
        assert out["code"] == "030002"
        assert out["market_open"] is True
        assert out["last_price"] == 1.65
        assert out["prev_close"] == 1.60
        assert out["change"] == pytest.approx(0.05)
        assert out["change_pct"] == pytest.approx(3.12)
        assert out["open"] == 1.62
        assert out["high"] == 1.70
        assert out["low"] == 1.58
        assert out["cum_volume_lots"] == 12450
        # 12450 張 × 1000 股 × 1.65 元 = 20,542,500
        assert out["cum_amount"] == 20542500.0
        assert out["asks"] == [1.66, 1.67, 1.68, 1.69, 1.70]
        assert out["bids"] == [1.65, 1.64, 1.63, 1.62, 1.61]
        assert out["up_limit"] == 2.68
        assert out["down_limit"] == 0.51

    def test_market_closed(self):
        out = quote._parse_quote_response(SAMPLE_MARKET_CLOSED, code="030002")
        assert out["found"] is True
        assert out["market_open"] is False
        assert out["last_price"] is None
        assert out["change"] is None
        assert out["cum_volume_lots"] is None
        assert out["cum_amount"] is None
        assert out["asks"] == []
        assert out["bids"] == []

    def test_empty_msg_array(self):
        out = quote._parse_quote_response(SAMPLE_EMPTY, code="030002")
        assert out["found"] is False
        assert "no quote" in out.get("error", "")

    def test_garbage_payload(self):
        out = quote._parse_quote_response("not a dict", code="030002")
        assert out["found"] is False


# --------------------------------------------------------------------------- #
# fetch_quote (mocked network)
# --------------------------------------------------------------------------- #
class TestFetchQuote:
    def test_success(self):
        fake_resp = mock.Mock()
        fake_resp.json.return_value = SAMPLE_MARKET_OPEN
        fake_resp.raise_for_status = mock.Mock()
        with mock.patch.object(quote.requests, "get", return_value=fake_resp):
            out = quote.fetch_quote("030002")
        assert out["found"] is True
        assert out["last_price"] == 1.65

    def test_strips_t_suffix_in_url(self):
        fake_resp = mock.Mock()
        fake_resp.json.return_value = SAMPLE_MARKET_OPEN
        fake_resp.raise_for_status = mock.Mock()
        with mock.patch.object(
            quote.requests, "get", return_value=fake_resp
        ) as m_get:
            quote.fetch_quote("03002T")
        args, kwargs = m_get.call_args
        assert kwargs["params"]["ex_ch"] == "tse_030002.tw"

    def test_retry_on_network_error(self):
        import requests as real_requests

        fake_resp = mock.Mock()
        fake_resp.json.return_value = SAMPLE_MARKET_OPEN
        fake_resp.raise_for_status = mock.Mock()

        with mock.patch.object(
            quote.requests,
            "get",
            side_effect=[real_requests.ConnectionError("boom"), fake_resp],
        ) as m_get:
            out = quote.fetch_quote("030002", retries=2)
        assert out["found"] is True
        assert m_get.call_count == 2

    def test_all_retries_fail(self):
        import requests as real_requests

        with mock.patch.object(
            quote.requests,
            "get",
            side_effect=real_requests.ConnectionError("nope"),
        ):
            out = quote.fetch_quote("030002", retries=2)
        assert out["found"] is False
        assert "failed" in out["error"]

    def test_invalid_code(self):
        out = quote.fetch_quote("XXX")
        assert out["found"] is False
        assert "invalid" in out["error"]
