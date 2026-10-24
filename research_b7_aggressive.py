"""research_b7_aggressive.py - 待辦⑥:進取模式(TQQQ/SOXL)的信用哨規則該用哪一套?
========================================================================
live 進取模式(aggressive_status.py):母指數(QQQ→TQQQ、SMH→SOXL)站上 200MA 且
  HYG/LQD/SOXX「全部」健康才持有,任一跌破 → 賣光。核心用的是「平滑減碼」(健康比例×曝險)。
兩套規則對同一天會給不同答案,所以要測:對 3 倍槓桿 ETF,哪一套才對?
用合成 3 倍(母指數日報酬×3,扣 2 倍借款成本=現金利率+1%,另扣 0.9%/年內扣),與真實 TQQQ/SOXL 對帳。
變體(全部只在母指數站上 200MA 時持有):
  A live:三哨任一壞 → 0(二元、最嚴)
  B 核心同款:HYG+LQD 健康比例 × 曝險(平滑)
  C HYG+LQD 皆壞才 0
  D 不用信用哨(只看 200MA)
  E 對照:母指數 1 倍 + 200MA(不用槓桿)
用法: python research_b7_aggressive.py
"""
import sys, warnings
warnings.filterwarnings('ignore')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
import research_data_2026q3 as D
import research_engine_2026q3 as E
import research_rot_engine as R

data = D.load()


def health(idx, tks):
    oks = []
    for tk in tks:
        s = data[tk]['Close'].dropna(); ma = s.rolling(200).mean()
        ok = (s >= ma).astype(float); ok[ma.isna()] = np.nan
        oks.append(ok.reindex(idx).ffill())
    return pd.concat(oks, axis=1)


def run(under, real):
    s = data[under]['Close'].dropna()
    hy = data['HYG']['Close'].dropna()
    idx = s.index[(s.index >= hy.index[0] + pd.Timedelta(days=300))]
    s = s.reindex(idx)
    r = s.pct_change().fillna(0).values
    ma = s.rolling(200).mean().reindex(idx).values
    full = data[under]['Close'].dropna()
    ma = full.rolling(200).mean().reindex(idx).values
    on = (s.values >= ma)
    cash = E.cash_daily(data, idx)
    lev3 = 3 * r - 2 * (cash + 0.01 / 252) - 0.009 / 252
    H3 = health(idx, ['HYG', 'LQD', 'SOXX'])
    H2 = health(idx, ['HYG', 'LQD'])
    var = {
        'A live:三哨任一壞→賣光': (H3.min(axis=1) >= 1).values.astype(float),
        'B 核心同款:HYG+LQD 平滑': H2.mean(axis=1).values,
        'C HYG+LQD 皆壞才賣光': (H2.max(axis=1) >= 1).values.astype(float),
        'D 不用信用哨': np.ones(len(idx)),
    }
    out, rows = {}, []
    for k, h in var.items():
        w = np.where(on, np.nan_to_num(h, nan=1.0), 0.0)
        wp = np.r_[0, w[:-1]]
        ret = wp * lev3 + (1 - wp) * cash - np.abs(np.diff(np.r_[0, wp])) * 0.001
        out[k] = pd.Series(ret, index=idx)
    wp = np.r_[0, on[:-1].astype(float)]
    out['E 對照:1倍+200MA'] = pd.Series(wp * r + (1 - wp) * cash - np.abs(np.diff(np.r_[0, wp])) * 0.0005, index=idx)
    IDX = R.sb_indices(len(idx), B=1000, mean_block=63, seed=0)
    base = out['A live:三哨任一壞→賣光']
    for k, x in out.items():
        m = R.metrics(x, cash)
        yr = x.groupby(idx.year).apply(lambda z: (1 + z).prod() - 1) * 100
        row = {'規則': k, 'CAGR%': round(m['CAGR'], 1), 'Vol%': round(m['Vol'], 1), '超額Sharpe': round(m['ShEx'], 2),
               'MDD%': round(m['MDD'], 1), '最差年%': round(yr.min(), 1), '2022%': round(yr.get(2022, np.nan), 1)}
        if k != 'A live:三哨任一壞→賣光':
            bs = R.boot_delta_sharpe(x.values, base.values, cash, IDX)
            row.update({'Δ vs live': round(bs['d'], 3), 'p': round(bs['p'], 3)})
        rows.append(row)
    # 合成 vs 真實對帳
    rl = data[real]['Close'].dropna().pct_change().reindex(idx).dropna()
    syn = pd.Series(lev3, index=idx).reindex(rl.index)
    yrs = len(rl) / 252
    print(f'\n{"=" * 120}\n{real}(合成 3×{under}){idx[0].date()}→{idx[-1].date()}'
          f'  對帳:真實 CAGR {((1 + rl).prod() ** (1 / yrs) - 1) * 100:.1f}% vs 合成 {((1 + syn).prod() ** (1 / yrs) - 1) * 100:.1f}%'
          f'、日報酬相關 {np.corrcoef(rl, syn)[0, 1]:.3f}\n{"=" * 120}')
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == '__main__':
    run('QQQ', 'TQQQ')
    run('SOXX', 'SOXL')
