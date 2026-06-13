"""
generate_fundamentals.py - 個股基本面數據引擎 (Buy-Side 級別)
================================================================
下載所有監控股票的基本面數據，涵蓋基金經理人最關注的核心指標：

  1. 估值面 (Valuation): P/E, Forward P/E, P/S, PEG, EV/EBITDA
  2. 獲利能力 (Profitability): EPS, 毛利率, 營業利潤率, 淨利率, ROE, ROA
  3. 成長性 (Growth): 營收 YoY, EPS YoY
  4. 財務體質 (Financial Health): D/E, 流動比率, FCF, FCF Yield
  5. 市場概況 (Market): 市值, Beta, 52W 高低, 分析師目標價
  6. 季度趨勢 (Quarterly Trends): 最近 8 季營收/EPS/毛利率走勢

用法：
  python generate_fundamentals.py
"""

import os
import sys

if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

import json
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
from scanner_base import AI_TECH_STOCKS, DASHBOARD_DIR


# ============================================================
# 工具函式
# ============================================================
def safe_get(info, key, default=None):
    """安全取值，避免 KeyError 或 NaN"""
    val = info.get(key, default)
    if val is None:
        return default
    if isinstance(val, float) and (np.isnan(val) or np.isinf(val)):
        return default
    return val


def fmt_num(num):
    """格式化大數字: 1234567890 -> '1.23B'"""
    if num is None:
        return None
    num = float(num)
    if abs(num) >= 1e12:
        return f"{num/1e12:.2f}T"
    elif abs(num) >= 1e9:
        return f"{num/1e9:.2f}B"
    elif abs(num) >= 1e6:
        return f"{num/1e6:.1f}M"
    elif abs(num) >= 1e3:
        return f"{num/1e3:.1f}K"
    return f"{num:.2f}"


def safe_round(val, digits=1):
    """安全四捨五入"""
    if val is None or val == 0:
        return None
    try:
        return round(float(val), digits)
    except (TypeError, ValueError):
        return None


def safe_pct(val, digits=1):
    """將小數轉為百分比 (0.55 -> 55.0)，返回 None 若無效"""
    if val is None or val == 0:
        return None
    try:
        result = round(float(val) * 100, digits)
        if abs(result) > 9999:  # 過大的數字通常是錯誤
            return None
        return result
    except (TypeError, ValueError):
        return None


def safe_int(val, default=0):
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return default
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def safe_float(val, default=0.0):
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


# ============================================================
# 季度趨勢數據
# ============================================================
def get_quarterly_trends(stock):
    """取得最近 8 季的營收、EPS、毛利率趨勢"""
    quarters = []

    try:
        income = stock.quarterly_income_stmt
        if income is None or income.empty:
            return quarters

        # 嘗試取得流通股數
        balance = stock.quarterly_balance_sheet
        shares_series = None
        if balance is not None and not balance.empty:
            for label in ['Ordinary Shares Number', 'Share Issued',
                          'Common Stock Shares Outstanding']:
                if label in balance.index:
                    shares_series = balance.loc[label]
                    break

        for col in income.columns[:8]:  # 最近 8 季
            q = {}
            month = col.month
            year = col.year
            q['date'] = col.strftime('%Y-%m-%d')
            q['label'] = f"Q{(month - 1) // 3 + 1}'{str(year)[-2:]}"

            # 營收
            for key in ['Total Revenue', 'Revenue']:
                if key in income.index:
                    v = income.loc[key, col]
                    if pd.notna(v):
                        q['revenue'] = float(v)
                        q['revenue_fmt'] = fmt_num(v)
                    break

            # 淨利
            for key in ['Net Income', 'Net Income Common Stockholders']:
                if key in income.index:
                    v = income.loc[key, col]
                    if pd.notna(v):
                        q['net_income'] = float(v)
                    break

            # 毛利
            if 'Gross Profit' in income.index:
                v = income.loc['Gross Profit', col]
                if pd.notna(v):
                    q['gross_profit'] = float(v)

            # 營業利益
            for key in ['Operating Income', 'EBIT']:
                if key in income.index:
                    v = income.loc[key, col]
                    if pd.notna(v):
                        q['operating_income'] = float(v)
                    break

            # 計算 EPS
            if 'net_income' in q and shares_series is not None:
                try:
                    closest = min(shares_series.index,
                                  key=lambda x: abs(x - col))
                    sc = shares_series[closest]
                    if pd.notna(sc) and float(sc) > 0:
                        q['eps'] = round(float(q['net_income']) / float(sc), 2)
                except Exception:
                    pass

            # 計算利潤率
            rev = q.get('revenue', 0)
            if rev and rev > 0:
                if 'gross_profit' in q:
                    q['gross_margin'] = round(q['gross_profit'] / rev * 100, 1)
                if 'operating_income' in q:
                    q['op_margin'] = round(
                        q['operating_income'] / rev * 100, 1)
                if 'net_income' in q:
                    q['net_margin'] = round(q['net_income'] / rev * 100, 1)

            quarters.append(q)

    except Exception:
        pass

    # 計算 YoY 成長率 (跟 4 季前比)
    for i in range(len(quarters)):
        if i + 4 < len(quarters):
            prev = quarters[i + 4]
            cur = quarters[i]
            if 'revenue' in cur and 'revenue' in prev and prev['revenue'] != 0:
                cur['rev_yoy'] = round(
                    (cur['revenue'] - prev['revenue'])
                    / abs(prev['revenue']) * 100, 1)
            if 'eps' in cur and 'eps' in prev and prev['eps'] != 0:
                cur['eps_yoy'] = round(
                    (cur['eps'] - prev['eps'])
                    / abs(prev['eps']) * 100, 1)

    return quarters


# ============================================================
# 單一股票基本面
# ============================================================
def fetch_fundamentals(ticker):
    """取得單一股票的完整基本面數據"""
    try:
        stock = yf.Ticker(ticker)
        info = stock.info

        if not info or 'symbol' not in info:
            return None

        price = safe_get(info, 'currentPrice') or safe_get(
            info, 'regularMarketPrice', 0)
        h52 = safe_get(info, 'fiftyTwoWeekHigh', 0)
        l52 = safe_get(info, 'fiftyTwoWeekLow', 0)
        mcap = safe_get(info, 'marketCap')
        fcf = safe_get(info, 'freeCashflow')

        pct_h = round((price - h52) / h52 * 100, 1) if h52 > 0 else None
        pct_l = round((price - l52) / l52 * 100, 1) if l52 > 0 else None
        fcf_y = (round(fcf / mcap * 100, 2)
                 if fcf and mcap and mcap > 0 else None)

        target = safe_get(info, 'targetMeanPrice')
        upside = (round((target - price) / price * 100, 1)
                  if target and price and price > 0 else None)

        data = {
            # 基本資訊
            'ticker': ticker,
            'name': safe_get(info, 'shortName', ticker),
            'sector': safe_get(info, 'sector', 'N/A'),
            'industry': safe_get(info, 'industry', 'N/A'),
            'price': round(price, 2) if price else None,

            # 估值面
            'pe_ttm': safe_round(safe_get(info, 'trailingPE')),
            'pe_fwd': safe_round(safe_get(info, 'forwardPE')),
            'ps': safe_round(safe_get(info, 'priceToSalesTrailing12Months')),
            'pb': safe_round(safe_get(info, 'priceToBook')),
            'peg': safe_round(safe_get(info, 'pegRatio'), 2),
            'ev_ebitda': safe_round(safe_get(info, 'enterpriseToEbitda')),

            # 獲利能力
            'eps_ttm': safe_round(safe_get(info, 'trailingEps'), 2),
            'eps_fwd': safe_round(safe_get(info, 'forwardEps'), 2),
            'rev_ttm': safe_get(info, 'totalRevenue'),
            'rev_fmt': fmt_num(safe_get(info, 'totalRevenue')),
            'gross_margin': safe_pct(safe_get(info, 'grossMargins')),
            'op_margin': safe_pct(safe_get(info, 'operatingMargins')),
            'net_margin': safe_pct(safe_get(info, 'profitMargins')),
            'roe': safe_pct(safe_get(info, 'returnOnEquity')),
            'roa': safe_pct(safe_get(info, 'returnOnAssets')),

            # 成長性
            'rev_growth': safe_pct(safe_get(info, 'revenueGrowth')),
            'earn_growth': safe_pct(safe_get(info, 'earningsGrowth')),

            # 財務體質
            'de_ratio': safe_round(safe_get(info, 'debtToEquity')),
            'current_ratio': safe_round(safe_get(info, 'currentRatio'), 2),
            'fcf': fcf,
            'fcf_fmt': fmt_num(fcf),
            'fcf_yield': fcf_y,
            'totalDebt': safe_get(info, 'totalDebt'),
            'totalCash': safe_get(info, 'totalCash'),
            'sharesOutstanding': safe_get(info, 'sharesOutstanding'),
            'operatingCashflow': safe_get(info, 'operatingCashflow'),
            'capitalExpenditure': safe_get(info, 'capitalExpenditure'),

            # 市場概況
            'mcap': mcap,
            'mcap_fmt': fmt_num(mcap),
            'beta': safe_round(safe_get(info, 'beta'), 2),
            'h52': round(h52, 2) if h52 else None,
            'l52': round(l52, 2) if l52 else None,
            'pct_h52': pct_h,
            'pct_l52': pct_l,
            'div_yield': safe_pct(safe_get(info, 'dividendYield'), 2),

            # 籌碼與資金面 (Smart Money)
            'inst_holders': safe_pct(safe_get(info, 'heldPercentInstitutions'), 1),
            'short_ratio': safe_round(safe_get(info, 'shortRatio'), 2),

            # 分析師共識
            'target': round(target, 2) if target else None,
            'target_hi': safe_round(safe_get(info, 'targetHighPrice'), 2),
            'target_lo': safe_round(safe_get(info, 'targetLowPrice'), 2),
            'analysts': safe_get(info, 'numberOfAnalystOpinions'),
            'rec': safe_get(info, 'recommendationKey', 'N/A'),
            'upside': upside,
        }

        # --- 財報預期修正 (Earnings Revisions) ---
        eps_up = 0
        eps_down = 0
        try:
            rev_df = stock.eps_revisions
            if rev_df is not None and not rev_df.empty:
                if '0y' in rev_df.index:
                    eps_up = int(rev_df.loc['0y', 'upLast30days'])
                    eps_down = int(rev_df.loc['0y', 'downLast30days'])
                elif len(rev_df) > 0:
                    eps_up = int(rev_df.iloc[0].get('upLast30days', 0))
                    eps_down = int(rev_df.iloc[0].get('downLast30days', 0))
        except Exception:
            pass
        data['eps_up_30d'] = eps_up
        data['eps_down_30d'] = eps_down

        # --- 選擇權金流 (Options Put/Call Ratio) ---
        pc_ratio = None
        oi_pc_ratio = None
        try:
            dates = stock.options
            if dates:
                total_c_vol = 0
                total_p_vol = 0
                total_c_oi = 0
                total_p_oi = 0
                for d in dates[:2]:
                    chain = stock.option_chain(d)
                    if chain.calls is not None and not chain.calls.empty:
                        total_c_vol += chain.calls['volume'].fillna(0).sum()
                        total_c_oi += chain.calls['openInterest'].fillna(0).sum()
                    if chain.puts is not None and not chain.puts.empty:
                        total_p_vol += chain.puts['volume'].fillna(0).sum()
                        total_p_oi += chain.puts['openInterest'].fillna(0).sum()
                
                if total_c_vol > 0:
                    pc_ratio = round(float(total_p_vol) / float(total_c_vol), 2)
                elif total_p_vol > 0:
                    pc_ratio = 9.99
                
                if total_c_oi > 0:
                    oi_pc_ratio = round(float(total_p_oi) / float(total_c_oi), 2)
        except Exception:
            pass
        data['options_pc_ratio'] = pc_ratio
        data['options_oi_pc_ratio'] = oi_pc_ratio

        # --- 選擇權合約推薦與損益模擬 (Long Call Options Selection & Simulation) ---
        options_analysis = None
        try:
            dates = stock.options
            if dates:
                from options_pricing import BlackScholes, PnLSimulator
                bs = BlackScholes()
                simulator = PnLSimulator()
                
                all_calls = []
                today_date = datetime.now().date()
                r_rate = 0.043
                
                # 下載日線歷史波動率作為 sigma 備用
                daily_close = stock.history(period="1mo")['Close']
                log_ret = np.log(daily_close / daily_close.shift(1)).dropna()
                hv_20 = float(log_ret.std() * np.sqrt(252)) if len(log_ret) >= 5 else 0.35
                if np.isnan(hv_20) or hv_20 <= 0:
                    hv_20 = 0.35
                
                # 掃描前 5 個到期日，以保證下載速度與流動性
                for d in dates[:5]:
                    expiry_date = datetime.strptime(d, '%Y-%m-%d').date()
                    dte = (expiry_date - today_date).days
                    if dte < 3 or dte > 120:
                        continue
                    T_years = dte / 365.0
                    
                    try:
                        chain = stock.option_chain(d)
                        calls = chain.calls
                        if calls is None or calls.empty:
                            continue
                    except Exception:
                        continue
                    
                    # 估算這期到期的 ATM IV
                    closest_atm_df = calls.copy()
                    closest_atm_df['_dist'] = abs(closest_atm_df['strike'] - price)
                    valid_chain = closest_atm_df.nsmallest(3, '_dist')
                    valid_ivs = valid_chain['impliedVolatility'].dropna()
                    valid_ivs = valid_ivs[valid_ivs > 0.01]
                    current_iv = float(valid_ivs.mean()) if not valid_ivs.empty else hv_20
                    if np.isnan(current_iv) or current_iv <= 0:
                        current_iv = hv_20
                        
                    for _, row in calls.iterrows():
                        strike = row.get('strike')
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
                        # 流動性適度過濾，排除極端不流動合約
                        if vol < 5 and oi < 10:
                            continue
                        if spread_pct > 0.35:
                            continue
                            
                        sigma_val = mkt_iv if mkt_iv > 0.01 else current_iv
                        if np.isnan(sigma_val) or sigma_val <= 0:
                            sigma_val = current_iv
                            
                        # 計算 Greeks
                        g = bs.call_greeks(price, strike, T_years, r_rate, sigma_val)
                        if g.delta < 0.20 or g.delta > 0.85:
                            continue
                            
                        premium = ask
                        breakeven = strike + premium
                        
                        # 模擬價格上漲 10% 的報酬率
                        target_price = price * 1.10
                        profit_at_target = max(target_price - strike, 0) - premium
                        rr_ratio = profit_at_target / premium if premium > 0 else 0
                        
                        # 評分系統
                        score = 0
                        # 1. 盈虧比 (0~25)
                        score += min(25, rr_ratio * 8)
                        # 2. Delta 甜蜜點 (0~15) -> 0.40~0.60 最佳
                        score += max(0, 15 - abs(g.delta - 0.50) * 40)
                        # 3. DTE 甜蜜點 (0~15) -> 25~60 天最佳
                        if 25 <= dte <= 60:
                            score += 15
                        elif 14 <= dte <= 90:
                            score += 10
                        elif 7 <= dte <= 120:
                            score += 5
                        # 4. Spread 流動性 (0~15)
                        score += max(0, 15 - spread_pct * 45)
                        # 5. Volume & OI (0~15)
                        score += min(15, (vol + oi) / 150)
                        # 6. Delta/Theta 效率 (0~15)
                        dt_ratio = abs(g.delta / g.theta) if g.theta != 0 else 0
                        score += min(15, dt_ratio * 3)
                        
                        all_calls.append({
                            'expiry': d,
                            'dte': dte,
                            'strike': float(strike),
                            'bid': float(bid),
                            'ask': float(ask),
                            'mid': float(mid),
                            'volume': int(vol),
                            'oi': int(oi),
                            'spread_pct': float(spread_pct),
                            'iv': float(sigma_val),
                            'delta': float(g.delta),
                            'theta': float(g.theta),
                            'breakeven': float(breakeven),
                            'breakeven_pct': float((breakeven / price - 1) * 100),
                            'rr_ratio': float(rr_ratio),
                            'score': float(score),
                        })
                
                if all_calls:
                    # 排序
                    all_calls.sort(key=lambda x: x['score'], reverse=True)
                    
                    # 找出短、中、長天期的最佳合約
                    short_best = None
                    mid_best = None
                    far_best = None
                    
                    for c in all_calls:
                        if c['dte'] <= 21 and short_best is None:
                            short_best = c
                        elif 25 <= c['dte'] <= 60 and mid_best is None:
                            mid_best = c
                        elif c['dte'] > 60 and far_best is None:
                            far_best = c
                            
                    best_overall = all_calls[0]
                    
                    # 生成損益模擬矩陣
                    price_changes = [-0.10, -0.05, 0.0, 0.05, 0.10, 0.20]
                    hold_days = [1, 7, max(1, best_overall['dte'] // 2), best_overall['dte']]
                    hold_days = sorted(list(set(hold_days)))
                    
                    pnl_matrix = []
                    for pct in price_changes:
                        tgt_p = price * (1 + pct)
                        row_pnl = {
                            'change_pct': round(pct * 100, 1),
                            'target_price': round(tgt_p, 2),
                            'scenarios': []
                        }
                        for days in hold_days:
                            rem_T = max((best_overall['dte'] - days) / 365.0, 0.0001)
                            if rem_T > 0.0001:
                                value = bs.call_price(tgt_p, best_overall['strike'], rem_T, r_rate, best_overall['iv'])
                            else:
                                value = max(tgt_p - best_overall['strike'], 0)
                                
                            pnl_share = value - best_overall['ask']
                            pnl_pct = (pnl_share / best_overall['ask']) * 100 if best_overall['ask'] > 0 else 0
                            
                            row_pnl['scenarios'].append({
                                'days_held': int(days),
                                'pnl_per_share': round(float(pnl_share), 2),
                                'pnl_pct': round(float(pnl_pct), 1),
                                'pnl_contract_usd': round(float(pnl_share) * 100, 1)
                            })
                        pnl_matrix.append(row_pnl)
                        
                    options_analysis = {
                        'best_overall': best_overall,
                        'short_best': short_best or best_overall,
                        'mid_best': mid_best or best_overall,
                        'far_best': far_best or best_overall,
                        'pnl_matrix': pnl_matrix,
                        'hold_days': hold_days,
                    }
        except Exception as e:
            print(f"  ⚠️ Options Analysis 計算失敗: {e}")
            
        data['options_analysis'] = options_analysis

        # 季度趨勢
        data['quarters'] = get_quarterly_trends(stock)

        return data

    except Exception as e:
        print(f"  ⚠️ {ticker} 失敗: {e}")
        return None


# ============================================================
# 主函式
# ============================================================
def generate_all_fundamentals():
    """下載所有股票基本面數據並存成 JSON"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] "
          f"💼 正在下載 {len(AI_TECH_STOCKS)} 檔股票基本面數據...")
    print("  (每檔約需 1-3 秒，預計 2-4 分鐘)\n")

    results = {}
    ok = 0

    for i, ticker in enumerate(AI_TECH_STOCKS):
        pct = int((i + 1) / len(AI_TECH_STOCKS) * 100)
        print(f"  [{i+1:2d}/{len(AI_TECH_STOCKS)}] {ticker:<6} ", end='',
              flush=True)
        data = fetch_fundamentals(ticker)
        if data:
            results[ticker] = data
            ok += 1
            rev = data.get('rev_fmt', '--')
            pe = data.get('pe_fwd') or data.get('pe_ttm') or '--'
            print(f"✅  Rev={rev}  P/E={pe}  [{pct}%]")
        else:
            print(f"❌  [{pct}%]")

    # 調用 valuation_engine 來計算專業估值並注入各股資料中
    try:
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] ⚖️ 正在執行專業多因子估值矩陣計算...")
        from valuation_engine import enrich_watchlist_valuations
        results = enrich_watchlist_valuations(results)
        print("  ✅ 估值矩陣計算完成！")
    except Exception as ve_err:
        print(f"  ⚠️ 估值矩陣計算失敗: {ve_err}")

    output = {
        'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'total': len(results),
        'stocks': results,
    }

    os.makedirs(DASHBOARD_DIR, exist_ok=True)
    fpath = os.path.join(DASHBOARD_DIR, 'fundamentals_data.json')
    with open(fpath, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False)

    print(f"\n  ✅ 基本面數據完成！"
          f"{ok}/{len(AI_TECH_STOCKS)} 檔，已寫入 fundamentals_data.json\n")
    return output


if __name__ == '__main__':
    generate_all_fundamentals()
