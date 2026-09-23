"""research_b2_overlays.py - 待辦②:三個 live 疊加機制重新驗證(等權基礎上)
========================================================================
live 現況(都沒在這輪驗過):
  1. vol-timing:牛市時 cap 隨 EWMA 波動縮放(只用在美股 QQQ/SMH)
  2. 恐慌容忍:跌破停損線時,如果 VIX>30 且跌破未滿 3 日 → 先不出場
  3. V底救援:系統空手 + 距252日高回撤>15% + VIX從≥40尖峰回落至75% → 半倉進場(SMH/SOXX)
事前判準:ΔSharpe ≥ +0.10 或 MDD 改善 ≥3pp,且 p<0.10(家族 Holm 後),分段 ≥2/3 同方向,美台分開。
基礎 = 等權(2026-09-24 起的預設持有方式)。
用法: python research_b2_overlays.py
"""
import sys, warnings
warnings.filterwarnings('ignore')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
import research_rot_engine as R

VOL_WIN, VOL_MED = 20, 252
CAP_LO, CAP_HI = 0.667, 1.667


def vix_series(idx):
    v = R.data()['^VIX']['Close'].reindex(idx).ffill()
    return v.values, v.rolling(20).mean().values


def vol_timing_factor(P, cols):
    """牛市(在場且MA200上彎)時,曝險 × clip(中位波動/EWMA波動, 0.667, 1.667)。"""
    F = np.ones((P.n, P.k))
    for j in cols:
        s = pd.Series(P.close[:, j])
        rv = s.pct_change().ewm(span=VOL_WIN).std().values * np.sqrt(252)
        med = pd.Series(rv).rolling(VOL_MED).median().values
        ma = pd.Series(P.ma[:, j])
        up = (ma > ma.shift(21)).values
        f = np.clip(np.where(rv > 0, med / np.maximum(rv, 1e-9), 1.0), CAP_LO, CAP_HI)
        F[:, j] = np.where(P.intrend[:, j] & up & ~np.isnan(med), f, 1.0)
    return np.nan_to_num(F, nan=1.0)


def panic_hold(P, delay=3, vix_thr=30.0):
    """恐慌容忍:跌破停損線但 VIX>門檻 且跌破未滿 delay 日 → 那幾天仍持有。回傳修正後的在場矩陣。"""
    vix, _ = vix_series(P.idx)
    exit_line = np.column_stack([P.ma[:, j] * P.bufs[P.tickers[j]] for j in range(P.k)])
    below = P.close < exit_line
    IT = P.intrend.copy()
    for j in range(P.k):
        run = 0
        for t in range(P.n):
            run = run + 1 if below[t, j] else 0
            if (not IT[t, j]) and below[t, j] and run <= delay and vix[t] > vix_thr:
                IT[t, j] = IT[t - 1, j] if t else False      # 延後出場(維持前一天狀態)
    return IT


def vbottom(P, cols, tranche=0.5, stop_mult=0.93):
    """V底救援:空手 + 距252日高 <−15% + VIX 15日尖峰≥40 且現值≤尖峰×0.75 → 半倉,停損 −7%。"""
    vix, _ = vix_series(P.idx)
    E = np.zeros((P.n, P.k)); S = np.full((P.n, P.k), -1)
    for j in cols:
        hold, entry = False, 0.0
        for t in range(252, P.n):
            c = P.close[t, j]
            if hold:
                if c < entry * stop_mult or P.intrend[t, j]:
                    hold = False
                else:
                    E[t, j], S[t, j] = tranche, j
                continue
            dd = c / P.close[t - 252:t + 1, j].max() - 1
            spike = np.nanmax(vix[max(0, t - 15):t + 1])
            if (not P.intrend[t, j]) and dd < -0.15 and spike >= 40 and vix[t] <= 0.75 * spike:
                hold, entry = True, c
                E[t, j], S[t, j] = tranche, j
    return S, E


def run(pool, tw_rf=0.0):
    P = R.build_panel(pool, tw_rf=tw_rf)
    reb = R.reb_days(P.idx, 0)
    base = R.ew_plan(P, reb=reb)
    IDX = R.sb_indices(P.n, B=1000, mean_block=63, seed=0)
    variants = {'基礎:等權(對照)': base}
    # vol-timing:美股只套 QQQ/SMH(live),另跑一個「全部標的都套」
    if P.market == 'US':
        cols = [P.col(t) for t in ('QQQ', 'SMH') if t in P.tickers]
        F = vol_timing_factor(P, cols)
        variants['+ vol-timing(QQQ/SMH,live)'] = R.Plan(S=base.S, E=base.E * F, reb=reb)
        Fa = vol_timing_factor(P, list(range(P.k)))
        variants['+ vol-timing(全部標的)'] = R.Plan(S=base.S, E=base.E * Fa, reb=reb)
    else:
        Fa = vol_timing_factor(P, list(range(P.k)))
        variants['+ vol-timing(全部標的·台股未啟用)'] = R.Plan(S=base.S, E=base.E * Fa, reb=reb)
    # 恐慌容忍
    for dly in (3, 5):
        IT = panic_hold(P, delay=dly)
        S2 = np.where(IT, np.arange(P.k)[None, :], -1)
        variants[f'+ 恐慌容忍{dly}日(live=3)'] = R.Plan(S=S2, E=(S2 >= 0).astype(float), reb=reb)
    # V底救援(美股 SMH/SOXX = live;台股全部)
    vcols = [P.col(t) for t in (('SMH', 'SOXX') if P.market == 'US' else tuple(P.tickers)) if t in P.tickers]
    Sv, Ev = vbottom(P, vcols)
    Sm = np.where(base.S >= 0, base.S, Sv)
    Em = np.where(base.S >= 0, base.E, Ev)
    variants['+ V底救援(半倉)'] = R.Plan(S=Sm, E=Em, reb=reb)
    rows, res = [], {}
    for name, pl in variants.items():
        r = R.run_plan(P, pl); res[name] = r.r
        m = R.metrics(r.r, P.rf, res=r)
        rows.append({'版本': name, 'CAGR%': round(m['CAGR'], 1), 'Vol%': round(m['Vol'], 1),
                     '超額Sharpe': round(m['ShEx'], 2), 'MDD%': round(m['MDD'], 1),
                     '平均曝險': round(m['Expo'], 2), '筆/年': round(m['Trades/yr'], 1)})
    b = res['基礎:等權(對照)']
    pv = {}
    for i, name in enumerate(variants):
        if i == 0:
            rows[i].update({'ΔSharpe': 0.0, 'p': None, 'ΔMDD': 0.0, '分段Δ': ''}); continue
        bs = R.boot_delta_sharpe(res[name], b, P.rf, IDX)
        mb, mv = R.metrics(b, P.rf), R.metrics(res[name], P.rf)
        segd = []
        for sn, a2, b2 in R.SEGMENTS[pool]:
            x, y = R.sub(res[name], P.idx, a2, b2), R.sub(b, P.idx, a2, b2)
            if len(x) < 200: continue
            rfs = pd.Series(P.rf, index=P.idx).reindex(x.index).values
            segd.append(f'{sn}{R.sharpe_ex(x.values, rfs) - R.sharpe_ex(y.values, rfs):+.2f}')
        rows[i].update({'ΔSharpe': round(bs['d'], 3), 'p': round(bs['p'], 3),
                        'ΔMDD': round(mv['MDD'] - mb['MDD'], 1), '分段Δ': ' '.join(segd)})
        pv[name] = bs['p']
    print(f'\n{"=" * 124}\n[{pool}] 疊加機制(等權基礎)  {P.idx[0].date()}→{P.idx[-1].date()}\n{"=" * 124}')
    print(pd.DataFrame(rows).to_string(index=False))
    items = sorted(pv.items(), key=lambda x: x[1]); m_, run_, hol = len(items), 0.0, {}
    for i, (k, p) in enumerate(items):
        run_ = max(run_, min(1.0, (m_ - i) * p)); hol[k] = round(run_, 3)
    print('Holm 校正後 p:', hol)


if __name__ == '__main__':
    run('US5'); run('TW4')
