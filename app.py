"""Flask app: 提供 /api/* + 靜態前端。

啟動時會在背景 thread 嘗試抓 / 更新快取，避免第一次 request 等下載。
若快取或網路失敗，API 仍會以「空資料」狀態回應，並透過 ``/api/health``
回報 ``status: "degraded"``。
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, render_template, request, send_from_directory

from warrant_finder import data as data_mod
from warrant_finder import fetcher
from warrant_finder import quote as quote_mod
from warrant_finder import search as search_mod
from warrant_finder import stock_map as stock_map_mod
from warrant_finder.dates import iso_to_date, roc_to_date, today_taipei, format_iso


def _parse_end_date(s):
    """同時吃 ROC 7 碼 ``1160503`` 與 ISO ``2027-05-03``。"""
    if not s:
        return None
    return iso_to_date(s) or roc_to_date(s)
from warrant_finder.iv import (
    DEFAULT_RISK_FREE_RATE,
    bs_greeks,
    bs_price,
    implied_volatility,
    year_fraction,
)


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

WARRANT_PATH = os.path.join(DATA_DIR, "warrants.json")
WARRANT_TRADE_PATH = os.path.join(DATA_DIR, "warrants_trade.json")
STOCK_PATH = os.path.join(DATA_DIR, "stocks.json")

# 上櫃（TPEX, mopsfin CSV）
WARRANT_OTC_PATH = os.path.join(DATA_DIR, "warrants_otc.csv")
WARRANT_OTC_TRADE_PATH = os.path.join(DATA_DIR, "warrants_trade_otc.csv")
STOCK_OTC_PATH = os.path.join(DATA_DIR, "stocks_otc.csv")

CACHE_MAX_AGE_HOURS = 12.0


class AppState:
    def __init__(self) -> None:
        self.warrants: List[dict] = []
        self.stocks: List[dict] = []
        self.warrants_mtime: Optional[datetime] = None
        self.stocks_mtime: Optional[datetime] = None
        self.last_attempt: Optional[datetime] = None
        self.last_error: Optional[str] = None
        self.lock = threading.RLock()

    def load_from_disk(self) -> None:
        with self.lock:
            listed_warrants, listed_stocks = _load_listed()
            otc_warrants, otc_stocks = _load_otc()
            warrants, stocks = data_mod.merge_listings(
                listed_warrants, otc_warrants, listed_stocks, otc_stocks,
            )
            self.warrants = warrants
            self.stocks = stocks
            # mtime 取最後更新的兩個檔
            mtimes: List[datetime] = []
            for p in (WARRANT_PATH, WARRANT_OTC_PATH, STOCK_PATH, STOCK_OTC_PATH):
                if os.path.exists(p):
                    mtimes.append(datetime.fromtimestamp(os.path.getmtime(p)))
            if mtimes:
                self.warrants_mtime = max(mtimes)
                self.stocks_mtime = max(mtimes)

    def update(self) -> None:
        with self.lock:
            self.last_attempt = datetime.utcnow()
            try:
                # 上市 + 上櫃兩批都抓（graceful：任一批失敗保留快取）
                fetcher.fetch_all(DATA_DIR, max_age_hours=CACHE_MAX_AGE_HOURS, force=False)
                fetcher.fetch_otc_all(DATA_DIR, max_age_hours=CACHE_MAX_AGE_HOURS, force=False)
                listed_warrants, listed_stocks = _load_listed()
                otc_warrants, otc_stocks = _load_otc()
                warrants, stocks = data_mod.merge_listings(
                    listed_warrants, otc_warrants, listed_stocks, otc_stocks,
                )
                self.warrants = warrants
                self.stocks = stocks
                self.last_error = None
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)


def _load_listed() -> tuple:
    warrs: List[dict] = []
    stocks: List[dict] = []
    if os.path.exists(WARRANT_PATH):
        warrs = data_mod.load_normalized(WARRANT_PATH, WARRANT_TRADE_PATH)
    if os.path.exists(STOCK_PATH):
        stocks = stock_map_mod.load_stock_map(STOCK_PATH)
    return warrs, stocks


def _load_otc() -> tuple:
    warrs: List[dict] = []
    stocks: List[dict] = []
    if os.path.exists(WARRANT_OTC_PATH):
        warrs = data_mod.load_otc_warrants(WARRANT_OTC_PATH)
    if os.path.exists(STOCK_OTC_PATH):
        stocks = data_mod.load_otc_stocks(STOCK_OTC_PATH)
    return warrs, stocks


state = AppState()


def _create_app() -> Flask:
    app = Flask(
        __name__,
        static_folder=STATIC_DIR,
        template_folder=TEMPLATES_DIR,
    )

    @app.get("/")
    def index():  # noqa: D401
        # 用 render_template 才能處理 Jinja 的 {{ url_for('static', ...) }}
        idx = os.path.join(TEMPLATES_DIR, "index.html")
        if os.path.exists(idx):
            return render_template("index.html")
        return (
            "<h1>TWSE Warrant Finder (backend)</h1>"
            "<p>前端尚未部署。請見 /api/health 與 README。</p>",
            200,
            {"Content-Type": "text/html; charset=utf-8"},
        )

    @app.get("/api/health")
    def health():
        with state.lock:
            last_updated = None
            for ts in (state.warrants_mtime, state.stocks_mtime):
                if ts and (last_updated is None or ts > last_updated):
                    last_updated = ts
            payload = {
                "status": "ok" if (state.warrants and state.stocks and not state.last_error) else "degraded",
                "warrants": len(state.warrants),
                "stocks": len(state.stocks),
                "last_updated": last_updated.isoformat() if last_updated else None,
                "today": today_taipei().isoformat(),
            }
            if state.last_error:
                payload["last_error"] = state.last_error
            return jsonify(payload)

    @app.get("/api/stocks")
    def api_stocks():
        q = request.args.get("q", "").strip()
        if not q:
            return jsonify({"results": []}), 200
        with state.lock:
            results = search_mod.search_stocks(q, state.stocks, limit=20)
        return jsonify({"results": results})

    @app.get("/api/warrants")
    def api_warrants():
        q = request.args.get("q", "").strip()
        type_filter = request.args.get("type")
        new_only_raw = request.args.get("new_only", "").strip().lower()
        new_only = new_only_raw in ("1", "true", "yes", "on")
        market = (request.args.get("market") or "all").lower()  # listed | otc | all
        listed_only = market == "listed"
        otc_only = market == "otc"
        if not q:
            return jsonify({"error": "missing q"}), 400
        with state.lock:
            if not state.warrants:
                return jsonify({"error": "warrants data not ready"}), 503
            matched, results = search_mod.search_warrants(
                q,
                state.warrants,
                state.stocks,
                type=type_filter,
                new_only=new_only,
            )
            # 套用 market filter（上市/上櫃/全選）。
            if listed_only or otc_only:
                keep = lambda m: (listed_only and m == "listed") or (
                    otc_only and m == "otc"
                )
                results = [r for r in results if keep(r.get("market", "listed"))]
            # 把 matched_stock 加上 market 欄位（filter 友善性）
            if matched is not None:
                matched_market = "listed"
                for s in state.stocks:
                    if s.get("code") == matched.get("code"):
                        matched_market = s.get("market", "listed")
                        break
                matched = dict(matched)
                matched["market"] = matched_market
            if not matched and not results:
                return jsonify({"error": f"stock not found: {q}"}), 404
        return jsonify(
            {
                "query": q,
                "matched_stock": matched,
                "count": len(results),
                "results": results,
            }
        )

    @app.get("/api/warrants/screen")
    def api_warrants_screen():
        """快速 metadata-level 篩選（不發 quote → 不會被 ban）。

        Query params:
          - q             代號或名稱（必填）
          - type          認購 / 認售
          - market        listed / otc / all（預設 all）
          - min_days      只列到期日 >= 今日+min_days 的（避免臨到期）
          - max_days      只列到期日 <= 今日+max_days 的
          - has_strike    true/false — 篩 strike_price != null
          - limit         int（預設 200）
        """
        q = request.args.get("q", "").strip()
        if not q:
            return jsonify({"error": "missing q"}), 400
        type_filter = request.args.get("type")
        market = (request.args.get("market") or "all").lower()
        try:
            min_days = int(request.args.get("min_days", "0"))
            max_days = int(request.args.get("max_days", "99999"))
            limit = max(1, min(int(request.args.get("limit", "200")), 1000))
        except ValueError:
            return jsonify({"error": "min_days/max_days/limit must be int"}), 400
        has_strike_raw = request.args.get("has_strike", "").lower()
        require_strike = has_strike_raw in ("1", "true", "yes")

        with state.lock:
            if not state.warrants:
                return jsonify({"error": "warrants data not ready"}), 503
            _, results = search_mod.search_warrants(
                q, state.warrants, state.stocks,
                type=type_filter, new_only=False,
            )

        today = today_taipei()
        filtered = []
        for w in results:
            mkt = w.get("market", "listed")
            if market == "listed" and mkt != "listed":
                continue
            if market == "otc" and mkt != "otc":
                continue
            if require_strike and w.get("strike_price") is None:
                continue
            end = _parse_end_date(w.get("end_date"))
            if end is None:
                continue
            days = (end - today).days
            if days < min_days or days > max_days:
                continue
            row = dict(w)
            row["days_to_expiry"] = days
            row["years_to_expiry"] = year_fraction(days)
            filtered.append(row)

        filtered.sort(key=lambda w: (w.get("days_to_expiry", 0), w.get("code", "")))
        truncated = filtered[limit:]
        filtered = filtered[:limit]

        return jsonify(
            {
                "query": q,
                "matched_stock": _match_stock_for_q(q, state.stocks),
                "count": len(filtered),
                "truncated_count": len(truncated),
                "results": filtered,
                "filters": {
                    "type": type_filter,
                    "market": market,
                    "min_days": min_days,
                    "max_days": max_days,
                    "has_strike": require_strike,
                    "limit": limit,
                },
            }
        )

    @app.get("/api/warrant/<code>/quote")
    def api_warrant_quote(code: str):
        """單檔權證的 5 分鐘延遲報價（TWSE 公開 API）。

        自動判斷上市/上櫃（依代號前綴），亦可用 ``?exchange=otc|tse`` 強制。
        """
        if not code or not code.strip():
            return jsonify({"error": "missing code"}), 400
        exchange_arg = request.args.get("exchange")
        try:
            kwargs = {"exchange": exchange_arg} if exchange_arg else {}
            data = quote_mod.fetch_quote(code.strip(), **kwargs)
        except ValueError as exc:
            return jsonify({"code": code, "error": str(exc)}), 400
        except Exception as exc:  # noqa: BLE001
            return jsonify({"code": code, "found": False, "error": str(exc)}), 502
        if not data.get("found"):
            return jsonify(data), 404
        return jsonify(data)

    @app.get("/api/warrant/<code>/iv")
    def api_warrant_iv(code: str):
        """單檔權證的 implied vol（會發 2 個報價 API，1 warrant + 1 underlying）。

        Query params:
          - underlying    標的股票代號（若 warrant 沒記錄 underlying_code，必須傳）
          - exchange      tse/otc（給 warrant 報價用），底層股報價會依代號判斷
          - r             風險利率（年化，預設 0.02）
        """
        if not code or not code.strip():
            return jsonify({"error": "missing code"}), 400
        warrant_code = code.strip()
        with state.lock:
            warrant = next(
                (w for w in state.warrants if w.get("code") == warrant_code),
                None,
            )
        if not warrant:
            return jsonify({"error": f"warrant not found in cache: {warrant_code}"}), 404

        # resolve underlying code
        underlying_code = request.args.get("underlying") or ""
        if not underlying_code:
            uc = warrant.get("underlying_code", "")
            if uc:
                underlying_code = uc
            else:
                # 從 stock_map 用名稱反查
                uname = warrant.get("underlying", "")
                for s in state.stocks:
                    if s.get("name") == uname:
                        underlying_code = s["code"]
                        break

        if not underlying_code:
            return jsonify(
                {
                    "error": "cannot resolve underlying code; pass ?underlying=XXXX",
                    "warrant_code": warrant_code,
                    "underlying_name": warrant.get("underlying"),
                }
            ), 400

        strike = warrant.get("strike_price")
        if strike is None:
            return jsonify({"error": "warrant has no strike_price"}), 422
        end = _parse_end_date(warrant.get("end_date"))
        if not end:
            return jsonify({"error": "warrant has no end_date"}), 422
        days = (end - today_taipei()).days
        if days <= 0:
            return jsonify({"error": f"warrant expired ({end})"}), 422

        try:
            r = float(request.args.get("r", str(DEFAULT_RISK_FREE_RATE)))
        except ValueError:
            return jsonify({"error": "r must be float"}), 400

        # exercise_ratio：上櫃 row 多帶「exercise_ratio_per_mille」（每仟張配發股數）
        er_per_lot = 1.0
        er_raw = warrant.get("exercise_ratio_per_mille")
        if er_raw:
            er_per_lot = er_raw / 1000.0
        # 上市權證通常 exercise_ratio = 1；若 strike_price 已經是「per share 履約價」
        # 但 warrant_price 也是以「1 張」報價，1 張 = 1000 單位 = 1000 股時需要再除。
        # 台灣上市權證多採 1000 股為一張，所以「1 張 warrant 報價」隱含 1 單位 × 1000。
        # 暫以 OTC 那邊的 per_mille 為主；上市若無此欄位則預設 1.0（per 1000 單位 = 1.0 表示 1 單位對 1 股標的）。
        if warrant.get("market") == "listed":
            er_per_lot = 1.0  # 上市每單位 = 1 股標的

        T = year_fraction(days)
        try:
            warrant_q = quote_mod.fetch_quote(
                warrant_code,
                exchange=request.args.get("exchange"),
            )
            import time as _time
            _time.sleep(1.0)
            underlying_q = quote_mod.fetch_quote(
                underlying_code,
                exchange=request.args.get("underlying_exchange") or (
                    "otc" if warrant.get("market") == "otc" else "tse"
                ),
            )
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": f"quote fetch failed: {exc}"}), 502

        wp = warrant_q.get("last_price") or warrant_q.get("prev_close")
        sp = underlying_q.get("last_price") or underlying_q.get("prev_close")
        if not wp or not sp:
            return jsonify({
                "error": "no price available (post-market or missing warrant quote)",
                "warrant_quote": warrant_q,
                "underlying_quote": underlying_q,
            }), 503

        iv = implied_volatility(
            wp, S=sp, K=strike, T=T, r=r, is_call=True,
            exercise_ratio=er_per_lot,
        )
        greeks = {}
        theoretical = None
        if iv is not None:
            greeks = bs_greeks(S=sp, K=strike, T=T, r=r, sigma=iv, is_call=True)
            theoretical = bs_price(S=sp, K=strike, T=T, r=r, sigma=iv, is_call=True)

        return jsonify({
            "warrant": {
                "code": warrant_code,
                "name": warrant.get("name"),
                "type": warrant.get("type"),
                "strike_price": strike,
                "end_date": format_iso(end),
                "days_to_expiry": days,
                "exercise_ratio_per_lot": er_per_lot,
            },
            "underlying": {
                "code": underlying_code,
                "name": warrant.get("underlying"),
                "price": sp,
            },
            "market_price_per_lot": wp,
            "r": r,
            "T": T,
            "implied_volatility": iv,
            "implied_volatility_pct": iv * 100 if iv is not None else None,
            "greeks": greeks,
            "bs_price_per_share_at_iv": theoretical,
            "quote_used": {"warrant": warrant_q, "underlying": underlying_q},
        })

    @app.errorhandler(404)
    def not_found(_e):
        return jsonify({"error": "not found"}), 404

    return app


def _match_stock_for_q(q: str, stocks: List[dict]) -> Optional[dict]:
    qn = (q or "").strip().lower()
    if not qn:
        return None
    for s in stocks:
        if s["code"].lower() == qn or s["name"].lower() == qn:
            return {"code": s["code"], "name": s["name"], "market": s.get("market", "listed")}
    for s in stocks:
        if qn in s["name"].lower() or qn in s.get("full_name", "").lower():
            return {"code": s["code"], "name": s["name"], "market": s.get("market", "listed")}
    return None

    @app.errorhandler(404)
    def not_found(_e):
        return jsonify({"error": "not found"}), 404

    return app


def _background_init() -> None:
    # 先嘗試讀本地快取
    state.load_from_disk()
    # 嘗試在背景更新 (失敗也沒關係，graceful fail)
    try:
        state.update()
    except Exception as exc:  # noqa: BLE001
        state.last_error = str(exc)


def main() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    t = threading.Thread(target=_background_init, name="init-fetcher", daemon=True)
    t.start()
    app = _create_app()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))


if __name__ == "__main__":
    main()
