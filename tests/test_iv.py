"""測試 ``warrant_finder.iv`` — 純計算不碰網路。

涵蓋：
- BS 價格（put-call parity、ATM、ITM/OTM、退化情形）
- Greeks（call/put 各項符號、ATM delta 接近 0.5）
- IV 反解（round-trip 還原、no-arbitrage boundary、無解情況）
- year_fraction
"""

from __future__ import annotations

import math

import pytest

from warrant_finder.iv import (
    DEFAULT_DIVIDEND_YIELD,
    DEFAULT_RISK_FREE_RATE,
    bs_greeks,
    bs_price,
    implied_volatility,
    year_fraction,
)


# --------------------------------------------------------------------------- #
# BS price
# --------------------------------------------------------------------------- #
class TestBsPrice:
    def test_atm_call_with_known_value(self):
        """ATM call (S=K=100, T=1, r=0.05, sigma=0.2) — Hull 標準答案 ≈ 10.4506。"""
        p = bs_price(S=100, K=100, T=1.0, r=0.05, sigma=0.2, is_call=True)
        assert p == pytest.approx(10.4506, abs=1e-3)

    def test_atm_put_mirrors_call(self):
        """ATM call 跟 ATM put 大致相同（OTM put 才有 delta 差）。"""
        c = bs_price(S=100, K=100, T=1.0, r=0.05, sigma=0.2, is_call=True)
        p = bs_price(S=100, K=100, T=1.0, r=0.05, sigma=0.2, is_call=False)
        # put-call parity: C - P = S·exp(-qT) - K·exp(-rT)，q=0
        # → |C - P| = |100 - 100·exp(-0.05)| ≈ 4.877
        assert (c - p) == pytest.approx(100 - 100 * math.exp(-0.05), abs=1e-6)

    def test_put_call_parity(self):
        """通用 put-call parity：C - P = S·e^(-qT) - K·e^(-rT)。"""
        S, K, T, r, sigma, q = 95.0, 100.0, 0.5, 0.04, 0.3, 0.015
        c = bs_price(S, K, T, r, sigma, q=q, is_call=True)
        p = bs_price(S, K, T, r, sigma, q=q, is_call=False)
        parity = S * math.exp(-q * T) - K * math.exp(-r * T)
        assert (c - p) == pytest.approx(parity, abs=1e-8)

    def test_deep_itm_call_collapses_to_intrinsic(self):
        """Deep ITM call (S ≫ K)，權證價 ≈ S - K·exp(-rT)。"""
        S, K, T, r, sigma = 200.0, 100.0, 0.25, 0.05, 0.5
        p = bs_price(S, K, T, r, sigma, is_call=True)
        assert p == pytest.approx(S - K * math.exp(-r * T), abs=0.05)

    def test_deep_otm_call_near_zero(self):
        """Deep OTM call (S ≪ K)，權證價趨近 0（剩下少數 time value）。"""
        S, K, T, r, sigma = 50.0, 100.0, 0.05, 0.05, 0.3
        p = bs_price(S, K, T, r, sigma, is_call=True)
        # 還是有一些時間值，不該是 0，但應該 < 1
        assert 0 < p < 1.0

    def test_invalid_T_raises(self):
        with pytest.raises(ValueError, match="T"):
            bs_price(S=100, K=100, T=0, r=0.02, sigma=0.2)

    def test_invalid_sigma_raises(self):
        with pytest.raises(ValueError, match="sigma"):
            bs_price(S=100, K=100, T=1, r=0.02, sigma=-0.1)


# --------------------------------------------------------------------------- #
# Greeks
# --------------------------------------------------------------------------- #
class TestBsGreeks:
    def test_call_delta_in_unit_interval(self):
        g = bs_greeks(S=100, K=100, T=1.0, r=0.02, sigma=0.3, is_call=True)
        assert 0 < g["delta"] < 1
        # ATM, r=0.02, q=0, sigma=0.3, T=1
        # d1 = (ln(1) + (0.02 + 0.045)·1) / 0.3 ≈ 0.2167
        # delta = e^(-qT) · N(d1) = N(0.2167) ≈ 0.5858
        assert g["delta"] == pytest.approx(0.5858, abs=0.01)

    def test_put_delta_negative(self):
        g = bs_greeks(S=100, K=100, T=1.0, r=0.02, sigma=0.3, is_call=False)
        assert -1 < g["delta"] < 0

    def test_call_put_delta_zero_sum(self):
        """Call delta - Put delta = exp(-qT)（q=0 時為 1）。"""
        gc = bs_greeks(S=100, K=100, T=1.0, r=0.02, sigma=0.3, is_call=True)
        gp = bs_greeks(S=100, K=100, T=1.0, r=0.02, sigma=0.3, is_call=False)
        # call_delta - put_delta 應該都差不多 = e^(-qT)·(N(d1) - N(-d1)) = e^(-qT)
        assert (gc["delta"] - gp["delta"]) == pytest.approx(
            math.exp(-DEFAULT_DIVIDEND_YIELD * 1.0), abs=1e-8
        )

    def test_gamma_always_positive(self):
        gc = bs_greeks(S=100, K=100, T=1.0, r=0.02, sigma=0.3, is_call=True)
        gp = bs_greeks(S=100, K=100, T=1.0, r=0.02, sigma=0.3, is_call=False)
        assert gc["gamma"] > 0
        assert gp["gamma"] == pytest.approx(gc["gamma"], abs=1e-10)

    def test_vega_always_positive(self):
        g = bs_greeks(S=100, K=100, T=1.0, r=0.02, sigma=0.3, is_call=True)
        assert g["vega"] > 0

    def test_theta_negative_for_long_options(self):
        """Long option 的 theta 通常 < 0（除非 deep ITM put + high r 特殊情形）。"""
        gc = bs_greeks(S=100, K=100, T=1.0, r=0.02, sigma=0.3, is_call=True)
        gp = bs_greeks(S=100, K=100, T=1.0, r=0.02, sigma=0.3, is_call=False)
        # ATM call 通常 theta < 0
        assert gc["theta"] < 0
        assert gp["theta"] < 0

    def test_rho_sign(self):
        """Call rho > 0, put rho < 0。"""
        gc = bs_greeks(S=100, K=100, T=1.0, r=0.02, sigma=0.3, is_call=True)
        gp = bs_greeks(S=100, K=100, T=1.0, r=0.02, sigma=0.3, is_call=False)
        assert gc["rho"] > 0
        assert gp["rho"] < 0

    def test_greek_keys(self):
        g = bs_greeks(S=100, K=100, T=1.0, r=0.02, sigma=0.3)
        assert set(g.keys()) == {"delta", "gamma", "vega", "theta", "rho"}


# --------------------------------------------------------------------------- #
# implied_volatility
# --------------------------------------------------------------------------- #
class TestImpliedVolatility:
    def test_round_trip_call(self):
        """給定 sigma 算價格，再用該價格反解 sigma，應回原值。"""
        S, K, T, r, sigma = 100.0, 100.0, 0.5, 0.02, 0.35
        price = bs_price(S, K, T, r, sigma)
        iv = implied_volatility(price, S, K, T, r)
        assert iv is not None
        assert iv == pytest.approx(sigma, abs=1e-5)

    def test_round_trip_put(self):
        S, K, T, r, sigma = 95.0, 100.0, 0.5, 0.02, 0.40
        price = bs_price(S, K, T, r, sigma, is_call=False)
        iv = implied_volatility(price, S, K, T, r, is_call=False)
        assert iv is not None
        assert iv == pytest.approx(sigma, abs=1e-5)

    def test_round_trip_multiple_sigmas(self):
        """多檔不同 sigma 都要能正確反解回來。"""
        for sigma in (0.1, 0.2, 0.4, 0.6, 0.9, 1.2):
            S, K, T, r = 100.0, 100.0, 0.5, 0.02
            price = bs_price(S, K, T, r, sigma)
            iv = implied_volatility(price, S, K, T, r)
            assert iv is not None, f"failed for sigma={sigma}"
            assert iv == pytest.approx(sigma, abs=1e-4), (
                f"sigma={sigma} → iv={iv}"
            )

    def test_round_trip_with_dividend(self):
        S, K, T, r, sigma, q = 100.0, 105.0, 0.75, 0.03, 0.28, 0.012
        price = bs_price(S, K, T, r, sigma, q=q)
        iv = implied_volatility(price, S, K, T, r, q=q)
        assert iv is not None
        assert iv == pytest.approx(sigma, abs=1e-4)

    def test_price_below_intrinsic_returns_none(self):
        """價格 < 內含值（套利機會）回 None。"""
        S, K, T, r = 110.0, 100.0, 0.5, 0.02
        # intrinsic of call = S·e^(-qT) - K·e^(-rT) ≈ 110·1 - 100·0.99 = 11.0
        bad_price = 5.0
        assert implied_volatility(bad_price, S, K, T, r) is None

    def test_price_above_upper_bound_returns_none(self):
        """Call 價格超過 S·e^(-qT)，違反 no-arb。"""
        S, K, T, r = 100.0, 100.0, 0.5, 0.02
        # upper = S·e^(-qT) = 100
        bad_price = 200.0
        assert implied_volatility(bad_price, S, K, T, r) is None

    def test_zero_or_negative_inputs(self):
        assert implied_volatility(1.0, 0, 100, 1, 0.02) is None
        assert implied_volatility(1.0, 100, 0, 1, 0.02) is None
        assert implied_volatility(1.0, 100, 100, 0, 0.02) is None
        assert implied_volatility(0.0, 100, 100, 1, 0.02) is None

    def test_deep_otm_iv_round_trip(self):
        """Deep OTM 也要 round-trip 成功（這是 Newton-Raphson 容易失敗的區域）。"""
        S, K, T, r, sigma = 80.0, 120.0, 0.25, 0.02, 0.5
        price = bs_price(S, K, T, r, sigma)
        iv = implied_volatility(price, S, K, T, r)
        assert iv is not None
        assert iv == pytest.approx(sigma, abs=1e-4)


# --------------------------------------------------------------------------- #
# year_fraction
# --------------------------------------------------------------------------- #
class TestYearFraction:
    def test_half_year(self):
        assert year_fraction(182.5) == pytest.approx(0.5, abs=1e-6)

    def test_zero_or_negative(self):
        assert year_fraction(0) == 0.0
        assert year_fraction(-5) == 0.0

    def test_one_year(self):
        assert year_fraction(365) == pytest.approx(1.0, abs=1e-9)

    def test_custom_day_count(self):
        # 360-day convention
        assert year_fraction(180, day_count=360) == pytest.approx(0.5, abs=1e-9)


# --------------------------------------------------------------------------- #
# Sanity vs SonarCue-like behavior
# --------------------------------------------------------------------------- #
class TestTypicalTaiwanWarrant:
    """用典型台股權證參數檢查數值合理性（不追求精確，僅範圍檢查）。"""

    def test_long_dated_call_iv_in_30_to_100_pct(self):
        """6 個月 ATM call，sigma=0.5 的參數應該反解回 ~0.5。"""
        S, K, T, r = 580.0, 600.0, 0.5, 0.02
        sigma = 0.5
        price = bs_price(S, K, T, r, sigma)
        iv = implied_volatility(price, S, K, T, r)
        assert iv is not None
        assert 0.30 < iv < 0.80  # 確保落在合理區間

    def test_short_dated_otm_put(self):
        """1 個月 OTM put 也應該 round-trip。"""
        S, K, T, r, sigma = 600.0, 580.0, 1 / 12, 0.02, 0.35
        price = bs_price(S, K, T, r, sigma, is_call=False)
        iv = implied_volatility(price, S, K, T, r, is_call=False)
        assert iv is not None
        assert iv == pytest.approx(sigma, abs=1e-4)
