"""寬鬆 JSON parser 測試。"""

import json

from warrant_finder.parser import clean_text, iter_loads, loads


def _sample_with_crlf() -> str:
    # 模擬 TWSE 母檔：備註欄位含真實 \r\n
    return (
        '['
        '{"權證代號":"03001T","備註":"第一行\\r\\n第二行\\r\\n第三行"},'
        '{"權證代號":"03002T","備註":"含\\t tab 與中文 與 \\"quote\\" 結尾"}'
        ']'
    )


def test_clean_text_removes_nul():
    assert clean_text("a\x00b") == "a b"
    # \\n 與 \\r 保留
    assert clean_text("a\r\nb") == "a\r\nb"


def test_default_loader_does_not_fail_on_crlf():
    # Python 3.9+ 的 json.loads 預設就容許 \\r\\n 在字串裡，
    # 但我們的 parser 仍須能正確解析。這個測試在
    # parser 與 stdlib 行為一致時，確認我們的 raw_clean 沒
    # 破壞掉字串內容。
    s = _sample_with_crlf()
    rows = json.loads(s)
    assert len(rows) == 2
    # 我們的寬鬆 parser 結果應該一致
    rows2 = loads(s)
    assert rows == rows2


def test_loads_with_crlf():
    s = _sample_with_crlf()
    rows = loads(s)
    assert len(rows) == 2
    assert rows[0]["權證代號"] == "03001T"
    assert "第二行" in rows[0]["備註"]
    assert "第一行" in rows[0]["備註"]
    assert rows[1]["權證代號"] == "03002T"


def test_loads_with_nul_bytes():
    s = '[{"a": "b\x00c"}]'.encode("utf-8")
    rows = loads(s)
    assert rows[0]["a"] == "b c"


def test_iter_loads():
    s = _sample_with_crlf()
    rows = list(iter_loads(s))
    assert len(rows) == 2
    assert rows[0]["權證代號"] == "03001T"


def test_iter_loads_handles_leading_whitespace():
    s = "  \n\r\t" + _sample_with_crlf()
    rows = list(iter_loads(s))
    assert len(rows) == 2
