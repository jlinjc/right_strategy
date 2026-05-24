# [LOCAL VERSION DIFF]: 全新加入的檔案。專門用於掃描適合買入 Call 選擇權的標的（結合選擇權定價模型與動能掃描）。
"""
us_scanner_buy_call.py - 機構級 Buy Call 選擇權掃描器
=======================================================
專業基金經理人等級的 Buy Call 策略掃描工具。
五層機構篩選框架：技術面 → 波動率 → 流動性 → 合約選擇 → Greeks 風控。

用法:
  python us_scanner_buy_call.py              # 單次掃描
  python us_scanner_buy_call.py --monitor    # 每 30 分鐘自動掃描

依賴: yfinance, numpy, scipy, pandas
"""

import sys
import time
import json
import os
import warnings

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple

from scanner_base import (
    AI_TECH_STOCKS, BENCHMARK, send_line_notify, save_dashboard_data,
    calc_atr, calculate_position, TOTAL_CAPITAL, RISK_PER_TRADE,
    calculate_market_breadth, DASHBOARD_DIR,
)
from options_pricing import (
    BlackScholes, IVAnalyzer, PnLSimulator, OptionAnalysis, GreeksResult,
)


# ============================================================
# 配置常數
# ============================================================
RISK_FREE_RATE = 0.043              # 美國 10Y 國債殖利率 (2024~2026 區間)
MAX_PREMIUM_PCT = 0.05              # 單筆 Call 最大權利金 = 本金的 5%
MAX_PREMIUM_AMOUNT = TOTAL_CAPITAL * MAX_PREMIUM_PCT

# --- Layer 1: 技術面 ---
MA_LONG = 200                       # 長期均線
MA_MID = 60                         # 中期均線
MA_SHORT_20 = 20                    # 短期均線
MA_SHORT_10 = 10                    # 超短期均線
RSI_PERIOD = 14
RSI_OVERBOUGHT = 72                 # RSI 超買閾值 (排除)

# --- Layer 2: 波動率環境 ---
IV_RANK_MAX = 50                    # IV Rank < 50% 才適合 Buy Call
IV_PERCENTILE_MAX = 60              # IV Percentile < 60%

# --- Layer 3: 流動性 ---
MIN_OPTION_VOLUME = 50              # 最低日成交量
MIN_OPEN_INTEREST = 200             # 最低未平倉量
MAX_SPREAD_PCT = 0.15               # 最大 Bid-Ask Spread (占 mid price)

# --- Layer 4: 合約選擇 ---
# 短天期合約
SHORT_TERM_DTE_MIN = 7              # 最短 7 天
SHORT_TERM_DTE_MAX = 21             # 最長 21 天
# 中天期合約
MID_TERM_DTE_MIN = 25               # 最短 25 天
MID_TERM_DTE_MAX = 90               # 最長 90 天
# Delta 範圍
DELTA_ITM_MIN = 0.60                # ITM: Delta 0.60~0.80
DELTA_ITM_MAX = 0.80
DELTA_ATM_MIN = 0.30                # ATM/OTM: Delta 0.30~0.60
DELTA_ATM_MAX = 0.60
# 綜合 Delta 範圍 (用戶要求全涵蓋)
DELTA_MIN = 0.30
DELTA_MAX = 0.80

# --- Layer 5: Greeks 風控 ---
MIN_RR_RATIO = 1.5                  # 最低報酬風險比 (10% 漲幅目標)
TARGET_PRICE_PCT = 0.10             # 報酬計算的目標漲幅

# --- 財報過濾 ---
EARNINGS_BUFFER_DAYS = 7            # 財報前後 7 天排除


# ============================================================
# 工具函式
# ============================================================
def calc_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    """計算 RSI"""
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def get_next_earnings_date(ticker_obj) -> Optional[datetime]:
    """從 yfinance Ticker 物件取得下次財報日期"""
    try:
        cal = ticker_obj.calendar
        if cal is not None:
            if isinstance(cal, pd.DataFrame) and 'Earnings Date' in cal.columns:
                dates = cal['Earnings Date']
                if len(dates) > 0:
                    return pd.to_datetime(dates.iloc[0])
            elif isinstance(cal, dict):
                if 'Earnings Date' in cal:
                    dates = cal['Earnings Date']
                    if isinstance(dates, list) and len(dates) > 0:
                        return pd.to_datetime(dates[0])
                    elif dates is not None:
                        return pd.to_datetime(dates)
    except Exception:
        pass
    return None


def classify_delta(delta: float) -> str:
    """Delta 分類標籤"""
    if delta >= 0.70:
        return "Deep ITM"
    elif delta >= 0.55:
        return "ITM"
    elif delta >= 0.45:
        return "ATM"
    elif delta >= 0.30:
        return "OTM"
    else:
        return "Deep OTM"


def classify_dte(days: int) -> str:
    """到期日分類標籤"""
    if days <= 14:
        return "短天期 (Weekly)"
    elif days <= 30:
        return "近月"
    elif days <= 60:
        return "中天期"
    else:
        return "遠月"


# ============================================================
# Layer 1: 技術面篩選
# ============================================================
def screen_technicals(ticker: str, df: pd.DataFrame) -> Tuple[bool, dict]:
    """
    技術面篩選：強勢股才值得 Buy Call。

    條件:
      1. 股價 > 200MA 且 > 20MA
      2. 均線多頭排列: 10MA > 20MA > 60MA
      3. RSI 不在超買區 (< 72)
      4. 加分: 回測均線 (低買點)
    """
    if len(df) < MA_LONG:
        return False, {'reason': f'數據不足 ({len(df)} < {MA_LONG})'}

    close = df['Close'].iloc[-1]
    high = df['High'].iloc[-1]
    low = df['Low'].iloc[-1]

    # 計算均線
    ma200 = df['Close'].rolling(MA_LONG).mean().iloc[-1]
    ma60 = df['Close'].rolling(MA_MID).mean().iloc[-1]
    ma20 = df['Close'].rolling(MA_SHORT_20).mean().iloc[-1]
    ma10 = df['Close'].rolling(MA_SHORT_10).mean().iloc[-1]

    # 計算 RSI
    rsi_series = calc_rsi(df['Close'], RSI_PERIOD)
    rsi = rsi_series.iloc[-1]

    # --- 篩選條件 ---
    # 1. 股價 > 200MA 且 > 20MA
    above_200ma = close > ma200
    above_20ma = close > ma20

    if not above_200ma:
        return False, {'reason': f'股價 ${close:.2f} < 200MA ${ma200:.2f}'}
    if not above_20ma:
        return False, {'reason': f'股價 ${close:.2f} < 20MA ${ma20:.2f}'}

    # 2. 均線多頭排列: 10MA > 20MA > 60MA
    ma_aligned = ma10 > ma20 > ma60
    if not ma_aligned:
        return False, {
            'reason': f'均線未多頭排列 (10MA={ma10:.2f}, 20MA={ma20:.2f}, 60MA={ma60:.2f})'
        }

    # 3. RSI 不超買
    if pd.isna(rsi):
        rsi = 50.0  # 預設中性
    if rsi > RSI_OVERBOUGHT:
        return False, {'reason': f'RSI {rsi:.1f} > {RSI_OVERBOUGHT} 超買'}

    # --- 計算額外資訊 ---
    # ATR
    atr_series = calc_atr(df['High'], df['Low'], df['Close'], 14)
    atr = atr_series.iloc[-1] if not pd.isna(atr_series.iloc[-1]) else close * 0.03

    # 3M 報酬率 (動能)
    if len(df) >= 63:
        ret_3m = (close / df['Close'].iloc[-63] - 1) * 100
    else:
        ret_3m = 0.0

    # 距離 200MA 的百分比 (越高越強勢)
    dist_200ma_pct = (close / ma200 - 1) * 100

    # 是否在均線回測中 (加分條件)
    is_pullback = low <= ma20 * 1.005 and close >= ma20 * 0.99

    # 強度評分 (0~100)
    score = 0
    score += min(30, dist_200ma_pct)          # 距離 200MA 越遠越強，最多 30 分
    score += min(20, ret_3m / 2)              # 3M 動能，最多 20 分
    score += 15 if ma_aligned else 0          # 均線多頭 15 分
    score += 10 if rsi < 50 else 5            # RSI 偏低 10 分 (較好的切入點)
    score += 15 if is_pullback else 0         # 回測均線 15 分
    score += 10 if above_200ma and above_20ma else 0  # 基本條件 10 分
    score = min(100, max(0, score))

    info = {
        'close': round(float(close), 2),
        'ma200': round(float(ma200), 2),
        'ma60': round(float(ma60), 2),
        'ma20': round(float(ma20), 2),
        'ma10': round(float(ma10), 2),
        'rsi': round(float(rsi), 1),
        'atr': round(float(atr), 2),
        'ret_3m': round(float(ret_3m), 1),
        'dist_200ma_pct': round(float(dist_200ma_pct), 1),
        'is_pullback': is_pullback,
        'ma_aligned': ma_aligned,
        'above_200ma': above_200ma,
        'above_20ma': above_20ma,
        'tech_score': round(score, 1),
    }

    return True, info


# ============================================================
# Layer 2 ~ 5: 選擇權分析
# ============================================================
def analyze_options_for_ticker(ticker: str, ticker_obj,
                                tech_info: dict,
                                daily_df: pd.DataFrame) -> List[OptionAnalysis]:
    """
    對通過技術面篩選的股票，進行完整的選擇權分析。
    包含 Layer 2 (波動率) ~ Layer 5 (Greeks 風控)。
    """
    results = []
    spot = tech_info['close']

    # --- 取得 Options Chain ---
    try:
        expirations = ticker_obj.options
        if not expirations:
            return results
    except Exception:
        return results

    # --- Layer 2: 計算 IV 環境 ---
    iv_analyzer = IVAnalyzer()
    hv_20 = iv_analyzer.calc_historical_volatility(daily_df['Close'], window=20)
    iv_history = iv_analyzer.estimate_iv_history_from_hv(daily_df['Close'], window=20, lookback_days=252)

    # --- 財報過濾 ---
    earnings_date = get_next_earnings_date(ticker_obj)
    today = datetime.now().date()

    # --- 遍歷到期日 ---
    bs = BlackScholes()
    simulator = PnLSimulator()

    for expiry_str in expirations:
        try:
            expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d').date()
        except ValueError:
            continue

        dte = (expiry_date - today).days
        if dte < SHORT_TERM_DTE_MIN or dte > MID_TERM_DTE_MAX:
            continue

        # 財報過濾: 如果到期日在財報前後 7 天內，跳過
        if earnings_date is not None:
            earnings_d = earnings_date.date() if hasattr(earnings_date, 'date') else earnings_date
            days_to_earnings = (earnings_d - today).days
            if 0 <= days_to_earnings <= dte + EARNINGS_BUFFER_DAYS:
                # 到期日跨過財報日，IV 可能被灌水
                if abs(days_to_earnings) <= EARNINGS_BUFFER_DAYS:
                    continue

        T = dte / 365.0

        # --- 取得 Call Options Chain ---
        try:
            chain = ticker_obj.option_chain(expiry_str)
            calls = chain.calls
            if calls.empty:
                continue
        except Exception:
            continue

        # --- 計算這個到期日的 ATM IV ---
        current_iv = iv_analyzer.estimate_iv_from_options_chain(calls, spot)
        if current_iv <= 0.01:
            current_iv = hv_20 if hv_20 > 0 else 0.30  # fallback

        iv_rank = iv_analyzer.calc_iv_rank(current_iv, iv_history) if iv_history else 50.0
        iv_percentile = iv_analyzer.calc_iv_percentile(current_iv, iv_history) if iv_history else 50.0

        # Layer 2 篩選: IV Rank / Percentile
        iv_pass = iv_rank <= IV_RANK_MAX or iv_percentile <= IV_PERCENTILE_MAX

        # --- 遍歷行權價 ---
        for _, row in calls.iterrows():
            strike = row['strike']
            if pd.isna(strike):
                continue

            # 安全取值: yfinance 的 volume/OI 可能是 NaN
            bid = row.get('bid', 0)
            bid = 0 if pd.isna(bid) else float(bid)
            ask = row.get('ask', 0)
            ask = 0 if pd.isna(ask) else float(ask)

            vol_raw = row.get('volume', 0)
            volume = 0 if pd.isna(vol_raw) else int(vol_raw)
            oi_raw = row.get('openInterest', 0)
            oi = 0 if pd.isna(oi_raw) else int(oi_raw)

            iv_raw = row.get('impliedVolatility', 0)
            market_iv = 0 if pd.isna(iv_raw) else float(iv_raw)

            # --- 基本數據品質檢查 ---
            if bid <= 0 or ask <= 0 or ask < bid:
                continue
            mid_price = (bid + ask) / 2
            if mid_price <= 0.05:
                continue

            # --- Layer 3: 流動性篩選 ---
            spread_pct = (ask - bid) / mid_price if mid_price > 0 else 1.0
            if volume < MIN_OPTION_VOLUME and oi < MIN_OPEN_INTEREST:
                continue
            if spread_pct > MAX_SPREAD_PCT:
                continue

            # --- 計算 Greeks ---
            sigma = market_iv if market_iv > 0.01 else current_iv
            greeks = bs.call_greeks(spot, strike, T, RISK_FREE_RATE, sigma)

            # --- Layer 4: Delta 篩選 ---
            if greeks.delta < DELTA_MIN or greeks.delta > DELTA_MAX:
                continue

            # --- Layer 5: Greeks 風控 ---
            # 損益分析
            premium = ask  # 用 Ask 價計算 (買方付 Ask)
            breakeven = simulator.calc_breakeven(strike, premium)
            rr_ratio = simulator.calc_reward_risk_ratio(spot, strike, premium, TARGET_PRICE_PCT)
            max_loss = premium * 100  # 每張合約 = 100 股

            # R:R 比篩選
            if rr_ratio < MIN_RR_RATIO:
                continue

            # BS 理論價 vs 市場價比較
            bs_price = greeks.theoretical_price
            mispricing = ((mid_price - bs_price) / bs_price * 100) if bs_price > 0 else 0

            # --- 倉位建議 ---
            cost_per_contract = premium * 100
            if cost_per_contract > MAX_PREMIUM_AMOUNT:
                # 單張就超過上限，降為 1 張但標記超限
                suggested_contracts = 1
            else:
                max_contracts = int(MAX_PREMIUM_AMOUNT / cost_per_contract)
                suggested_contracts = max(1, min(max_contracts, 5))  # 最多 5 張
            total_premium = suggested_contracts * cost_per_contract
            premium_pct = total_premium / TOTAL_CAPITAL * 100

            # --- 組裝結果 ---
            analysis = OptionAnalysis(
                ticker=ticker,
                underlying_price=spot,
                strike=strike,
                expiry=expiry_str,
                days_to_expiry=dte,
                option_type='call',
                market_price=round(mid_price, 2),
                bid=round(bid, 2),
                ask=round(ask, 2),
                volume=volume,
                open_interest=oi,
                implied_volatility=sigma,
                greeks=greeks,
                bs_theoretical=round(bs_price, 2),
                mispricing_pct=round(mispricing, 2),
                iv_rank=iv_rank,
                iv_percentile=iv_percentile,
                hv_20=hv_20,
                breakeven=round(breakeven, 2),
                max_loss=round(total_premium, 2),
                rr_ratio=round(rr_ratio, 2),
                suggested_contracts=suggested_contracts,
                total_premium=round(total_premium, 2),
                premium_pct_of_capital=round(premium_pct, 2),
            )
            results.append(analysis)

    return results


def select_best_contract(analyses: List[OptionAnalysis]) -> Optional[OptionAnalysis]:
    """
    從多個候選合約中選出最佳的一個。

    評分依據:
      1. R:R 比 (權重 30%)
      2. IV 環境 (IV Rank 越低越好, 權重 25%)
      3. 流動性 (Volume + OI, 權重 20%)
      4. Delta/Theta 比 (越高越好, 權重 15%)
      5. 定價偏差 (越接近理論價越好, 權重 10%)
    """
    if not analyses:
        return None

    scored = []
    for a in analyses:
        # R:R 分數 (0~30)
        rr_score = min(30, a.rr_ratio * 10)

        # IV 環境分數 (0~25): IV Rank 越低越好
        iv_score = max(0, 25 - (a.iv_rank or 50) / 4)

        # 流動性分數 (0~20)
        liq_score = min(20, (a.volume + a.open_interest) / 100)

        # Delta/Theta 比分數 (0~15)
        dt_ratio = abs(a.greeks.delta / a.greeks.theta) if a.greeks.theta != 0 else 0
        dt_score = min(15, dt_ratio / 2)

        # 定價偏差分數 (0~10): 偏差越小越好
        pricing_score = max(0, 10 - abs(a.mispricing_pct) / 2)

        total = rr_score + iv_score + liq_score + dt_score + pricing_score
        scored.append((total, a))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


# ============================================================
# 主掃描函式
# ============================================================
def scan_buy_call() -> Tuple[List[dict], List[str]]:
    """
    主掃描函式：遍歷所有監控股票，執行五層篩選。

    Returns:
        (recommendations, alerts)
        recommendations: [{ticker, tech_info, best_contract, all_contracts}, ...]
        alerts: [格式化的警報文字, ...]
    """
    print(f"\n{'═' * 60}")
    print(f"  Anti-Gravity | 機構級 Buy Call 掃描器")
    print(f"{'═' * 60}")
    print(f"  掃描時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  掃描標的: {len(AI_TECH_STOCKS)} 檔 AI/Tech 股")
    print(f"  風控上限: 單筆最大權利金 ${MAX_PREMIUM_AMOUNT:,.0f} (本金{MAX_PREMIUM_PCT*100}%)")
    print()

    # === Step 1: 下載日線數據 ===
    print("📥 下載日線數據 (1年)...")
    tickers_str = " ".join(AI_TECH_STOCKS + [BENCHMARK])
    daily_data = yf.download(tickers_str, period="1y", interval="1d",
                             progress=False, group_by='ticker')

    if daily_data.empty:
        print("❌ 日線數據下載失敗！")
        return [], []

    stocks_daily = {}
    for ticker in AI_TECH_STOCKS:
        try:
            if ticker in daily_data.columns.levels[0]:
                df = daily_data[ticker].dropna(how='all')
                if not df.empty and len(df) > MA_LONG:
                    stocks_daily[ticker] = df
        except Exception:
            pass

    print(f"  ✅ 成功載入 {len(stocks_daily)} 檔日線數據")

    # === Step 2: 大盤環境 ===
    print("\n🌐 評估大盤環境...")
    breadth = calculate_market_breadth()
    if breadth > 60:
        market_label = "🟢 強勢多頭"
        market_ok = True
    elif breadth >= 40:
        market_label = "🟡 震盪行情"
        market_ok = True
    else:
        market_label = "🔴 防守行情"
        market_ok = False

    print(f"  大盤廣度: {breadth:.1f}% {market_label}")

    if not market_ok:
        print(f"  ⚠️ 大盤廣度 < 40%，暫停 Buy Call 掃描（防守模式）")
        msg = (
            f"🌐 Anti-Gravity Buy Call 掃描報告\n"
            f"──────────────────────────────\n"
            f"大盤廣度: {breadth:.1f}% {market_label}\n"
            f"⚠️ 市場環境不佳，暫停 Buy Call 操作建議"
        )
        return [], [msg]

    # === Step 3: Layer 1 技術面篩選 ===
    print(f"\n🔍 Layer 1: 技術面篩選 (>200MA, >20MA, 均線多頭, RSI<{RSI_OVERBOUGHT})...")
    tech_passed = {}
    tech_failed = []

    for ticker, df in stocks_daily.items():
        passed, info = screen_technicals(ticker, df)
        if passed:
            tech_passed[ticker] = info
            print(f"  ✅ {ticker:6s} | ${info['close']:>8.2f} | RSI {info['rsi']:5.1f} | "
                  f"3M {info['ret_3m']:>+6.1f}% | Score {info['tech_score']:5.1f}")
        else:
            tech_failed.append((ticker, info.get('reason', '')))

    print(f"\n  通過: {len(tech_passed)} 檔 | 淘汰: {len(tech_failed)} 檔")
    if tech_failed:
        failed_str = ", ".join([f"{t}({r[:15]})" for t, r in tech_failed[:10]])
        print(f"  淘汰原因摘要: {failed_str}{'...' if len(tech_failed) > 10 else ''}")

    if not tech_passed:
        print("\n  ⚠️ 無股票通過技術面篩選，掃描結束。")
        return [], []

    # === Step 4: Layer 2~5 選擇權分析 ===
    print(f"\n📊 Layer 2~5: 選擇權分析 (IV環境 + 流動性 + 合約選擇 + Greeks)...")
    recommendations = []
    all_alerts = []

    # 按技術面分數排序，優先分析最強的股票
    sorted_tickers = sorted(tech_passed.items(), key=lambda x: x[1]['tech_score'], reverse=True)

    for ticker, tech_info in sorted_tickers:
        print(f"\n  ── {ticker} ──")
        try:
            ticker_obj = yf.Ticker(ticker)

            # 取得選擇權分析
            analyses = analyze_options_for_ticker(
                ticker, ticker_obj, tech_info, stocks_daily[ticker]
            )

            if not analyses:
                print(f"     ⚠️ 無合適的 Call 合約 (流動性/Delta/R:R 不足)")
                continue

            # 選出最佳合約
            best = select_best_contract(analyses)
            if best is None:
                continue

            # 分類所有合約
            short_term = [a for a in analyses if a.days_to_expiry <= SHORT_TERM_DTE_MAX]
            mid_term = [a for a in analyses if a.days_to_expiry > SHORT_TERM_DTE_MAX]
            best_short = select_best_contract(short_term)
            best_mid = select_best_contract(mid_term)

            rec = {
                'ticker': ticker,
                'tech_info': tech_info,
                'best_contract': best,
                'best_short_term': best_short,
                'best_mid_term': best_mid,
                'total_candidates': len(analyses),
            }
            recommendations.append(rec)

            # 印出摘要
            iv_label = "🟢 低" if (best.iv_rank or 50) < 30 else "🟡 中" if (best.iv_rank or 50) < 50 else "🔴 高"
            delta_label = classify_delta(best.greeks.delta)
            dte_label = classify_dte(best.days_to_expiry)

            print(f"     ✅ 推薦 | {best.expiry} ({dte_label}) K=${best.strike} "
                  f"| Δ={best.greeks.delta:.2f} ({delta_label}) "
                  f"| IV Rank {best.iv_rank:.0f}% {iv_label} "
                  f"| R:R {best.rr_ratio:.1f}:1")
            print(f"        Ask ${best.ask:.2f} | Vol={best.volume} OI={best.open_interest} "
                  f"| {best.suggested_contracts}張 共${best.total_premium:,.0f}")

        except Exception as e:
            print(f"     ❌ 分析失敗: {e}")
            continue

    # === Step 5: 產出報告 ===
    if recommendations:
        # 按技術面分數 + R:R 綜合排序
        recommendations.sort(
            key=lambda r: (r['tech_info']['tech_score'] * 0.5 + r['best_contract'].rr_ratio * 20),
            reverse=True
        )

        alerts = format_report(recommendations, breadth, market_label)
        all_alerts.extend(alerts)

        # 寫入 Dashboard JSON
        save_to_dashboard(recommendations, breadth, market_label)
    else:
        print(f"\n  ⚠️ 所有股票均未通過完整五層篩選，今日無 Buy Call 推薦。")

    print(f"\n{'═' * 60}")
    print(f"  掃描完成 | 推薦: {len(recommendations)} 檔")
    print(f"{'═' * 60}\n")

    return recommendations, all_alerts


# ============================================================
# 報告格式化
# ============================================================
def format_report(recommendations: List[dict],
                  breadth: float, market_label: str) -> List[str]:
    """格式化為專業報告文字 (同時印出到 console 和準備 LINE 通知)"""
    alerts = []

    header = (
        f"\n{'═' * 55}\n"
        f"  Anti-Gravity | 機構級 Buy Call 掃描報告\n"
        f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"{'═' * 55}\n"
        f"\n🌐 市場環境\n"
        f"├─ 大盤廣度: {breadth:.1f}% {market_label}\n"
        f"└─ 本金: ${TOTAL_CAPITAL:,} | 單筆上限: ${MAX_PREMIUM_AMOUNT:,.0f}\n"
    )
    print(header)

    line_msg_parts = [
        f"🌐 Anti-Gravity Buy Call 報告\n"
        f"大盤廣度: {breadth:.1f}% {market_label}\n"
        f"推薦 {len(recommendations)} 檔：\n"
    ]

    for idx, rec in enumerate(recommendations, 1):
        ticker = rec['ticker']
        ti = rec['tech_info']
        best = rec['best_contract']
        best_short = rec.get('best_short_term')
        best_mid = rec.get('best_mid_term')

        iv_label = "🟢 低檔" if (best.iv_rank or 50) < 30 else "🟡 中位" if (best.iv_rank or 50) < 50 else "🔴 偏高"
        delta_label = classify_delta(best.greeks.delta)
        dte_label = classify_dte(best.days_to_expiry)

        # Delta/Theta 比 (效率指標)
        dt_ratio = abs(best.greeks.delta / best.greeks.theta) if best.greeks.theta != 0 else float('inf')
        dt_label = "🟢" if dt_ratio > 3.0 else "🟡" if dt_ratio > 2.0 else "🔴"

        report = (
            f"\n{'─' * 55}\n"
            f"🎯 [推薦 #{idx}] {ticker} — Buy Call\n"
            f"{'─' * 55}\n"
            f"\n📊 技術面 (Score: {ti['tech_score']:.0f}/100):\n"
            f" ├─ 股價: ${ti['close']:.2f}"
            f" (> 200MA ${ti['ma200']:.2f} 🟢 +{ti['dist_200ma_pct']:.1f}%)\n"
            f" ├─ 20MA: ${ti['ma20']:.2f} 🟢"
            f" | 10MA: ${ti['ma10']:.2f}\n"
            f" ├─ 均線排列: 10MA > 20MA > 60MA"
            f" {'🟢 多頭排列' if ti['ma_aligned'] else '❌'}\n"
            f" ├─ RSI({RSI_PERIOD}): {ti['rsi']:.1f}"
            f" {'🟢 適合' if ti['rsi'] < 50 else '🟡 中性' if ti['rsi'] < RSI_OVERBOUGHT else '🔴 超買'}\n"
            f" ├─ 3M 動能: {ti['ret_3m']:+.1f}%\n"
            f" └─ 均線回測: {'✅ 接近20MA支撐' if ti['is_pullback'] else '—'}\n"
            f"\n📈 波動率分析:\n"
            f" ├─ 當前 IV: {best.implied_volatility*100:.1f}%\n"
            f" ├─ IV Rank (52W): {best.iv_rank:.1f}% {iv_label}"
            f"{'，Call 便宜' if (best.iv_rank or 50) < 30 else ''}\n"
            f" ├─ IV Percentile: {best.iv_percentile:.1f}%\n"
            f" ├─ HV(20): {best.hv_20*100:.1f}%\n"
            f" └─ IV/HV 比值: {best.implied_volatility/best.hv_20:.2f}"
            f" {'(定價合理)' if 0.8 <= best.implied_volatility/best.hv_20 <= 1.3 else '(偏高⚠️)' if best.implied_volatility/best.hv_20 > 1.3 else '(偏低🟢)'}"
            if best.hv_20 and best.hv_20 > 0 else
            f"\n📈 波動率分析:\n"
            f" ├─ 當前 IV: {best.implied_volatility*100:.1f}%\n"
            f" ├─ IV Rank (52W): {best.iv_rank:.1f}% {iv_label}\n"
            f" └─ IV Percentile: {best.iv_percentile:.1f}%\n"
        )

        # 推薦合約區塊
        report += (
            f"\n🎰 推薦合約 (最佳):\n"
            f" ├─ 到期日: {best.expiry} ({best.days_to_expiry}天, {dte_label})\n"
            f" ├─ 行權價: ${best.strike:.2f} ({delta_label})\n"
            f" ├─ Bid/Ask: ${best.bid:.2f} / ${best.ask:.2f}"
            f" (Spread {(best.ask-best.bid)/((best.ask+best.bid)/2)*100:.1f}%)\n"
            f" ├─ 理論價 (B-S): ${best.bs_theoretical:.2f}"
            f" (偏差 {best.mispricing_pct:+.1f}%)\n"
            f" └─ Vol={best.volume} | OI={best.open_interest}\n"
        )

        # 如果有不同天期的最佳合約，也列出
        if best_short and best_mid:
            if best_short != best:
                report += (
                    f"\n   📌 短天期替代: {best_short.expiry} ({best_short.days_to_expiry}天)"
                    f" K=${best_short.strike} Ask=${best_short.ask:.2f}"
                    f" Δ={best_short.greeks.delta:.2f} R:R={best_short.rr_ratio:.1f}:1\n"
                )
            if best_mid and best_mid != best:
                report += (
                    f"   📌 中天期替代: {best_mid.expiry} ({best_mid.days_to_expiry}天)"
                    f" K=${best_mid.strike} Ask=${best_mid.ask:.2f}"
                    f" Δ={best_mid.greeks.delta:.2f} R:R={best_mid.rr_ratio:.1f}:1\n"
                )

        # Greeks
        report += (
            f"\n🔬 Greeks:\n"
            f" ├─ Delta:  {best.greeks.delta:.4f}  (每漲$1，Option賺${best.greeks.delta:.2f})\n"
            f" ├─ Gamma:  {best.greeks.gamma:.4f}  (Delta變化速率)\n"
            f" ├─ Theta: {best.greeks.theta:.4f}  (每天時間價值損耗${abs(best.greeks.theta):.2f})\n"
            f" ├─ Vega:   {best.greeks.vega:.4f}  (IV每升1%，Option漲${best.greeks.vega:.2f})\n"
            f" └─ Δ/Θ 效率比: {dt_ratio:.1f} {dt_label}"
            f" {'(每損耗$1 Theta 可賺 >' + str(round(dt_ratio)) + '× Delta)' if dt_ratio < 100 else ''}\n"
        )

        # 倉位與風控
        breakeven_pct = (best.breakeven / best.underlying_price - 1) * 100
        report += (
            f"\n💰 倉位與風控 (本金 ${TOTAL_CAPITAL:,}):\n"
            f" ├─ 建議張數: {best.suggested_contracts} 張 (1張 = 100股)\n"
            f" ├─ 總權利金: ${best.total_premium:,.0f}"
            f" (佔本金 {best.premium_pct_of_capital:.1f}%)\n"
            f" ├─ 最大虧損: ${best.total_premium:,.0f} (= 權利金，有限風險)\n"
            f" ├─ 損益平衡: ${best.breakeven:.2f} ({breakeven_pct:+.1f}%)\n"
            f" └─ 報酬風險比 ({TARGET_PRICE_PCT*100:.0f}%漲幅目標): {best.rr_ratio:.1f}:1"
            f" {'🟢' if best.rr_ratio >= 2.0 else '🟡'}\n"
        )

        print(report)

        # LINE 通知摘要 (精簡版)
        line_msg_parts.append(
            f"\n🎯 {ticker} Buy Call\n"
            f"${ti['close']:.2f} | RSI {ti['rsi']:.1f} | Score {ti['tech_score']:.0f}\n"
            f"合約: {best.expiry} K=${best.strike} Ask=${best.ask:.2f}\n"
            f"Δ={best.greeks.delta:.2f} | IV Rank {best.iv_rank:.0f}% {iv_label}\n"
            f"R:R {best.rr_ratio:.1f}:1 | {best.suggested_contracts}張 ${best.total_premium:,.0f}\n"
        )

    # 組合 LINE 通知
    full_msg = "".join(line_msg_parts)
    alerts.append(full_msg)

    return alerts


# ============================================================
# Dashboard JSON 輸出
# ============================================================
def save_to_dashboard(recommendations: List[dict],
                      breadth: float, market_label: str):
    """儲存掃描結果到 Dashboard JSON"""
    output = {
        'scan_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'market_breadth': round(breadth, 1),
        'market_label': market_label,
        'capital': TOTAL_CAPITAL,
        'max_premium': MAX_PREMIUM_AMOUNT,
        'total_recommendations': len(recommendations),
        'recommendations': [],
    }

    for rec in recommendations:
        best = rec['best_contract']
        ti = rec['tech_info']

        entry = {
            'ticker': rec['ticker'],
            'tech_score': ti['tech_score'],
            'close': ti['close'],
            'rsi': ti['rsi'],
            'ma_aligned': ti['ma_aligned'],
            'ret_3m': ti['ret_3m'],
            'is_pullback': ti['is_pullback'],
            'best_contract': best.to_dict(),
        }

        # 加入替代合約
        if rec.get('best_short_term'):
            entry['short_term_alt'] = rec['best_short_term'].to_dict()
        if rec.get('best_mid_term'):
            entry['mid_term_alt'] = rec['best_mid_term'].to_dict()

        output['recommendations'].append(entry)

    # 清理 numpy 類型以確保 JSON 序列化
    import json

    def _clean_for_json(obj):
        if isinstance(obj, dict):
            return {k: _clean_for_json(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [_clean_for_json(v) for v in obj]
        elif isinstance(obj, (np.bool_,)):
            return bool(obj)
        elif isinstance(obj, (np.integer,)):
            return int(obj)
        elif isinstance(obj, (np.floating,)):
            return float(obj)
        elif isinstance(obj, (np.ndarray,)):
            return obj.tolist()
        return obj

    output = _clean_for_json(output)
    save_dashboard_data('buy_call_data.json', output)
    print(f"\n  💾 結果已寫入 Dashboard: buy_call_data.json")


# ============================================================
# 監控模式
# ============================================================
def start_buy_call_monitor(interval_min: int = 30):
    """
    每 interval_min 分鐘自動掃描一次。
    適合盤中或盤後持續監控。
    """
    print(f"\n{'═' * 55}")
    print(f"  Anti-Gravity | Buy Call 自動監控模式")
    print(f"{'═' * 55}")
    print(f"  掃描間隔: 每 {interval_min} 分鐘")
    print(f"  掃描標的: {len(AI_TECH_STOCKS)} 檔 AI/Tech 股")
    print(f"  按 Ctrl+C 停止\n")

    last_notified = None

    while True:
        try:
            now = datetime.now()
            print(f"\n[{now.strftime('%H:%M:%S')}] 🔄 開始掃描...")

            recommendations, alerts = scan_buy_call()

            # 發送 LINE 通知 (每天只發一次)
            today = now.date()
            if alerts and today != last_notified:
                print("📲 發送 LINE 通知...")
                for alert in alerts:
                    send_line_notify(alert)
                last_notified = today
                print("  ✅ LINE 通知已發送")
            elif today == last_notified:
                print("  ⏳ 今日通知已發送過，跳過 LINE 推播")

        except KeyboardInterrupt:
            print("\n🛑 監控已手動停止。")
            break
        except Exception as e:
            print(f"\n⚠️ 掃描發生錯誤: {e}")
            import traceback
            traceback.print_exc()

        print(f"\n  ⏳ 下次掃描: {interval_min} 分鐘後...")
        time.sleep(interval_min * 60)


# ============================================================
# 主程式
# ============================================================
if __name__ == '__main__':
    if '--monitor' in sys.argv:
        start_buy_call_monitor()
    else:
        recommendations, alerts = scan_buy_call()
        if alerts:
            print("\n📲 是否發送 LINE 通知？(掃描模式不自動發送)")
