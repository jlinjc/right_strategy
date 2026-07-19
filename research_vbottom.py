"""
research_vbottom.py — V底吃不到問題:三個「沒測過」的救援進場訊號(Jason 2026-07-19)
========================================================================
已證偽三次:D1(收復200MA但信用壞就回補=2022逐筆套死)、R1(加MA200上彎條件=2026也吃不到)、
線下接刀無條件版。這次測三個真正新的角度——關鍵是「能不能分開 V崩 與 2022式熊市反彈」:

  V1 VIX капitulation:近15日VIX尖峰≥40 且 現值≤尖峰×0.75(恐慌爆掉+正在退燒)。
     鑑別器假設:2022全年VIX最高~36從未破40;真V崩(2020/2025)都40+。含2008誠實測(VIX 80但底在5個月後)。
  V2 信用快解凍:HYG站回自己50MA(仍在200MA下)——比200MA信用哨早數週的「解凍」訊號。
  V3 廣度暴衝(Zweig thrust):S&P500成分站上20MA比例 從≤20% 十日內衝到≥55%(橫斷面新資料維度;
     資料2012起,誠實標注測不到2008/2011)。

救援倉規則(全部 PIT,shift1):只在「系統空手」且「距252日高點回撤>15%」(確在崩後語境)時啟用;
  進場=半倉(0.5);停損=進場價-7%;交還=正常系統進場條件成立時併回。
評估:①各訊號逐次觸發清單+事後21/63日 ②2022有沒有誤觸(陷阱測試) ③整合進BASE後全期
  Sharpe/CAGR/MDD+各崩盤窗(BASE vs BASE+V1/V2/V3/組合)。
"""
import sys, warnings
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd, yfinance as yf
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
import core_status as C

START = '2004-01-01'
TRANCHE = 0.5
RESCUE_STOP = 0.93           # 救援倉停損:進場價-7%
DD_GATE = 0.15               # 距252日高回撤>15%才准救援(崩後語境)
CRASH = {'2008-09': ('2008-09-01', '2009-12-31'), '2011': ('2011-07-01', '2012-06-30'),
         '2018Q4': ('2018-10-01', '2019-06-30'), '2020': ('2020-02-15', '2020-12-31'),
         '2022': ('2022-01-01', '2023-06-30'), '2025': ('2025-02-01', '2025-12-31'),
         '2026': ('2026-01-01', '2026-07-19')}


def dl(s):
    x = yf.download(s, start=START, auto_adjust=True, progress=False)['Close'].dropna()
    return x.iloc[:, 0] if isinstance(x, pd.DataFrame) else x


def perf(r, a='2006-01-01', b='2026-12-31'):
    r = r.loc[a:b].dropna()
    if len(r) < 60: return None
    sd = r.std(); eq = (1 + r).cumprod()
    return {'sharpe': r.mean() / sd * np.sqrt(252) if sd > 0 else np.nan,
            'cagr': (eq.iloc[-1] ** (252 / len(r)) - 1) * 100,
            'mdd': (eq / eq.cummax() - 1).min() * 100}


def breadth_pct20():
    """S&P500 成分站上自己20MA的比例(alpha cache,2012起)。"""
    try:
        close = pd.read_pickle('alpha/cache/prices_us.pkl')
        ma20 = close.rolling(20).mean()
        return ((close > ma20).sum(axis=1) / close.notna().sum(axis=1)).dropna()
    except Exception:
        return None


def build_signals(idx, vix, hyg, breadth):
    """回傳三個布林Series(對齊idx,PIT trailing)。"""
    v = vix.reindex(idx).ffill()
    vspike = v.rolling(15).max()
    s1 = (vspike >= 40) & (v <= vspike * 0.75)
    h = hyg.reindex(idx).ffill()
    h50 = h.rolling(50).mean(); h200 = h.rolling(200).mean()
    s2 = (h > h50) & (h < h200)
    if breadth is not None:
        b = breadth.reindex(idx).ffill()
        bmin10 = b.rolling(10).min()
        s3 = (b >= 0.55) & (bmin10 <= 0.20)
    else:
        s3 = pd.Series(False, index=idx)
    return s1.fillna(False), s2.fillna(False), s3.fillna(False)


def run(close, health, vix, sig_rescue, params):
    """BASE 狀態機 + 可選救援模組。sig_rescue=None 即 BASE。回傳(日報酬, 救援事件list)。"""
    budget, cap, buf = params['budget'], params['cap'], params['exit_buf']
    ma200 = close.rolling(200).mean(); el = ma200 * buf
    ret = close.pct_change()
    hi252 = close.rolling(252).max()
    df = pd.concat([close.rename('c'), ma200.rename('ma'), el.rename('el'),
                    health.rename('h'), vix.rename('v'), ret.rename('r'),
                    (close / hi252 - 1).rename('dd')], axis=1).dropna()
    sig = sig_rescue.reindex(df.index).fillna(False).values if sig_rescue is not None else None
    c = df['c'].values; ma = df['ma'].values; el_ = df['el'].values
    h = df['h'].values; vx = df['v'].values; dd = df['dd'].values
    n = len(df); below = c < el_
    db = np.zeros(n, int); runb = 0
    for t in range(n):
        runb = runb + 1 if below[t] else 0; db[t] = runb
    expo = np.zeros(n); in_pos = False; last_base = 0.0
    rescue = False; r_entry = 0.0; events = []
    for t in range(n):
        credit_off = h[t] <= 0
        reclaim = c[t] >= ma[t]
        panic = (not np.isnan(vx[t])) and vx[t] > C.PANIC_VIX
        # 正常系統
        if not in_pos:
            if reclaim and not credit_off:
                in_pos = True; rescue = False          # 正常進場,救援(若有)併回
        else:
            if credit_off:
                in_pos = False
            elif below[t] and not (panic and db[t] < C.PANIC_DELAY):
                in_pos = False
        # 救援模組:僅系統空手+崩後語境
        if sig is not None and not in_pos:
            if not rescue and sig[t] and dd[t] < -DD_GATE:
                rescue = True; r_entry = c[t]
                events.append([df.index[t], c[t], None, None])
            elif rescue and c[t] < r_entry * RESCUE_STOP:
                rescue = False                          # 救援停損
                if events and events[-1][2] is None:
                    events[-1][2] = df.index[t]; events[-1][3] = 'stop'
        if in_pos and reclaim:
            sd_ = (c[t] - el_[t]) / c[t]
            last_base = min(budget / max(sd_, C.STOP_DIST_FLOOR), cap)
            expo[t] = last_base * h[t]
            if events and events[-1][2] is None:
                events[-1][2] = df.index[t]; events[-1][3] = 'handover'
        elif in_pos:
            expo[t] = last_base * h[t]
        elif rescue:
            expo[t] = TRANCHE
    return (pd.Series(expo, index=df.index).shift(1) * df['r']).dropna(), events, df


def main():
    print("📥 下載 ...")
    smh, qqq = dl('SMH'), dl('QQQ')
    hyg, lqd, vix = dl('HYG'), dl('LQD'), dl('^VIX')
    hln = lambda s: (s >= s.rolling(200).mean()).astype(float)
    br = breadth_pct20()
    print(f"  廣度資料:{'有(' + str(br.index.min().date()) + '起)' if br is not None else '無'}")
    for tk, px in [('SMH', smh), ('QQQ', qqq)]:
        idx = px.index
        health = pd.concat([hln(hyg).reindex(idx).ffill(), hln(lqd).reindex(idx).ffill()], axis=1).mean(axis=1)
        v = vix.reindex(idx).ffill()
        s1, s2, s3 = build_signals(idx, vix, hyg, br)
        p = C.PARAMS.get(tk, C.DEFAULT_PARAM)
        print("=" * 96)
        print(f"  {tk}")
        print("=" * 96)
        variants = [('BASE', None), ('V1 VIX40尖峰回落', s1), ('V2 HYG站回50MA', s2),
                    ('V3 廣度暴衝', s3), ('V1|V3 任一', (s1 | s3))]
        base_r = None
        for name, sig in variants:
            r, ev, dfx = run(px, health, v, sig, p)
            st = perf(r)
            if name == 'BASE': base_r = r
            cells = []
            for wn, (a, b) in CRASH.items():
                seg = r.loc[a:b]
                cells.append(f"{wn}:{(1+seg).prod()-1:+.0%}" if len(seg) > 30 else f"{wn}:—")
            print(f"  {name:18} Sharpe {st['sharpe']:.3f}  CAGR {st['cagr']:5.1f}%  MDD {st['mdd']:6.1f}%   " + '  '.join(cells))
            if sig is not None and ev:
                # 逐次救援事件(壓縮顯示)
                print(f"     └救援 {len(ev)} 次:", end='')
                for e in ev[:10]:
                    d0, px0, d1, how = e
                    fwd = ''
                    if d1 is not None:
                        seg = px.loc[d0:d1]
                        fwd = f"{seg.iloc[-1]/px0-1:+.0%}({how})"
                    print(f" {d0.date()}→{fwd}", end='')
                print()
        print()


if __name__ == '__main__':
    main()
