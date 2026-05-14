"""
generate_institutional.py - 籌碼面分析引擎
============================================
下載所有監控股票的籌碼面數據：

  1. 內部人交易 (Insider Transactions) - 近 6 個月高管/董事買賣紀錄
  2. 持股結構 (Ownership Breakdown) - 機構/內部人/散戶持股比例
  3. 前 10 大機構持股人 (Top Institutional Holders)
  4. 空頭指標 (Short Interest) - 放空比例, 軋空天數
  5. 流通股結構 (Float Analysis) - 流通股數, 被鎖定比例
"""

import os
import sys

if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

import json
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from scanner_base import AI_TECH_STOCKS, DASHBOARD_DIR


def safe_val(v, default=None):
    if v is None:
        return default
    if isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
        return default
    return v


def fmt_num(num):
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
        return f"{num/1e3:.0f}K"
    return f"{num:,.0f}"


def fmt_money(num):
    """格式化金額"""
    if num is None:
        return None
    num = float(num)
    if abs(num) >= 1e9:
        return f"${num/1e9:.2f}B"
    elif abs(num) >= 1e6:
        return f"${num/1e6:.1f}M"
    elif abs(num) >= 1e3:
        return f"${num/1e3:.0f}K"
    return f"${num:,.0f}"


# ============================================================
# 內部人交易
# ============================================================
def get_insider_trades(stock, ticker):
    """取得近期內部人交易紀錄"""
    trades = []
    buy_total = 0
    sell_total = 0
    buy_count = 0
    sell_count = 0

    try:
        insider_df = stock.insider_transactions
        if insider_df is not None and not insider_df.empty:
            for _, row in insider_df.iterrows():
                trade = {}

                # 日期
                start_date = row.get('Start Date') or row.get('startDate')
                if start_date is not None:
                    if isinstance(start_date, pd.Timestamp):
                        trade['date'] = start_date.strftime('%Y-%m-%d')
                    else:
                        trade['date'] = str(start_date)[:10]
                else:
                    trade['date'] = '--'

                # 交易人
                trade['insider'] = str(row.get('Insider Trading', row.get('insider', row.get('Text', '--'))))

                # 職位
                trade['position'] = str(row.get('Position', row.get('position', '')))

                # 交易類型與金額
                trans_text = str(row.get('Transaction', row.get('transaction', row.get('Text', ''))))
                shares = row.get('Shares', row.get('shares', 0))
                value = row.get('Value', row.get('value', 0))

                if shares is None or (isinstance(shares, float) and np.isnan(shares)):
                    shares = 0
                if value is None or (isinstance(value, float) and np.isnan(value)):
                    value = 0

                shares = abs(int(shares))
                value = abs(float(value))

                # 判斷買賣 - 檢查所有可能包含交易類型的欄位
                all_text = (trans_text + ' ' + trade['insider']).lower()
                if 'purchase' in all_text or 'buy' in all_text:
                    trade['action'] = 'buy'
                    buy_total += value
                    buy_count += 1
                elif 'sale' in all_text or 'sell' in all_text:
                    trade['action'] = 'sell'
                    sell_total += value
                    sell_count += 1
                elif 'award' in all_text or 'grant' in all_text:
                    trade['action'] = 'grant'
                elif 'exercise' in all_text or 'option' in all_text:
                    trade['action'] = 'exercise'
                elif 'gift' in all_text:
                    trade['action'] = 'gift'
                else:
                    trade['action'] = 'other'

                trade['shares'] = shares
                trade['shares_fmt'] = fmt_num(shares)
                trade['value'] = value
                trade['value_fmt'] = fmt_money(value) if value > 0 else '--'
                trade['desc'] = trans_text[:60]

                trades.append(trade)

    except Exception as e:
        pass

    # 只保留最近 20 筆
    trades = trades[:20]

    return {
        'trades': trades,
        'buy_count': buy_count,
        'sell_count': sell_count,
        'buy_total': buy_total,
        'buy_total_fmt': fmt_money(buy_total),
        'sell_total': sell_total,
        'sell_total_fmt': fmt_money(sell_total),
        'net_sentiment': 'bullish' if buy_total > sell_total else 'bearish' if sell_total > buy_total else 'neutral',
    }


# ============================================================
# 持股結構
# ============================================================
def get_ownership(stock, info):
    """取得持股結構 (機構/內部人/散戶)"""
    ownership = {
        'institutional_pct': None,
        'insider_pct': None,
        'retail_pct': None,
        'institutional_count': None,
    }

    try:
        # 從 major_holders 取得
        major = stock.major_holders
        if major is not None and not major.empty:
            for _, row in major.iterrows():
                label = str(row.iloc[-1]).lower() if len(row) > 1 else ''
                val_str = str(row.iloc[0]).replace('%', '').strip()
                try:
                    val = float(val_str)
                except (ValueError, TypeError):
                    continue

                if 'insider' in label:
                    ownership['insider_pct'] = round(val, 1)
                elif 'institution' in label and 'float' not in label:
                    ownership['institutional_pct'] = round(val, 1)
                elif 'institution' in label and 'float' in label:
                    ownership['inst_float_pct'] = round(val, 1)
                elif 'number' in label and 'institution' in label:
                    ownership['institutional_count'] = int(val)

        # 計算散戶持股
        inst = ownership.get('institutional_pct') or 0
        ins = ownership.get('insider_pct') or 0
        if inst > 0 or ins > 0:
            retail = max(0, 100 - inst - ins)
            ownership['retail_pct'] = round(retail, 1)

    except Exception:
        pass

    # 從 info 補充
    if ownership['institutional_pct'] is None:
        held = safe_val(info.get('heldPercentInstitutions'))
        if held:
            ownership['institutional_pct'] = round(held * 100, 1)
    if ownership['insider_pct'] is None:
        held = safe_val(info.get('heldPercentInsiders'))
        if held:
            ownership['insider_pct'] = round(held * 100, 1)

    # 重新計算散戶
    inst = ownership.get('institutional_pct') or 0
    ins = ownership.get('insider_pct') or 0
    if inst > 0 or ins > 0:
        ownership['retail_pct'] = round(max(0, 100 - inst - ins), 1)

    return ownership


# ============================================================
# 前十大機構持股人
# ============================================================
def get_top_institutions(stock):
    """取得前 10 大機構持股人"""
    holders = []

    try:
        inst_df = stock.institutional_holders
        if inst_df is not None and not inst_df.empty:
            for _, row in inst_df.head(10).iterrows():
                holder = {}
                holder['name'] = str(row.get('Holder', row.get('holder', '--')))

                shares = row.get('Shares', row.get('shares', 0))
                if shares is not None and not (isinstance(shares, float) and np.isnan(shares)):
                    holder['shares'] = int(shares)
                    holder['shares_fmt'] = fmt_num(int(shares))
                else:
                    holder['shares'] = 0
                    holder['shares_fmt'] = '--'

                value = row.get('Value', row.get('value', 0))
                if value is not None and not (isinstance(value, float) and np.isnan(value)):
                    holder['value'] = float(value)
                    holder['value_fmt'] = fmt_money(float(value))
                else:
                    holder['value'] = 0
                    holder['value_fmt'] = '--'

                pct = row.get('% Out', row.get('pctHeld', row.get('Percentage', None)))
                if pct is not None and not (isinstance(pct, float) and np.isnan(pct)):
                    holder['pct'] = round(float(pct) * 100, 2) if float(pct) < 1 else round(float(pct), 2)
                else:
                    holder['pct'] = None

                date_reported = row.get('Date Reported', row.get('dateReported', None))
                if date_reported is not None:
                    if isinstance(date_reported, pd.Timestamp):
                        holder['date'] = date_reported.strftime('%Y-%m-%d')
                    else:
                        holder['date'] = str(date_reported)[:10]

                holders.append(holder)

    except Exception:
        pass

    return holders


# ============================================================
# 空頭指標
# ============================================================
def get_short_interest(info):
    """取得空頭部位指標"""
    short_data = {}

    shares_short = safe_val(info.get('sharesShort'))
    shares_float = safe_val(info.get('floatShares'))
    short_ratio = safe_val(info.get('shortRatio'))
    short_pct = safe_val(info.get('shortPercentOfFloat'))

    short_data['shares_short'] = shares_short
    short_data['shares_short_fmt'] = fmt_num(shares_short)
    short_data['short_ratio'] = round(short_ratio, 1) if short_ratio else None
    short_data['short_pct'] = round(short_pct * 100, 1) if short_pct and short_pct < 1 else (round(short_pct, 1) if short_pct else None)

    # 軋空風險等級
    pct = short_data.get('short_pct') or 0
    ratio = short_data.get('short_ratio') or 0
    if pct > 20 or ratio > 10:
        short_data['squeeze_risk'] = 'high'
    elif pct > 10 or ratio > 5:
        short_data['squeeze_risk'] = 'medium'
    else:
        short_data['squeeze_risk'] = 'low'

    # 流通股數據
    short_data['float_shares'] = shares_float
    short_data['float_shares_fmt'] = fmt_num(shares_float)
    short_data['shares_outstanding'] = safe_val(info.get('sharesOutstanding'))
    short_data['shares_outstanding_fmt'] = fmt_num(safe_val(info.get('sharesOutstanding')))

    return short_data


# ============================================================
# 主函式
# ============================================================
def fetch_institutional_data(ticker):
    """取得單一股票的籌碼面數據"""
    try:
        stock = yf.Ticker(ticker)
        info = stock.info

        if not info or 'symbol' not in info:
            return None

        data = {
            'ticker': ticker,
            'name': safe_val(info.get('shortName'), ticker),
        }

        # 1. 內部人交易
        data['insider'] = get_insider_trades(stock, ticker)

        # 2. 持股結構
        data['ownership'] = get_ownership(stock, info)

        # 3. 前十大機構
        data['top_institutions'] = get_top_institutions(stock)

        # 4. 空頭指標
        data['short'] = get_short_interest(info)

        return data

    except Exception as e:
        print(f"  ⚠️ {ticker} 失敗: {e}")
        return None


def generate_all_institutional():
    """下載所有股票的籌碼面數據"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] "
          f"🏦 正在下載 {len(AI_TECH_STOCKS)} 檔股票籌碼面數據...")
    print("  (每檔約需 2-4 秒，預計 3-5 分鐘)\n")

    results = {}
    ok = 0

    for i, ticker in enumerate(AI_TECH_STOCKS):
        pct = int((i + 1) / len(AI_TECH_STOCKS) * 100)
        print(f"  [{i+1:2d}/{len(AI_TECH_STOCKS)}] {ticker:<6} ", end='',
              flush=True)
        data = fetch_institutional_data(ticker)
        if data:
            results[ticker] = data
            ok += 1
            ins = data['insider']
            own = data['ownership']
            inst_pct = own.get('institutional_pct') or '--'
            print(f"✅  機構:{inst_pct}%  "
                  f"內部人買:{ins['buy_count']} 賣:{ins['sell_count']}  [{pct}%]")
        else:
            print(f"❌  [{pct}%]")

    output = {
        'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'total': len(results),
        'stocks': results,
    }

    os.makedirs(DASHBOARD_DIR, exist_ok=True)
    fpath = os.path.join(DASHBOARD_DIR, 'institutional_data.json')
    with open(fpath, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False)

    print(f"\n  ✅ 籌碼面數據完成！"
          f"{ok}/{len(AI_TECH_STOCKS)} 檔，已寫入 institutional_data.json\n")
    return output


if __name__ == '__main__':
    generate_all_institutional()
