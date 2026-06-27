"""
strategy_menu.py - 策略選單：統整所有回測 + 波動率目標復活槓桿 → 各有優勢的勝者
========================================================================
把整段研究的核心策略放同一把尺(2021-2026 含2022、誠實)比較，並對「被打敗的
槓桿」套上波動率目標(讓槓桿只在平靜期用)復活重測。輸出分類勝者選單給儀表板，
讓你/投資人自己依「要報酬還是要穩」選 —— 不急著只選一組。

分類：🛡️低風險(高Sharpe/低回撤) / ⚖️平衡 / 🚀高報酬 / 💎最佳風險報酬
輸出 Web_Dashboard/strategy_menu.json。

用法: python strategy_menu.py
"""

import os, sys, json, warnings
from datetime import datetime
import numpy as np
import pandas as pd
import yfinance as yf
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

from scanner_base import DASHBOARD_DIR

START = '2020-01-01'
CMP = pd.Timestamp('2021-06-01')
TICKERS = ['SMH', 'QQQ', 'SOXX', 'SOXL', 'TQQQ', 'SPY']


def dl(tickers):
    raw = yf.download(' '.join(tickers), start=START, interval='1d', progress=False, group_by='ticker')
    out = {}
    for tk in tickers:
        try:
            out[tk] = raw[tk]['Close'].dropna()
        except Exception:
            pass
    return out


def metrics(eq):
    x = eq[eq.index >= CMP].dropna()
    rets = x.pct_change().dropna()
    total = x.iloc[-1]/x.iloc[0]-1
    cagr = (x.iloc[-1]/x.iloc[0])**(252/len(x))-1
    mdd = (x/x.cummax()-1).min()
    sharpe = rets.mean()/rets.std()*np.sqrt(252) if rets.std() > 0 else 0
    y = x[(x.index >= '2022-01-01') & (x.index <= '2022-12-31')]
    y22 = (y.iloc[-1]/y.iloc[0]-1)*100 if len(y) > 20 else None
    return {'total': round(total*100, 1), 'cagr': round(cagr*100, 1),
            'sharpe': round(sharpe, 2), 'mdd': round(mdd*100, 1),
            'y22': round(y22, 1) if y22 is not None else None}


def buyhold(c): return (1+c.pct_change().fillna(0)).cumprod()
def timed(c, ma=200):
    sig = (c > c.rolling(ma).mean()).shift(1).fillna(False).astype(float)
    return (1+c.pct_change().fillna(0)*sig).cumprod()
def voltarget(c, tgt, lb, cap, ma=200):
    sig = (c > c.rolling(ma).mean()).astype(float)
    rv = c.pct_change().rolling(lb).std()*np.sqrt(252)
    pos = (sig*(tgt/rv).clip(0, cap)).shift(1).fillna(0)
    return (1+c.pct_change().fillna(0)*pos).cumprod()
def voltarget_lev(trend, hold, tgt, lb, cap, ma=200):
    sig = (trend > trend.rolling(ma).mean()).astype(float).reindex(hold.index).fillna(0)
    rv = hold.pct_change().rolling(lb).std()*np.sqrt(252)
    pos = (sig*(tgt/rv).clip(0, cap)).shift(1).fillna(0)
    return (1+hold.pct_change().fillna(0)*pos).cumprod()


def main():
    print(f"\n{'='*78}\n  策略選單：統整 + 波動率目標復活槓桿 (2021-2026含2022)\n{'='*78}")
    d = dl(TICKERS)

    rows = []  # (分類, 名稱, 一句話, metrics)
    rows.append(('參考', '買持 SMH', '不擇時、滿倉抱半導體', metrics(buyhold(d['SMH']))))
    rows.append(('參考', '買持 SPY', '不擇時、滿倉大盤', metrics(buyhold(d['SPY']))))
    rows.append(('平衡', 'timed-SMH@200MA', '站上抱、跌破現金(原冠軍核心)', metrics(timed(d['SMH']))))
    rows.append(('低風險', 'timed-QQQ@200MA', '較廣較穩的擇時核心', metrics(timed(d['QQQ']))))
    rows.append(('低風險', 'VolTgt-SMH 25% cap1.0', '波動高就大減倉,最穩', metrics(voltarget(d['SMH'], 0.25, 20, 1.0))))
    rows.append(('低風險', 'VolTgt-QQQ 20% cap1.0', '最低回撤的擇時核心', metrics(voltarget(d['QQQ'], 0.20, 20, 1.0))))
    rows.append(('平衡', 'VolTgt-SMH 30% cap1.0', '波動調整、不加槓桿', metrics(voltarget(d['SMH'], 0.30, 20, 1.0))))
    rows.append(('💎最佳', 'VolTgt-SMH 35% cap1.5', '平靜期上槓桿:報酬↑Sharpe↑回撤平', metrics(voltarget(d['SMH'], 0.35, 20, 1.5))))
    rows.append(('🚀高報酬', 'VolTgt-SMH 40% cap1.5', '更高曝險、報酬更衝', metrics(voltarget(d['SMH'], 0.40, 20, 1.5))))
    rows.append(('🚀高報酬', 'VolTgt-SMH 45% cap2.0', '激進、博最大財富', metrics(voltarget(d['SMH'], 0.45, 20, 2.0))))
    # 復活：被否決的槓桿，套上波動率目標 + SMH趨勢閘門
    if 'SOXL' in d:
        rows.append(('參考', '裸槓桿 SOXL(原否決)', '不控波動的3x,-72%回撤災難', metrics(timed(d['SOXL']))))
        rows.append(('🚀高報酬', 'VolTgt-SOXL 40% cap1.0', '3x半導體+波動控+SMH閘門(復活版)', metrics(voltarget_lev(d['SMH'], d['SOXL'], 0.40, 20, 1.0))))
    if 'TQQQ' in d:
        rows.append(('🚀高報酬', 'VolTgt-TQQQ 35% cap1.0', '3xQQQ+波動控(較穩槓桿)', metrics(voltarget_lev(d['QQQ'], d['TQQQ'], 0.35, 20, 1.0))))

    # 印表
    print(f"\n  {'分類':<8} {'策略':<24} {'總報酬':>8} {'CAGR':>7} {'Sharpe':>7} {'回撤':>8} {'2022':>7}")
    print(f"  {'-'*76}")
    for cat, name, _, m in sorted(rows, key=lambda r: -r[3]['sharpe']):
        print(f"  {cat:<8} {name:<24} {m['total']:>+7.0f}% {m['cagr']:>+6.0f}% {m['sharpe']:>7.2f} "
              f"{m['mdd']:>+7.0f}% {(str(m['y22'])+'%') if m['y22'] is not None else '—':>7}")

    # 各分類勝者
    def best(cat, key):
        cand = [r for r in rows if r[0] == cat]
        return max(cand, key=lambda r: r[3][key]) if cand else None
    print(f"\n  ── 各有優勢的勝者 ──")
    picks = {
        '🛡️ 低風險之王(最高Sharpe/低回撤)': best('低風險', 'sharpe'),
        '⚖️ 平衡之王': best('平衡', 'sharpe'),
        '🚀 高報酬之王': max([r for r in rows if r[0] == '🚀高報酬'], key=lambda r: r[3]['total']),
        '💎 最佳風險報酬': best('💎最佳', 'sharpe') or max(rows, key=lambda r: r[3]['sharpe']),
    }
    for label, r in picks.items():
        if r:
            m = r[3]
            print(f"  {label}: {r[1]} → {m['total']:+.0f}% / Sharpe {m['sharpe']} / 回撤 {m['mdd']:+.0f}%")

    # 寫 JSON 給儀表板
    menu = [{'category': cat, 'name': name, 'desc': desc, **m} for cat, name, desc, m in rows]
    out = {'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
           'period': f'{CMP.date()}~today (含2022熊市,誠實尺)',
           'note': '同一把尺對照;槓桿版已套波動率目標。報酬越高通常回撤越大,依風險偏好選。',
           'strategies': menu}
    os.makedirs(DASHBOARD_DIR, exist_ok=True)
    path = os.path.join(DASHBOARD_DIR, 'strategy_menu.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n💾 {path}")
    print(f"{'='*78}\n")


if __name__ == '__main__':
    main()
