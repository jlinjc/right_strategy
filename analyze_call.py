# [LOCAL VERSION DIFF]: 全新加入的檔案。用於分析買入 Call 選擇權的獲利機率與風險。
"""
analyze_call.py - 單股 Buy Call 最佳合約分析器
================================================
輸入任意股票代號，即時分析所有到期日 × 行權價的組合，
找出最適合買入的 Call Option 合約。

用法:
  python analyze_call.py NVDA
  python analyze_call.py TSLA
  python analyze_call.py              # 互動模式，提示輸入

輸出:
  1. 技術面快照
  2. IV 環境評估
  3. 各到期日最佳合約排名表
  4. 最終推薦 (短天期 / 中天期 / 最佳綜合)
  5. 損益情境模擬表
"""

import sys
import warnings

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from typing import Optional, List, Tuple

from options_pricing import (
    BlackScholes, IVAnalyzer, PnLSimulator, GreeksResult,
)

# ============================================================
# 配置
# ============================================================
RISK_FREE_RATE = 0.043
CAPITAL = 10_000
MAX_PREMIUM_PCT = 0.05
MAX_PREMIUM = CAPITAL * MAX_PREMIUM_PCT

# Delta / DTE 範圍 (全掃描)
DTE_MIN = 3
DTE_MAX = 120
DELTA_MIN = 0.20
DELTA_MAX = 0.85

# 流動性最低門檻 (放寬，因為是單股分析)
MIN_VOLUME = 10
MIN_OI = 50
MAX_SPREAD_PCT = 0.20


# ============================================================
# 工具
# ============================================================
def calc_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def safe_int(val, default=0):
    """安全轉換為 int，處理 NaN"""
    if pd.isna(val):
        return default
    return int(val)


def safe_float(val, default=0.0):
    """安全轉換為 float，處理 NaN"""
    if pd.isna(val):
        return default
    return float(val)


def classify_delta(d):
    if d >= 0.70: return "Deep ITM"
    elif d >= 0.55: return "ITM"
    elif d >= 0.45: return "ATM"
    elif d >= 0.30: return "OTM"
    else: return "Deep OTM"


def classify_dte(days):
    if days <= 7: return "本週"
    elif days <= 14: return "雙週"
    elif days <= 30: return "近月"
    elif days <= 60: return "中天期"
    else: return "遠月"


def score_contract(delta, dte, rr_ratio, iv_rank, spread_pct, volume, oi, theta):
    """綜合評分 0~100"""
    s = 0

    # R:R (0~25)
    s += min(25, rr_ratio * 8)

    # Delta 甜蜜點: 0.40~0.60 最佳 (0~15)
    delta_dist = abs(delta - 0.50)
    s += max(0, 15 - delta_dist * 40)

    # DTE 甜蜜點: 30~60天 最佳 (0~15)
    if 25 <= dte <= 60:
        s += 15
    elif 14 <= dte <= 90:
        s += 10
    elif 7 <= dte <= 120:
        s += 5

    # IV (0~15): 低 IV 好
    s += max(0, 15 - (iv_rank or 50) / 5)

    # 流動性 (0~15)
    liq = min(15, (volume + oi) / 200)
    s += liq

    # Spread (0~10): 越窄越好
    s += max(0, 10 - spread_pct * 50)

    # Delta/Theta 效率 (0~5)
    dt = abs(delta / theta) if theta != 0 else 0
    s += min(5, dt)

    return min(100, max(0, s))


# ============================================================
# 主分析函式
# ============================================================
def analyze_ticker(ticker: str):
    """對單一股票執行完整的 Buy Call 合約分析"""

    ticker = ticker.upper().strip()
    print(f"\n{'═' * 62}")
    print(f"  Anti-Gravity | Buy Call 最佳合約分析")
    print(f"  標的: {ticker}")
    print(f"  分析時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'═' * 62}")

    # === 1. 下載數據 ===
    print(f"\n📥 下載 {ticker} 數據...")
    tk = yf.Ticker(ticker)

    try:
        daily = tk.history(period="1y")
        if daily.empty or len(daily) < 20:
            print(f"  ❌ 無法取得 {ticker} 的日線數據 (可能代碼錯誤)")
            return None
    except Exception as e:
        print(f"  ❌ 下載失敗: {e}")
        return None

    spot = float(daily['Close'].iloc[-1])
    print(f"  ✅ 現價: ${spot:.2f} ({len(daily)} 天日線)")

    # === 2. 技術面快照 ===
    print(f"\n{'─' * 62}")
    print(f"📊 技術面快照")
    print(f"{'─' * 62}")

    ma10 = daily['Close'].rolling(10).mean().iloc[-1]
    ma20 = daily['Close'].rolling(20).mean().iloc[-1]
    ma60 = daily['Close'].rolling(60).mean().iloc[-1] if len(daily) >= 60 else float('nan')
    ma200 = daily['Close'].rolling(200).mean().iloc[-1] if len(daily) >= 200 else float('nan')
    rsi_series = calc_rsi(daily['Close'], 14)
    rsi = rsi_series.iloc[-1] if not pd.isna(rsi_series.iloc[-1]) else 50.0

    # 3M 報酬
    ret_3m = (spot / daily['Close'].iloc[-63] - 1) * 100 if len(daily) >= 63 else 0
    ret_1m = (spot / daily['Close'].iloc[-21] - 1) * 100 if len(daily) >= 21 else 0
    ret_1w = (spot / daily['Close'].iloc[-5] - 1) * 100 if len(daily) >= 5 else 0

    # 均線狀態
    ma_aligned = False
    if not pd.isna(ma60):
        ma_aligned = ma10 > ma20 > ma60

    above_200 = spot > ma200 if not pd.isna(ma200) else None
    above_20 = spot > ma20

    trend_label = "🟢 強勢" if (above_200 and above_20 and ma_aligned) else \
                  "🟡 中性" if above_20 else "🔴 弱勢"

    print(f"  股價:      ${spot:.2f}")
    if not pd.isna(ma200):
        dist_200 = (spot / ma200 - 1) * 100
        print(f"  200MA:     ${ma200:.2f} {'🟢 Above' if above_200 else '🔴 Below'}"
              f" ({dist_200:+.1f}%)")
    print(f"  20MA:      ${ma20:.2f} {'🟢 Above' if above_20 else '🔴 Below'}")
    print(f"  10MA:      ${ma10:.2f}")
    if not pd.isna(ma60):
        print(f"  均線排列:  {'🟢 多頭 (10>20>60)' if ma_aligned else '❌ 非多頭'}")
    print(f"  RSI(14):   {rsi:.1f}"
          f" {'🟢' if rsi < 50 else '🟡 中性' if rsi < 70 else '🔴 超買'}")
    print(f"  動能:      1W {ret_1w:+.1f}% | 1M {ret_1m:+.1f}% | 3M {ret_3m:+.1f}%")
    print(f"  趨勢判定:  {trend_label}")

    # === 3. IV 環境 ===
    print(f"\n{'─' * 62}")
    print(f"📈 波動率環境")
    print(f"{'─' * 62}")

    iv_analyzer = IVAnalyzer()
    hv_20 = iv_analyzer.calc_historical_volatility(daily['Close'], window=20)
    hv_60 = iv_analyzer.calc_historical_volatility(daily['Close'], window=60)
    iv_history = iv_analyzer.estimate_iv_history_from_hv(daily['Close'], 20, 252)

    print(f"  HV(20):    {hv_20*100:.1f}%")
    print(f"  HV(60):    {hv_60*100:.1f}%")

    # === 4. 取得 Options Chain & 分析所有合約 ===
    print(f"\n{'─' * 62}")
    print(f"🔍 掃描所有 Call 合約...")
    print(f"{'─' * 62}")

    try:
        expirations = tk.options
        if not expirations:
            print(f"  ❌ {ticker} 沒有可用的選擇權")
            return None
    except Exception as e:
        print(f"  ❌ 無法取得選擇權到期日: {e}")
        return None

    today = datetime.now().date()
    bs = BlackScholes()
    simulator = PnLSimulator()
    all_contracts = []

    # 計算財報日
    try:
        earnings_date = None
        cal = tk.calendar
        if cal is not None:
            if isinstance(cal, dict) and 'Earnings Date' in cal:
                ed = cal['Earnings Date']
                if isinstance(ed, list) and len(ed) > 0:
                    earnings_date = pd.to_datetime(ed[0]).date()
                elif ed is not None:
                    earnings_date = pd.to_datetime(ed).date()
    except Exception:
        earnings_date = None

    if earnings_date:
        days_to_earnings = (earnings_date - today).days
        print(f"  📅 下次財報: {earnings_date} ({days_to_earnings} 天後)"
              f" {'⚠️ 注意 IV Crush' if 0 < days_to_earnings <= 14 else ''}")
    else:
        days_to_earnings = None
        print(f"  📅 下次財報: 未知")

    for expiry_str in expirations:
        try:
            expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d').date()
        except ValueError:
            continue

        dte = (expiry_date - today).days
        if dte < DTE_MIN or dte > DTE_MAX:
            continue

        T = dte / 365.0

        try:
            chain = tk.option_chain(expiry_str)
            calls = chain.calls
            if calls.empty:
                continue
        except Exception:
            continue

        # ATM IV
        current_iv = iv_analyzer.estimate_iv_from_options_chain(calls, spot)
        if current_iv <= 0.01:
            current_iv = hv_20 if hv_20 > 0 else 0.30

        iv_rank = iv_analyzer.calc_iv_rank(current_iv, iv_history) if iv_history else 50.0
        iv_pctl = iv_analyzer.calc_iv_percentile(current_iv, iv_history) if iv_history else 50.0

        # 財報風險標記
        earnings_risk = False
        if days_to_earnings is not None and 0 < days_to_earnings <= dte + 7:
            if days_to_earnings <= 7:
                earnings_risk = True

        for _, row in calls.iterrows():
            strike = row['strike']
            if pd.isna(strike):
                continue

            bid = safe_float(row.get('bid', 0))
            ask = safe_float(row.get('ask', 0))
            vol = safe_int(row.get('volume', 0))
            oi = safe_int(row.get('openInterest', 0))
            mkt_iv = safe_float(row.get('impliedVolatility', 0))

            if bid <= 0 or ask <= 0 or ask < bid:
                continue
            mid = (bid + ask) / 2
            if mid < 0.05:
                continue

            spread_pct = (ask - bid) / mid
            if vol < MIN_VOLUME and oi < MIN_OI:
                continue
            if spread_pct > MAX_SPREAD_PCT:
                continue

            sigma = mkt_iv if mkt_iv > 0.01 else current_iv
            greeks = bs.call_greeks(spot, strike, T, RISK_FREE_RATE, sigma)

            if greeks.delta < DELTA_MIN or greeks.delta > DELTA_MAX:
                continue

            premium = ask
            breakeven = simulator.calc_breakeven(strike, premium)
            rr_ratio = simulator.calc_reward_risk_ratio(spot, strike, premium, 0.10)
            bs_price = greeks.theoretical_price
            mispricing = ((mid - bs_price) / bs_price * 100) if bs_price > 0 else 0

            # 倉位
            cost_per = premium * 100
            if cost_per > 0:
                max_c = int(MAX_PREMIUM / cost_per)
                contracts = max(1, min(max_c, 10))
            else:
                contracts = 1
            total_cost = contracts * cost_per

            # 評分
            contract_score = score_contract(
                greeks.delta, dte, rr_ratio, iv_rank, spread_pct, vol, oi, greeks.theta
            )

            # Delta/Theta 效率
            dt_ratio = abs(greeks.delta / greeks.theta) if greeks.theta != 0 else 0

            all_contracts.append({
                'expiry': expiry_str,
                'dte': dte,
                'dte_label': classify_dte(dte),
                'strike': strike,
                'delta': greeks.delta,
                'delta_label': classify_delta(greeks.delta),
                'gamma': greeks.gamma,
                'theta': greeks.theta,
                'vega': greeks.vega,
                'dt_ratio': dt_ratio,
                'bid': bid,
                'ask': ask,
                'mid': mid,
                'volume': vol,
                'oi': oi,
                'spread_pct': spread_pct,
                'iv': sigma,
                'iv_rank': iv_rank,
                'iv_pctl': iv_pctl,
                'bs_price': bs_price,
                'mispricing': mispricing,
                'breakeven': breakeven,
                'breakeven_pct': (breakeven / spot - 1) * 100,
                'rr_ratio': rr_ratio,
                'contracts': contracts,
                'total_cost': total_cost,
                'cost_pct': total_cost / CAPITAL * 100,
                'score': contract_score,
                'earnings_risk': earnings_risk,
            })

    if not all_contracts:
        print(f"  ❌ 無合適的 Call 合約通過篩選")
        return None

    print(f"  ✅ 找到 {len(all_contracts)} 個候選合約")

    # === 5. IV 環境報告 (用第一個到期日的 ATM IV) ===
    first = all_contracts[0]
    iv_label = "🟢 低檔" if first['iv_rank'] < 30 else "🟡 中位" if first['iv_rank'] < 50 else "🔴 偏高"
    print(f"  ATM IV:    {first['iv']*100:.1f}%")
    print(f"  IV Rank:   {first['iv_rank']:.1f}% {iv_label}")
    print(f"  IV Pctl:   {first['iv_pctl']:.1f}%")
    if hv_20 > 0:
        print(f"  IV/HV:     {first['iv']/hv_20:.2f}"
              f" {'(偏低🟢 買Call有利)' if first['iv']/hv_20 < 0.9 else '(合理)' if first['iv']/hv_20 < 1.2 else '(偏高⚠️)'}")

    # === 6. 各到期日最佳合約表 ===
    df = pd.DataFrame(all_contracts)
    df_sorted = df.sort_values('score', ascending=False)

    # 每個到期日取最佳
    best_per_expiry = df_sorted.groupby('expiry').first().reset_index()
    best_per_expiry = best_per_expiry.sort_values('dte')

    print(f"\n{'═' * 62}")
    print(f"📋 各到期日最佳合約排名")
    print(f"{'═' * 62}")
    print(f"  {'到期日':<12} {'DTE':>4} {'行權價':>8} {'Delta':>6} {'Ask':>7}"
          f" {'R:R':>5} {'IV%':>5} {'Δ/Θ':>5} {'Score':>6} {'評級'}")
    print(f"  {'─'*12} {'─'*4} {'─'*8} {'─'*6} {'─'*7}"
          f" {'─'*5} {'─'*5} {'─'*5} {'─'*6} {'─'*4}")

    for _, r in best_per_expiry.iterrows():
        score_emoji = "⭐" if r['score'] >= 70 else "🟢" if r['score'] >= 55 else "🟡" if r['score'] >= 40 else "🔴"
        earn_flag = "📊" if r['earnings_risk'] else "  "
        print(f"  {r['expiry']:<12} {r['dte']:>4d} {r['strike']:>8.1f}"
              f" {r['delta']:>6.2f} ${r['ask']:>6.2f}"
              f" {r['rr_ratio']:>4.1f}x {r['iv']*100:>4.0f}%"
              f" {r['dt_ratio']:>5.1f} {r['score']:>5.0f}  {score_emoji}{earn_flag}")

    # === 7. 全域 Top 10 合約 ===
    top10 = df_sorted.head(10)

    print(f"\n{'═' * 62}")
    print(f"🏆 綜合排名 Top 10 合約")
    print(f"{'═' * 62}")
    print(f"  {'#':>2} {'到期日':<12} {'K':>7} {'Δ':>5} {'類型':<9}"
          f" {'Ask':>7} {'R:R':>5} {'Δ/Θ':>5} {'OI':>6} {'Score':>5}")
    print(f"  {'─'*2} {'─'*12} {'─'*7} {'─'*5} {'─'*9}"
          f" {'─'*7} {'─'*5} {'─'*5} {'─'*6} {'─'*5}")

    for i, (_, r) in enumerate(top10.iterrows(), 1):
        label = f"{r['dte_label'][:4]}/{r['delta_label'][:3]}"
        print(f"  {i:>2} {r['expiry']:<12} ${r['strike']:>6.1f}"
              f" {r['delta']:>5.2f} {label:<9}"
              f" ${r['ask']:>6.2f} {r['rr_ratio']:>4.1f}x"
              f" {r['dt_ratio']:>5.1f} {r['oi']:>6} {r['score']:>5.0f}")

    # === 8. 最終推薦 ===
    # 短天期最佳 (<=21天)
    short = df_sorted[df_sorted['dte'] <= 21]
    # 中天期最佳 (25~60天)
    mid = df_sorted[(df_sorted['dte'] >= 25) & (df_sorted['dte'] <= 60)]
    # 遠月最佳 (>60天)
    far = df_sorted[df_sorted['dte'] > 60]
    # 最佳綜合
    best_overall = df_sorted.iloc[0]

    print(f"\n{'═' * 62}")
    print(f"🎯 最終推薦")
    print(f"{'═' * 62}")

    def print_recommendation(label, r):
        dt_label = "🟢" if r['dt_ratio'] > 3 else "🟡" if r['dt_ratio'] > 2 else "🔴"
        print(f"\n  {label}:")
        print(f"  ┌─ 合約: {r['expiry']} ({r['dte']}天) K=${r['strike']:.2f}")
        print(f"  ├─ Delta: {r['delta']:.3f} ({r['delta_label']}) | Gamma: {r['gamma']:.4f}")
        print(f"  ├─ Theta: {r['theta']:.4f}/日 | Vega: {r['vega']:.4f}")
        print(f"  ├─ Δ/Θ 效率: {r['dt_ratio']:.1f} {dt_label}")
        print(f"  ├─ Bid/Ask: ${r['bid']:.2f}/${r['ask']:.2f} (Spread {r['spread_pct']*100:.1f}%)")
        print(f"  ├─ Vol={r['volume']} OI={r['oi']}")
        print(f"  ├─ BS理論價: ${r['bs_price']:.2f} (偏差 {r['mispricing']:+.1f}%)")
        print(f"  ├─ 損益平衡: ${r['breakeven']:.2f} ({r['breakeven_pct']:+.1f}%)")
        print(f"  ├─ R:R (10%↑): {r['rr_ratio']:.1f}:1 {'🟢' if r['rr_ratio'] >= 2 else '🟡'}")
        print(f"  ├─ 建議: {r['contracts']}張 共${r['total_cost']:,.0f}"
              f" (本金{r['cost_pct']:.1f}%)")
        print(f"  └─ 評分: {r['score']:.0f}/100"
              f" {'⭐ 極佳' if r['score'] >= 70 else '🟢 良好' if r['score'] >= 55 else '🟡 可考慮' if r['score'] >= 40 else '🔴 風險較高'}")
        if r['earnings_risk']:
            print(f"       ⚠️ 注意: 此合約到期前有財報，IV Crush 風險")

    print_recommendation("⭐ 最佳綜合", best_overall)

    if not short.empty:
        bs = short.iloc[0]
        if bs.name != best_overall.name:
            print_recommendation("⚡ 短天期最佳 (≤21天)", bs)

    if not mid.empty:
        bm = mid.iloc[0]
        if bm.name != best_overall.name:
            print_recommendation("📌 中天期最佳 (25~60天)", bm)

    if not far.empty:
        bf = far.iloc[0]
        if bf.name != best_overall.name:
            print_recommendation("🔭 遠月最佳 (>60天)", bf)

    # === 9. 損益情境模擬 (用最佳合約) ===
    r = best_overall
    print(f"\n{'═' * 62}")
    print(f"💹 損益情境模擬 (最佳合約: {r['expiry']} K=${r['strike']})")
    print(f"{'═' * 62}")

    scenarios = simulator.simulate_scenarios(
        S=spot, K=r['strike'], T=r['dte']/365,
        r=RISK_FREE_RATE, sigma=r['iv'], premium=r['ask'],
        target_prices=[spot * (1 + p) for p in [-0.10, -0.05, 0, 0.03, 0.05, 0.10, 0.15, 0.20]],
        target_days=[1, 7, r['dte']//2, r['dte']],
    )

    # 格式化為表格
    # 取唯一天數
    days_list = sorted(set(s['days_held'] for s in scenarios))
    prices_list = sorted(set(s['target_price'] for s in scenarios))

    # 建立 lookup
    lookup = {}
    for s in scenarios:
        lookup[(s['target_price'], s['days_held'])] = s

    # 表頭
    header = f"  {'股價':<10}"
    for d in days_list:
        header += f" {'持有'+str(d)+'天':>10}"
    print(header)
    print(f"  {'─'*10}" + f" {'─'*10}" * len(days_list))

    for p in prices_list:
        chg = (p / spot - 1) * 100
        line = f"  ${p:<7.2f} ({chg:+.0f}%)"
        for d in days_list:
            s = lookup.get((p, d))
            if s:
                pnl = s['pnl']
                pnl_pct = s['pnl_pct']
                if pnl >= 0:
                    cell = f"+${pnl:.2f}"
                else:
                    cell = f"-${abs(pnl):.2f}"
                line += f" {cell:>10}"
            else:
                line += f" {'—':>10}"
        print(line)

    print(f"\n  💡 每張合約 = 100 股，實際損益 = 上表數值 × 100 × 張數")
    print(f"     以 {r['contracts']} 張計: 最大虧損 = ${r['total_cost']:,.0f}")

    # === 10. 總結 ===
    print(f"\n{'═' * 62}")
    print(f"📝 分析總結 — {ticker}")
    print(f"{'═' * 62}")
    print(f"  趨勢: {trend_label}")
    print(f"  IV 環境: {iv_label} (Rank {first['iv_rank']:.0f}%)")

    if first['iv_rank'] > 50:
        print(f"  ⚠️ IV 偏高，Buy Call 成本較貴。可考慮:")
        print(f"     • Bull Call Spread (降低 IV 影響)")
        print(f"     • 等待 IV 回落再進場")
        print(f"     • 選擇較高 Delta (ITM) 的合約 (受 IV 影響較小)")
    else:
        print(f"  🟢 IV 偏低，適合做 Long Call (買便宜保費)")

    if trend_label.startswith("🔴"):
        print(f"  ⚠️ 技術面偏弱，Buy Call 風險較高。建議等待趨勢轉強。")
    elif trend_label.startswith("🟢"):
        print(f"  🟢 技術面強勢，適合 Buy Call 順勢操作。")

    print(f"\n  最佳合約: {best_overall['expiry']} K=${best_overall['strike']}"
          f" Ask=${best_overall['ask']:.2f} (Score {best_overall['score']:.0f})")
    print(f"{'═' * 62}\n")

    return all_contracts


# ============================================================
# 主程式
# ============================================================
if __name__ == '__main__':
    if len(sys.argv) > 1:
        ticker = sys.argv[1]
    else:
        print("\n╔════════════════════════════════════════╗")
        print("║  Anti-Gravity | Buy Call 合約分析器    ║")
        print("║  輸入股票代號開始分析                   ║")
        print("╚════════════════════════════════════════╝")
        ticker = input("\n🔎 請輸入股票代號 (例如 NVDA, TSLA, AAPL): ").strip()

    if ticker:
        analyze_ticker(ticker)

        # 互動式: 可以繼續分析其他股票
        while True:
            next_ticker = input("\n🔎 分析下一檔? (輸入代號，或按 Enter 結束): ").strip()
            if not next_ticker:
                print("👋 掃描結束。")
                break
            analyze_ticker(next_ticker)
    else:
        print("❌ 未輸入股票代號。")
