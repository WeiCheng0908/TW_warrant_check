"""Demo: 為 6187 萬潤 找一支認購權證，計算 implied volatility。

資料源：上櫃 (OTC/TPEX) via mopsfin.twse.com.tw，
因為 6187 萬潤是上櫃股，TWSE 上市端點 (t187ap37_L) 沒有它的權證。

請求預算（cache 在 data/ 後就不再發）：
- t187ap37_O.csv 已抓（7.9 MB，本次 demo 跳過重抓）
- t187ap03_O.csv 已抓（538 KB，本次 demo 跳過重抓）
- 1× mis.twse.com.tw warrant 報價
- 1× mis.twse.com.tw 6187 標的報價
"""

from __future__ import annotations

import csv
import io
import sys
import time
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from warrant_finder.dates import roc_to_date, today_taipei
from warrant_finder.iv import (
    DEFAULT_RISK_FREE_RATE,
    bs_greeks,
    bs_price,
    implied_volatility,
    year_fraction,
)
from warrant_finder.quote import fetch_quote


TARGET_CODE = "6187"  # 萬潤
DATA_DIR = HERE / "data"
QUOTE_SLEEP_SEC = 1.0


# --------------------------------------------------------------------------- #
# CSV loading (OTC source uses CSV, listed source uses JSON)
# --------------------------------------------------------------------------- #
def _strip_bom(b: bytes) -> bytes:
    return b[3:] if b.startswith(b"\xef\xbb\xbf") else b


def _read_otc_csv(path: Path) -> list[dict]:
    with open(path, "rb") as f:
        raw = _strip_bom(f.read()).decode("utf-8")
    return list(csv.DictReader(io.StringIO(raw)))


def load_otc_stocks(path: Path) -> list[dict]:
    rows = _read_otc_csv(path)
    out = []
    for r in rows:
        code = (r.get("公司代號") or "").strip()
        name = (r.get("公司簡稱") or "").strip()
        if code and name:
            out.append({"code": code, "name": name, "full_name": r.get("公司名稱") or ""})
    return out


def load_otc_warrants(path: Path) -> list[dict]:
    """把上櫃權證 CSV 轉成跟 ``load_normalized`` 相容的 shape。"""
    rows = _read_otc_csv(path)
    out = []
    for r in rows:
        out.append(
            {
                "code": (r.get("權證代號") or "").strip(),
                "name": (r.get("權證簡稱") or "").strip(),
                "type": (r.get("權證類型") or "").strip(),
                "category": (r.get("類別") or "").strip(),
                "underlying": (r.get("標的證券/指數") or "").strip(),
                # 上櫃 CSV 沒有 ``標的證券代號`` 欄位（用名稱比對）
                "underlying_code": "",
                "start_date": (r.get("履約開始日") or "").strip(),
                "end_date": (r.get("最後交易日") or "").strip(),
                "expire_date": (r.get("履約截止日") or "").strip(),
                "strike_price": _to_float(r.get("最新履約價格(元)/履約指數")),
                "exercise_ratio": _to_float(r.get("最新標的履約配發數量(每仟單位權證)")),
                "issue_size": _to_float(r.get("發行單位數量(仟單位)")),
                "issuer": "",  # OTC CSV 沒有發行人欄位
                "notes": (r.get("備註") or "").strip(),
            }
        )
    return out


def _to_float(v) -> float | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s or s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Find + pick
# --------------------------------------------------------------------------- #
def find_warrants(warrants: list, code: str, name: str | None) -> list[dict]:
    out, seen = [], set()
    # OTC CSV 沒有標的代號，只能用名稱比對
    if name:
        for w in warrants:
            if w.get("underlying") == name and w["code"] not in seen:
                out.append(w)
                seen.add(w["code"])
    return out


def pick_demo_warrant(warrants: list, preferred_code: str | None = None) -> dict | None:
    # 若指定特定 code（拿來對齊 SonarCue 公開值）
    if preferred_code:
        for w in warrants:
            if w["code"] == preferred_code:
                if w["type"] == "認購" and w.get("strike_price") and w.get("end_date"):
                    return w
                print(f"  指定權證 {preferred_code} 不是認購或缺 strike/end_date，無法比較。")
                return None
        print(f"  沒找到 {preferred_code}，改用默認排序。")

    candidates = [
        w
        for w in warrants
        if w["type"] == "認購"
        and w.get("strike_price")
        and w.get("end_date")
    ]
    if not candidates:
        return None
    # 預設按到期日遠→近（較穩定）
    candidates.sort(key=lambda w: str(w["end_date"]), reverse=True)
    return candidates[0]


# --------------------------------------------------------------------------- #
# Demo
# --------------------------------------------------------------------------- #
def main() -> None:
    print("=" * 60)
    print(f"Demo: 計算 {TARGET_CODE} (萬潤) 一檔認購權證的 implied vol")
    print("=" * 60)

    print("\n[load] 上櫃權證 / 公司 CSV")
    stocks = load_otc_stocks(DATA_DIR / "stocks_otc.csv")
    warrants = load_otc_warrants(DATA_DIR / "warrants_otc.csv")
    print(f"  stocks   : {len(stocks)}")
    print(f"  warrants : {len(warrants)}")

    target_name = None
    for s in stocks:
        if s["code"] == TARGET_CODE:
            target_name = s["name"]
            print(f"\n[target] {TARGET_CODE} = {s['name']} ({s['full_name'][:30]})")
            break
    if not target_name:
        print(f"\n[target] {TARGET_CODE} = (? not in stocks_otc.csv)")

    matches = find_warrants(warrants, TARGET_CODE, target_name)
    print(f"\n[warrants under {TARGET_CODE}] {len(matches)} 檔（OTC）")

    if not matches:
        print("  無命中。中止。")
        return

    candidates = [w for w in matches if w["type"] == "認購" and w.get("strike_price") and w.get("end_date")]
    if not candidates:
        print("  沒有合格認購。中止。")
        return
    candidates.sort(key=lambda w: str(w["end_date"]), reverse=True)

    print("\n  Top 3 candidates（依到期日遠 → 近）:")
    for w in candidates[:3]:
        print(
            f"    {w['code']:<10}  {w['type']:<4}  "
            f"strike={w['strike_price']:>8.2f}  end={w['end_date']}  {w['name']}"
        )

    pick = pick_demo_warrant(candidates, preferred_code="712218")
    if not pick:
        print("  沒抓到認購。中止。")
        return
    print(f"\n[pick] {pick['code']}  {pick['name']}")
    print(f"  exercise_ratio={pick.get('exercise_ratio')}  (1 仟張權證可配發的股數)")

    # ---- 抓報價 ----
    # 712218 是上櫃權證（detected via leading 7），6187 是上櫃股票（需明示）
    print(f"\n[quote] (sleep {QUOTE_SLEEP_SEC}s between)")
    wq = fetch_quote(pick["code"])  # 7xxx 自動 otc
    time.sleep(QUOTE_SLEEP_SEC)
    sq = fetch_quote(TARGET_CODE, exchange="otc")  # 6187 是上櫃股，要明示

    def show(label, q):
        print(
            f"  {label:<12}: last={q.get('last_price')!r}  prev={q.get('prev_close')!r}  "
            f"mkt_open={q.get('market_open')}  found={q.get('found')}"
        )

    show(pick["code"], wq)
    show(TARGET_CODE, sq)

    warrant_px = wq.get("last_price") or wq.get("prev_close")
    underlying_px = sq.get("last_price") or sq.get("prev_close")
    if not warrant_px or not underlying_px:
        print("\n  報價缺資料，無法算 IV。")
        return

    K = float(pick["strike_price"])
    end = roc_to_date(pick["end_date"])
    if not end:
        print(f"\n  end_date={pick['end_date']} 無法解析。")
        return
    today = today_taipei()
    days = (end - today).days
    if days <= 0:
        print(f"\n  權證已過期（end={end} < today={today}）。")
        return
    T = year_fraction(days)

    # Exercise ratio — 母檔「最新標的履約配發數量」是「每仟單位權證」
    # 可換到的股數，例如 3.00 → 1 張 = 0.003 股。
    er_raw = pick.get("exercise_ratio") or 0.0  # per 1000 張
    er_per_lot = (er_raw / 1000.0) if er_raw else 1.0
    if er_per_lot != 1.0:
        print(
            f"\n[note] exercise ratio = {er_raw}/仟 → 1 張權證配 {er_per_lot:.4f} 股標的\n"
            f"        換算：每張報價 {warrant_px} 元 → 每 share 基礎 = {warrant_px / er_per_lot:.2f} 元"
        )

    print(
        f"\n[IV input]\n"
        f"  warrant_price (per 1 lot) = {warrant_px}\n"
        f"  warrant_price (per share) = {warrant_px / er_per_lot:.2f}\n"
        f"  underlying S              = {underlying_px}\n"
        f"  strike K                  = {K}\n"
        f"  exercise_ratio            = {er_per_lot}\n"
        f"  T (year frac)             = {T:.6f}  ({days} days)\n"
        f"  r                         = {DEFAULT_RISK_FREE_RATE}"
    )

    iv = implied_volatility(
        warrant_px, S=underlying_px, K=K, T=T,
        r=DEFAULT_RISK_FREE_RATE, is_call=True,
        exercise_ratio=er_per_lot,
    )
    if iv is None:
        print("\n  IV 反解失敗 — 價格可能落在 no-arbitrage bound 之外。")
        return

    print(f"\n[result]")
    print(f"  implied vol = {iv:.6f}  ({iv * 100:.2f}%)")

    greeks = bs_greeks(S=underlying_px, K=K, T=T, r=DEFAULT_RISK_FREE_RATE, sigma=iv, is_call=True)
    print("  greeks:")
    for k, v in greeks.items():
        print(f"    {k:6s} = {v:+.6f}")

    theoretical = bs_price(S=underlying_px, K=K, T=T, r=DEFAULT_RISK_FREE_RATE, sigma=iv, is_call=True)
    print(
        f"\n[sanity]\n"
        f"  BS price at σ = {theoretical:.4f}\n"
        f"  market price  = {warrant_px:.4f}\n"
        f"  diff          = {theoretical - warrant_px:+.6f}"
    )

    moneyness = underlying_px / K
    intrinsic = max(0.0, underlying_px - K)
    tv = warrant_px - intrinsic
    print(
        f"\n[context]\n"
        f"  S/K         = {moneyness:.4f} ({'ITM' if moneyness > 1 else 'OTM'})\n"
        f"  intrinsic   = {intrinsic:.4f}\n"
        f"  time value  = {tv:.4f}"
    )

    print(
        f"\n[compare with SonarCue]\n"
        f"  SonarCue 公開 IV (同檔 712218, 2026-10-16) = 115.4%\n"
        f"  自家 BS IV（含 exercise_ratio 換算）       = {iv*100:.2f}%\n"
        f"  差距：{abs(iv*100 - 115.4):.2f} pp（可能因子：除權息調整、配發比例時間錯位、bidask 中價 vs 收盤）"
    )


if __name__ == "__main__":
    main()
