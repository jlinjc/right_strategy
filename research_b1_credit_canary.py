"""research_b1_credit_canary.py - 待辦①:信用哨重新驗證(live 每天在用但這輪沒驗過)
========================================================================
live 現況:
  美股 = HYG + LQD 各看是否站上自己200MA → 健康比例(0/0.5/1)× 曝險
  台股 = HYG + LQD + SOXX 三合 → 健康比例(0/⅓/⅔/1)× 曝險
問題:這套「平滑減碼」是舊研究定的,本輪沒重驗;而且持有方式已改成等權,基礎變了。

事前判準(先寫死再跑):要「留著/改掉」某個版本,需要
  G1 效果:ΔSharpe ≥ +0.10 或 MDD 改善 ≥ 3pp
  G2 顯著:stationary block bootstrap 單尾 p < 0.10(家族內 Holm 校正)
  G3 分段:分段 ≥ 2/3 同方向
  G4 兩市場各自判定(不可合併)
對照組 = 完全不用信用哨(只有 200MA)。
用法: python research_b1_credit_canary.py
"""
import sys, warnings
warnings.filterwarnings('ignore')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
import research_rot_engine as R

VARIANTS = {
    'none':        {'tks': [],                     'mode': 'smooth', 'label': '不用信用哨(對照)'},
    'HYG':         {'tks': ['HYG'],                'mode': 'binary', 'label': 'HYG 單一(跌破→0)'},
    'HYG_LQD_sm':  {'tks': ['HYG', 'LQD'],         'mode': 'smooth', 'label': 'HYG+LQD 平滑(美股 live)'},
    'HYG_LQD_bin': {'tks': ['HYG', 'LQD'],         'mode': 'binary', 'label': 'HYG+LQD 皆壞才砍0'},
    'HYG_LQD_any': {'tks': ['HYG', 'LQD'],         'mode': 'any',    'label': 'HYG+LQD 任一壞就砍0'},
    'TRIPLE_sm':   {'tks': ['HYG', 'LQD', 'SOXX'], 'mode': 'smooth', 'label': 'HYG+LQD+SOXX 平滑(台股 live)'},
    'TRIPLE_bin':  {'tks': ['HYG', 'LQD', 'SOXX'], 'mode': 'binary', 'label': 'HYG+LQD+SOXX 皆壞才砍0'},
    'SOXX':        {'tks': ['SOXX'],               'mode': 'binary', 'label': 'SOXX(費半)單一'},
}


def health(idx, tks, mode):
    if not tks:
        return np.ones(len(idx))
    d = R.data()
    oks = []
    for tk in tks:
        s = d[tk]['Close'].dropna()
        ma = s.rolling(200).mean()
        ok = (s >= ma).astype(float)
        ok[ma.isna()] = np.nan
        oks.append(ok.reindex(idx).ffill())
    H = pd.concat(oks, axis=1)
    frac = H.mean(axis=1)
    if mode == 'smooth':
        out = frac
    elif mode == 'binary':          # 全壞才 0,否則 1
        out = (frac > 0).astype(float)
    else:                            # any:任一壞就 0
        out = (frac >= 1).astype(float)
    return out.fillna(1.0).values    # 暖身期視為健康(不誤砍)


def run(pool, tw_rf=0.0):
    P = R.build_panel(pool, tw_rf=tw_rf)
    reb = R.reb_days(P.idx, 0)
    base_plan = R.ew_plan(P, reb=reb)          # 持有方式已改等權 → 在等權之上測信用哨
    segs = R.SEGMENTS[pool]
    IDX = R.sb_indices(P.n, B=1000, mean_block=63, seed=0)
    res, rows = {}, []
    for key, v in VARIANTS.items():
        h = health(P.idx, v['tks'], v['mode'])
        pl = R.Plan(S=base_plan.S, E=base_plan.E * h[:, None], reb=reb)
        r = R.run_plan(P, pl)
        res[key] = r.r
        m = R.metrics(r.r, P.rf, res=r)
        rows.append({'版本': v['label'], 'CAGR%': round(m['CAGR'], 1), 'Vol%': round(m['Vol'], 1),
                     '超額Sharpe': round(m['ShEx'], 2), 'MDD%': round(m['MDD'], 1),
                     '平均曝險': round(m['Expo'], 2), '筆/年': round(m['Trades/yr'], 1)})
    base = res['none']
    for i, (key, v) in enumerate(VARIANTS.items()):
        if key == 'none':
            rows[i].update({'ΔSharpe vs 不用': 0.0, 'p': None, 'ΔMDD': 0.0, '分段Δ': ''})
            continue
        bs = R.boot_delta_sharpe(res[key], base, P.rf, IDX)
        mb, mv = R.metrics(base, P.rf), R.metrics(res[key], P.rf)
        segd = []
        for sn, a, b in segs:
            x, y = R.sub(res[key], P.idx, a, b), R.sub(base, P.idx, a, b)
            if len(x) < 200: continue
            rfs = pd.Series(P.rf, index=P.idx).reindex(x.index).values
            segd.append(f'{sn}{R.sharpe_ex(x.values, rfs) - R.sharpe_ex(y.values, rfs):+.2f}')
        rows[i].update({'ΔSharpe vs 不用': round(bs['d'], 3), 'p': round(bs['p'], 3),
                        'ΔMDD': round(mv['MDD'] - mb['MDD'], 1), '分段Δ': ' '.join(segd)})
    print(f'\n{"=" * 124}\n[{pool}] 等權組合 × 各種信用哨  {P.idx[0].date()}→{P.idx[-1].date()}'
          f'  (rf={"IRX" if P.market == "US" else f"{tw_rf:.0%}"})\n{"=" * 124}')
    print(pd.DataFrame(rows).to_string(index=False))
    # Holm(家族 = 7 個非對照版本)
    pv = {v['label']: rows[i]['p'] for i, (k, v) in enumerate(VARIANTS.items()) if k != 'none'}
    items = sorted(pv.items(), key=lambda x: x[1]); m_, run_, hol = len(items), 0.0, {}
    for i, (k, p) in enumerate(items):
        run_ = max(run_, min(1.0, (m_ - i) * p)); hol[k] = round(run_, 3)
    print('Holm 校正後 p:', hol)
    # 危機年份逐年
    yr = {}
    for key in ['none', 'HYG_LQD_sm', 'TRIPLE_sm', 'HYG_LQD_bin']:
        s = pd.Series(res[key].values, index=P.idx)
        yr[VARIANTS[key]['label']] = (s.groupby(P.idx.year).apply(lambda x: (1 + x).prod() - 1) * 100).round(1)
    print('\n逐年報酬(%):'); print(pd.DataFrame(yr).to_string())
    return res


if __name__ == '__main__':
    run('US5')
    run('TW4')
    run('TW3L')
