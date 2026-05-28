"""
generate_power_gauge.py - 個股綜合戰力評分引擎 (Stock Power Gauge)
====================================================================
為每檔股票計算 0~100 的五維綜合評分：
  1. 動能 (Momentum) 25%  — 報酬率排名、RSI、均線位置
  2. 技術面 (Technical) 20% — Squeeze、均線排列、ATR、距高點
  3. 基本面 (Fundamental) 25% — P/E、PEG、營收成長、ROE、毛利率
  4. 法人共識 (Consensus) 15% — 分析師目標價上檔、推薦評級
  5. 風險 (Risk ★反向) 15% — Beta、D/E、距低點、波動率

輸出: Web_Dashboard/power_gauge_data.json
"""

import os
import sys
import json
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

from scanner_base import AI_TECH_STOCKS, DASHBOARD_DIR

OUTPUT_PATH = os.path.join(DASHBOARD_DIR, 'power_gauge_data.json')

# 維度權重
WEIGHTS = {
    'momentum': 0.25,
    'technical': 0.20,
    'fundamental': 0.25,
    'consensus': 0.15,
    'risk': 0.15,
}


def clamp(val, lo=0, hi=100):
    """限制在 0~100"""
    if val is None or np.isnan(val):
        return 50
    return max(lo, min(hi, val))


def percentile_score(val, arr, ascending=True):
    """
    將 val 在 arr 中的百分位轉成 0~100 分。
    ascending=True: 值越大分數越高 (如報酬率)
    ascending=False: 值越小分數越高 (如 P/E)
    """
    if val is None or np.isnan(val):
        return 50
    arr_clean = [x for x in arr if x is not None and not np.isnan(x)]
    if not arr_clean:
        return 50
    rank = sum(1 for x in arr_clean if x <= val)
    pct = rank / len(arr_clean) * 100
    return pct if ascending else (100 - pct)


def calc_rsi(close_series, period=14):
    """計算 RSI"""
    if len(close_series) < period + 1:
        return 50
    delta = close_series.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    val = rsi.iloc[-1]
    return float(val) if not np.isnan(val) else 50


def check_squeeze(close, high, low, window=20):
    """TTM Squeeze 檢測"""
    if len(close) < window:
        return False
    ma = close.rolling(window).mean()
    std = close.rolling(window).std()
    bb_up = ma + (2 * std)
    bb_low = ma - (2 * std)
    tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.rolling(window).mean()
    kc_up = ma + (1.5 * atr)
    kc_low = ma - (1.5 * atr)
    return bool((bb_up.iloc[-1] < kc_up.iloc[-1]) and (bb_low.iloc[-1] > kc_low.iloc[-1]))


def generate_power_gauge():
    """主函式：計算所有股票的五維評分"""
    tickers = AI_TECH_STOCKS
    print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚡ Power Gauge: 正在計算 {len(tickers)} 檔股票的綜合戰力評分...")

    # ===== Phase 1: 下載價格數據 =====
    print("  → 下載 6 個月價格數據...")
    tickers_str = " ".join(tickers)
    df_6m = yf.download(tickers_str, period="6mo", interval="1d", progress=False, group_by='ticker')

    # ===== Phase 2: 讀取現有的基本面數據 =====
    fundamentals = {}
    fund_path = os.path.join(DASHBOARD_DIR, 'fundamentals_data.json')
    if os.path.exists(fund_path):
        try:
            with open(fund_path, 'r', encoding='utf-8') as f:
                fdata = json.load(f)
                fundamentals = fdata.get('stocks', {})
        except Exception:
            pass

    # ===== Phase 3: 讀取現有的動能數據 =====
    momentum_data = {}
    mom_path = os.path.join(DASHBOARD_DIR, 'momentum_data.json')
    if os.path.exists(mom_path):
        try:
            with open(mom_path, 'r', encoding='utf-8') as f:
                mdata = json.load(f)
                for stock in mdata.get('stocks', []):
                    momentum_data[stock['ticker']] = stock
        except Exception:
            pass

    # ===== Phase 4: 讀取各 Scanner 信號 =====
    scanner_signals = {}
    for fname, key in [('td9_data.json', 'td9'), ('ma_data.json', 'ma'), 
                        ('hod_data.json', 'hod'), ('orb_data.json', 'orb')]:
        fpath = os.path.join(DASHBOARD_DIR, fname)
        if os.path.exists(fpath):
            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    sdata = json.load(f)
                    scanner_signals[key] = sdata
            except Exception:
                pass

    # ===== Phase 5: 計算每檔的原始指標 =====
    raw_metrics = {}
    print("  → 計算技術指標...")

    for ticker in tickers:
        try:
            if len(tickers) > 1:
                t_data = df_6m[ticker].dropna(subset=['Close'])
            else:
                t_data = df_6m.dropna(subset=['Close'])

            if len(t_data) < 20:
                continue

            close = t_data['Close']
            high = t_data['High']
            low = t_data['Low']
            volume = t_data['Volume']

            last_price = float(close.iloc[-1])

            # --- 報酬率 ---
            ret_1m = float((close.iloc[-1] / close.iloc[-21] - 1) * 100) if len(close) >= 21 else 0
            ret_3m = float((close.iloc[-1] / close.iloc[-63] - 1) * 100) if len(close) >= 63 else 0
            ret_6m = float((close.iloc[-1] / close.iloc[0] - 1) * 100)

            # --- RSI ---
            rsi = calc_rsi(close)

            # --- 均線 ---
            ma10 = float(close.rolling(10).mean().iloc[-1])
            ma20 = float(close.rolling(20).mean().iloc[-1])
            ma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else ma20
            ma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else ma50

            above_ma10 = last_price > ma10
            above_ma20 = last_price > ma20
            above_ma50 = last_price > ma50

            # 均線排列 (多頭排列 = 10 > 20 > 50)
            ma_aligned = ma10 > ma20 > ma50

            # --- Squeeze ---
            is_squeezed = check_squeeze(close, high, low)

            # --- ATR / 波動率 ---
            tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
            atr14 = float(tr.rolling(14).mean().iloc[-1])
            atr_pct = atr14 / last_price * 100

            # --- 52W 高低 ---
            high_52w = float(high.max())
            low_52w = float(low.min())
            pct_from_high = (last_price - high_52w) / high_52w * 100
            pct_from_low = (last_price - low_52w) / low_52w * 100

            # --- 成交量趨勢與 OBV ---
            vol_avg20 = float(volume.rolling(20).mean().iloc[-1])
            vol_avg5 = float(volume.rolling(5).mean().iloc[-1])
            vol_ratio = vol_avg5 / vol_avg20 if vol_avg20 > 0 else 1

            obv = (np.sign(close.diff()) * volume).fillna(0).cumsum()
            obv_5d = float(obv.rolling(5).mean().iloc[-1])
            obv_20d = float(obv.rolling(20).mean().iloc[-1])
            obv_accumulating = obv_5d > obv_20d

            # --- 從基本面資料讀取 ---
            fund = fundamentals.get(ticker, {})
            pe_fwd = fund.get('pe_fwd')
            pe_ttm = fund.get('pe_ttm')
            peg = fund.get('peg')
            rev_growth = fund.get('rev_growth')
            earn_growth = fund.get('earn_growth')
            gross_margin = fund.get('gross_margin')
            op_margin = fund.get('op_margin')
            roe = fund.get('roe')
            roa = fund.get('roa')
            de_ratio = fund.get('de_ratio')
            current_ratio = fund.get('current_ratio')
            beta = fund.get('beta')
            fcf_yield = fund.get('fcf_yield')
            target_upside = fund.get('upside')
            target_price = fund.get('target')
            rec = fund.get('rec', 'N/A')
            analysts = fund.get('analysts', 0)
            mcap_fmt = fund.get('mcap_fmt', '')
            name = fund.get('name', ticker)
            sector = fund.get('sector', '')
            industry = fund.get('industry', '')
            inst_holders = fund.get('inst_holders')
            short_ratio = fund.get('short_ratio')
            eps_up_30d = fund.get('eps_up_30d', 0)
            eps_down_30d = fund.get('eps_down_30d', 0)
            options_pc_ratio = fund.get('options_pc_ratio')

            # --- 交易規劃 (Trade Plan) ---
            swing_low_20d = float(low.tail(20).min())
            swing_high_60d = float(high.tail(60).max())
            
            # 停損點：20MA 或 近1個月低點，取兩者較高者 (保護性防守)
            # 如果跌破這兩者，說明多頭結構破壞
            stop_loss = max(ma20, swing_low_20d)
            if last_price <= stop_loss:
                # 已經在停損點之下，改用近期低點作為支撐
                stop_loss = swing_low_20d * 0.98 
                
            # 目標價：分析師目標 或 近3個月高點，取兩者較高者
            plan_target = max(target_price or last_price, swing_high_60d)
            if plan_target <= last_price:
                plan_target = last_price * 1.15 # 預設至少 15% 空間
                
            risk = last_price - stop_loss
            reward = plan_target - last_price
            rr_ratio = reward / risk if risk > 0 else 9.99

            raw_metrics[ticker] = {
                'ticker': ticker,
                'name': name,
                'sector': sector,
                'industry': industry,
                'price': last_price,
                'mcap_fmt': mcap_fmt,
                # Momentum raw
                'ret_1m': ret_1m,
                'ret_3m': ret_3m,
                'ret_6m': ret_6m,
                'rsi': rsi,
                'above_ma10': above_ma10,
                'above_ma20': above_ma20,
                'above_ma50': above_ma50,
                # Technical raw
                'ma_aligned': ma_aligned,
                'is_squeezed': is_squeezed,
                'pct_from_high': pct_from_high,
                'atr_pct': atr_pct,
                'vol_ratio': vol_ratio,
                # Fundamental raw
                'pe_fwd': pe_fwd,
                'pe_ttm': pe_ttm,
                'peg': peg,
                'rev_growth': rev_growth,
                'earn_growth': earn_growth,
                'gross_margin': gross_margin,
                'op_margin': op_margin,
                'roe': roe,
                'de_ratio': de_ratio,
                'beta': beta,
                'fcf_yield': fcf_yield,
                'pct_from_low': pct_from_low,
                # Consensus & Smart Money
                'target_upside': target_upside,
                'rec': rec,
                'analysts': analysts or 0,
                'obv_accumulating': obv_accumulating,
                'inst_holders': inst_holders,
                'short_ratio': short_ratio,
                'eps_up_30d': eps_up_30d,
                'eps_down_30d': eps_down_30d,
                'options_pc_ratio': options_pc_ratio,
                # Trade Plan
                'stop_loss': stop_loss,
                'plan_target': plan_target,
                'rr_ratio': rr_ratio,
            }
        except Exception as e:
            print(f"  ⚠️ {ticker}: {e}")

    if not raw_metrics:
        print("  ❌ 無法計算任何股票的指標")
        return

    # ===== Phase 6: 百分位排名計算五維分數 =====
    print(f"  → 為 {len(raw_metrics)} 檔股票計算五維評分...")

    # 收集所有值供排名
    all_ret1m = [m['ret_1m'] for m in raw_metrics.values()]
    all_ret3m = [m['ret_3m'] for m in raw_metrics.values()]
    all_ret6m = [m['ret_6m'] for m in raw_metrics.values()]
    all_pe_fwd = [m['pe_fwd'] for m in raw_metrics.values() if m['pe_fwd'] and m['pe_fwd'] > 0]
    all_peg = [m['peg'] for m in raw_metrics.values() if m['peg'] and m['peg'] > 0]
    all_rev_growth = [m['rev_growth'] for m in raw_metrics.values() if m['rev_growth'] is not None]
    all_gross_margin = [m['gross_margin'] for m in raw_metrics.values() if m['gross_margin'] is not None]
    all_roe = [m['roe'] for m in raw_metrics.values() if m['roe'] is not None]

    results = []

    for ticker, m in raw_metrics.items():
        # --- 1. 動能分數 (25%) ---
        s_ret1m = percentile_score(m['ret_1m'], all_ret1m, ascending=True)
        s_ret3m = percentile_score(m['ret_3m'], all_ret3m, ascending=True)
        s_ret6m = percentile_score(m['ret_6m'], all_ret6m, ascending=True)
        # RSI: 50~70 最佳, <30 太弱, >80 太超買
        if m['rsi'] < 30:
            s_rsi = 20
        elif m['rsi'] < 50:
            s_rsi = 40 + (m['rsi'] - 30) / 20 * 30
        elif m['rsi'] <= 70:
            s_rsi = 70 + (m['rsi'] - 50) / 20 * 30
        else:
            s_rsi = max(30, 100 - (m['rsi'] - 70) * 3)

        s_ma = (30 if m['above_ma10'] else 0) + (35 if m['above_ma20'] else 0) + (35 if m['above_ma50'] else 0)

        momentum_score = clamp(
            s_ret1m * 0.25 + s_ret3m * 0.25 + s_ret6m * 0.15 + s_rsi * 0.15 + s_ma * 0.20
        )

        # --- 2. 技術面分數 (20%) ---
        s_aligned = 100 if m['ma_aligned'] else 30
        s_squeeze = 85 if m['is_squeezed'] else 50  # Squeeze 中 = 蓄勢待發
        # 距52W高點: 0% = 在高點 (好), -30% = 離高很遠 (差)
        s_from_high = clamp(100 + m['pct_from_high'] * 2)
        # ATR%: 2-4% 適中偏好, 太高太低都扣分
        if m['atr_pct'] < 1:
            s_atr = 30
        elif m['atr_pct'] <= 4:
            s_atr = 60 + (m['atr_pct'] - 1) / 3 * 30
        else:
            s_atr = max(20, 90 - (m['atr_pct'] - 4) * 10)
        # 量能: 近5日均量 > 20日均量 = 量增
        s_vol = clamp(50 + (m['vol_ratio'] - 1) * 50)

        technical_score = clamp(
            s_aligned * 0.30 + s_squeeze * 0.20 + s_from_high * 0.25 + s_atr * 0.10 + s_vol * 0.15
        )

        # --- 3. 基本面分數 (25%) ---
        pe = m['pe_fwd'] if m['pe_fwd'] and m['pe_fwd'] > 0 else m['pe_ttm']
        s_pe = percentile_score(pe, all_pe_fwd, ascending=False) if pe and pe > 0 else 50
        s_peg = percentile_score(m['peg'], all_peg, ascending=False) if m['peg'] and m['peg'] > 0 else 50
        s_rev = percentile_score(m['rev_growth'], all_rev_growth, ascending=True) if m['rev_growth'] is not None else 50
        s_margin = percentile_score(m['gross_margin'], all_gross_margin, ascending=True) if m['gross_margin'] is not None else 50
        s_roe_val = percentile_score(m['roe'], all_roe, ascending=True) if m['roe'] is not None else 50

        fundamental_score = clamp(
            s_pe * 0.20 + s_peg * 0.15 + s_rev * 0.25 + s_margin * 0.20 + s_roe_val * 0.20
        )

        # --- 4. 法人共識分數 (15%) ---
        # 目標價上檔空間
        if m['target_upside'] is not None:
            if m['target_upside'] > 30:
                s_upside = 95
            elif m['target_upside'] > 15:
                s_upside = 70 + (m['target_upside'] - 15) / 15 * 25
            elif m['target_upside'] > 0:
                s_upside = 50 + m['target_upside'] / 15 * 20
            else:
                s_upside = max(10, 50 + m['target_upside'] * 2)
        else:
            s_upside = 50

        # 推薦評級
        rec_scores = {'strongBuy': 95, 'buy': 80, 'hold': 50, 'sell': 25, 'strongSell': 10}
        s_rec = rec_scores.get(m['rec'], 50)

        # 分析師覆蓋數
        s_analysts = clamp(min(100, m['analysts'] * 3)) if m['analysts'] else 30

        consensus_score = clamp(
            s_upside * 0.45 + s_rec * 0.35 + s_analysts * 0.20
        )

        # --- 5. 風險分數 (15%, 反向：越低風險越高分) ---
        # Beta: 1.0 最佳, 越高越差
        if m['beta'] is not None:
            s_beta = clamp(100 - abs(m['beta'] - 1.0) * 30)
        else:
            s_beta = 50

        # D/E: 越低越好
        if m['de_ratio'] is not None:
            if m['de_ratio'] < 50:
                s_de = 90
            elif m['de_ratio'] < 100:
                s_de = 70
            elif m['de_ratio'] < 200:
                s_de = 50
            else:
                s_de = max(10, 50 - (m['de_ratio'] - 200) / 10)
        else:
            s_de = 50

        # 距 52W 低點: 越遠越安全 (越好)
        s_from_low = clamp(min(100, 30 + m['pct_from_low'] * 0.7))

        # ATR 波動率反向 (太波動 = 高風險)
        s_vol_risk = clamp(100 - m['atr_pct'] * 12)

        risk_score = clamp(
            s_beta * 0.30 + s_de * 0.25 + s_from_low * 0.25 + s_vol_risk * 0.20
        )

        # --- 綜合總分 ---
        total_score = (
            momentum_score * WEIGHTS['momentum'] +
            technical_score * WEIGHTS['technical'] +
            fundamental_score * WEIGHTS['fundamental'] +
            consensus_score * WEIGHTS['consensus'] +
            risk_score * WEIGHTS['risk']
        )

        # 組裝信號彙整 (從各 scanner JSON 讀取)
        signals = []
        td9_data = scanner_signals.get('td9', {})
        if isinstance(td9_data.get('sell'), list):
            for s in td9_data['sell']:
                if s.get('ticker') == ticker:
                    signals.append({'type': 'td9_sell', 'label': f"TD{s.get('count',9)} 賣出竭盡"})
        if isinstance(td9_data.get('buy'), list):
            for s in td9_data['buy']:
                if s.get('ticker') == ticker:
                    signals.append({'type': 'td9_buy', 'label': f"TD{s.get('count',9)} 買入竭盡"})
        ma_data = scanner_signals.get('ma', {})
        if isinstance(ma_data.get('signals'), list):
            for s in ma_data['signals']:
                if s.get('ticker') == ticker:
                    signals.append({'type': 'ma_touch', 'label': f"觸碰 {s.get('ma','MA')} 回測"})
        # Squeeze 也算信號
        if m['is_squeezed']:
            signals.append({'type': 'squeeze', 'label': 'TTM Squeeze 擠壓中'})
        # 動能信號
        mom_info = momentum_data.get(ticker, {})
        if mom_info.get('setup_type'):
            signals.append({'type': 'momentum', 'label': mom_info['setup_type']})

        # 判定等級
        if total_score >= 80:
            grade = 'S'
            grade_label = '強力推薦'
        elif total_score >= 65:
            grade = 'A'
            grade_label = '值得關注'
        elif total_score >= 50:
            grade = 'B'
            grade_label = '中性觀望'
        elif total_score >= 35:
            grade = 'C'
            grade_label = '偏弱謹慎'
        else:
            grade = 'D'
            grade_label = '避開'

        results.append({
            'ticker': ticker,
            'name': m['name'],
            'sector': m['sector'],
            'industry': m['industry'],
            'price': round(m['price'], 2),
            'mcap_fmt': m['mcap_fmt'],
            'total_score': round(total_score, 1),
            'grade': grade,
            'grade_label': grade_label,
            'dimensions': {
                'momentum': round(momentum_score, 1),
                'technical': round(technical_score, 1),
                'fundamental': round(fundamental_score, 1),
                'consensus': round(consensus_score, 1),
                'risk': round(risk_score, 1),
            },
            'trade_plan': {
                'stop_loss': round(m['stop_loss'], 2),
                'target': round(m['plan_target'], 2),
                'rr_ratio': round(m['rr_ratio'], 2),
                'risk_pct': round((m['stop_loss'] / m['price'] - 1) * 100, 1),
                'reward_pct': round((m['plan_target'] / m['price'] - 1) * 100, 1),
            },
            'smart_money': {
                'obv_accumulating': m['obv_accumulating'],
                'inst_holders': m['inst_holders'],
                'short_ratio': m['short_ratio'],
                'options_pc_ratio': m['options_pc_ratio'],
                'eps_up_30d': m['eps_up_30d'],
                'eps_down_30d': m['eps_down_30d'],
            },
            'key_metrics': {
                'ret_1m': round(m['ret_1m'], 1),
                'ret_3m': round(m['ret_3m'], 1),
                'rsi': round(m['rsi'], 1),
                'pe_fwd': round(pe, 1) if pe else None,
                'rev_growth': m['rev_growth'],
                'gross_margin': m['gross_margin'],
                'roe': m['roe'],
                'target_upside': m['target_upside'],
                'rec': m['rec'],
                'beta': m['beta'],
                'is_squeezed': m['is_squeezed'],
                'ma_aligned': m['ma_aligned'],
                'pct_from_high': round(m['pct_from_high'], 1),
            },
            'signals': signals,
        })

    # 按總分排序
    results.sort(key=lambda x: x['total_score'], reverse=True)

    # 加上排名
    for i, r in enumerate(results):
        r['rank'] = i + 1

    # 輸出
    output = {
        'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'total': len(results),
        'weights': WEIGHTS,
        'stocks': results,
    }

    os.makedirs(DASHBOARD_DIR, exist_ok=True)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # 印出 Top 10
    print(f"\n  ⚡ Power Gauge 排行榜 (Top 10):")
    print(f"  {'排名':>4} {'代碼':<6} {'總分':>5} {'等級':>2} {'動能':>5} {'技術':>5} {'基本':>5} {'共識':>5} {'風險':>5}")
    print(f"  {'─'*60}")
    for r in results[:10]:
        d = r['dimensions']
        print(f"  {r['rank']:>4} {r['ticker']:<6} {r['total_score']:>5.1f} {r['grade']:>2}"
              f"  {d['momentum']:>5.1f} {d['technical']:>5.1f} {d['fundamental']:>5.1f}"
              f"  {d['consensus']:>5.1f} {d['risk']:>5.1f}")

    print(f"\n  ✅ Power Gauge 完成！{len(results)} 檔已寫入 power_gauge_data.json\n")
    return output


if __name__ == '__main__':
    generate_power_gauge()
