"""Streamlit 版 TWSE 權證查詢系統。

部署到 Streamlit Cloud 的入口檔。

執行：
    streamlit run streamlit_app.py

架構：
- 複用 ``warrant_finder/`` 模組（fetcher / parser / search / stock_map / data）
- 首次啟動會在背景 thread 抓 TWSE OpenAPI 三個端點並落地到 ``data/``
- 用 ``@st.cache_data`` 快取解析後的資料
- Streamlit Cloud 上 ``data/`` 沒進 git，會自動觸發首次抓取
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone, timedelta

import pandas as pd
import streamlit as st

from warrant_finder import data as data_mod
from warrant_finder import fetcher
from warrant_finder import search as search_mod
from warrant_finder import stock_map as stock_map_mod
from warrant_finder.dates import today_taipei

# --------------------------------------------------------------------------- #
# 路徑 & 設定
# --------------------------------------------------------------------------- #
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
WARRANT_PATH = os.path.join(DATA_DIR, "warrants.json")
WARRANT_TRADE_PATH = os.path.join(DATA_DIR, "warrants_trade.json")
STOCK_PATH = os.path.join(DATA_DIR, "stocks.json")

os.makedirs(DATA_DIR, exist_ok=True)

TZ_TPE = timezone(timedelta(hours=8))


# --------------------------------------------------------------------------- #
# 背景抓取 — 第一次啟動時自動跑
# --------------------------------------------------------------------------- #
_FETCH_LOCK = threading.Lock()
_FETCH_DONE = threading.Event()


def _has_data() -> bool:
    return all(
        os.path.exists(p) and os.path.getsize(p) > 0
        for p in (WARRANT_PATH, STOCK_PATH)
    )


def _background_fetch() -> None:
    """背景 thread：抓三個端點寫到 data/。失敗也不會 crash，因為有 graceful fallback。"""
    with _FETCH_LOCK:
        if _FETCH_DONE.is_set():
            return
        try:
            fetcher.fetch_all(DATA_DIR, max_age_hours=12.0, force=False)
            _FETCH_DONE.set()
        except Exception as exc:  # noqa: BLE001
            # 第一次抓失敗也別讓 UI 整個死 — 用快取（如果有）或顯示空
            st.session_state["fetch_error"] = str(exc)


def _ensure_fetch_started() -> None:
    """Streamlit 每次 rerun 都會呼叫這個；用 Event 確保只抓一次。"""
    if _FETCH_DONE.is_set():
        return
    if "fetch_thread" not in st.session_state:
        t = threading.Thread(target=_background_fetch, name="streamlit-fetch", daemon=True)
        t.start()
        st.session_state["fetch_thread"] = t


# --------------------------------------------------------------------------- #
# 載入資料 — 用 st.cache_data 讓互動期間不會重讀
# --------------------------------------------------------------------------- #
@st.cache_data(ttl=3600, show_spinner=False)
def load_data() -> tuple[list[dict], list[dict], float | None]:
    """回傳 (warrants, stocks, file_mtime)。"""
    warrants: list[dict] = []
    stocks: list[dict] = []
    mtime: float | None = None

    if os.path.exists(WARRANT_PATH):
        try:
            warrants = data_mod.load_normalized(WARRANT_PATH, WARRANT_TRADE_PATH)
            mtime = os.path.getmtime(WARRANT_PATH)
        except Exception as exc:  # noqa: BLE001
            st.session_state["load_error"] = f"warrants 載入失敗: {exc}"

    if os.path.exists(STOCK_PATH):
        try:
            stocks = stock_map_mod.load_stock_map(STOCK_PATH)
            if mtime is None:
                mtime = os.path.getmtime(STOCK_PATH)
        except Exception as exc:  # noqa: BLE001
            st.session_state["load_error"] = (st.session_state.get("load_error", "") + f" stocks 載入失敗: {exc}").strip()

    return warrants, stocks, mtime


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #
st.set_page_config(
    page_title="TWSE 權證查詢",
    page_icon="📈",
    layout="wide",
)

st.title("📈 TWSE 上市權證查詢")
st.caption("資料來源：臺灣證券交易所 OpenAPI（公開、免費）")

# 觸發背景抓取
_ensure_fetch_started()

# 載入（cache 1 小時）
warrants, stocks, data_mtime = load_data()

# 側欄：狀態
with st.sidebar:
    st.header("資料狀態")
    if data_mtime:
        st.success(f"已載入 {len(warrants)} 筆權證、{len(stocks)} 支股票")
        st.caption(f"最後更新：{datetime.fromtimestamp(data_mtime, TZ_TPE).strftime('%Y-%m-%d %H:%M:%S')} (台北時間)")
    else:
        st.warning("尚未有資料")
    st.caption(f"今日：{today_taipei().isoformat()}")

    if "fetch_error" in st.session_state:
        st.error(f"抓取失敗：{st.session_state['fetch_error']}")
    if "load_error" in st.session_state:
        st.error(st.session_state["load_error"])

    if st.button("🔄 強制重新抓取", help="背景抓三個 TWSE 端點（會覆寫 data/）", disabled=not _FETCH_DONE.is_set() and "fetch_thread" in st.session_state):
        with st.spinner("抓取中，~10 分鐘..."):
            try:
                fetcher.fetch_all(DATA_DIR, max_age_hours=0, force=True)
                st.cache_data.clear()
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(f"抓取失敗：{exc}")

    st.divider()
    st.caption(
        "本工具僅供查詢與研究用途，不構成投資建議。"
        "\n資料來自 TWSE OpenAPI，可能與官方公告有出入，"
        "請以 [TWSE 官網](https://www.twse.com.tw) 為準。"
    )

# 主區：搜尋
if not warrants or not stocks:
    st.info("正在準備資料，第一次啟動需要抓取 TWSE 資料（~10 分鐘），請稍候...")
    if "fetch_thread" in st.session_state and st.session_state["fetch_thread"].is_alive():
        with st.spinner("背景抓取中..."):
            # 等最多 3 秒看 Event
            _FETCH_DONE.wait(timeout=3)
    st.stop()

# 搜尋區
col_search, col_type, col_new = st.columns([3, 1, 1])
with col_search:
    q = st.text_input(
        "🔍 輸入股票代號或名稱",
        placeholder="2330 / 台積電 / 台積",
        label_visibility="collapsed",
    )
with col_type:
    type_filter = st.selectbox("類型", ["全部", "認購", "認售"], label_visibility="visible")
with col_new:
    new_only = st.checkbox("只看今天新發行", value=False)

if not q:
    st.info("👆 輸入股票代號或名稱開始查詢")
    st.stop()

# 搜尋
matched, results = search_mod.search_warrants(
    q,
    warrants,
    stocks,
    type=None if type_filter == "全部" else type_filter,
    new_only=new_only,
)

if matched:
    st.subheader(f"配對到：{matched['name']}（{matched['code']}）")
elif not results:
    st.error(f"找不到對應的股票：{q}")
    st.stop()

st.write(f"共 {len(results)} 筆")

# 表格
if results:
    df = pd.DataFrame(
        [
            {
                "代號": r.get("code", ""),
                "簡稱": r.get("name", ""),
                "類型": r.get("type", ""),
                "履約價": r.get("strike_price"),
                "開始日": r.get("start_date", ""),
                "最後交易日": r.get("end_date", ""),
                "到期日": r.get("expire_date", ""),
                "發行量(仟單位)": r.get("issue_size"),
                "備註摘要": (r.get("notes") or "")[:80].replace("\n", " "),
            }
            for r in results
        ]
    )

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "履約價": st.column_config.NumberColumn(format="%.2f"),
            "發行量(仟單位)": st.column_config.NumberColumn(format="%.0f"),
        },
    )

    # 匯出
    csv = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "📥 下載 CSV",
        data=csv,
        file_name=f"warrants_{matched['code'] if matched else q}.csv",
        mime="text/csv",
    )

# 頁尾
st.divider()
st.caption("🤖 由 MiniMax Team Agent 建置｜原始資料：TWSE OpenAPI")
