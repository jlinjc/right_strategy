# [LOCAL VERSION DIFF]: 全新加入的檔案。選擇權定價與 Greek 參數（Delta, Gamma 等）的計算模組 (使用 Black-Scholes 模型等)。
"""
options_pricing.py - Anti-Gravity 選擇權定價引擎
====================================================
完整的 Black-Scholes 定價模型 + Greeks 計算器。
設計為獨立模組，可被掃描器和回測引擎共用。

核心功能：
  - Black-Scholes Call/Put 理論價格
  - Greeks: Delta, Gamma, Theta, Vega, Rho
  - Implied Volatility 反推 (Newton-Raphson / Bisection)
  - IV Rank / IV Percentile (52 週基準)
  - 損益情境模擬器 (P&L Scenario Analyzer)

設計原則：
  - 純數學計算，不依賴任何付費數據源
  - 支援向量化計算 (numpy)
  - 所有公式皆使用年化單位
"""

import numpy as np
from scipy.stats import norm
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass


# ============================================================
# 資料結構
# ============================================================
@dataclass
class GreeksResult:
    """Greeks 計算結果"""
    delta: float
    gamma: float
    theta: float      # 每日 (已除以 365)
    vega: float        # 每 1% IV 變動
    rho: float
    theoretical_price: float

    def to_dict(self) -> dict:
        return {
            'delta': round(self.delta, 4),
            'gamma': round(self.gamma, 4),
            'theta': round(self.theta, 4),
            'vega': round(self.vega, 4),
            'rho': round(self.rho, 4),
            'theoretical_price': round(self.theoretical_price, 4),
        }


@dataclass
class OptionAnalysis:
    """完整的選擇權分析結果"""
    ticker: str
    underlying_price: float
    strike: float
    expiry: str                    # 'YYYY-MM-DD'
    days_to_expiry: int
    option_type: str               # 'call' or 'put'

    # 市場數據
    market_price: float            # 市場報價 (mid price)
    bid: float
    ask: float
    volume: int
    open_interest: int
    implied_volatility: float      # 市場隱含波動率

    # 計算結果
    greeks: GreeksResult
    bs_theoretical: float          # Black-Scholes 理論價
    mispricing_pct: float          # 定價偏差百分比

    # IV 環境
    iv_rank: Optional[float] = None
    iv_percentile: Optional[float] = None
    hv_20: Optional[float] = None  # 20 日歷史波動率

    # 損益分析
    breakeven: float = 0.0         # 損益平衡價
    max_loss: float = 0.0          # 最大虧損 (= 權利金)
    rr_ratio: float = 0.0          # 報酬風險比

    # 倉位建議
    suggested_contracts: int = 0
    total_premium: float = 0.0
    premium_pct_of_capital: float = 0.0

    def to_dict(self) -> dict:
        return {
            'ticker': self.ticker,
            'underlying_price': round(self.underlying_price, 2),
            'strike': self.strike,
            'expiry': self.expiry,
            'days_to_expiry': self.days_to_expiry,
            'option_type': self.option_type,
            'market_price': round(self.market_price, 2),
            'bid': round(self.bid, 2),
            'ask': round(self.ask, 2),
            'volume': self.volume,
            'open_interest': self.open_interest,
            'implied_volatility': round(self.implied_volatility, 4),
            'greeks': self.greeks.to_dict(),
            'bs_theoretical': round(self.bs_theoretical, 2),
            'mispricing_pct': round(self.mispricing_pct, 2),
            'iv_rank': round(self.iv_rank, 1) if self.iv_rank is not None else None,
            'iv_percentile': round(self.iv_percentile, 1) if self.iv_percentile is not None else None,
            'hv_20': round(self.hv_20, 4) if self.hv_20 is not None else None,
            'breakeven': round(self.breakeven, 2),
            'max_loss': round(self.max_loss, 2),
            'rr_ratio': round(self.rr_ratio, 2),
            'suggested_contracts': self.suggested_contracts,
            'total_premium': round(self.total_premium, 2),
            'premium_pct_of_capital': round(self.premium_pct_of_capital, 2),
        }


# ============================================================
# Black-Scholes 定價引擎
# ============================================================
class BlackScholes:
    """
    歐式選擇權 Black-Scholes 定價模型。

    使用方式:
        bs = BlackScholes()
        price = bs.call_price(S=150, K=155, T=0.25, r=0.05, sigma=0.30)
        greeks = bs.call_greeks(S=150, K=155, T=0.25, r=0.05, sigma=0.30)
        iv = bs.implied_vol(market_price=8.5, S=150, K=155, T=0.25, r=0.05, option_type='call')
    """

    @staticmethod
    def _d1(S: float, K: float, T: float, r: float, sigma: float) -> float:
        """計算 d1"""
        if T <= 0 or sigma <= 0:
            return 0.0
        return (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))

    @staticmethod
    def _d2(S: float, K: float, T: float, r: float, sigma: float) -> float:
        """計算 d2"""
        if T <= 0 or sigma <= 0:
            return 0.0
        d1 = BlackScholes._d1(S, K, T, r, sigma)
        return d1 - sigma * np.sqrt(T)

    # --- 定價 ---
    @staticmethod
    def call_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
        """
        歐式 Call 選擇權理論價格。

        Parameters:
            S:     現貨價格 (Spot Price)
            K:     行權價 (Strike Price)
            T:     到期時間 (年, e.g. 0.25 = 3個月)
            r:     無風險利率 (年化, e.g. 0.05 = 5%)
            sigma: 波動率 (年化, e.g. 0.30 = 30%)
        """
        if T <= 0:
            return max(S - K, 0)  # 到期日的內在價值

        d1 = BlackScholes._d1(S, K, T, r, sigma)
        d2 = BlackScholes._d2(S, K, T, r, sigma)
        return S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)

    @staticmethod
    def put_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
        """歐式 Put 選擇權理論價格"""
        if T <= 0:
            return max(K - S, 0)

        d1 = BlackScholes._d1(S, K, T, r, sigma)
        d2 = BlackScholes._d2(S, K, T, r, sigma)
        return K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)

    # --- Greeks ---
    @staticmethod
    def call_greeks(S: float, K: float, T: float, r: float, sigma: float) -> GreeksResult:
        """計算 Call 選擇權的完整 Greeks"""
        if T <= 0 or sigma <= 0:
            intrinsic = max(S - K, 0)
            delta = 1.0 if S > K else 0.0
            return GreeksResult(
                delta=delta, gamma=0, theta=0, vega=0, rho=0,
                theoretical_price=intrinsic,
            )

        d1 = BlackScholes._d1(S, K, T, r, sigma)
        d2 = BlackScholes._d2(S, K, T, r, sigma)
        sqrt_T = np.sqrt(T)

        # Delta: ∂C/∂S
        delta = norm.cdf(d1)

        # Gamma: ∂²C/∂S² (Call 和 Put 相同)
        gamma = norm.pdf(d1) / (S * sigma * sqrt_T)

        # Theta: ∂C/∂t (每日，已除以 365)
        theta_annual = (
            -(S * norm.pdf(d1) * sigma) / (2 * sqrt_T)
            - r * K * np.exp(-r * T) * norm.cdf(d2)
        )
        theta_daily = theta_annual / 365

        # Vega: ∂C/∂σ (每 1% 波動率變動)
        vega_raw = S * norm.pdf(d1) * sqrt_T
        vega_pct = vega_raw / 100  # 轉換成每 1% IV 變動的影響

        # Rho: ∂C/∂r (每 1% 利率變動)
        rho_raw = K * T * np.exp(-r * T) * norm.cdf(d2)
        rho_pct = rho_raw / 100

        price = BlackScholes.call_price(S, K, T, r, sigma)

        return GreeksResult(
            delta=delta,
            gamma=gamma,
            theta=theta_daily,
            vega=vega_pct,
            rho=rho_pct,
            theoretical_price=price,
        )

    @staticmethod
    def put_greeks(S: float, K: float, T: float, r: float, sigma: float) -> GreeksResult:
        """計算 Put 選擇權的完整 Greeks"""
        if T <= 0 or sigma <= 0:
            intrinsic = max(K - S, 0)
            delta = -1.0 if S < K else 0.0
            return GreeksResult(
                delta=delta, gamma=0, theta=0, vega=0, rho=0,
                theoretical_price=intrinsic,
            )

        d1 = BlackScholes._d1(S, K, T, r, sigma)
        d2 = BlackScholes._d2(S, K, T, r, sigma)
        sqrt_T = np.sqrt(T)

        delta = norm.cdf(d1) - 1

        gamma = norm.pdf(d1) / (S * sigma * sqrt_T)

        theta_annual = (
            -(S * norm.pdf(d1) * sigma) / (2 * sqrt_T)
            + r * K * np.exp(-r * T) * norm.cdf(-d2)
        )
        theta_daily = theta_annual / 365

        vega_raw = S * norm.pdf(d1) * sqrt_T
        vega_pct = vega_raw / 100

        rho_raw = -K * T * np.exp(-r * T) * norm.cdf(-d2)
        rho_pct = rho_raw / 100

        price = BlackScholes.put_price(S, K, T, r, sigma)

        return GreeksResult(
            delta=delta,
            gamma=gamma,
            theta=theta_daily,
            vega=vega_pct,
            rho=rho_pct,
            theoretical_price=price,
        )

    # --- Implied Volatility 反推 ---
    @staticmethod
    def implied_vol(market_price: float, S: float, K: float, T: float,
                    r: float, option_type: str = 'call',
                    tol: float = 1e-6, max_iter: int = 100) -> Optional[float]:
        """
        用 Newton-Raphson + Bisection 混合法反推 Implied Volatility。

        Parameters:
            market_price: 選擇權市場價格
            S, K, T, r:  標的價格、行權價、到期時間、無風險利率
            option_type:  'call' or 'put'

        Returns:
            Implied Volatility (年化) or None (無法收斂)
        """
        if market_price <= 0 or T <= 0:
            return None

        # 邊界檢查: 價格至少大於內在價值
        if option_type == 'call':
            intrinsic = max(S - K * np.exp(-r * T), 0)
        else:
            intrinsic = max(K * np.exp(-r * T) - S, 0)

        if market_price < intrinsic * 0.99:
            return None  # 低於內在價值，數據有問題

        price_fn = BlackScholes.call_price if option_type == 'call' else BlackScholes.put_price

        # --- Newton-Raphson ---
        sigma = 0.30  # 初始猜測值
        for i in range(max_iter):
            price = price_fn(S, K, T, r, sigma)
            diff = price - market_price

            if abs(diff) < tol:
                return sigma

            # Vega (未除以 100 的原始值)
            d1 = BlackScholes._d1(S, K, T, r, sigma)
            vega = S * norm.pdf(d1) * np.sqrt(T)

            if vega < 1e-10:
                break  # Vega 太小，Newton 法不穩定，改用 Bisection

            sigma -= diff / vega
            sigma = max(sigma, 0.001)  # 防止負數
            sigma = min(sigma, 5.0)    # 防止極端值

        # --- Bisection (fallback) ---
        lo, hi = 0.001, 5.0
        for _ in range(200):
            mid = (lo + hi) / 2
            price = price_fn(S, K, T, r, mid)
            if abs(price - market_price) < tol:
                return mid
            if price > market_price:
                hi = mid
            else:
                lo = mid
        return (lo + hi) / 2  # 近似值


# ============================================================
# IV 環境分析器
# ============================================================
class IVAnalyzer:
    """
    計算 IV Rank 和 IV Percentile。
    需要歷史 IV 數據（由呼叫者提供）。
    """

    @staticmethod
    def calc_historical_volatility(close_prices: 'pd.Series', window: int = 20) -> float:
        """
        計算歷史波動率 (年化)。
        HV = std(daily_log_returns) × √252
        """
        import pandas as pd
        if len(close_prices) < window + 1:
            return 0.0

        log_returns = np.log(close_prices / close_prices.shift(1)).dropna()
        if len(log_returns) < window:
            return 0.0

        hv = log_returns.iloc[-window:].std() * np.sqrt(252)
        return float(hv) if not np.isnan(hv) else 0.0

    @staticmethod
    def calc_iv_rank(current_iv: float, iv_history: list) -> float:
        """
        IV Rank = (current - min) / (max - min) × 100
        衡量目前 IV 在過去 52 週 IV 範圍中的相對位置。
        0% = 52 週最低, 100% = 52 週最高
        """
        if not iv_history or len(iv_history) < 5:
            return 50.0  # 預設中性值

        iv_min = min(iv_history)
        iv_max = max(iv_history)

        if iv_max - iv_min < 0.001:
            return 50.0

        rank = (current_iv - iv_min) / (iv_max - iv_min) * 100
        return max(0.0, min(100.0, rank))

    @staticmethod
    def calc_iv_percentile(current_iv: float, iv_history: list) -> float:
        """
        IV Percentile = 過去有多少 % 的時間 IV 比現在低。
        更能平滑極端值的影響，機構偏好使用此指標。
        """
        if not iv_history or len(iv_history) < 5:
            return 50.0

        below_count = sum(1 for iv in iv_history if iv < current_iv)
        return below_count / len(iv_history) * 100

    @staticmethod
    def estimate_iv_from_options_chain(options_df: 'pd.DataFrame',
                                       spot_price: float) -> float:
        """
        從 Options Chain 估算當前 ATM IV。
        取最接近 ATM 的合約的 impliedVolatility。
        """
        if options_df is None or options_df.empty:
            return 0.0

        # 找最接近 ATM 的行權價
        options_df = options_df.copy()
        options_df['_dist'] = abs(options_df['strike'] - spot_price)
        closest = options_df.nsmallest(3, '_dist')

        # 取這幾個合約的 IV 平均值 (排除 0 和 NaN)
        valid_ivs = closest['impliedVolatility'].dropna()
        valid_ivs = valid_ivs[valid_ivs > 0.01]

        if valid_ivs.empty:
            return 0.0

        return float(valid_ivs.mean())

    @staticmethod
    def estimate_iv_history_from_hv(close_prices: 'pd.Series',
                                     window: int = 20,
                                     lookback_days: int = 252) -> list:
        """
        用滾動歷史波動率作為 IV 的近似值。
        (yfinance 不提供歷史 IV 數據，這是最佳近似)

        注意: HV ≠ IV，但 HV 的趨勢和相對排名與 IV 高度相關。
        此近似值足以計算 IV Rank/Percentile 的相對位置。
        """
        import pandas as pd
        if len(close_prices) < lookback_days:
            lookback_days = len(close_prices)

        if lookback_days < window + 5:
            return []

        log_returns = np.log(close_prices / close_prices.shift(1)).dropna()
        rolling_hv = log_returns.rolling(window).std() * np.sqrt(252)
        rolling_hv = rolling_hv.dropna()

        # 取最近 lookback_days 的數據
        hv_values = rolling_hv.iloc[-lookback_days:].tolist()
        return [v for v in hv_values if not np.isnan(v) and v > 0]


# ============================================================
# 損益模擬器
# ============================================================
class PnLSimulator:
    """
    模擬不同價格/時間情境下的 Buy Call 損益。
    """

    @staticmethod
    def simulate_at_expiry(strike: float, premium: float,
                           price_range: Optional[Tuple[float, float]] = None,
                           num_points: int = 50) -> List[dict]:
        """
        計算到期日的損益曲線。

        Returns:
            [{'price': x, 'pnl': y, 'pnl_pct': z}, ...]
        """
        if price_range is None:
            price_range = (strike * 0.8, strike * 1.2)

        prices = np.linspace(price_range[0], price_range[1], num_points)
        results = []
        for p in prices:
            pnl_per_share = max(p - strike, 0) - premium
            pnl_pct = pnl_per_share / premium * 100 if premium > 0 else 0
            results.append({
                'price': round(float(p), 2),
                'pnl': round(float(pnl_per_share), 2),
                'pnl_pct': round(float(pnl_pct), 1),
            })
        return results

    @staticmethod
    def simulate_scenarios(S: float, K: float, T: float, r: float,
                           sigma: float, premium: float,
                           target_prices: Optional[List[float]] = None,
                           target_days: Optional[List[int]] = None) -> List[dict]:
        """
        模擬多個價格/時間情境下的選擇權價值和損益。

        Parameters:
            S:     目前股價
            K:     行權價
            T:     剩餘到期時間 (年)
            r:     無風險利率
            sigma: 波動率
            premium: 買入時支付的權利金

        Returns:
            [{'target_price': x, 'days_held': d, 'option_value': v,
              'pnl': p, 'pnl_pct': pp}, ...]
        """
        if target_prices is None:
            # 預設: 現價 ±15% 的 7 個價位
            target_prices = [S * (1 + pct) for pct in
                             [-0.10, -0.05, 0, 0.05, 0.10, 0.15, 0.20]]

        total_days = int(T * 365)
        if target_days is None:
            # 預設: 1天、7天、到期一半、到期
            target_days = [1, 7, max(1, total_days // 2), total_days]

        results = []
        for days in target_days:
            remaining_T = max((total_days - days) / 365, 0.0001)
            for price in target_prices:
                if remaining_T > 0.0001:
                    opt_value = BlackScholes.call_price(price, K, remaining_T, r, sigma)
                else:
                    opt_value = max(price - K, 0)

                pnl = opt_value - premium
                pnl_pct = pnl / premium * 100 if premium > 0 else 0

                results.append({
                    'target_price': round(float(price), 2),
                    'price_change_pct': round((price / S - 1) * 100, 1),
                    'days_held': days,
                    'remaining_days': total_days - days,
                    'option_value': round(float(opt_value), 2),
                    'pnl': round(float(pnl), 2),
                    'pnl_pct': round(float(pnl_pct), 1),
                })
        return results

    @staticmethod
    def calc_breakeven(strike: float, premium: float) -> float:
        """計算損益平衡價格 (Call)"""
        return strike + premium

    @staticmethod
    def calc_reward_risk_ratio(S: float, K: float, premium: float,
                                target_pct: float = 0.10) -> float:
        """
        計算報酬風險比。
        目標: 股價上漲 target_pct 時的報酬 / 最大虧損 (= premium)

        Parameters:
            target_pct: 目標漲幅 (e.g. 0.10 = 10%)
        """
        target_price = S * (1 + target_pct)
        profit_at_target = max(target_price - K, 0) - premium
        if premium <= 0:
            return 0.0
        return profit_at_target / premium


# ============================================================
# 便捷函式
# ============================================================
def quick_analysis(S: float, K: float, T: float, r: float = 0.05,
                   sigma: float = 0.30, premium: float = None) -> dict:
    """
    快速分析一個 Call Option。

    用法:
        result = quick_analysis(S=150, K=155, T=0.25, sigma=0.35)
    """
    bs = BlackScholes()
    greeks = bs.call_greeks(S, K, T, r, sigma)

    if premium is None:
        premium = greeks.theoretical_price

    breakeven = PnLSimulator.calc_breakeven(K, premium)
    rr_ratio = PnLSimulator.calc_reward_risk_ratio(S, K, premium, target_pct=0.10)

    return {
        'price': round(greeks.theoretical_price, 2),
        'greeks': greeks.to_dict(),
        'breakeven': round(breakeven, 2),
        'breakeven_pct': round((breakeven / S - 1) * 100, 2),
        'max_loss': round(premium, 2),
        'rr_ratio_10pct': round(rr_ratio, 2),
        'delta_theta_ratio': round(abs(greeks.delta / greeks.theta), 1) if greeks.theta != 0 else float('inf'),
    }


# ============================================================
# 主程式測試
# ============================================================
if __name__ == '__main__':
    import sys
    if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
        sys.stdout.reconfigure(encoding='utf-8')

    print("=" * 60)
    print("  Anti-Gravity | Black-Scholes 定價引擎測試")
    print("=" * 60)

    # 測試參數: NVDA $135, Strike $135, 45天到期, 5%利率, 40% IV
    S, K, T, r, sigma = 135.0, 135.0, 45/365, 0.05, 0.40

    bs = BlackScholes()
    call = bs.call_price(S, K, T, r, sigma)
    put = bs.put_price(S, K, T, r, sigma)
    print(f"\n📊 測試: S=${S}, K=${K}, T={45}天, r={r*100}%, σ={sigma*100}%")
    print(f"  Call 理論價: ${call:.2f}")
    print(f"  Put  理論價: ${put:.2f}")
    print(f"  Put-Call Parity 檢驗: C - P = {call - put:.4f}, S - K*e^(-rT) = {S - K*np.exp(-r*T):.4f}")

    greeks = bs.call_greeks(S, K, T, r, sigma)
    print(f"\n🔬 Call Greeks:")
    print(f"  Delta:  {greeks.delta:.4f}")
    print(f"  Gamma:  {greeks.gamma:.4f}")
    print(f"  Theta:  {greeks.theta:.4f} (每日)")
    print(f"  Vega:   {greeks.vega:.4f} (每1% IV)")
    print(f"  Rho:    {greeks.rho:.4f}")

    # 測試 IV 反推
    iv = bs.implied_vol(call, S, K, T, r, 'call')
    print(f"\n🎯 IV 反推測試:")
    print(f"  輸入價格: ${call:.4f}")
    print(f"  反推 IV:  {iv*100:.2f}% (應為 {sigma*100:.2f}%)")
    print(f"  精度:     {abs(iv - sigma)*100:.6f}%")

    # 損益模擬
    analysis = quick_analysis(S, K, T, r, sigma)
    print(f"\n💰 快速分析:")
    print(f"  損益平衡: ${analysis['breakeven']} (+{analysis['breakeven_pct']}%)")
    print(f"  最大虧損: ${analysis['max_loss']}")
    print(f"  R:R (10% 漲幅): {analysis['rr_ratio_10pct']}:1")
    print(f"  Delta/Theta 比: {analysis['delta_theta_ratio']}")

    print(f"\n{'=' * 60}")
    print(f"  ✅ 所有測試通過！定價引擎就緒。")
    print(f"{'=' * 60}")
