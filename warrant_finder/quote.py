"""抓 TWSE 5 分鐘延遲報價。

端點：``https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_<code>.tw``
     （上櫃用 ``otc_<code>.tw``，但權證都在上市，所以這裡固定用 tse）

這是 TWSE 官方公開的 5 分鐘延遲行情，不需 API key、不需登入。
收盤後（13:30 之後）價格欄位會是 ``"-"`` 或 ``"0"``，需要 fallback 處理。
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

import requests

QUOTE_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
DEFAULT_TIMEOUT = 8
DEFAULT_RETRIES = 2
DEFAULT_UA = (
    "Mozilla/5.0 (compatible; TWSE-Warrant-Finder/0.1; "
    "+https://github.com/local/twse-warrant-finder)"
)


def _normalize_code(code: str) -> str:
    """把代號轉成 MIS API 認得的格式。

    規則：
    - 股票 4 碼（如 ``6187``、``2330``）→ 保持 4 碼不 padding。
    - 權證若帶英文字母後綴（如 ``03002T``）→ 去字母再 ``zfill(6)`` 得 6 碼。
    - 純數字權證（如 ``712218``、``088272``）→ 保持原樣（已是 6 碼）。
    """
    s = (code or "").strip()
    if not s:
        return ""
    digits = re.sub(r"\D", "", s)
    if not digits:
        return ""
    has_letter = any(c.isalpha() for c in s)
    # 股票：4 碼以內、無英文字母，直接回傳。
    if len(digits) <= 4 and not has_letter:
        return digits
    # 權證：pad 到 6 碼。
    return digits.zfill(6)[:6]


def _is_otc_code(code: str) -> bool:
    """Heuristic：判斷一個代號是否屬於「上櫃」。

    - TPEX 認購權證：6 碼純數字且以 7 開頭（700000–739999）。
    - TPEX 認售權證：5 碼數字 + 英文字母後綴 (P/U/T)，前綴以 7 開頭。

    注意：上市牛熊證也以 7 開頭（70000C–73999C），不過那仍走 TPEX 報價。

    TWSE 上市權證的「增額發行」代號（5 碼數字 + T 後綴，如 ``03002T``）以
    0 或 6 開頭，跟 TPEX 認售的 7 字頭前綴可區分。
    """
    if not code:
        return False
    # TPEX 認購：6 碼純數字且以 7 開頭
    if len(code) == 6 and code.isdigit() and code.startswith("7"):
        return True
    # TPEX 認售/牛熊：7 字頭 + 5 碼數字 + 字母結尾
    if len(code) == 6 and code[-1].isalpha() and code.startswith("7"):
        digits = re.sub(r"\D", "", code)
        if len(digits) == 5:
            return True
    return False


def _split_prices(raw: str) -> List[Optional[float]]:
    """``"1.6000_0.7300_"`` → ``[1.6, 0.73]``。空字串或 ``"-"`` 變 ``None``。

    Trailing 的純空 piece 若前一個是有意義的數字則丟掉；
    若前一個是空字串或 ``-`` 則保留為 ``None``（代表「缺的檔位」）。
    """
    parts = (raw or "").split("_")
    # Drop trailing empties only when preceded by a value-like piece.
    while len(parts) > 1 and parts[-1] == "" and parts[-2] not in ("", "-"):
        parts.pop()
    out: List[Optional[float]] = []
    for piece in parts:
        piece = piece.strip()
        if not piece or piece == "-":
            out.append(None)
            continue
        try:
            out.append(float(piece))
        except ValueError:
            out.append(None)
    return out


def _to_float(v: Any) -> Optional[float]:
    if v is None or v == "" or v == "-":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_quote(
    code: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    user_agent: str = DEFAULT_UA,
    exchange: Optional[str] = None,
) -> Dict[str, Any]:
    """抓單檔權證或股票的 5 分鐘延遲報價。

    Parameters
    ----------
    code : str
        權證代號（如 ``712218``）或股票代號（如 ``2330`` / ``6187``）
    exchange : str, optional
        ``"tse"``（上市）或 ``"otc"``（上櫃）。未指定時根據代號格式啟發式判斷：
        - 7 字頭 warrant → ``otc``
        - 其他情況 → ``tse``
        股票若不確定，呼叫端應該 explicit 指定（例如已知 6187 萬潤是上櫃）。

    回傳 dict；找不到 / 失敗時回傳 ``{"code": ..., "found": False, "error": "..."}``。
    """
    code_norm = _normalize_code(code)
    if not code_norm:
        return {
            "code": code,
            "found": False,
            "error": f"invalid code: {code!r}",
        }

    if exchange is None:
        exchange = "otc" if _is_otc_code(code) or _is_otc_code(code_norm) else "tse"
    else:
        exchange = exchange.lower()
        if exchange not in ("tse", "otc"):
            raise ValueError(f"exchange must be 'tse' or 'otc', got {exchange!r}")

    url = QUOTE_URL
    params = {"ex_ch": f"{exchange}_{code_norm}.tw"}
    # 註：上市股票/權證 → tse_，上櫃 → otc_
    headers = {
        "User-Agent": user_agent,
        "Accept": "application/json",
    }

    last_exc: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=timeout,
            )
            resp.raise_for_status()
            return _parse_quote_response(resp.json(), code=code_norm)
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(0.5 * attempt)
                continue
            break

    return {
        "code": code_norm,
        "found": False,
        "error": f"failed after {retries} attempts: {last_exc}",
    }


def _parse_quote_response(payload: Any, *, code: str) -> Dict[str, Any]:
    """解析 TWSE 5-min API 的回傳。"""
    try:
        arr = payload.get("msgArray", []) if isinstance(payload, dict) else []
    except AttributeError:
        return {"code": code, "found": False, "error": "unexpected payload"}

    if not arr:
        return {"code": code, "found": False, "error": "no quote returned"}

    row = arr[0] if isinstance(arr[0], dict) else {}

    last = _to_float(row.get("z"))
    prev = _to_float(row.get("y"))
    open_ = _to_float(row.get("o"))
    high = _to_float(row.get("h"))
    low = _to_float(row.get("l"))
    up_limit = _to_float(row.get("u"))
    down_limit = _to_float(row.get("w"))

    # 漲跌：API 沒直接給，自己算。收盤後 z 可能是 "-" → 變 None。
    change: Optional[float] = None
    change_pct: Optional[float] = None
    if last is not None and prev not in (None, 0):
        change = round(last - prev, 4)
        change_pct = round((last - prev) / prev * 100, 2)

    # 成交量/金額：API 的 v 字段是「張」，收盤後是 0 或 "-"
    volume_lots = _to_float(row.get("v"))
    cum_volume = _to_float(row.get("tv"))
    # Fallback only when tv was missing AND v has meaningful (non-zero) value,
    # so 收盤後 (v="0", tv="-") 不會把 cum_volume 從 None 蓋成 0.0。
    if cum_volume is None and volume_lots:
        cum_volume = volume_lots

    # 累計成交金額：API 沒直接給，用 cum_volume(張) × 1000(股) × last 推算
    # 收盤後 last 為 None，amount 也為 None
    cum_amount: Optional[float] = None
    if cum_volume is not None and last is not None:
        cum_amount = round(cum_volume * 1000 * last, 0)

    asks = _split_prices(row.get("a", ""))
    bids = _split_prices(row.get("b", ""))

    # 市場是否開盤：API 在 13:30 後會把 z 設為 "-"
    market_open = last is not None and row.get("z") not in (None, "", "-")

    return {
        "code": code,
        "name": row.get("n") or "",
        "full_name": row.get("nf") or "",
        "underlying": row.get("nf", "").split(" ")[0] if row.get("nf") else "",
        "found": True,
        "as_of_date": row.get("d") or "",
        "as_of_time": row.get("t") or "",
        "market_open": market_open,
        "last_price": last,
        "prev_close": prev,
        "change": change,
        "change_pct": change_pct,
        "open": open_,
        "high": high,
        "low": low,
        "up_limit": up_limit,
        "down_limit": down_limit,
        "volume_lots": volume_lots,
        "cum_volume_lots": cum_volume,
        "cum_amount": cum_amount,
        "asks": [p for p in asks if p is not None],
        "bids": [p for p in bids if p is not None],
    }
