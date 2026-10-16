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
from warrant_finder.dates import today_taipei


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

WARRANT_PATH = os.path.join(DATA_DIR, "warrants.json")
WARRANT_TRADE_PATH = os.path.join(DATA_DIR, "warrants_trade.json")
STOCK_PATH = os.path.join(DATA_DIR, "stocks.json")

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
            if os.path.exists(WARRANT_PATH):
                try:
                    self.warrants = data_mod.load_normalized(WARRANT_PATH, WARRANT_TRADE_PATH)
                    self.warrants_mtime = datetime.fromtimestamp(os.path.getmtime(WARRANT_PATH))
                except Exception as exc:  # noqa: BLE001
                    self.last_error = f"warrants load: {exc}"
            if os.path.exists(STOCK_PATH):
                try:
                    self.stocks = stock_map_mod.load_stock_map(STOCK_PATH)
                    self.stocks_mtime = datetime.fromtimestamp(os.path.getmtime(STOCK_PATH))
                except Exception as exc:  # noqa: BLE001
                    self.last_error = (self.last_error or "") + f" stocks load: {exc}"

    def update(self) -> None:
        with self.lock:
            self.last_attempt = datetime.utcnow()
            try:
                fetcher.fetch_all(DATA_DIR, max_age_hours=CACHE_MAX_AGE_HOURS, force=False)
                # 重新讀快取
                self.warrants = data_mod.load_normalized(WARRANT_PATH, WARRANT_TRADE_PATH)
                self.warrants_mtime = datetime.fromtimestamp(os.path.getmtime(WARRANT_PATH))
                self.stocks = stock_map_mod.load_stock_map(STOCK_PATH)
                self.stocks_mtime = datetime.fromtimestamp(os.path.getmtime(STOCK_PATH))
                self.last_error = None
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)


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
            # 找不到任何 stock 對應，但有 results 從 underlying 子字串而來 — 允許
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

    @app.get("/api/warrant/<code>/quote")
    def api_warrant_quote(code: str):
        """單檔權證的 5 分鐘延遲報價（TWSE 公開 API）。"""
        if not code or not code.strip():
            return jsonify({"error": "missing code"}), 400
        try:
            data = quote_mod.fetch_quote(code.strip())
        except Exception as exc:  # noqa: BLE001
            return jsonify({"code": code, "found": False, "error": str(exc)}), 502
        if not data.get("found"):
            return jsonify(data), 404
        return jsonify(data)

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
