"""
generate_signals.py - 定案策略即時信號產生器
================================================
用「完全相同的定案策略物件」掃描追蹤清單，輸出今日可執行的進場信號卡 +
大盤 regime 燈號 + 設定中觀察清單，寫入 Web_Dashboard/strategy_signals.json
給 strategy_dashboard.html 使用。

定案系統：
  進場 = MA拉回 + TTM動能>0 + 不追高(<1.08×10MA)
  出場 = 分批(進場+3×ATR賣50% + 剩餘吊燈3.5×ATR)，硬停損 = 進場時 ATR/均線停損
"""

import os
import sys
import json
import warnings
from datetime import datetime

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
import yfinance as yf

from scanner_base import AI_TECH_STOCKS, BENCHMARK, DASHBOARD_DIR
from filter_experiments import f_mom_positive, make_f_not_extended, _ttm_mom
from exit_experiments import _atr_at
from rs_selection import compute_rs_rank, RSRankScaledExit
from validate_universe import DIVERSE_UNIVERSE

# 定案策略參數（與回測完全相同）
SCALE_ATR_MULT = 3.0
SCALE_FRAC = 0.5
TRAIL_MULT = 3.5
ACCOUNT_SIZE = 100_000
RISK_PER_TRADE = 0.01
RS_THRESHOLD = 80.0   # 只交易 RS 排名前 20% 的領導股（系統化選股，洞#1 解法）

# 廣股池 = AI 50 ∪ 中性跨板塊 62，去重（讓 RS 排名從真正廣的池子選領導者）
BROAD_UNIVERSE = sorted(set(AI_TECH_STOCKS) | set(DIVERSE_UNIVERSE))


def compute_regime(bm: pd.DataFrame) -> dict:
    """大盤 QQQ regime 燈號 —— 人工看大盤手動控倉的紀律工具"""
    close = bm['Close']
    last = float(close.iloc[-1])
    ema21 = float(close.ewm(span=21, adjust=False).mean().iloc[-1])
    ma20 = float(close.iloc[-20:].mean())
    ma50 = float(close.iloc[-50:].mean())
    ma200 = float(close.iloc[-200:].mean()) if len(close) >= 200 else ma50

    if last >= ema21 and last >= ma50:
        light, label, action = ('green', '🟢 多頭 (QQQ > 21EMA 且 > 50MA)',
                                '可滿倉執行信號，正常控倉')
    elif last >= ma50:
        light, label, action = ('yellow', '🟡 震盪 (QQQ < 21EMA 但 > 50MA)',
                                '減半部位，只接最強信號')
    else:
        light, label, action = ('red', '🔴 修正 (QQQ < 50MA)',
                                '策略歷史上修正期失血 —— 停手或極輕倉觀望')
    return {
        'light': light, 'label': label, 'action': action,
        'qqq_close': round(last, 2), 'ema21': round(ema21, 2),
        'ma20': round(ma20, 2), 'ma50': round(ma50, 2), 'ma200': round(ma200, 2),
        'above_ema21': bool(last >= ema21), 'above_ma50': bool(last >= ma50),
    }


def build_card(tk: str, df: pd.DataFrame, sig, rs_pct=None) -> dict:
    """把一個進場訊號組成可執行的交易卡（含分批目標/停損/部位大小）"""
    idx = len(df) - 1
    entry = float(sig.entry_price)
    stop = float(sig.stop_loss)
    atr = _atr_at(df, idx)
    scale1_target = entry + SCALE_ATR_MULT * atr
    risk_per_share = max(entry - stop, 0.01)
    risk_pct = risk_per_share / entry * 100
    shares = int((ACCOUNT_SIZE * RISK_PER_TRADE) / risk_per_share)
    # 上限：單筆不超過帳戶 20%
    shares = min(shares, int(ACCOUNT_SIZE * 0.20 / entry))
    mom = _ttm_mom(idx, df)
    ma10 = float(df['Close'].iloc[-10:].mean())
    return {
        'ticker': tk,
        'entry': round(entry, 2),
        'stop': round(stop, 2),
        'risk_pct': round(risk_pct, 2),
        'scale1_target': round(scale1_target, 2),
        'scale1_gain_pct': round((scale1_target / entry - 1) * 100, 1),
        'scale_frac': int(SCALE_FRAC * 100),
        'trail_mult': TRAIL_MULT,
        'atr': round(atr, 2),
        'suggested_shares': shares,
        'suggested_dollars': round(shares * entry, 0),
        'momentum': round(mom, 2),
        'rs_pct': round(rs_pct, 0) if rs_pct is not None else None,
        'ext_vs_10ma_pct': round((entry / ma10 - 1) * 100, 1),
        'reason': sig.reason,
    }


def build_watch(tk: str, df: pd.DataFrame) -> dict:
    """非觸發股的設定狀態：離最近拉回均線多遠 + 動能/趨勢，看誰在醞釀"""
    idx = len(df) - 1
    close = float(df['Close'].iloc[-1])
    ma10 = float(df['Close'].iloc[-10:].mean())
    ma20 = float(df['Close'].iloc[-20:].mean())
    ma50 = float(df['Close'].iloc[-50:].mean())
    ma200 = float(df['Close'].iloc[-200:].mean()) if len(df) >= 200 else ma50
    mom = _ttm_mom(idx, df)
    # 離最近的下方拉回均線距離（正=均線在下方，越小越接近拉回）
    dists = []
    for w, v in (('10MA', ma10), ('20MA', ma20), ('60MA', float(df['Close'].iloc[-60:].mean()))):
        dists.append((w, (close / v - 1) * 100))
    nearest = min(dists, key=lambda x: abs(x[1]))
    return {
        'ticker': tk,
        'close': round(close, 2),
        'above_200ma': bool(close >= ma200),
        'momentum_pos': bool(mom > 0),
        'nearest_ma': nearest[0],
        'nearest_ma_dist_pct': round(nearest[1], 1),
        'trend_ok': bool(close >= ma200 and ma10 >= ma20),
    }


def main():
    tickers = [BENCHMARK] + BROAD_UNIVERSE
    print(f"📥 下載廣股池 {len(BROAD_UNIVERSE)} 檔 + {BENCHMARK} (2y)...")
    raw = yf.download(' '.join(tickers), period='2y', interval='1d',
                      progress=False, group_by='ticker')

    bm = raw[BENCHMARK].dropna(how='all').copy()
    if isinstance(bm.columns, pd.MultiIndex):
        bm.columns = bm.columns.get_level_values(0)

    regime = compute_regime(bm)

    # 收集個股 df + 算橫斷面 RS 排名
    stocks = {}
    for tk in BROAD_UNIVERSE:
        try:
            df = raw[tk].dropna(how='all').copy()
        except Exception:
            continue
        if len(df) >= 260:
            stocks[tk] = df
    rs_rank = compute_rs_rank(stocks)
    last_ts = bm.index[-1]
    rs_today = rs_rank.get(last_ts, {})

    # 定案策略：廣股池 + RS 前 20% 選股
    strategy = RSRankScaledExit(
        rs_rank=rs_rank, rs_threshold=RS_THRESHOLD,
        filters=[('mom+', f_mom_positive), ('not_ext', make_f_not_extended(1.08))],
        atr_target_mult=SCALE_ATR_MULT, scale_frac=SCALE_FRAC, trail_mult=TRAIL_MULT,
    )

    # 載入當下基本面（顯示用，非硬濾網 —— 回測證明硬篩無益）
    fund = {}
    fpath = os.path.join(DASHBOARD_DIR, 'broad_fundamentals.json')
    if os.path.exists(fpath):
        try:
            with open(fpath, encoding='utf-8') as f:
                fund = json.load(f)
        except Exception:
            fund = {}

    def fund_of(tk):
        d = fund.get(tk, {})
        if not d:
            return None
        def pct(x):
            return round(x * 100, 1) if isinstance(x, (int, float)) else None
        return {
            'net_margin': pct(d.get('profitMargins')),
            'roe': pct(d.get('returnOnEquity')),
            'rev_growth': pct(d.get('revenueGrowth')),
            'earn_growth': pct(d.get('earningsGrowth')),
            'fwd_pe': round(d['forwardPE'], 1) if isinstance(d.get('forwardPE'), (int, float)) else None,
        }

    cards, watch = [], []
    for tk, df in stocks.items():
        idx = len(df) - 1
        sig = strategy.scan(idx, tk, df, bm)
        if sig is not None:
            c = build_card(tk, df, sig, rs_pct=rs_today.get(tk))
            c['fundamentals'] = fund_of(tk)
            cards.append(c)
        else:
            # 醞釀中：RS 已在前20%、站上200MA、動能正、靠近拉回均線
            w = build_watch(tk, df)
            w['rs_pct'] = round(rs_today.get(tk), 0) if tk in rs_today else None
            if w['trend_ok'] and w['momentum_pos'] and (w['rs_pct'] or 0) >= RS_THRESHOLD:
                watch.append(w)

    cards.sort(key=lambda c: (-(c['rs_pct'] or 0), c['risk_pct']))  # RS 強的排前面
    watch.sort(key=lambda w: abs(w['nearest_ma_dist_pct']))

    out = {
        'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'data_date': last_ts.strftime('%Y-%m-%d') if hasattr(last_ts, 'strftime') else str(last_ts),
        'regime': regime,
        'config': {
            'universe': f'廣股池 {len(stocks)} 檔，RS 排名前 {int(100-RS_THRESHOLD)}% 才入選',
            'entry': 'RS前20% + MA拉回 + TTM動能>0 + 不追高(<1.08×10MA)',
            'exit': f'進場+{SCALE_ATR_MULT}×ATR 賣{int(SCALE_FRAC*100)}% + 剩餘吊燈{TRAIL_MULT}×ATR',
            'account_size': ACCOUNT_SIZE, 'risk_per_trade_pct': RISK_PER_TRADE * 100,
        },
        'signals': cards,
        'watchlist': watch,
        'n_signals': len(cards),
        'n_watch': len(watch),
    }
    os.makedirs(DASHBOARD_DIR, exist_ok=True)
    path = os.path.join(DASHBOARD_DIR, 'strategy_signals.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"✅ {regime['label']}")
    print(f"✅ {len(cards)} 個進場信號 / {len(watch)} 個醞釀中 (廣股池 {len(stocks)} 檔)")
    for c in cards:
        print(f"   {c['ticker']:6} RS{c['rs_pct']:.0f} 進${c['entry']:.2f} 停${c['stop']:.2f} "
              f"(風險{c['risk_pct']:.1f}%) → 分批${c['scale1_target']:.2f}")
    print(f"💾 {path}")


if __name__ == '__main__':
    main()
