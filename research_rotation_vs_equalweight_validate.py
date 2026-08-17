"""
research_rotation_vs_equalweight_validate.py — RS輪動單押 vs 5檔等權同時持有,哪個好?
========================================================================
Jason 問:「換標的對嗎?我一次只能持有一檔嗎,還是我可以平衡?」
用同一套進出場(200MA+信用哨)+ RiskTarget倉位,只差「多檔同時risk_on時怎麼分配」:
  A) 只持有RS最強的單一檔(現行系統做法,30日鎖)
  B) 5檔平均分攤,不挑最強
兩邊拿20年資料公平對打,附五檔日報酬相關係數矩陣(佐證「86%同向」的原始依據)。
"""
import warnings
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd, yfinance as yf
import core_status as C
from generate_kbar_annotations import _clean_ohlc, US, US_CANARY, _canary_health


def stats(r):
    r = r[~np.isnan(r)]
    mu, sd = r.mean(), r.std()
    eq = np.cumprod(1 + r)
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    return mu / sd * np.sqrt(252), (eq[-1] ** (252 / len(r)) - 1) * 100, dd * 100


def main():
    syms = [s for s, _ in US]
    all_syms = syms + ['HYG', 'LQD']
    raw = yf.download(' '.join(all_syms), start='2006-01-01', interval='1d',
                      progress=False, group_by='ticker', auto_adjust=True, threads=True)
    canary_raw = {tk: _clean_ohlc(raw[tk][['Open', 'High', 'Low', 'Close', 'Volume']], False)['Close']
                  for tk in ['HYG', 'LQD']}

    data = {s: _clean_ohlc(raw[s][['Open', 'High', 'Low', 'Close', 'Volume']], False) for s in syms}
    common = data[syms[0]].index
    for s in syms[1:]:
        common = common.intersection(data[s].index)
    common = common.sort_values()
    n = len(common)

    close = {s: data[s]['Close'].reindex(common).values.astype(float) for s in syms}
    ma200 = {s: pd.Series(close[s], index=common).rolling(C.MA).mean().values for s in syms}
    health = _canary_health(common, {k: canary_raw[k].reindex(common).ffill() for k in US_CANARY})

    r126, vol126 = {}, {}
    for s in syms:
        sc = pd.Series(close[s], index=common)
        r126[s] = (sc / sc.shift(126) - 1).values
        vol126[s] = (sc.pct_change().rolling(126).std() * np.sqrt(252)).values
    rs = {s: np.where(vol126[s] > 0, r126[s] / vol126[s], np.nan) for s in syms}

    exit_line, expo_arr = {}, {}
    in_pos = {s: False for s in syms}
    last_base = {s: 0.0 for s in syms}
    for s in syms:
        p = C.PARAMS.get(s, C.DEFAULT_PARAM)
        exit_line[s] = ma200[s] * p['exit_buf']
        expo_arr[s] = np.zeros(n)

    ret = {s: np.zeros(n) for s in syms}
    for s in syms:
        ret[s][1:] = close[s][1:] / close[s][:-1] - 1

    for t in range(n):
        for s in syms:
            p = C.PARAMS.get(s, C.DEFAULT_PARAM)
            if np.isnan(ma200[s][t]): continue
            reclaim = close[s][t] >= ma200[s][t]
            credit_off = health[t] <= 0
            if not in_pos[s]:
                if reclaim and not credit_off: in_pos[s] = True
            else:
                if credit_off or close[s][t] < exit_line[s][t]: in_pos[s] = False
            if in_pos[s] and reclaim:
                sd = (close[s][t] - exit_line[s][t]) / close[s][t]
                last_base[s] = min(p['budget'] / max(sd, C.STOP_DIST_FLOOR), p['cap'])
                expo_arr[s][t] = last_base[s] * health[t]
            elif in_pos[s]:
                expo_arr[s][t] = last_base[s] * health[t]

    retB = np.zeros(n)
    for t in range(1, n):
        retB[t] = sum(expo_arr[s][t - 1] * ret[s][t] for s in syms) / len(syms)

    MIN_HOLD = 30
    held, since_t = None, 0
    retA = np.zeros(n)
    for t in range(1, n):
        elig = [s for s in syms if in_pos[s] and not np.isnan(rs[s][t - 1])]
        strongest = max(elig, key=lambda s: rs[s][t - 1]) if elig else None
        if held is not None and held in elig:
            days = t - 1 - since_t
            if days >= MIN_HOLD and strongest and strongest != held:
                held, since_t = strongest, t - 1
        else:
            held, since_t = strongest, t - 1
        if held:
            retA[t] = expo_arr[held][t - 1] * ret[held][t]

    valid = np.arange(n) > 260
    sA, cA, dA = stats(retA[valid])
    sB, cB, dB = stats(retB[valid])
    print(f'A) RS輪動單押(30日鎖)   Sharpe {sA:.2f}  CAGR {cA:5.1f}%  MDD {dA:5.1f}%')
    print(f'B) 5檔等權同時持有      Sharpe {sB:.2f}  CAGR {cB:5.1f}%  MDD {dB:5.1f}%')
    print()
    print('5檔日報酬相關係數矩陣:')
    print(pd.DataFrame({s: ret[s] for s in syms}).corr().round(2))


if __name__ == '__main__':
    main()
