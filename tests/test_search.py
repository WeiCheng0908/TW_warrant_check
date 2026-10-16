"""搜尋邏輯測試 (mock 資料)。"""

from datetime import date, timedelta

from warrant_finder.data import normalize_warrant
from warrant_finder.dates import roc_to_date, today_taipei
from warrant_finder.search import search_stocks, search_warrants


def _today_roc() -> str:
    d = today_taipei()
    return f"{d.year - 1911:03d}{d.month:02d}{d.day:02d}"


def _build_mock_data():
    today = _today_roc()
    yesterday = f"{(today_taipei() - timedelta(days=1)).year - 1911:03d}{(today_taipei() - timedelta(days=1)).month:02d}{(today_taipei() - timedelta(days=1)).day:02d}"

    stocks = [
        {"code": "2330", "name": "台積電", "full_name": "台灣積體電路製造股份有限公司"},
        {"code": "2317", "name": "鴻海", "full_name": "鴻海精密工業股份有限公司"},
        {"code": "0050", "name": "元大台灣50", "full_name": "元大台灣50"},
    ]

    raw_warrants = [
        {
            "權證代號": "03001T",
            "權證簡稱": "台積電元大5A購01",
            "認購售別": "認購",
            "權證種類": "一般型",
            "標的證券/指數": "台積電",
            "標的證券代號": "2330",
            "履約開始日": today,
            "最後交易日": "1151215",
            "到期日": "1160101",
            "履約價格": "1849.61",
            "發行單位": "5000",
            "發行人": "元大",
            "備註": "備註 A\r\n備註 B",
        },
        {
            "權證代號": "03002T",
            "權證簡稱": "台積電群益5A售12",
            "認購售別": "認售",
            "權證種類": "一般型",
            "標的證券/指數": "台積電",
            "標的證券代號": "2330",
            "履約開始日": today,
            "最後交易日": "1151215",
            "到期日": "1160101",
            "履約價格": "1900.00",
            "發行單位": "4000",
            "發行人": "群益",
            "備註": "備註 C",
        },
        {
            "權證代號": "03100T",
            "權證簡稱": "鴻海元大5A購01",
            "認購售別": "認購",
            "權證種類": "一般型",
            "標的證券/指數": "鴻海",
            "標的證券代號": "2317",
            "履約開始日": yesterday,
            "最後交易日": "1151215",
            "到期日": "1160101",
            "履約價格": "210.00",
            "發行單位": "5000",
            "發行人": "元大",
            "備註": "",
        },
    ]
    warrants = [normalize_warrant(r) for r in raw_warrants]
    return stocks, warrants


def test_search_by_code():
    stocks, warrants = _build_mock_data()
    matched, results = search_warrants("2330", warrants, stocks)
    assert matched is not None
    assert matched["code"] == "2330"
    codes = {r["code"] for r in results}
    assert codes == {"03001T", "03002T"}


def test_search_by_name():
    stocks, warrants = _build_mock_data()
    matched, results = search_warrants("台積電", warrants, stocks)
    assert matched is not None
    assert matched["code"] == "2330"
    assert len(results) == 2
    for r in results:
        assert r["underlying"] == "台積電"


def test_search_by_partial_name():
    stocks, warrants = _build_mock_data()
    matched, results = search_warrants("台積", warrants, stocks)
    assert matched is not None
    assert matched["code"] == "2330"
    assert len(results) == 2


def test_search_type_filter():
    stocks, warrants = _build_mock_data()
    matched, results = search_warrants("2330", warrants, stocks, type="認售")
    assert matched["code"] == "2330"
    assert all(r["type"] == "認售" for r in results)
    assert len(results) == 1


def test_new_only_filter():
    stocks, warrants = _build_mock_data()
    matched, results = search_warrants("2330", warrants, stocks, new_only=True)
    assert matched["code"] == "2330"
    assert len(results) == 2
    today_iso = today_taipei().isoformat()
    for r in results:
        assert r["start_date"] == today_iso

    # 鴻海的不是今天開始 → new_only 應該過濾掉
    _, results2 = search_warrants("2317", warrants, stocks, new_only=True)
    assert results2 == []


def test_search_returns_dates_as_iso():
    stocks, warrants = _build_mock_data()
    _, results = search_warrants("2330", warrants, stocks)
    for r in results:
        # 解析回 ISO 格式且符合 YYYY-MM-DD
        assert len(r["start_date"]) == 10
        assert r["start_date"][4] == "-"


def test_search_stocks_partial():
    stocks, _ = _build_mock_data()
    res = search_stocks("台積", stocks)
    assert any(r["code"] == "2330" for r in res)
    res2 = search_stocks("23", stocks)
    assert any(r["code"] == "2330" for r in res2)


def test_search_not_found():
    stocks, warrants = _build_mock_data()
    matched, results = search_warrants("9999", warrants, stocks)
    assert matched is None
    assert results == []
