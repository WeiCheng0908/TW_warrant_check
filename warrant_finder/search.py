"""查詢邏輯：給定 q (代號/名稱/子字串) 回傳符合的權證清單。"""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Tuple

from .data import normalize_warrant
from .dates import roc_to_date, today_taipei, format_iso


def _resolve_stock(q: str, stocks: Sequence[dict]) -> Optional[dict]:
    """先用代號精確比對，再用名稱/簡稱做大小寫不敏感的子字串。"""
    if not q:
        return None
    q_norm = q.strip().lower()
    # 1) 代號
    for s in stocks:
        if s["code"].lower() == q_norm:
            return {"code": s["code"], "name": s["name"]}
    # 2) 簡稱/全名
    for s in stocks:
        if s["name"].lower() == q_norm or s.get("full_name", "").lower() == q_norm:
            return {"code": s["code"], "name": s["name"]}
    # 3) 子字串
    for s in stocks:
        if q_norm in s["name"].lower() or q_norm in s.get("full_name", "").lower():
            return {"code": s["code"], "name": s["name"]}
    return None


def search_stocks(q: str, stocks: Sequence[dict], limit: int = 20) -> List[dict]:
    if not q:
        return []
    qn = q.strip().lower()
    out: List[dict] = []
    seen = set()
    for s in stocks:
        code_l = s["code"].lower()
        name_l = s["name"].lower()
        full_l = s.get("full_name", "").lower()
        if code_l.startswith(qn) or qn in name_l or qn in full_l:
            key = s["code"]
            if key in seen:
                continue
            seen.add(key)
            out.append({"code": s["code"], "name": s["name"]})
            if len(out) >= limit:
                break
    return out


def _matches_warrant(
    w: dict,
    *,
    underlying_names: Iterable[str],
    underlying_codes: Iterable[str],
    type_filter: Optional[str],
    new_only: bool,
) -> bool:
    if type_filter:
        if w.get("type") != type_filter:
            return False
    if new_only:
        sd = w.get("start_date")
        sd_date = roc_to_date(sd)
        if sd_date is None or sd_date != today_taipei():
            return False
    u = (w.get("underlying") or "").strip()
    uc = (w.get("underlying_code") or "").strip()
    if any(name and name == u for name in underlying_names):
        return True
    if any(code and code == uc for code in underlying_codes):
        return True
    return False


def search_warrants(
    q: str,
    warrants: Sequence[dict],
    stocks: Sequence[dict],
    *,
    type: Optional[str] = None,  # noqa: A002 - public param name
    new_only: bool = False,
) -> Tuple[Optional[dict], List[dict]]:
    """回傳 ``(matched_stock, results)``。

    若 ``q`` 不對應任何 stock，``matched_stock`` 為 ``None``，但
    仍會用 ``q`` 對 ``underlying`` 做寬鬆子字串搜尋。
    """
    matched = _resolve_stock(q, stocks) if stocks else None
    underlying_names = []
    underlying_codes = []
    if matched:
        underlying_names.append(matched["name"])
        underlying_codes.append(matched["code"])

    qn = (q or "").strip().lower()
    results: List[dict] = []
    for w in warrants:
        if not _matches_warrant(
            w,
            underlying_names=underlying_names,
            underlying_codes=underlying_codes,
            type_filter=type,
            new_only=new_only,
        ):
            # 允許 q 直接命中 underlying 名稱 (若 stock_map 沒對應到)
            if matched is None and qn and qn in (w.get("underlying") or "").lower():
                pass
            else:
                continue
        results.append(_present_warrant(w))
    return matched, results


def _present_warrant(w: dict) -> dict:
    out = dict(w)
    out["start_date"] = format_iso(roc_to_date(w.get("start_date")))
    out["end_date"] = format_iso(roc_to_date(w.get("end_date")))
    out["expire_date"] = format_iso(roc_to_date(w.get("expire_date")))
    return out
