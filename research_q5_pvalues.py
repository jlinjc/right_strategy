"""research_q5_pvalues.py - 把所有結論放到同一把尺:算 p 值 + 多重檢定校正
========================================================================
先前黃燈類規則只用「逐年一致性」判斷,沒有 p 值。這裡統一補上:
  · 到場測試類(黃燈):樣本天數高度重疊 → 用「逐年 block bootstrap」(以年為區塊重抽 2000 次),
    p = 重抽後平均 ≤ 0 的比例。這比把每天當獨立樣本保守得多(誠實作法)。
  · 策略對策略類(持有方式/倉位/跌深槓桿):stationary block bootstrap 配對 ΔSharpe(同 rot 驗證)。
最後用 Holm 對整個家族校正,看哪些結論撐得住。
用法: python research_q5_pvalues.py
"""
import sys, warnings
warnings.filterwarnings('ignore')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
import research_q2b_thresholds as Q
import research_engine_2026q3 as E
import research_rot_engine as R

B = 2000
rng = np.random.default_rng(0)


def year_block_p(df, col='d'):
    """逐年 block bootstrap:重抽「年」而不是「天」,消掉同一年內樣本重疊造成的假顯著。"""
    yrs = sorted(df['yr'].unique())
    by = {y: df.loc[df.yr == y, col].values for y in yrs}
    obs = df[col].mean()
    sims = np.empty(B)
    for b in range(B):
        pick = rng.choice(yrs, size=len(yrs), replace=True)
        vals = np.concatenate([by[y] for y in pick])
        sims[b] = vals.mean()
    return obs, float((sims <= 0).mean()), float(np.quantile(sims, 0.05)), float(np.quantile(sims, 0.95))


def arrival_rows():
    """黃燈三類的到場測試(等待 − 立即買),美股台股分開。"""
    out = []
    specs = [
        ('美股 別追高(各檔現行門檻)', Q.US, 'D50', None),
        ('台股 別追高(新門檻12%)', Q.TW, 'D50', 0.12),
        ('台股 別追高(舊門檻6~8%)', Q.TW, 'D50', 'old'),
        ('美股 空手先別進(離停損≥20%,未追高)', Q.US, 'STOP20', None),
        ('台股 舊版空手先別進(離停損15%,未追高)', Q.TW, 'STOP15', None),
    ]
    for name, tks, key, thr in specs:
        dfs = []
        for tk in tks:
            s, I, on, dd = Q.series(tk)
            p = E.params(tk)
            if key == 'D50':
                t = (p['thr'] - 1) if thr is None else (
                    {'0050.TW': 0.06, '0052.TW': 0.08, '006208.TW': 0.08, '0051.TW': 0.08, '00757.TW': 0.12}[tk]
                    if thr == 'old' else thr)
                df = Q.arrival(s, I, on, dd['D50'], t)
            else:
                lim = 0.20 if key == 'STOP20' else 0.15
                ext_thr = (p['thr'] - 1) if tks is Q.US else 0.12
                dist = np.where(dd['D50'] < ext_thr, dd['STOP'], -1.0)
                df = Q.arrival(s, I, on, dist, lim)
            dfs.append(df)            # Q.arrival 回傳欄位:yr / A(立即買) / B(等待) / mddA / mddB / miss
        df = pd.concat(dfs)
        df['d'] = df['B'] - df['A']
        obs, p_, lo, hi = year_block_p(df)
        out.append({'結論': name, 'n': len(df), '效果(等待−立即買)%': round(obs * 100, 2),
                    '90%CI%': f'[{lo*100:.2f},{hi*100:.2f}]', 'p(逐年bootstrap)': round(p_, 3)})
    return out


def strategy_rows():
    out = []
    # 1) 持有方式:等權 − 單押(各池)
    for pool, lab in [('US5', '美股 等權−單押(主池)'), ('BROAD', '美股 等權−單押(無後見之明14檔)'),
                      ('TW4', '台股 等權−單押(主池)'), ('TW3L', '台股 等權−單押(長樣本)')]:
        P = R.build_panel(pool)
        f = R.run_plan(P, R.rotation_plan(P, elig='above', N=1, lock=21, switch='lock')).r
        w = R.run_plan(P, R.ew_plan(P, reb=R.reb_days(P.idx, 0))).r
        IDX = R.sb_indices(P.n, B=1000, mean_block=63, seed=0)
        bs = R.boot_delta_sharpe(w, f, P.rf, IDX)
        out.append({'結論': lab, 'n': P.n, '效果(ΔSharpe)': round(bs['d'], 3),
                    '90%CI%': f"[{bs['lo']:.2f},{bs['hi']:.2f}]", 'p(逐年bootstrap)': round(bs['p'], 3)})
    # 2) 台股倉位:固定1倍 − RiskTarget(組合層)
    P = R.build_panel('TW4')
    ew = R.ew_plan(P, reb=R.reb_days(P.idx, 0))
    fix = R.run_plan(P, ew).r
    rt = R.run_plan(P, R.ew_plan(P, reb=R.reb_days(P.idx, 0), sizing='rt')).r
    IDX = R.sb_indices(P.n, B=1000, mean_block=63, seed=0)
    bs = R.boot_delta_sharpe(fix, rt, P.rf, IDX)
    out.append({'結論': '台股 固定1倍−RiskTarget(等權組合)', 'n': P.n, '效果(ΔSharpe)': round(bs['d'], 3),
                '90%CI%': f"[{bs['lo']:.2f},{bs['hi']:.2f}]", 'p(逐年bootstrap)': round(bs['p'], 3)})
    # 3) 跌深加槓桿:跨標的最好的一條 DD10%/MA50 − 同平均曝險固定槓桿
    import research_q3_bull_dip_leverage as Q3
    for tk, tw in [('SPY', False), ('QQQ', False), ('SMH', False), ('0050.TW', True)]:
        s, df = Q3.base(tk, tw)
        cash = E.cash_daily(Q3.data, df.index) if not tw else np.zeros(len(df))
        it = df['intrend'].values.astype(float)
        e = Q3.dip_expo(s, df, 'DD', 0.10, 'MA50')
        r1 = Q3.sim(df, e, cash, 0.002 if tw else 0.001)
        r2 = Q3.sim(df, it * e[it > 0].mean(), cash, 0.002 if tw else 0.001)
        P2 = R.build_panel('TW4' if tw else 'US5', tickers=[tk] if not tw else None)
        rf = np.zeros(len(df)) if tw else E.cash_daily(Q3.data, df.index)
        IDX = R.sb_indices(len(df), B=1000, mean_block=63, seed=0)
        bs = R.boot_delta_sharpe(r1.values, r2.values, rf, IDX)
        out.append({'結論': f'{tk} 跌深上2x − 同平均槓桿', 'n': len(df), '效果(ΔSharpe)': round(bs['d'], 3),
                    '90%CI%': f"[{bs['lo']:.2f},{bs['hi']:.2f}]", 'p(逐年bootstrap)': round(bs['p'], 3)})
    return out


def holm(pv: dict):
    items = sorted(pv.items(), key=lambda x: x[1])
    m, run, out = len(items), 0.0, {}
    for i, (k, p) in enumerate(items):
        run = max(run, min(1.0, (m - i) * p)); out[k] = round(run, 3)
    return out


if __name__ == '__main__':
    print('=' * 120); print('Q5 所有結論的 p 值(黃燈類用逐年 block bootstrap,策略類用 stationary block bootstrap)')
    print('=' * 120)
    A = arrival_rows(); S = strategy_rows()
    dfa = pd.DataFrame(A); dfs = pd.DataFrame(S)
    print('\n【黃燈/門檻類】效果 = 等待比立即買多賺幾 %(正=等待較好)')
    print(dfa.to_string(index=False))
    print('\n【策略類】效果 = ΔSharpe')
    print(dfs.to_string(index=False))
    allp = {**{r['結論']: r['p(逐年bootstrap)'] for r in A}, **{r['結論']: r['p(逐年bootstrap)'] for r in S}}
    h = holm(allp)
    print('\n【Holm 多重檢定校正(家族 = 上面全部 {} 條)】'.format(len(allp)))
    hd = pd.DataFrame([{'結論': k, '原始p': v, 'Holm校正後p': h[k],
                        '撐得住?(<0.10)': '✔' if h[k] < 0.10 else '✘'} for k, v in
                       sorted(allp.items(), key=lambda x: x[1])])
    print(hd.to_string(index=False))
