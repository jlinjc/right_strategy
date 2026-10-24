"""paper_forward.py - 前瞻追蹤(真正的樣本外):新版 vs 原版,從研究凍結日之後才開始算
========================================================================
2002~2026-09 的資料在研究時已經全部看過 → 唯一乾淨的檢驗是「凍結之後的新資料」。
規則是確定性的,所以不需要每天記錄:每次都用完整歷史算出訊號,但只計算 FREEZE 之後的報酬。

模擬的是「持有層」(用 research_rot_engine,與驗證時同一套引擎):
  原版 美股:單押 RS 最強(30日鎖)× RiskTarget × 信用哨(HYG+LQD 平滑)
  新版 美股:5 檔等權各自擇時 × RiskTarget × 信用哨(HYG+LQD 平滑)
  原版 台股:單押 RS 最強(含 00757)× RiskTarget × 信用哨(HYG+LQD+SOXX 平滑)
  新版 台股:4 檔等權各自擇時 × 固定1倍 × 信用哨(HYG+LQD+SOXX 平滑)
  對照:SPY / 0050 買進持有
簡化(誠實標註):黃燈的「等拉回」進場時機、V底救援、R3 沒有模擬 → 這是持有層的比較。
輸出 Web_Dashboard/paper_forward.json。用法: python paper_forward.py
"""
import os, sys, json, warnings
from datetime import datetime
warnings.filterwarnings('ignore')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd, yfinance as yf

from scanner_base import DASHBOARD_DIR, dump_json
import research_rot_engine as R

FREEZE = '2026-09-24'          # 研究凍結日:這天之後的資料研究時沒看過
US = ['SMH', 'SOXX', 'QQQ', 'XLK', 'SPY']
TW_OLD = ['0050.TW', '0052.TW', '006208.TW', '0051.TW', '00757.TW']
TW_NEW = ['0050.TW', '0052.TW', '006208.TW', '0051.TW']
NEED = sorted(set(US + TW_OLD + ['HYG', 'LQD', '^IRX', '^VIX']))


def load():
    raw = yf.download(' '.join(NEED), period='5y', interval='1d', auto_adjust=True,
                      progress=False, group_by='ticker')
    d = {}
    for tk in NEED:
        try:
            df = raw[tk].dropna(subset=['Close'])
            if len(df): d[tk] = df
        except Exception:
            pass
    R._DATA = d                 # 讓引擎用這份新下載的資料(不依賴本機快取)
    return d


def health(idx, tks):
    d = R.data(); oks = []
    for tk in tks:
        s = d[tk]['Close'].dropna(); ma = s.rolling(200).mean()
        ok = (s >= ma).astype(float); ok[ma.isna()] = np.nan
        oks.append(ok.reindex(idx).ffill())
    return pd.concat(oks, axis=1).mean(axis=1).fillna(1.0).values


def scaled(plan, h):
    return R.Plan(S=plan.S, E=plan.E * h[:, None], reb=plan.reb)


def series(pool, tickers, kind, sizing, credit_tks):
    P = R.build_panel(pool, tickers=tickers, tw_rf=0.0)
    if kind == 'rot':
        pl = R.rotation_plan(P, elig='above', N=1, lock=21, switch='lock', sizing=sizing)
    else:
        pl = R.ew_plan(P, reb=R.reb_days(P.idx, 0), sizing=sizing)
    r = R.run_plan(P, scaled(pl, health(P.idx, credit_tks)))
    expo = pd.Series(r.W.sum(1), index=P.idx)
    return r.r, expo


def summarize(r, expo=None):
    s = r[r.index > pd.Timestamp(FREEZE)]
    if len(s) == 0:
        return None
    eq = (1 + s).cumprod()
    return {'days': int(len(s)), 'ret_pct': round((eq.iloc[-1] - 1) * 100, 2),
            'mdd_pct': round(((eq / eq.cummax()) - 1).min() * 100, 2),
            'expo_now': round(float(expo.iloc[-1]), 2) if expo is not None else 1.0,
            'curve': [[d.strftime('%Y-%m-%d'), round((v - 1) * 100, 2)] for d, v in eq.items()]}


def main():
    d = load()
    out = {'generated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'freeze': FREEZE,
           'note': ('研究凍結日之後才開始算的「真正樣本外」成績。模擬持有層(與驗證同一引擎);'
                    '黃燈進場時機、V底救援、R3 未模擬。天數少時差距多半是雜訊,至少累積 12 個月再下結論。'),
           'markets': {}}
    specs = {
        'us': [('原版:單押最強', 'US5', US, 'rot', 'rt', ['HYG', 'LQD']),
               ('新版:等權', 'US5', US, 'ew', 'rt', ['HYG', 'LQD'])],
        'tw': [('原版:單押最強(含00757)', 'TW5', TW_OLD, 'rot', 'rt', ['HYG', 'LQD', 'SOXX']),
               ('新版:4檔等權', 'TW4', TW_NEW, 'ew', 'binary', ['HYG', 'LQD', 'SOXX'])],
    }
    for mk, lst in specs.items():
        rows = []
        for name, pool, tks, kind, sz, ct in lst:
            r, e = series(pool, tks, kind, sz, ct)
            sm = summarize(r, e)
            if sm: rows.append({'name': name, **sm})
        bh = 'SPY' if mk == 'us' else '0050.TW'
        s = d[bh]['Close'].dropna()
        if mk == 'tw':
            s = R.D.tw_clean(s)
        sm = summarize(s.pct_change().fillna(0))
        if sm: rows.append({'name': f'對照:買進持有 {bh.replace(".TW", "")}', **sm})
        out['markets'][mk] = rows
    path = os.path.join(DASHBOARD_DIR, 'paper_forward.json')
    dump_json(path, out)
    for mk, rows in out['markets'].items():
        print(f'[{mk}] 凍結日 {FREEZE} 之後:')
        for x in rows:
            print(f"   {x['name']:22s} {x['days']:3d}天  報酬 {x['ret_pct']:+6.2f}%  最大回撤 {x['mdd_pct']:6.2f}%  目前曝險 {x['expo_now']}")
    print(f'💾 {path}')


if __name__ == '__main__':
    main()
