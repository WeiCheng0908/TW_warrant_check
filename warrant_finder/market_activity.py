"""市場熱度模組：以權證每日成交金額為 proxy，group by 標的股排行。

用途
- 找出市場上**權證交易最熱絡的標的股**——這些標的往往有高波動期望
  (vol-of-vol / 期貨商積極發行的「即將有大行情」訊號)。
- 因為權證發行商會在預期波動變大時踴躍發行 (尤其近期標的權證發行熱絡)，
  同時市場也會在「覺得這檔要動」時把資金堆到該檔的權證，
  所以**標的層級的權證成交金額總和**是個相當好的「市場預期波動指標」。
- 未來會串接 stocks 層級的成交金額，這個模組仍負責權證端的分組。

設計目標
- 純函式、可單元測試、可跟前端的 /market 頁面無縫接。
- input：warrant 母檔 + warrant daily trade 檔（上市用 t187ap42_L、上櫃用 t187ap42_O.csv）。
- output：每個標的股一列的 dict list，依 total_amount desc 排序。
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from typing import Iterable, List, Optional, Tuple


def _strip_bom(b: bytes) -> bytes:
    return b[3:] if b.startswith(b"\xef\xbb\xbf") else b


def _read_csv(path: str) -> List[dict]:
    try:
        with open(path, "rb") as f:
            raw = _strip_bom(f.read())
    except FileNotFoundError:
        return []
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8", errors="replace"))))


# --------------------------------------------------------------------------- #
# Trade file normalization
# --------------------------------------------------------------------------- #
def _to_float(v) -> float:
    if v is None:
        return 0.0
    s = str(v).strip().replace(",", "")
    if not s or s == "-":
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def load_otc_trade_rows(path: str) -> List[dict]:
    """讀 ``t187ap42_O.csv`` 並正規化成 ``[{code, amount, volume}, ...]``。"""
    out = []
    for r in _read_csv(path):
        code = (r.get("權證代號") or "").strip()
        if not code:
            continue
        out.append({
            "code": code,
            "amount": _to_float(r.get("成交金額")),
            "volume": _to_float(r.get("成交數量")),
        })
    return out


# --------------------------------------------------------------------------- #
# Aggregation by underlying stock
# --------------------------------------------------------------------------- #
def aggregate_warrant_activity(
    warrants: Iterable[dict],
    trade_rows: Iterable[dict],
    *,
    market: Optional[str] = None,
) -> List[dict]:
    """用權證母檔 + 當日成交，把成交金額依 underlying 股加總。

    Parameters
    ----------
    warrants : list of dict
        來自 ``data.load_normalized()`` 或 ``data.load_otc_warrants()`` 的母檔。
        每列需要 ``code`` 與 ``underlying``（標的名稱）。
    trade_rows : list of dict
        每列 ``{code, amount, volume}``。
    market : str, optional
        ``"listed"`` / ``"otc"`` / None。若指定，warrant 中 ``market`` 欄位
        不符的會被略過（雙邊存檔時篩選用）。

    Returns
    -------
    list of dict
        每列：
            ``stock_name``, ``stock_code (if found in master)``,
            ``market`` (``"listed"`` or ``"otc"``),
            ``total_amount`` (新台幣元),
            ``total_volume`` (張),
            ``warrant_count``,
            ``warrant_codes`` (list[str])
        依 ``total_amount`` 降序排序。
    """
    # 1) warrant code → underlying name / market
    code_to_meta: dict = {}
    for w in warrants:
        if market and w.get("market") not in (market, market):
            continue
        c = (w.get("code") or "").strip()
        u = (w.get("underlying") or "").strip()
        m = w.get("market") or "listed"
        if c and u:
            code_to_meta[c] = {"name": u, "market": m}

    # 2) sum amount/volume by underlying name
    agg: dict = defaultdict(lambda: {
        "total_amount": 0.0,
        "total_volume": 0.0,
        "warrant_count": 0,
        "warrant_codes": [],
        "market": None,
    })

    for t in trade_rows:
        c = t.get("code", "")
        meta = code_to_meta.get(c)
        if not meta:
            continue  # 不在我們 master 的權證代號（可能已下市）
        name = meta["name"]
        bucket = agg[name]
        bucket["total_amount"] += float(t.get("amount", 0) or 0)
        bucket["total_volume"] += float(t.get("volume", 0) or 0)
        bucket["warrant_count"] += 1
        bucket["warrant_codes"].append(c)
        # market 採多數決：第一個非 None 為預設，之後混合的話不覆寫
        if bucket["market"] is None:
            bucket["market"] = meta["market"]

    out: List[dict] = []
    for name, b in agg.items():
        out.append({
            "stock_name": name,
            "stock_code": _resolve_code(name, warrants),
            "market": b["market"] or "listed",
            "total_amount": round(b["total_amount"], 0),
            "total_volume": round(b["total_volume"], 0),
            "warrant_count": b["warrant_count"],
            "warrant_codes": b["warrant_codes"],
        })
    out.sort(key=lambda r: r["total_amount"], reverse=True)
    return out


def _resolve_code(name: str, warrants: Iterable[dict]) -> Optional[str]:
    """從 warrant master 找該 underlying 名稱對應的股票代號。"""
    for w in warrants:
        if (w.get("underlying") or "").strip() == name:
            code = (w.get("underlying_code") or "").strip()
            if code:
                return code
    return None


# --------------------------------------------------------------------------- #
# Convenience: 給定 master + trade path，one-shot top N
# --------------------------------------------------------------------------- #
def top_stocks_by_warrant_trading(
    warrants: Iterable[dict],
    trade_rows: Iterable[dict],
    *,
    limit: int = 20,
    min_total_amount: float = 0.0,
    market: Optional[str] = None,
) -> List[dict]:
    """``aggregate_warrant_activity`` + filter + take top N 的便利版本。"""
    rows = aggregate_warrant_activity(warrants, trade_rows, market=market)
    rows = [r for r in rows if r["total_amount"] >= min_total_amount]
    return rows[:limit]


# --------------------------------------------------------------------------- #
# File-level convenience：給路徑直接出結果
# --------------------------------------------------------------------------- #
def top_stocks_from_files(
    master_path: str,
    trade_path: str,
    *,
    trade_format: str = "auto",
    limit: int = 20,
    min_total_amount: float = 0.0,
    market: Optional[str] = None,
    stocks_otc_path: Optional[str] = None,
) -> Tuple[List[dict], List[dict]]:
    """一次性從檔案載入並回傳 (warrants, top stocks)。

    Parameters
    ----------
    master_path : str
        warrant 母檔 JSON 路徑（上市 t187ap37_L）或 CSV 路徑（上櫃 t187ap37_O）。
    trade_path : str
        warrant daily trade 檔路徑（JSON 或 CSV）。
    trade_format : str
        ``"auto"`` / ``"json"`` / ``"csv"`` — 預設 auto 依副檔名判斷。
    stocks_otc_path : str, optional
        提供時，用它把 ``stock_name`` 翻成 ``stock_code``。
        上櫃 warrant master 沒有 ``underlying_code``，但 ``stocks_otc.csv`` 有，
        這可以大幅提升 UI 可點擊性。

    Returns
    -------
    (warrants, top_stocks)
        warrants 為載入後的 list（呼叫端可用來 join）；top_stocks 為排行結果。
    """
    from .data import load_normalized, load_otc_warrants, load_otc_stocks

    is_otc = master_path.endswith((".csv",)) or "otc" in master_path.lower()
    if is_otc:
        warrs = load_otc_warrants(master_path)
        trades = load_otc_trade_rows(trade_path)
        name_to_code: dict = {}
        if stocks_otc_path:
            for s in load_otc_stocks(stocks_otc_path):
                name_to_code[(s.get("name") or "").strip()] = s.get("code")
    else:
        warrs = load_normalized(master_path)
        trades = _load_listed_trade(trade_path)
        name_to_code = {}

    top = top_stocks_by_warrant_trading(
        warrs, trades, limit=limit,
        min_total_amount=min_total_amount, market=market,
    )
    # 在 top 上補 stock_code（master 無 underlying_code 時 fallback 到 stocks_otc）
    for r in top:
        if not r.get("stock_code"):
            r["stock_code"] = name_to_code.get(r["stock_name"]) or _resolve_code(r["stock_name"], warrs)
    return warrs, top


def _load_listed_trade(path: str) -> List[dict]:
    """``t187ap42_L`` 是 JSON 陣列，欄位 ``成交金額`` / ``成交數量`` / ``權證代號``。

    2026 年中 TWSE 也曾把某些端點改成 CSV；為了 safe，這裡兩種都吃。
    """
    import json
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        return []
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]

    head = raw.lstrip()
    out = []
    if head.startswith(b"["):
        # JSON
        try:
            from . import parser
            rows = list(parser.iter_loads(raw))
        except Exception:
            rows = json.loads(raw)
        for r in rows:
            code = r.get("權證代號") or r.get("code") or ""
            if isinstance(code, (int, float)):
                code = str(int(code))
            code = str(code).strip()
            if not code:
                continue
            out.append({
                "code": code,
                "amount": _to_float(r.get("成交金額")),
                "volume": _to_float(r.get("成交數量")),
            })
    else:
        # CSV
        for r in _read_csv(path):
            code = (r.get("權證代號") or "").strip()
            if isinstance(code, (int, float)):
                code = str(int(code))
            if not code:
                continue
            out.append({
                "code": code,
                "amount": _to_float(r.get("成交金額")),
                "volume": _to_float(r.get("成交數量")),
            })
    return out
