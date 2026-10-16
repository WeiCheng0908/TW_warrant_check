"""
宽鬆 JSON parser for TWSE OpenAPI responses.

TWSE 的 t187ap37_L (上市權證基本資料彙總表) 內含「備註」欄位帶有真實
``\\r\\n`` 換行，以及偶爾出現的 NUL byte，這對預設 strict JSON parser
是無效的。本模組提供兩種寬鬆解析方式：

* :func:`clean_text` — 清洗一個可能含控制字元的字串，移除 NUL 與
  非法的 JSON 跳脫，但保留 ``\\r\\n`` 的視覺換行。
* :func:`loads` — 整檔解析前先清洗整個字串。
* :func:`iter_loads` — 以 streaming 方式逐個 object 解析（不嘗試把
  7 MB 的整份檔一次丟給 json.loads）。
"""

from __future__ import annotations

import json
import re
from typing import Iterator, List, Tuple

# 控制字元集：保留 \\t (0x09), \\n (0x0A), \\r (0x0D)，其餘 (尤其 0x00 NUL)
# 都替換成空白以免 JSON 拒絕。
# 兩種形態：str 與 bytes 各一份
_BAD_CTRL_STR = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_BAD_CTRL_BYTES = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def clean_text(text: str) -> str:
    """把控制字元 (尤其 NUL) 從一個字串中清掉。

    「備註」欄位帶真實 ``\\r\\n`` 並不違法，但本函式仍會把
    換行以外的非法控制位元組拿掉，方便 ``json.loads``。
    """
    if not isinstance(text, str):
        return text
    return _BAD_CTRL_STR.sub(" ", text)


def _clean_bytes(data: bytes) -> bytes:
    return _BAD_CTRL_BYTES.sub(b" ", data)


def loads(data) -> list:
    """以寬鬆模式載入整個 JSON 陣列。回傳 list of dict。

    接受 ``bytes`` 或 ``str``。先做控制字元清洗，再用
    ``json.JSONDecoder(strict=False)`` 解析。
    """
    if isinstance(data, (bytes, bytearray)):
        cleaned = _clean_bytes(bytes(data))
    else:
        cleaned = clean_text(data)
    return json.loads(cleaned, strict=False)


def iter_loads(data, *, chunk_size: int = 65536) -> Iterator[dict]:
    """逐個 object 解析大型 JSON 陣列。

    避免一次把 7 MB 整份丟進 ``json.loads``。我們逐塊讀入，
    偵測到「,」位於陣列的物件分隔位置時，把目前累積的 object
    文字丟給 ``json.loads``。**清洗**仍會對整個輸入做一次以處理
    NUL 與控制字元，但解析時是 O(object)。

    為求簡潔，這裡直接清洗整個輸入後再走 ``raw_decode`` 一次
    切多個 object 的方式 — ``json.RawJSONDecoder.raw_decode``
    一次只吃一個值，所以我們以手寫小型狀態機切換物件邊界。
    """
    if isinstance(data, (bytes, bytearray)):
        text = _clean_bytes(bytes(data)).decode("utf-8", errors="replace")
    else:
        text = clean_text(data)

    decoder = json.JSONDecoder(strict=False)
    idx = 0
    n = len(text)
    # 跳過前導空白
    while idx < n and text[idx] in " \t\r\n":
        idx += 1
    if idx >= n or text[idx] != "[":
        # 退回到整體解析
        for obj in decoder.decode(text):
            yield obj
        return
    idx += 1
    while idx < n:
        # 跳過空白與逗號
        while idx < n and text[idx] in " \t\r\n,":
            idx += 1
        if idx < n and text[idx] == "]":
            return
        obj, end = decoder.raw_decode(text, idx)
        yield obj
        idx = end


def safe_field(row: dict, *keys: str, default: str = "") -> str:
    """從一個 row 中取第一個存在且非空白的字串欄位。"""
    for k in keys:
        v = row.get(k)
        if v is None:
            continue
        if isinstance(v, str):
            v2 = v.strip()
            if v2:
                return v2
        else:
            return v
    return default
