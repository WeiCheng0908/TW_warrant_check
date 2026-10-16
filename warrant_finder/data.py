"""合併兩個 warrant 檔，按權證代號 join。

TWSE 真實資料的欄位名稱 (2026/10 取樣)：

* t187ap37_L (母檔)：
    - ``出表日期`` (ROC)
    - ``權證代號``
    - ``權證簡稱``
    - ``權證類型`` (認購 / 認售)
    - ``類別`` (一般型 / 價內型 / ...)
    - ``履約開始日`` (ROC)
    - ``最後交易日`` (ROC)
    - ``履約截止日`` (ROC)
    - ``發行單位數量(仟單位)``
    - ``標的證券/指數``
    - ``最新履約價格(元)/履約指數``
    - ``備註`` (可能含真實 \\r\\n)

* t187ap42_L (每日成交)：
    - ``出表日期``、``交易日期``、``權證代號``、``權證名稱``
    - ``成交金額``、``成交張數``
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from .parser import iter_loads


def _read(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    with open(path, "rb") as f:
        return list(iter_loads(f.read()))


def _code(row: dict) -> str:
    """權證代號可能為整數或字串 (在 t187ap42_L 中為 int)。"""
    v = row.get("權證代號")
    if v is None:
        return ""
    if isinstance(v, (int, float)):
        return str(int(v)).strip()
    return str(v).strip()


def load_warrants(base_path: str, trade_path: Optional[str] = None) -> tuple:
    """讀取母檔與 (可選) 每日成交檔，按 ``權證代號`` join。

    回傳 ``(base_rows, trade_by_code)``。
    """
    base_rows = _read(base_path)
    trade_rows = _read(trade_path) if trade_path else []
    trade_by_code: Dict[str, dict] = {}
    for row in trade_rows:
        code = _code(row)
        if code:
            trade_by_code[code] = row
    return base_rows, trade_by_code


def _to_number(v) -> Optional[float]:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if not s or s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def normalize_warrant(row: dict, trade: Optional[dict] = None) -> dict:
    """把 TWSE raw row 轉成 API 友善的 dict。

    同時容忍設計文件裡寫的舊欄位名 (``認購售別`` / ``履約價格`` ...) 與
    實際 API 回傳的欄位 (``權證類型`` / ``最新履約價格(元)/履約指數`` ...)。
    """
    notes_raw = (
        row.get("備註")
        or row.get("備 註")
        or ""
    )
    # 把多餘空白行清掉但保留 \\n 視覺換行
    notes = "\n".join(line.strip() for line in str(notes_raw).splitlines() if line.strip())

    code = _code(row)
    wtype = (
        row.get("權證類型")
        or row.get("認購售別")
        or row.get("認購/售")
        or ""
    ).strip()
    category = (
        row.get("類別")
        or row.get("權證種類")
        or ""
    ).strip()
    strike = _to_number(
        row.get("最新履約價格(元)/履約指數")
        or row.get("履約價格")
    )
    issue_size = _to_number(
        row.get("發行單位數量(仟單位)")
        or row.get("發行單位")
    )

    out = {
        "code": code,
        "name": (row.get("權證簡稱") or row.get("權証簡稱") or "").strip(),
        "type": wtype,
        "category": category,
        "underlying": (row.get("標的證券/指數") or "").strip(),
        "underlying_code": (row.get("標的證券代號") or row.get("標的代號") or "").strip(),
        "start_date": row.get("履約開始日"),
        "end_date": row.get("最後交易日"),
        "expire_date": row.get("履約截止日") or row.get("到期日"),
        "strike_price": strike,
        "issue_size": issue_size,
        "issuer": (row.get("發行人") or "").strip(),
        "notes": notes,
    }
    if trade:
        out["traded_today"] = {
            "amount": _to_number(trade.get("成交金額")),
            "volume_lots": _to_number(trade.get("成交張數")),
            "trade_date": trade.get("交易日期"),
        }
    return out


def load_normalized(base_path: str, trade_path: Optional[str] = None) -> List[dict]:
    base_rows, trade_by_code = load_warrants(base_path, trade_path)
    out: List[dict] = []
    for row in base_rows:
        code = _code(row)
        if not code:
            continue
        out.append(normalize_warrant(row, trade_by_code.get(code)))
    return out
