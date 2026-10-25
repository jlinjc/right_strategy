"""research_b14_execution_lag.py - R1:隔日執行的落差(官方績效假設「訊號當天收盤就成交」)
========================================================================
官方績效(v2_status.py OFFICIAL,research_b6_options_table.py 同一套)用引擎慣例:
  t 日收盤算訊號、t 日收盤就調整、t+1 吃報酬。實際上頁面是隔天才更新/執行:
  - 美股:台灣 09:00 那班用美股 t 日收盤算出訊號 → 美股 t+1 開盤(台灣晚上)才能下單
  - 台股:信用哨是美股 HYG/LQD/SOXX,美股 t 日收盤在台股 t 日收盤「之後」約 14 小時才出現
          → 回測在台股 t 日收盤就用到美股 t 日收盤 = 偷看未來;實際最早只能台股 t+1 開盤執行
比較三種執行方式(同一套規則、同成本):
  lag0       官方:t 日收盤成交(理想化;台股另含上面那個偷看)
  lag1_open  t+1 開盤成交(實際做得到的最快方式)
  lag1_close t+1 收盤成交(保守)
另外台股多一個診斷:只把信用哨往後移一天、核心仍 t 日收盤成交 → 單獨量化「偷看」的影響。
lag1_open 的算法:跑 lag1_close(計畫整體往後移一天),再把「t+1 當天換倉那部分」的報酬
  從 收盤→收盤 改成 開盤→收盤:r_open[t] ≈ r_lag1[t] + Σ_i (W'[t,i] − W'[t-1,i]) × (Close/Open − 1)[t,i]
  (加法近似,誤差是二階小量;開盤/收盤用同一天的原始價格,不受除權/分割影響)。
輸出 research_cache/b14_execution_lag.txt
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
import warnings; warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
import research_rot_engine as R, research_b1_credit_canary as C1

NOREB = None


def shift(a, k=1, fill=0):
    out = np.empty_like(a)
    out[:k] = fill
    out[k:] = a[:-k]
    return out


def intraday(P):
    d = R.data()
    cols = []
    for tk in P.tickers:
        df = d[tk][['Open', 'Close']].reindex(P.idx)
        x = (df['Close'] / df['Open'] - 1).replace([np.inf, -np.inf], np.nan)
        x[(x.abs() > 0.2) | (df['Open'] <= 0)] = np.nan      # 壞資料 → 當作開盤=收盤(不調整)
        cols.append(x.fillna(0.0).values)
    return np.stack(cols, axis=1)


def metrics_row(name, r, P, res=None):
    m = R.metrics(r, P.rf, res=res)
    return name, m


def study(pool, tickers, sizing, ctks, label):
    P = R.build_panel(pool, tickers=tickers)
    nore = np.zeros(P.n, dtype=bool)
    h = C1.health(P.idx, ctks, 'smooth')[:, None]
    ew = R.ew_plan(P, reb=nore, sizing=sizing)
    S, E = ew.S, ew.E * h
    r0 = R.run_plan(P, R.Plan(S=S, E=E, reb=nore))
    S1, E1 = shift(S, 1, -1), shift(E, 1, 0.0)
    r1 = R.run_plan(P, R.Plan(S=S1, E=E1, reb=nore))
    W = r1.W
    dW = np.vstack([np.zeros((1, W.shape[1])), np.diff(W, axis=0)])
    ropen = pd.Series(r1.r.values + (dW * intraday(P)).sum(axis=1), index=P.idx)
    rows = [('lag0 官方(t 日收盤成交)', r0.r, r0), ('lag1_open(t+1 開盤成交)', ropen, r1),
            ('lag1_close(t+1 收盤成交)', r1.r, r1)]
    if P.market == 'TW':
        hl = shift(h, 1, 1.0)
        rc = R.run_plan(P, R.Plan(S=S, E=ew.E * hl, reb=nore))
        rows.insert(1, ('診斷:只把信用哨延後一天', rc.r, rc))
    IDX = R.sb_indices(P.n, B=2000, mean_block=63, seed=0)
    lines = [f'\n[{label}] {P.idx[0].date()} → {P.idx[-1].date()}']
    m0 = R.metrics(r0.r, P.rf, res=r0)
    for name, r, res in rows:
        m = R.metrics(r, P.rf, res=res)
        extra = ''
        if res is not r0:
            bs = R.boot_delta_sharpe(r, r0.r, P.rf, IDX)
            extra = (f"  | vs 官方:CAGR {m['CAGR'] - m0['CAGR']:+.1f}pp  Δ超額Sharpe {bs['d']:+.3f} "
                     f"[{bs['lo']:+.2f},{bs['hi']:+.2f}]")
        lines.append(f"{name:28s} CAGR {m['CAGR']:5.1f}%  MDD {m['MDD']:6.1f}%  超額Sharpe {m['ShEx']:.2f}{extra}")
    return lines


if __name__ == '__main__':
    out = []
    out += study('US5', ['SMH', 'SOXX', 'QQQ', 'XLK', 'SPY'], 'rt', ['HYG', 'LQD'], '美股 5 檔等權 + RiskTarget + 信用哨(官方設定)')
    out += study('TW4', ['0050.TW', '0052.TW', '006208.TW', '0051.TW'], 'binary', ['HYG', 'LQD', 'SOXX'],
                 '台股 4 檔等權 + 信用哨(官方設定)')
    txt = '\n'.join(out)
    print(txt)
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'research_cache', 'b14_execution_lag.txt')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(txt + '\n')


def credit_value_tw():
    """台股信用哨在「做得到的執行方式」(t+1 開盤)下還有沒有 edge?(b1 的 +0.359 是在偷看未來的對齊下測的)"""
    P = R.build_panel('TW4', tickers=['0050.TW', '0052.TW', '006208.TW', '0051.TW'])
    nore = np.zeros(P.n, dtype=bool)
    ew = R.ew_plan(P, reb=nore, sizing='binary')
    ix = intraday(P)
    IDX = R.sb_indices(P.n, B=2000, mean_block=63, seed=0)

    def opened(E):
        r1 = R.run_plan(P, R.Plan(S=shift(ew.S, 1, -1), E=shift(E, 1, 0.0), reb=nore))
        W = r1.W; dW = np.vstack([np.zeros((1, W.shape[1])), np.diff(W, axis=0)])
        return pd.Series(r1.r.values + (dW * ix).sum(axis=1), index=P.idx), r1

    lines = ['\n[台股信用哨重驗:t+1 開盤成交(做得到的版本)]']
    base, rb = opened(ew.E)
    mb = R.metrics(base, P.rf, res=rb)
    lines.append(f"{'不用信用哨':24s} CAGR {mb['CAGR']:5.1f}%  MDD {mb['MDD']:6.1f}%  超額Sharpe {mb['ShEx']:.2f}")
    for tks, nm in ((['HYG', 'LQD', 'SOXX'], 'HYG+LQD+SOXX 平滑(live)'), (['HYG', 'LQD'], 'HYG+LQD 平滑'), (['SOXX'], 'SOXX 單一平滑')):
        h = C1.health(P.idx, tks, 'smooth')[:, None]
        r, res = opened(ew.E * h)
        m = R.metrics(r, P.rf, res=res)
        bs = R.boot_delta_sharpe(r, base, P.rf, IDX)
        lines.append(f"{nm:24s} CAGR {m['CAGR']:5.1f}%  MDD {m['MDD']:6.1f}%  超額Sharpe {m['ShEx']:.2f}"
                     f"  | vs 不用:Δ超額Sharpe {bs['d']:+.3f} [{bs['lo']:+.2f},{bs['hi']:+.2f}] p={bs['p']:.3f}")
    return lines


if __name__ == '__main__':
    extra = credit_value_tw()
    print('\n'.join(extra))
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'research_cache', 'b14_execution_lag.txt'), 'a', encoding='utf-8') as f:
        f.write('\n'.join(extra) + '\n')


def official_feasible():
    """官方設定 ×1.0 / ×1.25 在 t+1 開盤成交下的數字(給 v2 頁面 OFFICIAL 用)。"""
    lines = ['\n[官方設定:t+1 開盤成交]']
    for pool, tks, sizing, ctks, lab in (('US5', ['SMH', 'SOXX', 'QQQ', 'XLK', 'SPY'], 'rt', ['HYG', 'LQD'], '美股'),
                                         ('TW4', ['0050.TW', '0052.TW', '006208.TW', '0051.TW'], 'binary', ['HYG', 'LQD', 'SOXX'], '台股')):
        P = R.build_panel(pool, tickers=tks)
        nore = np.zeros(P.n, dtype=bool)
        ew = R.ew_plan(P, reb=nore, sizing=sizing)
        h = C1.health(P.idx, ctks, 'smooth')[:, None]
        ix = intraday(P)
        for lev in (1.0, 1.25):
            r1 = R.run_plan(P, R.Plan(S=shift(ew.S, 1, -1), E=shift(ew.E * h * lev, 1, 0.0), reb=nore))
            W = r1.W; dW = np.vstack([np.zeros((1, W.shape[1])), np.diff(W, axis=0)])
            r = pd.Series(r1.r.values + (dW * ix).sum(axis=1), index=P.idx)
            m = R.metrics(r, P.rf, res=r1)
            lines.append(f"{lab} ×{lev}  {P.idx[0].date()}~{P.idx[-1].date()}  CAGR {m['CAGR']:.1f}%  MDD {m['MDD']:.1f}%  超額Sharpe {m['ShEx']:.2f}")
    return lines


if __name__ == '__main__':
    extra2 = official_feasible()
    print('\n'.join(extra2))
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'research_cache', 'b14_execution_lag.txt'), 'a', encoding='utf-8') as f:
        f.write('\n'.join(extra2) + '\n')
