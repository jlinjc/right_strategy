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
