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

上櫃 (TPEX, mopsfin.twse.com.tw CSV)：
* ``t187ap37_O.csv``  同母檔欄位（CSV，含 BOM \\ufeff）
* ``t187ap42_O.csv``  同交易檔
* ``t187ap03_O.csv``  公司基本資料
"""

from __future__ import annotations

import csv
import io
import os
from typing import Dict, Iterable, List, Optional, Tuple

from .parser import iter_loads


# --------------------------------------------------------------------------- #
# Listed (TWSE) — JSON
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# OTC (TPEX) — CSV
# --------------------------------------------------------------------------- #
def _strip_bom(b: bytes) -> bytes:
    return b[3:] if b.startswith(b"\xef\xbb\xbf") else b


def _read_csv(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    with open(path, "rb") as f:
        raw = _strip_bom(f.read())
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))


def normalize_otc_warrant(row: dict) -> dict:
    """把上櫃權證 CSV 的一列轉成跟 ``normalize_warrant`` 相容的形狀。

    已知 CSV 欄位（2026/10 取樣）：
    ``出表日期, 權證代號, 權證簡稱, 權證類型, 類別, 流動量提供者報價方式,
    履約開始日, 最後交易日, 履約截止日, 發行單位數量(仟單位),
    結算方式(詳附註編號說明), 標的證券/指數, 最新標的履約配發數量(每仟單位權證),
    原始履約價格(元)/履約指數, 原始上限價格(元)/上限指數,
    原始下限價格(元)/下限指數, 最新履約價格(元)/履約指數,
    最新上限價格(元)/上限指數, 最新下限價格(元)/上限指數, 備註``

    上櫃 CSV **沒有** ``標的證券代號`` 欄位（用名稱比對）。
    """
    notes = (row.get("備註") or "").strip()
    strike = _to_number(row.get("最新履約價格(元)/履約指數"))
    issue_size = _to_number(row.get("發行單位數量(仟單位)"))
    # 「最新標的履約配發數量」是「每仟單位權證」可配發的股數，例如 3.00 → 每張 0.003 股。
    exercise_ratio = _to_number(row.get("最新標的履約配發數量(每仟單位權證)"))

    return {
        "code": (row.get("權證代號") or "").strip(),
        "name": (row.get("權證簡稱") or "").strip(),
        "type": (row.get("權證類型") or "").strip(),
        "category": (row.get("類別") or "").strip(),
        "underlying": (row.get("標的證券/指數") or "").strip(),
        "underlying_code": "",  # 上櫃 CSV 沒這欄位
        "start_date": (row.get("履約開始日") or "").strip(),
        "end_date": (row.get("最後交易日") or "").strip(),
        "expire_date": (row.get("履約截止日") or "").strip(),
        "strike_price": strike,
        "exercise_ratio_per_mille": exercise_ratio,  # 每仟張配發股數
        "issue_size": issue_size,
        "issuer": "",  # 上櫃 CSV 沒這欄位
        "notes": notes,
        "market": "otc",
    }


def normalize_otc_stock(row: dict) -> Optional[dict]:
    """上櫃股票 CSV (``t187ap03_O.csv``) 一列的標準化。

    回傳 ``None`` 表示該列沒代號或名稱。
    """
    code = (row.get("公司代號") or "").strip()
    name = (row.get("公司簡稱") or "").strip()
    if not code or not name:
        return None
    return {
        "code": code,
        "name": name,
        "full_name": (row.get("公司名稱") or "").strip(),
        "market": "otc",
    }


def load_otc_warrants(path: str) -> List[dict]:
    return [normalize_otc_warrant(r) for r in _read_csv(path)]


def load_otc_stocks(path: str) -> List[dict]:
    out: List[dict] = []
    for r in _read_csv(path):
        rec = normalize_otc_stock(r)
        if rec:
            out.append(rec)
    return out


def merge_listings(
    listed_warrants: Iterable[dict],
    otc_warrants: Iterable[dict],
    listed_stocks: Iterable[dict],
    otc_stocks: Iterable[dict],
) -> Tuple[List[dict], List[dict]]:
    """把上市 + 上櫃的 warrants 跟 stocks 合併，標的代號衝突時上市優先。

    在 warrants 上加 ``market`` (``"listed"`` / ``"otc"``)。
    在 stocks 同樣加 ``market`` 欄位（不會覆寫既有 ``market`` 欄位）。
    """
    merged_warrants: List[dict] = []
    seen_codes = set()
    for w in listed_warrants:
        w = dict(w)
        w["market"] = w.get("market") or "listed"
        merged_warrants.append(w)
        if w.get("code"):
            seen_codes.add(w["code"])
    for w in otc_warrants:
        w = dict(w)
        if w.get("code") in seen_codes:
            continue  # 上市優先：略過 OTC 同名代號（極少見但 defensive）
        w["market"] = "otc"
        merged_warrants.append(w)
        if w.get("code"):
            seen_codes.add(w["code"])

    merged_stocks: List[dict] = []
    seen_stock_codes = set()
    for s in listed_stocks:
        s = dict(s)
        s["market"] = s.get("market") or "listed"
        merged_stocks.append(s)
        if s.get("code"):
            seen_stock_codes.add(s["code"])
    for s in otc_stocks:
        if s.get("code") in seen_stock_codes:
            continue
        merged_stocks.append(s)
    return merged_warrants, merged_stocks


__all__ = [
    "load_warrants",
    "load_normalized",
    "normalize_warrant",
    "load_otc_warrants",
    "load_otc_stocks",
    "normalize_otc_warrant",
    "normalize_otc_stock",
    "merge_listings",
]
