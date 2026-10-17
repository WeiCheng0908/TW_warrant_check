"""Black-Scholes-Merton 權證定價 + IV 反解 + Greeks。

純計算模組，**不發任何網路請求**，可供 unit test 完整覆蓋。
設計目標：
- 無外部相依（不依賴 scipy / numpy）。
- 公式採用 BS-Merton（支援連續股息 yield ``q``）。
- ``implied_volatility`` 用 Newton-Raphson 為主、bisection fallback 為穩，
  在合約邊界外回傳 ``None``（不做 silent fallback）。
- Greeks 採用對沖基金通用 convention：
    * vega、rho 為「1% 變動」單位（industry standard）
    * theta 為「1 日」單位（per calendar day）
"""

from __future__ import annotations

import math
from typing import Dict, Optional, Tuple


# --------------------------------------------------------------------------- #
# 預設值：對齊 SonarCue (假設無風險利率 2%)
# --------------------------------------------------------------------------- #
DEFAULT_RISK_FREE_RATE: float = 0.02
DEFAULT_DIVIDEND_YIELD: float = 0.0  # 台股權證極少配息，預設 0

# IV 反解的數值穩定性邊界
_SIGMA_MIN: float = 1e-6
_SIGMA_MAX: float = 5.0  # 500% vol — 超過即視為垃圾輸入
_NR_TOL: float = 1e-7
_NR_MAX_ITER: int = 80
_BISECT_MAX_ITER: int = 200


# --------------------------------------------------------------------------- #
# Standard normal CDF / PDF（純 erfc，無 scipy）
# --------------------------------------------------------------------------- #
def _ndtr(x: float) -> float:
    """Standard normal CDF Φ(x)。

    使用恆等式 Φ(x) = 0.5 · erfc(-x/√2)，比直接 sum 級數準且快。
    """
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def _npdf(x: float) -> float:
    """Standard normal PDF φ(x)。"""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1_d2(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    q: float = DEFAULT_DIVIDEND_YIELD,
) -> Tuple[float, float]:
    """標準 BS d1 / d2。

    Raises ``ValueError`` 當 ``T`` 或 ``sigma`` 非正值。
    """
    if T <= 0:
        raise ValueError(f"T must be > 0, got {T}")
    if sigma <= 0:
        raise ValueError(f"sigma must be > 0, got {sigma}")
    sqrtT = math.sqrt(T)
    v = sigma * sqrtT
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / v
    d2 = d1 - v
    return d1, d2


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def bs_price(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    *,
    q: float = DEFAULT_DIVIDEND_YIELD,
    is_call: bool = True,
) -> float:
    """Black-Scholes-Merton 理論價格。

    Parameters
    ----------
    S : float
        標的現價（>0）
    K : float
        履約價（>0）
    T : float
        距到期年數（>0）
    r : float
        無風險利率（年化，連續複利）
    sigma : float
        波動率（年化，>0）
    q : float
        連續股息率（年化，預設 0）
    is_call : bool
        True=認購, False=認售

    Returns
    -------
    float
        BS 理論價
    """
    d1, d2 = _d1_d2(S, K, T, r, sigma, q)
    if is_call:
        return S * math.exp(-q * T) * _ndtr(d1) - K * math.exp(-r * T) * _ndtr(d2)
    return K * math.exp(-r * T) * _ndtr(-d2) - S * math.exp(-q * T) * _ndtr(-d1)


def bs_greeks(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    *,
    q: float = DEFAULT_DIVIDEND_YIELD,
    is_call: bool = True,
) -> Dict[str, float]:
    """解析 Greeks（單位：vega/rho per 1% vol/rate, theta per day）。

    Returns
    -------
    dict
        ``delta``, ``gamma``, ``vega``, ``theta``, ``rho``
    """
    d1, d2 = _d1_d2(S, K, T, r, sigma, q)
    sqrtT = math.sqrt(T)
    discount = math.exp(-q * T)
    pdf_d1 = _npdf(d1)

    gamma = discount * pdf_d1 / (S * sigma * sqrtT)
    # vega 標 1% sigma 變動：dPrice/d(sigma) = S·e^(-qT)·φ(d1)·√T
    # → per 1% = ÷ 100
    vega = S * discount * pdf_d1 * sqrtT / 100.0

    if is_call:
        delta = discount * _ndtr(d1)
        theta = (
            -S * discount * pdf_d1 * sigma / (2.0 * sqrtT)
            - r * K * math.exp(-r * T) * _ndtr(d2)
            + q * S * discount * _ndtr(d1)
        ) / 365.0
        rho = K * T * math.exp(-r * T) * _ndtr(d2) / 100.0
    else:
        delta = -discount * _ndtr(-d1)
        theta = (
            -S * discount * pdf_d1 * sigma / (2.0 * sqrtT)
            + r * K * math.exp(-r * T) * _ndtr(-d2)
            - q * S * discount * _ndtr(-d1)
        ) / 365.0
        rho = -K * T * math.exp(-r * T) * _ndtr(-d2) / 100.0

    return {
        "delta": delta,
        "gamma": gamma,
        "vega": vega,
        "theta": theta,
        "rho": rho,
    }


def _bs_vega_raw(
    S: float, K: float, T: float, r: float, sigma: float, q: float
) -> float:
    """Raw vega (dPrice / d(sigma) without /100) — Newton-Raphson 用。"""
    d1, _ = _d1_d2(S, K, T, r, sigma, q)
    return S * math.exp(-q * T) * _npdf(d1) * math.sqrt(T)


def implied_volatility(
    market_price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    *,
    q: float = DEFAULT_DIVIDEND_YIELD,
    is_call: bool = True,
    exercise_ratio: float = 1.0,
    tol: float = _NR_TOL,
    sigma_init: float = 0.5,
    sigma_low: float = _SIGMA_MIN,
    sigma_high: float = _SIGMA_MAX,
) -> Optional[float]:
    """從市場價格反推 implied volatility。

    流程：
    1. 先檢查價格是否落在 no-arbitrage bound 內（內含值 ≤ price ≤ 上界），
       否則回傳 ``None``（無法定義合理的 IV）。
    2. Newton-Raphson 為主，``_bs_vega_raw`` 接近 0 時自動切到 bisection。

    Parameters
    ----------
    market_price : float
        觀測到的權證**每張**價格（台灣報價以「一張」為單位）
    S, K, T, r, q, is_call :
        同 ``bs_price``
    exercise_ratio : float
        1 張權證可換多少股標的。台灣權證通常 < 1（例如 0.003 = 每張換 0.003 股）。
        換算上：``warrant_per_share_price = market_price / exercise_ratio``。
        預設 1.0 = 標準 BS（每張 = 1 股）。

    Returns
    -------
    float or None
        找到的 IV；若輸入違反 no-arbitrage bound，回傳 ``None``。
    """
    if T <= 0 or S <= 0 or K <= 0:
        return None
    if market_price <= 0:
        return None
    if exercise_ratio <= 0:
        return None

    # 將「每張 warrant 報價」轉成「每股 warrant 理論價」以套標準 BS
    price_per_share = market_price / exercise_ratio
    return _implied_vol_core(
        price_per_share, S, K, T, r,
        q=q, is_call=is_call,
        tol=tol, sigma_init=sigma_init,
        sigma_low=sigma_low, sigma_high=sigma_high,
    )


def _implied_vol_core(
    market_price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    *,
    q: float,
    is_call: bool,
    tol: float,
    sigma_init: float,
    sigma_low: float,
    sigma_high: float,
) -> Optional[float]:
    """``implied_volatility`` 的內部核心：直接對每股 BS 價格做反解。"""
    disc_r = math.exp(-r * T)
    disc_q = math.exp(-q * T)

    if is_call:
        intrinsic = max(0.0, S * disc_q - K * disc_r)
        upper = S * disc_q
    else:
        intrinsic = max(0.0, K * disc_r - S * disc_q)
        upper = K * disc_r

    if market_price < intrinsic - 1e-9 or market_price > upper + 1e-9:
        return None

    sigma = max(sigma_low, min(sigma_init, sigma_high))

    for _ in range(_NR_MAX_ITER):
        try:
            price = bs_price(S, K, T, r, sigma, q=q, is_call=is_call)
        except ValueError:
            return None
        diff = market_price - price
        if abs(diff) < tol:
            return max(sigma_low, min(sigma, sigma_high))

        vega = _bs_vega_raw(S, K, T, r, sigma, q)
        if vega < 1e-12:
            break

        new_sigma = sigma + diff / vega
        if new_sigma <= sigma_low:
            new_sigma = sigma / 2.0
        elif new_sigma >= sigma_high:
            new_sigma = (sigma + sigma_high) / 2.0
        sigma = new_sigma

    lo, hi = sigma_low, sigma_high
    for _ in range(_BISECT_MAX_ITER):
        mid = 0.5 * (lo + hi)
        try:
            price = bs_price(S, K, T, r, mid, q=q, is_call=is_call)
        except ValueError:
            return None
        diff = market_price - price
        if abs(diff) < tol or (hi - lo) < tol:
            return max(sigma_low, min(mid, sigma_high))
        if diff > 0:
            lo = mid
        else:
            hi = mid

    return None


def year_fraction(days_remaining: float, day_count: int = 365) -> float:
    """剩餘天數 → 年化係數。

    台灣慣例使用 ACT/365（簡單除以 365），不計閏年。
    若 ``days_remaining <= 0`` 回傳 0（避免 IV 反解時的 T<=0 錯誤）。
    """
    if days_remaining <= 0:
        return 0.0
    return days_remaining / float(day_count)


__all__ = [
    "DEFAULT_RISK_FREE_RATE",
    "DEFAULT_DIVIDEND_YIELD",
    "bs_price",
    "bs_greeks",
    "implied_volatility",
    "year_fraction",
]
