"""
earnings_filter.py - 財報盲區 + 財報後漂移(PEAD) 進場過濾
=================================================================
動能拉回系統提升「樣本純度」的單一最大槓桿：

  (a) 財報盲區 (blackout) —— 拉回訊號若落在「下一次財報前 N 天內」直接擋掉。
      財報跳空的方差遠大於停損能控制的範圍，停損在此形同虛設。這類交易是
      擲骰子，不是 edge。踢掉它們 → 命中率上升，且完全不碰任何贏家樣本。

  (b) 財報後漂移 (PEAD) 偏好 —— 「強勢股 + 財報大超預期 + 隨後量縮拉回」是
      整套系統品質最高的進場型態（疊了橫斷面動能 + 盈餘漂移兩個獨立 edge）。
      2025 文獻重新確認 PEAD 仍有效：beat 後股價傾向續漂 30-60 天。
      這裡不當硬閘門（不想漏掉非 PEAD 的好單），而是當「品質標記 + 排序加分」。

⚠️ 無 look-ahead：財報日期是事先公告、point-in-time 可知；財報意外(surprise)
   是公告「之後」才知道。兩者都不會污染回測。本模組抓的是當下快照，給即時
   信號用；若要乾淨回測需逐日重建排程（earnings_dates 本身含歷史，可支援）。
"""

import os
import sys
import json
import warnings
from datetime import date, datetime

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

import pandas as pd
import yfinance as yf

from scanner_base import DASHBOARD_DIR

CACHE = os.path.join(DASHBOARD_DIR, 'earnings_dates.json')

# ── 預設參數 ────────────────────────────────────────────────
BLACKOUT_DAYS = 5      # 下一次財報前 N 天(含)內，禁止新進場
PEAD_WINDOW = 45       # 財報後 N 天內仍視為「漂移窗口」
PEAD_MIN_SURPRISE = 5.0  # 視為「有意義 beat」的最低 EPS 意外百分比


def _to_date(ts):
    """把 yfinance 的 tz-aware Timestamp 安全轉成 date"""
    try:
        if isinstance(ts, (datetime, pd.Timestamp)):
            return ts.date()
        return pd.to_datetime(ts).date()
    except Exception:
        return None


def fetch_earnings(tickers, use_cache=True) -> dict:
    """
    回傳 {ticker: {'next': 'YYYY-MM-DD'|None,
                   'last': 'YYYY-MM-DD'|None,
                   'last_surprise_pct': float|None}}
    next  = 最近一次「尚未公布」的排定財報日
    last  = 最近一次「已公布」的財報日 + 當次 EPS 意外百分比
    """
    if use_cache and os.path.exists(CACHE):
        try:
            with open(CACHE, encoding='utf-8') as f:
                data = json.load(f)
            if set(tickers).issubset(set(data.keys())):
                return data
        except Exception:
            pass

    print(f"  抓取 {len(tickers)} 檔財報排程 (yfinance earnings_dates，較慢)...")
    today = date.today()
    out = {}
    for i, tk in enumerate(tickers):
        rec = {'next': None, 'last': None, 'last_surprise_pct': None}
        tkr = yf.Ticker(tk)
        try:
            ed = tkr.get_earnings_dates(limit=16)
            if ed is not None and not ed.empty:
                # 找欄位：Reported EPS / Surprise(%) 命名可能略有差異
                cols = {c.lower(): c for c in ed.columns}
                rep_col = next((cols[c] for c in cols if 'reported' in c), None)
                sur_col = next((cols[c] for c in cols if 'surprise' in c), None)

                future_dates, past = [], []
                for ts, row in ed.iterrows():
                    d = _to_date(ts)
                    if d is None:
                        continue
                    reported = row[rep_col] if rep_col else None
                    is_reported = reported is not None and not pd.isna(reported)
                    if d > today and not is_reported:
                        future_dates.append(d)
                    elif d <= today:
                        sur = None
                        if sur_col is not None and not pd.isna(row[sur_col]):
                            sur = round(float(row[sur_col]), 1)
                        past.append((d, sur))

                if future_dates:
                    rec['next'] = min(future_dates).strftime('%Y-%m-%d')
                if past:
                    d, sur = max(past, key=lambda x: x[0])
                    rec['last'] = d.strftime('%Y-%m-%d')
                    rec['last_surprise_pct'] = sur
        except Exception:
            pass

        # 後備：get_earnings_dates 失敗或無未來日時，用 .calendar 補「下次財報日」
        # （盲區是最關鍵的硬閘門，務必要有；PEAD 缺了頂多少個加分標記）
        if rec['next'] is None:
            try:
                cal = tkr.calendar or {}
                ed_cal = cal.get('Earnings Date')
                if ed_cal:
                    cand = ed_cal[0] if isinstance(ed_cal, (list, tuple)) else ed_cal
                    d = _to_date(cand)
                    if d and d > today:
                        rec['next'] = d.strftime('%Y-%m-%d')
            except Exception:
                pass

        out[tk] = rec
        if (i + 1) % 20 == 0:
            print(f"    ...{i+1}/{len(tickers)}")

    os.makedirs(DASHBOARD_DIR, exist_ok=True)
    with open(CACHE, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"  💾 {CACHE}")
    return out


def earnings_context(earnings: dict, tk: str, asof: date) -> dict:
    """
    針對某檔股票、某個基準日(asof)，算出可執行/可顯示的財報情境：
      days_to_earnings    : 距下次財報天數 (None=未知)
      in_blackout         : 是否落在財報前盲區
      days_since_earnings : 距上次財報天數 (None=未知)
      last_surprise_pct   : 上次 EPS 意外百分比
      pead_active         : 是否處於「財報大超預期後的漂移窗口」(品質加分)
    """
    rec = earnings.get(tk) or {}
    ctx = {
        'days_to_earnings': None, 'in_blackout': False,
        'days_since_earnings': None, 'last_surprise_pct': rec.get('last_surprise_pct'),
        'pead_active': False,
    }

    nxt = rec.get('next')
    if nxt:
        try:
            dte = (datetime.strptime(nxt, '%Y-%m-%d').date() - asof).days
            ctx['days_to_earnings'] = dte
            ctx['in_blackout'] = (0 <= dte <= BLACKOUT_DAYS)
        except Exception:
            pass

    lst = rec.get('last')
    if lst:
        try:
            dse = (asof - datetime.strptime(lst, '%Y-%m-%d').date()).days
            ctx['days_since_earnings'] = dse
            sur = rec.get('last_surprise_pct')
            if (0 <= dse <= PEAD_WINDOW and sur is not None
                    and sur >= PEAD_MIN_SURPRISE):
                ctx['pead_active'] = True
        except Exception:
            pass

    return ctx


if __name__ == '__main__':
    # 自測：抓幾檔看排程
    test = ['NVDA', 'AVGO', 'PLTR', 'LLY']
    e = fetch_earnings(test, use_cache=False)
    today = date.today()
    for tk in test:
        c = earnings_context(e, tk, today)
        print(f"{tk:6} next={e[tk]['next']} last={e[tk]['last']} "
              f"surprise={e[tk]['last_surprise_pct']} → "
              f"blackout={c['in_blackout']} pead={c['pead_active']} "
              f"(D-{c['days_to_earnings']})")
